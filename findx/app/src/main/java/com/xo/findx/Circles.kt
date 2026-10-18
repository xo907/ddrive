package com.xo.findx

import android.app.Notification
import android.app.NotificationManager
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import st.xo.ddrive.core.HostError
import st.xo.ddrive.core.Seal
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

/*
 * Circles: family and friends who see where each other are, live, and can ring each other's phones
 * (as Life360 does), through a DDrive.
 *
 * A circle is made on your DDrive and joined with its invitation, `https://<drive>/j/<circle>#<key>`.
 * The key after "#" is the circle's own (not your words): from it come the key every member seals
 * with and the token that shows the drive they were invited. So friends join from any DDrive, or none,
 * the drive passes it all along without reading it, and nothing of your own devices or history is
 * shared: only where this phone is now, its battery, and arriving at or leaving the circle's places.
 *
 *   seal = HKDF(circle key, "findx circle v1 seal"), auth = hex(HKDF(circle key, "findx circle v1 auth"))[:32]
 *   sealed with labels "findx circle info" ({name, places}), "findx circle report", "findx circle command"
 *
 * A phone is one member id in all its circles (Store.circleMember), a random one. The drive only ever
 * sees that id and sealed bytes.
 */

data class Place(val id: String, val name: String, val lat: Double, val lon: Double, val r: Double) {
    fun json(): JSONObject = JSONObject().put("id", id).put("name", name).put("lat", lat).put("lon", lon).put("r", r)

    companion object {
        fun of(o: JSONObject) = Place(o.getString("id"), o.optString("name", "Place"), o.getDouble("lat"), o.getDouble("lon"), o.optDouble("r", 150.0))
    }
}

data class Circle(
    val id: String, val key: String, val host: String, val name: String,
    val places: List<Place> = emptyList(), val mine: Boolean = false, val canRing: Boolean = true, val joined: Long = 0,
) {
    val raw get() = FxKeys.unb64url(key)
    val seal get() = Seal.hkdf(raw, "findx circle v1 seal")
    val auth get() = FxKeys.hex(Seal.hkdf(raw, "findx circle v1 auth")).take(32)
    val invite get() = "${host.trimEnd('/')}/j/$id#$key"

    fun json(): JSONObject = JSONObject().put("id", id).put("key", key).put("host", host).put("name", name).put("mine", mine)
        .put("canRing", canRing).put("joined", joined).put("places", JSONArray().apply { places.forEach { put(it.json()) } })

    companion object {
        fun of(o: JSONObject) = Circle(
            o.getString("id"), o.getString("key"), o.getString("host"), o.optString("name", "Circle"),
            (o.optJSONArray("places") ?: JSONArray()).let { a -> (0 until a.length()).map { Place.of(a.getJSONObject(it)) } },
            o.optBoolean("mine"), o.optBoolean("canRing", true), o.optLong("joined"),
        )

        /**
         * An invitation, as the join page's link to the app (`findx://join?host=…&c=…#key`) or as the
         * page's own address (`https://drive/j/<circle>#key`). Returns (host, circle, key) or null.
         */
        fun parseInvite(text: String): Triple<String, String, String>? {
            val t = text.trim().substringAfterLast(' ')
            val u = runCatching { java.net.URI(t) }.getOrNull() ?: return null
            val key = u.rawFragment ?: return null
            if (!Regex("^[A-Za-z0-9_-]{43}$").matches(key)) return null
            return when (u.scheme) {
                "findx" -> {
                    if (u.host != "join") return null
                    val q = (u.rawQuery ?: "").split("&").mapNotNull { kv ->
                        kv.split("=", limit = 2).takeIf { it.size == 2 }?.let { it[0] to java.net.URLDecoder.decode(it[1], "UTF-8") }
                    }.toMap()
                    val host = q["host"] ?: return null
                    val c = q["c"] ?: return null
                    if (!Regex("^[0-9a-f]{32}$").matches(c) || !Regex("^https?://[^/?#\\s]+$").matches(host.trimEnd('/'))) null
                    else Triple(host.trimEnd('/'), c, key)
                }
                "http", "https" -> {
                    val m = Regex("^/j/([0-9a-f]{32})$").find(u.rawPath ?: "") ?: return null
                    Triple("${u.scheme}://${u.rawAuthority}", m.groupValues[1], key)
                }
                else -> null
            }
        }
    }
}

/** What one member last said, opened. */
data class Member(
    val member: String, val person: String, val device: String, val t: Long, val fix: Fix?, val battery: Int, val charging: Boolean,
    val place: String?, val since: Long, val mode: String, val canRing: Boolean, val ringing: Boolean,
) {
    /** As the map wants a report. */
    fun asReport(id: String) = JSONObject().put("d", id).put("n", person).put("t", t).put("mode", mode).apply {
        fix?.let { put("fix", Report.fixJson(it)) }
        if (battery >= 0) put("bat", battery)
        if (charging) put("chg", true)
        place?.let { put("spot", JSONObject().put("name", it)) }
        if (since > 0) put("still", since)
        if (ringing) put("ring", true)
    }
}

/** A circle's drive, as its members use it: no sign-in, the invitation's token instead (findxcircles.py). */
class CircleClient(host: String) {
    private val base = host.trimEnd('/')

    fun call(what: String, body: JSONObject, timeoutMs: Int = 30_000): JSONObject {
        Meter.wake()
        val bytes = body.toString().toByteArray()
        val conn = try {
            (URL("$base/api/findx/c/$what").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"; connectTimeout = 15_000; readTimeout = timeoutMs; useCaches = false; doOutput = true
                setRequestProperty("Content-Type", "application/json"); setFixedLengthStreamingMode(bytes.size)
            }
        } catch (e: Exception) {
            throw HostError(0, "$base can't be reached (${e.message})")
        }
        try {
            conn.outputStream.use { it.write(bytes) }
            val code = conn.responseCode
            val text = (if (code >= 400) conn.errorStream else conn.inputStream)?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            if (code >= 400) throw HostError(code, runCatching { JSONObject(text).optString("error") }.getOrNull()?.ifEmpty { null } ?: "HTTP $code")
            return JSONObject(text.ifEmpty { "{}" })
        } catch (e: java.io.IOException) {
            throw HostError(0, "$base can't be reached (${e.message})")
        } finally {
            conn.disconnect()
        }
    }
}

object Circles {
    const val REPORT_EVERY_MS = 15 * 60_000L     // a circle hears from a phone that lies still this often
    const val MOVED_METRES = 50.0                 // ...and when it has moved this far
    const val LEAVE_MARGIN = 50.0                 // metres beyond a place's edge before it counts as left

    private val lock = Any()
    private val lastSent = ConcurrentHashMap<String, Pair<Long, Fix?>>()     // circle -> when, where
    private val seen = LinkedHashSet<String>()
    /** Members as last fetched: circle -> (when, members). */
    private val known = ConcurrentHashMap<String, Pair<Long, List<Member>>>()

    // ------------------------------------------------------------ kept on the phone (sealed)

    private fun file(ctx: Context) = File(ctx.filesDir, "circles.fx")

    fun all(ctx: Context): List<Circle> = synchronized(lock) {
        val f = file(ctx)
        if (!f.exists()) return emptyList()
        val a = runCatching { JSONArray(String(Store.unlock(f.readBytes()))) }.getOrNull() ?: return emptyList()
        (0 until a.length()).mapNotNull { runCatching { Circle.of(a.getJSONObject(it)) }.getOrNull() }
    }

    private fun save(ctx: Context, list: List<Circle>) = synchronized(lock) {
        val f = file(ctx)
        val tmp = File(f.path + ".tmp")
        tmp.writeBytes(Store.lock(JSONArray().apply { list.forEach { put(it.json()) } }.toString().toByteArray()))
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        Store.prefs(ctx).edit().putBoolean("circlesChanged", true).apply()
        Service.changed(ctx)
    }

    private fun update(ctx: Context, c: Circle) = save(ctx, all(ctx).filter { it.id != c.id } + c)

    fun get(ctx: Context, id: String) = all(ctx).firstOrNull { it.id == id }

    /**
     * For your browser on your DDrive (/FindX/circles.fxc, sealed with your report key): the circles that
     * live on that drive, so its FindX page can show and ring their people too.
     */
    fun forDashboard(ctx: Context, keys: FxKeys): ByteArray {
        val list = JSONArray()
        all(ctx).forEach { list.put(JSONObject().put("id", it.id).put("key", it.key).put("host", it.host).put("name", it.name)
            .put("member", Store.circleMember(ctx)).put("myName", Store.myName(ctx)).put("device", Store.device(ctx))
            .put("canRing", it.canRing).put("mine", it.mine)) }
        return keys.seal(keys.report, "findx circles", JSONObject().put("v", 1).put("circles", list).toString().toByteArray())
    }

    // ------------------------------------------------------------ making, joining, leaving

    private fun sealInfo(c: Circle): String = FxKeys.b64(Seal.seal(c.seal, JSONObject().put("v", 1).put("name", c.name)
        .put("places", JSONArray().apply { c.places.forEach { put(it.json()) } }).toString().toByteArray(), "findx circle info".toByteArray()))

    /** Makes a circle on your DDrive (only somebody signed in to a drive can make one there). */
    fun create(ctx: Context, name: String): Circle {
        val link = Link.of(ctx) ?: throw IllegalStateException("Link FindX to your DDrive first: circles live there")
        val c = Circle(FxKeys.hex(FxKeys.bytes(16)), FxKeys.b64url(FxKeys.bytes(32)), Store.host(ctx).trimEnd('/'),
            name.trim().ifEmpty { "Family" }.take(40), mine = true, joined = System.currentTimeMillis())
        link.client.postJson("/api/findx/circle/create", JSONObject().put("circle", c.id).put("auth", c.auth).put("info", sealInfo(c)))
        update(ctx, c)
        Log.note(ctx, "Made the circle \"${c.name}\"")
        return c
    }

    /** Joins with an invitation: its drive is asked for the circle's (sealed) name and places first. */
    fun join(ctx: Context, invite: String): Circle {
        val (host, id, key) = Circle.parseInvite(invite) ?: throw IllegalArgumentException("That isn't a FindX invitation")
        all(ctx).firstOrNull { it.id == id }?.let { return it }
        val probe = Circle(id, key, host, "")
        val o = CircleClient(host).call("info", JSONObject().put("circle", id).put("auth", probe.auth))
        val info = JSONObject(String(Seal.unseal(probe.seal, FxKeys.unb64(o.getString("info")), "findx circle info".toByteArray())))
        val places = (info.optJSONArray("places") ?: JSONArray()).let { a -> (0 until a.length()).map { Place.of(a.getJSONObject(it)) } }
        val c = probe.copy(name = info.optString("name", "Circle"), places = places, joined = System.currentTimeMillis())
        update(ctx, c)
        Log.note(ctx, "Joined the circle \"${c.name}\"")
        lastSent.remove(c.id)
        return c
    }

    /** Leaves (and, for one you made, deletes it for everybody when [forEveryone]). */
    fun leave(ctx: Context, c: Circle, forEveryone: Boolean = false) {
        runCatching { CircleClient(c.host).call("leave", JSONObject().put("circle", c.id).put("auth", c.auth).put("member", Store.circleMember(ctx))) }
        if (forEveryone && c.mine) Link.of(ctx)?.client?.postJson("/api/findx/circle/delete", JSONObject().put("circle", c.id))
        save(ctx, all(ctx).filter { it.id != c.id })
        known.remove(c.id)
        Log.note(ctx, (if (forEveryone) "Deleted" else "Left") + " the circle \"${c.name}\"")
    }

    fun setCanRing(ctx: Context, c: Circle, on: Boolean) = update(ctx, c.copy(canRing = on))

    /** The circle's places changed (any member may change them): sealed for everybody. */
    fun setPlaces(ctx: Context, c: Circle, places: List<Place>) {
        val next = c.copy(places = places)
        CircleClient(c.host).call("setinfo", JSONObject().put("circle", c.id).put("auth", c.auth).put("info", sealInfo(next)))
        update(ctx, next)
    }

    /** Fetches a circle's name and places again (somebody else may have changed them). */
    fun refreshInfo(ctx: Context, c: Circle): Circle {
        val o = CircleClient(c.host).call("info", JSONObject().put("circle", c.id).put("auth", c.auth))
        val info = JSONObject(String(Seal.unseal(c.seal, FxKeys.unb64(o.getString("info")), "findx circle info".toByteArray())))
        val places = (info.optJSONArray("places") ?: JSONArray()).let { a -> (0 until a.length()).map { Place.of(a.getJSONObject(it)) } }
        val next = c.copy(name = info.optString("name", c.name), places = places)
        if (next != c) update(ctx, next)
        return next
    }

    // ------------------------------------------------------------ where everybody is

    /** The members of a circle (not this phone), opened. [watch]: a map of the circle is open. */
    fun members(ctx: Context, c: Circle, watch: Boolean = false): List<Member> {
        val me = Store.circleMember(ctx)
        val a = CircleClient(c.host).call("members", JSONObject().put("circle", c.id).put("auth", c.auth).put("watch", watch)).optJSONArray("members") ?: JSONArray()
        val out = (0 until a.length()).mapNotNull { i ->
            val o = a.getJSONObject(i)
            if (o.optString("member") == me) return@mapNotNull null
            runCatching {
                val r = JSONObject(String(Seal.unseal(c.seal, FxKeys.unb64(o.getString("blob")), "findx circle report".toByteArray())))
                if (r.optString("m") != o.getString("member")) return@mapNotNull null
                Member(r.getString("m"), r.optString("p").ifEmpty { r.optString("n", "Someone") }, r.optString("n"), r.optLong("t"),
                    r.optJSONObject("fix")?.let { Report.fixOf(it) }, r.optInt("bat", -1), r.optBoolean("chg"),
                    r.optJSONObject("place")?.optString("name"), r.optLong("since"), r.optString("mode", "standby"),
                    r.optBoolean("canRing", true), r.optBoolean("ringing"))
            }.getOrNull()
        }
        known[c.id] = System.currentTimeMillis() to out
        return out
    }

    /** The members as last fetched, fetched again when older than [maxAgeMs]. */
    fun membersCached(ctx: Context, c: Circle, maxAgeMs: Long, watch: Boolean = false): List<Member> {
        val k = known[c.id]
        if (k != null && System.currentTimeMillis() - k.first < maxAgeMs && !watch) return k.second
        return runCatching { members(ctx, c, watch) }.getOrElse { k?.second ?: emptyList() }
    }

    /** Which of a circle's places a position is at, with a margin on the way out so it doesn't flicker at the edge. */
    fun placeAt(places: List<Place>, fix: Fix, wasAt: String?): Place? {
        places.firstOrNull { it.id == wasAt }?.let { p ->
            if (Geo.distance(p.lat, p.lon, fix.lat, fix.lon) <= p.r + LEAVE_MARGIN + minOf(fix.acc, 100.0)) return p
        }
        return places.filter { Geo.distance(it.lat, it.lon, fix.lat, fix.lon) <= it.r + minOf(fix.acc, 50.0) / 2 }
            .minByOrNull { Geo.distance(it.lat, it.lon, fix.lat, fix.lon) }
    }

    /**
     * Tells each circle where this phone is, when it's worth it: somebody is looking, it has moved
     * [MOVED_METRES], it arrived at or left a place (then everybody hears it), or [REPORT_EVERY_MS]
     * has passed. Returns whether anybody in a circle is watching.
     */
    fun reportAll(ctx: Context, r: Report, force: Boolean = false, watched: Set<String> = emptySet()): Boolean {
        val me = Store.circleMember(ctx)
        var watching = false
        for (c in all(ctx)) {
            val fix = r.fix
            val p = Store.prefs(ctx)
            val wasAt = p.getString("at:${c.id}", null)
            val at = fix?.let { placeAt(c.places, it, wasAt) }
            val arrived = at != null && at.id != wasAt
            val left = wasAt != null && at?.id != wasAt && fix != null
            if (fix != null && (arrived || left)) {
                p.edit().putString("at:${c.id}", at?.id).putLong("since:${c.id}", if (at != null) r.t else 0).apply()
                if (left) wasAt?.let { id -> c.places.firstOrNull { it.id == id } }?.let { event(ctx, c, "left", it) }
                if (arrived) event(ctx, c, "arrived", at!!)
            }
            val (sentAt, sentFix) = lastSent[c.id] ?: (0L to null)
            val moved = fix != null && (sentFix == null || Geo.distance(fix, sentFix) > MOVED_METRES)
            if (!(force || arrived || left || moved || c.id in watched || System.currentTimeMillis() - sentAt > REPORT_EVERY_MS)) continue
            val body = JSONObject().put("v", 1).put("m", me).put("p", Store.myName(ctx)).put("n", r.name).put("t", r.t).put("mode", r.mode)
                .put("bat", r.battery).put("chg", r.charging).put("canRing", c.canRing).put("ringing", r.ringing).apply {
                    fix?.let { put("fix", Report.fixJson(it)) }
                    at?.let { put("place", JSONObject().put("id", it.id).put("name", it.name)) }
                    if (at != null) put("since", p.getLong("since:${c.id}", r.t))
                }
            val blob = FxKeys.b64(Seal.seal(c.seal, body.toString().toByteArray(), "findx circle report".toByteArray()))
            runCatching {
                val o = CircleClient(c.host).call("report", JSONObject().put("circle", c.id).put("auth", c.auth).put("member", me).put("blob", blob))
                lastSent[c.id] = System.currentTimeMillis() to fix
                if (o.optBoolean("watching")) watching = true
            }.onFailure { if (it is HostError && it.status == 404) Log.note(ctx, "The circle \"${c.name}\" is gone") }
        }
        return watching
    }

    // ------------------------------------------------------------ commands: ring a friend, news for everybody

    private fun send(ctx: Context, c: Circle, to: String, what: String, args: JSONObject = JSONObject()) {
        val me = Store.circleMember(ctx)
        val cmd = JSONObject().put("c", what).put("to", to).put("from", me).put("fromName", Store.myName(ctx)).put("t", System.currentTimeMillis())
            .put("id", FxKeys.hex(FxKeys.bytes(8))).put("args", args)
        val blob = FxKeys.b64(Seal.seal(c.seal, cmd.toString().toByteArray(), "findx circle command".toByteArray()))
        CircleClient(c.host).call("command", JSONObject().put("circle", c.id).put("auth", c.auth).put("from", me).put("to", to).put("blob", blob))
    }

    fun ring(ctx: Context, c: Circle, member: String) = send(ctx, c, member, "ring")
    fun stopRing(ctx: Context, c: Circle, member: String) = send(ctx, c, member, "stop")

    private fun event(ctx: Context, c: Circle, kind: String, place: Place) {
        runCatching { send(ctx, c, "*", "event", JSONObject().put("kind", kind).put("place", place.id).put("placeName", place.name)) }
        Log.note(ctx, "${if (kind == "arrived") "Arrived at" else "Left"} ${place.name} (told \"${c.name}\")")
    }

    /** A command from a member, opened and acted on (if it's fresh, new, and for this phone). */
    fun act(ctx: Context, c: Circle, blob: String) {
        val o = runCatching { JSONObject(String(Seal.unseal(c.seal, FxKeys.unb64(blob), "findx circle command".toByteArray()))) }.getOrNull() ?: return
        val me = Store.circleMember(ctx)
        val now = System.currentTimeMillis()
        val to = o.optString("to")
        if ((to != me && to != "*") || o.optString("from") == me) return
        val t = o.optLong("t")
        if (t < now - Command.MAX_AGE_MS || t > now + 60_000) return
        synchronized(seen) {
            if (!seen.add(c.id + o.optString("id"))) return
            while (seen.size > 300) seen.remove(seen.first())
        }
        val from = o.optString("fromName").ifEmpty { "Someone" }.take(40)
        when (o.optString("c")) {
            "ring" -> if (c.canRing) {
                Log.note(ctx, "$from (${c.name}) rang this phone")
                Ring.start(ctx); Beacon.fast(ctx)
            } else Log.note(ctx, "$from (${c.name}) tried to ring this phone (not allowed in Settings of that circle)")
            "stop" -> Ring.stop(ctx)
            "event" -> {
                val a = o.optJSONObject("args") ?: return
                val what = if (a.optString("kind") == "arrived") "arrived at" else "left"
                notify(ctx, "$from $what ${a.optString("placeName", "a place").take(40)}", c.name)
            }
        }
    }

    private fun notify(ctx: Context, title: String, text: String) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val n = Notification.Builder(ctx, Service.CH_PEOPLE).setSmallIcon(R.drawable.ic_stat_findx).setContentTitle(title).setContentText(text)
            .setContentIntent(Service.openApp(ctx)).setAutoCancel(true).setCategory(Notification.CATEGORY_SOCIAL).build()
        nm.notify((title + text).hashCode(), n)
    }
}
