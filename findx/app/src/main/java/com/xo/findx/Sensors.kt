package com.xo.findx

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.TriggerEvent
import android.hardware.TriggerEventListener
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.net.wifi.rtt.RangingRequest
import android.net.wifi.rtt.RangingResult
import android.net.wifi.rtt.RangingResultCallback
import android.net.wifi.rtt.WifiRttManager
import android.os.BatteryManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executors
import kotlin.coroutines.resume
import kotlin.math.sqrt

/**
 * What the phone senses about where it lies, read in short bursts and never left running: which way up
 * it is, whether something covers it, how bright it is, the air pressure and magnetic field, and the
 * Wi-Fi around it. Together they say what satellites can't indoors: "face down under something, on
 * the desk in the study".
 */
object Sensors {
    class Snapshot(
        val pose: Pose, val lux: Double?, val covered: Boolean, val pressure: Double?, val magnet: Double?,
        val wifi: Map<String, Int>, val rtt: Map<String, Double>, val moving: Boolean,
    ) {
        val signature get() = Signature(wifi, rtt, magnet, pressure)
    }

    /** Reads the sensors for about [ms] (they are switched off again at once). */
    suspend fun snapshot(ctx: Context, ms: Long = 1_500, withRtt: Boolean = true): Snapshot {
        val sm = ctx.getSystemService(SensorManager::class.java)
        val grav = DoubleArray(3)
        var gravN = 0
        var accVar = 0.0
        var lastMag = 0.0
        var accMean = 0.0
        var lux: Double? = null
        var near = false
        var hpa: Double? = null
        var magSum = 0.0
        var magN = 0
        val listener = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                when (e.sensor.type) {
                    Sensor.TYPE_ACCELEROMETER -> {
                        for (i in 0..2) grav[i] += e.values[i].toDouble()
                        gravN++
                        val m = sqrt(e.values.take(3).sumOf { (it * it).toDouble() })
                        // Running variance of the strength of the pull: a phone that lies still barely varies.
                        val d = m - accMean
                        accMean += d / gravN
                        accVar += d * (m - accMean)
                        lastMag = m
                    }
                    Sensor.TYPE_LIGHT -> lux = e.values[0].toDouble()
                    Sensor.TYPE_PROXIMITY -> near = e.values[0] < minOf(e.sensor.maximumRange, 3f)
                    Sensor.TYPE_PRESSURE -> hpa = e.values[0].toDouble()
                    Sensor.TYPE_MAGNETIC_FIELD -> {
                        magSum += sqrt(e.values.take(3).sumOf { (it * it).toDouble() }); magN++
                    }
                }
            }

            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
        }
        val types = listOf(Sensor.TYPE_ACCELEROMETER, Sensor.TYPE_LIGHT, Sensor.TYPE_PROXIMITY, Sensor.TYPE_PRESSURE, Sensor.TYPE_MAGNETIC_FIELD)
        types.forEach { t -> sm.getDefaultSensor(t)?.let { sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME) } }
        val t0 = android.os.SystemClock.elapsedRealtime()
        val (wifi, rtt) = try {
            delay(ms)
            wifi(ctx, withRtt)
        } finally {
            sm.unregisterListener(listener)
            Meter.span(ctx, "sensors", android.os.SystemClock.elapsedRealtime() - t0)
        }
        val pose = if (gravN == 0) Pose.Unknown else Pose.of(grav[0] / gravN, grav[1] / gravN, grav[2] / gravN)
        val sd = if (gravN > 1) sqrt(accVar / (gravN - 1)) else 0.0
        val covered = near || (lux != null && lux!! < 2.0 && pose != Pose.FaceDown)
        return Snapshot(pose, lux, covered, hpa, if (magN > 0) magSum / magN else null, wifi, rtt, moving = sd > 0.35)
    }

    /**
     * The access points heard lately (from the phone's own scans: asking for a new scan is limited by
     * Android and costs battery, so this reads the last one), and, where they can measure it, the
     * distance to them by Wi-Fi round-trip time.
     */
    @SuppressLint("MissingPermission")
    private suspend fun wifi(ctx: Context, withRtt: Boolean): Pair<Map<String, Int>, Map<String, Double>> {
        val wm = ctx.applicationContext.getSystemService(WifiManager::class.java) ?: return emptyMap<String, Int>() to emptyMap()
        val results = runCatching { wm.scanResults }.getOrNull().orEmpty()
            .filter { System.currentTimeMillis() - (it.timestamp / 1000 + bootTime()) < 10 * 60_000 }
        val heard = results.associate { it.BSSID to it.level }
        if (!withRtt || !Permissions.has(ctx, android.Manifest.permission.ACCESS_FINE_LOCATION)) return heard to emptyMap()
        val rttm = ctx.getSystemService(WifiRttManager::class.java)
        val responders = results.filter { it.is80211mcResponder }.sortedByDescending { it.level }.take(RangingRequest.getMaxPeers())
        if (rttm == null || !rttm.isAvailable || responders.isEmpty()) return heard to emptyMap()
        val got = withTimeoutOrNull(3_000) {
            suspendCancellableCoroutine<Map<String, Double>> { cont ->
                val req = RangingRequest.Builder().addAccessPoints(responders).build()
                runCatching {
                    rttm.startRanging(req, Executors.newSingleThreadExecutor(), object : RangingResultCallback() {
                        override fun onRangingFailure(code: Int) { if (cont.isActive) cont.resume(emptyMap()) }
                        override fun onRangingResults(results: List<RangingResult>) {
                            val m = results.filter { it.status == RangingResult.STATUS_SUCCESS }
                                .associate { it.macAddress.toString() to it.distanceMm / 1000.0 }
                            if (cont.isActive) cont.resume(m)
                        }
                    })
                }.onFailure { if (cont.isActive) cont.resume(emptyMap()) }
            }
        }
        return heard to (got ?: emptyMap())
    }

    private fun bootTime() = System.currentTimeMillis() - android.os.SystemClock.elapsedRealtime()

    fun battery(ctx: Context): Pair<Int, Boolean> {
        val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return -1 to false
        val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        val plugged = i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0
        return (if (level < 0) -1 else level * 100 / scale) to plugged
    }

    fun network(ctx: Context): String {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return ""
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return ""
        return when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cell"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "wired"
            else -> "other"
        }
    }

    fun unmetered(ctx: Context): Boolean {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return false
        return cm.getNetworkCapabilities(cm.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == true
    }
}

/**
 * Tells when the phone starts moving, at almost no cost: the "significant motion" sensor is a
 * low-power chip of its own that wakes the phone only when you walk off with it. Phones without one
 * get the accelerometer sampled slowly and in batches instead.
 */
class MotionWatch(private val ctx: Context, private val onMove: () -> Unit) {
    private val sm = ctx.getSystemService(SensorManager::class.java)
    private val significant = sm.getDefaultSensor(Sensor.TYPE_SIGNIFICANT_MOTION)
    private var armed = false

    private val trigger = object : TriggerEventListener() {
        override fun onTrigger(event: TriggerEvent?) {
            armed = false                       // one-shot: armed again when the phone settles
            onMove()
        }
    }

    private var mean = 0.0
    private var n = 0
    private val fallback = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            val m = sqrt(e.values.take(3).sumOf { (it * it).toDouble() })
            n++
            mean += (m - mean) / minOf(n, 20)
            if (n > 20 && kotlin.math.abs(m - mean) > 1.6) { disarm(); onMove() }
        }

        override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    }

    val lowPower get() = significant != null

    fun arm() {
        if (armed) return
        armed = true
        if (significant != null) sm.requestTriggerSensor(trigger, significant)
        else sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            n = 0
            sm.registerListener(fallback, it, 200_000, 30_000_000)   // 5 readings a second, handed over every 30 s
        }
    }

    fun disarm() {
        if (!armed) return
        armed = false
        if (significant != null) sm.cancelTriggerSensor(trigger, significant) else sm.unregisterListener(fallback)
    }
}
