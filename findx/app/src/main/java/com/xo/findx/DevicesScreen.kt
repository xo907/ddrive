package com.xo.findx

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Your other phones and tablets linked to the same DDrive, as they last reported. */
@Composable
fun DevicesScreen(go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    Service.version.intValue
    var problem by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        while (true) {
            problem = withContext(Dispatchers.IO) {
                if (Store.words(ctx) == null) null
                else runCatching { Peers.refresh(ctx); null }.getOrElse { if (it is Link.NoRelay) "norelay" else it.message }
            }
            delay(20_000)
        }
    }
    Page("Devices", "Linked to the same DDrive") {
        when {
            Store.words(ctx) == null -> Empty("Link your DDrive", "Your phones and tablets find each other through your DDrive: link this phone, then the others with the same 24 words.") {
                Primary("Link DDrive") { go(Screen.LinkDDrive) }
            }
            problem == "norelay" -> Empty("Live finding needs DDrive's FindX update", "Your DDrive keeps FindX's history already. Seeing your other devices live, and ringing them from here, comes with the DDrive version that has FindX's live relay.") {}
            else -> {
                problem?.let { Body("Couldn't reach DDrive: $it", Red); Gap(10) }
                val peers = Store.peers(ctx).sortedByDescending { it.report?.t ?: it.seen }
                if (peers.isEmpty()) Empty("No other devices yet", "Install FindX on your other phones and tablets and link them with the same 24 words. They show up here.") {}
                else CardGroup {
                    peers.forEachIndexed { i, p ->
                        if (i > 0) RowDivider()
                        val r = p.report
                        Row2(
                            p.name,
                            listOfNotNull(
                                r?.let { Service.describe(it).ifEmpty { null } },
                                r?.let { "seen ${ago(it.t)}" } ?: "not heard from yet",
                                r?.battery?.takeIf { it >= 0 }?.let { "$it%" },
                            ).joinToString(" · "),
                            onClick = { go(Screen.Device(p.device)) },
                        ) { Icon(FxIcons.Chevron, null, tint = Grey) }
                    }
                }
            }
        }
    }
}

@Composable
fun Empty(title: String, text: String, action: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Surface2).padding(22.dp)) {
        Text(title, color = White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
        Gap(6)
        Body(text)
        Gap(14)
        action()
    }
}

/** One of your other devices: where it is, and what you can ask of it. */
@Composable
fun DeviceScreen(device: String, go: (Screen) -> Unit, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    Service.version.intValue
    val peer = Store.peers(ctx).firstOrNull { it.device == device }
    val r = peer?.report
    var said by remember { mutableStateOf<String?>(null) }
    var asking by remember { mutableStateOf(false) }
    var lostDialog by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { while (true) { withContext(Dispatchers.IO) { runCatching { Peers.refresh(ctx) } }; delay(if (Peers.live(device)) 3_000 else 15_000) } }

    fun ask(what: String, args: JSONObject = JSONObject(), done: String) {
        scope.launch {
            said = withContext(Dispatchers.IO) {
                runCatching {
                    (Service.running ?: throw IllegalStateException("FindX isn't running")).command(device, what, args)
                    if (what == "live" || what == "locate") Peers.markLive(device)
                    done
                }.getOrElse { it.message ?: "That didn't work" }
            }
        }
    }

    Page(peer?.name ?: "Device", r?.let { "Seen ${ago(it.t)}" + if (it.lost) " · lost mode" else "" } ?: "Not heard from yet", back = back) {
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(26.dp)).background(Surface2).padding(20.dp)) {
            Text(r?.let { Service.describe(it).ifEmpty { if (it.fix != null) "On the map" else "No position yet" } } ?: "No position yet",
                color = White, fontSize = 21.sp, fontWeight = FontWeight.Bold)
            r?.fix?.let { f ->
                Text((if (r.settled) "Pinned to ±" else "Within ±") + metres(f.acc) + " · " + "%.6f, %.6f".format(f.lat, f.lon), color = Grey, fontSize = 14.sp, modifier = Modifier.padding(top = 3.dp))
            }
            if (r != null && r.battery >= 0) Text("Battery ${r.battery}%" + if (r.charging) ", charging" else "", color = if (r.battery <= 15 && !r.charging) Red else Grey, fontSize = 14.sp)
            Gap(16)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Tile(FxIcons.Bell, if (r?.ringing == true) "Stop" else "Ring", Modifier.weight(1f)) {
                    if (r?.ringing == true) ask("stop", done = "Asked it to stop") else ask("ring", done = "Ringing it now")
                }
                Tile(FxIcons.Target, "Pin down", Modifier.weight(1f)) { ask("locate", done = "Asked it to pin itself down: watch the map") }
                Tile(FxIcons.Map, "Live map", Modifier.weight(1f)) { go(Screen.Map(device)) }
            }
            Gap(10)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Tile(FxIcons.Radar, "Find nearby", Modifier.weight(1f)) { go(Screen.FindOne(device)) }
                Tile(FxIcons.Flag, if (r?.lost == true) "Found it" else "Lost mode", Modifier.weight(1f)) {
                    if (r?.lost == true) ask("found", done = "Lost mode is off") else lostDialog = true
                }
                Tile(FxIcons.OpenLink, "Directions", Modifier.weight(1f)) {
                    r?.fix?.let { f -> runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:${f.lat},${f.lon}?q=${f.lat},${f.lon}(${Uri.encode(peer?.name ?: "Device")})"))) } }
                }
            }
        }
        said?.let { Gap(12); Body(it, White) }

        Section("Precisely where")
        Body("Pin down asks it for its most exact position: satellites averaged while it lies still, its saved spot from the Wi-Fi around it, and which way up it lies. When you're close, Find nearby leads you to it over Bluetooth, and to the centimetre with Ultra-Wideband on phones that have it. Ring makes it loud even on silent.")
    }

    if (lostDialog) {
        var msg by remember { mutableStateOf(Store.lostMessage(ctx)) }
        AlertDialog(
            onDismissRequest = { lostDialog = false }, containerColor = Line, shape = RoundedCornerShape(28.dp),
            title = { Text("Lost mode", color = White, fontWeight = FontWeight.Bold) },
            text = {
                Column {
                    Body("It shows your message on its lock screen, reports where it is more often, and its beacon calls out to your other devices.")
                    Gap(12)
                    TextBox(msg, { msg = it.take(200) }, "Message (a number to call?)")
                }
            },
            confirmButton = {
                LinkButton("Turn on") {
                    lostDialog = false
                    Store.setLost(ctx, Store.lost(ctx), msg)
                    ask("lost", JSONObject().put("message", msg), "Lost mode is on")
                }
            },
            dismissButton = { LinkButton("Cancel", Grey2) { lostDialog = false } },
        )
    }
}

