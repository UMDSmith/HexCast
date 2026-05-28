@echo off
REM Hexcast launcher: creates venv on first run, installs deps, starts server.
cd /d "%~dp0"

if not exist .venv (
    echo First run -- setting up virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Python not found. Install Python 3.10+ from https://www.python.org/
        echo Make sure to check "Add python.exe to PATH" during install.
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

.venv\Scripts\python.exe soundboard.py
pause
