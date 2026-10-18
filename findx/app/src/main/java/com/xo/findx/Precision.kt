package com.xo.findx

import android.content.Context
import androidx.core.uwb.RangingParameters
import androidx.core.uwb.RangingResult
import androidx.core.uwb.UwbAddress
import androidx.core.uwb.UwbComplexChannel
import androidx.core.uwb.UwbDevice
import androidx.core.uwb.UwbManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.security.SecureRandom

/**
 * Precision Finding with Ultra-Wideband: when both phones have it (most recent flagship phones, e.g.
 * Galaxy S Ultra and Plus, Pixel Pro and Fold), the finder measures the distance to the other phone
 * by radio round-trip time, to about 10 cm, and the direction it lies in, to a few degrees. That is
 * what tells you which end of the table, or which cushion.
 *
 * The two phones agree on a session over Bluetooth first (Beacon / Finder: the finder's offer is
 * sealed with your command key, so only your devices can start one), then range directly. Nothing
 * of it goes over the internet.
 */
object Precision {
    class Reading(val metres: Double, val azimuth: Double?, val elevation: Double?, val t: Long)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var answering: Job? = null

    fun supported(ctx: Context) = ctx.packageManager.hasSystemFeature("android.hardware.uwb") && Permissions.uwb(ctx)

    private fun params(sessionId: Int, key: ByteArray, channel: UwbComplexChannel, peer: ByteArray) = RangingParameters(
        uwbConfigType = RangingParameters.CONFIG_UNICAST_DS_TWR,
        sessionId = sessionId,
        subSessionId = 0,
        sessionKeyInfo = key,
        subSessionKeyInfo = null,
        complexChannel = channel,
        peerDevices = listOf(UwbDevice(UwbAddress(peer))),
        updateRateType = RangingParameters.RANGING_UPDATE_RATE_FREQUENT,
    )

    /**
     * The finder's side: ranges to the nearby phone at Bluetooth [address] and hands each reading to
     * [onReading] until cancelled. Throws when the other phone can't take part.
     */
    suspend fun range(ctx: Context, address: String, onReading: (Reading) -> Unit) {
        val keys = Store.keys(ctx) ?: throw IllegalStateException("FindX isn't linked yet")
        val manager = UwbManager.createInstance(ctx)
        val controller = manager.controllerSessionScope()
        val random = SecureRandom()
        val sessionId = random.nextInt(Int.MAX_VALUE - 1) + 1
        val key = ByteArray(8).also { random.nextBytes(it) }
        val ch = controller.uwbComplexChannel
        val offer = JSONObject().put("addr", FxKeys.b64(controller.localAddress.address)).put("ch", ch.channel).put("pre", ch.preambleIndex)
            .put("sid", sessionId).put("key", FxKeys.b64(key))
        val (_, peer) = Finder.send(ctx, address, Beacon.CMD_UWB, keys.seal(keys.command, "findx uwb", offer.toString().toByteArray()), readUwb = true)
        if (peer == null || peer.isEmpty()) throw IllegalStateException("The other phone didn't start Ultra-Wideband")
        controller.prepareSession(params(sessionId, key, ch, peer)).collect { r ->
            if (r is RangingResult.RangingResultPosition) {
                val p = r.position
                val d = p.distance?.value ?: return@collect
                onReading(Reading(d.toDouble(), p.azimuth?.value?.toDouble(), p.elevation?.value?.toDouble(), System.currentTimeMillis()))
            }
        }
    }

    /** The lost phone's side: takes part in a session a finder offered (over Bluetooth), for up to 5 minutes. */
    fun answer(ctx: Context, offer: JSONObject) {
        if (!supported(ctx)) throw IllegalStateException("no Ultra-Wideband here")
        answering?.cancel()
        answering = scope.launch {
            try {
                val controlee = UwbManager.createInstance(ctx).controleeSessionScope()
                Beacon.uwbAddress = controlee.localAddress.address
                val ch = UwbComplexChannel(offer.getInt("ch"), offer.getInt("pre"))
                withTimeoutOrNull(5 * 60_000L) {
                    controlee.prepareSession(params(offer.getInt("sid"), FxKeys.unb64(offer.getString("key")), ch, FxKeys.unb64(offer.getString("addr"))))
                        .collect { r -> if (r is RangingResult.RangingResultPeerDisconnected) throw kotlinx.coroutines.CancellationException("done") }
                }
            } catch (e: Exception) {
                if (e !is kotlinx.coroutines.CancellationException) Log.note(ctx, "Ultra-Wideband stopped: ${e.message}")
            } finally {
                Beacon.uwbAddress = ByteArray(0)
            }
        }
    }
}
