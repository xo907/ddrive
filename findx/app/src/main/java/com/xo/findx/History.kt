package com.xo.findx

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/*
 * Where you've been, kept so it is never lost.
 *
 * Each point goes first into this phone's journal (an hour a file, every line sealed with the phone's
 * hardware key), at once and before anything else. Then, a few at a time and in one go so the radio
 * wakes once rather than for every point, each hour goes to your DDrive as a single sealed file
 * (Segments.path), sent again when more of that hour has come in. A journal hour is only let go of
 * after DDrive has every point of it, and even then the phone keeps [KEEP_DAYS] days to show offline.
 * Nothing is ever deleted from DDrive by FindX.
 */
object History {
    const val KEEP_DAYS = 45L
    /** The hour being filled goes up at most this often (the hours before go as soon as they can). */
    const val PARTIAL_EVERY_MS = 20 * 60_000L

    private fun dir(ctx: Context) = File(ctx.filesDir, "history").apply { mkdirs() }
    private fun file(ctx: Context, hour: Long) = File(dir(ctx), "$hour.fxl")

    private val lock = Any()

    private fun pointJson(p: Point) = JSONArray().put(p.t).put(p.lat).put(p.lon).put(p.acc).put(p.until)

    /** Adds a point (or, with the same [Point.t] as before, a longer stay) to the journal. */
    fun add(ctx: Context, p: Point) = synchronized(lock) {
        val line = FxKeys.b64(Store.lock(pointJson(p).toString().toByteArray()))
        file(ctx, Segments.hourOf(p.t)).appendText(line + "\n")
        Store.prefs(ctx).edit().putLong("historyLast", p.t).apply()
    }

    /** The points of one hour on this phone, a stay's later lines replacing its earlier ones. */
    fun hour(ctx: Context, hour: Long): List<Point> = synchronized(lock) {
        val f = file(ctx, hour)
        if (!f.exists()) return emptyList()
        val byT = LinkedHashMap<Long, Point>()
        f.forEachLine { line ->
            if (line.isBlank()) return@forEachLine
            runCatching {
                val a = JSONArray(String(Store.unlock(FxKeys.unb64(line))))
                val p = Point(a.getLong(0), a.getDouble(1), a.getDouble(2), a.getDouble(3), a.getLong(4))
                byT[p.t] = p
            }
        }
        byT.values.sortedBy { it.t }
    }

    fun hours(ctx: Context): List<Long> = dir(ctx).listFiles()?.mapNotNull { it.name.removeSuffix(".fxl").toLongOrNull() }?.sorted() ?: emptyList()

    /** The points between two times that are on this phone. */
    fun points(ctx: Context, from: Long, to: Long): List<Point> =
        hours(ctx).filter { it >= Segments.hourOf(from) && it <= Segments.hourOf(to) }.flatMap { hour(ctx, it) }
            .filter { it.t <= to && maxOf(it.t, it.until) >= from }

    private fun lines(ctx: Context, hour: Long) = file(ctx, hour).let { if (it.exists()) it.readLines().count { l -> l.isNotBlank() } else 0 }
    private fun sentLines(ctx: Context, hour: Long) = Store.prefs(ctx).getInt("sent:$hour", 0)

    /** Hours with points DDrive doesn't have yet, oldest first. */
    fun pending(ctx: Context): List<Long> = hours(ctx).filter { lines(ctx, it) > sentLines(ctx, it) }

    fun pendingCount(ctx: Context) = pending(ctx).size

    /**
     * Sends what DDrive doesn't have yet. The hour still being filled goes at most every
     * [PARTIAL_EVERY_MS] (unless [force]). Returns how many hours went. Throws when DDrive can't be
     * reached: the journal keeps everything and the next try sends it.
     */
    fun upload(ctx: Context, link: Link, force: Boolean = false): Int {
        val keys = Store.keys(ctx) ?: return 0
        val device = Store.device(ctx)
        val now = System.currentTimeMillis()
        val current = Segments.hourOf(now)
        val p = Store.prefs(ctx)
        var sent = 0
        for (hour in pending(ctx)) {
            if (hour == current && !force && now - p.getLong("partialAt", 0) < PARTIAL_EVERY_MS) continue
            val n = lines(ctx, hour)
            val pts = hour(ctx, hour)
            if (pts.isEmpty()) { p.edit().putInt("sent:$hour", n).apply(); continue }
            val blob = keys.seal(keys.history, Segments.LABEL, Segments.encode(device, hour, pts))
            link.putFile(Segments.path(keys, device, hour), blob)
            p.edit().putInt("sent:$hour", n).apply { if (hour == current) putLong("partialAt", now) }.apply()
            Store.countUpload(ctx)
            sent++
        }
        prune(ctx)
        return sent
    }

    /** Lets go of journal hours that DDrive has in full and that are older than [KEEP_DAYS]. */
    fun prune(ctx: Context) = synchronized(lock) {
        val linked = Store.words(ctx) != null
        val oldest = Segments.hourOf(System.currentTimeMillis() - KEEP_DAYS * 86_400_000)
        for (h in hours(ctx)) {
            if (h >= oldest) continue
            // Never linked: there is no DDrive to keep it, so the phone keeps it (it's small).
            if (!linked || lines(ctx, h) > sentLines(ctx, h)) continue
            file(ctx, h).delete()
            Store.prefs(ctx).edit().remove("sent:$h").apply()
        }
    }

    /**
     * An hour of a device's history from DDrive (for the map, when it isn't on this phone), or null when
     * DDrive has none for that hour.
     */
    fun fetch(ctx: Context, link: Link, device: String, hour: Long): List<Point>? {
        val keys = Store.keys(ctx) ?: return null
        val blob = link.getFile(Segments.path(keys, device, hour)) ?: return null
        return Segments.decode(keys.open(keys.history, Segments.LABEL, blob)).third
    }

    /** The journal as the map page wants it: [[t, lat, lon, acc, until], ...]. */
    fun forMap(points: List<Point>): JSONArray = JSONArray().apply { points.forEach { put(pointJson(it)) } }

    fun forget(ctx: Context) = synchronized(lock) { dir(ctx).deleteRecursively() }

    fun summary(ctx: Context): JSONObject = JSONObject().put("hours", hours(ctx).size).put("pending", pendingCount(ctx))
        .put("last", Store.prefs(ctx).getLong("historyLast", 0))
}
