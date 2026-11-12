#!/bin/zsh
# Pull the live rig files into this repository so they can be committed and pushed.
# The rig runs from its own folders; this repository is the copy that goes to GitHub.
#   ./backup.sh            then:  git add -A && git commit -m "..." && git push
set -e
HERE=${0:A:h}
AC="$HOME/Library/Application Support/CrossOver/Bottles/${BOTTLE:-Steam(try3232)}/drive_c/Program Files (x86)/Steam/steamapps/common/assettocorsa"

# pedal haptics, control board, lap recorder and analysis
rsync -a --delete --exclude .venv --exclude '*.egg-info' --exclude __pycache__ --exclude .pytest_cache --exclude LICENSE \
  "$HOME/dev/moza-phf/" "$HERE/moza-phf/"

# wheel dashboard bridge, launcher, settings (logs and live status files stay behind)
mkdir -p "$HERE/rig"
rsync -a "$HOME/dev/moza-rig/moza-dash.py" "$HOME/dev/moza-rig/start-rig.sh" "$HOME/dev/moza-rig/config.json" \
  "$HOME/dev/moza-rig/icon.png" "$HOME/dev/moza-rig/icon.svg" "$HOME/dev/moza-rig/icon-credit.txt" "$HERE/rig/"
[ -f "$HOME/Desktop/MOZA Rig.command" ] && cp "$HOME/Desktop/MOZA Rig.command" "$HERE/rig/"

# every recorded lap
rsync -a --delete --exclude '*.tmp' "$HOME/dev/moza-rig/laps/" "$HERE/laps/"

# the in-game Boost and Power app
mkdir -p "$HERE/assetto-corsa/apps/rig_power"
rsync -a --delete --exclude '*.lua.v*' "$AC/apps/lua/rig_power/" "$HERE/assetto-corsa/apps/rig_power/"

# Macan S build scripts (the unpacked Kunos data they read is not ours to publish)
if [ -d "$HOME/dev/macan-s" ]; then
  mkdir -p "$HERE/macan-s"
  rsync -a "$HOME/dev/macan-s/build.py" "$HOME/dev/macan-s/acd_unpack.py" "$HERE/macan-s/"
fi

# lap log from Content Manager's saved sessions (times only: the session files themselves stay behind)
python3 "$HERE/tools/session_report.py" || true

cd "$HERE" && git status --short
