package com.xo.findx

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.location.LocationRequest
import android.os.SystemClock
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/**
 * Positions, from Android's own location system (no Google location SDK in FindX: nothing about where
 * you are goes anywhere through it). Three ways of asking, from cheapest to dearest:
 *
 * - **passive**: costs nothing; FindX hears the positions other apps ask for (a map, the weather).
 * - **tracking**: while you move, at a pace that suits how you move (Tracker picks it).
 * - **precise**: satellites and Wi-Fi at full rate for up to a minute and a half, averaged while the
 *   phone lies still (Settle), for when you're looking for it.
 */
object Locator {
    private val executor: Executor = Executors.newSingleThreadExecutor { r -> Thread(r, "fx-location").apply { isDaemon = true } }

    fun fix(l: Location) = Fix(
        l.latitude, l.longitude, if (l.hasAccuracy()) l.accuracy.toDouble() else 50.0, l.time,
        alt = if (l.hasAltitude()) l.altitude else null, speed = if (l.hasSpeed()) l.speed.toDouble() else null,
        bearing = if (l.hasBearing()) l.bearing.toDouble() else null, provider = l.provider ?: "",
    )

    private fun lm(ctx: Context) = ctx.getSystemService(LocationManager::class.java)

    fun allowed(ctx: Context) = Permissions.has(ctx, android.Manifest.permission.ACCESS_FINE_LOCATION)

    fun enabled(ctx: Context) = runCatching { lm(ctx).isLocationEnabled }.getOrDefault(false)

    /** The freshest position the phone already knows, without asking for a new one. */
    @SuppressLint("MissingPermission")
    fun lastKnown(ctx: Context): Fix? {
        if (!allowed(ctx)) return null
        val lm = lm(ctx)
        return lm.allProviders.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.maxByOrNull { it.time }?.let { fix(it) }
    }

    private fun request(intervalMs: Long, quality: Int, minMetres: Float) = LocationRequest.Builder(intervalMs)
        .setQuality(quality).setMinUpdateDistanceMeters(minMetres).setMinUpdateIntervalMillis(intervalMs / 2).build()

    /** A running request; [stop] ends it (and counts the time satellites were on). */
    class Watch internal constructor(private val ctx: Context, private val listeners: List<LocationListener>, private val gps: Boolean) {
        private val started = SystemClock.elapsedRealtime()
        @Volatile private var stopped = false

        init { if (gps) Meter.on("satellites") }

        fun stop() {
            if (stopped) return
            stopped = true
            listeners.forEach { runCatching { lm(ctx).removeUpdates(it) } }
            if (gps) { Store.addGpsMs(ctx, SystemClock.elapsedRealtime() - started); Meter.off(ctx, "satellites") }
        }
    }

    /** Positions other apps asked for, at no cost to FindX. */
    @SuppressLint("MissingPermission")
    fun passive(ctx: Context, onFix: (Fix) -> Unit): Watch? {
        if (!allowed(ctx)) return null
        val l = LocationListener { onFix(fix(it)) }
        return runCatching {
            lm(ctx).requestLocationUpdates(LocationManager.PASSIVE_PROVIDER, request(0, LocationRequest.QUALITY_LOW_POWER, 10f), executor, l)
            Watch(ctx, listOf(l), gps = false)
        }.getOrNull()
    }

    /**
     * Positions every [intervalMs] or so, and not before the phone has moved [minMetres]. [satellites]
     * asks for satellite fixes (dearer, metres instead of tens of metres).
     */
    @SuppressLint("MissingPermission")
    fun track(ctx: Context, intervalMs: Long, minMetres: Float, satellites: Boolean, onFix: (Fix) -> Unit): Watch? {
        if (!allowed(ctx)) return null
        val lm = lm(ctx)
        val l = LocationListener { onFix(fix(it)) }
        val quality = if (satellites) LocationRequest.QUALITY_HIGH_ACCURACY else LocationRequest.QUALITY_BALANCED_POWER_ACCURACY
        val provider = when {
            lm.hasProvider(LocationManager.FUSED_PROVIDER) -> LocationManager.FUSED_PROVIDER
            satellites -> LocationManager.GPS_PROVIDER
            lm.hasProvider(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> LocationManager.GPS_PROVIDER
        }
        return runCatching {
            lm.requestLocationUpdates(provider, request(intervalMs, quality, minMetres), executor, l)
            Watch(ctx, listOf(l), gps = satellites)
        }.getOrNull()
    }

    /**
     * Pins the phone down as well as it can be: satellites (every second) and the fused position at
     * once, averaged while the phone lies still. [onProgress] hears each better estimate as it comes,
     * so the map can tighten its circle while you watch. Stops when waiting longer won't help, after
     * [maxMs], or when cancelled.
     */
    @SuppressLint("MissingPermission")
    suspend fun precise(ctx: Context, maxMs: Long = Settle.MAX_MS, onProgress: (Fix, Boolean) -> Unit = { _, _ -> }): Fix? {
        if (!allowed(ctx)) return lastKnown(ctx)
        val lm = lm(ctx)
        val settle = Settle()
        var latest: Fix? = null
        var best: Fix? = null
        val done = CompletableDeferred<Unit>()
        val lock = Any()
        val start = System.currentTimeMillis()
        val l = LocationListener { loc ->
            val f = fix(loc)
            synchronized(lock) {
                latest = f
                val moving = (f.speed ?: 0.0) > 0.8
                if (moving) settle.clear() else settle.add(f)
                val e = (if (moving) null else settle.estimate()) ?: f
                if (best == null || e.acc < best!!.acc - 0.05 || moving || System.currentTimeMillis() - best!!.t > 5_000) {
                    best = e
                    onProgress(e, !moving && settle.count > 1)
                }
                if (!moving && settle.count >= 5 && settle.done(System.currentTimeMillis())) done.complete(Unit)
                if (System.currentTimeMillis() - start > maxMs) done.complete(Unit)
            }
        }
        val listeners = ArrayList<LocationListener>()
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER).filter { lm.hasProvider(it) }
        for (p in providers) {
            val one = LocationListener { l.onLocationChanged(it) }
            if (runCatching { lm.requestLocationUpdates(p, request(1_000, LocationRequest.QUALITY_HIGH_ACCURACY, 0f), executor, one) }.isSuccess) listeners.add(one)
        }
        lastKnown(ctx)?.takeIf { System.currentTimeMillis() - it.t < 60_000 }?.let { onProgress(it, false) }
        val watch = Watch(ctx, listeners, gps = true)
        try {
            withTimeoutOrNull(maxMs + 2_000) { done.await() }
        } finally {
            watch.stop()
        }
        return synchronized(lock) { best ?: latest } ?: lastKnown(ctx)
    }
}
