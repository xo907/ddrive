plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.xo.findx"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.xo.findx"
        minSdk = 31 // Android 12: the platform's fused location, the Nearby devices permission and today's background rules
        targetSdk = 35
        versionCode = 3
        versionName = "0.2.0"
    }

    // Signed with the key FindX has always been signed with (the build computer's Android debug key, unless
    // FINDX_KEYSTORE says otherwise): an update only installs over an app signed with the same key.
    signingConfigs {
        create("findx") {
            val ks = System.getenv("FINDX_KEYSTORE") ?: "${System.getProperty("user.home")}/.android/debug.keystore"
            storeFile = file(ks)
            storePassword = System.getenv("FINDX_KEYSTORE_PASSWORD") ?: "android"
            keyAlias = System.getenv("FINDX_KEY_ALIAS") ?: "androiddebugkey"
            keyPassword = System.getenv("FINDX_KEY_PASSWORD") ?: storePassword
        }
    }
    buildTypes {
        release {
            // Shrunk (R8): a few MB instead of 28, and faster to start.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("findx")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += "META-INF/*"
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-process:2.8.7")
    implementation("androidx.webkit:webkit:1.12.1")
    // Precision Finding between two phones with Ultra-Wideband (on-device radio ranging; nothing is sent anywhere).
    implementation("androidx.core.uwb:uwb:1.0.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
