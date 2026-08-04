#!/usr/bin/env bash
# KUZA-V2 GUI launcher
# Usage:  bash gui/start.sh [port]
#
# Starts the browser GUI in the background, then drops you into the
# interactive kuza2 session in this terminal — both live at once.
#
# Requires:  pip install aiohttp   (already in requirements.txt)

set -e
cd "$(dirname "$0")/.."

PORT="${1:-8888}"
export KUZA_GUI_PORT="$PORT"
export PYTHONUNBUFFERED=1

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║    KUZA-V2  ·  GUI + CLI LAUNCHER   ║"
echo "  ╠══════════════════════════════════════╣"
echo "  ║  Browser → http://localhost:${PORT}      ║"
echo "  ║  Terminal → interactive kuza2 below ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── Start GUI server in background ──────────────────────────────────────────
python gui/server.py &
GUI_PID=$!
echo "  GUI server started (PID $GUI_PID)  →  http://localhost:${PORT}"
echo "  Open that URL in your browser, then use the terminal below as usual."
echo ""
echo "  (Ctrl+C stops everything)"
echo ""

# Kill the GUI server when this script exits (Ctrl+C or natural exit)
trap 'echo ""; echo "  Stopping GUI server..."; kill "$GUI_PID" 2>/dev/null; exit 0' INT TERM EXIT

# ── Drop into interactive kuza2 in the foreground ──────────────────────────
python main.py
