# pivot_brain — the PIVOT brain (Skill 44 V1) as a stand-alone deployment

The paper/live trader for ONE setup, exactly as it runs in the research project since 2026-10-13: 5-minute EMA 9 crossing the daily Fibonacci pivots (P, R1, R2, S1, S2), stop at the previous 5-minute candle's extreme, target 1:3, one trade per level per direction per day, max 4 entries, flat by 15:15, expressed as one lot of the weekly NIFTY option two strikes in the money, premium cap ₹15,000 per lot, day stop −₹5,000, week brake −₹10,000 (live), stop guard 120 index points. Plain Python 3.11+, standard library only, no cloud services; ≈ 6 MB without logs.

Carried over unchanged from the research project: `engine/` (bars, EMA/VWAP, pivots, session labels), `tools/paper_trade.py` (the trader; `--live` for real orders), `tools/dhan_feed.py` (tick feed → 1m bars, with watchdog and gap backfill), `tools/live_broker.py` (Dhan super orders, IPv4-forced, hard limits, preflight), `tools/dhan_token.py` (TOTP token generation/renewal), `tools/live_manual.py` (manual test hook), `tools/dashboard.py`, `tools/journal.py` + `tools/regime.py` (session journal with mood labels), `tools/fetch_recent.py` (nightly data upkeep). Left out: every other brain's research, backtests, datasets (1.8 GB), reports.

## Prerequisites
- Linux or macOS with Python 3.11+ (`python3 -c "import zoneinfo, ssl"`). Server timezone Asia/Kolkata.
- Dhan account with the DhanHQ API and TOTP enabled; Data API plan (feed + historical); for live orders the server's **static IPv4 whitelisted** on web.dhan.co.
- Optional: Telegram bot for alerts.
Secrets are never in the repository: `~/.dhan/credentials.json` (client id, PIN, TOTP secret), `~/.dhan/access_token`, `~/.dhan/telegram.json`, all mode 600; or the environment variables listed in `deploy/env.example`.

## Setup (server)
```bash
sudo mkdir -p /opt/pivot_brain && sudo chown $USER /opt/pivot_brain && cp -r . /opt/pivot_brain && cd /opt/pivot_brain
sh deploy/install.sh
python3 tools/dhan_token.py setup          # client id, PIN, TOTP secret → ~/.dhan/credentials.json
python3 tools/dhan_token.py auto           # 24 h token → ~/.dhan/access_token
python3 tools/dhan_token.py verify
python3 tools/paper_trade.py --telegram-setup <BOT_TOKEN>   # optional; add --telegram-channel @name for a members' channel (signals only)
python3 tools/fetch_recent.py              # pulls the latest sessions into datasets/raw/dhan (the previous session is needed for the pivots)
python3 tools/live_broker.py               # read-only live preflight: token, funds, stray positions, week brake
crontab deploy/crontab.txt                 # token 08:30/20:30, session 09:00, stop 15:40, data 15:45
sudo cp deploy/pivot-dashboard.service /etc/systemd/system/ && sudo systemctl enable --now pivot-dashboard
```
The dashboard binds to 127.0.0.1 only; open it from your machine with `ssh -L 8765:127.0.0.1:8765 user@server` then http://127.0.0.1:8765.

## Running
`deploy/run_session.sh` starts the token check, the feed, and the paper trader (and the live trader when `LIVE=1`); `deploy/stop_session.sh` stops them after close. Manual equivalents:
```bash
python3 tools/dhan_feed.py
python3 tools/paper_trade.py --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/paper_<date>.log
python3 tools/paper_trade.py --live --i-accept-live-risk --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/live_<date>.log
```
Ledgers: `paper/TRADES_<date>.csv`, `paper/SESSION_<date>.md`, `paper/JOURNAL.md`; live in `paper/live/`. The trader's capital in live mode is read from Dhan's available funds at the start of each session.

## Live-mode hard limits (in code, not configurable from the dashboard)
1 lot · max 4 entries/day · day stop −₹5,000 · week brake −₹10,000 · no entry after 15:00 · flat by 15:15 · INTRADAY product (broker square-off backstop) · premium cap ₹15,000/lot · stop guard 120 pts · PIVOT brain only · super orders with broker-held target and stop legs · failed sell → 3 retries then a loud manual-exit alert. Preflight refuses to start on a stale token, funds < ₹12,000, a stray option position, or a hit week brake.

## What is NOT here
Backtesting (`tools/backtest_brain.py`), the other brains, research notes and datasets. The dashboard's Backtests/Data pages therefore show nothing on this deployment; Paper trading, Journal, Token and Jobs work.
