package com.xo.findx

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        secure()
        enableEdgeToEdge()
        takeInvite(intent)
        setContent { FindXTheme { Root() } }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        takeInvite(intent)
    }

    /** An invitation to a circle, from its page's "Open in FindX" (findx://join?host=…&c=…#key). */
    private fun takeInvite(intent: android.content.Intent?) {
        val data = intent?.data ?: return
        if (data.scheme == "findx" && data.host == "join") Invites.pending.value = data.toString()
    }

    /** Where you are is nobody else's business: no screenshots, no screen recording, a blank preview in Recents (unless allowed). */
    fun secure() {
        if (Store.screenshots(this)) window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        else window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
    }

    private var shownAt = 0L

    override fun onPause() {
        super.onPause()
        if (shownAt > 0) Meter.span(this, "ui", android.os.SystemClock.elapsedRealtime() - shownAt)
        shownAt = 0
    }

    override fun onResume() {
        super.onResume()
        shownAt = android.os.SystemClock.elapsedRealtime()
        secure()
        if (Store.setUp(this)) Service.start(this)
        Service.changed(this)
    }
}

sealed class Screen {
    data class Map(val device: String, val span: String = "now") : Screen()
    data class Device(val device: String) : Screen()
    data class FindOne(val key: String) : Screen()
    data object Settings : Screen()
    data object Spots : Screen()
    data object LinkDDrive : Screen()
    data object Allow : Screen()
    data object Activity : Screen()
    data class CircleSettings(val id: String) : Screen()
    data object Battery : Screen()
}

/** An invitation the app was opened with, waiting for the People tab. */
object Invites {
    val pending = androidx.compose.runtime.mutableStateOf<String?>(null)
}

@Composable
private fun Root() {
    val ctx = LocalContext.current
    var setUp by remember { mutableStateOf(Store.setUp(ctx)) }
    if (!setUp) { SetupFlow { Store.markSetUp(ctx); Service.start(ctx); setUp = true }; return }
    val stack = remember { mutableStateListOf<Screen>() }
    var tab by remember { mutableIntStateOf(0) }
    val go: (Screen) -> Unit = { stack.add(it) }
    val back: () -> Unit = { if (stack.isNotEmpty()) stack.removeAt(stack.lastIndex) }
    BackHandler(stack.isNotEmpty()) { back() }
    val invite = Invites.pending.value
    if (invite != null && tab != 2) { stack.clear(); tab = 2 }
    when (val s = stack.lastOrNull()) {
        is Screen.Map -> MapScreen(s.device, s.span, back)
        is Screen.Device -> DeviceScreen(s.device, go, back)
        is Screen.FindOne -> FindOneScreen(s.key, back)
        Screen.Settings -> SettingsScreen(go, back)
        Screen.Spots -> SpotsScreen(back)
        Screen.LinkDDrive -> LinkScreen(back)
        Screen.Allow -> AllowScreen(back)
        Screen.Activity -> ActivityScreen(back)
        is Screen.CircleSettings -> CircleSettingsScreen(s.id, back)
        Screen.Battery -> BatteryScreen(back)
        null -> Column(Modifier.fillMaxSize().background(Black)) {
            Box(Modifier.weight(1f)) {
                when (tab) {
                    0 -> HomeScreen(go)
                    1 -> DevicesScreen(go)
                    2 -> PeopleScreen(go, invite) { Invites.pending.value = null }
                    3 -> NearbyScreen(go)
                    else -> ShareScreen(go)
                }
            }
            Box(Modifier.navigationBarsPadding()) {
                BottomTabs(
                    listOf(FxIcons.Phone to "Phone", FxIcons.Devices to "Devices", FxIcons.People to "People", FxIcons.Radar to "Nearby", FxIcons.Share to "Share"),
                    tab,
                ) { tab = it }
            }
        }
    }
}
