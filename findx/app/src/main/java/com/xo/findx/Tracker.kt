package com.xo.findx

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Decides how hard to look: not at all while the phone lies still, at a sensible pace while it
 * moves, and quickly only while somebody is watching. Every position it gets goes into history.
 *
 * Still → moving: the motion chip (MotionWatch) says the phone was picked up and carried.
 * Moving → still: positions have stayed within [STOP_METRES] for [STOP_MS], and the phone itself is
 * still. The place it stopped at is pinned down briefly (a stay in history), then everything is
 * switched off again and the motion chip waits.
 */
class Tracker(private val svc: Service, private val scope: CoroutineScope) {
    @Volatile var mode = "standby"
        private set
    @Volatile var lastFix: Fix? = null
        private set
    @Volatile var stillSince = 0L
        private set

    private var moving = false
    private var watch: Locator.Watch? = null
    private var passive: Locator.Watch? = null
    private val motion = MotionWatch(svc) { scope.launch { startMoving() } }
    private var anchor: Fix? = null
    private var anchorAt = 0L
    private var stopCheck: Job? = null
    private var watchedUntil = 0L
    private var liveUntil = 0L
    private val recent = ArrayDeque<Fix>()
    private var stay: Point? = null
    private var lastPoint: Point? = null
    private val path = PathFilter()

    fun start() {
        passive = Locator.passive(svc) { onFix(it, free = true) }
        lastFix = Store.lastReport(svc)?.fix ?: Locator.lastKnown(svc)
        stillSince = Store.prefs(svc).getLong("stillSince", System.currentTimeMillis())
        settle()
    }

    fun stop() {
        watch?.stop(); watch = null
        passive?.stop(); passive = null
        motion.disarm()
        stopCheck?.cancel()
    }

    fun poke() { if (!moving) motion.arm() }

    /** The last 20 minutes of positions, for a live report's trail. */
    fun trail(): List<Fix> = synchronized(recent) { recent.filter { System.currentTimeMillis() - it.t < 20 * 60_000L }.takeLast(60) }

    /** Somebody has your map or a shared link open (the relay says so with every answer). */
    fun watched(on: Boolean) {
        val was = isLive()
        if (on) watchedUntil = System.currentTimeMillis() + 60_000
        if (isLive() != was) scope.launch {
            retune()
            // Watched while it lies still: one precise position (at most every 5 minutes) is all there is to show.
            if (isLive() && !moving && System.currentTimeMillis() - lastPinned > 5 * 60_000L) { lastPinned = System.currentTimeMillis(); svc.locate("a watcher") }
        }
    }

    private var lastPinned = 0L

    fun liveFor(ms: Long) {
        liveUntil = System.currentTimeMillis() + ms
        scope.launch { if (!moving) startMoving(byRequest = true) else retune() }
    }

    private fun isLive() = System.currentTimeMillis() < maxOf(watchedUntil, liveUntil)

    /** A precise fix from a locate: a better anchor for the stay. */
    fun precise(f: Fix) {
        lastFix = f
        if (!moving) recordStay(f)
    }

    private suspend fun startMoving(byRequest: Boolean = false) {
        if (moving) return
        moving = true
        stillSince = 0
        Store.prefs(svc).edit().putLong("stillSince", 0).apply()
        path.reset()
        closeStay()
        retune()
        if (!byRequest) Log.note(svc, "Moving")
        stopCheck?.cancel()
        stopCheck = scope.launch {
            while (isActive && moving) {
                delay(60_000)
                checkStopped()
            }
        }
        svc.report(lastFix)
    }

    /** How often to ask for a position while moving: by History detail, faster while watched. */
    private fun retune() {
        watch?.stop()
        watch = null
        if (!moving && !isLive()) { mode = "standby"; return }
        val live = isLive()
        mode = if (live) "live" else "moving"
        if (!moving) { Service.changed(svc); return }     // watched but lying still: the motion chip says when to look again
        val detail = Store.detail(svc)
        // Satellites whenever it moves (but in battery saver): Wi-Fi and masts are tens to hundreds of
        // metres out, which on foot draws a path through other people's gardens. Only while moving, so
        // a walk costs a few minutes of satellites, and the phone lying still costs nothing.
        val (interval, metres, satellites) = when {
            live -> Triple(2_000L, 0f, true)
            detail == 0 -> Triple(90_000L, 50f, false)
            detail == 1 -> Triple(8_000L, 8f, true)
            else -> Triple(3_000L, 3f, true)
        }
        watch = Locator.track(svc, interval, metres, satellites) { onFix(it, free = false) }
        Service.changed(svc)
    }

    private fun onFix(raw: Fix, free: Boolean) {
        if (raw.acc > 500) return
        if (free && !moving) {
            // A position another app asked for, while we think the phone lies still: if it's far off, it moved.
            val a = anchor
            if (raw.acc <= PathFilter.ROUGH && a != null && Geo.distance(a, raw) > maxOf(80.0, 2 * (a.acc + raw.acc))) scope.launch { startMoving() }
            if (lastFix == null) lastFix = raw
            return
        }
        // Moving: every position, ours or another app's, goes through the path filter; what it leaves out
        // (a jump to a mast, a stale or rough one among good ones) is neither drawn nor kept.
        val f = synchronized(path) { path.add(raw) } ?: return
        val prev = lastFix
        lastFix = f
        synchronized(recent) {
            recent.addLast(f)
            while (recent.size > 400) recent.removeFirst()
        }
        record(f)
        if (anchor == null || Geo.distance(anchor!!, f) > STOP_METRES + f.acc) { anchor = f; anchorAt = f.t }
        if (isLive() || prev == null || f.t - prev.t > 0) {
            if (isLive() || System.currentTimeMillis() - lastReportAt > 5 * 60_000L) {
                lastReportAt = System.currentTimeMillis()
                svc.report(f)
            }
        }
    }

    private var lastReportAt = 0L

    private fun record(f: Fix) {
        if (!Store.historyOn(svc)) return
        val last = lastPoint
        // A point every 15 m or so is plenty for a path; nearer than that it says nothing new.
        if (last != null && !last.stay && Geo.distance(last.lat, last.lon, f.lat, f.lon) < maxOf(15.0, f.acc / 2) && f.t - last.t < 10 * 60_000L) return
        val p = Point(f.t, f.lat, f.lon, f.acc)
        History.add(svc, p)
        lastPoint = p
    }

    private suspend fun checkStopped() {
        val a = anchor ?: return
        if (isLive() && System.currentTimeMillis() < liveUntil) return
        if (System.currentTimeMillis() - anchorAt < STOP_MS) return
        val s = runCatching { Sensors.snapshot(svc, 1_200, withRtt = false) }.getOrNull()
        if (s?.moving == true) return
        moving = false
        stillSince = anchorAt
        Store.prefs(svc).edit().putLong("stillSince", anchorAt).apply()
        retune()
        Log.note(svc, "Stopped")
        // Where it stopped, pinned down briefly (not in battery saver): what history keeps as the stay.
        val fix = if (Store.detail(svc) >= 1) Locator.precise(svc, maxMs = 25_000) else a
        recordStay(fix ?: a, since = anchorAt)
        svc.report(fix ?: a, settled = fix?.provider == "settled", sensed = runCatching { Sensors.snapshot(svc) }.getOrNull())
        settle()
    }

    private fun settle() {
        if (!moving) motion.arm()
        stopCheck?.cancel()
    }

    private fun recordStay(f: Fix, since: Long = stay?.t ?: f.t) {
        if (!Store.historyOn(svc)) return
        val p = Point(since, f.lat, f.lon, f.acc, until = maxOf(System.currentTimeMillis(), since + 1_000))
        History.add(svc, p)
        stay = p
        lastPoint = p
        anchor = f
    }

    /** The stay ends when the phone moves: its last line says until when. */
    private fun closeStay() {
        val s = stay ?: return
        History.add(svc, s.copy(until = System.currentTimeMillis()))
        stay = null
    }

    /** A long stay is brought up to date now and then, so history shows it lying there until now. */
    fun extendStay() {
        val s = stay ?: return
        if (System.currentTimeMillis() - s.until > 30 * 60_000L) {
            val p = s.copy(until = System.currentTimeMillis())
            History.add(svc, p)
            stay = p
        }
    }

    companion object {
        const val STOP_METRES = 35.0
        const val STOP_MS = 4 * 60_000L
    }
}
