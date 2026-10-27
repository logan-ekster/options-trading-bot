#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.botcity.optionsbot.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
rm -f "$PLIST"
echo "✔ Auto-start OFF. (To also stop the 9:10 wake-up: sudo pmset repeat cancel)"
