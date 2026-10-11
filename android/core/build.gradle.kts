plugins {
    kotlin("jvm")
}

kotlin {
    jvmToolchain(17)
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_11) }
}

java {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
}

dependencies {
    // Android brings org.json itself; on a computer (the tests) it comes from here.
    compileOnly("org.json:json:20231013")
    testImplementation("org.json:json:20231013")
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
    // The tests start a real DDrive host (python, from the repository this folder is in).
    systemProperty("ddrive.repo", rootDir.parentFile.absolutePath)
    testLogging {
        events("passed", "failed", "skipped")
        showStandardStreams = true
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
