package com.xo.findx

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/*
 * Managing this phone from your DDrive (its FindX page) or another of your devices: what the app's
 * screens do, done by sealed commands (only your devices and your browser have the command key).
 *
 * What a remote change can't do is make the phone harder to find: FindX can't be switched off,
 * unlinked or erased from afar, so somebody who got into your DDrive still can't silence it.
 *
 * The page sees how the phone is set up and how healthy it is in every report (settings, problems),
 * and the rest (battery use by day and hour, what FindX did lately, live links, spots) in a sealed
 * stats file the phone keeps on the drive, /FindX/s/<name>.fxs, sent with the history every hour
 * and at once when the page asks.
 */
object Remote {
    /** How the phone is set up, for the page's Settings tab. */
    fun settings(ctx: Context): JSONObject = JSONObject()
        .put("name", Store.deviceName(ctx)).put("myName", Store.myName(ctx))
        .put("history", Store.historyOn(ctx)).put("detail", Store.detail(ctx)).put("beacon", Store.beaconOn(ctx))
        .put("lostMessage", Store.lostMessage(ctx)).put("on", Store.on(ctx)).put("v", BuildConfig.VERSION_NAME)
        .put("model", "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}").put("android", android.os.Build.VERSION.RELEASE)

    /** What keeps this phone from being found as well as it could be (for a warning on the page). */
    fun problems(ctx: Context): List<String> = buildList {
        if (!Permissions.location(ctx)) add("location")
        else if (!Permissions.background(ctx)) add("background")
        if (!Locator.enabled(ctx)) add("locationOff")
        if (!Permissions.nearby(ctx)) add("nearby")
        if (!Permissions.notifications(ctx)) add("notifications")
        if (!Permissions.dnd(ctx)) add("dnd")
        if (!Permissions.unrestricted(ctx)) add("battery")
        if (!Permissions.fullScreen(ctx)) add("fullScreen")
        if (!Store.beaconOn(ctx)) add("beaconOff")
    }

    /** The stats file's content. */
    fun stats(ctx: Context): JSONObject {
        val p = Meter.profile(ctx)
        val now = System.currentTimeMillis()
        return JSONObject().put("v", 1).put("d", Store.device(ctx)).put("t", now)
            .put("profile", JSONObject().put("source", p.source).put("capacity", p.capacityMah).put("capacitySource", p.capacitySource)
                .put("cpu", p.cpuMa).put("gps", p.gpsMa).put("wifi", p.wifiMa).put("cell", p.cellMa).put("bleLow", p.bleLowMa)
                .put("bleFast", p.bleFastMa).put("scan", p.scanMa).put("sensor", p.sensorMa).put("ring", p.ringMa))
            .put("days", JSONArray().apply { Meter.days(ctx, 14).forEach { (t, u) -> put(JSONArray().put(t).put(u.json())) } })
            .put("hours", JSONArray().apply { Meter.hours(ctx, 24).forEach { (t, u) -> put(JSONArray().put(t).put(u.json())) } })
            .put("log", JSONArray().apply { Log.lines(ctx).take(40).forEach { (t, s) -> put(JSONArray().put(t).put(s)) } })
            .put("shares", JSONArray().apply {
                Store.shares(ctx).filter { it.live(now) }.forEach {
                    put(JSONObject().put("id", it.id).put("key", it.key).put("name", it.name).put("until", it.until).put("approx", it.approximate).put("made", it.made))
                }
            })
            .put("spots", JSONArray().apply { Store.spots(ctx).forEach { put(it.name) } })
            .put("history", History.summary(ctx))
    }

    fun statsPath(keys: FxKeys, device: String) = "${Store.ROOT}/s/${keys.name("s|$device")}.fxs"
    fun circlesPath(keys: FxKeys, device: String) = "${Store.ROOT}/c/${keys.name("c|$device")}.fxc"

    /** Sends the stats file now. */
    fun uploadStats(ctx: Context, link: Link, keys: FxKeys) {
        link.putFile(statsPath(keys, Store.device(ctx)), keys.seal(keys.report, "findx stats", stats(ctx).toString().toByteArray()))
        Store.prefs(ctx).edit().putLong("statsAt", System.currentTimeMillis()).apply()
    }

    /**
     * The commands that manage the phone (the ones that find it are the Service's). Returns what was
     * done, for the Activity list, or null when it isn't one of these.
     */
    fun act(ctx: Context, c: Command): String? {
        val a = c.args
        return when (c.what) {
            "set" -> {
                val changed = ArrayList<String>()
                if (a.has("name")) { Store.setDeviceName(ctx, a.optString("name")); changed.add("name") }
                if (a.has("myName")) { Store.setMyName(ctx, a.optString("myName")); changed.add("name in circles") }
                if (a.has("history")) { Store.setHistoryOn(ctx, a.optBoolean("history")); changed.add("history") }
                if (a.has("detail")) { Store.setDetail(ctx, a.optInt("detail")); changed.add("history detail") }
                if (a.has("beacon")) { Store.setBeaconOn(ctx, a.optBoolean("beacon")); Beacon.update(ctx); changed.add("findable nearby") }
                if (a.has("lostMessage")) { Store.setLost(ctx, Store.lost(ctx), a.optString("lostMessage")); if (Store.lost(ctx)) Service.lostNotice(ctx); changed.add("lost message") }
                // Turning FindX off, unlinking or erasing are not here on purpose: only on the phone, with its screen lock.
                Store.prefs(ctx).edit().putBoolean("circlesChanged", true).apply()
                "Settings changed from afar: " + changed.joinToString(", ").ifEmpty { "nothing" }
            }
            "share" -> {
                val s = Store.Share(a.getString("id"), a.getString("key"), a.optString("name").take(40), a.optLong("until"), a.optBoolean("approx"),
                    System.currentTimeMillis())
                if (!Regex("^[0-9a-f]{32}$").matches(s.id) || FxKeys.unb64url(s.key).size != 32) return "Refused a live link with a bad id or key"
                Store.saveShares(ctx, Store.shares(ctx).filter { it.id != s.id } + s)
                Service.running?.report(Store.lastReport(ctx)?.fix)
                "Started a live link from afar" + if (s.name.isNotEmpty()) " (for ${s.name})" else ""
            }
            "unshare" -> {
                Store.shares(ctx).firstOrNull { it.id == a.optString("id") }?.let { Shares.stop(ctx, it) }
                "Stopped a live link from afar"
            }
            "stats" -> "Sent its battery use and activity"          // (sent after every command anyway)
            "join" -> runCatching { Circles.join(ctx, a.getString("invite")); "Joined a circle from afar" }.getOrElse { "Couldn't join the circle: ${it.message}" }
            "leave" -> Circles.get(ctx, a.optString("circle"))?.let { Circles.leave(ctx, it); "Left the circle \"${it.name}\" from afar" } ?: "Not in that circle"
            "circleRing" -> Circles.get(ctx, a.optString("circle"))?.let { Circles.setCanRing(ctx, it, a.optBoolean("on")); "Circle ringing ${if (a.optBoolean("on")) "allowed" else "not allowed"} for \"${it.name}\"" }
            "circles" -> { Circles.all(ctx).forEach { runCatching { Circles.refreshInfo(ctx, it) } }; "Looked at its circles' places again" }
            else -> null
        }
    }

    val KINDS = setOf("set", "share", "unshare", "stats", "join", "leave", "circleRing", "circles")
}
