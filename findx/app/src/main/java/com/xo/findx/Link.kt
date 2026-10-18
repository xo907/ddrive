package com.xo.findx

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import st.xo.ddrive.core.HostClient
import st.xo.ddrive.core.HostError
import st.xo.ddrive.core.Seal
import st.xo.ddrive.core.Vault
import st.xo.ddrive.core.Words
import java.io.ByteArrayOutputStream
import java.net.URLEncoder

/**
 * FindX's line to your DDrive. Two parts:
 *
 * - **Files** (any DDrive): history, and a card for each device so your browser knows them, kept in
 *   /FindX as sealed files through DDrive's own file API. With an end-to-end encrypted account they
 *   are sealed a second time, as everything of that account is.
 * - **The live relay** (a DDrive with FindX on, /api/findx/...): the latest report of each device,
 *   commands to them, and shared links. The host only passes sealed things along and keeps the last
 *   few in memory; see PROTOCOL.md. A host without it answers 404, and FindX then does without live
 *   features (history and nearby finding still work).
 */
class Link private constructor(val client: HostClient, val e2e: Boolean, private val wordsKey: ByteArray) {
    private val vault: Vault? = if (e2e) Vault(client, wordsKey).apply { name = "FindX" } else null

    // ------------------------------------------------------------ files

    fun putFile(path: String, bytes: ByteArray) {
        Meter.wake()
        val folder = path.substringBeforeLast('/')
        mkdirs(folder)
        if (vault != null) {
            vault.sync()
            vault.put(path, bytes.size.toLong(), System.currentTimeMillis() / 1000.0) { offset, buf, n -> System.arraycopy(bytes, offset.toInt(), buf, 0, n) }
        } else {
            client.uploadParts(path, bytes.size.toLong(), null) { offset, buf, n -> System.arraycopy(bytes, offset.toInt(), buf, 0, n) }
        }
    }

    fun getFile(path: String): ByteArray? {
        Meter.wake()
        val out = ByteArrayOutputStream()
        if (vault != null) {
            vault.sync()
            val node = vault.resolve(path) ?: return null
            vault.read(node, out)
        } else {
            try {
                client.download(path, out)
            } catch (e: HostError) {
                if (e.status == 404) return null
                throw e
            }
        }
        return out.toByteArray()
    }

    private val made = HashSet<String>()

    private fun mkdirs(path: String) {
        if (path in made) return
        if (vault != null) {
            vault.sync(); vault.mkdirs(path)
        } else {
            var at = ""
            for (part in path.trim('/').split('/').filter { it.isNotEmpty() }) {
                val parent = at.ifEmpty { "/" }
                at += "/$part"
                val there = try {
                    val items = client.list(parent)
                    (0 until items.length()).map { items.getJSONObject(it) }.firstOrNull { it.optString("name") == part }
                } catch (e: HostError) {
                    if (e.status == 404) null else throw e
                }
                if (there == null) client.mkdir(at)
            }
        }
        made.add(path)
    }

    // ------------------------------------------------------------ the live relay

    private fun q(vararg p: Pair<String, String>) = "?" + p.joinToString("&") { (k, v) -> k + "=" + URLEncoder.encode(v, "UTF-8") }

    /** Calls the relay; a host without it (404) gets [NoRelay]. */
    private fun <T> relay(call: () -> T): T = try {
        Meter.wake()
        call()
    } catch (e: HostError) {
        if (e.status == 404) throw NoRelay() else throw e
    }

    class NoRelay : Exception("This DDrive doesn't have FindX's live relay yet.")

    /** The newest FindX that is published, as your DDrive knows it (DDrive 3.11+; it asks GitHub, this phone
     *  asks nobody else): (version, where to download it), or null. */
    fun newestFindX(): Pair<String, String>? {
        val o = client.getJson("/api/status")
        val u = o.optJSONObject("update") ?: o.optJSONObject("apps") ?: return null
        val v = u.optString("findx").takeIf { it.isNotEmpty() && it != "null" } ?: return null
        return v to u.optString("findx_apk")
    }

    /** What the relay said back to a report: whether anyone is watching now (so the phone sends more often). */
    class Heard(val watching: Boolean, val shareWatching: Set<String>)

    fun hello(group: String, device: String, card: ByteArray) = relay {
        client.postJson("/api/findx/hello", JSONObject().put("group", group).put("device", device).put("card", FxKeys.b64(card)))
    }

    fun report(group: String, device: String, blob: ByteArray, shares: Map<String, ByteArray>): Heard = relay {
        val o = client.postJson("/api/findx/report", JSONObject().put("group", group).put("device", device).put("blob", FxKeys.b64(blob))
            .put("shares", JSONObject().apply { shares.forEach { (id, b) -> put(id, FxKeys.b64(b)) } }))
        val sw = o.optJSONArray("shareWatching") ?: JSONArray()
        Heard(o.optBoolean("watching"), (0 until sw.length()).map { sw.getString(it) }.toSet())
    }

    /** Waits (up to [waitS] seconds, held open by the host) for commands after [after]. */
    /** Commands, whether anybody is watching, and whether the relay wants this phone's hello again (it was emptied). */
    class Waited(val commands: List<Pair<Long, ByteArray>>, val watching: Boolean, val hello: Boolean)

    fun waitCommands(group: String, device: String, after: Long, waitS: Int): Waited = relay {
        val o = client.getJson("/api/findx/wait" + q("group" to group, "device" to device, "after" to after.toString(), "wait" to waitS.toString()))
        val a = o.optJSONArray("commands") ?: JSONArray()
        Waited((0 until a.length()).map { a.getJSONObject(it).let { c -> c.getLong("seq") to FxKeys.unb64(c.getString("blob")) } },
               o.optBoolean("watching"), o.optBoolean("hello"))
    }

    fun command(group: String, to: String, blob: ByteArray) = relay {
        client.postJson("/api/findx/command", JSONObject().put("group", group).put("to", to).put("blob", FxKeys.b64(blob)))
    }

    /** Your devices as the relay knows them: (device, sealed card, sealed latest report or null, when). */
    fun devices(group: String): List<Triple<String, ByteArray, Pair<ByteArray?, Long>>> = relay {
        val a = client.getJson("/api/findx/devices" + q("group" to group)).optJSONArray("devices") ?: JSONArray()
        (0 until a.length()).map { i ->
            val o = a.getJSONObject(i)
            Triple(o.getString("device"), FxKeys.unb64(o.getString("card")),
                (o.optString("report").takeIf { it.isNotEmpty() }?.let { FxKeys.unb64(it) }) to o.optLong("at"))
        }
    }

    fun share(group: String, device: String, share: String, until: Long) = relay {
        client.postJson("/api/findx/share", JSONObject().put("group", group).put("device", device).put("share", share).put("until", until))
    }

    fun unshare(group: String, share: String) = relay {
        client.postJson("/api/findx/unshare", JSONObject().put("group", group).put("share", share))
    }

    companion object {
        @Volatile private var cached: Pair<String, Link>? = null

        /** Signs in with 24 words the way DDrive Lite does: first as an end-to-end account, which sends no words. */
        fun connect(address: String, words: String): Triple<Link, String, String> {
            val phrase = words.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            val key = try {
                Words.phraseToKey(phrase)
            } catch (e: IllegalArgumentException) {
                throw HostError(0, "Those words aren't right: ${e.message}.")
            }
            var e2e = true
            var client = HostClient(address, "", auth = FxKeys.hex(Seal.authKey(key)))
            try {
                client.login()
            } catch (e: HostError) {
                if (e.status != 401) throw e
                e2e = false                              // the drive's owner, or an older account: the words are sent
                client = HostClient(address, phrase)
                client.login()
            }
            val st = client.status()
            val user = if (st.optString("role", "owner") == "owner") "the owner" else "account ${st.optString("account")}"
            return Triple(Link(client, e2e, key), phrase, user)
        }

        /** The saved link, or null when FindX isn't linked to a DDrive. */
        fun of(ctx: Context): Link? {
            val words = Store.words(ctx) ?: return null
            val host = Store.host(ctx).ifEmpty { return null }
            val id = "$host|${words.hashCode()}|${Store.e2e(ctx)}"
            cached?.let { if (it.first == id) return it.second }
            val key = Words.phraseToKey(words)
            val client = if (Store.e2e(ctx)) HostClient(host, "", auth = FxKeys.hex(Seal.authKey(key)), timeoutMs = 300_000)
                         else HostClient(host, words, timeoutMs = 300_000)
            return Link(client, Store.e2e(ctx), key).also { cached = id to it }
        }

        fun forget() { cached = null }
    }
}
