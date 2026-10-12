package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.io.OutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/*
 * End-to-end encrypted accounts on this device: the same keys and formats as ddrive/vault.py, which
 * describes them (and as the browser's vaultcore.js). The 24 words are turned into keys here and
 * stay here. The host gets a sign-in key that can't be turned back into them, and after that only
 * sealed things: files under random names, and a list of files it can't read.
 */

/** Something sealed could not be opened, or a file could not be sealed. */
class VaultError(message: String) : IOException(message)

object Seal {
    private val random = SecureRandom()
    const val CHUNK_LOG = 20
    const val CHUNK = 1 shl CHUNK_LOG            // a file is sealed in pieces of 1 MiB
    const val OVERHEAD = 28                      // nonce + tag of a sealed thing
    const val HEAD = 5                           // "DDV1" + the piece size
    val AAD_OP = "ddv1 op".toByteArray()
    val AAD_SYNC = "ddv1 sync".toByteArray()
    private val AAD_CHUNK = "ddv1 chunk".toByteArray()

    private fun hmac(key: ByteArray, data: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(data)

    /** HKDF-SHA256 with no salt, 32 bytes. */
    fun hkdf(key: ByteArray, info: String): ByteArray = hmac(hmac(ByteArray(32), key), info.toByteArray() + byteArrayOf(1))

    /** What an end-to-end account signs in with: made from its key, but no way back to it. */
    fun authKey(key: ByteArray) = hkdf(key, "ddrive vault v1 auth")

    fun metaKey(key: ByteArray) = hkdf(key, "ddrive vault v1 meta")

    fun randomBytes(n: Int) = ByteArray(n).also { random.nextBytes(it) }

    fun newId() = randomBytes(16).joinToString("") { "%02x".format(it) }

    /** nonce + tag + ciphertext */
    fun seal(key: ByteArray, data: ByteArray, label: ByteArray, offset: Int = 0, length: Int = data.size): ByteArray {
        val nonce = randomBytes(12)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        c.updateAAD(label)
        val out = c.doFinal(data, offset, length)            // ciphertext + tag
        val blob = ByteArray(OVERHEAD + length)
        System.arraycopy(nonce, 0, blob, 0, 12)
        System.arraycopy(out, length, blob, 12, 16)
        System.arraycopy(out, 0, blob, 28, length)
        return blob
    }

    fun unseal(key: ByteArray, blob: ByteArray, label: ByteArray, offset: Int = 0, length: Int = blob.size - offset): ByteArray {
        val n = length - OVERHEAD
        if (n < 0) throw VaultError("This is cut short.")
        val data = ByteArray(n + 16)
        System.arraycopy(blob, offset + 28, data, 0, n)
        System.arraycopy(blob, offset + 12, data, n, 16)
        try {
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, blob, offset, 12))
            c.updateAAD(label)
            return c.doFinal(data)
        } catch (e: java.security.GeneralSecurityException) {
            throw VaultError("This can't be opened with these 24 words (or it was changed on the way).")
        }
    }

    fun chunkLabel(n: Long, last: Boolean): ByteArray =
        AAD_CHUNK + ByteBuffer.allocate(8).putLong(n).array() + byteArrayOf(if (last) 1 else 0)

    fun chunks(size: Long): Long = maxOf(1L, (size + CHUNK - 1) / CHUNK)

    fun sealedSize(size: Long): Long = HEAD + size + OVERHEAD * chunks(size)
}

/** The 24 words: 256 bits written the BIP39 way (as ddrive/words.py). */
object Words {
    private val list: List<String> by lazy {
        val text = Words::class.java.getResourceAsStream("wordlist.txt")?.use { it.readBytes().toString(Charsets.UTF_8) }
            ?: throw IllegalStateException("the word list is missing")
        text.split(Regex("\\s+")).filter { it.isNotEmpty() }.also { check(it.size == 2048) { "the word list is damaged" } }
    }

    /** The key a phrase stands for. Throws IllegalArgumentException that says what is wrong with it. */
    fun phraseToKey(phrase: String): ByteArray {
        val given = Regex("[a-z]+").findAll(phrase.lowercase()).map { it.value }.toList()
        require(given.size == 24) { "a recovery phrase has 24 words; this has ${given.size}" }
        val index = HashMap<String, Int>()
        list.forEachIndexed { i, w -> index[w] = i }
        var bits = java.math.BigInteger.ZERO
        given.forEachIndexed { n, w ->
            val i = index[w] ?: run {
                val close = list.firstOrNull { it.take(4) == w.take(4) }
                throw IllegalArgumentException("word ${n + 1}, '$w', is not one of the words used" + (if (close != null) " (did you mean '$close'?)" else ""))
            }
            bits = bits.shiftLeft(11).or(java.math.BigInteger.valueOf(i.toLong()))
        }
        val raw = bits.shiftRight(8).toByteArray()
        val key = ByteArray(32)
        val from = maxOf(0, raw.size - 32)
        System.arraycopy(raw, from, key, 32 - (raw.size - from), raw.size - from)
        val sum = MessageDigest.getInstance("SHA-256").digest(key)[0].toInt() and 0xFF
        require(sum == bits.and(java.math.BigInteger.valueOf(0xFF)).toInt()) { "the words don't fit together: one is wrong or out of order" }
        return key
    }

    fun keyToPhrase(key: ByteArray): String {
        val sum = MessageDigest.getInstance("SHA-256").digest(key)[0].toInt() and 0xFF
        val bits = java.math.BigInteger(1, key).shiftLeft(8).or(java.math.BigInteger.valueOf(sum.toLong()))
        return (23 downTo 0).joinToString(" ") { list[bits.shiftRight(11 * it).and(java.math.BigInteger.valueOf(0x7FF)).toInt()] }
    }
}

/**
 * An end-to-end account as this device uses it: the list of files, kept here in the clear and on
 * the host sealed, and files going there and back sealed. `client` is signed in with the account's
 * auth key (HostClient(address, "", auth = hex of Seal.authKey(key))).
 */
class Vault(val client: HostClient, key: ByteArray) {
    val meta: ByteArray = Seal.metaKey(key)
    private val nodes = HashMap<String, JSONObject>()        // id -> {id, parent, name, dir, size, mtime, obj, key}
    private var kids: HashMap<String, HashMap<String, JSONObject>>? = null
    private var seq = 0L
    private var since = 0
    private val lock = Any()

    // ------------------------------------------------------------ the list of files
    /** Bring the list up to date with what the host has. */
    fun sync() = synchronized(lock) {
        while (true) {
            val got = client.getJson("/api/vault/log?after=$seq")
            val snap = got.optJSONObject("snapshot")
            if (snap != null && snap.getLong("seq") > seq) {      // behind the last sealed whole list: start from it
                val raw = ByteArrayOutputStream()
                readObject(meta, snap.getString("object"), snap.getLong("size"), raw, null)
                val state = JSONObject(raw.toString("UTF-8")).getJSONObject("nodes")
                nodes.clear()
                for (id in state.keys()) nodes[id] = state.getJSONObject(id)
                seq = snap.getLong("seq")
                since = 0
                kids = null
                continue
            }
            val batches = got.getJSONArray("batches")
            for (i in 0 until batches.length()) {
                val b = batches.getJSONArray(i)
                val batch = Base64.getDecoder().decode(b.getString(1))
                var pos = 0
                while (pos + 4 <= batch.size) {
                    val n = ByteBuffer.wrap(batch, pos, 4).int
                    apply(JSONObject(String(Seal.unseal(meta, batch, Seal.AAD_OP, pos + 4, n), Charsets.UTF_8)))
                    since++
                    pos += 4 + n
                }
                seq = b.getLong(0)
            }
            if (!got.optBoolean("more")) break
        }
    }

    private fun apply(op: JSONObject) {
        kids = null
        when (op.optString("op")) {
            "put" -> {
                val n = JSONObject()
                for (k in listOf("id", "parent", "name", "dir", "size", "mtime", "obj", "key", "thumb")) if (op.has(k)) n.put(k, op.get(k))
                nodes[op.getString("id")] = n
            }
            "move" -> nodes[op.getString("id")]?.put("parent", op.getString("parent"))?.put("name", op.getString("name"))
            "del" -> for (id in under(op.getString("id"))) nodes.remove(id)
        }
    }

    private fun under(id: String): List<String> {
        val out = ArrayList<String>()
        val todo = ArrayDeque(listOf(id))
        while (todo.isNotEmpty()) {
            val cur = todo.removeLast()
            if (cur !in nodes) continue
            out.add(cur)
            for (n in nodes.values) if (n.optString("parent") == cur) todo.add(n.getString("id"))
        }
        return out
    }

    private fun send(vararg ops: JSONObject) = synchronized(lock) {
        val batch = ByteArrayOutputStream()
        for (op in ops) {
            val blob = Seal.seal(meta, op.toString().toByteArray(), Seal.AAD_OP)
            batch.write(ByteBuffer.allocate(4).putInt(blob.size).array())
            batch.write(blob)
        }
        client.postBytes("/api/vault/log", batch.toByteArray())
        sync()
        if (since >= SNAPSHOT_EVERY) try {
            snapshot()
        } catch (e: IOException) {
            // another time
        } catch (e: HostError) {
            // another time
        }
    }

    private fun snapshot() {
        val all = JSONObject()
        for ((id, n) in nodes) all.put(id, n)
        val state = JSONObject().put("nodes", all).toString().toByteArray()
        val id = Seal.newId()
        store(id, meta, state.size.toLong(), null, {}, {}) { offset, buf, want -> System.arraycopy(state, offset.toInt(), buf, 0, want) }
        client.postJson("/api/vault/snapshot", JSONObject().put("seq", seq).put("object", id).put("size", state.size))
        since = 0
    }

    // ------------------------------------------------------------ finding things
    private fun byFolder(): HashMap<String, HashMap<String, JSONObject>> {
        kids?.let { return it }
        val made = HashMap<String, HashMap<String, JSONObject>>()
        for (n in nodes.values) made.getOrPut(n.optString("parent")) { HashMap() }[n.optString("name")] = n
        kids = made
        return made
    }

    fun children(id: String): List<JSONObject> = synchronized(lock) { byFolder()[id]?.values?.toList() ?: emptyList() }

    /** The node at a path ("/" or "" is the top folder, whose id is ""), or null. */
    fun resolve(path: String): JSONObject? = synchronized(lock) {
        var node = JSONObject().put("id", "").put("dir", true).put("name", "")
        val by = byFolder()
        for (name in path.trim('/').split('/').filter { it.isNotEmpty() }) {
            node = by[node.getString("id")]?.get(name) ?: return null
        }
        node
    }

    val fileCount: Int get() = synchronized(lock) { nodes.values.count { !it.optBoolean("dir") } }
    val bytes: Long get() = synchronized(lock) { nodes.values.sumOf { if (it.optBoolean("dir")) 0L else it.optLong("size") } }

    // ------------------------------------------------------------ changing things
    /** Have a folder (and the ones above it) exist. */
    fun mkdirs(path: String) {
        var made = ""
        for (part in path.trim('/').split('/').filter { it.isNotEmpty() }) {
            val parent = resolve(made) ?: throw VaultError("Not found: $made")
            made += "/$part"
            val n = resolve(made)
            if (n == null) {
                send(JSONObject().put("op", "put").put("id", Seal.newId()).put("parent", parent.getString("id")).put("name", part)
                    .put("dir", true).put("size", 0).put("mtime", now()))
            } else if (!n.optBoolean("dir")) throw VaultError("$made in your account is a file")
        }
    }

    fun move(from: String, to: String) {
        val node = resolve(from) ?: throw HostError(404, "Not found: $from")
        val up = resolve(to.substringBeforeLast('/', "")) ?: throw HostError(404, "Not found: ${to.substringBeforeLast('/', "")}")
        if (resolve(to) != null) throw HostError(409, "Something with that name already exists")
        send(JSONObject().put("op", "move").put("id", node.getString("id")).put("parent", up.getString("id")).put("name", to.substringAfterLast('/')))
    }

    /** Remove a file or a folder (with what is in it). */
    fun delete(path: String) {
        val node = resolve(path) ?: throw HostError(404, "Not found: $path")
        val objects = JSONArray()
        synchronized(lock) { for (id in under(node.getString("id"))) nodes[id]?.optString("obj")?.takeIf { it.isNotEmpty() && it != "null" }?.let { objects.put(it) } }
        send(JSONObject().put("op", "del").put("id", node.getString("id")))
        if (objects.length() > 0) client.postJson("/api/vault/delete", JSONObject().put("ids", objects))
    }

    // ------------------------------------------------------------ files
    /** Seal `size` bytes (read(offset, buffer, n) fills the buffer) and send them to the host as object `id`. */
    private fun store(id: String, key: ByteArray, size: Long, stop: (() -> Boolean)?, onPart: (Long) -> Unit, onNote: (String) -> Unit,
                      read: (Long, ByteArray, Int) -> Unit) {
        val count = Seal.chunks(size)
        val total = Seal.sealedSize(size)
        var offset = 0L
        var part = ByteArrayOutputStream()
        part.write("DDV1".toByteArray())
        part.write(Seal.CHUNK_LOG)
        val buf = ByteArray(Seal.CHUNK)
        var sent = 0L
        fun post(done: Long) {
            val bytes = part.toByteArray()
            part = ByteArrayOutputStream()
            while (true) {
                if (stop != null && stop()) throw Stopped()
                val got = client.postBytes("/api/vault/object?id=$id&size=$total&offset=$offset", bytes)
                if (got.has("wait")) {
                    onNote("Waiting for the host: " + got.optString("wait"))
                    for (i in 0 until 15) {
                        if (stop != null && stop()) throw Stopped()
                        Thread.sleep(1000)
                    }
                    continue
                }
                if (!got.optBoolean("ok")) throw VaultError("The host lost its place in this file; it is sent again next time.")
                offset = got.getLong("offset")
                onNote("")
                onPart(done - sent)
                sent = done
                return
            }
        }
        for (n in 0 until count) {
            if (stop != null && stop()) throw Stopped()
            val want = minOf(Seal.CHUNK.toLong(), size - n * Seal.CHUNK).toInt()
            if (want > 0) read(n * Seal.CHUNK, buf, want)
            part.write(Seal.seal(key, buf, Seal.chunkLabel(n, n == count - 1), 0, want))
            if ((n + 1) % PART_CHUNKS == 0L || n == count - 1) post(minOf(size, (n + 1) * Seal.CHUNK))
        }
    }

    /** Store a file of this device at a path of the account, replacing what was there. */
    fun put(path: String, file: File, size: Long, mtime: Double, stop: (() -> Boolean)? = null, onPart: (Long) -> Unit = {}, onNote: (String) -> Unit = {}) {
        val up = resolve(path.substringBeforeLast('/', "")) ?: throw HostError(404, "Not found: ${path.substringBeforeLast('/', "")}")
        val old = resolve(path)
        if (old != null && old.optBoolean("dir")) throw VaultError("$path is a folder in your account")
        val key = Seal.randomBytes(32)
        val obj = Seal.newId()
        try {
            RandomAccessFile(file, "r").use { f ->
                store(obj, key, size, stop, onPart, onNote) { offset, buf, want ->
                    if (f.length() < offset + want) throw LocalError(IOException("it changed while it was being copied; trying again next time"))
                    f.seek(offset)
                    f.readFully(buf, 0, want)
                }
            }
        } catch (e: java.io.FileNotFoundException) {
            throw LocalError(e)
        }
        send(JSONObject().put("op", "put").put("id", old?.getString("id") ?: Seal.newId()).put("parent", up.getString("id"))
            .put("name", path.substringAfterLast('/')).put("dir", false).put("size", size).put("mtime", mtime)
            .put("obj", obj).put("key", Base64.getEncoder().encodeToString(key)))
        val before = old?.optString("obj").orEmpty()
        if (before.isNotEmpty() && before != "null") try {
            client.postJson("/api/vault/delete", JSONObject().put("ids", JSONArray().put(before)))
        } catch (e: HostError) {
            // it is only room that is not given back
        }
    }

    private fun readObject(key: ByteArray, obj: String, size: Long, out: OutputStream, stop: (() -> Boolean)?): Long {
        val count = Seal.chunks(size)
        var n = 0L
        var done = 0L
        while (n < count) {
            if (stop != null && stop()) throw Stopped()
            val until = minOf(count, n + PART_CHUNKS)
            var bytes = 0L
            for (i in n until until) bytes += minOf(Seal.CHUNK.toLong(), size - i * Seal.CHUNK) + Seal.OVERHEAD
            val raw = client.getRange("/api/vault/object?id=$obj", Seal.HEAD + n * (Seal.CHUNK + Seal.OVERHEAD), bytes)
            if (raw.size.toLong() != bytes) throw VaultError("The host gave back less than the file; trying again next time.")
            var pos = 0
            for (i in n until until) {
                val want = (minOf(Seal.CHUNK.toLong(), size - i * Seal.CHUNK) + Seal.OVERHEAD).toInt()
                val piece = Seal.unseal(key, raw, Seal.chunkLabel(i, i == count - 1), pos, want)
                try {
                    out.write(piece)
                } catch (e: IOException) {
                    throw LocalError(e)
                }
                done += piece.size
                pos += want
            }
            n = until
        }
        return done
    }

    /** A file's content, opened, into `out`. Returns how many bytes that was. */
    fun read(node: JSONObject, out: OutputStream, stop: (() -> Boolean)? = null): Long =
        readObject(Base64.getDecoder().decode(node.getString("key")), node.getString("obj"), node.getLong("size"), out, stop)

    /** The ids of the objects the host has but hasn't stored on Discord yet. */
    fun pending(): Set<String> {
        val ids = client.getJson("/api/vault/pending").getJSONArray("ids")
        return (0 until ids.length()).map { ids.getString(it) }.toSet()
    }

    // ------------------------------------------------------------ what is said about folder syncing, sealed
    fun sealMessage(json: String): String = Base64.getEncoder().encodeToString(Seal.seal(meta, json.toByteArray(), Seal.AAD_SYNC))

    fun openMessage(text: String): String = String(Seal.unseal(meta, Base64.getDecoder().decode(text), Seal.AAD_SYNC), Charsets.UTF_8)

    private companion object {
        const val PART_CHUNKS = 8L               // sealed pieces go to the host this many at a time
        const val SNAPSHOT_EVERY = 400
    }
}
