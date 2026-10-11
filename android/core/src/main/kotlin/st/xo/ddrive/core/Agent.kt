package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.thread
import kotlin.concurrent.withLock

private const val POLL_WAIT = 25.0          // how long the host may hold our question "anything new?" open
private const val REPORT_EVERY = 20_000L    // the host hears how the pairs are doing at least this often (ms)
private const val LINK_RETRY = 15_000L      // after the host couldn't be reached
private const val LINK_REFUSED = 600_000L   // after the host didn't take our sign-in (asking often would lock us out)

/** Something a thread waits for, with a time limit. */
private class Signal {
    private val lock = ReentrantLock()
    private val cond = lock.newCondition()
    private var set = false

    fun set() = lock.withLock {
        set = true
        cond.signalAll()
    }

    /** Wait until set (and clear it) or until `ms` went by. */
    fun await(ms: Long) = lock.withLock {
        if (!set) cond.await(ms, java.util.concurrent.TimeUnit.MILLISECONDS)
        set = false
    }
}

/**
 * Folder syncing on this device, steered from the host's dashboard: what litesync.py's Agent is on
 * a computer. It says to the host that this device is there, waits there for word (changed pairs, a
 * pair to start or stop, a folder to list for the folder browser), runs the pairs when they are
 * due, and tells the host how they are doing. The pairs are kept here, in `dir`.
 *
 * @param livePoll how often (seconds) a "live" pair looks at its folder here: a phone isn't told about changes.
 *   Only when something changed in it does the pair run (which asks the host for the whole drive folder).
 * @param liveFull how often (seconds) a "live" pair runs anyway, which is how changes on the drive are noticed.
 * @param browse the folders in a folder of this device: {path, parent, dirs: [{name, path}], roots} or {error}.
 * @param mayRun why nothing may be copied right now ("Waiting for Wi-Fi"), or null when it may.
 * @param refuse why a pair's folder can't be used on this device, or null.
 * @param touched a file of this device that a sync wrote, replaced or removed.
 */
class Agent(private val client: HostClient, val device: String, val name: String, private val dir: File,
            private val trashRoot: File, private val home: String, private val platform: String = "android",
            private val livePoll: Double = 60.0, private val liveFull: Double = 900.0, private val keepFree: Long = 1L shl 30,
            private val browse: (String) -> JSONObject = { JSONObject().put("error", "Folders can't be listed here") },
            private val mayRun: () -> String? = { null },
            private val refuse: (Job) -> String? = { null },
            private val log: (String) -> Unit = {},
            private val touched: (File) -> Unit = {},
            private val onChange: () -> Unit = {}) {
    @Volatile var online = false
        private set
    @Volatile var running: String? = null
        private set
    @Volatile var jobs: List<Job> = emptyList()
        private set
    @Volatile var lastProblem: String? = null       // why the host can't be reached, for this device's own screen
        private set

    private val lock = ReentrantLock()               // guards status, requests, raw
    private val status = HashMap<String, JSONObject>()
    private val requests = LinkedHashSet<String>()
    private var raw = JSONArray()                    // the pairs as the host and we keep them
    private var rev = 0L                             // the number of the pairs' last change that we know of
    @Volatile private var greeted = false
    @Volatile private var stopping = false
    @Volatile private var cancel: String? = null
    private val wake = Signal()
    private val report = Signal()
    private val linkPause = Signal()
    private val threads = ArrayList<Thread>()
    private var lastSave = 0L
    private var lastTrash = 0.0
    private val looked = HashMap<String, Double>()   // live pairs: when the folder here was last looked at
    private val prints = HashMap<String, Long>()     // live pairs: what the folder here looked like after the last pass

    private val jobsFile get() = File(dir, "jobs.json")
    private val statusFile get() = File(dir, "status.json")

    init {
        dir.mkdirs()
        try {
            val saved = JSONObject(statusFile.readText()).optJSONObject("jobs")
            if (saved != null) for (k in saved.keys()) {
                val s = saved.optJSONObject(k) ?: continue
                if (s.optString("state") == "running") s.put("state", "idle")
                s.remove("progress")
                status[k] = s
            }
        } catch (e: Exception) {
            // nothing kept yet
        }
        try {
            val saved = JSONObject(jobsFile.readText())
            rev = saved.optLong("rev")
            apply(saved.optJSONArray("jobs") ?: JSONArray())
        } catch (e: Exception) {
            // no pairs yet
        }
    }

    // ------------------------------------------------------------ the pairs
    private fun write(file: File, text: String) {
        val tmp = File(file.path + ".tmp")
        tmp.writeText(text)
        Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
    }

    private fun apply(list: JSONArray) {
        val parsed = ArrayList<Job>()
        for (i in 0 until list.length()) {
            val j = list.optJSONObject(i) ?: continue
            if (j.optString("id").isNotEmpty()) parsed.add(Job.from(j))
        }
        lock.withLock {
            raw = list
            val known = parsed.map { it.id }.toSet()
            status.keys.retainAll(known)
            requests.retainAll(known)
            for (j in parsed) status.getOrPut(j.id) { JSONObject().put("since", now()) }
        }
        jobs = parsed
        wake.set()
        save(force = true)
    }

    private fun take(got: JSONObject) {
        val list = got.optJSONArray("jobs") ?: JSONArray()
        val newRev = got.optLong("rev")
        val changed = lock.withLock { list.toString() != raw.toString() }
        if (!changed && newRev == rev) return
        rev = newRev
        if (changed) apply(list)
        write(jobsFile, JSONObject().put("rev", rev).put("jobs", list).toString())
    }

    fun job(id: String) = jobs.firstOrNull { it.id == id }

    // ------------------------------------------------------------ talking to the host
    /** Say to the host that this device is there. Throws HostError with a message for people. */
    fun hello(): JSONObject {
        val body = lock.withLock {
            JSONObject().put("device", device).put("name", name).put("platform", platform).put("home", home)
                .put("rev", rev).put("jobs", JSONArray(raw.toString()))
        }
        val got = try {
            client.syncCall("hello", body)
        } catch (e: HostError) {
            if (e.status == 404) throw HostError(404, "The host at ${client.address} runs a DDrive from before folder syncing for " +
                                                      "lite devices (2.2.0). Update it there first.")
            throw e
        }
        greeted = true
        take(got)
        return got
    }

    /** Take this device off the host's Sync page (folder syncing was turned off here). */
    fun bye() {
        try {
            client.syncCall("bye", JSONObject().put("device", device))
        } catch (e: HostError) {
            // an unreachable host forgets by itself
        }
    }

    private fun act(c: JSONObject) {
        when (c.optString("do")) {
            "run" -> {
                val id = c.optString("id")
                if (c.optBoolean("again")) File(dir, "$id.backup.json").delete()
                lock.withLock { requests.add(id) }
                wake.set()
            }
            "stop" -> {
                val id = c.optString("id")
                if (running == id) cancel = id
                lock.withLock { requests.remove(id) }
            }
            "browse" -> {
                val result = try {
                    browse(c.optString("path"))
                } catch (e: Exception) {
                    JSONObject().put("error", e.message ?: "That folder can't be opened")
                }
                client.syncCall("answer", JSONObject().put("device", device).put("req", c.optString("req")).put("result", result))
            }
        }
    }

    /** Wait at the host for word: changed pairs, a pair to start or stop, a folder to list. */
    private fun link() {
        var told: String? = null
        while (!stopping) {
            var wait = LINK_RETRY
            try {
                if (!greeted) hello()
                if (!online) {
                    online = true
                    told = null
                    lastProblem = null
                    log("Folder syncing: connected to ${client.address} as $name")
                    report.set()
                    wake.set()
                    onChange()
                }
                val got = client.syncCall("poll", JSONObject().put("device", device).put("rev", rev).put("wait", POLL_WAIT))
                if (stopping) break
                take(got)
                val commands = got.optJSONArray("commands") ?: JSONArray()
                for (i in 0 until commands.length()) act(commands.getJSONObject(i))
                continue
            } catch (e: HostError) {
                val was = greeted
                online = false
                greeted = false
                if (e.status == 404 && was) continue           // the host was restarted: it doesn't know us any more
                if (e.status == 401 || e.status == 429) wait = LINK_REFUSED
                lastProblem = e.message
                if (e.message != told) {
                    told = e.message
                    log("Folder syncing waits: ${e.message}")
                }
                onChange()
            } catch (e: Exception) {
                online = false
                greeted = false
                lastProblem = e.message ?: e.toString()
                log("Folder syncing: $e")
                onChange()
            }
            if (!stopping) linkPause.await(wait)
        }
        online = false
    }

    /** Try to reach the host now (the network came back, say) instead of waiting out the pause. */
    fun retryNow() {
        linkPause.set()
        wake.set()
    }

    private fun reporter() {
        while (!stopping) {
            report.await(REPORT_EVERY)
            if (!online || stopping) continue
            try {
                client.syncCall("report", JSONObject().put("device", device).put("running", running ?: JSONObject.NULL)
                    .put("status", snapshot()))
            } catch (e: HostError) {
                // the other thread notices and connects again
            } catch (e: Exception) {
                log("Folder syncing: report: $e")
            }
            Thread.sleep(1000)
        }
    }

    // ------------------------------------------------------------ when a pair runs
    private fun nextTime(j: Job, now: Double): Double? {
        val s = lock.withLock { status[j.id] } ?: return null
        val last = s.optDouble("last_run", 0.0).let { if (it.isNaN()) 0.0 else it }
        val retry = if (s.has("retry_at") && !s.isNull("retry_at")) s.optDouble("retry_at") else null
        var next: Double? = when (j.trigger) {
            "manual" -> null
            "live" -> maxOf(last, looked[j.id] ?: 0.0) + maxOf(livePoll, MIN_GAP)
            "interval" -> if (last > 0) last + j.every * 60.0 else now
            else -> nextDaily(j.at, if (last > 0) last else s.optDouble("since", now))
        }
        if (retry != null && j.trigger != "manual") next = if (next == null) retry else minOf(next, retry)
        return next
    }

    private fun due(now: Double): Job? {
        if (!online || mayRun() != null) return null          // nothing can be copied without the host
        val asked = lock.withLock { requests.firstOrNull()?.also { requests.remove(it) } }
        if (asked != null) job(asked)?.let { return it }
        for (j in jobs) {
            if (!j.enabled) continue
            val next = nextTime(j, now)
            if (next == null || next > now) continue
            if (j.trigger == "live" && !retryDue(j, now) && now - lastRun(j) < liveFull && !changedHere(j)) {
                looked[j.id] = now               // nothing new in the folder: no need to ask the host for everything
                continue
            }
            return j
        }
        return null
    }

    private fun lastRun(j: Job) = lock.withLock { status[j.id]?.optDouble("last_run", 0.0) ?: 0.0 }.let { if (it.isNaN()) 0.0 else it }

    private fun retryDue(j: Job, now: Double): Boolean {
        val s = lock.withLock { status[j.id] } ?: return false
        return s.has("retry_at") && !s.isNull("retry_at") && s.optDouble("retry_at") <= now
    }

    /** The files of a pair's folder here, as one number: it changes when a file comes, goes or changes. */
    private fun print(j: Job): Long {
        var sum = 0L
        for ((rel, st) in scanLocal(File(j.local), Excluder(j.exclude), ArrayList(), skip = trashRoot).files)
            sum += (rel.hashCode() * 31L + st.size) * 1_000_003L + st.mtime.toBits()
        return sum
    }

    private fun changedHere(j: Job): Boolean {
        if (j.direction == "down") return false
        val before = prints[j.id] ?: return true
        return print(j) != before
    }

    private fun loop() {
        while (!stopping) {
            wake.await(1000)
            if (stopping) break
            try {
                val t = now()
                due(t)?.let { runJob(it) }
                if (t - lastTrash > 3600) {
                    lastTrash = t
                    cleanTrash()
                }
            } catch (e: Exception) {
                log("Folder syncing: $e")
                Thread.sleep(5000)
            }
        }
    }

    /** Run one pair now and record how it went. Returns the error ("" when there was none). */
    fun runJob(job: Job, settle: Double = SETTLE): String {
        val id = job.id
        val prog = JSONObject().put("done", 0).put("total", 0).put("bytes", 0L).put("total_bytes", 0L).put("file", "")
        val started = now()
        lock.withLock {
            status.getOrPut(id) { JSONObject().put("since", started) }.put("state", "running").put("started", started)
                .put("error", "").put("retry_at", JSONObject.NULL).put("progress", prog)
        }
        running = id
        cancel = null
        save(force = true)
        onChange()
        val progress = object : Progress {
            override fun plan(total: Int, totalBytes: Long) {
                lock.withLock { prog.put("total", total).put("total_bytes", totalBytes) }
                save()
            }

            override fun file(rel: String, action: String, size: Long) {
                lock.withLock { prog.put("file", rel).put("action", action) }
                save()
            }

            override fun done(bytes: Long) {
                lock.withLock { prog.put("done", prog.optInt("done") + 1).put("bytes", prog.optLong("bytes") + bytes) }
                save()
            }
        }
        val run = SyncRun(client, job, dir, trashRoot, name, settle, keepFree, { cancel == id || stopping }, progress, touched)
        var error = ""
        try {
            refuse(job)?.let { throw SyncError(it) }
            run.run()
        } catch (e: Stopped) {
            error = "Stopped before it was finished."
        } catch (e: SyncError) {
            error = e.message ?: "Failed"
        } catch (e: HostError) {
            error = "Failed: ${e.message}"
        } catch (e: Exception) {
            error = "Failed: ${e.message ?: e}"
            log("Sync ${job.name} failed: $e")
        } finally {
            running = null
        }
        val t = now()
        val res = run.r
        val stoppedByHand = error.startsWith("Stopped")
        lock.withLock {
            val s = status.getOrPut(id) { JSONObject().put("since", started) }
            val last = JSONObject()
            for ((k, v) in res) last.put(k, v)
            s.put("last_run", t).put("duration", Math.round((t - started) * 10) / 10.0).put("last", last)
                .put("problems", JSONArray(run.problems.take(50))).put("error", error)
                .put("state", if (error.isNotEmpty() && !stoppedByHand) "error" else "idle")
            s.remove("progress")
            if (error.isEmpty()) s.put("last_ok", t)
            if (error.isNotEmpty() && !stoppedByHand) {
                s.put("retry_at", t + FAIL_RETRY)
            } else if ((res["busy"] ?: 0) > 0 || (job.mode == "move" && (res["waiting"] ?: 0) > 0)) {
                var retry = t + RETRY
                run.settledAt?.let { retry = minOf(retry, maxOf(t + 1.0, it + 0.5)) }
                s.put("retry_at", retry)
            }
            val entry = JSONObject().put("at", t).put("error", error)
            for (k in listOf("up", "down", "deleted_remote", "deleted_local", "moved", "conflicts", "errors", "bytes"))
                entry.put(k, res[k] ?: 0)
            val hist = JSONArray().put(entry)
            val old = s.optJSONArray("history")
            if (old != null) for (i in 0 until minOf(old.length(), HISTORY - 1)) hist.put(old.get(i))
            s.put("history", hist)
        }
        if (job.trigger == "live" && job.direction != "down") prints[id] = try {
            print(job)
        } catch (e: Exception) {
            0L
        }
        if (error.isNotEmpty()) log("Sync ${job.name}: $error")
        else if (listOf("up", "down", "deleted_remote", "deleted_local", "moved", "conflicts", "errors").any { (res[it] ?: 0) > 0 })
            log("Sync ${job.name}: ${res["up"]} copied to the drive, ${res["down"]} copied here, ${res["deleted_remote"]} deleted " +
                "on the drive, ${res["deleted_local"]} removed here, ${res["moved"]} moved, ${res["conflicts"]} conflict(s), " +
                "${res["errors"]} problem(s)")
        save(force = true)
        onChange()
        return error
    }

    /** {pair id: how it is doing}, as the host's Sync page shows it. */
    fun snapshot(): JSONObject {
        val out = JSONObject()
        val t = now()
        val held = mayRun()
        for (j in jobs) {
            val s = lock.withLock { JSONObject((status[j.id] ?: JSONObject()).toString()) }
            if (!s.has("state")) s.put("state", "idle")
            if (!j.enabled && s.optString("state") != "running") s.put("state", "paused")
            s.put("next", if (j.enabled) nextTime(j, t) ?: JSONObject.NULL else JSONObject.NULL)
            s.put("watching", false)
            if (held != null && j.enabled && s.optString("state") != "running") s.put("error", held)
            out.put(j.id, s)
        }
        return out
    }

    /** One line about what is going on, for this device's own screen and notification. */
    fun summary(): String {
        val id = running
        if (id != null) {
            val p = lock.withLock { status[id]?.optJSONObject("progress")?.let { JSONObject(it.toString()) } }
            val what = job(id)?.name ?: "a folder"
            return if (p != null && p.optInt("total") > 0) "Syncing $what: ${p.optInt("done")} of ${p.optInt("total")} files" else "Syncing $what"
        }
        if (!online) return lastProblem ?: "Connecting to the host"
        mayRun()?.let { return it }
        return when (val n = jobs.count { it.enabled }) {
            0 -> "Connected. Add folders on the host's Sync page"
            1 -> "Connected · 1 folder in step"
            else -> "Connected · $n folders in step"
        }
    }

    // ------------------------------------------------------------ housekeeping
    private fun save(force: Boolean = false) {
        val t = System.currentTimeMillis()
        if (!force && t - lastSave < 1000) return
        lastSave = t
        try {
            val all = JSONObject()
            lock.withLock { for ((k, v) in status) all.put(k, JSONObject(v.toString())) }
            write(statusFile, JSONObject().put("time", t / 1000.0).put("running", running ?: JSONObject.NULL).put("jobs", all).toString())
        } catch (e: Exception) {
            // shown again at the next change
        }
        report.set()
    }

    private fun cleanTrash() {
        val cutoff = System.currentTimeMillis() - (TRASH_DAYS * 86_400_000).toLong()
        for (job in trashRoot.listFiles() ?: return)
            for (run in job.listFiles() ?: continue)
                if (run.lastModified() < cutoff) run.deleteRecursively()
    }

    // ------------------------------------------------------------ outside
    fun start(): Agent {
        stopping = false
        for ((name, body) in listOf<Pair<String, () -> Unit>>("Sync" to ::loop, "SyncLink" to ::link, "SyncReport" to ::reporter))
            threads.add(thread(name = name, isDaemon = true, block = body))
        return this
    }

    /** Stop syncing (a pair that is running is given up). Returns at once; `join` waits for it. */
    fun stop() {
        stopping = true
        cancel = running
        wake.set()
        report.set()
        linkPause.set()
        val wasOnline = online
        threads.add(thread(name = "SyncStop", isDaemon = true) {
            if (wasOnline) try {             // let go of the question we have open at the host
                client.syncCall("poll", JSONObject().put("device", device).put("rev", rev).put("wait", 0))
            } catch (e: Exception) {
                // gone anyway
            }
        })
    }

    fun join(ms: Long = 8000) {
        for (t in threads.toList()) t.join(ms)
    }
}
