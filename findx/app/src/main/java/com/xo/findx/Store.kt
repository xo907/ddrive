package com.xo.findx

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONArray
import org.json.JSONObject
import st.xo.ddrive.core.Words
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/*
 * What FindX keeps on the phone. Everything that says where you were, or that could open it (your
 * words, history, spots, shares), is encrypted with a key held in the phone's security hardware, so a
 * copy of the app's storage is no use anywhere else. Android's backup is off (no_backup.xml).
 */
object Store {
    const val ROOT = "/FindX"
    private const val ALIAS = "findx-local"

    fun prefs(ctx: Context) = ctx.getSharedPreferences("fx", Context.MODE_PRIVATE)

    // ------------------------------------------------------------ the hardware key

    private fun hardwareKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        val spec = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256)
        try {
            if (Build.VERSION.SDK_INT >= 28) spec.setIsStrongBoxBacked(true)
            gen.init(spec.build())
            return gen.generateKey()
        } catch (e: Exception) {                         // no StrongBox on this phone: the ordinary secure hardware
            spec.setIsStrongBoxBacked(false)
            gen.init(spec.build())
            return gen.generateKey()
        }
    }

    @Volatile private var key: SecretKey? = null
    private fun hk() = key ?: synchronized(this) { key ?: hardwareKey().also { key = it } }

    fun lock(data: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, hk())
        return c.iv + c.doFinal(data)
    }

    fun unlock(blob: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, hk(), GCMParameterSpec(128, blob, 0, 12))
        return c.doFinal(blob, 12, blob.size - 12)
    }

    fun lockText(s: String) = FxKeys.b64(lock(s.toByteArray()))
    fun unlockText(s: String) = String(unlock(FxKeys.unb64(s)), Charsets.UTF_8)

    private fun writeLocked(f: File, data: ByteArray) {
        f.parentFile?.mkdirs()
        val tmp = File(f.path + ".tmp")
        tmp.writeBytes(lock(data))
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
    }

    private fun readLocked(f: File): ByteArray? = if (!f.exists()) null else runCatching { unlock(f.readBytes()) }.getOrNull()

    // ------------------------------------------------------------ this device

    fun device(ctx: Context): String = prefs(ctx).getString("device", null) ?: FxKeys.hex(FxKeys.bytes(16)).also {
        prefs(ctx).edit().putString("device", it).apply()
    }

    fun deviceName(ctx: Context): String = prefs(ctx).getString("name", null)
        ?: (Build.MODEL ?: "This phone").let { m -> if (m.startsWith(Build.MANUFACTURER ?: "", true)) m else "${Build.MANUFACTURER} $m" }

    fun setDeviceName(ctx: Context, name: String) = prefs(ctx).edit().putString("name", name.trim().take(40)).apply()

    fun setUp(ctx: Context) = prefs(ctx).getBoolean("setUp", false)
    fun markSetUp(ctx: Context) = prefs(ctx).edit().putBoolean("setUp", true).apply()

    /** Whether FindX looks after this phone at all (off only by your choice, confirmed with your fingerprint or PIN). */
    fun on(ctx: Context) = prefs(ctx).getBoolean("on", true)
    fun setOn(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("on", on).apply()

    /** Your other devices can find this one over Bluetooth, even with no internet. */
    fun beaconOn(ctx: Context) = prefs(ctx).getBoolean("beacon", true)
    fun setBeaconOn(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("beacon", on).apply()

    /** History detail: 0 battery saver, 1 balanced, 2 detailed. */
    fun detail(ctx: Context) = prefs(ctx).getInt("detail", 1)
    fun setDetail(ctx: Context, d: Int) = prefs(ctx).edit().putInt("detail", d.coerceIn(0, 2)).apply()

    fun historyOn(ctx: Context) = prefs(ctx).getBoolean("history", true)
    fun setHistoryOn(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("history", on).apply()

    /** Screenshots of FindX, and its preview in Recents: blocked unless you allow them. */
    fun screenshots(ctx: Context) = prefs(ctx).getBoolean("screenshots", false)
    fun setScreenshots(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("screenshots", on).apply()

    /** This phone in all its circles: a random id, so a circle's drive can't tell it is any of your devices. */
    fun circleMember(ctx: Context): String = prefs(ctx).getString("circleMember", null) ?: FxKeys.hex(FxKeys.bytes(16)).also {
        prefs(ctx).edit().putString("circleMember", it).apply()
    }

    /** The name the people in your circles see (it's sealed: only they read it). */
    fun myName(ctx: Context): String = prefs(ctx).getString("myName", null)?.ifEmpty { null } ?: deviceName(ctx)
    fun setMyName(ctx: Context, name: String) = prefs(ctx).edit().putString("myName", name.trim().take(30)).apply()

    /** Appearance: 0 follows the phone, 1 light, 2 dark. */
    fun theme(ctx: Context) = prefs(ctx).getInt("theme", 0)
    fun setTheme(ctx: Context, t: Int) = prefs(ctx).edit().putInt("theme", t.coerceIn(0, 2)).apply()

    fun lost(ctx: Context) = prefs(ctx).getBoolean("lost", false)
    fun lostMessage(ctx: Context) = prefs(ctx).getString("lostMessage", "") ?: ""
    fun setLost(ctx: Context, lost: Boolean, message: String = lostMessage(ctx)) =
        prefs(ctx).edit().putBoolean("lost", lost).putString("lostMessage", message.take(200)).apply()

    // ------------------------------------------------------------ the link to DDrive

    /** Your 24 words, or null when FindX isn't linked yet. */
    fun words(ctx: Context): String? = prefs(ctx).getString("words", null)?.let { runCatching { unlockText(it) }.getOrNull() }

    @Volatile private var keysFor: Pair<String, FxKeys>? = null

    fun keys(ctx: Context): FxKeys? {
        val w = words(ctx) ?: return null
        keysFor?.let { if (it.first == w) return it.second }
        return FxKeys(Words.phraseToKey(w)).also { keysFor = w to it }
    }

    fun host(ctx: Context) = prefs(ctx).getString("host", "") ?: ""
    fun user(ctx: Context) = prefs(ctx).getString("user", "") ?: ""
    fun e2e(ctx: Context) = prefs(ctx).getBoolean("e2e", false)

    fun link(ctx: Context, host: String, words: String, e2e: Boolean, user: String) {
        prefs(ctx).edit().putString("host", host).putString("words", lockText(words)).putBoolean("e2e", e2e).putString("user", user)
            .remove("linkProblem").commit()
        keysFor = null
    }

    fun unlink(ctx: Context) {
        prefs(ctx).edit().remove("host").remove("words").remove("e2e").remove("user").remove("linkProblem").remove("relay").remove("keysFileFor").remove("waitS").commit()
        keysFor = null
    }

    fun linkProblem(ctx: Context) = prefs(ctx).getString("linkProblem", null)
    fun setLinkProblem(ctx: Context, p: String?) = prefs(ctx).edit().putString("linkProblem", p).apply()

    /** Whether the host has FindX's live relay (a DDrive from the version that has FindX on). */
    fun relay(ctx: Context) = prefs(ctx).getBoolean("relay", false)
    fun setRelay(ctx: Context, on: Boolean) = prefs(ctx).edit().putBoolean("relay", on).apply()

    // ------------------------------------------------------------ the last report, for the app's own screen

    fun lastReport(ctx: Context): Report? = readLocked(File(ctx.filesDir, "last.fx"))?.let { runCatching { Report.of(JSONObject(String(it))) }.getOrNull() }
    fun saveReport(ctx: Context, r: Report) = writeLocked(File(ctx.filesDir, "last.fx"), r.toJson().toString().toByteArray())

    // ------------------------------------------------------------ spots

    data class Spot(val id: String, val name: String, val sig: Signature, val lat: Double?, val lon: Double?, val made: Long)

    private fun sigJson(s: Signature) = JSONObject().apply {
        put("wifi", JSONObject().apply { s.wifi.forEach { (k, v) -> put(k, v) } })
        put("rtt", JSONObject().apply { s.rtt.forEach { (k, v) -> put(k, v) } })
        s.magnet?.let { put("mag", it) }
        s.pressure?.let { put("hpa", it) }
    }

    private fun sigOf(o: JSONObject): Signature {
        val w = o.optJSONObject("wifi") ?: JSONObject()
        val r = o.optJSONObject("rtt") ?: JSONObject()
        return Signature(
            w.keys().asSequence().associateWith { w.getInt(it) }, r.keys().asSequence().associateWith { r.getDouble(it) },
            o.optDouble("mag").takeIf { !it.isNaN() }, o.optDouble("hpa").takeIf { !it.isNaN() },
        )
    }

    fun spots(ctx: Context): List<Spot> {
        val a = readLocked(File(ctx.filesDir, "spots.fx"))?.let { JSONArray(String(it)) } ?: return emptyList()
        return (0 until a.length()).map {
            val o = a.getJSONObject(it)
            Spot(o.getString("id"), o.getString("name"), sigOf(o.getJSONObject("sig")),
                o.optDouble("lat").takeIf { v -> !v.isNaN() }, o.optDouble("lon").takeIf { v -> !v.isNaN() }, o.optLong("made"))
        }
    }

    fun saveSpots(ctx: Context, spots: List<Spot>) = writeLocked(File(ctx.filesDir, "spots.fx"), JSONArray().apply {
        spots.forEach { s ->
            put(JSONObject().put("id", s.id).put("name", s.name).put("sig", sigJson(s.sig)).put("made", s.made).apply {
                s.lat?.let { put("lat", it) }; s.lon?.let { put("lon", it) }
            })
        }
    }.toString().toByteArray())

    // ------------------------------------------------------------ shares

    /** A live-location link. [until] 0 means until you stop it. The key is only in the link and here. */
    data class Share(val id: String, val key: String, val name: String, val until: Long, val approximate: Boolean, val made: Long) {
        fun live(now: Long) = until == 0L || now < until
    }

    fun shares(ctx: Context): List<Share> {
        val a = readLocked(File(ctx.filesDir, "shares.fx"))?.let { JSONArray(String(it)) } ?: return emptyList()
        return (0 until a.length()).map {
            val o = a.getJSONObject(it)
            Share(o.getString("id"), o.getString("key"), o.optString("name"), o.optLong("until"), o.optBoolean("approx"), o.optLong("made"))
        }
    }

    fun saveShares(ctx: Context, shares: List<Share>) = writeLocked(File(ctx.filesDir, "shares.fx"), JSONArray().apply {
        shares.forEach { put(JSONObject().put("id", it.id).put("key", it.key).put("name", it.name).put("until", it.until).put("approx", it.approximate).put("made", it.made)) }
    }.toString().toByteArray())

    // ------------------------------------------------------------ other devices, as last heard

    data class Peer(val device: String, val name: String, val report: Report?, val seen: Long, val uwb: Boolean)

    fun peers(ctx: Context): List<Peer> {
        val a = readLocked(File(ctx.filesDir, "peers.fx"))?.let { JSONArray(String(it)) } ?: return emptyList()
        return (0 until a.length()).mapNotNull {
            val o = a.getJSONObject(it)
            runCatching {
                Peer(o.getString("d"), o.optString("n"), o.optJSONObject("r")?.let { r -> Report.of(r) }, o.optLong("seen"), o.optBoolean("uwb"))
            }.getOrNull()
        }
    }

    fun savePeers(ctx: Context, peers: List<Peer>) = writeLocked(File(ctx.filesDir, "peers.fx"), JSONArray().apply {
        peers.forEach { p -> put(JSONObject().put("d", p.device).put("n", p.name).put("seen", p.seen).put("uwb", p.uwb).apply { p.report?.let { put("r", it.toJson()) } }) }
    }.toString().toByteArray())

    // ------------------------------------------------------------ battery use, so you can see what FindX costs

    fun addGpsMs(ctx: Context, ms: Long) {
        if (ms <= 0) return
        val p = prefs(ctx)
        val day = System.currentTimeMillis() / 86_400_000
        val e = p.edit()
        if (p.getLong("gpsDay", 0) != day) e.putLong("gpsDay", day).putLong("gpsYesterday", p.getLong("gpsToday", 0)).putLong("gpsToday", 0)
        e.putLong("gpsToday", (if (p.getLong("gpsDay", 0) == day) p.getLong("gpsToday", 0) else 0) + ms).apply()
    }

    fun gpsToday(ctx: Context): Long {
        val p = prefs(ctx)
        return if (p.getLong("gpsDay", 0) == System.currentTimeMillis() / 86_400_000) p.getLong("gpsToday", 0) else 0
    }

    fun countUpload(ctx: Context) {
        val p = prefs(ctx)
        val day = System.currentTimeMillis() / 86_400_000
        val n = if (p.getLong("upDay", 0) == day) p.getInt("upToday", 0) else 0
        p.edit().putLong("upDay", day).putInt("upToday", n + 1).apply()
    }

    fun uploadsToday(ctx: Context): Int {
        val p = prefs(ctx)
        return if (p.getLong("upDay", 0) == System.currentTimeMillis() / 86_400_000) p.getInt("upToday", 0) else 0
    }

    /** Erases everything FindX keeps on this phone (not what is on DDrive). */
    fun eraseLocal(ctx: Context) {
        ctx.filesDir.listFiles()?.forEach { it.deleteRecursively() }
        prefs(ctx).edit().clear().commit()
        runCatching { KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(ALIAS) }
        key = null
        keysFor = null
    }
}
