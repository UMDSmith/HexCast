@echo off
setlocal enableextensions
cd /d "%~dp0"
title Hexcast

echo(
echo  ============================================
echo    Hexcast  -  setup and launcher
echo  ============================================
echo(

REM ---------------------------------------------------------------------------
REM 1. Find a usable Python. Prefer the "py" launcher, then "python" on PATH.
REM ---------------------------------------------------------------------------
set "PYCMD="
for %%P in ("py -3" "python") do if not defined PYCMD ( %%~P --version >nul 2>&1 && set "PYCMD=%%~P" )

if not defined PYCMD (
    echo  [X] Python is not installed on this computer.
    echo(
    echo      1. Go to  https://www.python.org/downloads/
    echo      2. Download Python 3.10 or newer and run the installer.
    echo      3. IMPORTANT: tick the box  "Add python.exe to PATH"  on the first screen.
    echo      4. Close this window, then double-click start.bat again.
    echo(
    pause
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM 2. Make sure it is new enough (3.10+).
REM ---------------------------------------------------------------------------
%PYCMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [X] Your Python is too old - Hexcast needs version 3.10 or newer.
    echo      Installed version:
    %PYCMD% --version
    echo      Get the latest from  https://www.python.org/downloads/  and tick "Add python.exe to PATH".
    echo(
    pause
    exit /b 1
)

echo  [1/3] Python found:
%PYCMD% --version

REM ---------------------------------------------------------------------------
REM 3. Create the virtual environment the first time.
REM ---------------------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [2/3] Setting up for the first time - this only happens once...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo  [X] Could not create the virtual environment. See the message above.
        pause
        exit /b 1
    )
) else (
    echo  [2/3] Environment ready.
)

set "VENVPY=.venv\Scripts\python.exe"

REM ---------------------------------------------------------------------------
REM 4. Install or update dependencies.
REM    We remember what we last installed in requirements.lock and re-install
REM    whenever requirements.txt changes - so an update just works.
REM ---------------------------------------------------------------------------
set "NEEDINSTALL="
if not exist ".venv\requirements.lock" set "NEEDINSTALL=1"
if exist ".venv\requirements.lock" fc /b requirements.txt ".venv\requirements.lock" >nul 2>&1 || set "NEEDINSTALL=1"

if defined NEEDINSTALL (
    echo  [3/3] Installing components - this can take a couple of minutes the first time...
    "%VENVPY%" -m pip install --upgrade pip
    "%VENVPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo(
        echo  [X] Something went wrong while installing. Check your internet connection
        echo      and run start.bat again. The messages above say what failed.
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\requirements.lock" >nul
) else (
    echo  [3/3] Components up to date.
)

REM ---------------------------------------------------------------------------
REM 5. Safety net: confirm the key packages actually import. If a previous
REM    install was incomplete, heal it once and re-check.
REM ---------------------------------------------------------------------------
"%VENVPY%" -c "import fastapi, uvicorn, httpx, websockets, watchdog" >nul 2>&1
if errorlevel 1 (
    echo  Some components are missing - repairing...
    "%VENVPY%" -m pip install -r requirements.txt
    copy /y requirements.txt ".venv\requirements.lock" >nul
    "%VENVPY%" -c "import fastapi, uvicorn, httpx, websockets, watchdog" >nul 2>&1
    if errorlevel 1 (
        echo  [X] Components still not working. Please send the messages above for help.
        pause
        exit /b 1
    )
)

REM ---------------------------------------------------------------------------
REM 6. The first time (and again if this folder is moved): put a Hexcast icon
REM    on the desktop, so it starts with a double-click from there. It is made
REM    once - delete the icon and it stays gone.
REM ---------------------------------------------------------------------------
set "HEXCAST_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = $env:HEXCAST_DIR; $mark = Join-Path $d '.venv\shortcut.done'; if ((Test-Path -LiteralPath $mark) -and ((Get-Content -LiteralPath $mark -Raw).Trim() -eq $d)) { exit 1 }; $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Hexcast.lnk')); $s.TargetPath = Join-Path $d 'start.bat'; $s.WorkingDirectory = $d; $s.IconLocation = (Join-Path $d 'static\hexcast.ico') + ',0'; $s.Description = 'Start Hexcast'; $s.Save(); Set-Content -LiteralPath $mark -Value $d" >nul 2>&1
if not errorlevel 1 echo  Added a Hexcast icon to your desktop - next time, just double-click it.

REM ---------------------------------------------------------------------------
REM 7. Launch.
REM ---------------------------------------------------------------------------
echo(
echo  Starting Hexcast - leave this window open while you stream.
echo  Control panel:  http://localhost:4747/
echo  Add Twitch, Music, Games and more from the + tab of the control panel.
echo(
"%VENVPY%" hexcast.py

echo(
echo  Hexcast has stopped.
pause
