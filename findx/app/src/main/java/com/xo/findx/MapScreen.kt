package com.xo.findx

import android.annotation.SuppressLint
import android.content.Context
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebViewAssetLoader
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * The map (assets/map: the same page DDrive's dashboard shows), in a WebView that answers its questions
 * from FindX itself. The page and the map engine are part of the app; only the map's pictures (tiles)
 * come from the internet, from OpenFreeMap, which keeps no logs of who looks where and sets no cookies.
 * The WebView keeps no cookies either, and its cache of tiles stays in the app.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun MapScreen(device: String, span: String = "now", back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = remember { CoroutineScope(SupervisorJob() + Dispatchers.Default) }
    val bridge = remember { Bridge(ctx.applicationContext, device, span, scope) }
    BackHandler { back() }
    val density = androidx.compose.ui.platform.LocalDensity.current
    val topInset = (WindowInsets.statusBars.getTop(density) / density.density).toInt()
    val bottomInset = (WindowInsets.navigationBars.getBottom(density) / density.density).toInt()
    bridge.onBack = back
    DisposableEffect(Unit) { onDispose { bridge.close(); scope.cancel() } }
    Box(Modifier.fillMaxSize().background(Black)) {
        AndroidView(factory = { c ->
            val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(c)).build()
            // For looking inside the map while building FindX (a hidden setting, off unless set by hand on a debug build).
            if (c.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0 && Store.prefs(c).getBoolean("devtools", false))
                WebView.setWebContentsDebuggingEnabled(true)
            WebView(c).apply {
                // Without this the page's viewport is 0 px tall in Compose (the WebView is measured as wrap-content).
                layoutParams = android.view.ViewGroup.LayoutParams(android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.MATCH_PARENT)
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true             // (the map remembers its light or dark setting; kept in the app only)
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.cacheMode = WebSettings.LOAD_DEFAULT
                settings.setGeolocationEnabled(false)
                android.webkit.CookieManager.getInstance().setAcceptCookie(false)
                webViewClient = object : WebViewClient() {
                    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                        loader.shouldInterceptRequest(request.url)
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true   // links (map credits) stay out
                }
                webChromeClient = object : android.webkit.WebChromeClient() {
                    override fun onConsoleMessage(m: android.webkit.ConsoleMessage): Boolean {
                        android.util.Log.i("FindXMap", "${m.message()} (${m.sourceId().substringAfterLast('/')}:${m.lineNumber()})")
                        return true
                    }
                }
                bridge.web = this
                addJavascriptInterface(bridge, "FindXApp")
                // The page draws under the status bar; it is told how tall that is (WebView doesn't know).
                loadUrl("https://appassets.androidplatform.net/assets/map/index.html?top=$topInset&bottom=$bottomInset")
            }
        }, modifier = Modifier.fillMaxSize())
    }
}

/** What the map page asks, answered on a background thread and handed back with FX.reply. */
private class Bridge(private val ctx: Context, private val first: String, private val span: String, private val scope: CoroutineScope) {
    @Volatile var web: WebView? = null
    @Volatile var onBack: (() -> Unit)? = null
    private val me = Store.device(ctx)
    private val watchingSent = ConcurrentHashMap<String, Long>()

    @JavascriptInterface
    fun request(id: String, method: String, args: String) {
        scope.launch {
            val a = runCatching { JSONObject(args) }.getOrDefault(JSONObject())
            val answer: Any? = runCatching { answer(method, a) }.getOrElse { JSONObject().put("error", it.message ?: "That didn't work") }
            val json = JSONObject.quote(answer?.toString() ?: "null")
            withContext(Dispatchers.Main) { web?.evaluateJavascript("FX.reply(${JSONObject.quote(id)}, $json)", null) }
        }
    }

    private suspend fun answer(method: String, a: JSONObject): Any? = when (method) {
        "initial" -> JSONObject().put("id", first).put("span", span)
        "devices" -> JSONArray().apply {
            put(JSONObject().put("id", me).put("name", Store.deviceName(ctx)).put("me", true))
            Store.peers(ctx).forEach { put(JSONObject().put("id", it.device).put("name", it.name).put("seen", it.seen)) }
            // The people in your circles.
            Circles.all(ctx).forEach { c ->
                Circles.membersCached(ctx, c, 20_000).forEach { m ->
                    put(JSONObject().put("id", "c:${c.id}:${m.member}").put("name", m.person).put("seen", m.t).put("kind", "person").put("group", c.name))
                }
            }
        }
        "latest" -> latest(a.getString("id"))
        "history" -> history(a.getString("id"), a.getLong("from"), a.getLong("to"))
        "act" -> act(a.getString("id"), a.getString("what"), a.optJSONObject("args") ?: JSONObject())
        "watching" -> { watching(a.getString("id"), a.optBoolean("on")); JSONObject() }
        "back" -> { withContext(Dispatchers.Main) { onBack?.invoke() }; JSONObject() }
        else -> null
    }

    /** "c:<circle>:<member>": a person in one of your circles. */
    private fun person(id: String): Pair<Circle, String>? {
        val parts = id.split(":")
        if (parts.size != 3 || parts[0] != "c") return null
        return Circles.get(ctx, parts[1])?.let { it to parts[2] }
    }

    private fun latest(id: String): JSONObject? {
        if (id == me) return Store.lastReport(ctx)?.toJson()
        person(id)?.let { (c, member) ->
            val fresh = if ((watchingSent[id] ?: 0) > System.currentTimeMillis() - 120_000) 4_000L else 20_000L
            return Circles.membersCached(ctx, c, fresh).firstOrNull { it.member == member }?.asReport(id)
        }
        Peers.refreshIfStale(ctx, if (Peers.live(id)) 2_000 else 15_000)
        return Store.peers(ctx).firstOrNull { it.device == id }?.report?.toJson()
    }

    private suspend fun history(id: String, from: Long, to: Long): JSONArray = if (id.startsWith("c:")) JSONArray() else withContext(Dispatchers.IO) {
        val local = if (id == me) History.points(ctx, from, to) else emptyList()
        val link = Link.of(ctx)
        val have = local.map { Segments.hourOf(it.t) }.toSet()
        val fetched = ArrayList<Point>()
        if (link != null) {
            // Hours this phone doesn't keep (older ones, or another device's) come from DDrive. Other devices: a week at most here.
            val first = Segments.hourOf(if (id == me) from else maxOf(from, to - 7 * 86_400_000L))
            val hours = (first..Segments.hourOf(to)).filter { it !in have && (id != me || it < (History.hours(ctx).firstOrNull() ?: Long.MAX_VALUE)) }
            hours.chunked(6).forEach { group ->
                group.map { h -> scope.launch(Dispatchers.IO) { runCatching { History.fetch(ctx, link, id, h) }.getOrNull()?.let { synchronized(fetched) { fetched.addAll(it) } } } }
                    .forEach { it.join() }
            }
        }
        History.forMap((local + fetched).filter { it.t <= to && maxOf(it.t, it.until) >= from }.sortedBy { it.t })
    }

    private suspend fun act(id: String, what: String, args: JSONObject): JSONObject {
        person(id)?.let { (c, member) ->
            if (what != "ring") return JSONObject().put("said", "Only ringing, for people in your circles")
            withContext(Dispatchers.IO) { Circles.ring(ctx, c, member) }
            val name = Circles.membersCached(ctx, c, 60_000).firstOrNull { it.member == member }?.person ?: "their"
            return JSONObject().put("said", "Ringing $name's phone")
        }
        if (id == me) {
            val svc = Service.running
            when (what) {
                "ring" -> Ring.start(ctx, 15_000)
                "locate" -> { if (svc == null) throw IllegalStateException("FindX isn't running"); scope.launch { svc.locate("you") } }
                "live" -> Service.start(ctx, Service.ACTION_LOCATE)
                "lost" -> return JSONObject().put("said", "Lost mode is for your other devices. Use it from another phone or DDrive.")
            }
            return JSONObject().put("said", when (what) { "ring" -> "Ringing for 15 seconds"; "locate" -> "Pinning it down"; else -> "Done" })
        }
        val svc = Service.running ?: throw IllegalStateException("FindX isn't running")
        withContext(Dispatchers.IO) {
            svc.command(id, what, if (what == "lost") JSONObject().put("message", Store.lostMessage(ctx)) else args)
        }
        if (what == "live" || what == "locate") Peers.markLive(id)
        return JSONObject().put("said", when (what) {
            "ring" -> "Ringing it now"; "locate" -> "Asked it to pin itself down"; "live" -> "Live for 10 minutes"; "lost" -> "Lost mode is on"; else -> "Sent"
        })
    }

    /** Watching another device: ask it to go live (every minute while the map shows it, so it stops soon after). */
    private fun watching(id: String, on: Boolean) {
        if (!on || id == me) return
        val last = watchingSent[id] ?: 0
        if (System.currentTimeMillis() - last < 60_000) return
        watchingSent[id] = System.currentTimeMillis()
        person(id)?.let { (c, _) -> runCatching { Circles.members(ctx, c, watch = true) }; return }
        Peers.markLive(id)
        runCatching { Service.running?.command(id, "live", JSONObject().put("ms", 2 * 60_000L)) }
    }

    fun close() { web = null }
}

/** Your other devices, as the relay last told (sealed: opened here with your keys). */
object Peers {
    @Volatile private var refreshedAt = 0L
    private val liveUntil = ConcurrentHashMap<String, Long>()

    fun markLive(id: String) { liveUntil[id] = System.currentTimeMillis() + 3 * 60_000L }
    fun live(id: String) = (liveUntil[id] ?: 0) > System.currentTimeMillis()

    fun refreshIfStale(ctx: Context, maxAgeMs: Long) {
        if (System.currentTimeMillis() - refreshedAt < maxAgeMs) return
        runCatching { refresh(ctx) }
    }

    /** Asks the relay for your devices and their latest reports. Throws when it can't. */
    @Synchronized
    fun refresh(ctx: Context) {
        val link = Link.of(ctx) ?: return
        val keys = Store.keys(ctx) ?: return
        val me = Store.device(ctx)
        val old = Store.peers(ctx).associateBy { it.device }
        val got = link.devices(keys.group).mapNotNull { (device, card, rep) ->
            if (device == me) return@mapNotNull null
            val c = runCatching { JSONObject(String(keys.open(keys.report, "findx card", card))) }.getOrNull() ?: return@mapNotNull null
            if (c.optString("d") != device) return@mapNotNull null          // a card must be for the device it is filed under
            val r = rep.first?.let { b -> runCatching { Report.of(JSONObject(String(keys.open(keys.report, "findx report", b)))) }.getOrNull() }
                ?.takeIf { it.device == device }
            Store.Peer(device, c.optString("n", "Device"), r ?: old[device]?.report, if (rep.second > 0) rep.second else old[device]?.seen ?: 0, c.optBoolean("uwb"))
        }
        Store.savePeers(ctx, got)
        refreshedAt = System.currentTimeMillis()
        Service.changed(ctx)
    }
}
