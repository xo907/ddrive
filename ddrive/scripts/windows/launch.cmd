@echo off
rem Runs a DDrive command from the repository folder. Used by the hidden starter
rem (mount_hidden.vbs) and by the Explorer right-click menu.
setlocal
cd /d "%~dp0..\..\.."
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY exit /b 1
%PY% -m ddrive %*
