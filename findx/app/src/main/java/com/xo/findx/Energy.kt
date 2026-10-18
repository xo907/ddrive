package com.xo.findx

import org.json.JSONObject

/*
 * How much battery FindX uses: what it did (Usage, counted exactly as it happens) turned into charge
 * (mAh) with how much each part of the phone draws (Profile: this phone's own power profile when it
 * can be read, which is what Android's battery screen uses too, or typical values). No Android in
 * here, so it is unit tested.
 */

/** What FindX did in some stretch of time. Counts and durations, exactly as they were. */
data class Usage(
    val cpuMs: Long = 0,          // processor time of FindX's process (from the kernel)
    val wakeWifi: Int = 0,        // times FindX woke the Wi-Fi radio to send or fetch something
    val wakeCell: Int = 0,        // ...the mobile radio
    val bytesWifi: Long = 0,      // bytes FindX sent and got over Wi-Fi (the kernel's count for FindX)
    val bytesCell: Long = 0,      // ...over mobile data
    val gpsMs: Long = 0,          // satellites on
    val bleLowMs: Long = 0,       // the Bluetooth beacon, about once a second
    val bleFastMs: Long = 0,      // ...ten times a second (while being looked for)
    val scanMs: Long = 0,         // Bluetooth scanning (the Nearby screen open)
    val sensorMs: Long = 0,       // motion, light, pressure and compass sensors on (short bursts)
    val ringMs: Long = 0,         // ringing: speaker, torch and vibration
    val uiMs: Long = 0,           // the app on screen (the screen itself is Android's to count)
) {
    operator fun plus(o: Usage) = Usage(
        cpuMs + o.cpuMs, wakeWifi + o.wakeWifi, wakeCell + o.wakeCell, bytesWifi + o.bytesWifi, bytesCell + o.bytesCell,
        gpsMs + o.gpsMs, bleLowMs + o.bleLowMs, bleFastMs + o.bleFastMs, scanMs + o.scanMs, sensorMs + o.sensorMs,
        ringMs + o.ringMs, uiMs + o.uiMs,
    )

    fun json(): JSONObject = JSONObject().apply {
        if (cpuMs != 0L) put("cpu", cpuMs); if (wakeWifi != 0) put("ww", wakeWifi); if (wakeCell != 0) put("wc", wakeCell)
        if (bytesWifi != 0L) put("bw", bytesWifi); if (bytesCell != 0L) put("bc", bytesCell); if (gpsMs != 0L) put("gps", gpsMs)
        if (bleLowMs != 0L) put("bl", bleLowMs); if (bleFastMs != 0L) put("bf", bleFastMs); if (scanMs != 0L) put("scan", scanMs)
        if (sensorMs != 0L) put("sens", sensorMs); if (ringMs != 0L) put("ring", ringMs); if (uiMs != 0L) put("ui", uiMs)
    }

    companion object {
        fun of(o: JSONObject) = Usage(
            o.optLong("cpu"), o.optInt("ww"), o.optInt("wc"), o.optLong("bw"), o.optLong("bc"), o.optLong("gps"),
            o.optLong("bl"), o.optLong("bf"), o.optLong("scan"), o.optLong("sens"), o.optLong("ring"), o.optLong("ui"),
        )
    }
}

/**
 * How much each part draws, in mA. [source] says where the figures came from: "phone" (this phone's
 * power profile, as its maker set it), or "typical" (values for a recent phone, when it can't be read).
 */
data class Profile(
    val cpuMa: Double = 120.0,        // one processor core busy (the low-power cores, where background work runs)
    val gpsMa: Double = 30.0,         // satellite receiver on
    val wifiMa: Double = 110.0,       // Wi-Fi radio busy
    val cellMa: Double = 200.0,       // mobile radio busy
    val bleLowMa: Double = 0.08,      // beacon at about 1 per second (average: each burst is a few milliseconds)
    val bleFastMa: Double = 0.5,      // beacon at 10 per second
    val scanMa: Double = 6.0,         // Bluetooth scanning, fast
    val sensorMa: Double = 1.5,       // a handful of sensors on together
    val ringMa: Double = 330.0,       // speaker at full volume, torch, vibration motor
    val capacityMah: Double = 4500.0, // the battery
    val source: String = "typical",
    val capacitySource: String = "typical",
) {
    companion object {
        const val WIFI_TAIL_S = 0.3   // the Wi-Fi radio stays awake this long after a request
        const val CELL_TAIL_S = 6.0   // the mobile radio stays at full power this long after a request (its "tail")
        const val WIFI_BPS = 2_000_000.0
        const val CELL_BPS = 1_000_000.0

        /**
         * A power profile as Android keeps it (name -> value, arrays as lists): the figures FindX
         * needs, each falling back to the typical one when the phone doesn't say or says nonsense.
         */
        fun from(items: Map<String, Double>, arrays: Map<String, List<Double>>, capacityMah: Double?, capacitySource: String): Profile {
            val t = Profile()
            fun ok(v: Double?, lo: Double, hi: Double) = v?.takeIf { it in lo..hi }
            // Processor: the first (low-power) cluster's middle speed, else the old single figure.
            val cluster = arrays["cpu.core_power.cluster0"]?.takeIf { it.isNotEmpty() }
            val cpu = ok(cluster?.let { it[it.size / 2] }, 5.0, 1000.0) ?: ok(items["cpu.active"], 5.0, 1000.0)
            val gps = ok(items["gps.on"], 1.0, 300.0) ?: ok(arrays["gps.signalqualitybased"]?.average(), 1.0, 300.0)
            val wifi = ok(items["wifi.active"], 5.0, 1000.0) ?: ok(items["wifi.controller.tx"], 5.0, 1000.0)
            val cell = ok(items["modem.controller.tx"]?.let { tx -> (tx + (items["modem.controller.rx"] ?: tx)) / 2 }, 5.0, 2000.0)
                ?: ok(items["radio.active"], 5.0, 2000.0)
            val ring = listOfNotNull(ok(items["camera.flashlight"], 1.0, 2000.0)?.let { it / 2 }, ok(items["audio"], 1.0, 2000.0))
                .takeIf { it.size == 2 }?.sum()?.plus(60.0)
            val fromPhone = listOf(cpu, gps, wifi, cell).count { it != null } >= 2
            return Profile(
                cpuMa = cpu ?: t.cpuMa, gpsMa = gps ?: t.gpsMa, wifiMa = wifi ?: t.wifiMa, cellMa = cell ?: t.cellMa,
                ringMa = ring ?: t.ringMa, capacityMah = capacityMah ?: t.capacityMah,
                source = if (fromPhone) "phone" else "typical", capacitySource = if (capacityMah != null) capacitySource else "typical",
            )
        }
    }
}

object Energy {
    val PARTS = listOf("satellites", "network", "processor", "bluetooth", "sensors", "ringing")

    /** Charge (mAh) by part. */
    fun mah(u: Usage, p: Profile): Map<String, Double> {
        fun h(ms: Long) = ms / 3_600_000.0
        val wifiS = u.wakeWifi * Profile.WIFI_TAIL_S + u.bytesWifi / Profile.WIFI_BPS
        val cellS = u.wakeCell * Profile.CELL_TAIL_S + u.bytesCell / Profile.CELL_BPS
        return mapOf(
            "satellites" to h(u.gpsMs) * p.gpsMa,
            "network" to (wifiS * p.wifiMa + cellS * p.cellMa) / 3600.0,
            "processor" to h(u.cpuMs) * p.cpuMa,
            "bluetooth" to h(u.bleLowMs) * p.bleLowMa + h(u.bleFastMs) * p.bleFastMa + h(u.scanMs) * p.scanMa,
            "sensors" to h(u.sensorMs) * p.sensorMa,
            "ringing" to h(u.ringMs) * p.ringMa,
        )
    }

    fun total(u: Usage, p: Profile) = mah(u, p).values.sum()

    /** As a share of a full battery, in %. */
    fun percent(u: Usage, p: Profile) = 100.0 * total(u, p) / p.capacityMah

    /** What FindX draws right now (mA), from what is on. */
    fun drawNow(p: Profile, gps: Boolean, bleFast: Boolean, bleOn: Boolean, scanning: Boolean, ringing: Boolean): Double =
        (if (gps) p.gpsMa else 0.0) + (if (bleOn) (if (bleFast) p.bleFastMa else p.bleLowMa) else 0.0) +
            (if (scanning) p.scanMa else 0.0) + (if (ringing) p.ringMa else 0.0)

    /** mAh in words. */
    fun words(mah: Double): String = when {
        mah < 0.1 -> "%.2f mAh".format(mah)
        mah < 10 -> "%.1f mAh".format(mah)
        else -> "%.0f mAh".format(mah)
    }

    fun pct(p: Double): String = when {
        p < 0.01 -> "under 0.01%"
        p < 1 -> "%.2f%%".format(p)
        else -> "%.1f%%".format(p)
    }
}
