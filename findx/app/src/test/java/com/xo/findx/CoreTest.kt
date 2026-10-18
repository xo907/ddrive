package com.xo.findx

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import st.xo.ddrive.core.VaultError
import st.xo.ddrive.core.Words
import java.util.Random

class GeoTest {
    @Test fun distanceIsRight() {
        // Two points 0.001° of latitude apart are 111.2 m apart anywhere.
        assertEquals(111.2, Geo.distance(44.0, -63.0, 44.001, -63.0), 0.2)
        assertEquals(0.0, Geo.distance(1.0, 2.0, 1.0, 2.0), 1e-9)
        val (e, n) = Geo.toLocal(44.0, -63.0, 44.0003, -62.9995)
        val (lat, lon) = Geo.fromLocal(44.0, -63.0, e, n)
        assertEquals(44.0003, lat, 1e-9); assertEquals(-62.9995, lon, 1e-9)
    }

    @Test fun settleAveragesNoiseAndGetsTighterWithTime() {
        val rnd = Random(7)
        val s = Settle()
        val (tLat, tLon) = 44.6488 to -63.5752
        for (i in 0 until 90) {
            val (lat, lon) = Geo.fromLocal(tLat, tLon, rnd.nextGaussian() * 4, rnd.nextGaussian() * 4)
            s.add(Fix(lat, lon, 4.0, i * 1_000L))
        }
        val e = s.estimate()!!
        // 90 s of 4 m noise, averaged: within a couple of metres of the truth...
        assertTrue(Geo.distance(e.lat, e.lon, tLat, tLon) < 2.0)
        // ...and it says so honestly: better than one fix, never better than the floor.
        assertTrue(e.acc < 4.0)
        assertTrue(e.acc >= Settle.FLOOR)
    }

    @Test fun settleDoesNotOverclaimFromAQuickBurst() {
        // Ten fixes in two seconds are nearly the same reading ten times: not ten times better.
        val s = Settle()
        for (i in 0 until 10) s.add(Fix(44.0, -63.0, 5.0, i * 200L))
        assertTrue(s.estimate()!!.acc > 3.5)
    }

    @Test fun settleIgnoresAWildFix() {
        val s = Settle()
        for (i in 0 until 20) s.add(Fix(44.0, -63.0, 3.0, i * 1_000L))
        val (lat, lon) = Geo.fromLocal(44.0, -63.0, 400.0, 0.0)    // a jump to a mast 400 m away
        s.add(Fix(lat, lon, 5.0, 21_000L))
        val e = s.estimate()!!
        assertTrue(Geo.distance(e.lat, e.lon, 44.0, -63.0) < 1.0)
    }

    @Test fun signalFilterSmoothsAndTrends() {
        val f = SignalFilter()
        val rnd = Random(3)
        var t = 0L
        repeat(40) { f.add((-80 + rnd.nextGaussian() * 6).toInt(), t); t += 200 }
        assertEquals(-80.0, f.value, 4.0)
        repeat(30) { i -> f.add((-80 + i).coerceAtMost(-55), t); t += 200 }   // walking closer
        assertTrue(f.trend() > 0)
        assertTrue(SignalFilter.metres(-59.0) in 0.9..1.1)
        assertTrue(SignalFilter.metres(-80.0) > SignalFilter.metres(-65.0))
    }

    @Test fun poseFromGravity() {
        assertEquals(Pose.FaceUp, Pose.of(0.1, 0.2, 9.8))
        assertEquals(Pose.FaceDown, Pose.of(0.0, 0.3, -9.7))
        assertEquals(Pose.Upright, Pose.of(0.2, 9.7, 0.5))
        assertEquals(Pose.OnSide, Pose.of(9.7, 0.4, 0.3))
        assertEquals(Pose.Tilted, Pose.of(0.0, 6.0, 7.7))
        assertEquals(Pose.Unknown, Pose.of(0.0, 0.0, 20.0))   // shaken, not lying
    }

    @Test fun spotsTellPlacesApart() {
        val desk = Signature(mapOf("a" to -45, "b" to -70, "c" to -82), mapOf("a" to 3.1), magnet = 48.0, pressure = 1010.2)
        val bed = Signature(mapOf("a" to -72, "b" to -48, "d" to -60), mapOf("a" to 9.5), magnet = 31.0, pressure = 1009.9)
        val nowAtDesk = Signature(mapOf("a" to -47, "b" to -69, "c" to -85), mapOf("a" to 3.4), magnet = 47.0, pressure = 1010.1)
        assertTrue(Spots.similarity(nowAtDesk, desk) > Spots.similarity(nowAtDesk, bed) + 0.3)
        assertEquals("desk", Spots.best(nowAtDesk, listOf("desk" to desk, "bed" to bed))!!.first)
        assertNull(Spots.best(Signature(), listOf("desk" to desk)))
        assertEquals(1.0, Spots.similarity(desk, desk), 1e-9)
    }

    @Test fun sunIsUpAtNoonAndDownAtMidnight() {
        // Halifax, 21 June 2026: local noon ~16:30 UTC, midnight ~04:30 UTC.
        val noon = java.time.Instant.parse("2026-06-21T16:30:00Z").toEpochMilli()
        val night = java.time.Instant.parse("2026-06-21T04:30:00Z").toEpochMilli()
        assertEquals(68.5, Sun.altitude(44.65, -63.57, noon), 1.5)
        assertTrue(Sun.altitude(44.65, -63.57, night) < -15)
        assertEquals(1.0, Sun.daylight(44.65, -63.57, noon), 0.0)
        assertEquals(0.0, Sun.daylight(44.65, -63.57, night), 0.0)
    }
}

class PathTest {
    private val o = Fix(44.6488, -63.5752, 5.0, 0)
    private fun at(e: Double, n: Double, t: Long, acc: Double): Fix {
        val (lat, lon) = Geo.fromLocal(o.lat, o.lon, e, n)
        return Fix(lat, lon, acc, t)
    }

    /** A walk east then north at 1.4 m/s, a satellite position every 8 s, wandering by about 5 m. */
    private fun walk(rnd: Random, n: Int, t0: Long = 1_000_000L): List<Pair<Fix, Pair<Double, Double>>> = (0 until n).map { i ->
        val t = i * 8.0
        val (e, nn) = if (i < n / 2) Pair(1.4 * t, 0.0) else Pair(1.4 * (n / 2) * 8.0, 1.4 * (t - (n / 2) * 8.0))
        at(e + rnd.nextGaussian() * 5, nn + rnd.nextGaussian() * 5, t0 + (t * 1000).toLong(), 8.0) to Pair(e, nn)
    }

    private fun error(f: Fix, truth: Pair<Double, Double>): Double {
        val (e, n) = Geo.toLocal(o.lat, o.lon, f.lat, f.lon)
        return Math.hypot(e - truth.first, n - truth.second)
    }

    @Test fun followsAWalkRoundACornerAndSmoothsIt() {
        val pf = PathFilter()
        val raw = ArrayList<Double>(); val clean = ArrayList<Double>()
        for ((f, truth) in walk(Random(7), 60)) {
            val c = pf.add(f)
            assertNotNull("a good position left out at ${f.t}", c)
            raw.add(error(f, truth)); clean.add(error(c!!, truth))
        }
        assertTrue("worst ${clean.max()}", clean.max() < 22)
        assertTrue("smoother: ${clean.average()} vs ${raw.average()}", clean.drop(5).average() < raw.drop(5).average())
    }

    @Test fun leavesOutJumpsMastsAndStalePositions() {
        val pf = PathFilter()
        val w = walk(Random(3), 40)
        for ((i, p) in w.withIndex()) {
            val (f, truth) = p
            when (i) {
                10 -> assertNull("a jump 450 m off", pf.add(at(truth.first + 320, truth.second - 320, f.t - 4_000, 30.0)))
                15 -> assertNull("a mast's guess among satellite positions", pf.add(at(truth.first + 250, truth.second + 150, f.t - 4_000, 140.0)))
                18 -> assertNull("a rough Wi-Fi one among good ones", pf.add(at(truth.first + 90, truth.second, f.t - 4_000, 70.0)))
                20 -> assertNull("older than the last", pf.add(at(truth.first, truth.second, f.t - 30_000, 8.0)))
            }
            val c = pf.add(f)
            assertNotNull(c)
            assertTrue("off by ${error(c!!, truth)} at $i", error(c, truth) < 22)
        }
    }

    @Test fun startsAgainWhenItReallyGotSomewhereElse() {
        val pf = PathFilter()
        val w = walk(Random(5), 10)
        w.forEach { pf.add(it.first) }
        val t = w.last().first.t
        // Positions resume 600 m away (a gap in them; a bus): the first two are doubted, then it's believed.
        val there = (1..3).map { at(1_000.0 + it * 10, 400.0, t + it * 8_000L, 6.0) }
        assertNull(pf.add(there[0]))
        assertNull(pf.add(there[1]))
        val c = pf.add(there[2])
        assertNotNull(c)
        assertTrue(Geo.distance(c!!, there[2]) < 1)
        // A long gap starts the path again at once.
        assertNotNull(pf.add(at(0.0, 0.0, t + 10 * 60_000L, 8.0)))
    }
}

class SecretsTest {
    private val phrase = Words.keyToPhrase(ByteArray(32) { it.toByte() })
    private val keys = FxKeys(Words.phraseToKey(phrase))

    @Test fun sameWordsSameKeysEachJobItsOwn() {
        val again = FxKeys(Words.phraseToKey(phrase))
        assertArrayEquals(keys.report, again.report)
        assertEquals(keys.group, again.group)
        assertFalse(keys.report.contentEquals(keys.history))
        assertFalse(keys.command.contentEquals(keys.beacon))
        assertNotEquals(keys.group, FxKeys(ByteArray(32) { 9 }).group)
        assertEquals(32, keys.group.length)
    }

    @Test fun sealedOpensOnlyWithItsKeyAndLabel() {
        val blob = keys.seal(keys.report, "findx report", "hello".toByteArray())
        assertEquals("hello", String(keys.open(keys.report, "findx report", blob)))
        try { keys.open(keys.history, "findx report", blob); fail() } catch (e: VaultError) {}
        try { keys.open(keys.report, "findx command", blob); fail() } catch (e: VaultError) {}
        blob[blob.size - 1] = (blob[blob.size - 1] + 1).toByte()
        try { keys.open(keys.report, "findx report", blob); fail() } catch (e: VaultError) {}
    }

    @Test fun beaconIsRecognisedOnlyByItsOwner() {
        val now = 1_800_000_000_000L
        val w = FxKeys.window(now)
        val data = BeaconFormat.encode(keys, "dev1", w, uwb = true, alert = false)
        assertEquals(10, data.size)
        val seen = BeaconFormat.decode(keys, data, now + 60_000)!!
        assertTrue(seen.uwb)
        assertEquals("dev1", BeaconFormat.which(keys, seen.deviceTag, listOf("dev0", "dev1"), now))
        // A stranger's keys don't recognise it.
        assertNull(BeaconFormat.decode(FxKeys(ByteArray(32) { 5 }), data, now))
        // The next window says something else entirely (nobody can follow it by its name).
        assertFalse(data.contentEquals(BeaconFormat.encode(keys, "dev1", w + 1, uwb = true, alert = false)))
        // Still recognised a window later (clocks differ), not hours later.
        assertNotNull(BeaconFormat.decode(keys, data, now + FxKeys.WINDOW_MS))
        assertNull(BeaconFormat.decode(keys, data, now + 5 * FxKeys.WINDOW_MS))
    }

    @Test fun commandProofIsBoundToChallengeAndKey() {
        val c1 = ByteArray(16) { 1 }
        val c2 = ByteArray(16) { 2 }
        val p = keys.commandProof(c1, byteArrayOf(Beacon.CMD_RING))
        assertArrayEquals(p, keys.commandProof(c1, byteArrayOf(Beacon.CMD_RING)))
        assertFalse(p.contentEquals(keys.commandProof(c2, byteArrayOf(Beacon.CMD_RING))))
        assertFalse(p.contentEquals(keys.commandProof(c1, byteArrayOf(Beacon.CMD_STOP))))
        assertFalse(p.contentEquals(FxKeys(ByteArray(32) { 5 }).commandProof(c1, byteArrayOf(Beacon.CMD_RING))))
    }

    /** The same keys and names as the browser makes them (findx-core.js), or the dashboard couldn't find or open anything. */
    @Test fun sameAsTheBrowser() {
        val k = FxKeys(ByteArray(32) { it.toByte() })
        assertEquals("90a556e32eca0810fbe7fb7acd85b2bf", k.group)
        assertEquals("4dfd295309d3a659b53a4ad5c53ae172c3965163422442e5211157fd9f0f7ec4", FxKeys.hex(k.report))
        assertEquals("/FindX/h/792f011892892323/fa6db7b21fcc81303eb7b20ef69a533f.fxh", Segments.path(k, "1".repeat(32), 500_000L))
    }

    @Test fun historyPathsGiveNothingAway() {
        val h = 500_000L
        val p = Segments.path(keys, "dev1", h)
        assertTrue(p.startsWith("/FindX/h/"))
        assertEquals(p, Segments.path(keys, "dev1", h))
        assertNotEquals(p, Segments.path(keys, "dev1", h + 1))
        assertNotEquals(p, Segments.path(keys, "dev2", h))
        assertFalse(p.contains("dev1") || p.contains(h.toString()))
    }
}

class CircleTest {
    private val key = FxKeys.b64url(ByteArray(32) { it.toByte() })

    /** The same keys as the browser makes from the invitation (findx-core.js FX.circleKeys), or nothing would open. */
    @Test fun sameAsTheBrowser() {
        val c = Circle("c".repeat(32), key, "https://dd.example.com", "Family")
        assertEquals("9ce4809cd3745cabd32b106d27ac938b7fed4679e7e1f798bc694bac554a4671", FxKeys.hex(c.seal))
        assertEquals("0488ae8bf551f2402013719a6bbe1b83", c.auth)
        assertEquals("https://dd.example.com/j/${"c".repeat(32)}#$key", c.invite)
    }

    @Test fun invitations() {
        val id = "a1".repeat(16)
        assertEquals(Triple("https://dd.example.com", id, key), Circle.parseInvite("Join my circle: https://dd.example.com/j/$id#$key"))
        assertEquals(Triple("https://dd.example.com:8443", id, key),
            Circle.parseInvite("findx://join?host=https%3A%2F%2Fdd.example.com%3A8443&c=$id#$key"))
        assertNull(Circle.parseInvite("https://dd.example.com/j/$id"))                  // no key
        assertNull(Circle.parseInvite("https://dd.example.com/f/$id#$key"))             // a shared link, not an invitation
        assertNull(Circle.parseInvite("findx://join?host=javascript:alert(1)&c=$id#$key"))
        assertNull(Circle.parseInvite("findx://join?host=https%3A%2F%2Fa.example&c=nothex#$key"))
    }

    @Test fun arrivingAndLeavingAPlace() {
        val home = Place("h", "Home", 44.0, -63.0, 100.0)
        val work = Place("w", "Work", 44.05, -63.0, 150.0)
        fun at(east: Double, acc: Double = 10.0): Fix { val (lat, lon) = Geo.fromLocal(44.0, -63.0, east, 0.0); return Fix(lat, lon, acc, 0) }
        assertEquals("h", Circles.placeAt(listOf(home, work), at(40.0), null)?.id)
        assertNull(Circles.placeAt(listOf(home, work), at(300.0), null))
        // Just outside the edge after being in: still there (no flicker at the edge)...
        assertEquals("h", Circles.placeAt(listOf(home), at(130.0), "h")?.id)
        // ...but not if it hadn't arrived, and not well beyond the edge.
        assertNull(Circles.placeAt(listOf(home), at(130.0), null))
        assertNull(Circles.placeAt(listOf(home), at(260.0), "h"))
    }

    @Test fun facesHaveTheBrowsersColour() {
        // FX.hue('ab12') is 354 in the browser.
        var h = 0L
        for (ch in "ab12") h = (h * 31 + ch.code) and 0xFFFFFFFFL
        assertEquals(354L, h % 360)
    }
}

class EnergyTest {
    private val p = Profile(cpuMa = 100.0, gpsMa = 30.0, wifiMa = 100.0, cellMa = 200.0, capacityMah = 5000.0)

    @Test fun eachPartFromWhatItDid() {
        val hour = 3_600_000L
        val m = Energy.mah(Usage(gpsMs = hour, cpuMs = 36_000), p)
        assertEquals(30.0, m.getValue("satellites"), 1e-9)       // an hour of satellites at 30 mA
        assertEquals(1.0, m.getValue("processor"), 1e-9)         // 36 s of processing at 100 mA
        assertEquals(31.0, Energy.total(Usage(gpsMs = hour, cpuMs = 36_000), p), 1e-9)
        assertEquals(0.62, Energy.percent(Usage(gpsMs = hour, cpuMs = 36_000), p), 1e-9)
    }

    @Test fun wakingTheMobileRadioCostsItsTail() {
        // One wake-up on mobile data keeps the radio up ~6 s; on Wi-Fi a fraction of a second.
        val cell = Energy.mah(Usage(wakeCell = 1), p).getValue("network")
        val wifi = Energy.mah(Usage(wakeWifi = 1), p).getValue("network")
        assertEquals(200.0 * 6.0 / 3600, cell, 1e-9)
        assertTrue(cell > 30 * wifi)
        // A day of long waits held open (a wake-up every 4 minutes) on Wi-Fi: 3 mAh, under 0.1% of a battery.
        assertEquals(3.0, Energy.mah(Usage(wakeWifi = 360), p).getValue("network"), 1e-9)
        assertTrue(Energy.percent(Usage(wakeWifi = 360), p) < 0.1)
    }

    @Test fun addingUp() {
        val a = Usage(cpuMs = 5, wakeWifi = 1, gpsMs = 10, bleLowMs = 3)
        val b = Usage(cpuMs = 7, wakeCell = 2, gpsMs = 1, ringMs = 4)
        assertEquals(Usage(cpuMs = 12, wakeWifi = 1, wakeCell = 2, gpsMs = 11, bleLowMs = 3, ringMs = 4), a + b)
        assertEquals(a, Usage.of(org.json.JSONObject(a.json().toString())))
    }

    @Test fun thePhonesOwnProfileWhenItMakesSense() {
        val phone = Profile.from(
            mapOf("gps.on" to 22.0, "wifi.active" to 90.0, "modem.controller.tx" to 300.0, "modem.controller.rx" to 100.0,
                "camera.flashlight" to 160.0, "audio" to 70.0),
            mapOf("cpu.core_power.cluster0" to listOf(10.0, 20.0, 30.0, 40.0)), 4410.0, "battery",
        )
        assertEquals("phone", phone.source)
        assertEquals(30.0, phone.cpuMa, 0.0)          // the middle speed of the low-power cores
        assertEquals(22.0, phone.gpsMa, 0.0)
        assertEquals(200.0, phone.cellMa, 0.0)         // between sending and receiving
        assertEquals(80.0 + 70.0 + 60.0, phone.ringMa, 0.0)
        assertEquals(4410.0, phone.capacityMah, 0.0)
        // Nonsense (an emulator's placeholders, zeros) falls back to typical figures.
        val none = Profile.from(mapOf("gps.on" to 0.0, "cpu.active" to 0.1), emptyMap(), null, "battery")
        assertEquals("typical", none.source)
        assertEquals("typical", none.capacitySource)
        assertEquals(Profile().gpsMa, none.gpsMa, 0.0)
    }

    @Test fun drawRightNow() {
        assertEquals(0.0, Energy.drawNow(p, gps = false, bleFast = false, bleOn = false, scanning = false, ringing = false), 0.0)
        assertEquals(30.0 + p.bleLowMa, Energy.drawNow(p, gps = true, bleFast = false, bleOn = true, scanning = false, ringing = false), 1e-9)
    }

    @Test fun sharedLinksDontCarryBatteryUse() {
        val r = Report("d", "n", 1, fix = Fix(1.0, 2.0, 5.0, 1), use = org.json.JSONObject().put("pct", 0.4))
        assertNull(r.forShare(approximate = false).use)
        assertEquals(0.4, Report.of(org.json.JSONObject(r.toJson().toString())).use!!.getDouble("pct"), 0.0)
    }
}

class ModelTest {
    private val report = Report(
        device = "d1", name = "Phone", t = 1_000L, fix = Fix(44.648812345, -63.575234567, 1.4, 900L, alt = 30.0, speed = 0.0),
        settled = true, stillSince = 500L, pose = Pose.FaceDown, lux = 0.4, covered = true, spot = "Desk", spotScore = 0.86,
        floorDelta = 1.0, battery = 64, charging = true, network = "wifi", mode = "standby", lost = true,
        trail = listOf(Fix(44.0, -63.0, 5.0, 100L)),
    )

    @Test fun reportRoundTrips() {
        val back = Report.of(JSONObject(report.toJson().toString()))
        assertEquals(report.device, back.device)
        assertEquals(report.fix!!.lat, back.fix!!.lat, 1e-7)
        assertEquals(Pose.FaceDown, back.pose)
        assertEquals("Desk", back.spot)
        assertTrue(back.settled && back.lost && back.charging && back.covered)
        assertEquals(1, back.trail.size)
    }

    @Test fun approximateShareShowsOnlyTheTown() {
        val s = report.forShare(approximate = true)
        assertNull(s.spot)
        assertTrue(s.trail.isEmpty())
        assertEquals(Pose.Unknown, s.pose)
        assertTrue(s.fix!!.acc >= 1_000)
        assertTrue(Geo.distance(s.fix!!, report.fix!!) < 1_000)
        val exact = report.forShare(approximate = false)
        assertNull(exact.spot)                      // your rooms' names aren't shared either way
        assertEquals(report.fix!!.lat, exact.fix!!.lat, 0.0)
    }

    @Test fun segmentsRoundTrip() {
        val pts = listOf(Point(3_600_000L, 44.1, -63.2, 4.5), Point(3_660_000L, 44.1001, -63.2001, 3.0, until = 3_900_000L))
        val (d, h, back) = Segments.decode(Segments.encode("dev", 1L, pts))
        assertEquals("dev", d); assertEquals(1L, h)
        assertEquals(2, back.size)
        assertEquals(44.1001, back[1].lat, 1e-6)
        assertTrue(back[1].stay)
        assertEquals(3_900_000L, back[1].until)
    }

    @Test fun simplifyKeepsShapeAndStays() {
        // A straight walk with a stay in the middle and one corner.
        val pts = ArrayList<Point>()
        for (i in 0..20) { val (lat, lon) = Geo.fromLocal(44.0, -63.0, i * 10.0, 0.0); pts.add(Point(i * 1_000L, lat, lon, 5.0)) }
        val (sl, so) = Geo.fromLocal(44.0, -63.0, 100.0, 0.0)
        pts[10] = Point(10_000L, sl, so, 3.0, until = 99_000L)
        for (i in 1..10) { val (lat, lon) = Geo.fromLocal(44.0, -63.0, 200.0, i * 10.0); pts.add(Point(20_000L + i * 1_000L, lat, lon, 5.0)) }
        val s = Segments.simplify(pts)
        assertTrue(s.size < pts.size / 3)
        assertTrue(s.any { it.stay })
        assertEquals(pts.first(), s.first()); assertEquals(pts.last(), s.last())
    }

    @Test fun commandsRoundTrip() {
        val c = Command("ring", "d2", "web", 5L, "abc", JSONObject().put("ms", 10_000))
        val back = Command.of(JSONObject(c.toJson().toString()))
        assertEquals(c.what, back.what); assertEquals(10_000, back.args.getInt("ms"))
    }

    @Test fun ringSoundSweepsWithSharpStarts() {
        val pcm = Ring.chirps()
        assertTrue(pcm.size > 30_000)
        assertTrue(pcm.maxOf { kotlin.math.abs(it.toInt()) } > 20_000)    // loud
        assertEquals(0, pcm.last().toInt())                                 // ends in the pause
    }
}

class VersionsTest {
    @Test fun comparesAsNumbers() {
        assertTrue(Versions.newer("0.10.0", "0.9.9"))
        assertTrue(Versions.newer("1.0", "0.99.99"))
        assertFalse(Versions.newer("0.2.0", "0.2.0"))
        assertFalse(Versions.newer("0.1.9", "0.2.0"))
        assertFalse(Versions.newer("nonsense", "0.1.0"))
    }
}
