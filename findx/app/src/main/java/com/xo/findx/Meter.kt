package com.xo.findx

import android.content.Context
import android.content.res.Resources
import android.net.TrafficStats
import android.os.BatteryManager
import android.os.Process
import android.os.SystemClock
import org.json.JSONObject
import org.xmlpull.v1.XmlPullParser
import java.io.File
import java.util.Calendar
import kotlin.math.abs

/**
 * FindX's own battery meter: what it does, counted as it happens, kept by the hour and by the day
 * (Energy turns it into charge). Exact where Android lets an app be exact:
 *
 * - processor time: the kernel's count for FindX's process,
 * - network: the kernel's byte count for FindX (TrafficStats), and every time FindX woke the radio
 *   (a request while the radio was still awake from the last one doesn't count again),
 * - satellites, Bluetooth, sensors, ringing: timed from when FindX turned them on to when it turned
 *   them off.
 *
 * Android's own per-app figure needs a permission only system apps have, so Settings links to it for
 * comparison. Nothing here leaves the phone, except today's total in FindX's sealed reports.
 */
object Meter {
    private const val HOURS_KEPT = 72
    private const val DAYS_KEPT = 60
    private val lock = Any()
    @Volatile private var loaded: JSONObject? = null

    private fun file(ctx: Context) = File(ctx.filesDir, "meter.json")

    private fun data(ctx: Context): JSONObject = loaded ?: synchronized(lock) {
        loaded ?: (runCatching { JSONObject(file(ctx).readText()) }.getOrNull() ?: JSONObject()).also { loaded = it }
    }

    @Volatile private var savedAt = 0L

    /** Writes it down at most once a minute (writing costs battery too), and always when [now]. */
    private fun save(ctx: Context, now: Boolean = false) {
        if (!now && SystemClock.elapsedRealtime() - savedAt < 60_000) return
        savedAt = SystemClock.elapsedRealtime()
        val d = loaded ?: return
        val tmp = File(file(ctx).path + ".tmp")
        tmp.writeText(d.toString())
        if (!tmp.renameTo(file(ctx))) { file(ctx).delete(); tmp.renameTo(file(ctx)) }
    }

    fun hourOf(t: Long) = t / 3_600_000L

    /** Local calendar day number (so "today" starts at your midnight). */
    fun dayOf(t: Long): Long {
        val c = Calendar.getInstance().apply { timeInMillis = t }
        return c.get(Calendar.YEAR) * 1000L + c.get(Calendar.DAY_OF_YEAR)
    }

    /** Adds to this hour and this day. */
    fun add(ctx: Context, u: Usage, t: Long = System.currentTimeMillis()) = synchronized(lock) {
        val d = data(ctx)
        val hours = d.optJSONObject("hours") ?: JSONObject().also { d.put("hours", it) }
        val days = d.optJSONObject("days") ?: JSONObject().also { d.put("days", it) }
        val h = hourOf(t).toString()
        val dy = dayOf(t).toString()
        hours.put(h, (hours.optJSONObject(h)?.let { Usage.of(it) } ?: Usage()).plus(u).json())
        days.put(dy, (days.optJSONObject(dy)?.let { Usage.of(it) } ?: Usage()).plus(u).json())
        val now = hourOf(t)
        hours.keys().asSequence().toList().filter { (it.toLongOrNull() ?: 0) < now - HOURS_KEPT }.forEach { hours.remove(it) }
        if (days.length() > DAYS_KEPT) days.keys().asSequence().toList().sortedBy { it.toLong() }.dropLast(DAYS_KEPT).forEach { days.remove(it) }
        save(ctx)
    }

    // ------------------------------------------------------------ the kernel's counts

    /**
     * Brings processor time and network bytes up to date (the service calls it every few minutes, and
     * the battery screen when it opens), and adds what the parts still on have used so far.
     */
    fun tick(ctx: Context) {
        val d = synchronized(lock) { data(ctx) }
        var u = Usage()
        val pid = Process.myPid()
        val cpu = Process.getElapsedCpuTime()
        synchronized(lock) {
            val lastPid = d.optInt("pid", -1)
            val lastCpu = if (lastPid == pid) d.optLong("cpu", 0) else 0
            if (cpu >= lastCpu) u = u.copy(cpuMs = cpu - lastCpu)
            d.put("pid", pid).put("cpu", cpu)
            val uid = Process.myUid()
            val bytes = TrafficStats.getUidRxBytes(uid).coerceAtLeast(0) + TrafficStats.getUidTxBytes(uid).coerceAtLeast(0)
            val bootAt = System.currentTimeMillis() - SystemClock.elapsedRealtime()
            val sameBoot = abs(d.optLong("boot", 0) - bootAt) < 60_000
            val lastBytes = if (sameBoot) d.optLong("bytes", 0) else 0
            val got = (bytes - lastBytes).coerceAtLeast(0)
            d.put("bytes", bytes).put("boot", bootAt)
            u = if (Sensors.network(ctx) == "cell") u.copy(bytesCell = got) else u.copy(bytesWifi = got)
        }
        u += runningSoFar()
        add(ctx, u)
        synchronized(lock) { save(ctx, now = true) }
    }

    // ------------------------------------------------------------ what FindX turns on

    private val since = HashMap<String, Long>()     // part -> when it was turned on (or last counted)
    private val users = HashMap<String, Int>()      // part -> how many things have it on

    /** The app, for counting from code that has no context at hand (network requests). */
    @Volatile var app: Context? = null

    /** A part turned on (satellites, beaconLow, beaconFast, scan). Counted until [off]; two at once count once. */
    fun on(part: String) = synchronized(since) {
        users[part] = (users[part] ?: 0) + 1
        since.putIfAbsent(part, SystemClock.elapsedRealtime())
    }

    fun off(ctx: Context, part: String) {
        val t0 = synchronized(since) {
            val n = (users[part] ?: 0) - 1
            if (n > 0) { users[part] = n; return }
            users.remove(part)
            since.remove(part)
        } ?: return
        add(ctx, usageOf(part, SystemClock.elapsedRealtime() - t0))
    }

    fun isOn(part: String) = synchronized(since) { part in since }

    /** What the parts still on have used since last counted (they go on being counted). */
    private fun runningSoFar(): Usage = synchronized(since) {
        val now = SystemClock.elapsedRealtime()
        var u = Usage()
        for ((part, t0) in since) u += usageOf(part, now - t0)
        since.keys.toList().forEach { since[it] = now }
        u
    }

    /** A part that was on for a known time (sensor bursts, ringing, the app on screen). */
    fun span(ctx: Context, part: String, ms: Long) { if (ms > 0) add(ctx, usageOf(part, ms)) }

    private fun usageOf(part: String, ms: Long) = when (part) {
        "satellites" -> Usage(gpsMs = ms)
        "beaconLow" -> Usage(bleLowMs = ms)
        "beaconFast" -> Usage(bleFastMs = ms)
        "scan" -> Usage(scanMs = ms)
        "sensors" -> Usage(sensorMs = ms)
        "ringing" -> Usage(ringMs = ms)
        "ui" -> Usage(uiMs = ms)
        else -> Usage()
    }

    @Volatile private var lastWake = 0L
    @Volatile private var lastWakeCell = false

    /**
     * FindX is about to send or fetch something. Counts as waking the radio unless it is still awake
     * from the last time (its "tail": 6 s on mobile data, a fraction of a second on Wi-Fi).
     */
    fun wake() { app?.let { wake(it) } }

    fun wake(ctx: Context) {
        val now = SystemClock.elapsedRealtime()
        val cell = Sensors.network(ctx) == "cell"
        val tail = ((if (lastWakeCell) Profile.CELL_TAIL_S else Profile.WIFI_TAIL_S) * 1000).toLong()
        if (now - lastWake > tail || cell != lastWakeCell) add(ctx, if (cell) Usage(wakeCell = 1) else Usage(wakeWifi = 1))
        lastWake = now
        lastWakeCell = cell
    }

    // ------------------------------------------------------------ reading it back

    fun hour(ctx: Context, hour: Long): Usage = synchronized(lock) { data(ctx).optJSONObject("hours")?.optJSONObject(hour.toString())?.let { Usage.of(it) } ?: Usage() }

    fun day(ctx: Context, day: Long): Usage = synchronized(lock) { data(ctx).optJSONObject("days")?.optJSONObject(day.toString())?.let { Usage.of(it) } ?: Usage() }

    /** The last [n] days, oldest first, as (day start ms, usage). */
    fun days(ctx: Context, n: Int): List<Pair<Long, Usage>> {
        val c = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
        return (n - 1 downTo 0).map { back ->
            val start = (c.clone() as Calendar).apply { add(Calendar.DAY_OF_YEAR, -back) }.timeInMillis
            start to day(ctx, dayOf(start))
        }
    }

    /** The last 24 hours, oldest first, as (hour start ms, usage). */
    fun hours(ctx: Context, n: Int = 24): List<Pair<Long, Usage>> {
        val now = hourOf(System.currentTimeMillis())
        return (now - n + 1..now).map { it * 3_600_000L to hour(ctx, it) }
    }

    /** Days FindX has been counting (for averages that don't pretend). */
    fun daysCounted(ctx: Context): Int = synchronized(lock) { data(ctx).optJSONObject("days")?.length() ?: 0 }

    fun today(ctx: Context) = day(ctx, dayOf(System.currentTimeMillis()))

    // ------------------------------------------------------------ this phone

    @Volatile private var profile: Profile? = null

    /**
     * This phone's power profile (its maker's figures for what each part draws, which Android's own
     * battery screen uses), and its battery's capacity from the battery's charge counter.
     */
    fun profile(ctx: Context): Profile = profile ?: run {
        val items = HashMap<String, Double>()
        val arrays = HashMap<String, MutableList<Double>>()
        runCatching {
            val res = Resources.getSystem()
            val id = res.getIdentifier("power_profile", "xml", "android")
            if (id != 0) res.getXml(id).use { x ->
                var array: String? = null
                while (x.next() != XmlPullParser.END_DOCUMENT) {
                    if (x.eventType == XmlPullParser.START_TAG) when (x.name) {
                        "item" -> { val n = x.getAttributeValue(null, "name"); x.next(); x.text?.trim()?.toDoubleOrNull()?.let { items[n] = it } }
                        "array" -> { array = x.getAttributeValue(null, "name"); arrays[array!!] = ArrayList() }
                        "value" -> { x.next(); x.text?.trim()?.toDoubleOrNull()?.let { v -> array?.let { arrays[it]?.add(v) } } }
                    } else if (x.eventType == XmlPullParser.END_TAG && x.name == "array") array = null
                }
            }
        }
        val (cap, capSource) = capacity(ctx, items["battery.capacity"])
        Profile.from(items, arrays, cap, capSource).also { profile = it }
    }

    /** The battery's capacity (mAh): from its charge counter and level now, else the power profile. */
    private fun capacity(ctx: Context, fromProfile: Double?): Pair<Double?, String> {
        val bm = ctx.getSystemService(BatteryManager::class.java)
        val counter = runCatching { bm.getLongProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER) }.getOrDefault(Long.MIN_VALUE)
        val level = runCatching { bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) }.getOrDefault(0)
        if (counter > 0 && level >= 10) {
            val mah = counter / 1000.0 / (level / 100.0)
            if (mah in 1_500.0..20_000.0) return mah to "battery"
        }
        // (1000 mAh is Android's placeholder, left in by phones that don't say: not a real battery.)
        return (fromProfile?.takeIf { it in 1_500.0..20_000.0 }) to "profile"
    }

    /** What the whole phone draws right now (mA, from the battery itself), and whether it is charging. */
    fun phoneNow(ctx: Context): Pair<Double, Boolean>? {
        val bm = ctx.getSystemService(BatteryManager::class.java)
        val raw = runCatching { bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW) }.getOrNull() ?: return null
        if (raw == Int.MIN_VALUE || raw == 0) return null
        val ma = if (abs(raw) > 20_000) raw / 1000.0 else raw.toDouble()      // µA as Android says, but some phones say mA
        return abs(ma) to (Sensors.battery(ctx).second)
    }

    /** What FindX draws right now (mA), from what it has on. */
    fun findxNow(ctx: Context): Double = Energy.drawNow(profile(ctx), isOn("satellites"), isOn("beaconFast"),
        isOn("beaconLow") || isOn("beaconFast"), isOn("scan"), Ring.ringing.value)

    /** Today's total, for FindX's reports (your other devices and DDrive show it): {mah, pct}. */
    fun forReport(ctx: Context): JSONObject {
        val p = profile(ctx)
        val u = today(ctx)
        return JSONObject().put("mah", Report.round(Energy.total(u, p), 2)).put("pct", Report.round(Energy.percent(u, p), 3))
    }
}
