package com.xo.findx

/** Versions like 0.2.0, compared as numbers. */
object Versions {
    fun parse(v: String): List<Int> = v.trim().split(".").map { it.toIntOrNull() ?: return emptyList() }

    /** True when [a] is newer than [b]. */
    fun newer(a: String, b: String): Boolean {
        val x = parse(a)
        val y = parse(b)
        if (x.isEmpty() || y.isEmpty()) return false
        for (i in 0 until maxOf(x.size, y.size)) {
            val p = x.getOrElse(i) { 0 }
            val q = y.getOrElse(i) { 0 }
            if (p != q) return p > q
        }
        return false
    }
}
