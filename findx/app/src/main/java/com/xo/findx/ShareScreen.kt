package com.xo.findx

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Live location links. Anyone with a link sees where this phone is, live, on a page on your DDrive,
 * until it ends. The link's key is after the "#", which browsers never send anywhere, so your DDrive
 * serves the page without being able to read what it shows. Stopping a link (or its end) drops it.
 */
@Composable
fun ShareScreen(go: (Screen) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    Service.version.intValue
    var making by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    refresh
    val now = System.currentTimeMillis()
    val shares = Store.shares(ctx).filter { it.live(now) }

    Page("Share", "Your live location, by link") {
        if (Store.words(ctx) == null) {
            Empty("Link your DDrive first", "Links open a page on your DDrive, so it has to be one others can reach (an https address).") { Primary("Link DDrive") { go(Screen.LinkDDrive) } }
            return@Page
        }
        if (making) {
            NewShare(onCancel = { making = false }) { name, ms, approximate ->
                scope.launch {
                    problem = withContext(Dispatchers.IO) { runCatching { Shares.create(ctx, name, ms, approximate) }.exceptionOrNull()?.let { if (it is Link.NoRelay) "Sharing needs the DDrive version with FindX's live relay." else it.message } }
                    if (problem == null) { making = false; refresh++; Shares.send(ctx, Store.shares(ctx).maxBy { it.made }) }
                }
            }
            problem?.let { Gap(10); Body(it, Red) }
            return@Page
        }
        Primary("Share my live location") { making = true; problem = null }
        Gap(8)
        Body("You choose how long. They see this phone move on a map, live, and nothing of its history.", Grey)
        if (shares.isNotEmpty()) {
            Section("Sharing now")
            CardGroup {
                shares.sortedByDescending { it.made }.forEachIndexed { i, s ->
                    if (i > 0) RowDivider()
                    Column(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 14.dp)) {
                        Text(s.name.ifEmpty { "Link" }, color = White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                        Text((if (s.until == 0L) "Until you stop it" else "Until ${time(s.until)}" + if (s.until - now > 86_400_000) " on ${java.text.DateFormat.getDateInstance(java.text.DateFormat.MEDIUM).format(java.util.Date(s.until))}" else "") +
                            if (s.approximate) " · town only" else " · exact", color = Grey, fontSize = 13.sp)
                        Gap(10)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Chips(listOf("Send again" to false, "Copy link" to false, "Stop" to false)) { which ->
                                when (which) {
                                    0 -> Shares.send(ctx, s)
                                    1 -> Shares.copy(ctx, s)
                                    else -> scope.launch { withContext(Dispatchers.IO) { Shares.stop(ctx, s) }; refresh++ }
                                }
                            }
                        }
                    }
                }
            }
        }
        problem?.let { Gap(10); Body(it, Red) }
    }
}

@Composable
private fun NewShare(onCancel: () -> Unit, onMake: (String, Long, Boolean) -> Unit) {
    var name by remember { mutableStateOf("") }
    var length by remember { mutableIntStateOf(1) }
    var approximate by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(Surface2).padding(18.dp)) {
        Text("New link", color = White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
        Gap(12)
        TextBox(name, { name = it.take(40) }, "Who it's for (only you see this)")
        Gap(16)
        Text("How long", color = Grey, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Gap(8)
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            Chips(Shares.LENGTHS.mapIndexed { i, (label, _) -> label to (i == length) }) { length = it }
        }
        Gap(16)
        Text("How exact", color = Grey, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        Gap(8)
        Chips(listOf("Exact" to !approximate, "Town only" to approximate)) { approximate = it == 1 }
        Gap(18)
        Primary("Make the link") { onMake(name.trim(), Shares.LENGTHS[length].second, approximate) }
        LinkButton("Cancel", Grey2, onCancel)
    }
}

object Shares {
    /** The lengths offered; 0 is "until I stop it". */
    val LENGTHS = listOf("15 min" to 15 * 60_000L, "1 hour" to 3_600_000L, "8 hours" to 8 * 3_600_000L, "24 hours" to 86_400_000L,
        "7 days" to 7 * 86_400_000L, "Until I stop" to 0L)

    fun link(ctx: Context, s: Store.Share) = "${Store.host(ctx).trimEnd('/')}/f/${s.id}#${s.key}"

    /** Makes a link: a random id for the host, a random key only the link carries. */
    fun create(ctx: Context, name: String, ms: Long, approximate: Boolean): Store.Share {
        val link = Link.of(ctx) ?: throw IllegalStateException("Link your DDrive first")
        val keys = Store.keys(ctx)!!
        val now = System.currentTimeMillis()
        val s = Store.Share(FxKeys.hex(FxKeys.bytes(16)), FxKeys.b64url(FxKeys.bytes(32)), name, if (ms == 0L) 0L else now + ms, approximate, now)
        link.share(keys.group, Store.device(ctx), s.id, s.until)
        Store.saveShares(ctx, Store.shares(ctx) + s)
        Log.note(ctx, "Started sharing live location" + if (name.isNotEmpty()) " with $name" else "")
        Service.running?.report(Store.lastReport(ctx)?.fix)        // so the page has something to show at once
        return s
    }

    fun stop(ctx: Context, s: Store.Share) {
        Store.saveShares(ctx, Store.shares(ctx).filter { it.id != s.id })
        runCatching { Link.of(ctx)?.unshare(Store.keys(ctx)!!.group, s.id) }
        Log.note(ctx, "Stopped a live location link")
        Service.changed(ctx)
    }

    fun send(ctx: Context, s: Store.Share) {
        val text = "Here's where I am, live: ${link(ctx, s)}"
        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), "Share live location")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun copy(ctx: Context, s: Store.Share) {
        ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("FindX link", link(ctx, s)))
    }
}
