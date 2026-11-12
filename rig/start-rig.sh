#!/bin/zsh
# Starts the wheel dashboard bridge, the pedal haptics and the control board for
# Assetto Corsa, and opens the board on its all-laps map. The board records every
# lap you complete while this is running. Run it any time after Torqer; everything
# waits for a game session and reconnects by itself. Close this window or press
# Ctrl+C to stop all three.
#   --check   only show which MOZA devices were found; start and stop nothing

PY="$HOME/dev/moza-phf/.venv/bin/python"
DASH="$HOME/dev/moza-rig/moza-dash.py"

# Serial port of the MOZA device (USB vendor 346e) with this product id, or nothing.
# The two must be told apart: the dashboard script alone grabs whichever comes first.
port_for() {
  "$PY" -c 'import sys
from serial.tools import list_ports
print(next((p.device for p in list_ports.comports()
            if p.vid == 0x346E and p.pid == int(sys.argv[1], 16)), ""))' "$1"
}

BASE=$(port_for 0004)   # R5 wheelbase: wheel screen + rev lights
BOX=$(port_for 002f)    # pedal haptic Control Box
echo "wheelbase:  ${BASE:-not found}"
echo "haptic box: ${BOX:-not found}"
[[ "$1" == "--check" ]] && exit 0
if [[ -z "$BASE" && -z "$BOX" ]]; then
  echo "No MOZA devices found: starting the control board only (settings and lap analytics)."
  echo "Plug them in, power them on, and run this again for the wheel screen and haptics."
fi

# Only one copy of each may hold a port. The patterns match the full commands this
# script starts, so they don't take down an editor or shell that merely names a file.
pkill -f "moza_phf.cli run ac --serial" 2>/dev/null
pkill -f "moza_phf.cli board --port" 2>/dev/null
if pkill -f "moza-dash.py --port" 2>/dev/null; then
  # the wheel screen wedges if a new session opens right after the old one drops
  echo "stopping the running dashboard bridge; waiting 16 s for the wheel to settle..."
  sleep 16
else
  sleep 1
fi

LOGS="$HOME/dev/moza-rig/logs"; mkdir -p "$LOGS"
pids=()
if [[ -n "$BASE" ]]; then
  "$PY" -u "$DASH" --port "$BASE" > >(tee "$LOGS/dash.log") 2>&1 &
  pids+=($!)
fi
if [[ -n "$BOX" ]]; then
  "$PY" -u -m moza_phf.cli run ac --serial "$BOX" --pedals brake,throttle > >(tee "$LOGS/haptics.log") 2>&1 &
  pids+=($!)
fi
# Control board: haptics settings, per-car rev lights, lap analytics. It also runs the lap recorder.
BOARD_PORT=8765
"$PY" -u -m moza_phf.cli board --port $BOARD_PORT > >(tee "$LOGS/board.log") 2>&1 &
pids+=($!)
trap 'kill $pids 2>/dev/null' EXIT INT TERM HUP
# Wait for the board to answer (it takes a moment to start), then say whether laps are being logged.
REC=$("$PY" - $BOARD_PORT <<'PYEOF'
import json, sys, time, urllib.request
for _ in range(40):
    try:
        state = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/state", timeout=1))
        laps = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/rec/laps", timeout=2))
        if state.get("recorder") is not None:
            print(f"on, {len(laps.get('laps', []))} laps saved so far"); break
    except Exception:
        pass
    time.sleep(0.25)
else:
    print("NOT RUNNING")
PYEOF
)
echo "lap logging:   $REC  (every lap you complete is saved to ~/dev/moza-rig/laps)"
echo "control board: http://127.0.0.1:$BOARD_PORT  (opening the all-laps map in your browser)"
open "http://127.0.0.1:$BOARD_PORT/#overlay"
wait
