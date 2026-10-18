package com.xo.findx

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** First run: what FindX is, what it needs Android to allow, and linking your DDrive. */
@Composable
fun SetupFlow(done: () -> Unit) {
    val ctx = LocalContext.current
    var step by remember { mutableIntStateOf(0) }
    when (step) {
        0 -> Column(Modifier.fillMaxSize().background(Black).safeDrawingPadding().verticalScroll(rememberScrollState()).padding(24.dp)) {
            Gap(40)
            Image(painterResource(R.drawable.logo), null, modifier = Modifier.size(88.dp))
            Gap(24)
            Title("Find this phone, to the spot it's lying on")
            Gap(18)
            Point("Anywhere", "On a map from your other devices and your DDrive, live, and where it has been, kept for good.")
            Point("Indoors", "Which room and which spot, which way up it lies, and whether something covers it.")
            Point("Close by", "Warmer and colder over Bluetooth, and to the centimetre with Ultra-Wideband. Ring it loud even on silent.")
            Point("Private", "Sealed with your own words before it leaves the phone: your DDrive keeps it but can't read it. No accounts, no ads, no tracking.")
            Point("Light on battery", "Lying still it does next to nothing; it only looks hard while it moves or you're looking for it.")
            Gap(24)
            Primary("Set up") { step = 1 }
        }
        1 -> Column(Modifier.fillMaxSize().background(Black).safeDrawingPadding()) {
            LargeHeader("Allow", "Each one, and why", onBack = { step = 0 })
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
                AllowList()
                Gap(20)
            }
            Column(Modifier.padding(16.dp)) {
                Primary(if (Permissions.location(ctx)) "Next" else "Skip for now") { step = 2 }
            }
        }
        else -> Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) { LinkScreen(back = done) }
            if (Store.words(ctx) == null) Column(Modifier.background(Black).padding(16.dp)) { Ghost("Not now (this phone only)", Modifier.padding(0.dp)) { done() } }
        }
    }
}

@Composable
private fun Point(title: String, text: String) {
    Column(Modifier.padding(vertical = 8.dp)) {
        Text(title, color = Red, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        Body(text)
    }
}
