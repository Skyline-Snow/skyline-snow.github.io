#!/bin/bash
# Keeps this Mac's copy of Skyline Snow up to date with GitHub, where the cloud jobs save new data.
# Pulls once at login and every day at 07:00 (or on wake, if the Mac was asleep then).
# Usage: bash snowline/learn/autosync.sh install | uninstall | status | now
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LOGS="$REPO/snowline/data/logs"
PLIST="$HOME/Library/LaunchAgents/com.skylinesnow.sync.plist"
GIT="$(command -v git)"

case "${1:-status}" in
  install)
    mkdir -p "$LOGS" "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.skylinesnow.sync</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-c</string>
    <string>echo "--- \$(date '+%Y-%m-%d %H:%M')"; "$GIT" -C "$REPO" pull --ff-only --quiet &amp;&amp; "$GIT" -C "$REPO" log -1 --format='now at: %h %s'</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGS/sync.log</string>
  <key>StandardErrorPath</key><string>$LOGS/sync.log</string>
</dict></plist>
EOF
    launchctl bootout "gui/$(id -u)/com.skylinesnow.sync" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Installed. It pulls at login and daily at 07:00. Log: $LOGS/sync.log" ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/com.skylinesnow.sync" 2>/dev/null || true
    rm -f "$PLIST"; echo "Removed." ;;
  now)
    launchctl kickstart -k "gui/$(id -u)/com.skylinesnow.sync" && echo "Sync started; see $LOGS/sync.log" ;;
  status)
    launchctl print "gui/$(id -u)/com.skylinesnow.sync" 2>/dev/null | grep -E "state|last exit" || echo "Not installed."
    tail -n 4 "$LOGS/sync.log" 2>/dev/null || true ;;
esac
