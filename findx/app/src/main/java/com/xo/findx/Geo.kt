package com.xo.findx

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/*
 * The arithmetic of finding: no Android in here, so all of it is unit tested.
 */

/** One position as a receiver gave it. [acc] is its radius in metres (about 68% sure), [t] milliseconds. */
data class Fix(
    val lat: Double, val lon: Double, val acc: Double, val t: Long,
    val alt: Double? = null, val speed: Double? = null, val bearing: Double? = null, val provider: String = "",
)

object Geo {
    const val EARTH = 6_371_008.8

    private fun rad(d: Double) = d * PI / 180
    private fun deg(r: Double) = r * 180 / PI

    /** Metres between two points (haversine). */
    fun distance(aLat: Double, aLon: Double, bLat: Double, bLon: Double): Double {
        val dLat = rad(bLat - aLat)
        val dLon = rad(bLon - aLon)
        val h = sin(dLat / 2).pow(2) + cos(rad(aLat)) * cos(rad(bLat)) * sin(dLon / 2).pow(2)
        return 2 * EARTH * asin(min(1.0, sqrt(h)))
    }

    fun distance(a: Fix, b: Fix) = distance(a.lat, a.lon, b.lat, b.lon)

    /** Compass bearing from a to b, degrees from north. */
    fun bearing(aLat: Double, aLon: Double, bLat: Double, bLon: Double): Double {
        val y = sin(rad(bLon - aLon)) * cos(rad(bLat))
        val x = cos(rad(aLat)) * sin(rad(bLat)) - sin(rad(aLat)) * cos(rad(bLat)) * cos(rad(bLon - aLon))
        return (deg(atan2(y, x)) + 360) % 360
    }

    /** East/north metres of a point from an origin (flat-earth, fine within a few km). */
    fun toLocal(oLat: Double, oLon: Double, lat: Double, lon: Double): Pair<Double, Double> =
        Pair(rad(lon - oLon) * EARTH * cos(rad(oLat)), rad(lat - oLat) * EARTH)

    fun fromLocal(oLat: Double, oLon: Double, east: Double, north: Double): Pair<Double, Double> =
        Pair(oLat + deg(north / EARTH), oLon + deg(east / (EARTH * cos(rad(oLat)))))
}

/**
 * Turns positions into a path as they come, the way a walk or a drive really goes. Phones give
 * positions of every quality: satellites (metres), Wi-Fi (tens of metres), a mobile mast (hundreds)
 * and now and then one that is simply wrong (a reflection, the wrong mast). Drawn as they come they
 * scribble: zigzags, and straight lines to places never been.
 *
 * A Kalman filter (position and speed, east and north) weighs each position by its accuracy, so a
 * rough one moves the path a little and a good one a lot. A position that the path so far can't
 * explain (further than the speed it was going at, give or take how unsure both are) is left out;
 * so is a rough one while good ones are coming in, and one older than the last. If several left out
 * in a row agree with each other, the phone really did get there (a train, a gap in the positions):
 * the path starts again from them.
 */
class PathFilter {
    private class Axis {
        var p = 0.0; var v = 0.0
        var a = 0.0; var b = 0.0; var c = 0.0          // covariance [[a, b], [b, c]]

        fun reset(z: Double, r: Double, v0: Double, vv: Double) { p = z; v = v0; a = r; b = 0.0; c = vv }
        fun predict(dt: Double, q: Double) {
            p += v * dt
            a += 2 * dt * b + dt * dt * c + q * dt.pow(4) / 4
            b += dt * c + q * dt.pow(3) / 2
            c += q * dt * dt
        }
        fun innovation(z: Double) = z - p
        fun spread(r: Double) = a + r
        fun update(z: Double, r: Double) {
            val s = a + r
            val k0 = a / s; val k1 = b / s
            val y = z - p
            p += k0 * y; v += k1 * y
            val a0 = a; val b0 = b
            a = (1 - k0) * a0; b = (1 - k0) * b0; c -= k1 * b0
        }
    }

    private val x = Axis()
    private val y = Axis()
    private var origin: Fix? = null
    private var t = 0L
    private var goodAt = 0L
    private var goodAcc = 0.0
    private val doubted = ArrayList<Fix>()

    /** Kept positions so far (for tests and the log). */
    var kept = 0
        private set
    var dropped = 0
        private set

    /** The cleaned position for [f], or null when it is left out. */
    fun add(f: Fix): Fix? {
        if (f.acc > MAX_ACC || f.acc <= 0.0) { dropped++; return null }
        val o = origin
        if (o == null || f.t - t > RESTART_MS) return start(f)
        if (f.t <= t) { dropped++; return null }
        // A rough position while good ones are coming in says less than they do: wait for the next good one.
        if (goodAt > 0 && f.t - goodAt < 60_000 && f.acc > max(ROUGH, 4 * goodAcc)) { dropped++; return null }
        val dt = (f.t - t) / 1000.0
        val speed = sqrt(x.v * x.v + y.v * y.v)
        val q = 0.05 + 0.02 * speed * speed                // walking turns gently; a car can turn hard
        val (e, n) = Geo.toLocal(o.lat, o.lon, f.lat, f.lon)
        val r = f.acc * f.acc
        val px = Axis().also { copy(x, it) }; val py = Axis().also { copy(y, it) }
        px.predict(dt, q); py.predict(dt, q)
        val ix = px.innovation(e); val iy = py.innovation(n)
        val d2 = ix * ix / px.spread(r) + iy * iy / py.spread(r)
        if (d2 > GATE && hypot(ix, iy) > f.acc + 10) {
            dropped++
            doubted.add(f)
            // Several in a row that agree with each other: it really is there.
            if (doubted.size >= 3 && doubted.takeLast(3).zipWithNext().all { (a, b) -> a.acc <= ROUGH && b.acc <= ROUGH &&
                    Geo.distance(a, b) <= (a.acc + b.acc) + MAX_SPEED * max(1.0, (b.t - a.t) / 1000.0) }) {
                val last = doubted.last()
                return start(last)
            }
            if (doubted.size > 6) doubted.removeAt(0)
            return null
        }
        doubted.clear()
        copy(px, x); copy(py, y)
        x.update(e, r); y.update(n, r)
        t = f.t
        if (f.acc <= GOOD) { goodAt = f.t; goodAcc = f.acc }
        kept++
        return out(f)
    }

    private fun start(f: Fix): Fix {
        origin = f
        doubted.clear()
        val vv = if (f.speed != null && f.bearing != null) 1.0 else 25.0
        val sp = f.speed ?: 0.0
        val br = Math.toRadians(f.bearing ?: 0.0)
        x.reset(0.0, f.acc * f.acc, if (f.bearing != null) sp * sin(br) else 0.0, vv)
        y.reset(0.0, f.acc * f.acc, if (f.bearing != null) sp * cos(br) else 0.0, vv)
        t = f.t
        if (f.acc <= GOOD) { goodAt = f.t; goodAcc = f.acc }
        kept++
        return f
    }

    private fun out(f: Fix): Fix {
        val o = origin!!
        val (lat, lon) = Geo.fromLocal(o.lat, o.lon, x.p, y.p)
        return f.copy(lat = lat, lon = lon, acc = min(f.acc, sqrt(max(x.a, y.a))))
    }

    private fun copy(from: Axis, to: Axis) { to.p = from.p; to.v = from.v; to.a = from.a; to.b = from.b; to.c = from.c }

    fun reset() { origin = null; doubted.clear(); goodAt = 0 }

    companion object {
        const val MAX_ACC = 150.0          // a mast's guess: says which town, not which street
        const val ROUGH = 60.0
        const val GOOD = 25.0
        const val GATE = 13.8              // chi-squared, 2 degrees of freedom, 99.9%
        const val MAX_SPEED = 50.0         // m/s: a fast train
        const val RESTART_MS = 90_000L
    }
}

/**
 * Pins down where a phone lies while it lies still. A single satellite fix wanders by metres from
 * second to second; averaged over time the wandering cancels out, though not all of it: the errors of
 * fixes a few seconds apart are related (the same satellites, the same reflections off walls), so
 * the estimate only gets better with time, not with how many fixes came in. That is what [accuracy]
 * says, and it never claims better than [FLOOR] metres, which is what a phone's receiver can honestly
 * do even after a long wait.
 *
 * Fixes far from the rest (a reflection, a jump to a mobile mast) are left out.
 */
class Settle {
    private val fixes = ArrayList<Fix>()

    val count get() = fixes.size

    fun add(f: Fix) {
        if (f.acc <= 0 || f.acc > MAX_ACC || f.lat.isNaN() || f.lon.isNaN()) return
        fixes.add(f)
        if (fixes.size > 600) fixes.removeAt(0)
    }

    fun clear() = fixes.clear()

    /** The best estimate so far, or null with nothing to go on. */
    fun estimate(): Fix? {
        if (fixes.isEmpty()) return null
        val best = fixes.minBy { it.acc }
        if (fixes.size == 1) return best
        val o = best
        val pts = fixes.map { f -> Geo.toLocal(o.lat, o.lon, f.lat, f.lon) to f }
        // The centre to judge outliers by: the median of east and north, which a few wild fixes can't move.
        val mE = median(pts.map { it.first.first })
        val mN = median(pts.map { it.first.second })
        val kept = pts.filter { (p, f) -> hypot(p.first - mE, p.second - mN) <= 3 * max(f.acc, 3.0) }
            .ifEmpty { return best }
        var sw = 0.0; var se = 0.0; var sn = 0.0
        for ((p, f) in kept) {
            val w = 1 / (f.acc * f.acc)
            sw += w; se += w * p.first; sn += w * p.second
        }
        val e = se / sw
        val n = sn / sw
        val (lat, lon) = Geo.fromLocal(o.lat, o.lon, e, n)
        val span = (kept.maxOf { it.second.t } - kept.minOf { it.second.t }).coerceAtLeast(0)
        val independent = min(kept.size.toDouble(), 1 + span / CORRELATION_MS)
        // 1/sqrt(sum w) is the radius if every fix were independent; correlated ones count as fewer.
        val acc = max(FLOOR, sqrt(1 / sw) * sqrt(kept.size / independent))
        val alts = kept.mapNotNull { it.second.alt }
        return Fix(lat, lon, min(acc, best.acc), kept.maxOf { it.second.t },
            alt = if (alts.isEmpty()) null else median(alts), provider = "settled")
    }

    /** True once waiting longer hardly helps: the estimate is at the floor, or has stopped getting better. */
    fun done(now: Long): Boolean {
        val e = estimate() ?: return false
        if (e.acc <= FLOOR + 0.05) return true
        val first = fixes.first().t
        return now - first >= MAX_MS
    }

    companion object {
        const val FLOOR = 1.0               // metres: what a phone's receiver can honestly claim
        const val MAX_ACC = 2_000.0         // fixes vaguer than this say nothing about a table
        const val CORRELATION_MS = 15_000.0 // fixes this far apart have errors of their own
        const val MAX_MS = 90_000L          // a precise locate gives up waiting after this

        fun median(xs: List<Double>): Double {
            val s = xs.sorted()
            return if (s.size % 2 == 1) s[s.size / 2] else (s[s.size / 2 - 1] + s[s.size / 2]) / 2
        }

        private fun hypot(a: Double, b: Double) = sqrt(a * a + b * b)
    }
}

/**
 * Bluetooth signal strength, smoothed (a one-dimensional Kalman filter), and what it says about how far
 * away the other phone is. Signal strength jumps by 5-10 dB from one reading to the next (a hand, a
 * body, a reflection), so a single reading means little; the smoothed value moves steadily.
 */
class SignalFilter(private val processNoise: Double = 0.8, private val measureNoise: Double = 16.0) {
    var value = Double.NaN
        private set
    private var p = 1.0
    private val history = ArrayDeque<Pair<Long, Double>>()

    fun add(rssi: Int, t: Long): Double {
        if (value.isNaN()) {
            value = rssi.toDouble(); p = measureNoise
        } else {
            p += processNoise
            val k = p / (p + measureNoise)
            value += k * (rssi - value)
            p *= 1 - k
        }
        history.addLast(t to value)
        while (history.size > 2 && t - history.first().first > 6_000) history.removeFirst()
        return value
    }

    /** dB per second over the last few seconds: positive means getting closer. */
    fun trend(): Double {
        if (history.size < 3) return 0.0
        val (t0, v0) = history.first()
        val (t1, v1) = history.last()
        val dt = (t1 - t0) / 1000.0
        return if (dt < 1.5) 0.0 else (v1 - v0) / dt
    }

    fun reset() { value = Double.NaN; history.clear() }

    companion object {
        /** Signal strength at one metre of a phone advertising fast at high power (measured on phones; they vary by a few dB). */
        const val AT_1M = -59.0

        /** Metres for a smoothed signal strength; [n] is how fast signal fades indoors (2 in the open, up to 3 through things). */
        fun metres(rssi: Double, atOneMetre: Double = AT_1M, n: Double = 2.2): Double =
            10.0.pow((atOneMetre - rssi) / (10 * n)).coerceIn(0.05, 60.0)

        fun words(m: Double) = when {
            m < 0.5 -> "Right here"
            m < 1.5 -> "Within arm's reach"
            m < 4 -> "In this room"
            m < 10 -> "Nearby"
            else -> "Further away"
        }
    }
}

/** Which way up the phone lies, from gravity (x, y, z in m/s² as the accelerometer gives it). */
enum class Pose(val words: String) {
    FaceUp("Lying face up"), FaceDown("Lying face down"), Upright("Standing upright"),
    OnSide("On its side"), Tilted("Propped up at an angle"), Unknown("");

    companion object {
        fun of(x: Double, y: Double, z: Double): Pose {
            val g = sqrt(x * x + y * y + z * z)
            if (g < 7 || g > 12.5) return Unknown       // moving or falling: no steady gravity
            val nz = z / g
            val ny = y / g
            val nx = x / g
            return when {
                nz > 0.94 -> FaceUp
                nz < -0.94 -> FaceDown
                abs(ny) > 0.9 -> Upright
                abs(nx) > 0.9 -> OnSide
                else -> Tilted
            }
        }
    }
}

/**
 * A spot in a home or office, as the phone senses it lying there: the Wi-Fi access points it hears and
 * how loud, the distance to those that measure it (Wi-Fi round-trip time, about a metre), the strength
 * of the magnetic field (steel in furniture and walls bends it differently a metre away) and the air
 * pressure (which floor). None of it needs satellites, so it works where they don't: indoors.
 */
data class Signature(
    val wifi: Map<String, Int> = emptyMap(),       // access point -> dBm
    val rtt: Map<String, Double> = emptyMap(),     // access point -> metres
    val magnet: Double? = null,                     // µT
    val pressure: Double? = null,                   // hPa
)

object Spots {
    /**
     * How alike two signatures are, 0 (nothing alike) to 1 (the same spot). Each kind of evidence that
     * both have counts; with none in common the answer is 0.
     */
    fun similarity(a: Signature, b: Signature): Double {
        var sum = 0.0
        var weight = 0.0
        if (a.wifi.isNotEmpty() && b.wifi.isNotEmpty()) {
            // The 20 loudest of either. One heard only on one side counts as heard at -100 on the other.
            val keys = (a.wifi.entries.sortedByDescending { it.value }.take(20).map { it.key } +
                b.wifi.entries.sortedByDescending { it.value }.take(20).map { it.key }).toSet()
            var s = 0.0; var w = 0.0
            for (k in keys) {
                val x = a.wifi[k] ?: -100
                val y = b.wifi[k] ?: -100
                val loud = (max(x, y) + 100).coerceAtLeast(1).toDouble()   // loud ones say more
                s += loud * (1 - min(1.0, abs(x - y) / 25.0))
                w += loud
            }
            if (w > 0) { sum += 3 * s / w; weight += 3 }
        }
        val common = a.rtt.keys intersect b.rtt.keys
        if (common.isNotEmpty()) {
            val s = common.map { 1 - min(1.0, abs(a.rtt[it]!! - b.rtt[it]!!) / 3.0) }.average()
            sum += 4 * s; weight += 4                   // a metre or two is what round-trip time can tell apart
        }
        if (a.magnet != null && b.magnet != null) {
            sum += 1 - min(1.0, abs(a.magnet - b.magnet) / 12.0); weight += 1
        }
        if (a.pressure != null && b.pressure != null) {
            // ~0.12 hPa a floor; the weather moves pressure too, so only big differences rule a spot out.
            sum += 1 - min(1.0, abs(a.pressure - b.pressure) / 1.5); weight += 1
        }
        return if (weight == 0.0) 0.0 else sum / weight
    }

    /** The saved spot most like [now], with how alike, if any is alike enough to say so. */
    fun <T> best(now: Signature, saved: List<Pair<T, Signature>>, threshold: Double = 0.55): Pair<T, Double>? =
        saved.map { it.first to similarity(now, it.second) }.filter { it.second >= threshold }.maxByOrNull { it.second }
}

/**
 * Where the sun is, for the map's day and night (NOAA's simplified formulas, good to a fraction of a
 * degree, which is plenty to tell dusk from night).
 */
object Sun {
    /** Height of the sun above the horizon in degrees at [t] (ms) at a place. */
    fun altitude(lat: Double, lon: Double, t: Long): Double {
        val d = t / 86_400_000.0 + 2440587.5 - 2451545.0       // days since J2000
        val g = Math.toRadians((357.529 + 0.98560028 * d) % 360)
        val q = (280.459 + 0.98564736 * d) % 360
        val l = Math.toRadians((q + 1.915 * sin(g) + 0.020 * sin(2 * g)) % 360)
        val e = Math.toRadians(23.439 - 0.00000036 * d)
        val ra = atan2(cos(e) * sin(l), cos(l))
        val dec = asin(sin(e) * sin(l))
        val gmst = (18.697374558 + 24.06570982441908 * d) % 24
        val ha = Math.toRadians(gmst * 15 + lon) - ra
        val phi = Math.toRadians(lat)
        return Math.toDegrees(asin(sin(phi) * sin(dec) + cos(phi) * cos(dec) * cos(ha)))
    }

    /** 0 at night, 1 in daylight, in between through twilight (sun from -6° to +3°). */
    fun daylight(lat: Double, lon: Double, t: Long): Double = ((altitude(lat, lon, t) + 6) / 9).coerceIn(0.0, 1.0)
}
