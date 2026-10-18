package com.xo.findx

import android.app.Activity
import android.app.Application
import android.app.KeyguardManager
import android.content.Context
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.CancellationSignal
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Black, white and one red (SaveX's and GalleryX's palette), with soft greys for depth, and the same
// in light: the names say what each is for in the dark theme ("Black" is the background, "White" the
// text), and in the light theme they are its light counterparts. Settings › Appearance picks one, or
// follows the phone.
object Look {
    /** 0 follows the phone, 1 light, 2 dark (Store.theme). */
    var choice by mutableStateOf(0)
    /** What it comes to now. */
    var dark by mutableStateOf(true)
}

private fun pick(dark: Long, light: Long) = Color(if (Look.dark) dark else light)

val Black: Color get() = pick(0xFF000000, 0xFFF2F2F7)      // the background
val White: Color get() = pick(0xFFFFFFFF, 0xFF000000)      // text and icons
val Red = Color(0xFFFF2A2A)
val OnRed = Color(0xFFFFFFFF)                              // text and icons on red, in either theme
val Grey = Color(0xFF8E8E93)                               // secondary text
val Grey2: Color get() = pick(0xFFC7C7CC, 0xFF3C3C43)      // tertiary text
val Line: Color get() = pick(0xFF1C1C1E, 0xFFFFFFFF)       // raised surfaces (dialogs, sheets)
val Line2: Color get() = pick(0xFF2C2C2E, 0xFFD1D1D6)      // outlines, dividers
val Surface2: Color get() = pick(0xFF141416, 0xFFFFFFFF)   // cards
val Surface3: Color get() = pick(0xFF1F1F23, 0xFFE9E9EE)   // pressed / inner cards
val Warn: Color get() = pick(0xFF2A1416, 0xFFFFE8E6)       // a card that needs you
val Calm = Color(0xFF30D158)                               // "all good" dots only

@Composable
fun FindXTheme(content: @Composable () -> Unit) {
    val system = androidx.compose.foundation.isSystemInDarkTheme()
    val dark = when (Look.choice) { 1 -> false; 2 -> true; else -> system }
    if (Look.dark != dark) Look.dark = dark
    val activity = Guard.activity(androidx.compose.ui.platform.LocalContext.current) as? androidx.activity.ComponentActivity
    androidx.compose.runtime.LaunchedEffect(dark) {
        // The status and navigation bar icons: light on the dark theme, dark on the light one.
        val t = android.graphics.Color.TRANSPARENT
        activity?.enableEdgeToEdge(
            if (dark) androidx.activity.SystemBarStyle.dark(t) else androidx.activity.SystemBarStyle.light(t, t),
            if (dark) androidx.activity.SystemBarStyle.dark(t) else androidx.activity.SystemBarStyle.light(t, t),
        )
    }
    MaterialTheme(
        colorScheme = if (Look.dark) darkColorScheme(
            primary = Red, onPrimary = OnRed, background = Black, onBackground = White,
            surface = Black, onSurface = White, surfaceVariant = Surface2, outline = Line2,
        ) else lightColorScheme(
            primary = Red, onPrimary = OnRed, background = Black, onBackground = White,
            surface = Black, onSurface = White, surfaceVariant = Surface2, outline = Line2,
        ),
        content = content,
    )
}

class FindXApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Service.channels(this)
        Meter.app = this
        Look.choice = Store.theme(this)
        if (Store.setUp(this) && Store.on(this)) Service.start(this)
        if (Store.lost(this)) Service.lostNotice(this)
    }
}

/**
 * Changes that would help someone who has your phone hide it from you (turning FindX off, unlinking
 * it, ending lost mode, erasing it) need the phone's own fingerprint, face or screen lock first.
 * A phone with no screen lock can't ask, so it doesn't.
 */
object Guard {
    fun ask(activity: Activity, why: String, onOk: () -> Unit) {
        val km = activity.getSystemService(KeyguardManager::class.java)
        if (!km.isDeviceSecure) { onOk(); return }
        val auth = BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.DEVICE_CREDENTIAL
        val prompt = BiometricPrompt.Builder(activity).setTitle("Confirm it's you").setSubtitle(why).setAllowedAuthenticators(auth).build()
        prompt.authenticate(CancellationSignal(), activity.mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult?) = onOk()
        })
    }

    fun activity(ctx: Context): Activity? {
        var c = ctx
        while (c is android.content.ContextWrapper) {
            if (c is Activity) return c
            c = c.baseContext
        }
        return null
    }
}
