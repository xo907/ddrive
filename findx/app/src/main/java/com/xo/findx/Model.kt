package com.xo.findx

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream
import kotlin.math.roundToLong

/*
 * What FindX says, in the shapes it says it in: a phone's report (where it is and how it lies), a
 * command to a phone, and an hour of history. Plain JSON before it is sealed; the browser reads the
 * same (see PROTOCOL.md).
 */

/** Where a phone is and what it senses, as one moment's report. */
data class Report(
    val device: String,
    val name: String,
    val t: Long,
    val fix: Fix? = null,
    val settled: Boolean = false,      // the fix is an average over a still phone, not one reading
    val stillSince: Long = 0,          // when it last moved (0: moving, or not known)
    val pose: Pose = Pose.Unknown,
    val lux: Double? = null,
    val covered: Boolean = false,      // something against the screen (a pocket, a cushion, face down on cloth)
    val spot: String? = null,          // the saved spot it seems to lie at
    val spotScore: Double = 0.0,
    val floorDelta: Double? = null,    // floors above (+) or below (-) that spot, from air pressure
    val battery: Int = -1,
    val charging: Boolean = false,
    val network: String = "",
    val mode: String = "standby",      // standby | live | locating
    val ringing: Boolean = false,
    val lost: Boolean = false,
    val trail: List<Fix> = emptyList(),
    val use: JSONObject? = null,       // FindX's own battery use today: {mah, pct} (Meter)
    val cfg: JSONObject? = null,       // how it is set up (Remote.settings), for managing it from afar
    val problems: List<String> = emptyList(),   // what keeps it from being found as well as it could be
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("v", 1); put("d", device); put("n", name); put("t", t)
        fix?.let { put("fix", fixJson(it).put("settled", settled)) }
        if (stillSince > 0) put("still", stillSince)
        if (pose != Pose.Unknown) put("pose", pose.name)
        lux?.let { put("lux", round(it, 1)) }
        if (covered) put("covered", true)
        spot?.let { put("spot", JSONObject().put("name", it).put("score", round(spotScore, 2))) }
        floorDelta?.let { put("floor", round(it, 1)) }
        if (battery >= 0) put("bat", battery)
        if (charging) put("chg", true)
        if (network.isNotEmpty()) put("net", network)
        put("mode", mode)
        if (ringing) put("ring", true)
        if (lost) put("lost", true)
        use?.let { put("use", it) }
        cfg?.let { put("cfg", it) }
        if (problems.isNotEmpty()) put("ok", JSONArray().apply { problems.forEach { put(it) } })
        if (trail.isNotEmpty()) put("trail", JSONArray().apply {
            trail.forEach { put(JSONArray().put(round(it.lat, 7)).put(round(it.lon, 7)).put(it.t).put(round(it.acc, 1))) }
        })
    }

    /** What someone you share with sees: no spots, no trail, and with [approximate] only the town. */
    fun forShare(approximate: Boolean): Report {
        val f = fix ?: return copy(spot = null, trail = emptyList())
        val shown = if (!approximate) f else f.copy(
            lat = (f.lat * 100).roundToLong() / 100.0, lon = (f.lon * 100).roundToLong() / 100.0,
            acc = maxOf(f.acc, 1_000.0), alt = null, speed = null, bearing = null,
        )
        return copy(fix = shown, spot = null, spotScore = 0.0, floorDelta = null, trail = emptyList(), use = null, cfg = null, problems = emptyList(),
            pose = if (approximate) Pose.Unknown else pose, lux = null, covered = false)
    }

    companion object {
        fun fixJson(f: Fix) = JSONObject().apply {
            put("lat", round(f.lat, 7)); put("lon", round(f.lon, 7)); put("acc", round(f.acc, 1)); put("t", f.t)
            f.alt?.let { put("alt", round(it, 1)) }
            f.speed?.let { put("spd", round(it, 2)) }
            f.bearing?.let { put("brg", round(it, 0)) }
            if (f.provider.isNotEmpty()) put("src", f.provider)
        }

        fun fixOf(o: JSONObject) = Fix(
            o.getDouble("lat"), o.getDouble("lon"), o.getDouble("acc"), o.getLong("t"),
            alt = o.optDouble("alt").takeIf { !it.isNaN() }, speed = o.optDouble("spd").takeIf { !it.isNaN() },
            bearing = o.optDouble("brg").takeIf { !it.isNaN() }, provider = o.optString("src"),
        )

        fun of(o: JSONObject): Report {
            val fix = o.optJSONObject("fix")
            val spot = o.optJSONObject("spot")
            val trail = o.optJSONArray("trail")
            return Report(
                device = o.getString("d"), name = o.optString("n"), t = o.getLong("t"),
                fix = fix?.let { fixOf(it) }, settled = fix?.optBoolean("settled") ?: false,
                stillSince = o.optLong("still"),
                pose = runCatching { Pose.valueOf(o.optString("pose")) }.getOrDefault(Pose.Unknown),
                lux = o.optDouble("lux").takeIf { !it.isNaN() }, covered = o.optBoolean("covered"),
                spot = spot?.optString("name"), spotScore = spot?.optDouble("score") ?: 0.0,
                floorDelta = o.optDouble("floor").takeIf { !it.isNaN() },
                battery = o.optInt("bat", -1), charging = o.optBoolean("chg"), network = o.optString("net"),
                mode = o.optString("mode", "standby"), ringing = o.optBoolean("ring"), lost = o.optBoolean("lost"),
                use = o.optJSONObject("use"), cfg = o.optJSONObject("cfg"),
                problems = o.optJSONArray("ok")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList(),
                trail = if (trail == null) emptyList() else (0 until trail.length()).map {
                    val a = trail.getJSONArray(it); Fix(a.getDouble(0), a.getDouble(1), a.getDouble(3), a.getLong(2))
                },
            )
        }

        fun round(x: Double, places: Int): Double {
            var m = 1.0
            repeat(places) { m *= 10 }
            return (x * m).roundToLong() / m
        }
    }
}

/** Something asked of a phone: by you on DDrive, or by another of your phones. */
data class Command(
    val what: String,            // ring | stop | locate | live | lost | found | beacon
    val to: String,              // the device it is for
    val from: String,            // the device (or "web") that asks
    val t: Long,
    val id: String,
    val args: JSONObject = JSONObject(),
) {
    fun toJson(): JSONObject = JSONObject().put("c", what).put("to", to).put("from", from).put("t", t).put("id", id).put("args", args)

    companion object {
        val KINDS = setOf("ring", "stop", "locate", "live", "lost", "found", "beacon",
            // managing it from afar (Remote.kt)
            "set", "share", "unshare", "stats", "join", "leave", "circleRing", "circles")
        const val MAX_AGE_MS = 10 * 60_000L    // older than this (or from the future): not acted on

        fun of(o: JSONObject) = Command(o.getString("c"), o.getString("to"), o.optString("from"), o.getLong("t"), o.getString("id"),
            o.optJSONObject("args") ?: JSONObject())
    }
}

/**
 * A place in history: a point on the way somewhere, or a stay (a place it lay still at from [t] to
 * [until]). Stays are what make history small: a night on the nightstand is one entry, not thousands.
 */
data class Point(val t: Long, val lat: Double, val lon: Double, val acc: Double, val until: Long = 0) {
    val stay get() = until > t
}

/** History is kept in hour-long pieces: the phone fills one, and DDrive keeps each for good. */
object Segments {
    const val HOUR = 3_600_000L
    const val LABEL = "findx history"

    fun hourOf(t: Long) = t / HOUR

    /** Compact form: [seconds, lat×1e6, lon×1e6, accuracy in dm, until in seconds or 0]. */
    fun encode(device: String, hour: Long, points: List<Point>): ByteArray {
        val a = JSONArray()
        for (p in points.sortedBy { it.t }) {
            a.put(JSONArray().put(p.t / 1000).put((p.lat * 1e6).roundToLong()).put((p.lon * 1e6).roundToLong())
                .put((p.acc * 10).roundToLong()).put(p.until / 1000))
        }
        val json = JSONObject().put("v", 1).put("d", device).put("h", hour).put("p", a).toString().toByteArray()
        val out = ByteArrayOutputStream()
        GZIPOutputStream(out).use { it.write(json) }
        return out.toByteArray()
    }

    fun decode(bytes: ByteArray): Triple<String, Long, List<Point>> {
        val o = JSONObject(GZIPInputStream(ByteArrayInputStream(bytes)).use { it.readBytes() }.toString(Charsets.UTF_8))
        val a = o.getJSONArray("p")
        val pts = (0 until a.length()).map {
            val e = a.getJSONArray(it)
            Point(e.getLong(0) * 1000, e.getLong(1) / 1e6, e.getLong(2) / 1e6, e.getLong(3) / 10.0, e.getLong(4) * 1000)
        }
        return Triple(o.getString("d"), o.getLong("h"), pts)
    }

    /**
     * Where an hour of a device's history lives on DDrive: /FindX/h/<month name>/<hour name>.fxh, both
     * names made with the names key, so the folder doesn't show when you were out, or even which months.
     */
    fun path(keys: FxKeys, device: String, hour: Long): String {
        val month = java.time.Instant.ofEpochMilli(hour * HOUR).atZone(java.time.ZoneOffset.UTC).let { "${it.year}-${it.monthValue}" }
        return "${Store.ROOT}/h/${keys.name("m|$device|$month", 16)}/${keys.name("h|$device|$hour")}.fxh"
    }

    /**
     * Thins out a run of points: a point that adds nothing (it lies within [metres] of the line between
     * its neighbours, Douglas-Peucker) is dropped. Stays are always kept.
     */
    fun simplify(points: List<Point>, metres: Double = 8.0): List<Point> {
        if (points.size < 3) return points
        val keep = BooleanArray(points.size)
        keep[0] = true; keep[points.lastIndex] = true
        points.forEachIndexed { i, p -> if (p.stay) keep[i] = true }
        fun go(a: Int, b: Int) {
            if (b <= a + 1) return
            val pa = points[a]; val pb = points[b]
            val (bx, by) = Geo.toLocal(pa.lat, pa.lon, pb.lat, pb.lon)
            val len2 = bx * bx + by * by
            var far = -1; var farD = -1.0
            for (i in a + 1 until b) {
                val (px, py) = Geo.toLocal(pa.lat, pa.lon, points[i].lat, points[i].lon)
                val u = if (len2 == 0.0) 0.0 else ((px * bx + py * by) / len2).coerceIn(0.0, 1.0)
                val dx = px - u * bx; val dy = py - u * by
                val d = kotlin.math.sqrt(dx * dx + dy * dy)
                if (d > farD) { farD = d; far = i }
            }
            if (farD > metres) { keep[far] = true; go(a, far); go(far, b) }
        }
        go(0, points.lastIndex)
        return points.filterIndexed { i, _ -> keep[i] }
    }
}
