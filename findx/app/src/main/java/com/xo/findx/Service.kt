package com.xo.findx

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import st.xo.ddrive.core.HostError

/**
 * FindX at work, in the background. What it does depends on what is going on, so that most of the
 * time it does next to nothing:
 *
 * | State      | When                                    | Costs                                                   |
 * |------------|-----------------------------------------|---------------------------------------------------------|
 * | still      | the phone lies somewhere (most of the day) | nothing: a motion chip waits; free positions other apps ask for are kept |
 * | moving     | you walk or drive off with it           | positions at the pace History detail says               |
 * | watched    | someone has your map or a shared link open | a position every few seconds while moving, only then |
 * | locating   | you asked where it is                   | satellites at full rate for up to 90 s                  |
 *
 * Waiting for commands ("ring") is one request the host holds open for minutes at a time: the radio
 * sleeps in between, and a command wakes the phone the moment it comes. History goes up once every
 * 20 minutes at most, in one go.
 */
class Service : android.app.Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private lateinit var tracker: Tracker
    private var relayJob: Job? = null
    private var lowBattery: BroadcastReceiver? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = this
        channels(this)
        foreground()
        tracker = Tracker(this, scope)
        tracker.start()
        relayJob = scope.launch { relayLoop() }
        scope.launch { circlesLoop() }
        scope.launch { housekeeping() }
        lowBattery = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) { scope.launch { lastWords() } }
        }
        ContextCompat.registerReceiver(this, lowBattery, IntentFilter(Intent.ACTION_BATTERY_LOW), ContextCompat.RECEIVER_NOT_EXPORTED)
        Beacon.update(this)
        kickLater(this)
        Log.note(this, "FindX started")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        foreground()
        when (intent?.action) {
            ACTION_KICK -> { Beacon.update(this); tracker.poke() }
            ACTION_LOCATE -> scope.launch { locate(from = "app") }
            ACTION_STOP_RING -> Ring.stop(this)
        }
        kickLater(this)
        return START_STICKY
    }

    override fun onDestroy() {
        running = null
        tracker.stop()
        Beacon.stop(this)
        lowBattery?.let { runCatching { unregisterReceiver(it) } }
        scope.cancel()
        super.onDestroy()
    }

    private fun foreground() {
        val n = Notification.Builder(this, CH_QUIET).setSmallIcon(R.drawable.ic_stat_findx)
            .setContentTitle(if (Store.lost(this)) "Lost mode is on" else "FindX is looking after this phone")
            .setContentText(statusLine.value.ifEmpty { "Findable, using almost no battery" })
            .setContentIntent(openApp(this)).setOngoing(true).setShowWhen(false).setCategory(Notification.CATEGORY_SERVICE).build()
        val types = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or (if (Permissions.location(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0)
        try {
            startForeground(NOTE_SERVICE, n, types)
        } catch (e: Exception) {           // location type not allowed from here (started in the background): without it
            startForeground(NOTE_SERVICE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        }
    }

    // ------------------------------------------------------------ the relay: reports out, commands in

    private val seen = LinkedHashSet<String>()

    /** Holds a request open with the host to hear commands, for as long as FindX is linked. */
    private suspend fun relayLoop() {
        var failures = 0
        var cut = 0                              // long waits cut off in a row (a proxy that does that, or a restart)
        var after = Store.prefs(this).getLong("cmdSeq", 0)
        var helloAt = 0L
        while (scope.isActive) {
            val link = Link.of(this)
            val keys = Store.keys(this)
            if (link == null || keys == null || !Store.on(this)) { delay(60_000); continue }
            // A DDrive without the relay is asked again now and then (it may have been updated meanwhile).
            if (!Store.relay(this) && System.currentTimeMillis() - helloAt < 30 * 60_000L) { delay(5 * 60_000L); continue }
            try {
                if (System.currentTimeMillis() - helloAt > 6 * 3_600_000L || !Store.relay(this)) {
                    helloAt = System.currentTimeMillis()
                    withContext(Dispatchers.IO) {
                        link.hello(keys.group, Store.device(this@Service), card(keys))
                        // The relay keeps links in memory only: tell it again which are live (after a restart it has none).
                        val now = System.currentTimeMillis()
                        Store.shares(this@Service).filter { it.live(now) }.forEach { link.share(keys.group, Store.device(this@Service), it.id, it.until) }
                    }
                    if (!Store.relay(this)) { Store.setRelay(this, true); Log.note(this, "Linked to DDrive's live relay") }
                    report(tracker.lastFix)
                }
                // A proxy in front of the drive may cut a request off after a minute (nginx does by default):
                // then the wait is made that short for this drive, rather than failing every time. Only when it
                // happens three times in a row: a drive being restarted (an update) cuts one off too. Long waits
                // are tried again every six hours (the proxy may have been set up for them meanwhile).
                val p = Store.prefs(this)
                if (p.getInt("waitS", WAIT_S) < WAIT_S && System.currentTimeMillis() - p.getLong("waitShortAt", 0) > 6 * 3_600_000L)
                    p.edit().remove("waitS").apply()
                val waitS = p.getInt("waitS", WAIT_S)
                val began = SystemClock.elapsedRealtime()
                val waited = try {
                    withContext(Dispatchers.IO) { link.waitCommands(keys.group, Store.device(this@Service), after, waitS) }
                } catch (e: HostError) {
                    val held = (SystemClock.elapsedRealtime() - began) / 1000
                    if (waitS > SHORT_WAIT_S && held >= 25 && e.status in setOf(0, 502, 504) && ++cut >= 3) {
                        p.edit().putInt("waitS", SHORT_WAIT_S).putLong("waitShortAt", System.currentTimeMillis()).apply()
                        Log.note(this, "The way to DDrive cuts long requests off: waiting ${SHORT_WAIT_S} s at a time")
                        cut = 0
                        continue
                    }
                    throw e
                }
                failures = 0
                cut = 0
                Store.setLinkProblem(this, null)
                if (waited.hello) helloAt = 0                // the relay was emptied (a restart of an older DDrive): hello again now
                val commands = waited.commands
                tracker.watched(waited.watching)
                for ((seq, blob) in commands) {
                    after = maxOf(after, seq)
                    runCatching { Command.of(JSONObject(String(keys.open(keys.command, "findx command", blob)))) }.getOrNull()?.let { act(it) }
                }
                Store.prefs(this).edit().putLong("cmdSeq", after).apply()
            } catch (e: Link.NoRelay) {
                if (Store.relay(this)) Log.note(this, "This DDrive has no live relay: history only")
                Store.setRelay(this, false)
            } catch (e: kotlinx.coroutines.CancellationException) {
                if (!scope.isActive) throw e
            } catch (e: Exception) {
                failures++
                val status = (e as? HostError)?.status ?: 0
                // Not answering, or a proxy saying the drive behind it isn't (502, 503, 504): usually a restart,
                // an update. Asked again soon (at most every 2 minutes), and said as such.
                val down = status == 0 || status in 502..504
                Store.setLinkProblem(this, if (down) "DDrive isn't answering right now (it may be restarting, or this phone is offline). Trying again by itself."
                                          else (e as? HostError)?.message ?: e.message)
                delay(minOf(if (down) 2 * 60_000L else 15 * 60_000L, 5_000L shl minOf(failures, 8)))   // 10 s, 20 s, 40 s ...
            }
        }
    }

    private fun card(keys: FxKeys): ByteArray = keys.seal(keys.report, "findx card", JSONObject().put("d", Store.device(this))
        .put("n", Store.deviceName(this)).put("model", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")
        .put("uwb", Precision.supported(this)).put("v", BuildConfig.VERSION_NAME).toString().toByteArray())

    /** Acts on a command from one of your devices or DDrive (sealed with your command key, so nobody else can). */
    private suspend fun act(c: Command) {
        val now = System.currentTimeMillis()
        if (c.to != Store.device(this) || c.what !in Command.KINDS) return
        if (c.t < now - Command.MAX_AGE_MS || c.t > now + 60_000 || !seen.add(c.id)) return
        while (seen.size > 200) seen.remove(seen.first())
        Log.note(this, "Asked to ${c.what} (from ${if (c.from == "web") "DDrive" else "another device"})")
        withWake {
            when (c.what) {
                "ring" -> { Ring.start(this, c.args.optLong("ms", Ring.MAX_MS)); Beacon.fast(this) }
                "stop" -> Ring.stop(this)
                "locate" -> locate(from = c.from)
                "live" -> tracker.liveFor(c.args.optLong("ms", 10 * 60_000L).coerceIn(30_000L, 60 * 60_000L))
                "lost" -> { Store.setLost(this, true, c.args.optString("message", Store.lostMessage(this))); lostNotice(this); Beacon.fast(this, 60 * 60_000L); locate(from = c.from) }
                "found" -> { Store.setLost(this, false); getSystemService(NotificationManager::class.java).cancel(NOTE_LOST); changed(this) }
                "beacon" -> Beacon.fast(this)
                else -> Remote.act(this, c)?.let { Log.note(this, it) }
            }
        }
        // So the page sees what came of it at once: a report, and the stats file (settings, links, activity).
        scope.launch(Dispatchers.IO) {
            report(tracker.lastFix ?: Store.lastReport(this@Service)?.fix)
            val link = Link.of(this@Service) ?: return@launch
            val keys = Store.keys(this@Service) ?: return@launch
            runCatching { Remote.uploadStats(this@Service, link, keys) }
            if (c.what in setOf("join", "leave", "circleRing", "set")) runCatching {
                link.putFile(Remote.circlesPath(keys, Store.device(this@Service)), Circles.forDashboard(this@Service, keys))
                Store.prefs(this@Service).edit().putBoolean("circlesChanged", false).apply()
            }
        }
    }

    /** Sends where the phone is now (and to each live share), unless the host has no relay. */
    fun report(fix: Fix?, mode: String = tracker.mode, settled: Boolean = false, sensed: Sensors.Snapshot? = null) {
        val r = buildReport(fix, mode, settled, sensed)
        Store.saveReport(this, r)
        statusLine.value = describe(r)
        version.intValue++
        if (Circles.all(this).isNotEmpty()) scope.launch(Dispatchers.IO) {
            if (Circles.reportAll(this@Service, r, watched = circlesWatched)) tracker.watched(true)
        }
        val link = Link.of(this) ?: return
        val keys = Store.keys(this) ?: return
        if (!Store.relay(this)) return
        scope.launch(Dispatchers.IO) {
            val now = System.currentTimeMillis()
            val all = Store.shares(this@Service)
            val (live, ended) = all.partition { it.live(now) }
            if (ended.isNotEmpty()) {
                Store.saveShares(this@Service, live)
                ended.forEach { s -> runCatching { link.unshare(keys.group, s.id) } }
            }
            val shared = live.associate { s ->
                s.id to keys.seal(FxKeys.unb64url(s.key), "findx share", r.forShare(s.approximate).toJson().toString().toByteArray())
            }
            runCatching {
                val heard = link.report(keys.group, Store.device(this@Service), keys.seal(keys.report, "findx report", r.toJson().toString().toByteArray()), shared)
                tracker.watched(heard.watching || heard.shareWatching.isNotEmpty())
            }.onFailure { if (it is Link.NoRelay) Store.setRelay(this@Service, false) }
        }
    }

    private fun buildReport(fix: Fix?, mode: String, settled: Boolean, s: Sensors.Snapshot?): Report {
        val (bat, chg) = Sensors.battery(this)
        val spots = Store.spots(this)
        val match = s?.let { snap -> Spots.best(snap.signature, spots.map { it to it.sig }) }
        return Report(
            device = Store.device(this), name = Store.deviceName(this), t = System.currentTimeMillis(), fix = fix, settled = settled,
            stillSince = tracker.stillSince, pose = s?.pose ?: Pose.Unknown, lux = s?.lux, covered = s?.covered ?: false,
            spot = match?.first?.name, spotScore = match?.second ?: 0.0,
            floorDelta = match?.first?.sig?.pressure?.let { p -> s.pressure?.let { (p - it) / 0.12 / 3.0 } },   // ~3 m a floor
            battery = bat, charging = chg, network = Sensors.network(this), mode = mode,
            ringing = Ring.ringing.value, lost = Store.lost(this), trail = if (mode == "live") tracker.trail() else emptyList(),
            use = runCatching { Meter.forReport(this) }.getOrNull(),
            cfg = runCatching { Remote.settings(this) }.getOrNull(), problems = runCatching { Remote.problems(this) }.getOrDefault(emptyList()),
        )
    }

    /** Pins this phone down as well as it can be, reporting each better estimate on the way. */
    suspend fun locate(from: String) {
        if (locating.value) return
        locating.value = true
        try {
            withWake {
                val sensed = runCatching { Sensors.snapshot(this, 1_500) }.getOrNull()
                var last = 0L
                val fix = Locator.precise(this) { f, settled ->
                    val now = System.currentTimeMillis()
                    if (now - last > 3_000) { last = now; report(f, "locating", settled, sensed) }
                }
                report(fix, tracker.mode, settled = fix?.provider == "settled", sensed = sensed)
                fix?.let { tracker.precise(it) }
                Log.note(this, "Located${fix?.let { " to ±%.1f m".format(it.acc) } ?: " (no position)"} for $from")
            }
        } finally {
            locating.value = false
        }
    }

    /** The battery is about to run out: one last precise position, and all history to DDrive. */
    private suspend fun lastWords() {
        Log.note(this, "Battery low: sending a last position")
        locate(from = "low battery")
        withContext(Dispatchers.IO) { Link.of(this@Service)?.let { runCatching { History.upload(this@Service, it, force = true) } } }
    }

    // ------------------------------------------------------------ circles: news and rings from family and friends

    @Volatile private var circlesWatched: Set<String> = emptySet()
    private val circleWaits = HashMap<String, Job>()

    /** One wait per drive that has circles of this phone (usually one), started and stopped as circles come and go. */
    private suspend fun circlesLoop() {
        while (scope.isActive) {
            val hosts = Circles.all(this).map { it.host }.toSet()
            synchronized(circleWaits) {
                circleWaits.keys.filter { it !in hosts }.forEach { circleWaits.remove(it)?.cancel() }
                for (h in hosts) if (circleWaits[h]?.isActive != true) circleWaits[h] = scope.launch(Dispatchers.IO) { circleWait(h) }
            }
            delay(60_000)
        }
    }

    private suspend fun circleWait(host: String) {
        var failures = 0
        var cut = 0
        val client = CircleClient(host)
        while (scope.isActive) {
            val mine = Circles.all(this).filter { it.host == host }
            if (mine.isEmpty() || !Store.on(this)) return
            val p = Store.prefs(this)
            val waitS = p.getInt("cwait:$host", WAIT_S)
            val began = SystemClock.elapsedRealtime()
            try {
                val o = client.call("wait", JSONObject().put("member", Store.circleMember(this)).put("wait", waitS)
                    .put("circles", org.json.JSONArray().apply {
                        mine.forEach { put(JSONObject().put("circle", it.id).put("auth", it.auth).put("after", p.getLong("cseq:${it.id}", 0))) }
                    }), timeoutMs = (waitS + 30) * 1000)
                failures = 0
                cut = 0
                val cmds = o.optJSONArray("commands") ?: org.json.JSONArray()
                for (i in 0 until cmds.length()) {
                    val x = cmds.getJSONObject(i)
                    val c = mine.firstOrNull { it.id == x.optString("circle") } ?: continue
                    p.edit().putLong("cseq:${c.id}", maxOf(p.getLong("cseq:${c.id}", 0), x.optLong("seq"))).apply()
                    withWake { Circles.act(this, c, x.getString("blob")) }
                }
                val w = o.optJSONArray("watching") ?: org.json.JSONArray()
                val watched = (0 until w.length()).map { w.getString(it) }.toSet()
                val newly = watched - circlesWatched
                circlesWatched = watched
                if (newly.isNotEmpty()) {                    // somebody just opened the circle's map: tell it where this phone is
                    tracker.watched(true)
                    Store.lastReport(this)?.let { Circles.reportAll(this, it.copy(t = System.currentTimeMillis()), watched = newly) }
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: HostError) {
                val held = (SystemClock.elapsedRealtime() - began) / 1000
                if (waitS > SHORT_WAIT_S && held >= 25 && e.status in setOf(0, 502, 504) && ++cut >= 3) { p.edit().putInt("cwait:$host", SHORT_WAIT_S).apply(); cut = 0; continue }
                failures++
                val down = e.status == 0 || e.status in 502..504          // (a restart, usually: asked again soon)
                delay(minOf(if (down) 2 * 60_000L else 15 * 60_000L, 5_000L shl minOf(failures, 8)))
            } catch (e: Exception) {
                failures++
                delay(minOf(15 * 60_000L, 5_000L shl minOf(failures, 8)))
            }
        }
    }

    private suspend fun housekeeping() {
        var cardFileFor = ""
        while (scope.isActive) {
            Beacon.update(this)
            tracker.extendStay()
            runCatching { Meter.tick(this) }
            val link = Link.of(this)
            val keys = Store.keys(this)
            if (link != null && keys != null) {
                withContext(Dispatchers.IO) {
                    runCatching {
                        // Your browser finds each device's history by its card in /FindX/d.
                        val want = "${Store.deviceName(this@Service)}|${Store.device(this@Service)}"
                        if (cardFileFor != want) {
                            link.putFile("${Store.ROOT}/d/${keys.name("d|" + Store.device(this@Service))}.fxd", card(keys))
                            cardFileFor = want
                        }
                        // The keys your browser opens it all with, once for each DDrive this phone is linked to.
                        val linkedTo = "${Store.host(this@Service)}|${keys.group}"
                        if (Store.prefs(this@Service).getString("keysFileFor", "") != linkedTo) {
                            link.putFile("${Store.ROOT}/keys.fxk", keys.forDashboard().toByteArray())
                            Store.prefs(this@Service).edit().putString("keysFileFor", linkedTo).apply()
                        }
                        // Your circles, for the FindX page on your DDrive (sealed with your keys).
                        if (Store.prefs(this@Service).getBoolean("circlesChanged", true)) {
                            link.putFile(Remote.circlesPath(keys, Store.device(this@Service)), Circles.forDashboard(this@Service, keys))
                            Store.prefs(this@Service).edit().putBoolean("circlesChanged", false).apply()
                        }
                        History.upload(this@Service, link)
                        // Once a day: is a newer FindX out? (Your DDrive knows; the phone asks nobody else.)
                        val p = Store.prefs(this@Service)
                        if (System.currentTimeMillis() - p.getLong("newestAt", 0) > 86_400_000L) {
                            p.edit().putLong("newestAt", System.currentTimeMillis()).apply()
                            runCatching { link.newestFindX() }.getOrNull()?.let { (v, url) ->
                                p.edit().putString("newest", if (Versions.newer(v, BuildConfig.VERSION_NAME)) "$v|$url" else "").apply()
                            }
                        }
                        // Battery use, activity and live links for the FindX page: once an hour.
                        if (System.currentTimeMillis() - Store.prefs(this@Service).getLong("statsAt", 0) > 3_600_000L) Remote.uploadStats(this@Service, link, keys)
                    }.onFailure { Store.setLinkProblem(this@Service, it.message) }
                }
            }
            // A circle's name and places may have been changed by somebody else: now and then, look.
            if (System.currentTimeMillis() - Store.prefs(this).getLong("circlesInfoAt", 0) > 3 * 3_600_000L) {
                Store.prefs(this).edit().putLong("circlesInfoAt", System.currentTimeMillis()).apply()
                withContext(Dispatchers.IO) { Circles.all(this@Service).forEach { c -> runCatching { Circles.refreshInfo(this@Service, c) } } }
            }
            if (tracker.mode == "standby" && System.currentTimeMillis() - Store.prefs(this).getLong("heartbeat", 0) > 30 * 60_000L) {
                Store.prefs(this).edit().putLong("heartbeat", System.currentTimeMillis()).apply()
                report(tracker.lastFix ?: Locator.lastKnown(this))
            }
            delay(5 * 60_000L)
        }
    }

    private suspend fun <T> withWake(block: suspend () -> T): T {
        val wl = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "FindX:work")
        wl.acquire(3 * 60_000L)
        try {
            return block()
        } finally {
            if (wl.isHeld) wl.release()
        }
    }

    /** Sends a command to another of your devices through the relay. */
    fun command(to: String, what: String, args: JSONObject = JSONObject()) {
        val link = Link.of(this) ?: throw IllegalStateException("Link FindX to your DDrive first")
        val keys = Store.keys(this)!!
        val c = Command(what, to, Store.device(this), System.currentTimeMillis(), FxKeys.hex(FxKeys.bytes(8)), args)
        link.command(keys.group, to, keys.seal(keys.command, "findx command", c.toJson().toString().toByteArray()))
    }

    companion object {
        const val WAIT_S = 240                   // the host holds a wait this long (under a mobile network's idle cut-off)
        const val SHORT_WAIT_S = 50              // ...or this long, behind a proxy that cuts requests off after a minute
        const val CH_QUIET = "quiet"
        const val CH_ALERT = "alert"
        const val CH_LOST = "lost"
        const val CH_PEOPLE = "people"
        const val NOTE_SERVICE = 1
        const val NOTE_RING = 2
        const val NOTE_LOST = 3
        const val ACTION_KICK = "com.xo.findx.KICK"
        const val ACTION_LOCATE = "com.xo.findx.LOCATE"
        const val ACTION_STOP_RING = "com.xo.findx.STOP_RING"

        @Volatile var running: Service? = null
            private set

        val statusLine = mutableStateOf("")
        val locating = mutableStateOf(false)
        /** Bumped when there is something new to show. */
        val version = mutableIntStateOf(0)

        fun changed(ctx: Context) { version.intValue++; running?.foreground() }

        fun start(ctx: Context, action: String? = null) {
            if (!Store.on(ctx) || !Store.setUp(ctx)) return
            runCatching { ctx.startForegroundService(Intent(ctx, Service::class.java).setAction(action)) }
                .onFailure { Log.note(ctx, "Couldn't start in the background: ${it.message}") }
        }

        fun stop(ctx: Context) = ctx.stopService(Intent(ctx, Service::class.java))

        /** A nudge every ~10 minutes (allowed while the phone dozes): a wait that died quietly is started again. */
        fun kickLater(ctx: Context) {
            val am = ctx.getSystemService(AlarmManager::class.java)
            val pi = PendingIntent.getBroadcast(ctx, 0, Intent(ctx, KickReceiver::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, SystemClock.elapsedRealtime() + 10 * 60_000L, pi)
        }

        fun openApp(ctx: Context): PendingIntent = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)

        fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_QUIET, "FindX at work", NotificationManager.IMPORTANCE_MIN).apply {
                description = "The small note Android needs while FindX looks after this phone."; setShowBadge(false)
            })
            nm.createNotificationChannel(NotificationChannel(CH_ALERT, "Ringing", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "When you ring this phone to find it."; setSound(null, null); setBypassDnd(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            })
            nm.createNotificationChannel(NotificationChannel(CH_PEOPLE, "Circles", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Family and friends arriving at and leaving your circles' places."
            })
            nm.createNotificationChannel(NotificationChannel(CH_LOST, "Lost mode", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Your message to whoever finds this phone, on its lock screen."; lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            })
        }

        /** Your message on the lock screen, for whoever finds the phone. */
        fun lostNotice(ctx: Context) {
            val msg = Store.lostMessage(ctx).ifEmpty { "This phone is lost. Please call its owner or hand it in." }
            val n = Notification.Builder(ctx, CH_LOST).setSmallIcon(R.drawable.ic_stat_findx).setContentTitle("This phone is lost")
                .setContentText(msg).setStyle(Notification.BigTextStyle().bigText(msg)).setOngoing(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC).setCategory(Notification.CATEGORY_STATUS).build()
            ctx.getSystemService(NotificationManager::class.java).notify(NOTE_LOST, n)
            changed(ctx)
        }

        fun describe(r: Report): String {
            val parts = ArrayList<String>()
            r.spot?.let { parts.add("At $it") }
            if (r.pose != Pose.Unknown && r.mode != "live") parts.add(r.pose.words.lowercase())
            r.fix?.let { if (r.spot == null) parts.add("±%.0f m".format(it.acc)) }
            return parts.joinToString(", ").replaceFirstChar { it.uppercase() }
        }
    }
}

/** The alarm's nudge, and starting again after the phone restarts or FindX is updated. */
class KickReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (Service.running == null) Service.start(ctx) else Service.start(ctx, Service.ACTION_KICK)
    }
}
