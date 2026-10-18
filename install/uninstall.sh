#!/bin/bash
set -euo pipefail

PLIST_DIR="$HOME/Library/LaunchAgents"

for name in tracker dashboard; do
  dest="$PLIST_DIR/com.vigil.$name.plist"
  if [ -f "$dest" ]; then
    launchctl unload "$dest" 2>/dev/null || true
    rm "$dest"
    echo "Removed com.vigil.$name"
  fi
done

echo "Vigil background services removed. Your data in data/ is untouched."
