// DDrive Lite for Android. `core` is the folder syncing itself (plain Kotlin, tested on a computer
// against a real DDrive host); `app` is the Android program around it.
plugins {
    id("com.android.application") version "8.7.3" apply false
    kotlin("android") version "2.0.21" apply false
    kotlin("jvm") version "2.0.21" apply false
}

// Where the build's output goes when it should stay out of the source folder (one that is synced, say).
val out: String? = providers.environmentVariable("DDRIVE_ANDROID_OUT").orNull
if (out != null) allprojects { layout.buildDirectory.set(file("$out/$name")) }
