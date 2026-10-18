package com.xo.findx

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.lang.ref.WeakReference

/** The phone's screen while it rings: bright, over the lock screen, with one big button to stop it. */
class RingActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.attributes = window.attributes.apply { screenBrightness = 1f }
        current = WeakReference(this)
        setContent { FindXTheme { RingScreen { Ring.stop(this); finish() } } }
    }

    override fun onDestroy() {
        if (current?.get() === this) current = null
        super.onDestroy()
    }

    companion object {
        private var current: WeakReference<RingActivity>? = null

        /** Shows the screen: as a full-screen alert, which Android allows from the background, and wakes the screen. */
        fun show(ctx: Context) {
            val open = Intent(ctx, RingActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            val pi = PendingIntent.getActivity(ctx, 1, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val stop = PendingIntent.getService(ctx, 2, Intent(ctx, Service::class.java).setAction(Service.ACTION_STOP_RING), PendingIntent.FLAG_IMMUTABLE)
            val n = Notification.Builder(ctx, Service.CH_ALERT).setSmallIcon(R.drawable.ic_stat_findx)
                .setContentTitle("FindX is ringing this phone").setContentText("Tap to stop")
                .setCategory(Notification.CATEGORY_ALARM).setVisibility(Notification.VISIBILITY_PUBLIC)
                .setFullScreenIntent(pi, true).setContentIntent(pi).setOngoing(true)
                .addAction(Notification.Action.Builder(null, "I found it", stop).build()).build()
            ctx.getSystemService(NotificationManager::class.java).notify(Service.NOTE_RING, n)
        }

        fun close() {
            current?.get()?.let { a -> a.runOnUiThread { a.finish() } }
            Service.running?.getSystemService(NotificationManager::class.java)?.cancel(Service.NOTE_RING)
        }
    }
}

@Composable
private fun RingScreen(onStop: () -> Unit) {
    val pulse = rememberInfiniteTransition(label = "pulse")
    val s by pulse.animateFloat(0.85f, 1.15f, infiniteRepeatable(tween(600), RepeatMode.Reverse), label = "s")
    val ctx = LocalContext.current
    LaunchedEffect(Ring.ringing.value) { if (!Ring.ringing.value) onStop() }
    Column(
        Modifier.fillMaxSize().background(Black).safeDrawingPadding().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Gap(40)
            Text("Here I am", color = White, fontSize = 34.sp, fontWeight = FontWeight.Bold)
            Gap(8)
            Text("Someone is looking for this phone with FindX", color = Grey2, fontSize = 16.sp, textAlign = TextAlign.Center)
            if (Store.lost(ctx)) {
                Gap(18)
                Text(Store.lostMessage(ctx).ifEmpty { "This phone is lost. Please call its owner." }, color = White, fontSize = 18.sp, textAlign = TextAlign.Center)
            }
        }
        Box(Modifier.size(240.dp).scale(s).clip(CircleShape).background(Red), contentAlignment = Alignment.Center) {
            Icon(FxIcons.Bell, null, tint = OnRed, modifier = Modifier.size(96.dp))
        }
        Primary("I found it", onClick = onStop)
    }
}
