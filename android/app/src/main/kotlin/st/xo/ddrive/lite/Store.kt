package st.xo.ddrive.lite

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import st.xo.ddrive.core.HostClient
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * What this phone remembers: the host, the sign-in, and whether folder syncing is on. The 24 words
 * are kept encrypted with a key that lives in Android's keystore and never leaves the phone.
 */
class Store(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("lite", Context.MODE_PRIVATE)

    val host: String get() = prefs.getString("host", "") ?: ""
    val user: String get() = prefs.getString("user", "") ?: ""
    val connected get() = host.isNotEmpty() && prefs.contains("phrase")

    var sync: Boolean
        get() = prefs.getBoolean("sync", false)
        set(v) = prefs.edit().putBoolean("sync", v).apply()

    /** Copy only over Wi-Fi (or another connection that isn't counted): a photo library is big. */
    var wifiOnly: Boolean
        get() = prefs.getBoolean("wifi_only", true)
        set(v) = prefs.edit().putBoolean("wifi_only", v).apply()

    /** What the host knows this phone by; made once. */
    val device: String
        get() = prefs.getString("device", null) ?: ByteArray(8).also { SecureRandom().nextBytes(it) }
            .joinToString("") { "%02x".format(it) }.also { prefs.edit().putString("device", it).apply() }

    fun connect(host: String, user: String, phrase: String) {
        prefs.edit().putString("host", host).putString("user", user).putString("phrase", seal(phrase)).apply()
    }

    fun forget() {
        prefs.edit().remove("host").remove("user").remove("phrase").putBoolean("sync", false).apply()
    }

    /** A sign-in to the host, or null when this phone isn't connected (or its saved words can't be read any more). */
    fun client(): HostClient? {
        if (!connected) return null
        val phrase = try {
            open(prefs.getString("phrase", "") ?: "")
        } catch (e: Exception) {
            return null
        }
        return HostClient(host, phrase)
    }

    // ------------------------------------------------------------ the keystore
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256).build())
        return gen.generateKey()
    }

    private fun seal(text: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        return Base64.encodeToString(c.iv + c.doFinal(text.toByteArray()), Base64.NO_WRAP)
    }

    private fun open(sealed: String): String {
        val raw = Base64.decode(sealed, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12))
        return String(c.doFinal(raw, 12, raw.size - 12))
    }

    private companion object {
        const val ALIAS = "ddrive-lite"
    }
}
