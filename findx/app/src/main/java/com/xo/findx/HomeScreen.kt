package com.xo.findx

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

/** "This phone": where FindX thinks it is, how sure, and what keeping it findable costs. */
@Composable
fun HomeScreen(go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    Service.version.intValue                      // redraw when something changes
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { delay(5_000); now = System.currentTimeMillis() } }
    val r = Store.lastReport(ctx)
    val locating = Service.locating.value

    Page("FindX", Store.deviceName(ctx), actions = { IconAction(FxIcons.Gear, "Settings") { go(Screen.Settings) } }) {
        Problems(go)
        NewerFindX()

        // Where it is.
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(26.dp)).background(Surface2).clickable { go(Screen.Map(Store.device(ctx))) }.padding(20.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(52.dp).clip(CircleShape).background(Red), contentAlignment = Alignment.Center) {
                    Icon(FxIcons.Pin, null, tint = OnRed, modifier = Modifier.size(28.dp))
                }
                HGap(14)
                Column(Modifier.weight(1f)) {
                    Text(
                        when {
                            locating -> "Pinning it down…"
                            r?.fix == null -> "No position yet"
                            else -> Service.describe(r).ifEmpty { "Here" }
                        },
                        color = White, fontSize = 20.sp, fontWeight = FontWeight.Bold, lineHeight = 24.sp,
                    )
                    val f = r?.fix
                    Text(
                        if (f == null) "Tap Pin it down, or open the map" else
                            (if (r.settled) "Pinned to ±" else "Within ±") + metres(f.acc) + " · " + ago(f.t, now),
                        color = Grey, fontSize = 14.sp, modifier = Modifier.padding(top = 2.dp),
                    )
                }
                Icon(FxIcons.Chevron, null, tint = Grey)
            }
            Gap(16)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Tile(FxIcons.Target, if (locating) "Working…" else "Pin it down", Modifier.weight(1f)) {
                    if (!locating) Service.start(ctx, Service.ACTION_LOCATE)
                }
                Tile(FxIcons.Bell, if (Ring.ringing.value) "Stop" else "Test ring", Modifier.weight(1f)) {
                    if (Ring.ringing.value) Ring.stop(ctx) else Ring.start(ctx, 5_000)
                }
                Tile(FxIcons.Map, "Map", Modifier.weight(1f)) { go(Screen.Map(Store.device(ctx))) }
            }
        }

        if (r != null && (r.pose != Pose.Unknown || r.spot != null || r.battery >= 0)) {
            Section("How it lies")
            CardGroup {
                r.spot?.let { Row2("Spot", "Matches your saved spot \"$it\" (${(r.spotScore * 100).toInt()}% alike)") }
                if (r.spot != null && r.pose != Pose.Unknown) RowDivider()
                if (r.pose != Pose.Unknown) Row2(r.pose.words, listOfNotNull(
                    if (r.covered) "something covers the screen" else null,
                    r.lux?.let { if (it < 3) "dark around it" else if (it > 500) "in bright light" else null },
                    if (r.stillSince > 0) "still since ${time(r.stillSince)}" else null,
                ).joinToString(", ").replaceFirstChar { it.uppercase() }.ifEmpty { null })
                if (r.battery >= 0) { if (r.spot != null || r.pose != Pose.Unknown) RowDivider(); Row2("Battery ${r.battery}%", if (r.charging) "Charging" else null) }
            }
        }

        Section("History")
        CardGroup {
            Row2("Today", "Where this phone has been since midnight", onClick = { go(Screen.Map(Store.device(ctx), "today")) }) { Icon(FxIcons.Chevron, null, tint = Grey) }
            RowDivider()
            Row2("The last 7 days", onClick = { go(Screen.Map(Store.device(ctx), "week")) }) { Icon(FxIcons.Chevron, null, tint = Grey) }
            RowDivider()
            val pending = History.pendingCount(ctx)
            Row2(
                if (!Store.historyOn(ctx)) "History is off" else if (Store.words(ctx) == null) "Kept on this phone only" else if (pending == 0) "All of it is on your DDrive" else "$pending ${if (pending == 1) "hour" else "hours"} still to go to DDrive",
                if (Store.words(ctx) == null) "Link your DDrive to keep it for good" else "Sealed with your words: DDrive sees random bytes",
            )
        }

        Section("Battery")
        CardGroup {
            val p = remember { Meter.profile(ctx) }
            val today = Meter.today(ctx)
            Row2("FindX used ${Energy.pct(Energy.percent(today, p))} of your battery today",
                "${Energy.words(Energy.total(today, p))} since midnight, measured. Lying still, FindX waits on the phone's motion chip, which uses next to nothing.",
                onClick = { go(Screen.Battery) }) { Icon(FxIcons.Chevron, null, tint = Grey) }
        }
        Gap(20)
    }
}

/** Things to set right before FindX can do all it should. */
@Composable
fun Problems(go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val items = buildList {
        if (!Store.on(ctx)) add(Triple("FindX is off", "This phone can't be found. Turn it on in Settings.", Screen.Settings))
        if (!Permissions.location(ctx) || !Permissions.background(ctx)) add(Triple("Allow location all the time", "Without it this phone can't be found while FindX is closed.", Screen.Allow))
        else if (!Permissions.nearby(ctx) || !Permissions.notifications(ctx) || !Permissions.unrestricted(ctx) || !Permissions.dnd(ctx)) add(Triple("Finish setting up", "A few more things FindX needs to work everywhere.", Screen.Allow))
        if (!Locator.enabled(ctx)) add(Triple("Location is off", "Turn on Location in the phone's quick settings.", Screen.Allow))
        if (Store.words(ctx) == null) add(Triple("Link your DDrive", "To find this phone from anywhere, see it live and keep its history.", Screen.LinkDDrive))
        else Store.linkProblem(ctx)?.let { add(Triple("Can't reach your DDrive", it, Screen.LinkDDrive)) }
    }
    if (items.isEmpty()) return
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(Warn)) {
        items.forEachIndexed { i, (title, text, screen) ->
            if (i > 0) RowDivider()
            Row2(title, text, onClick = { go(screen) }) { Icon(FxIcons.Chevron, null, tint = Grey) }
        }
    }
    Gap(14)
}

/** A newer FindX is out (your DDrive said so): where to get it. */
@Composable
fun NewerFindX() {
    val ctx = LocalContext.current
    val (v, url) = (Store.prefs(ctx).getString("newest", "") ?: "").split("|", limit = 2).let { if (it.size == 2) it[0] to it[1] else return }
    if (!Versions.newer(v, BuildConfig.VERSION_NAME) || url.isEmpty()) return
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(Surface3)) {
        Row2("FindX $v is out", "This phone has ${BuildConfig.VERSION_NAME}. Tap to download it, then open it to update.", onClick = {
            runCatching { ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) }
        }) { Icon(FxIcons.Chevron, null, tint = Grey) }
    }
    Gap(14)
}

/** A big header, then scrolling content (the tabs' pages). */
@Composable
fun Page(title: String, subtitle: String? = null, back: (() -> Unit)? = null, actions: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit = {}, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().background(Black).safeDrawingPadding()) {
        LargeHeader(title, subtitle, back, actions)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(top = 8.dp, bottom = 20.dp)) { content() }
    }
}

@Composable
fun Tile(icon: ImageVector, label: String, modifier: Modifier = Modifier, tint: Color = Red, onClick: () -> Unit) {
    Column(
        modifier.clip(RoundedCornerShape(18.dp)).background(Surface3).clickable(onClick = onClick).padding(vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(24.dp))
        Gap(6)
        Text(label, color = White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}

fun metres(m: Double): String = when {
    m < 1 -> "${(m * 100).toInt()} cm"
    m < 10 -> "%.1f m".format(m).replace(".0 m", " m")
    m < 1000 -> "${m.toInt()} m"
    else -> "%.1f km".format(m / 1000)
}

fun ago(t: Long, now: Long = System.currentTimeMillis()): String {
    val s = ((now - t) / 1000).coerceAtLeast(0)
    return when {
        s < 10 -> "just now"
        s < 60 -> "$s s ago"
        s < 3600 -> "${s / 60} min ago"
        s < 86_400 -> "${s / 3600} h ago"
        else -> "${s / 86_400} d ago"
    }
}

fun time(t: Long): String = java.text.DateFormat.getTimeInstance(java.text.DateFormat.SHORT).format(java.util.Date(t))
