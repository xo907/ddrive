package st.xo.ddrive.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Folder syncing as the phone does it, against a real DDrive host (started from this repository,
 * with a local folder standing in for Discord). The "dashboard" is a second sign-in making the
 * requests the Sync page makes; the "phone" is the agent with a folder of the test.
 */
class AgentTest {
    private lateinit var host: Process
    private lateinit var address: String
    private lateinit var phrase: String
    private lateinit var page: HostClient
    private lateinit var tmp: File
    private lateinit var here: File
    private var agent: Agent? = null
    private val device = "0123456789abcdef"

    @BeforeTest
    fun setUp() {
        val repo = System.getProperty("ddrive.repo")
        val python = System.getenv("DDRIVE_PYTHON") ?: if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3"
        host = ProcessBuilder(python, File("src/test/python/host.py").absolutePath, repo)
            .redirectError(ProcessBuilder.Redirect.DISCARD).start()
        val line = host.inputStream.bufferedReader().readLine() ?: fail("the test host did not start")
        val info = JSONObject(line)
        address = "http://127.0.0.1:${info.getInt("port")}"
        phrase = info.getString("phrase")
        page = HostClient(address, phrase)
        tmp = Files.createTempDirectory("dd-phone-").toFile()
        here = File(tmp, "Pictures").apply { mkdirs() }
    }

    @AfterTest
    fun tearDown() {
        agent?.let {
            it.stop()
            it.join()
        }
        host.outputStream.close()
        host.waitFor()
        tmp.deleteRecursively()
    }

    // ------------------------------------------------------------ helpers
    private fun newAgent(start: Boolean = true, livePoll: Double = 3600.0): Agent {
        val a = Agent(HostClient(address, phrase), device, "phone", File(tmp, "state"), File(tmp, "Removed by sync"), home = tmp.path,
                      livePoll = livePoll, liveFull = 3600.0, keepFree = 0, partSize = 64 * 1024,
                      browse = { path ->
                          val dirs = JSONArray()
                          File(path).listFiles()?.filter { it.isDirectory }?.sortedBy { it.name }
                              ?.forEach { dirs.put(JSONObject().put("name", it.name).put("path", it.path)) }
                          JSONObject().put("path", path).put("parent", File(path).parent).put("dirs", dirs).put("roots", JSONArray())
                      },
                      refuse = { if (it.local.startsWith("/") || it.local.contains(":")) null else "Give the whole path" })
        agent = a
        return if (start) a.start() else a
    }

    private fun <T> until(what: String, seconds: Int = 20, check: () -> T?): T {
        val end = System.currentTimeMillis() + seconds * 1000
        while (System.currentTimeMillis() < end) {
            val got = check()
            if (got != null && got != false) return got
            Thread.sleep(50)
        }
        fail("$what did not happen")
    }

    private fun shown(): JSONObject? {
        val devices = page.getJson("/api/sync").getJSONArray("devices")
        for (i in 0 until devices.length()) if (devices.getJSONObject(i).getString("id") == device) return devices.getJSONObject(i)
        return null
    }

    private fun connected() = until("connecting") { if (agent!!.online) shown()?.takeIf { it.getBoolean("online") } else null }

    private fun add(mode: String, remote: String = "/Backup"): String {
        val job = JSONObject().put("local", here.path).put("remote", remote).put("mode", mode).put("trigger", "manual")
        return page.postJson("/api/sync/save", JSONObject().put("device", device).put("job", job)).getJSONObject("job").getString("id")
    }

    private fun change(id: String, key: String, value: Any) {
        page.postJson("/api/sync/save", JSONObject().put("device", device).put("job", JSONObject().put("id", id).put(key, value)))
    }

    private fun history(id: String) = agent!!.snapshot().optJSONObject(id)?.optJSONArray("history")

    /** Press "Sync now" on the dashboard and wait for the pass; returns its line in the history. */
    private fun run(id: String): JSONObject {
        until("the pair reaching the phone") { agent!!.job(id) }
        val before = history(id)?.optJSONObject(0)?.optDouble("at")
        page.postJson("/api/sync/run", JSONObject().put("device", device).put("id", id))
        val res = until("the pair running") {
            history(id)?.optJSONObject(0)?.takeIf { it.optDouble("at") != before && agent!!.running == null }
        }
        assertEquals("", res.getString("error"))
        return res
    }

    private fun put(rel: String, data: ByteArray, age: Int = 100): File {
        val f = File(here, rel)
        f.parentFile.mkdirs()
        f.writeBytes(data)
        f.setLastModified(System.currentTimeMillis() - age * 1000L)
        return f
    }

    private fun local(rel: String) = File(here, rel).takeIf { it.isFile }?.readBytes()?.toString(Charsets.UTF_8)

    private fun remote(path: String): String? = try {
        ByteArrayOutputStream().also { page.download(path, it) }.toString("UTF-8")
    } catch (e: HostError) {
        if (e.status != 404) throw e
        null
    }

    private fun remoteMtime(folder: String, name: String): Double? {
        val items = page.list(folder)
        for (i in 0 until items.length()) if (items.getJSONObject(i).getString("name") == name) return items.getJSONObject(i).getDouble("mtime")
        return null
    }

    // ------------------------------------------------------------ the tests
    @Test
    fun aPhoneShowsUpAndIsSteeredFromTheDashboard() {
        newAgent()
        val dev = connected()
        assertEquals("phone", dev.getString("name"))
        assertFalse(dev.getBoolean("windows"))
        // a pair is added on the dashboard, for a folder of the phone; the phone does the copying
        put("a.jpg", "one".toByteArray())
        val big = ByteArray(300_000) { (it * 7).toByte() }
        put("Camera/b.bin", big)
        put("video.mp4.crdownload", "half a download".toByteArray())
        put(".trashed-123-old.jpg", "deleted in the gallery".toByteArray())
        val id = add("backup")
        assertEquals(2, run(id).getInt("up"))
        assertEquals("one", remote("/Backup/a.jpg"))
        assertEquals(big.size, ByteArrayOutputStream().also { page.download("/Backup/Camera/b.bin", it) }.size())
        assertNull(remote("/Backup/video.mp4.crdownload"))
        assertNull(remote("/Backup/.trashed-123-old.jpg"))
        // copies keep the file's own time, so nothing is copied twice
        assertEquals(File(here, "a.jpg").lastModified() / 1000.0, remoteMtime("/Backup", "a.jpg")!!, 0.01)
        assertEquals(0, run(id).getInt("up"))
        // a backup copies each file once: what is done with the copy on the drive is not undone
        page.delete("/Backup/a.jpg")
        assertEquals(0, run(id).getInt("up"))
        assertNull(remote("/Backup/a.jpg"))
        // the dashboard sees how it went
        val card = until("the report") { shown()!!.getJSONArray("jobs").optJSONObject(0)?.takeIf { it.getJSONObject("status").optJSONArray("history")?.length() == 3 } }
        assertEquals("Back up", card.getString("label"))
        assertEquals("idle", card.getJSONObject("status").getString("state"))
        // mirror: deleting here deletes there
        change(id, "mode", "mirror")
        until("the change") { agent!!.job(id)?.mode == "mirror" }
        assertEquals(1, run(id).getInt("up"))                   // a.jpg is back, a mirror is exact
        File(here, "Camera/b.bin").delete()
        File(here, "Camera").delete()
        assertEquals(1, run(id).getInt("deleted_remote"))
        assertNull(remote("/Backup/Camera/b.bin"))
        // pausing and removing it there reaches the phone
        page.postJson("/api/sync/pause", JSONObject().put("device", device).put("id", id).put("paused", true))
        until("the pause") { agent!!.job(id)?.enabled == false }
        page.postJson("/api/sync/remove", JSONObject().put("device", device).put("id", id))
        until("the removal") { agent!!.jobs.isEmpty() }
    }

    @Test
    fun bothWaysAndDown() {
        newAgent()
        connected()
        page.mkdir("/Shared")
        "drive".toByteArray().let { d -> page.upload("/Shared/from-drive.txt", d.size.toLong(), 1_700_000_000.0) { d.inputStream() } }
        put("from-here.txt", "here".toByteArray())
        val id = add("two-way", "/Shared")
        var res = run(id)
        assertEquals(1 to 1, res.getInt("up") to res.getInt("down"))
        assertEquals("drive", local("from-drive.txt"))
        assertEquals("here", remote("/Shared/from-here.txt"))
        assertEquals(1_700_000_000_000, File(here, "from-drive.txt").lastModified())
        assertEquals(0 to 0, run(id).let { it.getInt("up") to it.getInt("down") })
        // a deletion on either side goes to the other
        File(here, "from-drive.txt").delete()
        page.delete("/Shared/from-here.txt")
        res = run(id)
        assertEquals(1 to 1, res.getInt("deleted_remote") to res.getInt("deleted_local"))
        assertNull(local("from-here.txt"))
        assertNull(remote("/Shared/from-drive.txt"))
        assertTrue(File(tmp, "Removed by sync").walk().any { it.name == "from-here.txt" })      // moved aside, not deleted
        // a file changed on both sides is kept twice; the newer one keeps the name
        put("both.txt", "first".toByteArray())
        run(id)
        put("both.txt", "changed here".toByteArray(), age = 50)
        "changed on the drive".toByteArray().let { d -> page.upload("/Shared/both.txt", d.size.toLong(), System.currentTimeMillis() / 1000.0) { d.inputStream() } }
        assertEquals(1, run(id).getInt("conflicts"))
        assertEquals("changed on the drive", local("both.txt"))
        assertEquals(listOf(12L, 20L), here.listFiles()!!.map { it.length() }.sorted())
        assertEquals(2, page.list("/Shared").length())
        assertEquals(0, run(id).let { it.getInt("up") + it.getInt("down") + it.getInt("conflicts") })
        // drive -> phone, exactly
        change(id, "mode", "download-mirror")
        until("the change") { agent!!.job(id)?.mode == "download-mirror" }
        put("only-here.txt", "x".toByteArray())
        assertEquals(1, run(id).getInt("deleted_local"))
        assertNull(local("only-here.txt"))
    }

    @Test
    fun aLivePairRunsWhenSomethingChangesHere() {
        newAgent(livePoll = 1.0)
        connected()
        put("one.jpg", "1".toByteArray())
        val job = JSONObject().put("local", here.path).put("remote", "/Live").put("mode", "backup").put("trigger", "live")
        val id = page.postJson("/api/sync/save", JSONObject().put("device", device).put("job", job)).getJSONObject("job").getString("id")
        until("the first pass") { remote("/Live/one.jpg") }
        Thread.sleep(8000)                       // nothing changes: the folder is looked at, the host is not asked again
        val passes = history(id)!!.length()
        assertTrue(passes <= 2, "passes: $passes")
        put("two.jpg", "2".toByteArray())        // a new photo: it goes by itself
        until("the new file going up") { remote("/Live/two.jpg") }
        until("that pass being over") { history(id)!!.length() > passes }       // (the file is on the host before the pass ends)
    }

    @Test
    fun aBigFileGoesInPartsAndGoesOnWhereItStopped() {
        val data = ByteArray(300_000) { (it * 13).toByte() }
        val f = put("film.mp4", data)
        val mtime = f.lastModified() / 1000.0
        val client = HostClient(address, phrase)
        // the first part arrives, then the connection is lost
        var parts = 0
        try {
            client.uploadParts("/film.mp4", f, f.length(), mtime, stop = { parts >= 1 }, part = 64 * 1024, onPart = { parts++ })
            fail("it was not stopped")
        } catch (e: Stopped) {
            // as meant
        }
        assertNull(remote("/film.mp4"))                         // not a file before all of it is there
        // the next try goes on from there
        val heard = ArrayList<Long>()
        val got = client.uploadParts("/film.mp4", f, f.length(), mtime, part = 64 * 1024, onPart = { heard.add(it) })
        assertTrue(got.getBoolean("done"))
        assertEquals(64L * 1024, heard[0])                      // what was there already
        assertEquals(300_000L, heard.sum())
        assertTrue(data.contentEquals(ByteArrayOutputStream().also { page.download("/film.mp4", it) }.toByteArray()))
        assertEquals(mtime, remoteMtime("/", "film.mp4")!!, 0.001)
        // and a pair sends its files that way (several parts for the big one), counting as it goes
        newAgent()
        connected()
        val id = add("backup", "/Films")
        val res = run(id)
        assertEquals(1 to 300_000L, res.getInt("up") to res.getLong("bytes"))
        assertTrue(data.contentEquals(ByteArrayOutputStream().also { page.download("/Films/film.mp4", it) }.toByteArray()))
    }

    @Test
    fun moveFreesThePhone() {
        newAgent()
        connected()
        put("clip.mp4", ByteArray(1000) { 1 })
        val id = add("move", "/Moved")
        run(id)
        // it goes from the phone once the host has it safely in "Discord" (here: at once, or a pass later)
        until("the file leaving the phone", 40) {
            if (File(here, "clip.mp4").exists()) {
                run(id)
                null
            } else true
        }
        assertEquals(1000, ByteArrayOutputStream().also { page.download("/Moved/clip.mp4", it) }.size())
    }

    @Test
    fun foldersAreLookedAtOnThePhoneAndPairsAreKeptThere() {
        newAgent()
        connected()
        File(here, "Camera").mkdirs()
        val got = page.getJson("/api/sync/browse?device=$device&path=" + java.net.URLEncoder.encode(here.path, "UTF-8"))
        assertEquals("Camera", got.getJSONArray("dirs").getJSONObject(0).getString("name"))
        // a folder that isn't there, and one the phone refuses, are told by the pair
        val gone = page.postJson("/api/sync/save", JSONObject().put("device", device).put("job",
            JSONObject().put("local", here.path + "-not").put("remote", "/A").put("trigger", "manual"))).getJSONObject("job").getString("id")
        until("the pair") { agent!!.job(gone) }
        page.postJson("/api/sync/run", JSONObject().put("device", device).put("id", gone))
        until("the error on the dashboard") {
            shown()!!.getJSONArray("jobs").getJSONObject(0).getJSONObject("status").takeIf { it.getString("state") == "error" && "isn't there" in it.getString("error") }
        }
        val part = page.postJson("/api/sync/save", JSONObject().put("device", device).put("job",
            JSONObject().put("local", "Pictures").put("remote", "/B").put("trigger", "manual"))).getJSONObject("job").getString("id")
        until("the pair") { agent!!.job(part) }
        page.postJson("/api/sync/run", JSONObject().put("device", device).put("id", part))
        until("the refusal") { agent!!.snapshot().getJSONObject(part).optString("error").takeIf { "whole path" in it } }
        // a phone that was restarted knows its pairs, and says them to a host that was restarted
        agent!!.stop()
        agent!!.join()
        val again = newAgent(start = false)
        assertEquals(setOf(gone, part), again.jobs.map { it.id }.toSet())
        again.start()
        until("coming back") { shown()?.takeIf { it.getBoolean("online") && it.getJSONArray("jobs").length() == 2 } }
        // turned off: off the host's list
        again.stop()
        again.join()
        again.bye()
        assertNull(shown())
    }

    @Test
    fun whatIsLeftOut() {
        val ex = Excluder(listOf("*.iso", "Temp/", "Raw/2020"))
        for (p in listOf("movie.mp4.crdownload", "a/b/~\$report.docx", "Thumbs.db", "x/big.ISO", "Temp", "Raw/2020/a.dng",
                         "DCIM/.thumbnails", ".pending-1700000000-IMG_1.jpg", "DCIM/.trashed-1700000000-IMG_2.jpg"))
            assertTrue(ex(p), p)
        for (p in listOf("movie.mp4", "Raw/2021/a.dng", "template/a.txt", "DCIM/Camera/IMG_1.jpg", "notes.tmpl"))
            assertFalse(ex(p), p)
        assertTrue(globRegex("img_[0-9]?.*").matches("img_12.jpg"))
        assertFalse(globRegex("img_[!0-9]*").matches("img_1"))
        assertEquals("a/b (conflict phone 2023-11-14 22.13).txt".length, conflictCopyName("a/b.txt", "phone").length)
        assertTrue(same(Stamp(5, 100.0), Stamp(5, 101.9)))
        assertFalse(same(Stamp(5, 100.0), Stamp(6, 100.0)))
        assertFalse(same(Stamp(5, 100.0), null))
        assertNotNull(nextDaily("03:00", now()).takeIf { it > now() && it <= now() + 86400 + 3700 })
    }
}
