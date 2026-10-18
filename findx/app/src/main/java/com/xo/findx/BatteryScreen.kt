package com.xo.findx

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Calendar
import java.util.Date

/** Each part's colour in the charts. */
val PartColour = mapOf(
    "satellites" to Color(0xFFFF2A2A), "network" to Color(0xFF0A84FF), "processor" to Color(0xFFBF5AF2),
    "bluetooth" to Color(0xFF30B0C7), "sensors" to Color(0xFFFF9F0A), "ringing" to Color(0xFFFFCC00),
)
val PartName = mapOf(
    "satellites" to "Satellites", "network" to "Network", "processor" to "Processor",
    "bluetooth" to "Bluetooth", "sensors" to "Sensors", "ringing" to "Ringing",
)

fun duration(ms: Long): String {
    val s = ms / 1000
    return when {
        s < 1 -> "under a second"
        s < 60 -> "$s s"
        s < 3600 -> "${s / 60} min ${s % 60} s"
        else -> "${s / 3600} h ${s / 60 % 60} min"
    }
}

fun bytes(b: Long): String = when {
    b < 1024 -> "$b B"
    b < 1024 * 1024 -> "%.0f KB".format(b / 1024.0)
    else -> "%.1f MB".format(b / 1048576.0)
}

/** What a part did, in plain numbers (exactly as counted). */
fun measured(part: String, u: Usage): String = when (part) {
    "satellites" -> if (u.gpsMs == 0L) "Not used" else "On for ${duration(u.gpsMs)}"
    "network" -> "${u.wakeWifi + u.wakeCell} radio wake-ups" + (if (u.wakeCell > 0) " (${u.wakeCell} on mobile data)" else "") +
        " · ${bytes(u.bytesWifi + u.bytesCell)}"
    "processor" -> "${duration(u.cpuMs)} of processing"
    "bluetooth" -> listOfNotNull(
        if (u.bleLowMs + u.bleFastMs > 0) "beacon on ${duration(u.bleLowMs + u.bleFastMs)}" + if (u.bleFastMs > 0) " (fast ${duration(u.bleFastMs)})" else "" else null,
        if (u.scanMs > 0) "looking for your devices ${duration(u.scanMs)}" else null,
    ).joinToString(" · ").ifEmpty { "Not used" }.replaceFirstChar { it.uppercase() }
    "sensors" -> if (u.sensorMs == 0L) "Not used" else "Short readings, ${duration(u.sensorMs)} in all"
    "ringing" -> if (u.ringMs == 0L) "Didn't ring" else "Rang for ${duration(u.ringMs)}"
    else -> ""
}

/** How much battery FindX uses, measured: today, by the hour, the last two weeks, and right now. */
@Composable
fun BatteryScreen(back: () -> Unit) {
    val ctx = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    var selected by remember { mutableLongStateOf(0L) }       // the day shown below the chart (0: today)
    LaunchedEffect(Unit) {
        while (true) {
            withContext(Dispatchers.IO) { runCatching { Meter.tick(ctx) } }
            tick++
            delay(5_000)
        }
    }
    tick
    val p = remember { Meter.profile(ctx) }
    val days = Meter.days(ctx, 14)
    val hours = Meter.hours(ctx, 24)
    val today = days.last().second
    val shownDay = days.firstOrNull { it.first == selected } ?: days.last()
    val counted = Meter.daysCounted(ctx).coerceAtMost(7)
    val avgPct = if (counted >= 2) days.takeLast(counted).dropLast(1).map { Energy.percent(it.second, p) }.average() else null

    Page("Battery use", "What FindX itself uses, measured", back = back) {
        // The headline.
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(26.dp)).background(Surface2).padding(20.dp)) {
            Text(Energy.pct(Energy.percent(today, p)), color = White, fontSize = 44.sp, fontWeight = FontWeight.Bold)
            Text("of your battery today, since midnight · ${Energy.words(Energy.total(today, p))}", color = Grey, fontSize = 14.sp)
            if (avgPct != null) {
                Gap(10)
                Body("About ${Energy.pct(avgPct)} a day over the last ${counted - 1} days.")
            }
        }

        Section("The last 14 days")
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(Surface2).padding(16.dp)) {
            Bars(days.map { it.first to Energy.mah(it.second, p) }, selected = shownDay.first, labels = { t ->
                Calendar.getInstance().apply { timeInMillis = t }.getDisplayName(Calendar.DAY_OF_WEEK, Calendar.NARROW_FORMAT, java.util.Locale.getDefault()) ?: ""
            }) { selected = it }
            Gap(8)
            Legend()
        }

        Section("Today, by the hour")
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(Surface2).padding(16.dp)) {
            Bars(hours.map { it.first to Energy.mah(it.second, p) }, selected = -1, labels = { t ->
                val h = Calendar.getInstance().apply { timeInMillis = t }.get(Calendar.HOUR_OF_DAY)
                if (h % 6 == 0) "$h" else ""
            }) {}
        }

        val dayLabel = if (shownDay.first == days.last().first) "Today" else DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date(shownDay.first))
        Section("$dayLabel, part by part")
        val parts = Energy.mah(shownDay.second, p)
        val total = parts.values.sum().coerceAtLeast(1e-9)
        CardGroup {
            Energy.PARTS.forEachIndexed { i, part ->
                if (i > 0) RowDivider()
                Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(PartColour.getValue(part)))
                    HGap(12)
                    Column(Modifier.weight(1f)) {
                        Text(PartName.getValue(part), color = White, fontSize = 16.sp)
                        Text(measured(part, shownDay.second), color = Grey, fontSize = 13.sp, lineHeight = 17.sp)
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text(Energy.words(parts.getValue(part)), color = White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        Text("%.0f%%".format(100 * parts.getValue(part) / total), color = Grey, fontSize = 12.sp)
                    }
                }
            }
            if (shownDay.second.uiMs > 0) {
                RowDivider()
                Row2("FindX on screen ${duration(shownDay.second.uiMs)}", "The screen's own use is Android's to count (Settings › Battery), so it isn't in the figures above.")
            }
        }

        Section("Right now")
        val phone = Meter.phoneNow(ctx)
        val mine = Meter.findxNow(ctx)
        CardGroup {
            Row2("FindX: about ${if (mine >= 1) "%.0f".format(mine) else "%.2f".format(mine)} mA", listOfNotNull(
                if (Meter.isOn("satellites")) "satellites on" else null,
                if (Meter.isOn("beaconFast")) "beacon fast" else if (Meter.isOn("beaconLow")) "beacon (once a second)" else null,
                if (Meter.isOn("scan")) "looking for your devices" else null,
                if (Ring.ringing.value) "ringing" else null,
            ).joinToString(", ").ifEmpty { "nothing on: waiting quietly" }.replaceFirstChar { it.uppercase() } + ", plus a moment of processing now and then")
            if (phone != null) {
                RowDivider()
                Row2("Whole phone: ${"%.0f".format(phone.first)} mA" + if (phone.second) " (charging)" else "",
                    "Measured by the battery itself" + if (!phone.second && phone.first > 0) ": FindX is about ${Energy.pct(100 * mine / phone.first)} of it" else "")
            }
        }

        Section("How this is measured")
        CardGroup {
            Row2("Exact", "Processing time (the kernel's count for FindX), network bytes (the same), each time FindX woke the radio, and how long it had satellites, Bluetooth, sensors and ringing on.")
            RowDivider()
            Row2("Turned into battery with", if (p.source == "phone") "This phone's own power profile: its maker's figures for what each part draws, which Android's battery screen uses too."
                else "Typical figures for a recent phone (this phone doesn't share its power profile with apps). Real use can differ by a factor of two either way.")
            RowDivider()
            Row2("Battery", "%.0f mAh, ".format(p.capacityMah) + when (p.capacitySource) {
                "battery" -> "from the battery's own charge counter"
                "profile" -> "from the phone's power profile"
                else -> "a typical size (this phone doesn't say)"
            })
            RowDivider()
            Row2("Android's own figure", "Settings › Apps › FindX › Battery. Android counts the same way with figures only the system can read, and adds time on screen.",
                onClick = { runCatching { ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}"))) } }) {
                LinkButton("Open", Red) { runCatching { ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}"))) } }
            }
        }
        Gap(20)
    }
}

/** Stacked bars, one per time slot, coloured by part. Tapping one picks it. */
@Composable
private fun Bars(slots: List<Pair<Long, Map<String, Double>>>, selected: Long, labels: (Long) -> String, onPick: (Long) -> Unit) {
    val max = slots.maxOfOrNull { it.second.values.sum() }?.takeIf { it > 0 } ?: 1.0
    val grid = Line2
    Column {
        Canvas(Modifier.fillMaxWidth().height(130.dp).pointerInput(slots) {
            detectTapGestures { pos ->
                val i = (pos.x / (size.width / slots.size)).toInt().coerceIn(0, slots.lastIndex)
                onPick(slots[i].first)
            }
        }) {
            val w = size.width / slots.size
            val bw = w * 0.62f
            for (k in 1..3) drawLine(grid, Offset(0f, size.height * k / 4), Offset(size.width, size.height * k / 4), strokeWidth = 1f)
            slots.forEachIndexed { i, (t, parts) ->
                var y = size.height
                val x = i * w + (w - bw) / 2
                val faded = selected >= 0 && t != selected
                for (part in Energy.PARTS) {
                    val h = (parts.getValue(part) / max * (size.height - 4)).toFloat()
                    if (h <= 0f) continue
                    y -= h
                    drawRoundRect(PartColour.getValue(part).copy(alpha = if (faded) 0.45f else 1f), Offset(x, y), Size(bw, h), CornerRadius(3f, 3f))
                }
            }
        }
        Row(Modifier.fillMaxWidth()) {
            slots.forEach { (t, _) ->
                Text(labels(t), color = if (t == selected) White else Grey, fontSize = 11.sp, modifier = Modifier.weight(1f),
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
        }
        Text("Highest: ${Energy.words(max)}", color = Grey, fontSize = 11.sp, modifier = Modifier.padding(top = 4.dp))
    }
}

@Composable
private fun Legend() {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        Energy.PARTS.chunked(3).forEach { col ->
            Column(Modifier.weight(1f)) {
                col.forEach { part ->
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(PartColour.getValue(part)))
                        HGap(6)
                        Text(PartName.getValue(part), color = Grey, fontSize = 12.sp)
                    }
                }
            }
        }
    }
}
