package com.xo.findx

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.core.content.ContextCompat

/** What FindX needs Android to allow, and why, in the order setup asks for it. */
object Permissions {
    fun has(ctx: Context, p: String) = ContextCompat.checkSelfPermission(ctx, p) == PackageManager.PERMISSION_GRANTED

    fun location(ctx: Context) = has(ctx, Manifest.permission.ACCESS_FINE_LOCATION)
    fun background(ctx: Context) = has(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    fun bluetooth(ctx: Context) = listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE, Manifest.permission.BLUETOOTH_CONNECT).all { has(ctx, it) }
    fun uwb(ctx: Context) = has(ctx, Manifest.permission.UWB_RANGING)
    fun notifications(ctx: Context) = android.os.Build.VERSION.SDK_INT < 33 || has(ctx, Manifest.permission.POST_NOTIFICATIONS)
    fun wifi(ctx: Context) = android.os.Build.VERSION.SDK_INT < 33 || has(ctx, Manifest.permission.NEARBY_WIFI_DEVICES)

    fun fullScreen(ctx: Context) = android.os.Build.VERSION.SDK_INT < 34 || ctx.getSystemService(NotificationManager::class.java).canUseFullScreenIntent()

    /** Do Not Disturb access: so ringing gets through even "total silence". */
    fun dnd(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).isNotificationPolicyAccessGranted

    fun dndIntent() = Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)

    fun unrestricted(ctx: Context) = ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)

    /** Nearby devices: Bluetooth (finding and being found), Ultra-Wideband and Wi-Fi distance, in one Android question. */
    val NEARBY = buildList {
        add(Manifest.permission.BLUETOOTH_SCAN); add(Manifest.permission.BLUETOOTH_ADVERTISE); add(Manifest.permission.BLUETOOTH_CONNECT)
        add(Manifest.permission.UWB_RANGING)
        if (android.os.Build.VERSION.SDK_INT >= 33) add(Manifest.permission.NEARBY_WIFI_DEVICES)
    }.toTypedArray()

    fun nearby(ctx: Context) = bluetooth(ctx) && wifi(ctx)

    fun allGood(ctx: Context) = location(ctx) && background(ctx) && nearby(ctx) && notifications(ctx) && unrestricted(ctx)

    fun appSettings(ctx: Context) = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}"))

    @android.annotation.SuppressLint("BatteryLife")
    fun unrestrictedIntent(ctx: Context) = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))

    fun fullScreenIntent(ctx: Context) = Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:${ctx.packageName}"))
}

/** A short list of what FindX did lately, kept on the phone (sealed) for Settings › Activity. Nothing leaves the phone. */
object Log {
    private const val MAX = 60

    fun note(ctx: Context, what: String) {
        android.util.Log.i("FindX", what)
        runCatching {
            val p = Store.prefs(ctx)
            val old = p.getString("log", null)?.let { runCatching { Store.unlockText(it) }.getOrNull() } ?: ""
            val lines = (listOf("${System.currentTimeMillis()}\t$what") + old.split("\n").filter { it.isNotBlank() }).take(MAX)
            p.edit().putString("log", Store.lockText(lines.joinToString("\n"))).apply()
        }
    }

    fun lines(ctx: Context): List<Pair<Long, String>> {
        val text = Store.prefs(ctx).getString("log", null)?.let { runCatching { Store.unlockText(it) }.getOrNull() } ?: return emptyList()
        return text.split("\n").mapNotNull { l -> l.split("\t", limit = 2).takeIf { it.size == 2 }?.let { (it[0].toLongOrNull() ?: 0) to it[1] } }
    }
}
