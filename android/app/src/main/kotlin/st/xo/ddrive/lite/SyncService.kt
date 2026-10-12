package st.xo.ddrive.lite

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaScannerConnection
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import st.xo.ddrive.core.Agent
import st.xo.ddrive.core.Vault
import java.io.File
import kotlin.concurrent.thread

/**
 * Folder syncing, for as long as it is turned on: it has to go on while the app is not on screen, so
 * it is a service with a notification that says what it is doing. The syncing itself is the agent
 * (core), which waits at the host for word from its Sync page and copies when a pair is due.
 */
class SyncService : Service() {
    private var wake: PowerManager.WakeLock? = null
    private var lastShown = ""
    private var lastNotified = 0L
    private val network = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n: Network) {
            agent?.retryNow()
        }

        override fun onCapabilitiesChanged(n: Network, caps: android.net.NetworkCapabilities) {
            agent?.retryNow()                    // Wi-Fi came or went: pairs waiting for it may run now
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Folder syncing", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Shown while this phone syncs folders with your DDrive"
            setShowBadge(false)
        })
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, notification("Starting"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, notification("Starting"))
        wake = (getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ddrive:sync")
            .apply { setReferenceCounted(false) }
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(network)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val store = Store(this)
        if (!store.sync || !store.connected) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (agent == null) {
            val client = store.client()
            if (client == null) {                // the saved words can't be read any more: connect again in the app
                stopSelf()
                return START_NOT_STICKY
            }
            val app = applicationContext
            // An end-to-end encrypted account: files are sealed on this phone before they go, and so is what is said about them.
            val vault = if (store.e2e) Vault(client, store.accountKey() ?: return START_NOT_STICKY.also { stopSelf() }) else null
            agent = Agent(client, store.device, Phone.name(app), File(filesDir, "sync"), Phone.trash,
                          home = Phone.storage.absolutePath, vault = vault,
                          browse = { path -> Phone.browse(app, path) },
                          mayRun = { Phone.held(app, store) },
                          refuse = { job ->
                              when {
                                  !job.local.startsWith("/") -> "“${job.local}” isn't a whole path on the phone. Give one like ${Phone.storage}/DCIM, or use Browse."
                                  File(job.local).absolutePath.startsWith(app.dataDir.absolutePath) -> "That folder holds DDrive Lite's own data; choose another one."
                                  else -> null
                              }
                          },
                          log = { Log.i("DDriveLite", it) },
                          // the gallery and other apps hear about files that came, changed or went
                          touched = { file -> MediaScannerConnection.scanFile(app, arrayOf(file.absolutePath), null, null) },
                          onChange = { changed() }).start()
        }
        changed()
        return START_STICKY
    }

    /** Something about the syncing changed: say so in the notification and on the app's screen. */
    private fun changed() {
        val a = agent ?: return
        val text = a.summary()
        val busy = a.running != null
        try {
            if (busy) wake?.acquire(60 * 60 * 1000L) else if (wake?.isHeld == true) wake?.release()
        } catch (e: RuntimeException) {
            // the lock is only a help
        }
        val now = System.currentTimeMillis()
        if (text != lastShown && (now - lastNotified > 1000 || !busy)) {
            lastShown = text
            lastNotified = now
            getSystemService(NotificationManager::class.java).notify(1, notification(text))
        }
        listener?.invoke()
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_sync).setContentTitle("DDrive Lite")
            .setContentText(text).setOngoing(true).setOnlyAlertOnce(true).setContentIntent(open).build()
    }

    override fun onDestroy() {
        try {
            getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(network)
        } catch (e: RuntimeException) {
            // was never registered
        }
        val a = agent
        agent = null
        val off = !Store(this).sync
        if (a != null) thread(name = "SyncOff") {
            a.stop()
            a.join()
            if (off) a.bye()                     // turned off: off the host's Sync page
        }
        if (wake?.isHeld == true) wake?.release()
        listener?.invoke()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "sync"

        /** The syncing that is running, for the app's screen. */
        @Volatile var agent: Agent? = null
            private set

        /** Called (on any thread) when what the screen shows may have changed. */
        @Volatile var listener: (() -> Unit)? = null

        fun start(context: Context) {
            context.startForegroundService(Intent(context, SyncService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SyncService::class.java))
        }
    }
}

/** Folder syncing comes back by itself after the phone was restarted or the app was updated. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val store = Store(context)
        if (store.sync && store.connected) try {
            SyncService.start(context)
        } catch (e: RuntimeException) {
            Log.w("DDriveLite", "Folder syncing could not start by itself: $e")
        }
    }
}
