#!/bin/bash
# Double-click (or run ./setup.command) once to install everything.
cd "$(dirname "$0")"
python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip -q && pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo ""
echo "✔ Installed. Now open the .env file and paste your Alpaca PAPER keys."
