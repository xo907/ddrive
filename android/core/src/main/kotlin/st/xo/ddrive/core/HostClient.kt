package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** What the host answered when it wasn't "yes" (status 0: it couldn't be reached at all). */
class HostError(val status: Int, message: String) : Exception(message)

/** The copy was stopped half-way (Stop on the Sync page, or syncing was turned off). */
class Stopped : Exception("stopped")

/** Something went wrong with a file on this device while it was being sent or received. */
class LocalError(cause: IOException) : Exception(cause.message ?: cause.toString(), cause)

/**
 * The host's dashboard, as a program sees it: the same requests DDrive Lite makes on a computer
 * (lite.py), signed in with 24 words. Signs in again by itself when the session has run out.
 */
class HostClient(address: String, private val phrase: String, @Volatile var cookie: String = "", private val timeoutMs: Int = 60_000,
                 private val auth: String = "") {      // auth: what an end-to-end encrypted account signs in with (Vault.kt); its words stay here
    val address: String
    private val loginLock = Any()

    init {
        val text = address.trim().trimEnd('/')
        val url = try {
            URL(if ("://" in text) text else "http://$text")
        } catch (e: Exception) {
            null
        }
        if (url == null || url.protocol !in setOf("http", "https") || url.host.isNullOrEmpty())
            throw HostError(0, "'$text' is not an address like http://192.168.1.20:8765 or https://drive.example.com")
        this.address = "${url.protocol}://${url.authority}"
    }

    val secure get() = address.startsWith("https://")

    // ------------------------------------------------------------ plumbing
    private fun open(method: String, path: String, headers: Map<String, String>, session: String = cookie): HttpURLConnection {
        val conn = URL(address + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15_000
        conn.readTimeout = timeoutMs
        conn.instanceFollowRedirects = false
        conn.useCaches = false
        conn.setRequestProperty("Cookie", "dd_session=$session")
        if (method == "POST") conn.setRequestProperty("X-DD", "1")
        for ((k, v) in headers) conn.setRequestProperty(k, v)
        return conn
    }

    private fun fail(conn: HttpURLConnection, code: Int): Nothing {
        val text = try {
            conn.errorStream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
        } catch (e: IOException) {
            ""
        }
        val message = try {
            JSONObject(text).optString("error")
        } catch (e: Exception) {
            ""
        }
        throw HostError(code, message.ifEmpty { "The host answered with HTTP $code" })
    }

    /**
     * One request. `send` writes the body (called again when the request is made a second time
     * after signing in), `receive` reads the answer. Errors of the network become HostError(0).
     */
    private fun <T> exchange(method: String, path: String, headers: Map<String, String> = emptyMap(), length: Long = -1,
                             send: ((OutputStream) -> Unit)? = null, receive: (HttpURLConnection) -> T): T {
        for (attempt in 0..1) {
            val session = cookie
            val conn = try {
                open(method, path, headers, session)
            } catch (e: IOException) {
                throw HostError(0, "The host at $address can't be reached (${e.message})")
            }
            try {
                if (send != null) {
                    conn.doOutput = true
                    if (length >= 0) conn.setFixedLengthStreamingMode(length)
                    conn.outputStream.use(send)
                }
                val code = conn.responseCode
                if (code == 401 && attempt == 0 && (phrase.isNotEmpty() || auth.isNotEmpty())) {
                    conn.errorStream?.close()
                    login(stale = session)
                    continue
                }
                if (code >= 400) fail(conn, code)
                return receive(conn)
            } catch (e: Stopped) {
                conn.disconnect()
                throw e
            } catch (e: LocalError) {
                conn.disconnect()
                throw e
            } catch (e: IOException) {
                conn.disconnect()
                throw HostError(0, "The host at $address can't be reached (${e.message ?: e.javaClass.simpleName})")
            }
        }
        throw HostError(401, "The host doesn't take this sign-in any more")
    }

    fun login(stale: String? = null) {
        synchronized(loginLock) {
            if (stale != null && stale != cookie) return       // another thread signed in meanwhile
            val form = (if (auth.isNotEmpty()) "auth=$auth" else "phrase=" + URLEncoder.encode(phrase, "UTF-8")).toByteArray()
            val conn = try {
                open("POST", "/login", mapOf("Content-Type" to "application/x-www-form-urlencoded"))
            } catch (e: IOException) {
                throw HostError(0, "The host at $address can't be reached (${e.message})")
            }
            val code: Int
            var got = ""
            try {
                conn.doOutput = true
                conn.setFixedLengthStreamingMode(form.size)
                conn.outputStream.use { it.write(form) }
                code = conn.responseCode
                for ((k, values) in conn.headerFields) {
                    if (k != null && k.equals("Set-Cookie", ignoreCase = true)) {
                        for (v in values) for (part in v.split(";")) {
                            val p = part.trim()
                            if (p.startsWith("dd_session=")) got = p.substring("dd_session=".length)
                        }
                    }
                }
                (if (code >= 400) conn.errorStream else conn.inputStream)?.use { it.readBytes() }
            } catch (e: IOException) {
                conn.disconnect()
                throw HostError(0, "The host at $address can't be reached (${e.message ?: e.javaClass.simpleName})")
            }
            if (code == 303 && got.isNotEmpty()) {
                cookie = got
                return
            }
            throw when (code) {
                429 -> HostError(429, "Too many wrong tries from this device. Wait a few minutes and try again.")
                401 -> HostError(401, "The host doesn't know those 24 words. Use the drive's recovery phrase, or the phrase of your account on that dashboard.")
                421 -> HostError(421, "The host doesn't answer to that name. Use its number address, or add the name to \"Domain names\" in the host's Settings.")
                else -> HostError(code, "That address doesn't answer like a DDrive dashboard (HTTP $code)")
            }
        }
    }

    private fun body(conn: HttpURLConnection) = conn.inputStream.use { it.readBytes() }

    fun getJson(path: String): JSONObject =
        exchange("GET", path) { JSONObject(body(it).toString(Charsets.UTF_8).ifEmpty { "{}" }) }

    fun postJson(path: String, data: JSONObject): JSONObject {
        val bytes = data.toString().toByteArray()
        return exchange("POST", path, mapOf("Content-Type" to "application/json"), bytes.size.toLong(), { it.write(bytes) }) {
            JSONObject(body(it).toString(Charsets.UTF_8).ifEmpty { "{}" })
        }
    }

    /** Send bytes as they are (sealed things). */
    fun postBytes(path: String, bytes: ByteArray): JSONObject =
        exchange("POST", path, mapOf("Content-Type" to "application/octet-stream"), bytes.size.toLong(), { it.write(bytes) }) {
            JSONObject(body(it).toString(Charsets.UTF_8).ifEmpty { "{}" })
        }

    /** `length` bytes of what is at `path`, from `start`. */
    fun getRange(path: String, start: Long, length: Long): ByteArray =
        exchange("GET", path, mapOf("Range" to "bytes=$start-${start + length - 1}")) { body(it) }

    private fun q(vararg params: Pair<String, String>) =
        "?" + params.joinToString("&") { (k, v) -> k + "=" + URLEncoder.encode(v, "UTF-8") }

    // ------------------------------------------------------------ the drive
    fun status(): JSONObject = getJson("/api/status")

    fun list(path: String): JSONArray = getJson("/api/list" + q("path" to path)).getJSONArray("items")

    /** Everything under a folder of the drive: {exists, dir, files: [[path in it, size, time, state]], dirs}. */
    fun tree(path: String): JSONObject = getJson("/api/sync/tree" + q("path" to path))

    /** A whole file into `out`. `stop` returning true gives up; `progress` hears how many bytes came. */
    fun download(path: String, out: OutputStream, stop: (() -> Boolean)? = null): Long =
        exchange("GET", "/api/file" + q("path" to path, "dl" to "1")) { conn ->
            var done = 0L
            conn.inputStream.use { input ->
                val buf = ByteArray(256 * 1024)
                while (true) {
                    if (stop != null && stop()) throw Stopped()
                    val n = input.read(buf)
                    if (n < 0) break
                    try {
                        out.write(buf, 0, n)
                    } catch (e: IOException) {
                        throw LocalError(e)
                    }
                    done += n
                }
            }
            done
        }

    /**
     * Send a file of exactly `size` bytes. mtime: the time the file has here, which the host gives
     * its copy (a folder sync tells copies apart by size and time). `source` opens the file (again,
     * when the request has to be made twice).
     */
    fun upload(path: String, size: Long, mtime: Double?, stop: (() -> Boolean)? = null, source: () -> InputStream): JSONObject {
        val query = if (mtime == null) q("path" to path) else q("path" to path, "mtime" to mtime.toString())
        return exchange("POST", "/api/upload$query", mapOf("Content-Type" to "application/octet-stream"), size, { out ->
            val input = try {
                source()
            } catch (e: IOException) {
                throw LocalError(e)
            }
            input.use {
                val buf = ByteArray(256 * 1024)
                var left = size
                while (left > 0) {
                    if (stop != null && stop()) throw Stopped()
                    val n = try {
                        it.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                    } catch (e: IOException) {
                        throw LocalError(e)
                    }
                    if (n < 0) throw LocalError(IOException("it changed while it was being copied; trying again next time"))
                    out.write(buf, 0, n)
                    left -= n
                }
            }
        }) { JSONObject(body(it).toString(Charsets.UTF_8).ifEmpty { "{}" }) }
    }

    /**
     * Send a file a part at a time (a host from 2.4.0 on). Each part is a short request, so a reverse
     * proxy in front of the host or a connection that drops doesn't stand in the way of a big file:
     * it goes on where it stopped, and a host that has to catch up first says so instead of leaving
     * us waiting. onPart hears how many more bytes are there, onNote what it waits for ("" = nothing).
     */
    fun uploadParts(path: String, file: File, size: Long, mtime: Double?, stop: (() -> Boolean)? = null, part: Int = PART,
                    onPart: (Long) -> Unit = {}, onNote: (String) -> Unit = {}): JSONObject {
        val args = ArrayList<Pair<String, String>>()
        args.add("path" to path)
        args.add("size" to size.toString())
        if (mtime != null) args.add("mtime" to mtime.toString())
        var offset = 0L
        if (size > part) {                       // some of it may be there already
            offset = getJson("/api/upload/part" + q(*args.toTypedArray())).optLong("offset")
            if (offset < 0 || offset > size) offset = 0
            if (offset > 0) onPart(offset)
        }
        val buf = ByteArray(part)
        while (true) {
            if (stop != null && stop()) throw Stopped()
            val want = minOf(part.toLong(), size - offset).toInt()
            try {
                java.io.RandomAccessFile(file, "r").use { f ->
                    if (f.length() < offset + want) throw IOException("it changed while it was being copied; trying again next time")
                    f.seek(offset)
                    f.readFully(buf, 0, want)
                }
            } catch (e: IOException) {
                throw LocalError(e)
            }
            val query = q(*(args + ("offset" to offset.toString())).toTypedArray())
            val got = exchange("POST", "/api/upload/part$query", mapOf("Content-Type" to "application/octet-stream"), want.toLong(),
                               { it.write(buf, 0, want) }) { JSONObject(body(it).toString(Charsets.UTF_8).ifEmpty { "{}" }) }
            if (got.has("wait")) {
                onNote("Waiting for the host: " + got.optString("wait"))
                for (i in 0 until 15) {
                    if (stop != null && stop()) throw Stopped()
                    Thread.sleep(1000)
                }
            } else if (got.optBoolean("ok")) {
                onNote("")
                onPart(want.toLong())
                if (got.optBoolean("done")) return got
            }
            offset = got.optLong("offset").coerceIn(0, size)        // where the host is
        }
    }

    fun mkdir(path: String) {
        postJson("/api/mkdir", JSONObject().put("path", path))
    }

    fun move(from: String, to: String) {
        postJson("/api/move", JSONObject().put("from", from).put("to", to))
    }

    fun delete(path: String) {
        postJson("/api/delete", JSONObject().put("path", path))
    }

    /** Folder syncing on this device, as said to the host (/api/lite/sync/...). */
    fun syncCall(what: String, data: JSONObject): JSONObject = postJson("/api/lite/sync/$what", data)

    companion object {
        const val PART = 8 * 1024 * 1024         // files go to the host in parts of this size
    }
}
