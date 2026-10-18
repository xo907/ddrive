package st.xo.ddrive.lite

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.provider.OpenableColumns
import android.util.Log
import st.xo.ddrive.core.HostClient
import st.xo.ddrive.core.HostError
import st.xo.ddrive.core.Vault
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Sends what was shared to DDrive (ShareActivity) into a folder of the drive, one file after another,
 * with a notification that says how far it is. A file of an end-to-end encrypted account is sealed on
 * the phone before it goes, as folder syncing does. A name that is taken gets " (2)" and so on: nothing
 * on the drive is replaced.
 */
class UploadService : Service() {
    private val work = Executors.newSingleThreadExecutor()
    private var pending = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Saving to DDrive", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while what you shared to DDrive is sent"
                setShowBadge(false)
            })
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val first = note("Getting ready…", 0, 0)
        if (Build.VERSION.SDK_INT >= 34) startForeground(NOTE, first, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC) else startForeground(NOTE, first)
        if (intent == null) {
            if (pending == 0) stopSelf(startId)
            return START_NOT_STICKY
        }
        val folder = intent.getStringExtra(EXTRA_FOLDER) ?: "/From phone"
        @Suppress("DEPRECATION")
        val uris: List<Uri> = (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(EXTRA_URIS, Uri::class.java)
                               else intent.getParcelableArrayListExtra(EXTRA_URIS)) ?: emptyList()
        val text = intent.getStringExtra(EXTRA_TEXT)
        val subject = intent.getStringExtra(EXTRA_SUBJECT)
        pending++
        work.execute {
            try {
                send(folder, uris, text, subject)
            } finally {
                pending--
                if (pending == 0) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf(startId)
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun send(folder: String, uris: List<Uri>, text: String?, subject: String?) {
        val store = Store(this)
        val client = store.client() ?: return done("Sign in to DDrive again in the app: the saved sign-in can't be read.", folder, 0, true)
        val vault = if (store.e2e) Vault(client, store.accountKey() ?: return done("Sign in to DDrive again in the app.", folder, 0, true)) else null
        val wake = (getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ddrive:share")
        wake.acquire(6 * 60 * 60 * 1000L)
        var saved = 0
        val failed = ArrayList<String>()
        try {
            vault?.sync()                        // (the account's list of files, as it is now)
            if (vault != null) vault.mkdirs(folder) else mkdirs(client, folder)
            val items: List<Pair<String, () -> File>> = if (text != null) {
                val name = (subject?.takeIf { it.isNotBlank() }?.take(60)?.replace(Regex("[\\\\/:*?\"<>|]"), "_")
                            ?: "Shared " + SimpleDateFormat("yyyy-MM-dd HH.mm", Locale.US).format(Date())) + ".txt"
                listOf(name to { File(cacheDir, "share-text").apply { writeText(text) } })
            } else uris.map { uri -> nameOf(uri) to { copy(uri) } }
            val taken = HashSet(existing(client, vault, folder))
            items.forEachIndexed { i, (wanted, open) ->
                val name = free(wanted, taken)
                taken.add(name.lowercase())
                getSystemService(NotificationManager::class.java).notify(NOTE, note("Saving $name", i, items.size))
                var tmp: File? = null
                try {
                    tmp = open()
                    val path = "$folder/$name"
                    val mtime = System.currentTimeMillis() / 1000.0
                    if (vault != null) vault.put(path, tmp, tmp.length(), mtime) else client.uploadParts(path, tmp, tmp.length(), mtime)
                    saved++
                } catch (e: Exception) {
                    Log.w("DDrive", "Couldn't save $name: $e")
                    failed.add("$name (${e.message ?: e.javaClass.simpleName})")
                } finally {
                    tmp?.delete()
                }
            }
        } catch (e: Exception) {
            failed.add(e.message ?: e.toString())
        } finally {
            if (wake.isHeld) wake.release()
        }
        done(if (failed.isEmpty()) "Saved ${plural(saved)} to $folder" else "Saved ${plural(saved)} to $folder; not saved: ${failed.joinToString(", ")}",
             folder, saved, failed.isNotEmpty())
    }

    private fun plural(n: Int) = if (n == 1) "1 file" else "$n files"

    /** The folder and the ones above it, on a drive (an end-to-end account's: Vault.mkdirs). */
    private fun mkdirs(client: HostClient, folder: String) {
        var at = ""
        for (part in folder.trim('/').split('/').filter { it.isNotEmpty() }) {
            val parent = at.ifEmpty { "/" }
            at += "/$part"
            val there = try {
                val items = client.list(parent)
                (0 until items.length()).any { items.getJSONObject(it).optString("name") == part }
            } catch (e: HostError) {
                false
            }
            if (!there) client.mkdir(at)
        }
    }

    private fun existing(client: HostClient, vault: Vault?, folder: String): List<String> = try {
        if (vault != null) {
            val node = vault.resolve(folder)
            if (node == null) emptyList() else vault.children(node.getString("id")).map { it.optString("name").lowercase() }
        } else {
            val items = client.list(folder)
            (0 until items.length()).map { items.getJSONObject(it).optString("name").lowercase() }
        }
    } catch (e: Exception) {
        emptyList()
    }

    /** "photo.jpg", or "photo (2).jpg" when that is taken. */
    private fun free(name: String, taken: Set<String>): String {
        if (name.lowercase() !in taken) return name
        val dot = name.lastIndexOf('.').takeIf { it > 0 } ?: name.length
        for (i in 2..10_000) {
            val n = name.substring(0, dot) + " ($i)" + name.substring(dot)
            if (n.lowercase() !in taken) return n
        }
        return name
    }

    private fun nameOf(uri: Uri): String {
        val shown = runCatching {
            contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull() ?: uri.lastPathSegment?.substringAfterLast('/')
        return (shown ?: "Shared file").replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "Shared file" }
    }

    /** A copy of what was shared, to send from (it can only be read as a stream, and the upload goes in parts). */
    private fun copy(uri: Uri): File {
        val tmp = File.createTempFile("share-", ".part", cacheDir)
        (contentResolver.openInputStream(uri) ?: throw IllegalStateException("it can't be read")).use { input ->
            tmp.outputStream().use { input.copyTo(it, 256 * 1024) }
        }
        return tmp
    }

    private fun open(folder: String): PendingIntent = PendingIntent.getActivity(this, 0,
        Intent(this, DashboardActivity::class.java).putExtra(DashboardActivity.EXTRA_PAGE, "#/files" + folder.split('/').joinToString("/") { Uri.encode(it) })
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun note(text: String, done: Int, total: Int): Notification {
        val b = Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_sync).setContentTitle("Saving to DDrive")
            .setContentText(text).setOngoing(true).setOnlyAlertOnce(true)
        if (total > 1) b.setProgress(total, done, false) else b.setProgress(0, 0, true)
        return b.build()
    }

    private fun done(text: String, folder: String, saved: Int, problem: Boolean) {
        // Notifications not allowed: said on the screen instead (it can't be seen otherwise).
        if (!getSystemService(NotificationManager::class.java).areNotificationsEnabled())
            android.os.Handler(mainLooper).post { android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_LONG).show() }
        val n = Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_sync)
            .setContentTitle(if (problem) "Not everything was saved to DDrive" else "Saved to DDrive")
            .setContentText(text).setStyle(Notification.BigTextStyle().bigText(text))
            .setAutoCancel(true).setContentIntent(open(folder)).build()
        getSystemService(NotificationManager::class.java).notify(DONE + (System.currentTimeMillis() % 1000).toInt(), n)
    }

    companion object {
        const val EXTRA_FOLDER = "folder"
        const val EXTRA_URIS = "uris"
        const val EXTRA_TEXT = "text"
        const val EXTRA_SUBJECT = "subject"
        private const val CHANNEL = "share"
        private const val NOTE = 40
        private const val DONE = 41
    }
}
