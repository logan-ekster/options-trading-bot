#!/bin/bash
# One-time Mac power settings so the bot isn't put to sleep mid-day. Asks for your Mac password.
echo "Setting: never sleep while plugged in, and wake at 9:10 AM on weekdays..."
sudo pmset -c sleep 0 && sudo pmset -c disksleep 0 && sudo pmset -c powernap 0 && \
sudo pmset repeat wakeorpoweron MTWRF 09:10:00 && echo "✔ Done." && pmset -g | grep -E " sleep|powernap" && pmset -g sched
echo ""
echo "Still required: keep the Mac PLUGGED IN and the LID OPEN (closing the lid sleeps it no matter what)."
echo "The screen can turn off - that's fine."
