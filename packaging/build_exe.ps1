# Builds dist\DDrive.exe, a single program that needs neither Python nor git.
# Usage (PowerShell, from the repository folder):  packaging\build_exe.ps1
# WinFsp still has to be installed separately (https://winfsp.dev/rel/): it is a driver.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m pip install --upgrade pyinstaller pillow
python -m PyInstaller --noconfirm --clean --onefile --console `
    --name DDrive `
    --icon docs\images\logo-128.png `
    --add-data "ddrive\web;ddrive\web" `
    --add-data "ddrive\wordlist.txt;ddrive" `
    --add-data "CHANGELOG.md;." `
    --collect-submodules ddrive `
    packaging\entry.py
Write-Host "Built dist\DDrive.exe"

# The lite-only program: uses a drive that runs on another computer (no dashboard, no bot, no key).
python -m PyInstaller --noconfirm --clean --onefile --console `
    --name DDriveLite `
    --paths . `
    --icon docs\images\logo-128.png `
    packaging\lite_entry.py
Write-Host "Built dist\DDriveLite.exe"
