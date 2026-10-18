package com.xo.findx

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.VibrationEffect
import android.os.VibratorManager
import androidx.compose.runtime.mutableStateOf
import kotlin.concurrent.thread
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Makes the phone easy to find by ear and eye: a sound, the torch flashing and the phone buzzing
 * against whatever it lies on.
 *
 * It gets through everything that would keep a phone quiet: silent and vibrate mode, the volume
 * turned down (alarm volume goes to full and comes back after), and Do Not Disturb, even "total
 * silence", once you've given FindX Do Not Disturb access (setup asks; Android only lets you grant it
 * yourself). While it rings, Do Not Disturb is off and the ringer is on; your settings come back the
 * moment it stops.
 *
 * The sound is made for being found, not for being pleasant: short chirps sweeping from 1 to 5 kHz
 * with sharp starts. People place a sound by the tiny delay and loudness difference between their
 * ears, and that works best for sounds with sharp starts that cover many pitches; a pure beep at one
 * pitch is notoriously hard to place (it seems to come from everywhere). It plays as an alarm, so it
 * rings through silent mode, at full volume (the volume you had comes back afterwards), getting
 * louder over the first seconds. Picking the phone up turns it down; Stop or "I found it" ends it.
 * It stops by itself after [MAX_MS].
 */
object Ring {
    const val MAX_MS = 5 * 60_000L
    private const val RATE = 44_100

    val ringing = mutableStateOf(false)

    @Volatile private var stopAt = 0L
    @Volatile private var quiet = false
    private var thread: Thread? = null

    fun start(ctx: Context, forMs: Long = MAX_MS) {
        val app = ctx.applicationContext
        synchronized(this) {
            stopAt = System.currentTimeMillis() + forMs.coerceIn(5_000, MAX_MS)
            quiet = false
            if (thread?.isAlive == true) return
            ringing.value = true
            thread = thread(name = "fx-ring") { play(app) }
        }
        RingActivity.show(app)
        Service.changed(app)
    }

    fun stop(ctx: Context) {
        stopAt = 0
        Service.changed(ctx.applicationContext)
    }

    private fun play(ctx: Context) {
        val am = ctx.getSystemService(AudioManager::class.java)
        val quietness = Quietness(ctx, am)
        quietness.lift()
        val before = am.getStreamVolume(AudioManager.STREAM_ALARM)
        runCatching { am.setStreamVolume(AudioManager.STREAM_ALARM, am.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0) }
        val torch = Torch(ctx)
        val buzz = buzzer(ctx)
        val pickup = Pickup(ctx) { quiet = true }
        val pcm = chirps()
        val track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(RATE).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(pcm.size * 2).build()
        val started = System.currentTimeMillis()
        try {
            track.play()
            buzz?.let { it(true) }
            pickup.start()
            var flash = false
            while (System.currentTimeMillis() < stopAt) {
                val ramp = ((System.currentTimeMillis() - started) / 4_000f).coerceIn(0.25f, 1f)
                track.setVolume(if (quiet) 0.25f else ramp)
                flash = !flash
                torch.set(flash)
                track.write(pcm, 0, pcm.size)        // blocks for the length of the pattern (~1.2 s)
            }
        } catch (_: Exception) {
        } finally {
            runCatching { track.stop(); track.release() }
            Meter.span(ctx, "ringing", System.currentTimeMillis() - started)
            torch.set(false)
            buzz?.let { it(false) }
            pickup.stop()
            runCatching { am.setStreamVolume(AudioManager.STREAM_ALARM, before, 0) }
            quietness.restore()
            ringing.value = false
            RingActivity.close()
            Service.changed(ctx)
        }
    }

    /** Three rising chirps and a pause, as 16-bit samples. */
    fun chirps(): ShortArray {
        val chirp = (RATE * 0.16).toInt()
        val gap = (RATE * 0.07).toInt()
        val pause = (RATE * 0.45).toInt()
        val out = ShortArray(3 * (chirp + gap) + pause)
        var i = 0
        repeat(3) {
            var phase = 0.0
            for (n in 0 until chirp) {
                val t = n.toDouble() / chirp
                val f = 1_000 * Math.pow(5.0, t)                        // 1 kHz to 5 kHz, sweeping evenly in pitch
                phase += 2 * PI * f / RATE
                val env = minOf(1.0, n / (RATE * 0.002)) * exp(-2.2 * t)  // a 2 ms attack: a sharp start
                val s = sin(phase) + 0.35 * sin(2 * phase)               // a harmonic makes it carry
                out[i++] = (s / 1.35 * env * 32_000).toInt().toShort()
            }
            i += gap
        }
        return out
    }

    private fun buzzer(ctx: Context): ((Boolean) -> Unit)? {
        val v = ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator ?: return null
        if (!v.hasVibrator()) return null
        val pattern = VibrationEffect.createWaveform(longArrayOf(0, 400, 250, 400, 600), 0)
        return { on ->
            when {
                !on -> v.cancel()
                android.os.Build.VERSION.SDK_INT >= 33 -> v.vibrate(pattern, android.os.VibrationAttributes.createForUsage(android.os.VibrationAttributes.USAGE_ALARM))
                else -> @Suppress("DEPRECATION") v.vibrate(pattern, AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build())
            }
        }
    }

    /**
     * Do Not Disturb and silent mode, lifted while ringing and put back after. Needs Do Not Disturb
     * access (Permissions.dnd); without it the alarm sound still gets through silent mode and the
     * usual Do Not Disturb settings (which let alarms through), just not "total silence".
     */
    private class Quietness(ctx: Context, private val am: AudioManager) {
        private val nm = ctx.getSystemService(android.app.NotificationManager::class.java)
        private var filter = -1
        private var ringer = -1

        fun lift() {
            if (!nm.isNotificationPolicyAccessGranted) return
            runCatching {
                filter = nm.currentInterruptionFilter
                if (filter != android.app.NotificationManager.INTERRUPTION_FILTER_ALL) nm.setInterruptionFilter(android.app.NotificationManager.INTERRUPTION_FILTER_ALL)
            }
            runCatching {
                ringer = am.ringerMode
                if (ringer != AudioManager.RINGER_MODE_NORMAL) am.ringerMode = AudioManager.RINGER_MODE_NORMAL
            }
        }

        fun restore() {
            if (!nm.isNotificationPolicyAccessGranted) return
            runCatching { if (ringer >= 0 && am.ringerMode != ringer) am.ringerMode = ringer }
            runCatching { if (filter > 0 && nm.currentInterruptionFilter != filter) nm.setInterruptionFilter(filter) }
        }
    }

    /** The camera's torch, if there is one. */
    private class Torch(ctx: Context) {
        private val cm = ctx.getSystemService(CameraManager::class.java)
        private val id = runCatching {
            cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
        }.getOrNull()

        fun set(on: Boolean) { id?.let { runCatching { cm.setTorchMode(it, on) } } }
    }

    /** Notices the phone being picked up (a jolt well above lying still). */
    private class Pickup(ctx: Context, private val onPickup: () -> Unit) : SensorEventListener {
        private val sm = ctx.getSystemService(SensorManager::class.java)
        private var jolts = 0

        fun start() { sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sm.registerListener(this, it, SensorManager.SENSOR_DELAY_UI) } }
        fun stop() = sm.unregisterListener(this)

        override fun onSensorChanged(e: SensorEvent) {
            val g = sqrt(e.values.take(3).sumOf { (it * it).toDouble() })
            // The phone's own buzzing shakes it a little; a hand lifting it shakes it a lot.
            if (abs(g - SensorManager.GRAVITY_EARTH) > 3.5) { if (++jolts > 3) onPickup() } else jolts = 0
        }

        override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    }
}
