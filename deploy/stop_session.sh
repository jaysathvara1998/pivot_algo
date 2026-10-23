#!/bin/sh
# Stops the traders and the feed (run after 15:35). Does NOT close an open live position — the trader squares off at 15:15 by itself.
pkill -f "tools/paper_trade.py"; pkill -f "tools/dhan_feed.py"; echo "stopped"
