plugins {
    id("com.android.application")
    kotlin("android")
}

// The app has the version of DDrive itself (ddrive/__init__.py).
val ddriveVersion: String = Regex("""__version__\s*=\s*"([^"]+)"""")
    .find(rootDir.parentFile.resolve("ddrive/__init__.py").readText())!!.groupValues[1]
val (major, minor, patch) = ddriveVersion.split(".").map { it.toInt() }

// Signing: the key is not in the repository. Without these the release build is left unsigned.
val keystore: String? = providers.environmentVariable("DDRIVE_KEYSTORE").orNull

android {
    namespace = "st.xo.ddrive.lite"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "st.xo.ddrive.lite"
        minSdk = 26
        targetSdk = 35
        versionCode = major * 10000 + minor * 100 + patch
        versionName = ddriveVersion
    }

    signingConfigs {
        if (keystore != null) create("release") {
            storeFile = file(keystore)
            storePassword = providers.environmentVariable("DDRIVE_KEYSTORE_PASSWORD").get()
            keyAlias = providers.environmentVariable("DDRIVE_KEY_ALIAS").getOrElse("ddrive-lite")
            keyPassword = providers.environmentVariable("DDRIVE_KEY_PASSWORD").orElse(providers.environmentVariable("DDRIVE_KEYSTORE_PASSWORD")).get()
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}

kotlin {
    jvmToolchain(17)
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_11) }
}

dependencies {
    implementation(project(":core"))
}
