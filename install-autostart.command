#!/bin/bash
# Starts the bot automatically every weekday at 9:20 AM (paper mode), and also whenever you log in
# (so a restart or a late login still catches the trading day). Outside market hours it just waits or exits.
# Run once:  ./install-autostart.command      Undo:  ./uninstall-autostart.command
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
LABEL="com.botcity.optionsbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ ! -x "$DIR/.venv/bin/python" ]; then echo "✖ Run ./setup.command first."; exit 1; fi
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data"
DAYS=""
for d in 1 2 3 4 5; do
  DAYS="$DAYS<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>20</integer></dict>"
done
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-s</string><string>-i</string><string>-m</string>
    <string>$DIR/.venv/bin/python</string><string>$DIR/run.py</string>
    <string>paper</string><string>--no-browser</string><string>--exit-after-close</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StartCalendarInterval</key><array>$DAYS</array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/bot.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/bot.log</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "✔ Auto-start ON: the bot will start itself at 9:20 AM every weekday (paper mode)."
echo ""
echo "  Watch it:  open http://localhost:8050   or   ./watch-log.command"
echo "  IMPORTANT - the Mac must stay awake during market hours:"
echo "     plugged in, lid OPEN, and run once:  ./keep-awake-setup.command"
