#!/usr/bin/env bash
# Hexcast launcher: checks Python, sets up the environment, keeps dependencies
# up to date (even after an update), then starts the server.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  ============================================"
echo "    Hexcast  -  setup and launcher"
echo "  ============================================"
echo

# 1. Find a usable Python (3.10+).
PYCMD=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
            PYCMD="$c"; break
        fi
    fi
done

if [ -z "$PYCMD" ]; then
    echo "  [X] Python 3.10 or newer was not found."
    echo "      Install it with your package manager, e.g.:"
    echo "        Ubuntu/Debian:  sudo apt install python3 python3-venv"
    echo "        macOS (brew):   brew install python"
    echo "      Then run ./start.sh again."
    exit 1
fi

echo "  [1/3] Python found: $("$PYCMD" --version 2>&1)"

# 2. Create the virtual environment the first time.
if [ ! -x ".venv/bin/python" ]; then
    echo "  [2/3] Setting up for the first time - this only happens once..."
    "$PYCMD" -m venv .venv
else
    echo "  [2/3] Environment ready."
fi
VENVPY=".venv/bin/python"

# 3. Install/update dependencies whenever requirements.txt changes.
NEEDINSTALL=0
if [ ! -f ".venv/requirements.lock" ] || ! cmp -s requirements.txt ".venv/requirements.lock"; then
    NEEDINSTALL=1
fi
if [ "$NEEDINSTALL" = "1" ]; then
    echo "  [3/3] Installing components - this can take a couple of minutes the first time..."
    "$VENVPY" -m pip install --upgrade pip
    "$VENVPY" -m pip install -r requirements.txt
    cp -f requirements.txt ".venv/requirements.lock"
else
    echo "  [3/3] Components up to date."
fi

# 4. Safety net: confirm the key packages import; repair once if not.
if ! "$VENVPY" -c "import fastapi, uvicorn, httpx, socketio, websockets" >/dev/null 2>&1; then
    echo "  Some components are missing - repairing..."
    "$VENVPY" -m pip install -r requirements.txt
    cp -f requirements.txt ".venv/requirements.lock"
fi

echo
echo "  Starting Hexcast - leave this running while you stream."
echo "  Control panel:  http://localhost:4747/"
echo
exec "$VENVPY" hexcast.py
