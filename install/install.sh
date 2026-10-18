#!/bin/bash
set -euo pipefail

VIGIL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$VIGIL_DIR/data/logs"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

for name in tracker dashboard; do
  src="$VIGIL_DIR/install/com.vigil.$name.plist"
  dest="$PLIST_DIR/com.vigil.$name.plist"
  sed "s|__VIGIL_DIR__|$VIGIL_DIR|g" "$src" > "$dest"
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load "$dest"
  echo "Installed and started com.vigil.$name"
done

echo
echo "Vigil is running. Dashboard: http://localhost:8900"
echo "To uninstall: bash install/uninstall.sh"
