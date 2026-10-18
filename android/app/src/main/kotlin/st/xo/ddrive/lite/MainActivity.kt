package st.xo.ddrive.lite

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import st.xo.ddrive.core.HostClient
import st.xo.ddrive.core.HostError
import st.xo.ddrive.core.Seal
import st.xo.ddrive.core.Words
import kotlin.concurrent.thread

/**
 * Connecting the phone to a host (its address and your 24 words) and the phone's own settings:
 * folder syncing and what the phone has to allow for it. Everything else is the dashboard
 * (DashboardActivity). Which folders are synced, and how, is chosen on its Sync page.
 */
class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var root: LinearLayout
    private val ui = Handler(Looper.getMainLooper())
    private var statusLine: TextView? = null
    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(40), dp(24), dp(40))
        }
        setContentView(ScrollView(this).apply {
            isFillViewport = true
            fitsSystemWindows = true
            addView(root)
        })
    }

    override fun onResume() {
        super.onResume()
        SyncService.listener = { ui.post { refresh() } }
        if (store.sync && store.connected && SyncService.agent == null) startSync()
        draw()
    }

    override fun onPause() {
        SyncService.listener = null
        super.onPause()
    }

    // ------------------------------------------------------------ small pieces
    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()

    private fun text(s: String, size: Float = 15f, color: Int = INK, bold: Boolean = false, top: Int = 0) = TextView(this).apply {
        text = s
        textSize = size
        setTextColor(color)
        if (bold) typeface = Typeface.DEFAULT_BOLD
        setLineSpacing(0f, 1.15f)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(top) }
    }

    private fun button(label: String, top: Int = 12, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(top) }
        setOnClickListener { onClick() }
    }

    private fun input(hint: String, lines: Int = 1, type: Int = InputType.TYPE_CLASS_TEXT) = EditText(this).apply {
        this.hint = hint
        setHintTextColor(FAINT)
        setTextColor(INK)
        textSize = 15f
        inputType = type
        if (lines > 1) {
            minLines = lines
            gravity = Gravity.TOP
        }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) }
    }

    /** A setting: what it is, a line about it, and a switch. */
    private fun toggle(title: String, about: String, on: Boolean, top: Int = 22, change: (Boolean) -> Unit): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(top) }
        }
        val words = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(12) }
            addView(text(title, 16f, bold = true))
            addView(text(about, 13f, MUTED, top = 2))
        }
        row.addView(words)
        row.addView(Switch(this).apply {
            isChecked = on
            setOnCheckedChangeListener { _, v -> change(v) }
        })
        return row
    }

    /** Something the phone has to allow, with a button while it hasn't. */
    private fun permission(title: String, about: String, granted: Boolean, grant: () -> Unit) {
        root.addView(text((if (granted) "✓  " else "•  ") + title, 15f, if (granted) MUTED else INK, bold = !granted, top = 16))
        if (!granted) {
            root.addView(text(about, 13f, MUTED, top = 2))
            root.addView(button("Allow", top = 6, onClick = grant))
        }
    }

    private fun say(message: String) {
        AlertDialog.Builder(this).setMessage(message).setPositiveButton("OK", null).show()
    }

    // ------------------------------------------------------------ the screen
    private fun draw() {
        root.removeAllViews()
        statusLine = null
        root.addView(text(if (store.connected) "This phone" else "DDrive", 26f, bold = true))
        if (!store.connected) drawConnect() else drawConnected()
    }

    private fun drawConnect() {
        root.addView(text("Your DDrive on this phone: your files, gallery, notes, FindX and everything else the dashboard has, " +
                          "and this phone's folders backed up to it by themselves.", 15f, MUTED, top = 6))
        root.addView(text("Address of the host's dashboard", 13f, MUTED, top = 28))
        val address = input("http://192.168.1.20:8765 or https://drive.example.com", type = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        root.addView(address)
        root.addView(text("Your 24 words", 13f, MUTED, top = 18))
        val words = input("the drive's recovery phrase, or your account's", lines = 4,
                          type = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)
        root.addView(words)
        val go = button("Connect", top = 18) {}
        go.setOnClickListener {
            if (busy) return@setOnClickListener
            val host = address.text.toString().trim()
            val phrase = words.text.toString().trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            if (host.isEmpty() || phrase.isEmpty()) {
                say("Give the address of the host's dashboard and your 24 words.")
                return@setOnClickListener
            }
            busy = true
            go.text = "Connecting…"
            thread {
                val result = try {
                    val key = try {
                        Words.phraseToKey(phrase)
                    } catch (e: IllegalArgumentException) {
                        throw HostError(0, "That phrase isn't right: ${e.message}.")
                    }
                    // First as an end-to-end encrypted account, which sends no words: only a sign-in key made from them.
                    var e2e = true
                    var client = HostClient(host, "", auth = Seal.authKey(key).joinToString("") { "%02x".format(it) })
                    try {
                        client.login()
                    } catch (e: HostError) {
                        if (e.status != 401) throw e
                        e2e = false                          // the owner, or an account from before 2.5.0: the words are sent
                        client = HostClient(host, phrase)
                        client.login()
                    }
                    val st = client.status()
                    val user = if (st.optString("role", "owner") == "owner") "the owner" else "account ${st.optString("account")}"
                    store.connect(client.address, user, phrase, e2e)
                    null
                } catch (e: HostError) {
                    e.message
                } catch (e: Exception) {
                    e.message ?: e.toString()
                }
                ui.post {
                    busy = false
                    if (result != null) {
                        go.text = "Connect"
                        say(result)
                    } else openDashboard("")
                }
            }
        }
        root.addView(go)
        root.addView(text("The words are kept encrypted on this phone. Over plain http they travel unencrypted when signing in: " +
                          "fine on your own Wi-Fi, use the host's https address elsewhere.", 13f, FAINT, top = 18))
    }

    private fun drawConnected() {
        root.addView(text("${store.host}\nSigned in as ${store.user}", 14f, MUTED, top = 6))
        root.addView(button("Open DDrive", top = 16) { openDashboard("") })
        if (store.e2e) root.addView(text("End-to-end encrypted: files are sealed on this phone before they are sent, and your words never leave it.", 13f, OK, top = 4))

        root.addView(toggle("Folder syncing", if (store.e2e) "This phone shows up under Sync on your account's page. Its folders are chosen there."
                                              else "This phone shows up on the host's Sync page. Its folders are chosen there.", store.sync, top = 30) { on ->
            if (on == store.sync) return@toggle
            if (on) {
                AlertDialog.Builder(this).setTitle("Turn folder syncing on?")
                    .setMessage(if (store.e2e) "Whoever signs in to your account's page with these 24 words can then choose any folder of this phone " +
                                    "to copy into the account, or to copy its folders into. You add the folders there, under Sync. The files, " +
                                    "this phone's name and its folders are sealed before they leave it."
                                else "Whoever is signed in to the host's dashboard with these 24 words can then choose any folder of this phone " +
                                    "to copy to the drive, or to copy drive folders into. You add the folders there, on the Sync page.")
                    .setPositiveButton("Turn on") { _, _ ->
                        store.sync = true
                        startSync()
                        draw()
                    }
                    .setNegativeButton("Cancel") { _, _ -> draw() }.setOnCancelListener { draw() }.show()
            } else {
                store.sync = false
                SyncService.stop(this)                  // (it says to the host that this phone is off the page)
                draw()
            }
        })
        statusLine = text("", 14f, top = 10).also { root.addView(it) }

        if (store.sync) {
            root.addView(button("Open the Sync page", top = 16) { openDashboard("#/sync") })
            root.addView(text("What the phone has to allow", 13f, FAINT, top = 30))
            permission("All files access", "So that any folder you choose on the Sync page can be read, and written for the modes that copy to the phone.",
                       Phone.hasAllFiles(this)) { askAllFiles() }
            permission("Notifications", "The notification that says syncing is running, and what it is doing.", Phone.mayNotify(this)) {
                if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
            }
            permission("Run in the background", "Without this Android stops the syncing after a while when the phone is not in use.",
                       Phone.unrestricted(this)) {
                try {
                    startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
                } catch (e: Exception) {
                    startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                }
            }
        }

        root.addView(toggle("Only on Wi-Fi", "Off: files are also copied over mobile data.", store.wifiOnly, top = 30) { on ->
            store.wifiOnly = on
            SyncService.agent?.retryNow()
            refresh()
        })
        root.addView(text("Files a sync removes from this phone (the download mirror and two-way modes) are moved to " +
                          "“DDrive Lite/Removed by sync” in the phone's storage and kept 30 days.", 13f, FAINT, top = 26))
        root.addView(button("Sign out on this phone", top = 30) {
            AlertDialog.Builder(this).setMessage("Remove the host's address and your sign-in from this phone? Folder syncing stops; " +
                                                 "nothing is deleted on the phone or on the drive.")
                .setPositiveButton("Sign out") { _, _ ->
                    DashboardActivity.forgetAll(this, store)
                    draw()
                }.setNegativeButton("Cancel", null).show()
        })
        root.addView(text("Version ${packageManager.getPackageInfo(packageName, 0).versionName}", 12f, FAINT, top = 24))
        refresh()
    }

    private fun openDashboard(page: String) {
        startActivity(Intent(this, DashboardActivity::class.java).putExtra(DashboardActivity.EXTRA_PAGE, page)
                          .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }

    private fun refresh() {
        val line = statusLine ?: return
        val agent = SyncService.agent
        val (words, color) = when {
            !store.sync -> "Off" to MUTED
            agent == null -> "Starting…" to MUTED
            !Phone.hasAllFiles(this) -> "Waiting for “All files access” (below)" to WARN
            else -> agent.summary() to if (agent.online && Phone.held(this, store) == null) OK else WARN
        }
        line.text = words
        line.setTextColor(color)
    }

    private fun startSync() {
        try {
            SyncService.start(this)
        } catch (e: RuntimeException) {
            say("Folder syncing could not start: ${e.message}")
        }
    }

    private fun askAllFiles() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
            } catch (e: Exception) {
                startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            }
        } else {
            requestPermissions(arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE), 1)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        SyncService.agent?.retryNow()
        draw()
    }

    private companion object {
        val INK = Color.parseColor("#f2f2f2")
        val MUTED = Color.parseColor("#a0a0a0")
        val FAINT = Color.parseColor("#707070")
        val OK = Color.parseColor("#4cd964")
        val WARN = Color.parseColor("#ffb340")
    }
}
