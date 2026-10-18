package com.xo.findx

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** A soft colour of their own for each person (as the map gives them: findx-core.js FX.hue). */
fun hueOf(id: String): Color {
    var h = 0L
    for (ch in id) h = (h * 31 + ch.code) and 0xFFFFFFFFL
    return Color.hsl((h % 360).toFloat(), 0.62f, 0.52f)
}

@Composable
fun Face(name: String, id: String, size: Int = 44) = Box(
    Modifier.size(size.dp).clip(CircleShape).background(hueOf(id)), contentAlignment = Alignment.Center,
) {
    Text(name.trim().split(Regex("\\s+")).mapNotNull { it.firstOrNull()?.uppercaseChar() }.take(2).joinToString(""),
        color = OnRed, fontWeight = FontWeight.Bold, fontSize = (size * 0.36).sp)
}

/** Where a member is, in words: at a place, how far from here, or how long ago. */
fun whereWords(m: Member, here: Fix?, now: Long = System.currentTimeMillis()): String {
    val parts = ArrayList<String>()
    if (m.place != null) parts.add("At ${m.place}" + if (m.since > 0) " since ${time(m.since)}" else "")
    else if (m.fix != null && here != null) {
        val d = Geo.distance(m.fix, here)
        parts.add(if (d < 150) "Near you" else "${metres(d)} away")
    }
    if (m.fix == null) parts.add("No position yet")
    parts.add(if (now - m.t < 90_000) "now" else ago(m.t, now))
    if (m.battery >= 0) parts.add("${m.battery}%" + if (m.charging) " charging" else "")
    return parts.joinToString(" · ")
}

/** Family and friends: your circles, and where everybody in them is. */
@Composable
fun PeopleScreen(go: (Screen) -> Unit, invite: String?, onInviteUsed: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    Service.version.intValue
    var refresh by remember { mutableIntStateOf(0) }
    var members by remember { mutableStateOf<Map<String, List<Member>>>(emptyMap()) }
    var making by remember { mutableStateOf(false) }
    var joining by remember { mutableStateOf(invite) }
    var problem by remember { mutableStateOf<String?>(null) }
    val circles = remember(refresh, Service.version.intValue) { Circles.all(ctx) }
    LaunchedEffect(circles.map { it.id }) {
        while (true) {
            members = withContext(Dispatchers.IO) { circles.associate { it.id to Circles.membersCached(ctx, it, 15_000) } }
            delay(20_000)
        }
    }
    LaunchedEffect(invite) { if (invite != null) { joining = invite; onInviteUsed() } }
    val here = Store.lastReport(ctx)?.fix

    Page("People", "Family and friends, live", actions = { IconAction(FxIcons.Plus, "Make a circle") { making = true } }) {
        joining?.let { text ->
            JoinCard(text, onCancel = { joining = null }) { link ->
                scope.launch {
                    problem = withContext(Dispatchers.IO) { runCatching { Circles.join(ctx, link); Service.running?.report(Store.lastReport(ctx)?.fix ?: Locator.lastKnown(ctx)); null }.getOrElse { it.message ?: "That didn't work" } }
                    if (problem == null) { joining = null; refresh++ }
                }
            }
            problem?.let { Gap(8); Body(it, Red) }
            Gap(16)
        }
        if (making) {
            MakeCard(onCancel = { making = false }) { name ->
                scope.launch {
                    problem = withContext(Dispatchers.IO) { runCatching { Circles.create(ctx, name); Service.running?.report(Store.lastReport(ctx)?.fix ?: Locator.lastKnown(ctx)); null }.getOrElse { it.message ?: "That didn't work" } }
                    if (problem == null) { making = false; refresh++ }
                }
            }
            problem?.let { Gap(8); Body(it, Red) }
            Gap(16)
        }
        if (circles.isEmpty() && !making && joining == null) {
            Empty("Share where you are with your people", "A circle is family or friends who see where each other are, live, get told when somebody arrives home, and can ring each other's phones to find them. It's all sealed with the circle's own key: your DDrive passes it on without reading it.") {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Primary("Make a circle", modifier = Modifier.weight(1f)) { making = true }
                    Ghost("Join", Modifier.weight(1f)) { joining = "" }
                }
            }
            return@Page
        }
        circles.forEach { c ->
            Section(c.name)
            CardGroup {
                val list = members[c.id].orEmpty().sortedByDescending { it.t }
                Row2(Store.myName(ctx) + " (you)", "Seen by everybody here" + if (!c.canRing) " · they can't ring you" else "") {}
                list.forEach { m ->
                    RowDivider()
                    Row(
                        Modifier.fillMaxWidth().clickable { go(Screen.Map("c:${c.id}:${m.member}")) }.padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Face(m.person, m.member)
                        HGap(12)
                        Column(Modifier.weight(1f)) {
                            Text(m.person, color = White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                            Text(whereWords(m, here), color = Grey, fontSize = 13.sp, lineHeight = 17.sp)
                        }
                        if (m.canRing) Box(Modifier.size(40.dp).clip(CircleShape).background(Surface3).clickable {
                            scope.launch {
                                problem = withContext(Dispatchers.IO) { runCatching { Circles.ring(ctx, c, m.member); null }.getOrElse { it.message } }
                                if (problem == null) problem = "Ringing ${m.person}'s phone"
                            }
                        }, contentAlignment = Alignment.Center) { Icon(FxIcons.Bell, "Ring", tint = Red, modifier = Modifier.size(20.dp)) }
                    }
                }
                if (list.isEmpty()) { RowDivider(); Row2("Nobody else yet", "Invite them: they see you as soon as they join") {} }
                RowDivider()
                Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Tile(FxIcons.Share, "Invite", Modifier.weight(1f)) {
                        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
                            .putExtra(Intent.EXTRA_TEXT, "Join my circle \"${c.name}\" on FindX, to see where each other are: ${c.invite}"), "Invite to ${c.name}")
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }
                    Tile(FxIcons.Map, "Map", Modifier.weight(1f)) { go(Screen.Map(list.firstOrNull()?.let { "c:${c.id}:${it.member}" } ?: Store.device(ctx))) }
                    Tile(FxIcons.Gear, "Circle", Modifier.weight(1f)) { go(Screen.CircleSettings(c.id)) }
                }
            }
        }
        problem?.let { if (!making && joining == null) { Gap(10); Body(it, if (it.startsWith("Ringing")) White else Red) } }
        Gap(16)
        if (joining == null) LinkButton("Join a circle with an invitation", Red) { joining = "" }
    }
}

@Composable
private fun MakeCard(onCancel: () -> Unit, onMake: (String) -> Unit) {
    var name by remember { mutableStateOf("Family") }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Surface2).padding(18.dp)) {
        Text("New circle", color = White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
        Gap(4)
        Body("It lives on your DDrive. You'll invite people with a link; they can join from any DDrive, or none.")
        Gap(12)
        TextBox(name, { name = it.take(40) }, "Its name, like Family or Friends")
        Gap(14)
        Primary("Make it", enabled = name.isNotBlank()) { onMake(name) }
        LinkButton("Cancel", Grey2, onCancel)
    }
}

@Composable
private fun JoinCard(initial: String, onCancel: () -> Unit, onJoin: (String) -> Unit) {
    val ctx = LocalContext.current
    var text by remember(initial) { mutableStateOf(initial) }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Surface2).padding(18.dp)) {
        Text("Join a circle", color = White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
        Gap(4)
        Body("Paste the invitation you were sent. Everybody in the circle will see where this phone is, live, until you leave.")
        Gap(12)
        TextBox(text, { text = it }, "Invitation link")
        Gap(8)
        LinkButton("Paste", Grey2) {
            ctx.getSystemService(ClipboardManager::class.java).primaryClip?.getItemAt(0)?.coerceToText(ctx)?.toString()?.let { text = it }
        }
        Gap(8)
        Primary("Join", enabled = Circle.parseInvite(text) != null) { onJoin(text) }
        LinkButton("Cancel", Grey2, onCancel)
    }
}

/** One circle: who you are in it, its places, whether they may ring you, leaving. */
@Composable
fun CircleSettingsScreen(id: String, back: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    Service.version.intValue
    val c = Circles.get(ctx, id) ?: run { back(); return }
    var myName by remember { mutableStateOf(Store.myName(ctx)) }
    var placeName by remember { mutableStateOf("") }
    var radius by remember { mutableIntStateOf(1) }
    var said by remember { mutableStateOf<String?>(null) }
    var leaving by remember { mutableStateOf(0) }       // 1 leave, 2 delete for everybody
    val radii = listOf(100.0, 200.0, 500.0)

    Page(c.name, if (c.mine) "Made by you" else "You joined it", back = back) {
        CardGroup {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                TextBox(myName, { myName = it.take(30); Store.setMyName(ctx, myName) }, "Your name in your circles")
            }
            RowDivider()
            Row2("Let them ring this phone", "So they can help you find it. It rings loud, even on silent.", trailing = {
                FxSwitch(c.canRing) { Circles.setCanRing(ctx, c, it) }
            })
            RowDivider()
            Row2("Invitation", c.invite.take(48) + "…", onClick = {
                ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("FindX invitation", c.invite))
                said = "Invitation copied"
            }) { Icon(FxIcons.Copy, "Copy", tint = Grey) }
        }

        Section("Places")
        Body("Everybody is told when somebody arrives at or leaves one of these. Only places you add here, and only to this circle.", Grey)
        Gap(10)
        CardGroup {
            c.places.forEachIndexed { i, p ->
                if (i > 0) RowDivider()
                Row2(p.name, "Within ${p.r.toInt()} m", trailing = {
                    LinkButton("Remove", Red) {
                        scope.launch { said = withContext(Dispatchers.IO) { runCatching { Circles.setPlaces(ctx, c, c.places.filter { it.id != p.id }); null }.getOrElse { it.message } } }
                    }
                })
            }
            if (c.places.isNotEmpty()) RowDivider()
            Column(Modifier.padding(14.dp)) {
                TextBox(placeName, { placeName = it.take(30) }, "Add where this phone is now, as…")
                Gap(10)
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    Chips(radii.mapIndexed { i, r -> "${r.toInt()} m" to (i == radius) }) { radius = it }
                }
                Gap(10)
                Primary("Add this place", enabled = placeName.isNotBlank()) {
                    val f = Store.lastReport(ctx)?.fix ?: Locator.lastKnown(ctx)
                    if (f == null) { said = "This phone doesn't know where it is yet. Pin it down first."; return@Primary }
                    val p = Place(FxKeys.hex(FxKeys.bytes(8)), placeName.trim(), f.lat, f.lon, radii[radius])
                    scope.launch {
                        said = withContext(Dispatchers.IO) { runCatching { Circles.setPlaces(ctx, c, c.places + p); null }.getOrElse { it.message } }
                        if (said == null) placeName = ""
                    }
                }
            }
        }
        said?.let { Gap(10); Body(it, White) }

        Section("")
        CardGroup {
            Row2("Leave this circle", "They stop seeing where you are, and you them.", titleColor = Red, onClick = { leaving = 1 })
            if (c.mine) {
                RowDivider()
                Row2("Delete it for everybody", "Nobody can use it any more.", titleColor = Red, onClick = { leaving = 2 })
            }
        }
    }
    if (leaving > 0) Confirm(
        if (leaving == 2) "Delete \"${c.name}\"?" else "Leave \"${c.name}\"?",
        if (leaving == 2) "Everybody in it stops seeing each other." else "To come back, you'll need a new invitation.",
        if (leaving == 2) "Delete" else "Leave",
        onYes = { scope.launch { withContext(Dispatchers.IO) { runCatching { Circles.leave(ctx, c, forEveryone = leaving == 2) } }; back() } },
        onDismiss = { leaving = 0 },
    )
}
