package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** What a pass tells while it runs (the bar on the host's Sync page). */
interface Progress {
    fun plan(total: Int, totalBytes: Long) {}
    fun file(rel: String, action: String, size: Long) {}
    fun done(bytes: Long) {}

    /** That much more of the file being copied is there. */
    fun part(bytes: Long) {}

    /** What the copy is waiting for ("" = nothing any more). */
    fun note(text: String) {}
}

/**
 * One pass of one pair: sync.py's Run, against a drive that is on the host. The modes do what they
 * do there; a difference is only in how the drive is reached (requests to the host's dashboard).
 */
class SyncRun(private val client: HostClient, private val job: Job, private val stateDir: File, private val trashRoot: File,
              private val device: String, private val settle: Double = SETTLE, private val keepFree: Long = 0,
              private val stop: () -> Boolean = { false }, private val progress: Progress = object : Progress {},
              private val touched: (File) -> Unit = {},         // a file of this device appeared, changed or went (a phone's gallery wants to know)
              private val parts: Int = 0,                       // > 0: the host takes files in parts (2.4.0 on), of this size
              private val vault: Vault? = null) {               // the drive is an end-to-end encrypted account: files are sealed here
    val r = linkedMapOf("up" to 0L, "down" to 0L, "deleted_remote" to 0L, "deleted_local" to 0L, "moved" to 0L, "conflicts" to 0L,
                        "bytes" to 0L, "busy" to 0L, "waiting" to 0L, "errors" to 0L, "skipped_deletes" to 0L, "kept_away" to 0L,
                        "files" to 0L, "remote_files" to 0L)
    val problems = ArrayList<String>()
    var settledAt: Double? = null        // when the newest file held back because it was just written can go

    private val localRoot = File(job.local)
    private val remote = job.remote
    private val excluded = Excluder(job.exclude)
    private val knownDirs = HashSet<String>()            // folders of the drive (inside the pair's) known to be there
    private var rootThere = false
    private var trash: File? = null
    private var lastScan: RemoteScan? = null
    // Copies made here whose time this device would not take (some phones' storage refuses): the file
    // as it is here -> the file on the drive it is a copy of. Without it they would look changed forever.
    private val aliasFile = File(stateDir, "${job.id}.alias.json")
    private val aliases = HashMap<String, Pair<Stamp, Stamp>>()

    // -------------------------------------------------- helpers
    private fun l(rel: String) = File(localRoot, rel)

    private fun rp(rel: String) = if (rel.isEmpty()) remote else if (remote == "/") "/$rel" else "$remote/$rel"

    private fun inc(key: String, by: Long = 1) {
        r[key] = (r[key] ?: 0) + by
    }

    private fun problem(rel: String, message: String?) {
        inc("errors")
        if (problems.size < 50) problems.add("$rel: ${message ?: "failed"}")
    }

    private fun settled(l: Stamp): Boolean {
        if (settle > 0 && now() - l.mtime < settle) {
            inc("busy")
            settledAt = minOf(settledAt ?: Double.MAX_VALUE, l.mtime + settle)
            return false
        }
        return true
    }

    private fun check() {
        if (stop()) throw Stopped()
    }

    /** What the host refuses about one file is that file's problem; a host that is gone ends the pass. */
    private fun oneFile(e: HostError) = e.status in setOf(400, 403, 404, 409, 413, 507)

    // The drive, reached the plain way or as an end-to-end encrypted account (where it is this device
    // that knows the list of files, and files go and come sealed).
    private var waiting: Set<String> = emptySet()      // (vault) objects the host has but hasn't stored on Discord yet

    private fun rScan(): RemoteScan {
        val v = vault ?: return scanRemote(client, remote, excluded)
        v.sync()
        waiting = v.pending()
        val files = HashMap<String, RemoteFile>()
        val dirs = HashSet<String>()
        val root = v.resolve(remote) ?: return RemoteScan(false, false, files, dirs)
        if (!root.optBoolean("dir")) return RemoteScan(true, false, files, dirs)
        val todo = ArrayDeque(listOf(root.getString("id") to ""))
        while (todo.isNotEmpty()) {
            val (id, relDir) = todo.removeLast()
            for (n in v.children(id)) {
                val rel = if (relDir.isEmpty()) n.getString("name") else relDir + "/" + n.getString("name")
                if (excluded(rel)) continue
                if (n.optBoolean("dir")) {
                    dirs.add(rel)
                    todo.add(n.getString("id") to rel)
                } else {
                    files[rel] = RemoteFile(Stamp(n.optLong("size"), n.optDouble("mtime")), if (n.optString("obj") in waiting) "pending" else "synced")
                }
            }
        }
        return RemoteScan(true, true, files, dirs)
    }

    private fun rDelete(path: String) = if (vault != null) vault.delete(path) else client.delete(path)

    private fun mkdirRemote(path: String) {
        if (vault != null) return vault.mkdirs(path)
        try {
            client.mkdir(path)
        } catch (e: HostError) {
            if (e.status != 409) throw e
        }
    }

    private fun ensureRoot() {
        if (rootThere) return
        var made = ""
        for (part in remote.trim('/').split('/').filter { it.isNotEmpty() }) {
            made += "/$part"
            mkdirRemote(made)
        }
        rootThere = true
    }

    /** Have folder `rel` (inside the pair's drive folder) exist on the drive. */
    private fun ensureRemoteDir(rel: String) {
        ensureRoot()
        if (rel.isEmpty() || rel in knownDirs) return
        var made = ""
        for (part in rel.split('/')) {
            made = if (made.isEmpty()) part else "$made/$part"
            if (made !in knownDirs) {
                mkdirRemote(rp(made))
                knownDirs.add(made)
            }
        }
    }

    private fun remoteStamp(rel: String): Stamp? {
        if (vault != null) {
            vault.sync()
            val n = vault.resolve(rp(rel)) ?: return null
            return if (n.optBoolean("dir")) null else Stamp(n.optLong("size"), n.optDouble("mtime"))
        }
        val items = try {
            client.list(rp(rel.substringBeforeLast('/', "")))
        } catch (e: HostError) {
            if (oneFile(e)) return null
            throw e
        }
        val name = rel.substringAfterLast('/')
        for (i in 0 until items.length()) {
            val e = items.getJSONObject(i)
            if (e.getString("name") == name && !e.optBoolean("dir")) return Stamp(e.getLong("size"), e.getDouble("mtime"))
        }
        return null
    }

    private fun remoteDirEmpty(rel: String) = try {
        if (vault != null) {
            vault.sync()
            vault.resolve(rp(rel))?.let { vault.children(it.getString("id")).isEmpty() } ?: false
        } else client.list(rp(rel)).length() == 0
    } catch (e: HostError) {
        if (!oneFile(e)) throw e
        false
    }

    // -------------------------------------------------- copying
    /** Copy file `rel` of this device onto the drive, keeping its modification time. */
    private fun upload(rel: String, l: Stamp): Boolean {
        val src = l(rel)
        progress.file(rel, "up", l.size)
        val size: Long
        try {
            ensureRemoteDir(rel.substringBeforeLast('/', ""))
            if (rel in knownDirs) throw IOException("the drive has a folder with that name")
            size = src.length()
            if (vault != null) vault.put(rp(rel), src, size, src.lastModified() / 1000.0, stop, { progress.part(it) }, { progress.note(it) })
            else if (parts > 0) client.uploadParts(rp(rel), src, size, src.lastModified() / 1000.0, stop, parts, { progress.part(it) }, { progress.note(it) })
            else client.upload(rp(rel), size, src.lastModified() / 1000.0, stop) { FileInputStream(src) }
        } catch (e: HostError) {
            if (!oneFile(e)) throw e
            problem(rel, e.message)
            return false
        } catch (e: LocalError) {
            problem(rel, e.message)
            return false
        } catch (e: IOException) {
            problem(rel, e.message)
            return false
        }
        inc("up")
        inc("bytes", size)
        progress.done(size)
        return true
    }

    /** Copy drive file `rel` into the folder of this device, keeping its modification time. */
    private fun download(rel: String, rf: Stamp): Boolean {
        val dst = l(rel)
        val tmp = File(dst.path + ".ddsync-part")
        progress.file(rel, "down", rf.size)
        try {
            val parent = dst.parentFile ?: localRoot
            val free = (if (parent.isDirectory) parent else localRoot).usableSpace
            if (free - rf.size < keepFree)
                throw IOException("not enough free space here (%.1f GiB free, keeping %.1f GiB free)".format(free / 1073741824.0, keepFree / 1073741824.0))
            parent.mkdirs()
            if (dst.isDirectory) throw IOException("this device has a folder with that name")
            val got = FileOutputStream(tmp).use { out ->
                if (vault != null) vault.read(vault.resolve(rp(rel)) ?: throw IOException("it is gone from your account"), out, stop)
                else client.download(rp(rel), out, stop)
            }
            if (got != rf.size || remoteStamp(rel) != rf)
                throw IOException("it changed while it was being copied; trying again next time")
            tmp.setLastModified(Math.round(rf.mtime * 1000))
            try {
                Files.move(tmp.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (e: IOException) {
                Files.move(tmp.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            val here = stampOf(dst)
            if (same(here, rf)) aliases.remove(rel) else aliases[rel] = here to rf      // the time didn't stick
            touched(dst)
        } catch (e: Stopped) {
            tmp.delete()
            throw e
        } catch (e: HostError) {
            tmp.delete()
            if (!oneFile(e)) throw e
            problem(rel, e.message)
            return false
        } catch (e: LocalError) {
            tmp.delete()
            problem(rel, e.message)
            return false
        } catch (e: IOException) {
            tmp.delete()
            problem(rel, e.message)
            return false
        }
        inc("down")
        inc("bytes", rf.size)
        progress.done(rf.size)
        return true
    }

    private fun deleteRemote(rel: String): Boolean {
        try {
            rDelete(rp(rel))
        } catch (e: HostError) {
            if (e.status == 404) return true
            if (!oneFile(e)) throw e
            problem(rel, e.message)
            return false
        }
        inc("deleted_remote")
        return true
    }

    /** Move a file of this device into the holding folder (never deleted outright). */
    private fun deleteLocal(rel: String): Boolean {
        val src = l(rel)
        if (!src.exists()) return true
        val dir = trash ?: File(File(trashRoot, job.id), java.text.SimpleDateFormat("yyyyMMdd-HHmmss", java.util.Locale.US)
            .format(java.util.Date())).also { trash = it }
        val dst = File(dir, rel)
        try {
            dst.parentFile?.mkdirs()
            try {
                Files.move(src.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING)
            } catch (e: IOException) {
                Files.copy(src.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING)
                if (!src.delete()) throw IOException("could not be removed")
            }
        } catch (e: IOException) {
            problem(rel, e.message)
            return false
        }
        inc("deleted_local")
        touched(src)
        return true
    }

    // -------------------------------------------------- state kept between passes
    private fun readState(file: File): JSONObject {
        val st = try {
            JSONObject(file.readText())
        } catch (e: Exception) {
            return JSONObject()
        }
        return if (st.optString("local") == job.local && st.optString("remote") == remote) st else JSONObject()
    }

    private fun stamps(o: JSONObject?): HashMap<String, Stamp> {
        val out = HashMap<String, Stamp>()
        if (o == null) return out
        for (k in o.keys()) {
            val v = o.optJSONArray(k) ?: continue
            out[k] = Stamp(v.optLong(0), v.optDouble(1))
        }
        return out
    }

    private fun writeState(file: File, files: Map<String, Stamp>, dirs: Collection<String>? = null) {
        val fo = JSONObject()
        for ((k, v) in files) fo.put(k, JSONArray().put(v.size).put(v.mtime))
        val st = JSONObject().put("local", job.local).put("remote", remote).put("files", fo)
        if (dirs != null) st.put("dirs", JSONArray(dirs.sorted()))
        val tmp = File(file.path + ".tmp")
        tmp.writeText(st.toString())
        Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING)
    }

    private fun loadAliases(local: HashMap<String, Stamp>) {
        val st = readState(aliasFile).optJSONObject("files") ?: return
        for (k in st.keys()) {
            val v = st.optJSONArray(k) ?: continue
            val here = Stamp(v.optLong(0), v.optDouble(1))
            val there = Stamp(v.optLong(2), v.optDouble(3))
            if (same(local[k], here)) {              // still the copy we made: it counts as what it is a copy of
                aliases[k] = here to there
                local[k] = there
            }
        }
    }

    private fun saveAliases() {
        if (aliases.isEmpty() && !aliasFile.exists()) return
        val fo = JSONObject()
        for ((k, v) in aliases) fo.put(k, JSONArray().put(v.first.size).put(v.first.mtime).put(v.second.size).put(v.second.mtime))
        val tmp = File(aliasFile.path + ".tmp")
        tmp.writeText(JSONObject().put("local", job.local).put("remote", remote).put("files", fo).toString())
        Files.move(tmp.toPath(), aliasFile.toPath(), StandardCopyOption.REPLACE_EXISTING)
    }

    // -------------------------------------------------- the pass
    fun run(): Map<String, Long> {
        val direction = job.direction
        if (direction != "down" && !localRoot.isDirectory)
            throw SyncError("The folder ${job.local} isn't there (storage that isn't connected?). Nothing was changed.")
        if (direction == "down") localRoot.mkdirs()
        val remoteScan = rScan()
        if (remoteScan.exists && !remoteScan.isDir) throw SyncError("$remote on the drive is a file, not a folder.")
        rootThere = remoteScan.exists
        if (!remoteScan.exists) {
            if (job.mode == "download-mirror") throw SyncError("$remote doesn't exist on the drive. Nothing was changed.")
            if (direction != "down") ensureRoot()
        }
        knownDirs.addAll(remoteScan.dirs)
        stateDir.mkdirs()
        val local = scanLocal(localRoot, excluded, problems, skip = trashRoot)
        loadAliases(local.files)
        r["files"] = local.files.size.toLong()
        r["remote_files"] = remoteScan.files.size.toLong()
        if (local.files.isEmpty() && direction != "down") problems.add("${job.local} is empty, so there is nothing to copy.")
        val rs = HashMap<String, Stamp>()
        for ((k, v) in remoteScan.files) rs[k] = v.stamp
        try {
            when (job.mode) {
                "backup" -> backup(local.files, local.dirs, rs, remoteScan.dirs)
                "mirror" -> up(local.files, local.dirs, rs, remoteScan.dirs, exact = true, known = null)
                "move" -> move(local.files, local.dirs, rs, remoteScan.dirs)
                "download" -> down(local.files, local.dirs, rs, remoteScan.dirs, exact = false)
                "download-mirror" -> down(local.files, local.dirs, rs, remoteScan.dirs, exact = true)
                "two-way" -> twoWay(local.files, local.dirs, rs, remoteScan.dirs)
            }
        } finally {
            saveAliases()
        }
        if (direction != "down") {               // how many files are still on their way to Discord
            val after = lastScan ?: rScan()
            r["waiting"] = after.files.values.count { it.state != "synced" }.toLong()
        }
        return r
    }

    private fun plan(sizes: List<Long>) = progress.plan(sizes.size, sizes.sum())

    // ---------- this device -> drive
    /**
     * known: what this pair has already copied (backup). A file that hasn't changed here since
     * then is left alone, wherever its copy on the drive went.
     */
    private fun up(L: Map<String, Stamp>, Ldirs: Set<String>, R: Map<String, Stamp>, Rdirs: Set<String>, exact: Boolean,
                   known: HashMap<String, Stamp>?) {
        val uploads = ArrayList<String>()
        for ((rel, l) in L) {
            if (same(l, R[rel])) {
                known?.put(rel, l)
            } else if (known != null && same(l, known[rel])) {
                inc("kept_away")                 // moved, renamed, changed or deleted on the drive by its owner
            } else if (settled(l)) {
                uploads.add(rel)
            }
        }
        uploads.sort()
        plan(uploads.map { L.getValue(it).size })
        for (rel in uploads) {
            check()
            if (upload(rel, L.getValue(rel)) && known != null) known[rel] = L.getValue(rel)
        }
        for (rel in (Ldirs - Rdirs).sorted()) {
            if (known != null && uploads.none { it == rel || it.startsWith("$rel/") } && known.keys.any { it.startsWith("$rel/") })
                continue                         // a folder that was backed up and then moved away on the drive
            check()
            try {
                ensureRemoteDir(rel)
            } catch (e: HostError) {
                if (!oneFile(e)) throw e
                problem(rel, e.message)
            }
        }
        if (!exact) return
        val gone = R.keys.filter { it !in L }.sorted()
        val goneDirs = Rdirs.filter { it !in Ldirs }
        if (gone.size >= GUARD && L.isEmpty() && Ldirs.isEmpty()) {
            // An empty source almost always means storage that isn't there or a mistake: don't wipe the drive.
            r["skipped_deletes"] = gone.size.toLong()
            problems.add("This folder is empty, so the ${gone.size} file(s) in $remote were not deleted. " +
                         "Delete them on the drive yourself if that is what you want.")
            return
        }
        for (rel in gone) {
            check()
            deleteRemote(rel)
        }
        for (rel in goneDirs.sortedByDescending { p -> p.count { it == '/' } }) {
            if (remoteDirEmpty(rel)) {
                try {
                    rDelete(rp(rel))
                } catch (e: HostError) {
                    if (!oneFile(e)) throw e
                    if (e.status != 404) problem(rel, e.message)
                }
            }
        }
    }

    private fun backup(L: Map<String, Stamp>, Ldirs: Set<String>, R: Map<String, Stamp>, Rdirs: Set<String>) {
        val file = File(stateDir, "${job.id}.backup.json")
        val known = HashMap(stamps(readState(file).optJSONObject("files")).filterKeys { it in L })
        try {
            up(L, Ldirs, R, Rdirs, exact = false, known = known)
        } finally {
            writeState(file, known)
        }
        val away = r["kept_away"] ?: 0
        if (away > 0) problems.add("$away file(s) backed up earlier are no longer where the backup put them on the drive " +
                                   "(moved, renamed or deleted there). They are not copied again unless they change here; " +
                                   "“Copy everything again” puts them back.")
    }

    private fun move(L: Map<String, Stamp>, Ldirs: Set<String>, R: Map<String, Stamp>, Rdirs: Set<String>) {
        up(L, Ldirs, R, Rdirs, exact = false, known = null)
        // Delete what is safely in Discord (uploaded, same size and time) and hasn't changed since.
        val after = rScan().also { lastScan = it }
        val emptied = HashSet<String>()
        for (rel in L.keys.sorted()) {
            check()
            val node = after.files[rel] ?: continue
            if (node.state != "synced") continue
            val src = l(rel)
            if (!src.isFile || !same(stampOf(src), node.stamp)) continue
            if (src.delete()) {
                inc("moved")
                touched(src)
                emptied.add(rel.substringBeforeLast('/', ""))
            } else {
                problem(rel, "could not be removed from this device")
            }
        }
        // Remove sub-folders the move left empty (never the folder itself).
        for (rel in Ldirs.sortedByDescending { p -> p.count { it == '/' } })
            if (emptied.any { it == rel || it.startsWith("$rel/") }) l(rel).delete()          // only when empty
    }

    // ---------- drive -> this device
    private fun down(L: Map<String, Stamp>, Ldirs: Set<String>, R: Map<String, Stamp>, Rdirs: Set<String>, exact: Boolean) {
        val downloads = R.keys.filter { !same(R[it], L[it]) }.sorted()
        plan(downloads.map { R.getValue(it).size })
        for (rel in downloads) {
            check()
            download(rel, R.getValue(rel))
        }
        for (rel in (Rdirs - Ldirs).sorted()) l(rel).mkdirs()
        if (!exact) return
        val gone = L.keys.filter { it !in R }.sorted()
        if (gone.size >= GUARD && R.isEmpty() && Rdirs.isEmpty()) {
            r["skipped_deletes"] = gone.size.toLong()
            problems.add("$remote is empty on the drive, so the ${gone.size} file(s) here were not removed.")
            return
        }
        for (rel in gone) {
            check()
            deleteLocal(rel)
        }
        for (rel in (Ldirs - Rdirs).sortedByDescending { p -> p.count { it == '/' } }) l(rel).delete()       // only when empty
    }

    // ---------- both ways
    private fun twoWay(L: Map<String, Stamp>, Ldirs: Set<String>, R: Map<String, Stamp>, Rdirs: Set<String>) {
        val file = File(stateDir, "${job.id}.state.json")
        val st = readState(file)
        val B = stamps(st.optJSONObject("files"))
        val Bdirs = HashSet<String>()
        st.optJSONArray("dirs")?.let { for (i in 0 until it.length()) Bdirs.add(it.getString(i)) }
        val newB = HashMap<String, Stamp>()
        val todo = ArrayList<Pair<String, String>>()
        val lostLocal = L.isEmpty() && Ldirs.isEmpty() && B.size >= GUARD
        val lostRemote = R.isEmpty() && Rdirs.isEmpty() && B.size >= GUARD
        for (rel in (L.keys + R.keys + B.keys).sorted()) {
            val l = L[rel]
            val rr = R[rel]
            val b = B[rel]
            if (l != null && !settled(l)) {
                if (b != null) newB[rel] = b
                continue
            }
            if (l != null && rr != null) {
                when {
                    same(l, rr) -> newB[rel] = l
                    b != null && same(rr, b) -> todo.add("up" to rel)
                    b != null && same(l, b) -> todo.add("down" to rel)
                    else -> todo.add("conflict" to rel)
                }
            } else if (l != null) {
                if (b == null || !same(l, b)) {
                    todo.add("up" to rel)
                } else if (lostRemote) {
                    newB[rel] = b
                    inc("skipped_deletes")
                } else {
                    todo.add("del-local" to rel)
                }
            } else if (rr != null) {
                if (b == null || !same(rr, b)) {
                    todo.add("down" to rel)
                } else if (lostLocal) {
                    newB[rel] = b
                    inc("skipped_deletes")
                } else {
                    todo.add("del-remote" to rel)
                }
            }
        }
        if ((r["skipped_deletes"] ?: 0) > 0) {
            val side = if (lostLocal) "This folder" else "$remote on the drive"
            problems.add("$side is empty, so nothing was deleted on the other side. Delete the files there yourself " +
                         "if that is what you want.")
        }
        plan(todo.filter { it.first in setOf("up", "down", "conflict") }.map { (L[it.second] ?: R[it.second])?.size ?: 0L })
        var dirsNow: Set<String> = Bdirs
        try {
            for ((action, rel) in todo) {
                check()
                when (action) {
                    "up" -> if (upload(rel, L.getValue(rel))) newB[rel] = L.getValue(rel)
                    "down" -> if (download(rel, R.getValue(rel))) newB[rel] = R.getValue(rel)
                    "del-local" -> if (!deleteLocal(rel)) newB[rel] = B.getValue(rel)
                    "del-remote" -> if (!deleteRemote(rel)) newB[rel] = B.getValue(rel)
                    else -> conflict(rel, L.getValue(rel), R.getValue(rel), newB)
                }
            }
            // Folders: new ones are created on the other side; one removed on one side goes on the
            // other once nothing is left in it.
            val oneSide = (Ldirs - Rdirs) + (Rdirs - Ldirs)
            val removed = oneSide.filter { it in Bdirs && !(lostLocal || lostRemote) }.toSet()
            for (rel in (oneSide - removed).sortedBy { p -> p.count { it == '/' } }) {
                if (rel in Ldirs) ensureRemoteDir(rel) else l(rel).mkdirs()
            }
            for (rel in removed.sortedByDescending { p -> p.count { it == '/' } }) {
                if (rel in Ldirs) {
                    l(rel).delete()              // only when empty
                } else if (remoteDirEmpty(rel)) {
                    try {
                        rDelete(rp(rel))
                    } catch (e: HostError) {
                        if (!oneFile(e)) throw e
                    }
                }
            }
            val here = scanLocal(localRoot, excluded, ArrayList(), skip = trashRoot)
            val there = rScan().also { lastScan = it }
            dirsNow = here.dirs.intersect(there.dirs)
            newB.keys.retainAll(here.files.keys)
        } finally {
            writeState(file, newB, dirsNow)
        }
    }

    /** Both sides changed: the newer one keeps the name, the other is kept as a conflict copy. */
    private fun conflict(rel: String, l: Stamp, rf: Stamp, newB: HashMap<String, Stamp>) {
        inc("conflicts")
        val copy = conflictCopyName(rel, device)
        try {
            if (l.mtime >= rf.mtime) {
                if (vault != null) vault.move(rp(rel), rp(copy)) else client.move(rp(rel), rp(copy))
                if (upload(rel, l)) newB[rel] = l
                if (download(copy, rf)) newB[copy] = rf
            } else {
                Files.move(l(rel).toPath(), l(copy).toPath(), StandardCopyOption.REPLACE_EXISTING)
                touched(l(rel))
                touched(l(copy))
                val st = stampOf(l(copy))
                if (download(rel, rf)) newB[rel] = rf
                if (upload(copy, st)) newB[copy] = st
            }
        } catch (e: HostError) {
            if (!oneFile(e)) throw e
            problem(rel, e.message)
        } catch (e: IOException) {
            problem(rel, e.message)
        }
    }
}
