package com.xo.findx

import st.xo.ddrive.core.Seal
import java.nio.ByteBuffer
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/*
 * FindX's keys. All of them are made from the 24 words of your DDrive (the drive's recovery phrase or an
 * account's), so every phone you link with the same words, and your browser on DDrive, can open what
 * the others sealed, and nobody else can: not the DDrive host, not Discord where DDrive keeps its
 * files, not someone who copies the phone's storage. Each job has a key of its own, so a key given for
 * one (the Bluetooth beacon's) opens nothing else.
 *
 * The browser makes the same keys (findx.js), so the formats here are the formats there:
 *   key(job)  = HKDF-SHA256(words key, "findx v1 " + job)
 *   sealed    = nonce(12) + tag(16) + AES-256-GCM ciphertext, with a label as associated data
 */
class FxKeys(wordsKey: ByteArray) {
    val report = Seal.hkdf(wordsKey, "findx v1 report")     // live reports and device cards
    val history = Seal.hkdf(wordsKey, "findx v1 history")   // the history kept on DDrive
    val names = Seal.hkdf(wordsKey, "findx v1 names")       // file names that give nothing away
    val command = Seal.hkdf(wordsKey, "findx v1 command")   // "ring", "locate": only your devices can ask
    val beacon = Seal.hkdf(wordsKey, "findx v1 beacon")     // the Bluetooth beacon's changing name
    /** Who we are to a host's relay: the same for all your devices, and no way back to the words. */
    val group: String = hex(Seal.hkdf(wordsKey, "findx v1 group")).take(32)

    /**
     * What the dashboard needs to open FindX's things, as a file the phones leave on the drive
     * (/FindX/keys.fxk). With an end-to-end encrypted account that file is sealed like all of the
     * account's files, so only your browser opens it; for the owner or an earlier account the drive
     * has the words anyway. (Not the beacon key: the dashboard has no use for it.)
     */
    fun forDashboard(): String = org.json.JSONObject().put("v", 1).put("report", hex(report)).put("history", hex(history))
        .put("names", hex(names)).put("command", hex(command)).put("group", group).toString()

    fun name(text: String, length: Int = 32): String = hex(hmac(names, text.toByteArray())).take(length)

    fun seal(key: ByteArray, label: String, data: ByteArray): ByteArray = Seal.seal(key, data, label.toByteArray())
    fun open(key: ByteArray, label: String, blob: ByteArray): ByteArray = Seal.unseal(key, blob, label.toByteArray())

    /**
     * What a phone's beacon says during one 15-minute window: 4 bytes that all your devices share in that
     * window (so they know it's one of yours) and 4 that tell which device it is. Both change every
     * window, so nobody without the key can follow a phone around by them or tell it is the same phone
     * an hour later (Android changes the Bluetooth address it advertises from, too).
     */
    fun groupTag(window: Long): ByteArray = hmac(beacon, "group".toByteArray() + ByteBuffer.allocate(8).putLong(window).array()).copyOf(4)

    fun deviceTag(device: String, window: Long): ByteArray =
        hmac(beacon, "device".toByteArray() + device.toByteArray() + ByteBuffer.allocate(8).putLong(window).array()).copyOf(4)

    /** Proof that a Bluetooth command comes from one of your devices (and is meant for this challenge). */
    fun commandProof(challenge: ByteArray, message: ByteArray): ByteArray = hmac(command, challenge + message).copyOf(16)

    companion object {
        const val WINDOW_MS = 15 * 60_000L
        private val random = SecureRandom()

        fun window(t: Long) = t / WINDOW_MS

        fun hmac(key: ByteArray, data: ByteArray): ByteArray =
            Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(data)

        fun bytes(n: Int) = ByteArray(n).also { random.nextBytes(it) }
        fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
        fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        fun b64url(b: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(b)
        fun unb64url(s: String): ByteArray = Base64.getUrlDecoder().decode(s)
        fun b64(b: ByteArray): String = Base64.getEncoder().encodeToString(b)
        fun unb64(s: String): ByteArray = Base64.getDecoder().decode(s)

        /** Constant-time comparison. */
        fun same(a: ByteArray, b: ByteArray): Boolean {
            if (a.size != b.size) return false
            var d = 0
            for (i in a.indices) d = d or (a[i].toInt() xor b[i].toInt())
            return d == 0
        }
    }
}
