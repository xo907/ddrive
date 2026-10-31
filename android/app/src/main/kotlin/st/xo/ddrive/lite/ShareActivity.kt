package st.xo.ddrive.lite

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * "Save to DDrive" in the phone's share sheet: photos, videos, documents or a bit of text from any app
 * go into a folder of your DDrive. Which folder is asked (the last one is remembered); the files are
 * sent by UploadService, in the background, sealed on the phone first for an end-to-end account.
 *
 * A link goes into your Bookmarks instead: straight away from "Bookmark in DDrive" (the BookmarkShare alias in
 * the manifest), or when chosen from "Save to DDrive". The dashboard's Bookmarks page saves it (with its preview),
 * in the app (DashboardActivity), so an end-to-end account's bookmarks are sealed on the phone like the rest.
 */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = Store(this)
        if (!store.connected) {
            Toast.makeText(this, "Sign in to your DDrive in the DDrive app first.", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }
        val uris = received(intent)
        val text = if (uris.isEmpty()) intent.getStringExtra(Intent.EXTRA_TEXT)?.takeIf { it.isNotBlank() } else null
        if (uris.isEmpty() && text == null) {
            Toast.makeText(this, "There is nothing in that to save.", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        val links = text?.split(Regex("""\s+"""))?.filter { LINK.matches(it) }.orEmpty()
        if (intent.component?.className?.endsWith(".BookmarkShare") == true) {
            if (links.isEmpty()) {
                Toast.makeText(this, "There is no web address in that to bookmark.", Toast.LENGTH_LONG).show()
                finish()
            } else bookmark(links)
            return
        }
        if (links.isNotEmpty()) {
            dialog().setTitle("Save the link to DDrive")
                .setMessage(links.joinToString("\n"))
                .setPositiveButton("Bookmark it") { _, _ -> bookmark(links) }
                .setNeutralButton("Save as a file") { _, _ -> askFolder(store, uris, text) }
                .setNegativeButton("Cancel") { _, _ -> finish() }
                .setOnCancelListener { finish() }
                .show()
            return
        }
        askFolder(store, uris, text)
    }

    private fun dialog(): AlertDialog.Builder {
        val night = (resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) == android.content.res.Configuration.UI_MODE_NIGHT_YES
        return AlertDialog.Builder(this, if (night) android.R.style.Theme_DeviceDefault_Dialog_Alert else android.R.style.Theme_DeviceDefault_Light_Dialog_Alert)
    }

    /** Into the Bookmarks of your DDrive: the app opens on that page, which saves them. */
    private fun bookmark(links: List<String>) {
        startActivity(Intent(this, DashboardActivity::class.java)
            .putExtra(DashboardActivity.EXTRA_MARKS, links.joinToString(" "))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun askFolder(store: Store, uris: List<Uri>, text: String?) {
        val pad = (20 * resources.displayMetrics.density).toInt()
        val folder = EditText(this).apply {
            setText(store.shareFolder)
            setSelection(store.shareFolder.length)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            hint = "/From phone"
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
            addView(TextView(this@ShareActivity).apply { this.text = "Into this folder of your DDrive:" })
            addView(folder)
        }
        val what = when {
            uris.size == 1 -> "1 file"
            uris.isNotEmpty() -> "${uris.size} files"
            else -> "the text"
        }
        dialog()
            .setTitle("Save $what to DDrive")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val where = "/" + folder.text.toString().trim().trim('/').ifEmpty { "From phone" }
                store.shareFolder = where
                val send = Intent(this, UploadService::class.java).putExtra(UploadService.EXTRA_FOLDER, where)
                if (text != null) send.putExtra(UploadService.EXTRA_TEXT, text).putExtra(UploadService.EXTRA_SUBJECT, intent.getStringExtra(Intent.EXTRA_SUBJECT))
                if (uris.isNotEmpty()) {
                    // The files may be read by the service only through these grants (the sharing app gave them to this activity).
                    val clip = ClipData.newRawUri("", uris[0])
                    for (u in uris.drop(1)) clip.addItem(ClipData.Item(u))
                    send.clipData = clip
                    send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    send.putParcelableArrayListExtra(UploadService.EXTRA_URIS, ArrayList(uris))
                }
                Toast.makeText(this, "Saving $what to $where", Toast.LENGTH_SHORT).show()
                // How far it is, and that it is done, is said in a notification: Android 13 on asks once whether it may.
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
                    && !Store(this).askedNotify) {
                    Store(this).askedNotify = true
                    waiting = send
                    requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 3)
                } else go(send)
            }
            .setNegativeButton("Cancel") { _, _ -> finish() }
            .setOnCancelListener { finish() }
            .show()
    }

    private var waiting: Intent? = null

    companion object {
        /** A web address as the dashboard takes one (app.js looksLikeLink). */
        private val LINK = Regex("""^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(/\S*)?$|^https?://\S+$""", RegexOption.IGNORE_CASE)
    }

    private fun go(send: Intent) {
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(send) else startService(send)
        finish()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        waiting?.let { go(it) } ?: finish()
    }

    @Suppress("DEPRECATION")
    private fun received(intent: Intent): List<Uri> = when (intent.action) {
        Intent.ACTION_SEND -> listOfNotNull(
            if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java) else intent.getParcelableExtra(Intent.EXTRA_STREAM))
        Intent.ACTION_SEND_MULTIPLE ->
            (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
             else intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)) ?: emptyList()
        else -> emptyList()
    }
}
