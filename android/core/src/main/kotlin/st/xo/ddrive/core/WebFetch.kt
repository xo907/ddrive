package st.xo.ddrive.core

import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URI
import java.net.URLDecoder
import java.nio.charset.Charset
import java.util.Base64

/**
 * What the computer with the drive does for other accounts and may not do for an end-to-end encrypted
 * one, because it would see what is fetched: reading what a web page says about itself (for a bookmark)
 * and saving the file at a web address. A device of the account does it instead (see ddrive/pagepreview.py,
 * which this follows).
 */
object WebFetch {
    private const val PAGE_MAX = 1024 * 1024
    private const val IMAGE_MAX = 4 * 1024 * 1024
    private const val AGENT = "Mozilla/5.0 (compatible; DDrive; link preview) facebookexternalhit/1.1"
    private val IMAGE_TYPES = mapOf("image/jpeg" to "jpg", "image/png" to "png", "image/webp" to "webp", "image/gif" to "gif", "image/avif" to "avif",
                                    "image/x-icon" to "ico", "image/vnd.microsoft.icon" to "ico", "image/svg+xml" to "svg")

    /** True when `host` is somewhere on the internet (not this device or the network it is in). */
    fun publicAddress(host: String): Boolean = try {
        val all = InetAddress.getAllByName(host)
        all.isNotEmpty() && all.none {
            it.isLoopbackAddress || it.isSiteLocalAddress || it.isLinkLocalAddress || it.isAnyLocalAddress || it.isMulticastAddress ||
                (it.address.size == 16 && (it.address[0].toInt() and 0xfe) == 0xfc)          // fc00::/7
        }
    } catch (e: Exception) {
        false
    }

    /** A typed or pasted address as a full one: 'example.com/x' -> 'https://example.com/x'. */
    fun normalize(text: String): String {
        var url = text.trim()
        if (url.isEmpty() || url.length > 2000 || url.any { it.isWhitespace() || it in "<>\"" }) throw IOException("That doesn't look like a web address")
        if (!Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://").containsMatchIn(url)) url = "https://$url"
        val u = try { URI(url) } catch (e: Exception) { throw IOException("That doesn't look like a web address") }
        val host = u.host ?: ""
        if (u.scheme?.lowercase() !in listOf("http", "https") || host.isEmpty() || ("." !in host && host != "localhost"))
            throw IOException("That doesn't look like a web address")
        return url.substringBefore('#')
    }

    /** Open a web address, following where it sends us, for as long as that is on the internet (unless `private`). */
    fun open(address: String, private: Boolean, accept: String = "*/*", timeoutMs: Int = 15_000): HttpURLConnection {
        var url = address
        repeat(6) {
            val u = URI(url)
            if (u.scheme?.lowercase() !in listOf("http", "https") || u.host.isNullOrEmpty()) throw IOException("not a web address")
            if (!private && !publicAddress(u.host)) throw IOException("that address is on this device or the network it is in")
            val conn = u.toURL().openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs * 2
            conn.setRequestProperty("User-Agent", AGENT)
            conn.setRequestProperty("Accept", accept)
            conn.setRequestProperty("Accept-Language", "en,*;q=0.5")
            val code = conn.responseCode
            if (code in 300..399 && conn.getHeaderField("Location") != null) {
                val next = u.resolve(conn.getHeaderField("Location")).toString()
                conn.disconnect()
                url = next
                return@repeat
            }
            if (code !in 200..299) {
                conn.disconnect()
                throw IOException("the site answered $code")
            }
            return conn
        }
        throw IOException("it redirects too often")
    }

    private fun InputStream.readUpTo(limit: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val buf = ByteArray(64 * 1024)
        while (out.size() <= limit) {
            val n = read(buf)
            if (n < 0) break
            out.write(buf, 0, n)
        }
        return out.toByteArray()
    }

    private fun type(conn: HttpURLConnection) = (conn.contentType ?: "").substringBefore(';').trim().lowercase()

    private fun attrs(tag: String): Map<String, String> {
        val out = HashMap<String, String>()
        for (m in Regex("""([a-zA-Z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""").findAll(tag)) {
            val key = m.groupValues[1].lowercase()
            if (key !in out) out[key] = unescape(m.groupValues[2] + m.groupValues[3] + m.groupValues[4])
        }
        return out
    }

    private fun unescape(s: String): String = s.replace("&quot;", "\"").replace("&#39;", "'").replace("&apos;", "'").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&nbsp;", " ")
        .replace(Regex("&#(\\d{1,7});")) { m -> m.groupValues[1].toIntOrNull()?.takeIf { Character.isValidCodePoint(it) }?.let { String(Character.toChars(it)) } ?: "" }
        .replace(Regex("&#[xX]([0-9a-fA-F]{1,6});")) { m -> m.groupValues[1].toIntOrNull(16)?.takeIf { Character.isValidCodePoint(it) }?.let { String(Character.toChars(it)) } ?: "" }
        .replace("&amp;", "&")

    private fun clean(text: String?, limit: Int): String {
        val t = (text ?: "").replace(Regex("\\s+"), " ").trim()
        return if (t.length > limit) t.take(limit - 1).trimEnd() + "…" else t
    }

    /** {title, description, site, image, icon, color} from a page's HTML (addresses made absolute). */
    fun parseMeta(html: String, url: String): JSONObject {
        val end = Regex("</head\\s*>|<body[\\s>]", RegexOption.IGNORE_CASE).find(html)?.range?.first ?: html.length
        val head = html.substring(0, end)
        val meta = HashMap<String, String>()
        val icons = ArrayList<Pair<Int, String>>()
        for (m in Regex("<(meta|link)\\b([^>]*)>", RegexOption.IGNORE_CASE).findAll(head)) {
            val a = attrs(m.groupValues[2])
            if (m.groupValues[1].equals("meta", true)) {
                val key = (a["property"] ?: a["name"] ?: a["itemprop"] ?: "").trim().lowercase()
                val content = a["content"] ?: ""
                if (key.isNotEmpty() && content.isNotEmpty() && key !in meta) meta[key] = content.trim()
            } else {
                val href = a["href"] ?: continue
                val rel = (a["rel"] ?: "").lowercase().split(Regex("\\s+"))
                if ("icon" in rel || rel.any { it.startsWith("apple-touch-icon") }) {
                    var size = Regex("^(\\d+)").find(a["sizes"] ?: "")?.groupValues?.get(1)?.toIntOrNull() ?: if (rel.any { it.startsWith("apple-touch-icon") }) 180 else 32
                    if (a["type"] == "image/svg+xml" || href.lowercase().substringBefore('?').endsWith(".svg")) size = 64
                    icons.add(size to href.trim())
                }
            }
        }
        val title = Regex("<title\\b[^>]*>(.*?)</title\\s*>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)).find(head)?.groupValues?.get(1)?.let(::unescape)
        val base = URI(url)
        fun absolute(href: String?): String = if (href.isNullOrEmpty()) "" else try { base.resolve(href.trim().replace(" ", "%20")).toString() } catch (e: Exception) { "" }
        val host = (base.host ?: "").removePrefix("www.")
        // the smallest icon that is still sharp at the size it is shown
        val icon = icons.filter { it.first >= 32 }.minByOrNull { it.first }?.second ?: icons.maxByOrNull { it.first }?.second
        val color = meta["theme-color"] ?: ""
        return JSONObject()
            .put("title", clean(meta["og:title"] ?: meta["twitter:title"] ?: title, 300))
            .put("description", clean(meta["og:description"] ?: meta["twitter:description"] ?: meta["description"], 600))
            .put("site", clean(meta["og:site_name"] ?: meta["application-name"], 80).ifEmpty { host })
            .put("image", absolute(meta["og:image:secure_url"] ?: meta["og:image"] ?: meta["twitter:image"] ?: meta["twitter:image:src"]))
            .put("icon", absolute(icon).ifEmpty { absolute("/favicon.ico") })
            .put("color", if (Regex("#[0-9a-fA-F]{3,8}").matches(color)) color else "")
    }

    /** What the page at `url` says about itself, with its banner and icon: {title, description, site, color, image, icon}, a picture as {data, ext}. */
    fun preview(address: String, private: Boolean = false): JSONObject {
        val url = normalize(address)
        val host = (URI(url).host ?: "").removePrefix("www.")
        val out = JSONObject().put("title", "").put("description", "").put("site", host).put("color", "")
        fun picture(bytes: ByteArray, ext: String) = JSONObject().put("data", Base64.getEncoder().encodeToString(bytes)).put("ext", ext)
        val links = HashMap<String, String>()
        try {
            val conn = open(url, private, "text/html,application/xhtml+xml,*/*;q=0.8")
            val final = conn.url.toString()
            val ctype = type(conn)
            val data = try { conn.inputStream.use { it.readUpTo(PAGE_MAX) } } finally { conn.disconnect() }
            links["icon"] = URI(final).resolve("/favicon.ico").toString()
            if (ctype in IMAGE_TYPES && data.size <= IMAGE_MAX) {               // the link is a picture itself
                out.put("image", picture(data, IMAGE_TYPES.getValue(ctype)))
                out.put("title", URI(final).path.substringAfterLast('/'))
            } else if ("html" in ctype || "xml" in ctype) {
                val named = Regex("charset=([\\w-]+)", RegexOption.IGNORE_CASE).find(conn.contentType ?: "")?.groupValues?.get(1)
                    ?: Regex("<meta[^>]+charset=[\"']?([\\w-]+)", RegexOption.IGNORE_CASE).find(String(data, 0, minOf(4096, data.size), Charsets.ISO_8859_1))?.groupValues?.get(1)
                val charset = try { Charset.forName(named ?: "utf-8") } catch (e: Exception) { Charsets.UTF_8 }
                val meta = parseMeta(String(data, charset), final)
                for (k in listOf("title", "description", "site", "color")) out.put(k, meta.getString(k))
                links["image"] = meta.getString("image")
                links["icon"] = meta.getString("icon")
            } else {
                out.put("title", URI(final).path.substringAfterLast('/'))
            }
        } catch (e: Exception) {
            return out
        }
        val threads = listOf("image" to IMAGE_MAX, "icon" to 512 * 1024).map { (key, limit) ->
            Thread {
                val link = links[key]
                if (link.isNullOrEmpty() || out.has(key)) return@Thread
                try {
                    val conn = open(link, private, "image/*", 8_000)
                    val ctype = type(conn)
                    val data = try { conn.inputStream.use { it.readUpTo(limit) } } finally { conn.disconnect() }
                    if (ctype in IMAGE_TYPES && data.isNotEmpty() && data.size <= limit) synchronized(out) { out.put(key, picture(data, IMAGE_TYPES.getValue(ctype))) }
                } catch (e: Exception) {
                    // a page without that picture
                }
            }.apply { isDaemon = true; start() }
        }
        threads.forEach { it.join(12_000) }
        return synchronized(out) { JSONObject(out.toString()) }
    }

    /** The name a download should get: what the site calls it, or the end of its address. */
    fun nameOf(conn: HttpURLConnection, address: String): String {
        val said = Regex("filename\\*?=(?:UTF-8'')?\"?([^\";]+)", RegexOption.IGNORE_CASE).find(conn.getHeaderField("Content-Disposition") ?: "")?.groupValues?.get(1)
        val raw = said ?: try { URI(address).rawPath ?: "" } catch (e: Exception) { "" }
        val name = try { URLDecoder.decode(raw.replace("+", "%2B"), "UTF-8") } catch (e: Exception) { raw }
        return name.replace('\\', '/').substringAfterLast('/').replace(Regex("[\\x00-\\x1f<>:\"|?*]"), "_").trim().trim('.').take(180).ifEmpty { "download" }
    }

    /** Download what is at a web address into `to`. Returns the name it should get. */
    fun download(address: String, to: File, private: Boolean, stop: () -> Boolean, heard: (name: String, bytes: Long, total: Long) -> Unit): String {
        val conn = open(address.trim(), private, timeoutMs = 30_000)
        try {
            val name = nameOf(conn, conn.url.toString())
            val total = conn.contentLengthLong.coerceAtLeast(0)
            var bytes = 0L
            conn.inputStream.use { input ->
                to.outputStream().use { out ->
                    val buf = ByteArray(256 * 1024)
                    while (true) {
                        if (stop()) throw Stopped()
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        bytes += n
                        heard(name, bytes, total)
                    }
                }
            }
            return name
        } finally {
            conn.disconnect()
        }
    }
}
