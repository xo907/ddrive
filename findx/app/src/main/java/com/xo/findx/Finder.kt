package com.xo.findx

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.ParcelUuid
import androidx.compose.runtime.mutableStateMapOf
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeout
import org.json.JSONObject

/**
 * Looking for your devices nearby, over Bluetooth: which are in range, how far (smoothed signal
 * strength, good to about a metre close up, a few metres across a room), and whether you are getting
 * warmer. Runs only while the Nearby screen is open.
 */
@SuppressLint("MissingPermission")
object Finder {
    class Near(
        val key: String,              // the device id if known, else the Bluetooth address
        val device: String?,
        val name: String,
        val address: String,
        val rssi: Double,
        val metres: Double,
        val trend: Double,
        val uwb: Boolean,
        val alert: Boolean,
        val seen: Long,
    )

    val near = mutableStateMapOf<String, Near>()
    private val filters = HashMap<String, SignalFilter>()
    private val names = HashMap<String, String>()          // device id -> name, from cards read over Bluetooth
    private var scanning: ScanCallback? = null

    fun start(ctx: Context) {
        if (scanning != null) return
        val keys = Store.keys(ctx) ?: return
        if (!Beacon.available(ctx)) return
        val scanner = ctx.getSystemService(BluetoothManager::class.java).adapter.bluetoothLeScanner ?: return
        Store.peers(ctx).forEach { names[it.device] = it.name }
        val me = Store.device(ctx)
        val cb = object : ScanCallback() {
            override fun onScanResult(type: Int, r: ScanResult) {
                val data = r.scanRecord?.getServiceData(ParcelUuid(Beacon.SERVICE)) ?: return
                val now = System.currentTimeMillis()
                val seen = BeaconFormat.decode(keys, data, now) ?: return
                val device = BeaconFormat.which(keys, seen.deviceTag, names.keys + Store.peers(ctx).map { it.device }, now)
                if (device == me) return
                val key = device ?: r.device.address
                val f = filters.getOrPut(key) { SignalFilter() }
                val v = f.add(r.rssi, now)
                near[key] = Near(key, device, device?.let { names[it] } ?: "One of your devices", r.device.address, v,
                    SignalFilter.metres(v), f.trend(), seen.uwb, seen.alert, now)
            }
        }
        val filter = ScanFilter.Builder().setServiceData(ParcelUuid(Beacon.SERVICE), byteArrayOf(BeaconFormat.VERSION), byteArrayOf(-1)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).setReportDelay(0).build()
        runCatching { scanner.startScan(listOf(filter), settings, cb); scanning = cb; Meter.on("scan") }
    }

    fun stop(ctx: Context) {
        val cb = scanning ?: return
        scanning = null
        Meter.off(ctx, "scan")
        runCatching { ctx.getSystemService(BluetoothManager::class.java).adapter.bluetoothLeScanner?.stopScan(cb) }
        filters.clear()
        near.clear()
    }

    /** Forgets devices not heard for a while (called by the screen). */
    fun prune(now: Long) {
        near.keys.filter { now - (near[it]?.seen ?: 0) > 12_000 }.forEach { near.remove(it); filters.remove(it) }
    }

    // ------------------------------------------------------------ talking to a nearby device

    /**
     * Connects to a nearby device and sends it a command with proof for its challenge. Returns its card
     * (device id and name) when it has one. Throws when it can't be reached or refuses.
     */
    suspend fun send(ctx: Context, address: String, cmd: Byte, args: ByteArray = ByteArray(0), readUwb: Boolean = false): Pair<JSONObject?, ByteArray?> {
        val keys = Store.keys(ctx) ?: throw IllegalStateException("FindX isn't linked yet")
        val adapter = ctx.getSystemService(BluetoothManager::class.java).adapter
        val device: BluetoothDevice = adapter.getRemoteDevice(address)
        val g = Gatt()
        val gatt = device.connectGatt(ctx, false, g, BluetoothDevice.TRANSPORT_LE)
        try {
            withTimeout(10_000) { g.connected.await() }
            // Room for a whole sealed command in one packet (the default fits 20 bytes).
            if (gatt.requestMtu(247)) runCatching { withTimeout(4_000) { g.mtu.await() } }
            gatt.discoverServices()
            withTimeout(8_000) { g.discovered.await() }
            val service = gatt.getService(Beacon.SERVICE) ?: throw IllegalStateException("That device isn't FindX")
            val card = runCatching {
                val sealed = g.read(gatt, service.getCharacteristic(Beacon.CARD))
                JSONObject(String(keys.open(keys.report, "findx card", sealed))).also { names[it.getString("d")] = it.optString("n") }
            }.getOrNull()
            val challenge = g.read(gatt, service.getCharacteristic(Beacon.CHALLENGE))
            val proof = keys.commandProof(challenge, byteArrayOf(cmd) + args)
            g.write(gatt, service.getCharacteristic(Beacon.CONTROL), byteArrayOf(cmd) + proof + args)
            var uwb: ByteArray? = null
            if (readUwb) {
                for (i in 0 until 20) {                           // the other phone needs a moment to open its side
                    uwb = g.read(gatt, service.getCharacteristic(Beacon.UWB))
                    if (uwb.isNotEmpty()) break
                    kotlinx.coroutines.delay(250)
                }
            }
            return card to uwb
        } finally {
            runCatching { gatt.disconnect(); gatt.close() }
        }
    }

    private class Gatt : BluetoothGattCallback() {
        val connected = CompletableDeferred<Unit>()
        val discovered = CompletableDeferred<Unit>()
        val mtu = CompletableDeferred<Int>()
        @Volatile private var pending: CompletableDeferred<ByteArray>? = null

        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) connected.complete(Unit)
            else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                val e = IllegalStateException("The connection dropped")
                connected.completeExceptionally(e); discovered.completeExceptionally(e); pending?.completeExceptionally(e)
            }
        }

        override fun onMtuChanged(gatt: BluetoothGatt, value: Int, status: Int) { mtu.complete(value) }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) { discovered.complete(Unit) }

        override fun onCharacteristicRead(gatt: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) pending?.complete(value) else pending?.completeExceptionally(IllegalStateException("read refused ($status)"))
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, ch: BluetoothGattCharacteristic, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) pending?.complete(ByteArray(0))
            else pending?.completeExceptionally(IllegalStateException("The other phone refused (it may be linked to other words)"))
        }

        suspend fun read(gatt: BluetoothGatt, ch: BluetoothGattCharacteristic): ByteArray {
            val d = CompletableDeferred<ByteArray>().also { pending = it }
            if (!gatt.readCharacteristic(ch)) throw IllegalStateException("couldn't read")
            return withTimeout(6_000) { d.await() }
        }

        suspend fun write(gatt: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray) {
            val d = CompletableDeferred<ByteArray>().also { pending = it }
            gatt.writeCharacteristic(ch, value, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
            withTimeout(6_000) { d.await() }
        }
    }
}
