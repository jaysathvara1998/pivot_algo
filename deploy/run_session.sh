#!/bin/sh
# Starts the day's processes. Cron it at 09:00 IST on trading days (see crontab.txt). LIVE is started only if LIVE=1 is exported.
cd "$(dirname "$0")/.." || exit 1
D=$(date +%Y-%m-%d); mkdir -p paper/logs logs
python3 tools/dhan_token.py auto >> logs/token.log 2>&1
if ! pgrep -f "tools/dhan_feed.py" >/dev/null; then nohup python3 tools/dhan_feed.py > paper/logs/feed_$D.log 2>&1 & echo "feed started"; fi
sleep 45
if ! pgrep -f "tools/paper_trade.py --brains" >/dev/null; then nohup python3 tools/paper_trade.py --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/paper_$D.log > paper/logs/paper_stdout_$D.log 2>&1 & echo "paper trader started"; fi
if [ "$LIVE" = "1" ] && ! pgrep -f "tools/paper_trade.py --live" >/dev/null; then nohup python3 tools/paper_trade.py --live --i-accept-live-risk --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/live_$D.log > paper/logs/live_stdout_$D.log 2>&1 & echo "LIVE trader started"; fi
