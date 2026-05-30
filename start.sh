#!/usr/bin/env bash
# Hexcast launcher: creates venv on first run, installs deps, starts server.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "First run — setting up virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python hexcast.py
