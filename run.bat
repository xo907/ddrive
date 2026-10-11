@echo off
rem DDrive - Made by XO.ST - https://github.com/xo907/ddrive
rem   run.bat              opens the menu (or just double-click this file)
rem   run.bat <command>    runs a command directly, e.g.  run.bat status
setlocal
if /i "%~1"=="--menu-from-temp" goto menu

cd /d "%~dp0"
call :find_python || exit /b 1
if not "%~1"=="" (
    %PY% -m ddrive %*
    exit /b
)
rem The menu can update DDrive, which replaces this file, so run the menu from a copy.
copy /y "%~f0" "%TEMP%\DDrive-run.bat" >nul
"%TEMP%\DDrive-run.bat" --menu-from-temp "%~dp0."

:menu
cd /d "%~f2"
title DDrive
call :find_python || exit /b 1
:again
%PY% -m ddrive menu
if errorlevel 76 goto failed
if errorlevel 75 goto again
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo   Something went wrong (see above).
pause
exit /b 1

:find_python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if defined PY exit /b 0
echo.
echo   Python 3 is required. Install it with:
echo       winget install -e --id Python.Python.3.12 --source winget
echo   then open a new window and run run.bat again.
pause
exit /b 1
