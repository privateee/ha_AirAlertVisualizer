#!/bin/bash
# ============================================================================
#  DroneVisualizer - macOS launcher
#
#  Double-click this file in Finder to set up (first run only) and start the
#  app.  A Terminal window opens and shows the live server log; leave it open
#  while you use DroneVisualizer.  Press Ctrl-C or close the window to stop.
#
#  Requires Python 3.11 or newer (see README-macOS.md).
# ============================================================================

set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PYBIN="$VENV/bin/python"
STAMP="$VENV/.requirements.md5"

pause_and_exit() {
    echo
    read -n1 -s -r -p "Press any key to close this window..."
    echo
    exit "${1:-1}"
}
trap 'echo; echo "!! Startup failed - see the messages above."; pause_and_exit 1' ERR

# --- locate a suitable Python (>= 3.11) -------------------------------------
find_python() {
    local c v major minor
    for c in python3.13 python3.12 python3.11 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        v=$("$c" -c 'import sys;print("%d %d"%sys.version_info[:2])' 2>/dev/null) || continue
        major=${v% *}; minor=${v#* }
        if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then echo "$c"; return 0; fi
    done
    return 1
}

# --- first run: create the virtualenv --------------------------------------
if [ ! -x "$PYBIN" ]; then
    echo "First run - setting up a private Python environment in ./$VENV ..."
    if ! PY=$(find_python); then
        cat <<'MSG'

  Python 3.11 or newer was not found.

  Install it with Homebrew:      brew install python@3.12
  ...or download the installer:  https://www.python.org/downloads/macos/

  Then double-click DroneVisualizer.command again.
MSG
        pause_and_exit 1
    fi
    echo "Using $("$PY" --version)"
    "$PY" -m venv "$VENV"
    "$PYBIN" -m pip install --quiet --upgrade pip
fi

# --- install / update dependencies when requirements.txt changes -----------
REQ_HASH=$(md5 -q requirements.txt 2>/dev/null || shasum requirements.txt | cut -d" " -f1)
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$REQ_HASH" ]; then
    echo "Installing Python packages (one-time, needs internet)..."
    "$PYBIN" -m pip install --upgrade -r requirements.txt
    echo "$REQ_HASH" > "$STAMP"
    echo "Setup complete."
fi

# --- figure out the port so we can open the browser ----------------------
PORT=8750
for f in config.yaml config.example.yaml; do
    [ -f "$f" ] || continue
    p=$(sed -n '/^server:/,/^[^[:space:]#]/p' "$f" \
        | sed -n 's/^[[:space:]]\{1,\}port:[[:space:]]*\([0-9]\{2,\}\).*/\1/p' | head -1)
    if [ -n "$p" ]; then PORT="$p"; break; fi
done
URL="http://localhost:${PORT}"

trap - ERR
echo
echo "  DroneVisualizer is starting on  ${URL}"
echo "  Keep this window open.  Press Ctrl-C to stop."
echo
( sleep 3; open "$URL" >/dev/null 2>&1 || true ) &

exec "$PYBIN" -m dronevis run
