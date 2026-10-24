<#
  DDrive Call & Screenshare Recorder — Vencord Injector (Windows)
  Live-streams Discord call audio & screenshare video straight to DDrive in memory.
  Zero disk usage, no background pauses when minimized.

  Usage:
    powershell -ExecutionPolicy Bypass -File install-recorder.ps1 [-Branch auto|stable|ptb|canary] [-Uninstall] [-Restart] [-Yes]
#>
param(
  [ValidateSet('auto', 'stable', 'ptb', 'canary')]
  [string]$Branch = 'auto',
  [switch]$Uninstall,
  [switch]$Restart,
  [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PluginFolder = 'ddriveRecorder'
$PluginName = 'DDriveRecorder'
$PluginFiles = @('index.tsx', 'native.ts', 'style.css')
$MinNodeMajor = 18

# Candidate Vencord locations
$CandidateVencordDirs = @(
  (Join-Path $env:LOCALAPPDATA 'E2EE-Discord\Vencord'),
  (Join-Path $env:LOCALAPPDATA 'DDrive\Vencord'),
  (Join-Path $env:LOCALAPPDATA 'Vencord')
)

function Write-Banner {
  Write-Host ''
  Write-Host '  =======================================================' -ForegroundColor Cyan
  Write-Host '   DDrive Live Call & Screenshare Recorder for Vencord   ' -ForegroundColor White
  Write-Host '   Zero Disk Usage · Live Stream to DDrive · No Pauses   ' -ForegroundColor DarkGray
  Write-Host '  =======================================================' -ForegroundColor Cyan
  Write-Host ''
}

function Write-Step([string]$message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Info([string]$message) { Write-Host "    $message" -ForegroundColor Gray }
function Write-Done([string]$message) { Write-Host "    $message" -ForegroundColor Green }

function Confirm-Step([string]$question) {
  if ($Yes) { return $true }
  $answer = Read-Host "    $question [Y/n]"
  return ($answer -eq '' -or $answer -match '^[Yy]')
}

function Test-Command([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Invoke-Native([string]$what, [scriptblock]$command) {
  & $command
  if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Get-NodeMajor {
  if (-not (Test-Command 'node')) { return 0 }
  $version = (& node --version) -replace '^v', ''
  return [int]($version.Split('.')[0])
}

function Check-Prerequisites {
  Write-Step 'Checking requirements'

  if (-not (Test-Command 'git')) { throw 'Git is required. Please install Git: https://git-scm.com/' }
  Write-Done "git $((& git --version) -replace '^git version ', '')"

  if ((Get-NodeMajor) -lt $MinNodeMajor) {
    throw "Node.js $MinNodeMajor or newer is required: https://nodejs.org/"
  }
  Write-Done "node $(& node --version)"

  if (-not (Test-Command 'pnpm')) {
    Write-Info 'pnpm is missing. Installing pnpm via npm...'
    Invoke-Native 'Installing pnpm' { npm install --global pnpm }
  }
  Write-Done "pnpm $(& pnpm --version)"
}

function Get-DiscordProcesses {
  return @(Get-Process -Name 'Discord', 'DiscordPTB', 'DiscordCanary', 'DiscordDevelopment' -ErrorAction SilentlyContinue)
}

function Stop-Discord {
  $processes = Get-DiscordProcesses
  if ($processes.Count -eq 0) { return @() }
  $names = @($processes | Select-Object -ExpandProperty Name -Unique)
  if (-not (Confirm-Step "Discord is currently running ($($names -join ', ')). Close it to apply changes?")) {
    return @()
  }
  $restart = @()
  foreach ($name in $names) {
    $exe = ($processes | Where-Object { $_.Name -eq $name -and $_.Path } | Select-Object -First 1).Path
    if ($exe) { $restart += $exe }
  }
  $processes | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  return $restart
}

function Start-Discord([string[]]$executables) {
  foreach ($exe in $executables) {
    $installDir = Split-Path (Split-Path $exe -Parent) -Parent
    $updater = Join-Path $installDir 'Update.exe'
    if (Test-Path $updater) {
      Start-Process $updater -ArgumentList '--processStart', (Split-Path $exe -Leaf)
    } else {
      Start-Process $exe
    }
  }
}

function Find-Or-Clone-Vencord {
  foreach ($dir in $CandidateVencordDirs) {
    if (Test-Path (Join-Path $dir 'package.json')) {
      return $dir
    }
  }

  $target = Join-Path $env:LOCALAPPDATA 'DDrive\Vencord'
  Write-Step "Cloning Vencord into $target..."
  New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
  Invoke-Native 'Cloning Vencord' { git clone --depth 1 https://github.com/Vendicated/Vencord.git $target }
  return $target
}

function Copy-PluginFiles([string]$vencordDir) {
  Write-Step 'Copying DDriveRecorder plugin to Vencord'
  $sourceDir = Join-Path $PSScriptRoot "vencord\$PluginFolder"
  if (-not (Test-Path $sourceDir)) {
    throw "Plugin source directory not found at $sourceDir"
  }

  $targetDir = Join-Path $vencordDir "src\userplugins\$PluginFolder"
  New-Item -ItemType Directory -Force $targetDir | Out-Null

  foreach ($file in $PluginFiles) {
    $src = Join-Path $sourceDir $file
    $dst = Join-Path $targetDir $file
    if (-not (Test-Path $src)) { throw "Missing plugin file: $src" }
    Copy-Item $src $dst -Force
  }
  Write-Done "Copied plugin to $targetDir"
}

function Build-Vencord([string]$vencordDir) {
  Write-Step 'Building Vencord with DDriveRecorder...'
  Push-Location $vencordDir
  try {
    $env:CI = 'true'
    Invoke-Native 'Installing dependencies' { pnpm install --frozen-lockfile }
    Invoke-Native 'Building Vencord' { pnpm build }
  } finally {
    Remove-Item Env:\CI -ErrorAction SilentlyContinue
    Pop-Location
  }
  Write-Done 'Vencord built successfully'
}

function Inject-Vencord([string]$vencordDir) {
  Write-Step "Injecting Vencord into Discord (branch: $Branch)..."
  Push-Location $vencordDir
  try {
    Invoke-Native 'Vencord injection' { node scripts/runInstaller.mjs '--' '--install' '--branch' $Branch }
  } finally {
    Pop-Location
  }
  Write-Done 'Vencord injected into Discord'
}

function Enable-Plugin([string]$vencordDir) {
  Write-Step 'Enabling DDriveRecorder in settings'
  $settingsDir = Join-Path $vencordDir 'settings'
  $settingsFile = Join-Path $settingsDir 'settings.json'
  New-Item -ItemType Directory -Force $settingsDir | Out-Null

  $settings = if (Test-Path $settingsFile) {
    Get-Content $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
  } else {
    [pscustomobject]@{}
  }

  if (-not $settings.PSObject.Properties['plugins']) {
    $settings | Add-Member -NotePropertyName plugins -NotePropertyValue ([pscustomobject]@{} )
  }
  if ($settings.plugins.PSObject.Properties[$PluginName]) {
    $plugin = $settings.plugins.$PluginName
    if ($plugin.PSObject.Properties['enabled']) { $plugin.enabled = $true }
    else { $plugin | Add-Member -NotePropertyName enabled -NotePropertyValue $true }
  } else {
    $settings.plugins | Add-Member -NotePropertyName $PluginName -NotePropertyValue ([pscustomobject]@{ enabled = $true })
  }

  $json = $settings | ConvertTo-Json -Depth 64
  [System.IO.File]::WriteAllText($settingsFile, $json, (New-Object System.Text.UTF8Encoding $false))
  Write-Done "Enabled $PluginName in Vencord settings"
}

function Remove-Plugin([string]$vencordDir) {
  Write-Step 'Removing DDriveRecorder plugin...'
  $targetDir = Join-Path $vencordDir "src\userplugins\$PluginFolder"
  if (Test-Path $targetDir) {
    Remove-Item $targetDir -Recurse -Force
    Write-Done "Removed $targetDir"
  }
  Build-Vencord $vencordDir
  Inject-Vencord $vencordDir
  Write-Done 'Vencord updated without DDriveRecorder'
}

# ───────────────────────────────────────────────────────────────────────────

try {
  Write-Banner

  Check-Prerequisites
  $vencordDir = Find-Or-Clone-Vencord
  Write-Info "Using Vencord at: $vencordDir"

  if ($Uninstall) {
    $restartList = Stop-Discord
    Remove-Plugin $vencordDir
    if ($restartList.Count -gt 0) { Start-Discord $restartList }
    Write-Done 'DDriveRecorder uninstalled successfully.'
    exit 0
  }

  $restartList = Stop-Discord
  Copy-PluginFiles $vencordDir
  Build-Vencord $vencordDir
  Inject-Vencord $vencordDir
  Enable-Plugin $vencordDir

  if ($restartList.Count -gt 0) {
    Start-Discord $restartList
    Write-Done 'Discord was restarted with DDriveRecorder active!'
  } elseif ($Restart) {
    $procs = Get-DiscordProcesses
    if ($procs.Count -gt 0) {
      $restarts = Stop-Discord
      Start-Discord $restarts
    }
  }

  Write-Host ''
  Write-Host '  [+] DDrive Call & Screenshare Recorder is installed!' -ForegroundColor Green
  Write-Host '  1. Open Discord Settings -> Plugins -> DDrive Call Recorder' -ForegroundColor White
  Write-Host '  2. Enter your DDrive Server URL and Recorder Key' -ForegroundColor White
  Write-Host '  3. In any voice call, click the Record button in the Voice bar' -ForegroundColor White
  Write-Host '  4. If anyone shares their screen, it records the screenshare automatically' -ForegroundColor White
  Write-Host '  5. Minimizing the window will NOT pause recording (anti-throttle active)' -ForegroundColor White
  Write-Host ''
} catch {
  Write-Host ''
  Write-Host "  Installation failed: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
