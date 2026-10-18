package com.xo.findx

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
private fun Chevron() = Icon(FxIcons.Chevron, null, tint = Grey)

@Composable
private fun Note(text: String) = Text(text, color = Grey, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(start = 6.dp, top = 8.dp, end = 6.dp))

/** Bumps a counter whenever the app comes back to the front (after a trip to Android's settings). */
@Composable
fun resumed(): Int {
    var n by remember { mutableIntStateOf(0) }
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val o = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) n++ }
        owner.lifecycle.addObserver(o)
        onDispose { owner.lifecycle.removeObserver(o) }
    }
    return n
}

@Composable
fun SettingsScreen(go: (Screen) -> Unit, back: () -> Unit) {
    val ctx = LocalContext.current
    resumed()
    var on by remember { mutableStateOf(Store.on(ctx)) }
    var beacon by remember { mutableStateOf(Store.beaconOn(ctx)) }
    var history by remember { mutableStateOf(Store.historyOn(ctx)) }
    var detail by remember { mutableIntStateOf(Store.detail(ctx)) }
    var name by remember { mutableStateOf(Store.deviceName(ctx)) }
    var message by remember { mutableStateOf(Store.lostMessage(ctx)) }
    var erasing by remember { mutableStateOf(false) }

    Page("Settings", back = back) {
        CardGroup {
            Row2("Find this phone", if (on) "On: findable from your devices and DDrive" else "Off: nobody can find it, not even you", trailing = {
                FxSwitch(on) { want ->
                    if (want) { Store.setOn(ctx, true); on = true; Service.start(ctx) }
                    else Guard.activity(ctx)?.let { a -> Guard.ask(a, "Turn off FindX") { Store.setOn(ctx, false); on = false; Service.stop(ctx); Beacon.stop(ctx) } }
                }
            })
        }
        Note("Turning it off asks for your fingerprint or screen lock, so whoever has your phone can't.")

        Section("Appearance")
        CardGroup {
            Column(Modifier.padding(horizontal = 18.dp, vertical = 14.dp)) {
                Chips(listOf("Like the phone" to (Look.choice == 0), "Light" to (Look.choice == 1), "Dark" to (Look.choice == 2))) {
                    Look.choice = it; Store.setTheme(ctx, it)
                }
                Gap(8)
                Text("The map has its own: it follows day and night where the phone is, or is always light or dark (the sun button on the map).",
                    color = Grey, fontSize = 13.sp, lineHeight = 18.sp)
            }
        }

        Section("This phone")
        CardGroup {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                TextBox(name, { name = it.take(40); Store.setDeviceName(ctx, name) }, "Name your devices see")
            }
            RowDivider()
            Row2("Spots", "Places in your home it can tell apart, like \"desk\" or \"nightstand\" (${Store.spots(ctx).size} saved)", onClick = { go(Screen.Spots) }) { Chevron() }
            RowDivider()
            Row2("Findable nearby", "A Bluetooth beacon only your devices recognise, so they can find it even with no internet. Costs about 1% battery a day.", trailing = {
                FxSwitch(beacon) { beacon = it; Store.setBeaconOn(ctx, it); Beacon.update(ctx) }
            })
        }

        Section("History")
        CardGroup {
            Row2("Keep history", "Where this phone has been, kept on your DDrive for good, sealed with your words", trailing = {
                FxSwitch(history) { history = it; Store.setHistoryOn(ctx, it) }
            })
            if (history) {
                RowDivider()
                Column(Modifier.padding(horizontal = 18.dp, vertical = 14.dp)) {
                    Text("Detail while moving", color = White, fontSize = 16.sp)
                    Gap(10)
                    Chips(listOf("Battery saver" to (detail == 0), "Balanced" to (detail == 1), "Detailed" to (detail == 2))) { detail = it; Store.setDetail(ctx, it) }
                    Gap(8)
                    Text(when (detail) {
                        0 -> "Wi-Fi and mobile network only, a point every minute and a half: streets, not paths. Satellites never."
                        1 -> "Satellites every 8 seconds or so while you move, on foot too, and a short satellite fix where you stop: the streets you really took."
                        else -> "Satellites every 3 seconds while you move: every turn, at a few percent of battery an hour on the move."
                    } + " Lying still costs nothing in any of them.", color = Grey, fontSize = 13.sp, lineHeight = 18.sp)
                }
            }
        }

        Section("If it's lost")
        CardGroup {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                TextBox(message, { message = it.take(200); Store.setLost(ctx, Store.lost(ctx), message) }, "Lock screen message in lost mode")
            }
            if (Store.lost(ctx)) {
                RowDivider()
                Row2("Lost mode is on", "Tap to turn it off", titleColor = Red, onClick = {
                    Guard.activity(ctx)?.let { a -> Guard.ask(a, "Turn off lost mode") { Store.setLost(ctx, false); Service.changed(ctx) } }
                })
            }
        }

        Section("Your DDrive")
        CardGroup {
            Row2(if (Store.words(ctx) == null) "Not linked" else Store.host(ctx), if (Store.words(ctx) == null) null else "Signed in as ${Store.user(ctx)}" +
                (if (Store.relay(ctx)) " · live" else " · history only"), onClick = { go(Screen.LinkDDrive) }) { Chevron() }
        }

        Section("Android")
        CardGroup {
            Row2("Permissions", if (Permissions.allGood(ctx) && Permissions.dnd(ctx)) "All set" else "Some are missing", onClick = { go(Screen.Allow) }) { Chevron() }
            RowDivider()
            var shots by remember { mutableStateOf(Store.screenshots(ctx)) }
            Row2("Allow screenshots", "Off: FindX is hidden from screenshots, screen recording and Recents", trailing = {
                FxSwitch(shots) { shots = it; Store.setScreenshots(ctx, it); (Guard.activity(ctx) as? MainActivity)?.secure() }
            })
            RowDivider()
            Row2("Activity", "What FindX did lately (kept on this phone only)", onClick = { go(Screen.Activity) }) { Chevron() }
            RowDivider()
            Row2("Battery use", "What FindX itself uses, measured: today, by the hour, the last two weeks", onClick = { go(Screen.Battery) }) { Chevron() }
        }

        Section("")
        CardGroup {
            Row2("Erase FindX on this phone", "Its history, spots, links and your words. What is on your DDrive stays.", titleColor = Red, onClick = {
                Guard.activity(ctx)?.let { a -> Guard.ask(a, "Erase FindX on this phone") { erasing = true } }
            })
        }
        Note("FindX ${BuildConfig.VERSION_NAME}. No accounts, no ads, no analytics: nothing leaves this phone except to your own DDrive, and the map's pictures from OpenFreeMap.")
    }
    if (erasing) Confirm("Erase FindX?", "This phone forgets everything FindX kept and stops being findable. Your DDrive keeps the history.", "Erase", onYes = {
        Service.stop(ctx); Beacon.stop(ctx); History.forget(ctx); Store.eraseLocal(ctx); Link.forget()
        (ctx as? android.app.Activity)?.recreate() ?: Guard.activity(ctx)?.recreate()
    }, onDismiss = { erasing = false })
}

/** Linking FindX to a DDrive with its address and 24 words. */
@Composable
fun LinkScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var address by remember { mutableStateOf(Store.host(ctx)) }
    var words by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<String?>(null) }
    var unlinking by remember { mutableStateOf(false) }
    val linked = Store.words(ctx) != null

    Page("Your DDrive", back = back) {
        if (linked) {
            CardGroup {
                Row2(Store.host(ctx), "Signed in as ${Store.user(ctx)}")
                RowDivider()
                Row2(if (Store.relay(ctx)) "Live finding is on" else "History only", if (Store.relay(ctx)) "Your DDrive passes FindX's sealed reports and commands along." else
                    "This DDrive keeps FindX's history. Live finding, ringing from afar and links come with the DDrive version that has FindX's live relay; FindX checks again every few hours.")
                Store.linkProblem(ctx)?.let { RowDivider(); Row2("Last problem", it, titleColor = Red) }
                RowDivider()
                Row2("Unlink", "This phone stops reporting to DDrive. Its history there stays.", titleColor = Red, onClick = {
                    Guard.activity(ctx)?.let { a -> Guard.ask(a, "Unlink FindX from DDrive") { unlinking = true } }
                })
            }
            if (unlinking) Confirm("Unlink?", "Your other devices and DDrive won't hear from this phone any more.", "Unlink", onYes = {
                Store.unlink(ctx); Link.forget(); Beacon.stop(ctx); Service.changed(ctx); back()
            }, onDismiss = { unlinking = false })
            return@Page
        }
        Body("FindX keeps its history on your own DDrive and finds your other devices through it. Use the words of your DDrive account (or the drive's recovery phrase), the same on every device you link.")
        Gap(16)
        TextBox(address, { address = it.trim() }, "DDrive address, like https://dd.example.com")
        Gap(10)
        OutlinedTextField(
            value = words, onValueChange = { words = it.lowercase() }, label = { Text("Your 24 words") },
            visualTransformation = PasswordVisualTransformation(), shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false), colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        Gap(8)
        Note("The words stay on this phone, locked with its security hardware. With an end-to-end encrypted account they never leave it at all; otherwise they're sent once to sign in (use https away from home). Everything FindX stores is sealed with keys made from them, so DDrive can't read where you've been.")
        problem?.let { Gap(10); Body(it, Red) }
        Gap(16)
        Primary(if (busy) "Signing in…" else "Link", enabled = !busy && address.isNotBlank() && words.isNotBlank()) {
            busy = true; problem = null
            scope.launch {
                problem = withContext(Dispatchers.IO) {
                    runCatching {
                        val (link, phrase, user) = Link.connect(address, words)
                        Store.link(ctx, link.client.address, phrase, link.e2e, user)
                        Link.forget()
                        null
                    }.getOrElse { it.message ?: "That didn't work" }
                }
                busy = false
                if (problem == null) { words = ""; Service.stop(ctx); Service.start(ctx); back() }
            }
        }
    }
}


/** Saved spots: what "at the desk" means to this phone. */
@Composable
fun SpotsScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var spots by remember { mutableStateOf(Store.spots(ctx)) }
    var name by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var said by remember { mutableStateOf<String?>(null) }
    var allowed by remember { mutableStateOf(Permissions.location(ctx) && Permissions.wifi(ctx)) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { allowed = Permissions.location(ctx) && Permissions.wifi(ctx) }

    Page("Spots", back = back) {
        Body("Satellites can't see indoors, but the phone can tell spots in your home apart by what it senses lying there: the Wi-Fi around it and how far each access point is, the magnetic field (steel in furniture bends it) and the air pressure (which floor). Save the places it usually lies, and when it's lost FindX says which one it's at.")
        Gap(16)
        if (!allowed) { Primary("Allow Wi-Fi and location") { ask.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION) + if (android.os.Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.NEARBY_WIFI_DEVICES) else emptyArray()) }; return@Page }
        TextBox(name, { name = it.take(30) }, "Name, like Desk or Nightstand")
        Gap(10)
        Primary(if (busy) "Sensing… keep it still" else "Save this spot", enabled = !busy && name.isNotBlank()) {
            busy = true; said = null
            scope.launch {
                said = runCatching {
                    // A few readings, a second apart, while it lies there: steadier than one.
                    val snaps = (1..5).map { Sensors.snapshot(ctx, 1_200) }
                    if (snaps.any { it.moving }) "It moved. Lay it down where it usually lies and try again."
                    else {
                        val wifi = snaps.flatMap { it.wifi.entries }.groupBy({ it.key }, { it.value }).mapValues { (_, v) -> v.average().toInt() }
                        val rtt = snaps.flatMap { it.rtt.entries }.groupBy({ it.key }, { it.value }).mapValues { (_, v) -> v.average() }
                        val sig = Signature(wifi, rtt, snaps.mapNotNull { it.magnet }.average().takeIf { !it.isNaN() }, snaps.mapNotNull { it.pressure }.average().takeIf { !it.isNaN() })
                        val fix = Locator.lastKnown(ctx)
                        Store.saveSpots(ctx, Store.spots(ctx) + Store.Spot(FxKeys.hex(FxKeys.bytes(8)), name.trim(), sig, fix?.lat, fix?.lon, System.currentTimeMillis()))
                        spots = Store.spots(ctx)
                        val what = listOfNotNull(if (wifi.isNotEmpty()) "${wifi.size} Wi-Fi" else null, if (rtt.isNotEmpty()) "${rtt.size} with distance" else null,
                            sig.magnet?.let { "magnetic field" }, sig.pressure?.let { "air pressure" })
                        name = ""
                        "Saved, from " + what.joinToString(", ") + if (wifi.isEmpty()) ". No Wi-Fi heard: spots tell apart much better with Wi-Fi on." else "."
                    }
                }.getOrElse { it.message }
                busy = false
            }
        }
        said?.let { Gap(10); Body(it, White) }
        if (spots.isNotEmpty()) {
            Section("Saved")
            CardGroup {
                spots.forEachIndexed { i, s ->
                    if (i > 0) RowDivider()
                    Row2(s.name, "${s.sig.wifi.size} Wi-Fi · saved ${java.text.DateFormat.getDateInstance().format(java.util.Date(s.made))}", trailing = {
                        LinkButton("Remove", Red) { Store.saveSpots(ctx, Store.spots(ctx).filter { it.id != s.id }); spots = Store.spots(ctx) }
                    })
                }
            }
            Note("Save the same place again from time to time (Wi-Fi changes); the nearest match wins.")
        }
    }
}

/** What Android must allow, each with why, and a button that asks. */
@Composable
fun AllowScreen(back: () -> Unit) {
    Page("Permissions", back = back) { AllowList() }
}

@Composable
fun AllowList() {
    val ctx = LocalContext.current
    resumed()
    var n by remember { mutableIntStateOf(0) }
    n
    val askFine = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { n++ }
    val askOne = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { n++ }
    fun open(i: android.content.Intent) = runCatching { ctx.startActivity(i) }

    CardGroup {
        Item("Location", "Where the phone is. Precise, so it can be pinned to a metre.", Permissions.location(ctx)) {
            askFine.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
        }
        RowDivider()
        Item("Location all the time", "So it can be found while FindX is closed. Choose \"Allow all the time\".", Permissions.background(ctx), enabled = Permissions.location(ctx)) {
            askOne.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        }
        RowDivider()
        Item("Nearby devices", "Bluetooth and Ultra-Wideband to find your devices close by, Wi-Fi distance for spots.", Permissions.nearby(ctx)) { askFine.launch(Permissions.NEARBY) }
        RowDivider()
        Item("Notifications", "Lost mode's message on the lock screen, and ringing.", Permissions.notifications(ctx)) {
            if (android.os.Build.VERSION.SDK_INT >= 33) askOne.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        RowDivider()
        Item("Ring through Do Not Disturb", "So ringing gets through even total silence. Find FindX in the list and allow it.", Permissions.dnd(ctx)) { open(Permissions.dndIntent()) }
        RowDivider()
        Item("Full-screen alerts", "To light the screen up while it rings.", Permissions.fullScreen(ctx)) { open(Permissions.fullScreenIntent(ctx)) }
        RowDivider()
        Item("Not held back by battery saving", "So a ring or a locate gets through the moment you ask. FindX itself uses almost no battery.", Permissions.unrestricted(ctx)) {
            open(Permissions.unrestrictedIntent(ctx))
        }
    }
    Note("Samsung phones: also check Settings › Battery › Background usage limits, and keep FindX out of \"Sleeping apps\".")
}

@Composable
private fun Item(title: String, text: String, done: Boolean, enabled: Boolean = true, onAsk: () -> Unit) {
    Row2(title, text, onClick = if (done || !enabled) null else onAsk) {
        if (done) Icon(FxIcons.Check, "Done", tint = Calm) else if (enabled) LinkButton("Allow", Red, onAsk) else Text("First the one above", color = Grey, fontSize = 12.sp)
    }
}

@Composable
fun ActivityScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    Page("Activity", "Kept on this phone only", back = back) {
        val lines = Log.lines(ctx)
        if (lines.isEmpty()) Body("Nothing yet.")
        else CardGroup {
            lines.forEachIndexed { i, (t, what) ->
                if (i > 0) RowDivider()
                Row2(what, formatDate(t))
            }
        }
    }
}
