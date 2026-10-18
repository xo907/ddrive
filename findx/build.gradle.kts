plugins {
    id("com.android.application") version "8.9.1" apply false
    id("org.jetbrains.kotlin.android") version "2.1.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.20" apply false
}

// When FINDX_BUILD_DIR is set, build output
// goes there instead (build-apk.ps1 sets it).
System.getenv("FINDX_BUILD_DIR")?.let { dir ->
    allprojects { layout.buildDirectory.set(file("$dir/${project.name}")) }
}
