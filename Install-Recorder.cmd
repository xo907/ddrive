@echo off
rem DDrive Call & Screenshare Recorder - Vencord Injector
rem Live-streams Discord voice calls and screenshares to DDrive in memory.
title DDrive Recorder Installer for Vencord
setlocal

set "SCRIPT=%~dp0install-recorder.ps1"
if not exist "%SCRIPT%" (
    echo [ERROR] install-recorder.ps1 not found in %~dp0
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*

if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed.
    pause
    exit /b %errorlevel%
)
