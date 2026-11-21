#!/bin/bash
# Installs (or removes) two macOS background jobs for Skyline Snow's learning loop:
#   collect   every 3 hours   resort sensors + forecast snapshots
#   calibrate daily at 05:30  re-learns calibration.json
# Usage: bash learn/schedule.sh install | uninstall | status
set -euo pipefail
LEARN="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$LEARN")"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/data/logs"
PY="$(command -v python3)"

plist() { # name, schedule-xml
  cat > "$AGENTS/com.snowline.$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.snowline.$1</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$LEARN/$1.py</string></array>
  <key>WorkingDirectory</key><string>$LEARN</string>
  $2
  <key>StandardOutPath</key><string>$LOGS/$1.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$1.log</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
EOF
}

case "${1:-status}" in
  install)
    mkdir -p "$AGENTS" "$LOGS"
    plist collect   "<key>StartInterval</key><integer>10800</integer>"
    plist calibrate "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>30</integer></dict>"
    for j in collect calibrate; do
      launchctl unload "$AGENTS/com.snowline.$j.plist" 2>/dev/null || true
      launchctl load "$AGENTS/com.snowline.$j.plist"
    done
    echo "Installed. Logs: $LOGS" ;;
  uninstall)
    for j in collect calibrate; do
      launchctl unload "$AGENTS/com.snowline.$j.plist" 2>/dev/null || true
      rm -f "$AGENTS/com.snowline.$j.plist"
    done
    echo "Removed." ;;
  status)
    launchctl list | grep com.snowline || echo "Not installed." ;;
esac
