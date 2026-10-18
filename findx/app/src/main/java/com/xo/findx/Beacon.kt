package com.xo.findx

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.content.Context
import android.os.ParcelUuid
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * This phone, findable over Bluetooth by your other devices, with no internet needed.
 *
 * It advertises [BeaconFormat] about once a second (fast, ten times a second, while someone is
 * looking for it), which costs very little battery. Its name changes every 15 minutes and means
 * nothing without your key. A finder that is one of yours can connect and ask it to ring, or to
 * start Ultra-Wideband ranging; every such ask carries proof made with your key for a challenge this
 * phone just made up, so nobody else can, and nobody can replay an old one.
 */
@SuppressLint("MissingPermission")
object Beacon {
    val SERVICE: UUID = UUID.fromString("7b8e0001-3c1f-4d2e-9a5b-f1d0c0de0001")
    val CHALLENGE: UUID = UUID.fromString("7b8e0002-3c1f-4d2e-9a5b-f1d0c0de0001")
    val CONTROL: UUID = UUID.fromString("7b8e0003-3c1f-4d2e-9a5b-f1d0c0de0001")
    val CARD: UUID = UUID.fromString("7b8e0004-3c1f-4d2e-9a5b-f1d0c0de0001")
    val UWB: UUID = UUID.fromString("7b8e0005-3c1f-4d2e-9a5b-f1d0c0de0001")

    const val CMD_RING: Byte = 1
    const val CMD_STOP: Byte = 2
    const val CMD_FAST: Byte = 3
    const val CMD_UWB: Byte = 4

    private var server: BluetoothGattServer? = null
    private var advertising: AdvertiseCallback? = null
    private var window = -1L
    private var fastUntil = 0L
    private val challenges = ConcurrentHashMap<String, Pair<ByteArray, Long>>()
    @Volatile var uwbAddress: ByteArray = ByteArray(0)

    fun available(ctx: Context): Boolean {
        val bm = ctx.getSystemService(BluetoothManager::class.java) ?: return false
        return bm.adapter?.isEnabled == true && Permissions.bluetooth(ctx)
    }

    fun fast(ctx: Context, forMs: Long = 10 * 60_000L) {
        fastUntil = System.currentTimeMillis() + forMs
        restart(ctx)
    }

    val isFast get() = System.currentTimeMillis() < fastUntil

    /** Starts (or, in a new 15-minute window or speed, restarts) advertising. Called by the service now and then. */
    @Synchronized
    fun update(ctx: Context) {
        val keys = Store.keys(ctx)
        if (keys == null || !Store.beaconOn(ctx) || !Store.on(ctx) || !available(ctx)) { stop(); return }
        val w = FxKeys.window(System.currentTimeMillis())
        if (advertising != null && w == window && (isFast == lastFast)) return
        restart(ctx)
    }

    private var lastFast = false

    @Synchronized
    private fun restart(ctx: Context) {
        stopAdvertising(ctx)
        val keys = Store.keys(ctx) ?: return
        if (!available(ctx)) return
        val bm = ctx.getSystemService(BluetoothManager::class.java)
        val adv = bm.adapter.bluetoothLeAdvertiser ?: return
        if (server == null) server = openServer(ctx, bm)
        window = FxKeys.window(System.currentTimeMillis())
        lastFast = isFast
        val payload = BeaconFormat.encode(keys, Store.device(ctx), window, uwb = Precision.supported(ctx), alert = Ring.ringing.value || Store.lost(ctx))
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(if (lastFast) AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY else AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(true).setTimeout(0).build()
        val data = AdvertiseData.Builder().setIncludeDeviceName(false).setIncludeTxPowerLevel(false)
            .addServiceData(ParcelUuid(SERVICE), payload).build()
        val cb = object : AdvertiseCallback() {
            override fun onStartFailure(errorCode: Int) { Log.note(ctx, "Bluetooth beacon didn't start (error $errorCode)") }
        }
        runCatching { adv.startAdvertising(settings, data, cb); advertising = cb; meterPart = if (lastFast) "beaconFast" else "beaconLow"; Meter.on(meterPart!!) }
    }

    private var meterPart: String? = null

    private fun stopAdvertising(ctx: Context) {
        val cb = advertising ?: return
        advertising = null
        meterPart?.let { Meter.off(ctx, it) }
        meterPart = null
        runCatching { ctx.getSystemService(BluetoothManager::class.java).adapter.bluetoothLeAdvertiser?.stopAdvertising(cb) }
    }

    @Synchronized
    fun stop(ctx: Context? = null) {
        if (ctx != null) stopAdvertising(ctx) else { advertising = null; Meter.app?.let { a -> meterPart?.let { Meter.off(a, it) } }; meterPart = null }
        runCatching { server?.close() }
        server = null
    }

    private fun openServer(ctx: Context, bm: BluetoothManager): BluetoothGattServer? {
        var gatt: BluetoothGattServer? = null
        val cb = object : BluetoothGattServerCallback() {
            override fun onCharacteristicReadRequest(device: BluetoothDevice, requestId: Int, offset: Int, ch: BluetoothGattCharacteristic) {
                val value: ByteArray = when (ch.uuid) {
                    CHALLENGE -> FxKeys.bytes(16).also { challenges[device.address] = it to System.currentTimeMillis() }
                    CARD -> Store.keys(ctx)?.let { k ->
                        k.seal(k.report, "findx card", JSONObject().put("d", Store.device(ctx)).put("n", Store.deviceName(ctx))
                            .put("uwb", Precision.supported(ctx)).toString().toByteArray())
                    } ?: ByteArray(0)
                    UWB -> uwbAddress
                    else -> ByteArray(0)
                }
                val part = if (offset >= value.size) ByteArray(0) else value.copyOfRange(offset, value.size)
                gatt?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, part)
            }

            override fun onCharacteristicWriteRequest(device: BluetoothDevice, requestId: Int, ch: BluetoothGattCharacteristic, preparedWrite: Boolean,
                                                      responseNeeded: Boolean, offset: Int, value: ByteArray?) {
                val ok = ch.uuid == CONTROL && value != null && handle(ctx, device.address, value)
                if (responseNeeded) gatt?.sendResponse(device, requestId, if (ok) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_INSUFFICIENT_AUTHENTICATION, 0, null)
            }
        }
        gatt = runCatching { bm.openGattServer(ctx, cb) }.getOrNull() ?: return null
        val service = BluetoothGattService(SERVICE, BluetoothGattService.SERVICE_TYPE_PRIMARY)
        fun ch(id: UUID, props: Int, perms: Int) = BluetoothGattCharacteristic(id, props, perms)
        service.addCharacteristic(ch(CHALLENGE, BluetoothGattCharacteristic.PROPERTY_READ, BluetoothGattCharacteristic.PERMISSION_READ))
        service.addCharacteristic(ch(CARD, BluetoothGattCharacteristic.PROPERTY_READ, BluetoothGattCharacteristic.PERMISSION_READ))
        service.addCharacteristic(ch(UWB, BluetoothGattCharacteristic.PROPERTY_READ, BluetoothGattCharacteristic.PERMISSION_READ))
        service.addCharacteristic(ch(CONTROL, BluetoothGattCharacteristic.PROPERTY_WRITE, BluetoothGattCharacteristic.PERMISSION_WRITE))
        gatt.addService(service)
        return gatt
    }

    /** A command over Bluetooth: [cmd, proof(16), args...]. Acted on only with a fresh challenge and the right proof. */
    private fun handle(ctx: Context, from: String, value: ByteArray): Boolean {
        val keys = Store.keys(ctx) ?: return false
        val (challenge, at) = challenges.remove(from) ?: return false
        if (System.currentTimeMillis() - at > 30_000 || value.size < 17) return false
        val cmd = value[0]
        val proof = value.copyOfRange(1, 17)
        val args = value.copyOfRange(17, value.size)
        if (!FxKeys.same(proof, keys.commandProof(challenge, byteArrayOf(cmd) + args))) {
            Log.note(ctx, "Refused a Bluetooth command without the right proof")
            return false
        }
        when (cmd) {
            CMD_RING -> Ring.start(ctx)
            CMD_STOP -> Ring.stop(ctx)
            CMD_FAST -> fast(ctx)
            CMD_UWB -> runCatching {
                val o = JSONObject(String(keys.open(keys.command, "findx uwb", args)))
                Precision.answer(ctx, o)
            }.onFailure { return false }
            else -> return false
        }
        return true
    }
}

/** What the beacon says: [version, flags, group tag (4), device tag (4)]. */
object BeaconFormat {
    const val VERSION: Byte = 1
    const val FLAG_UWB = 1
    const val FLAG_ALERT = 2

    fun encode(keys: FxKeys, device: String, window: Long, uwb: Boolean, alert: Boolean): ByteArray {
        val flags = (if (uwb) FLAG_UWB else 0) or (if (alert) FLAG_ALERT else 0)
        return byteArrayOf(VERSION, flags.toByte()) + keys.groupTag(window) + keys.deviceTag(device, window)
    }

    class Seen(val deviceTag: ByteArray, val uwb: Boolean, val alert: Boolean)

    /**
     * Reads what a beacon says, if it is one of yours (its group tag fits this window or one next to
     * it: the two phones' clocks may differ a little).
     */
    fun decode(keys: FxKeys, data: ByteArray, now: Long): Seen? {
        if (data.size < 10 || data[0] != VERSION) return null
        val group = data.copyOfRange(2, 6)
        val w = FxKeys.window(now)
        if ((w - 1..w + 1).none { FxKeys.same(keys.groupTag(it), group) }) return null
        val flags = data[1].toInt()
        return Seen(data.copyOfRange(6, 10), flags and FLAG_UWB != 0, flags and FLAG_ALERT != 0)
    }

    /** Which of [devices] a device tag belongs to, if any. */
    fun which(keys: FxKeys, tag: ByteArray, devices: Collection<String>, now: Long): String? {
        val w = FxKeys.window(now)
        return devices.firstOrNull { d -> (w - 1..w + 1).any { FxKeys.same(keys.deviceTag(d, it), tag) } }
    }
}
