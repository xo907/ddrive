package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import kotlin.math.abs

/*
 * The rules of folder syncing, as on a computer (ddrive/sync.py, which this follows closely so
 * that a folder synced by a phone behaves like one synced by a computer): what a pair is, which
 * files are left out, how two copies are told apart.
 */

/** mode -> which way it copies: "up" (this device -> drive), "down", "both". */
val MODES = mapOf("backup" to "up", "mirror" to "up", "two-way" to "both", "move" to "up",
                  "download" to "down", "download-mirror" to "down")

val DEFAULT_EXCLUDE = listOf("*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload", "*.tmp", "~$*",
                             ".~lock.*", "Thumbs.db", "desktop.ini", ".DS_Store", "*.ddsync-part",
                             // what Android keeps next to photos: half-written and half-deleted files, previews
                             ".pending-*", ".trashed-*", ".thumbnails")

const val SETTLE = 5.0              // a file must be unchanged this long (seconds) before it is copied
const val MIN_GAP = 5.0             // live pairs: at least this long between two passes
const val RETRY = 15.0              // run again this soon when files were busy or still uploading
const val FAIL_RETRY = 120.0        // a pass that failed altogether is tried again after this long
const val TRASH_DAYS = 30.0         // files removed from this device by a sync are kept this long
const val GUARD = 3                 // a side that is suddenly empty deletes nothing on the other if that'd be this many files
const val MTIME_SLACK = 2.0         // some storage keeps times in 2 second steps
const val HISTORY = 10

class SyncError(message: String) : Exception(message)

/** A file as the comparison sees it: its size and when it was last changed (seconds). */
data class Stamp(val size: Long, val mtime: Double)

fun same(a: Stamp?, b: Stamp?) = a != null && b != null && a.size == b.size && abs(a.mtime - b.mtime) <= MTIME_SLACK

/** A pair: a folder of this device and a folder of the drive, what is copied which way, and when. */
data class Job(val id: String, val name: String, val local: String, val remote: String, val mode: String,
               val trigger: String, val every: Int, val at: String, val exclude: List<String>, val enabled: Boolean) {
    val direction get() = MODES[mode] ?: "up"

    companion object {
        fun from(j: JSONObject): Job {
            val ex = j.optJSONArray("exclude")
            return Job(id = j.getString("id"), name = j.optString("name"), local = j.optString("local"),
                       remote = "/" + j.optString("remote").trim('/'),
                       mode = j.optString("mode", "backup").let { if (it in MODES) it else "backup" },
                       trigger = j.optString("trigger", "live"), every = maxOf(1, j.optInt("every", 60)),
                       at = j.optString("at", "03:00"),
                       exclude = (0 until (ex?.length() ?: 0)).map { ex!!.optString(it) }.filter { it.isNotBlank() },
                       enabled = j.optBoolean("enabled", true))
        }
    }
}

/** Shell-style pattern (*, ?, [abc]) as a regular expression, like Python's fnmatch. */
internal fun globRegex(pattern: String): Regex {
    val out = StringBuilder()
    var i = 0
    while (i < pattern.length) {
        val c = pattern[i]
        when {
            c == '*' -> out.append(".*")
            c == '?' -> out.append('.')
            c == '[' -> {
                var j = i + 1
                if (j < pattern.length && pattern[j] == '!') j++
                if (j < pattern.length && pattern[j] == ']') j++
                while (j < pattern.length && pattern[j] != ']') j++
                if (j >= pattern.length) {
                    out.append("\\[")
                } else {
                    var body = pattern.substring(i + 1, j).replace("\\", "\\\\")
                    body = if (body.startsWith("!")) "^" + body.substring(1) else if (body.startsWith("^")) "\\" + body else body
                    out.append('[').append(body).append(']')
                    i = j
                }
            }
            else -> out.append(Regex.escape(c.toString()))
        }
        i++
    }
    return Regex(out.toString(), RegexOption.DOT_MATCHES_ALL)
}

/** Which paths inside a pair's folders are left out: unfinished downloads and the like, and what the pair names. */
class Excluder(patterns: List<String>) {
    private val names = ArrayList<Regex>()
    private val paths = ArrayList<Pair<Regex, String>>()

    init {
        for (raw in DEFAULT_EXCLUDE + patterns) {
            val p = raw.replace("\\", "/").trim().trim('/').lowercase()
            if (p.isEmpty()) continue
            if ("/" in p) paths.add(globRegex(p) to p) else names.add(globRegex(p))
        }
    }

    operator fun invoke(rel: String): Boolean {
        val r = rel.lowercase()
        val name = r.substringAfterLast('/')
        return names.any { it.matches(name) } || paths.any { (re, p) -> re.matches(r) || r.startsWith("$p/") }
    }
}

class LocalScan(val files: HashMap<String, Stamp>, val dirs: HashSet<String>)

fun stampOf(f: File) = Stamp(f.length(), f.lastModified() / 1000.0)

/** The files (with size and time) and the folders under `root`, as 'a/b.txt' paths. `skip`: a folder not looked into. */
fun scanLocal(root: File, excluded: Excluder, problems: MutableList<String>, skip: File? = null): LocalScan {
    val files = HashMap<String, Stamp>()
    val dirs = HashSet<String>()
    val todo = ArrayDeque<String>()
    todo.add("")
    val skipPath = skip?.absolutePath
    while (todo.isNotEmpty()) {
        val relDir = todo.removeLast()
        val dir = if (relDir.isEmpty()) root else File(root, relDir)
        val entries = dir.listFiles()
        if (entries == null) {
            problems.add("Can't read ${relDir.ifEmpty { root.path }}")
            continue
        }
        for (e in entries) {
            val rel = if (relDir.isEmpty()) e.name else "$relDir/${e.name}"
            if (excluded(rel) || Files.isSymbolicLink(e.toPath())) continue
            if (e.isDirectory) {
                if (skipPath != null && e.absolutePath == skipPath) continue
                dirs.add(rel)
                todo.add(rel)
            } else if (e.isFile) {
                files[rel] = stampOf(e)
            }
        }
    }
    return LocalScan(files, dirs)
}

/** A file on the drive, as the host listed it. */
class RemoteFile(val stamp: Stamp, val state: String)

class RemoteScan(val exists: Boolean, val isDir: Boolean, val files: HashMap<String, RemoteFile>, val dirs: HashSet<String>)

/** Everything under drive folder `root`, asked of the host in one go, without what the pair leaves out. */
fun scanRemote(client: HostClient, root: String, excluded: Excluder): RemoteScan {
    val tree = client.tree(root)
    val files = HashMap<String, RemoteFile>()
    val dirs = HashSet<String>()
    val skipped = HashSet<String>()
    if (!tree.optBoolean("dir")) return RemoteScan(tree.optBoolean("exists"), false, files, dirs)
    val ds = tree.optJSONArray("dirs") ?: JSONArray()
    for (i in 0 until ds.length()) {             // parents come before what is in them
        val rel = ds.getString(i)
        if (rel.substringBeforeLast('/', "") in skipped || excluded(rel)) skipped.add(rel) else dirs.add(rel)
    }
    val fs = tree.optJSONArray("files") ?: JSONArray()
    for (i in 0 until fs.length()) {
        val f = fs.getJSONArray(i)
        val rel = f.getString(0)
        if (rel.substringBeforeLast('/', "") !in skipped && !excluded(rel))
            files[rel] = RemoteFile(Stamp(f.getLong(1), f.getDouble(2)), f.optString(3, "synced"))
    }
    return RemoteScan(true, true, files, dirs)
}

fun conflictCopyName(rel: String, device: String, now: Long = System.currentTimeMillis()): String {
    val folder = rel.substringBeforeLast('/', "")
    val name = rel.substringAfterLast('/')
    val dot = name.lastIndexOf('.')
    val (stem, ext) = if (dot > 0) name.substring(0, dot) to name.substring(dot) else name to ""
    val t = Instant.ofEpochMilli(now).atZone(ZoneId.systemDefault())
    val stamp = "%04d-%02d-%02d %02d.%02d".format(t.year, t.monthValue, t.dayOfMonth, t.hour, t.minute)
    val new = "$stem (conflict $device $stamp)$ext"
    return if (folder.isEmpty()) new else "$folder/$new"
}

/** The next HH:MM after `after` (seconds since 1970), in this device's time zone. */
fun nextDaily(at: String, after: Double): Double {
    val parts = at.split(":")
    val time = LocalTime.of(parts.getOrNull(0)?.toIntOrNull()?.coerceIn(0, 23) ?: 3, parts.getOrNull(1)?.toIntOrNull()?.coerceIn(0, 59) ?: 0)
    val zone = ZoneId.systemDefault()
    val day = Instant.ofEpochMilli((after * 1000).toLong()).atZone(zone).toLocalDate()
    fun on(d: LocalDate) = d.atTime(time).atZone(zone).toEpochSecond().toDouble()
    return if (on(day) > after) on(day) else on(day.plusDays(1))
}

fun now() = System.currentTimeMillis() / 1000.0
