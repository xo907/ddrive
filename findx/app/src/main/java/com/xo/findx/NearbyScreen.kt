package com.xo.findx

import android.view.HapticFeedbackConstants
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.ln
import kotlin.math.max

/** Your devices within Bluetooth range, nearest first. */
@Composable
fun NearbyScreen(go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    var allowed by remember { mutableStateOf(Permissions.bluetooth(ctx)) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { allowed = Permissions.bluetooth(ctx) }
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    DisposableEffect(allowed) {
        if (allowed) Finder.start(ctx)
        onDispose { Finder.stop(ctx) }
    }
    LaunchedEffect(Unit) { while (true) { delay(1_000); now = System.currentTimeMillis(); Finder.prune(now) } }

    Page("Nearby", "Your devices within Bluetooth range") {
        when {
            Store.words(ctx) == null -> Empty("Link your DDrive first", "Your devices know each other by keys made from your 24 words, so link this phone (and the one you're looking for) with the same words.") {
                Primary("Link DDrive") { go(Screen.LinkDDrive) }
            }
            !allowed -> Empty("Allow Nearby devices", "FindX uses Bluetooth to lead you to your devices, without the internet. It never pairs with anything.") {
                Primary("Allow") { ask.launch(Permissions.NEARBY) }
            }
            !Beacon.available(ctx) -> Empty("Bluetooth is off", "Turn on Bluetooth in the phone's quick settings.") {}
            Finder.near.isEmpty() -> {
                Radar()
                Gap(8)
                Body("Looking… Your devices answer from up to about 30 metres, through a wall or two. A device you don't see here may be out of range, switched off or out of battery: try its map instead.")
            }
            else -> CardGroup {
                Finder.near.values.sortedBy { it.metres }.forEachIndexed { i, n ->
                    if (i > 0) RowDivider()
                    Row2(n.name, "${SignalFilter.words(n.metres)} · about ${metres(n.metres)}" + (if (n.alert) " · ringing or lost" else "") + (if (n.uwb && Precision.supported(ctx)) " · precise finding" else ""),
                        onClick = { go(Screen.FindOne(n.key)) }) { Icon(FxIcons.Chevron, null, tint = Grey) }
                }
            }
        }
    }
}

@Composable
private fun Radar() {
    val sweep by animateFloatAsState(360f, tween(2000), label = "sweep")
    Box(Modifier.fillMaxWidth().aspectRatio(1.6f), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(200.dp)) {
            for (i in 1..3) drawCircle(Line2, radius = size.minDimension / 2 * i / 3, style = Stroke(1.5f))
            drawCircle(Red, radius = 8f)
        }
        Icon(FxIcons.Radar, null, tint = Red.copy(alpha = 0.6f), modifier = Modifier.size(64.dp).rotate(sweep))
    }
}

/** Leading you to one device: warmer and colder, and with Ultra-Wideband an arrow and centimetres. */
@Composable
fun FindOneScreen(key: String, back: () -> Unit) {
    val ctx = LocalContext.current
    val view = LocalView.current
    val scope = rememberCoroutineScope()
    var precise by remember { mutableStateOf<Precision.Reading?>(null) }
    var preciseJob by remember { mutableStateOf<Job?>(null) }
    var said by remember { mutableStateOf<String?>(null) }
    DisposableEffect(Unit) {
        Finder.start(ctx)
        onDispose { preciseJob?.cancel(); Finder.stop(ctx) }
    }
    val near = Finder.near[key] ?: Finder.near.values.firstOrNull { it.device == key }
    val name = near?.name ?: Store.peers(ctx).firstOrNull { it.device == key }?.name ?: "Your device"
    val metres = precise?.takeIf { System.currentTimeMillis() - it.t < 3_000 }?.metres ?: near?.metres

    // Ask it to call out faster while we look (once we're in range).
    LaunchedEffect(near?.address) {
        val a = near?.address ?: return@LaunchedEffect
        withContext(Dispatchers.IO) { runCatching { Finder.send(ctx, a, Beacon.CMD_FAST) } }
    }
    // A tick under your thumb, quicker the closer you get.
    val latest by rememberUpdatedState(metres)
    LaunchedEffect(Unit) {
        while (true) {
            val m = latest
            if (m != null) view.performHapticFeedback(if (m < 0.5) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.CLOCK_TICK)
            delay(if (m == null) 1_000 else (120 + 900 * (m / 8).coerceIn(0.0, 1.0)).toLong())
        }
    }

    Column(Modifier.fillMaxSize().background(Black).safeDrawingPadding()) {
        LargeHeader(name, if (near == null) "Looking for it…" else if (precise != null) "Precise finding" else "Bluetooth", onBack = back)
        Column(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 20.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            val closeness = if (metres == null) 0f else (1 - ln(max(metres, 0.1) / 0.1) / ln(300.0)).toFloat().coerceIn(0.05f, 1f)
            val grow by animateFloatAsState(closeness, tween(500), label = "grow")
            Box(Modifier.size(260.dp), contentAlignment = Alignment.Center) {
                Canvas(Modifier.fillMaxSize()) {
                    val r = size.minDimension / 2
                    for (i in 1..4) drawCircle(Line2, radius = r * i / 4, style = Stroke(1.5f))
                    drawCircle(Red.copy(alpha = 0.18f), radius = r * grow)
                    drawCircle(Red, radius = r * grow * 0.45f, center = Offset(size.width / 2, size.height / 2))
                }
                val az = precise?.azimuth
                if (az != null) Icon(FxIcons.Arrow, null, tint = White, modifier = Modifier.size(84.dp).rotate(az.toFloat()))
            }
            Gap(22)
            Text(metres?.let { metres(it) } ?: "—", color = White, fontSize = 56.sp, fontWeight = FontWeight.Bold)
            val trend = near?.trend ?: 0.0
            Text(
                when {
                    metres == null -> "Out of range for now. Walk around slowly."
                    precise?.azimuth != null -> "Follow the arrow"
                    trend > 0.6 -> "Warmer"
                    trend < -0.6 -> "Colder"
                    else -> SignalFilter.words(metres)
                },
                color = if (trend > 0.6) Red else Grey2, fontSize = 20.sp, fontWeight = FontWeight.SemiBold,
            )
            said?.let { Gap(10); Body(it) }
        }
        Row(Modifier.fillMaxWidth().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Tile(FxIcons.Bell, "Ring", Modifier.weight(1f)) {
                scope.launch {
                    said = withContext(Dispatchers.IO) {
                        val a = near?.address
                        runCatching {
                            if (a != null) Finder.send(ctx, a, Beacon.CMD_RING)
                            else (Service.running ?: error("FindX isn't running")).command(near?.device ?: key, "ring")
                            "Ringing: listen for it"
                        }.getOrElse { it.message }
                    }
                }
            }
            val canPrecise = near?.uwb == true && Precision.supported(ctx)
            Tile(FxIcons.Target, if (preciseJob?.isActive == true) "Precise: on" else "Precise", Modifier.weight(1f), tint = if (canPrecise) Red else Grey) {
                if (!canPrecise) { said = "Precise finding needs Ultra-Wideband on both phones (recent Galaxy S Plus and Ultra, Pixel Pro and Fold)."; return@Tile }
                if (preciseJob?.isActive == true) { preciseJob?.cancel(); precise = null; return@Tile }
                preciseJob = scope.launch(Dispatchers.Default) {
                    runCatching { Precision.range(ctx, near!!.address) { precise = it } }.onFailure { e ->
                        if (e !is kotlinx.coroutines.CancellationException) said = "Precise finding didn't start: ${e.message}"
                    }
                }
            }
        }
    }
}

