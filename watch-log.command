#!/bin/bash
# Shows what the auto-started bot is doing (Ctrl+C closes this view - the bot keeps running).
cd "$(dirname "$0")" && touch data/bot.log && tail -n 40 -f data/bot.log
