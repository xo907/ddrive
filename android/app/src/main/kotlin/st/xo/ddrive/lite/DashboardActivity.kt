package st.xo.ddrive.lite

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * DDrive on the phone: the host's dashboard, the whole of it (Files, Gallery, Notes, FindX, Sync,
 * locked folders, end-to-end encrypted accounts), signed in with the words this phone keeps.
 *
 * Signing in is the dashboard's own: the words are put into its sign-in form here, on the phone, and
 * the page does the rest as it does in any browser. For an end-to-end encrypted account that means
 * the keys are made in this page and only a sign-in key made from them goes to the host; for the
 * owner, the words are sent, as everywhere. Nothing but the host's own address is shown in here:
 * links elsewhere open in the phone's browser.
 */
class DashboardActivity : Activity() {
    private lateinit var store: Store
    private lateinit var root: FrameLayout
    private lateinit var web: WebView
    private var hostOrigin = ""
    @Volatile private var pageOrigin = ""
    private var marksToAdd: String? = null      // links shared to "Bookmark in DDrive", handed to the page once it is signed in
    private var triedSignIn = false
    private var filePick: ValueCallback<Array<Uri>>? = null
    private var fullScreen: View? = null
    private var fullScreenDone: WebChromeClient.CustomViewCallback? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        if (!store.connected) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }
        hostOrigin = origin(store.host)
        root = FrameLayout(this).apply { fitsSystemWindows = true }
        web = WebView(this).apply {
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true            // (and IndexedDB: an end-to-end account's key and list are kept there)
            settings.databaseEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.setSupportMultipleWindows(false)    // (a link that opens a new tab opens here, or in the browser)
            settings.userAgentString = settings.userAgentString + " DDriveApp/" + version()
            addJavascriptInterface(Bridge(), "DDriveApp")
            webViewClient = Client()
            webChromeClient = Chrome()
            setDownloadListener { url, _, disposition, mime, _ -> download(url, disposition, mime) }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        root.addView(web)
        setContentView(root)
        paintBars()
        val want = intent?.getStringExtra(EXTRA_PAGE) ?: ""
        marksToAdd = intent?.getStringExtra(EXTRA_MARKS)
        if (savedInstanceState != null && want.isEmpty() && marksToAdd == null) web.restoreState(savedInstanceState)
        else web.loadUrl(store.host.trimEnd('/') + "/" + want)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        intent?.getStringExtra(EXTRA_MARKS)?.let { marksToAdd = it; addMarks(); return }
        val want = intent?.getStringExtra(EXTRA_PAGE) ?: return
        web.loadUrl(store.host.trimEnd('/') + "/" + want)
    }

    override fun onResume() {
        super.onResume()
        if (::web.isInitialized) web.onResume()
        if (store.connected && store.sync && SyncService.agent == null) try {
            SyncService.start(this)
        } catch (e: RuntimeException) { /* the settings screen says why */ }
    }

    override fun onPause() {
        if (::web.isInitialized) web.onPause()
        CookieManager.getInstance().flush()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (fullScreen != null) {
            fullScreenDone?.onCustomViewHidden()
            return
        }
        if (!::web.isInitialized) {
            @Suppress("DEPRECATION") super.onBackPressed()
            return
        }
        // What is open on top of the page (a dialog, a menu, the viewer) closes first; then the page before.
        web.evaluateJavascript("(function(){try{return !!(window.ddBack&&window.ddBack())}catch(e){return false}})()") { closed ->
            if (closed == "true") return@evaluateJavascript
            if (web.canGoBack()) web.goBack() else finish()
        }
    }

    // ------------------------------------------------------------ the page
    private fun version(): String = try { packageManager.getPackageInfo(packageName, 0).versionName ?: "" } catch (e: Exception) { "" }

    private fun night() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    /** The bars around the page in the dashboard's colours (it is light or dark as the phone is). */
    @Suppress("DEPRECATION")
    private fun paintBars() {
        val bg = if (night()) Color.parseColor("#0f0f10") else Color.parseColor("#fbfbfa")
        root.setBackgroundColor(bg)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        var flags = window.decorView.systemUiVisibility
        flags = if (night()) flags and View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR.inv() else flags or View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        if (Build.VERSION.SDK_INT >= 26) flags = if (night()) flags and View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR.inv() else flags or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        window.decorView.systemUiVisibility = flags
    }

    /** The status and navigation bars shown, or hidden (they come back with a swipe while hidden). */
    @Suppress("DEPRECATION")
    private fun bars(show: Boolean) {
        root.fitsSystemWindows = show
        if (Build.VERSION.SDK_INT >= 30) {
            val c = window.insetsController ?: return
            if (show) c.show(android.view.WindowInsets.Type.systemBars())
            else {
                c.hide(android.view.WindowInsets.Type.systemBars())
                c.systemBarsBehavior = android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = if (show) 0
                else View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }
        if (show) paintBars()
        root.requestApplyInsets()
    }

    private fun origin(url: String): String = try {
        val u = Uri.parse(url)
        val port = if (u.port == -1) "" else ":${u.port}"
        "${u.scheme?.lowercase()}://${u.host?.lowercase()}$port"
    } catch (e: Exception) { "" }

    private fun mine(url: String) = origin(url) == hostOrigin

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            if (url.startsWith("blob:") || url.startsWith("data:") || url.startsWith("about:") || mine(url)) return false
            try {
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this@DashboardActivity, "Nothing on this phone opens $url", Toast.LENGTH_LONG).show()
            }
            return true
        }

        override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
            pageOrigin = origin(url)
        }

        override fun onPageFinished(view: WebView, url: String) {
            pageOrigin = origin(url)
            if (!mine(url)) return
            view.evaluateJavascript(SAVE_BLOBS, null)
            addMarks()
            // The sign-in page: signed in here with the words this phone keeps (once each time the app is opened).
            view.evaluateJavascript("(function(){var f=document.querySelector('form[action=\"/login\"]');" +
                                    "return f ? (window.DDV && DDV.usable ? 'sealed' : 'plain') : ''})()") { kind ->
                val k = kind.trim('"')
                if (k.isEmpty() || triedSignIn) return@evaluateJavascript
                triedSignIn = true
                signIn(k == "sealed")
            }
        }
    }

    /** Hands the shared links to the dashboard's Bookmarks page (app.js ddAddMarks), once it is the signed-in dashboard. */
    private fun addMarks() {
        val text = marksToAdd ?: return
        web.evaluateJavascript("(function(t){if(window.ddAddMarks)return ddAddMarks(t)?'done':'';" +
                               "return document.querySelector('form[action=\"/login\"]')||!document.getElementById('nav')?'':'old'})" +
                               "(${JSONObject.quote(text)})") { said ->
            when (said.trim('"')) {
                "done" -> marksToAdd = null
                "old" -> {
                    marksToAdd = null
                    Toast.makeText(this, "Your DDrive host is too old to take bookmarks from the app: update it first.", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun signIn(canSeal: Boolean) {
        val phrase = store.phrase() ?: return
        if (store.e2e && !canSeal) {
            AlertDialog.Builder(this).setTitle("Use the https address")
                .setMessage("Your account is end-to-end encrypted, and its files are opened on this phone. That needs a secure " +
                            "address: ${store.host} isn't one. In Settings › This phone, forget the host and connect with its https address.")
                .setPositiveButton("OK", null).show()
            return
        }
        // End-to-end: the page makes the keys and sends only a sign-in key. The owner: the words are sent, the earlier way.
        val plain = if (store.e2e) "false" else "true"
        web.evaluateJavascript("(function(w,plain){var f=document.querySelector('form[action=\"/login\"]');if(!f)return;" +
                               "f.querySelector('textarea').value=w;if(plain){f.dataset.plain='1';f.submit();}" +
                               "else if(f.requestSubmit)f.requestSubmit();else f.dispatchEvent(new Event('submit',{cancelable:true}));})" +
                               "(${JSONObject.quote(phrase)},$plain)", null)
    }

    private inner class Chrome : WebChromeClient() {
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            filePick?.onReceiveValue(null)
            filePick = callback
            val pick = params.createIntent().apply {
                if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                addCategory(Intent.CATEGORY_OPENABLE)
            }
            return try {
                startActivityForResult(Intent.createChooser(pick, null), PICK_FILES)
                true
            } catch (e: ActivityNotFoundException) {
                filePick = null
                false
            }
        }

        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            fullScreen?.let { root.removeView(it) }
            fullScreen = view
            fullScreenDone = callback
            root.addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            web.visibility = View.GONE
            bars(false)                          // (a video full screen: the whole screen, the phone's bars too)
        }

        override fun onHideCustomView() {
            fullScreen?.let { root.removeView(it) }
            fullScreen = null
            fullScreenDone = null
            web.visibility = View.VISIBLE
            bars(true)
        }

        override fun onPermissionRequest(request: PermissionRequest) = request.deny()      // (no camera or microphone for the page)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION") super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != PICK_FILES) return
        val callback = filePick ?: return
        filePick = null
        if (resultCode != RESULT_OK || data == null) return callback.onReceiveValue(null)
        val clip = data.clipData
        val uris = if (clip != null) Array(clip.itemCount) { clip.getItemAt(it).uri } else data.data?.let { arrayOf(it) }
        callback.onReceiveValue(uris)
    }

    // ------------------------------------------------------------ files the page hands over
    /** A file at an address of the host (the drive's own files): the phone's download manager fetches it, signed in. */
    private fun download(url: String, disposition: String?, mime: String?) {
        if (!mine(url)) return
        val name = named.remove(url) ?: nameIn(disposition) ?: URLUtil.guessFileName(url, disposition, mime)
        try {
            val req = DownloadManager.Request(Uri.parse(url))
                .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "DDrive/$name")
            if (mime != null) req.setMimeType(mime)
            getSystemService(DownloadManager::class.java).enqueue(req)
            Toast.makeText(this, "Downloading $name to Downloads/DDrive", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "That can't be downloaded: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    /** Names the page gave its download links (<a download="name">), by address. */
    private val named = ConcurrentHashMap<String, String>()

    /** The file name in a Content-Disposition header, also the UTF-8 one (filename*=UTF-8''…), which Android doesn't read. */
    private fun nameIn(disposition: String?): String? {
        if (disposition.isNullOrEmpty()) return null
        Regex("""filename\*\s*=\s*UTF-8''([^;]+)""", RegexOption.IGNORE_CASE).find(disposition)?.let {
            return try { java.net.URLDecoder.decode(it.groupValues[1].trim(), "UTF-8") } catch (e: Exception) { null }
        }
        return Regex("""filename\s*=\s*"?([^";]+)"?""", RegexOption.IGNORE_CASE).find(disposition)?.groupValues?.get(1)?.trim()
    }

    private val saving = ConcurrentHashMap<Int, Pair<OutputStream, () -> Unit>>()
    private val saveIds = AtomicInteger()

    /** Somewhere in Downloads/DDrive to write a file the page opened itself (an end-to-end account's). */
    private fun newDownload(name: String, mime: String): Pair<OutputStream, () -> Unit> {
        val safe = name.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifEmpty { "download" }
        if (Build.VERSION.SDK_INT >= 29) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, safe)
                if (mime.isNotEmpty()) put(MediaStore.Downloads.MIME_TYPE, mime)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/DDrive")
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw IllegalStateException("no room for it")
            val out = contentResolver.openOutputStream(uri) ?: throw IllegalStateException("it can't be written")
            return out to {
                contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            }
        }
        val public = checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED
        val dir = File(if (public) Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
                       else getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "DDrive").apply { mkdirs() }
        var file = File(dir, safe)
        var i = 2
        while (file.exists()) file = File(dir, safe.substringBeforeLast('.') + " ($i)" + (if ('.' in safe) "." + safe.substringAfterLast('.') else "")).also { i++ }
        return FileOutputStream(file) to {}
    }

    /** What the page may ask of the app. Only the host's own pages are listened to. */
    private inner class Bridge {
        private fun ours() = pageOrigin == hostOrigin

        @JavascriptInterface fun version(): String = this@DashboardActivity.version()

        @JavascriptInterface fun settings() {
            if (!ours()) return
            runOnUiThread { startActivity(Intent(this@DashboardActivity, MainActivity::class.java)) }
        }

        @JavascriptInterface fun signOut() {
            if (!ours()) return
            runOnUiThread { askSignOut() }
        }

        @JavascriptInterface fun named(url: String, name: String) {
            if (ours() && name.isNotEmpty()) named[url] = name
        }

        @JavascriptInterface fun saveStart(name: String, mime: String): Int {
            if (!ours()) return -1
            return try {
                val id = saveIds.incrementAndGet()
                saving[id] = newDownload(name, mime)
                id
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this@DashboardActivity, "It can't be saved: ${e.message}", Toast.LENGTH_LONG).show() }
                -1
            }
        }

        @JavascriptInterface fun saveChunk(id: Int, b64: String): Boolean {
            val s = saving[id] ?: return false
            return try {
                s.first.write(Base64.decode(b64, Base64.DEFAULT))
                true
            } catch (e: Exception) {
                saving.remove(id)
                runCatching { s.first.close() }
                false
            }
        }

        @JavascriptInterface fun saveEnd(id: Int, name: String) {
            val s = saving.remove(id) ?: return
            runCatching { s.first.close() }
            runCatching { s.second() }
            runOnUiThread { Toast.makeText(this@DashboardActivity, "Saved $name to Downloads/DDrive", Toast.LENGTH_SHORT).show() }
        }
    }

    private fun askSignOut() {
        AlertDialog.Builder(this).setTitle("Sign out on this phone?")
            .setMessage("DDrive forgets the host and your words on this phone, and folder syncing stops. Nothing is deleted, on the phone or on the drive.")
            .setPositiveButton("Sign out") { _, _ ->
                web.evaluateJavascript("fetch('/api/logout',{method:'POST',credentials:'same-origin'," +
                                       "headers:{'X-DD':'1','Content-Type':'application/json'},body:'{}'}).catch(function(){})", null)
                web.postDelayed({
                    forgetAll(this, store)
                    startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_NEW_TASK))
                    finish()
                }, 600)
            }.setNegativeButton("Cancel", null).show()
    }

    companion object {
        const val EXTRA_PAGE = "page"
        const val EXTRA_MARKS = "marks"
        private const val PICK_FILES = 7

        /** Forget the host on this phone: its address and words, folder syncing, and what the page kept. */
        fun forgetAll(context: Context, store: Store) {
            store.sync = false
            SyncService.stop(context)
            store.forget()
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            WebStorage.getInstance().deleteAllData()          // (an end-to-end account's key and list in the page's storage too)
        }

        /** Files the page opens itself (an end-to-end account's: blob: addresses) are handed to the app in pieces. */
        private val SAVE_BLOBS = """
            (function () {
              if (window.__ddApp || !window.DDriveApp) return; window.__ddApp = 1;
              // The files behind the page's blob: addresses, kept as they are made (the page may not fetch blob:
              // addresses, and lets go of one as soon as it has handed it over): a while, for the app to read.
              const made = new Map(), create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
              URL.createObjectURL = function (o) { const u = create(o); if (o instanceof Blob) made.set(u, o); return u; };
              URL.revokeObjectURL = function (u) { setTimeout(function () { made.delete(u); revoke(u); }, 120000); };
              async function save(url, name) {
                try {
                  const b = made.get(url);
                  if (!b) throw new Error('the file is no longer there');
                  const id = DDriveApp.saveStart(name || 'download', b.type || '');
                  if (id < 0) return;
                  const C = 393216;
                  for (let i = 0; i < b.size || i === 0; i += C) {
                    const a = new Uint8Array(await b.slice(i, i + C).arrayBuffer());
                    let s = ''; for (let j = 0; j < a.length; j += 0x8000) s += String.fromCharCode.apply(null, a.subarray(j, j + 0x8000));
                    if (!DDriveApp.saveChunk(id, btoa(s))) return;
                    if (!b.size) break;
                  }
                  DDriveApp.saveEnd(id, name || 'download');
                } catch (e) { console.log('DDrive: could not save ' + name + ': ' + e); }
              }
              const click = HTMLAnchorElement.prototype.click;
              HTMLAnchorElement.prototype.click = function () {
                if (this.href && this.href.startsWith('blob:')) { save(this.href, this.download || this.textContent.trim()); return; }
                if (this.download && this.href) DDriveApp.named(this.href, this.download);     // (the name for the download manager)
                return click.call(this);
              };
              document.addEventListener('click', function (e) {
                const a = e.target.closest && e.target.closest('a[href^="blob:"]');
                if (a) { e.preventDefault(); save(a.href, a.download || 'download'); }
              }, true);
            })();
        """.trimIndent()
    }
}
