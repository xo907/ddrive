package st.xo.ddrive.lite

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.os.Build
import android.os.Environment
import android.os.PowerManager
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** What the syncing needs to know about the phone it runs on: its storage, its name, its network. */
object Phone {
    /** The storage a person sees as "the phone" (usually /storage/emulated/0). */
    val storage: File get() = Environment.getExternalStorageDirectory()

    /** Where files that a sync removed from the phone are kept for 30 days, where the Files app can reach them. */
    val trash: File get() = File(storage, "DDrive Lite/Removed by sync")

    fun name(context: Context): String =
        (Settings.Global.getString(context.contentResolver, "device_name") ?: "").trim().ifEmpty { Build.MODEL ?: "Phone" }.take(60)

    /** The phone's storage and any memory cards, as places to start browsing from. */
    private fun roots(context: Context): List<Pair<String, File>> {
        val out = ArrayList<Pair<String, File>>()
        out.add("Phone storage" to storage)
        for (dir in context.getExternalFilesDirs(null)) {
            val path = dir?.absolutePath ?: continue
            val cut = path.indexOf("/Android/")
            if (cut <= 0) continue
            val root = File(path.substring(0, cut))
            if (root.absolutePath != storage.absolutePath && root.isDirectory) out.add("Memory card (${root.name})" to root)
        }
        return out
    }

    /** The folders in a folder of this phone, for the folder browser on the host's Sync page (sync.browse there). */
    fun browse(context: Context, path: String): JSONObject {
        val roots = roots(context)
        val rootList = JSONArray()
        for ((label, f) in roots) rootList.put(JSONObject().put("name", label).put("path", f.absolutePath))
        if (path.isBlank()) return JSONObject().put("path", "").put("parent", JSONObject.NULL).put("dirs", rootList).put("roots", rootList)
        val dir = File(path.trim())
        if (!dir.isAbsolute || !dir.isDirectory) return JSONObject().put("error", "Folder not found: $path")
        val entries = dir.listFiles() ?: return JSONObject().put("error", "Can't open $path. Does DDrive Lite on the phone have “All files access”?")
        val dirs = JSONArray()
        for (e in entries.filter { it.isDirectory && !it.name.startsWith(".") }.sortedBy { it.name.lowercase() }.take(2000))
            dirs.put(JSONObject().put("name", e.name).put("path", e.absolutePath))
        val isRoot = roots.any { it.second.absolutePath == dir.absolutePath }
        return JSONObject().put("path", dir.absolutePath).put("parent", if (isRoot) "" else dir.parent ?: "")
            .put("dirs", dirs).put("roots", rootList)
    }

    fun hasAllFiles(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= 30) Environment.isExternalStorageManager()
        else context.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    fun mayNotify(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun unrestricted(context: Context): Boolean =
        (context.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(context.packageName)

    /** True on mobile data and other connections where what is sent is counted. */
    fun metered(context: Context): Boolean =
        (context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).isActiveNetworkMetered

    /** Why nothing may be copied right now, in words for the host's Sync page; null when it may. */
    fun held(context: Context, store: Store): String? = when {
        !hasAllFiles(context) -> "DDrive Lite on the phone needs “All files access”. Open the app there."
        store.wifiOnly && metered(context) -> "Waiting for Wi-Fi (the phone is set to copy only over Wi-Fi)"
        else -> null
    }
}
