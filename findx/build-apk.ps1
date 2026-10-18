# Builds FindX (shrunk release, signed with the build computer's Android debug key, the key FindX has
# always been signed with) and runs the unit tests. The app goes to release/FindX.apk with its version in
# release/VERSION, which DDrive reads to tell phones that a newer FindX is out.
# Uses the toolchain in E:\SaveX-toolchain and keeps build output out of the repository.
# Usage:  powershell -ExecutionPolicy Bypass -File build-apk.ps1
$ErrorActionPreference = 'Stop'
$T = 'E:\SaveX-toolchain'
$env:JAVA_HOME = "$T\jdk17"
$env:ANDROID_HOME = "$T\sdk"
$env:GRADLE_USER_HOME = "$T\gradle-home"
$env:FINDX_BUILD_DIR = "$T\build-findx"

Push-Location $PSScriptRoot
try {
    & "$T\gradle-8.11.1\bin\gradle.bat" testDebugUnitTest assembleRelease --project-cache-dir "$T\project-cache-findx" --console=plain
    if ($LASTEXITCODE -ne 0) { throw "Build failed" }
    $apk = Get-ChildItem "$T\build-findx\app\outputs\apk\release\*.apk" | Select-Object -First 1
    New-Item -ItemType Directory -Force "$PSScriptRoot\release" | Out-Null
    Copy-Item $apk.FullName "$PSScriptRoot\release\FindX.apk" -Force
    $version = (Select-String -Path "$PSScriptRoot\app\build.gradle.kts" -Pattern 'versionName = "([^"]+)"').Matches[0].Groups[1].Value
    [IO.File]::WriteAllText("$PSScriptRoot\release\VERSION", "$version`n")
    "Built: $PSScriptRoot\release\FindX.apk $version ($([math]::Round((Get-Item "$PSScriptRoot\release\FindX.apk").Length / 1MB, 1)) MB)"
} finally {
    Pop-Location
}
