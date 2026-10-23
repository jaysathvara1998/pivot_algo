# pivot_brain — complete setup and operating guide

Step-by-step procedure for installing and running the PIVOT brain (paper and live) on a server or a laptop. `README.md` is the short overview; this file is the full procedure, each step a command with what to expect.

---

## 0. What you are installing

| part | script | job |
|---|---|---|
| feed daemon | `tools/dhan_feed.py` | Dhan WebSocket ticks → 1-minute NIFTY bars + ATM±3 option quotes, written to `paper/FEED_<date>.json` every 2 s |
| paper trader | `tools/paper_trade.py --brains PIVOT …` | reads the feed, applies the PIVOT rule, books simulated fills with charges; ledger in `paper/` |
| live trader | `tools/paper_trade.py --live …` | same rule, real Dhan super orders (entry + broker-held target/stop legs); ledger in `paper/live/` |

Plus the token tool (`tools/dhan_token.py`), the dashboard (`tools/dashboard.py`, local only), the journal (`tools/journal.py`) and nightly data upkeep (`tools/fetch_recent.py`). Python 3.11+ standard library only: nothing to pip-install.

The rule: 5-minute EMA 9 crossing the daily Fibonacci pivots (P, R1, R2, S1, S2 from the previous session's high, low, close); entry at the signal bar's close; stop = previous 5-minute candle's extreme; target 1:3; exits on 1-minute closes; one signal per level per direction per day; max 4 entries; entries 09:20–15:00; flat at 15:15; one lot of the weekly NIFTY option two strikes in the money; premium cap ₹15,000 per lot; stop guard 120 index points; day stop −₹5,000.

---

## 1. Server prerequisites

Tested on Ubuntu 22.04/24.04 and macOS. A 1-vCPU / 1 GB VM is enough.

```bash
python3 --version                              # 3.11 or newer
python3 -c "import zoneinfo, ssl; print('ok')"
sudo timedatectl set-timezone Asia/Kolkata     # Linux; all schedules below are IST
date
```

Outbound HTTPS to api.dhan.co, auth.dhan.co, images.dhan.co, api-feed.dhan.co (WebSocket) and api.telegram.org. **The server needs a fixed public IPv4**: Dhan rejects order placement from any address not whitelisted on the account (DH-905 "Invalid IP"). Find it with `curl -4 -s https://api.ipify.org`.

---

## 2. Install the project

```bash
sudo mkdir -p /opt/pivot_brain && sudo chown $USER /opt/pivot_brain
# copy the folder:  scp -r pivot_brain/ user@server:/opt/pivot_brain   (or git clone if you pushed it)
cd /opt/pivot_brain
sh deploy/install.sh        # timezone, python check, creates ~/.dhan (mode 700), prints the next steps
```

If you use another path, edit `deploy/crontab.txt` and `deploy/pivot-dashboard.service`.

---

## 3. Dhan account prerequisites (once, on web.dhan.co)

1. DhanHQ APIs → enable API access; note the client ID.
2. Enable TOTP (DhanHQ APIs page) → copy the base32 secret shown during setup. The server uses it to generate a fresh 24-hour token every day.
3. Static IP → add the server's IPv4 as the primary IP. It cannot be changed again for 7 days. Orders are refused from any other address; data and quotes are unaffected.
4. Funds: keep at least ₹20,000 free for one lot of a 2-strike ITM weekly option (premiums ₹150–₹240 × 65). The live preflight refuses to start below ₹12,000.
5. Data API plan active (feed and historical bars).

---

## 4. Credentials on the server

Secrets live only under `~/.dhan/` (mode 600) or in environment variables, never in the project folder.

Option A, type them in (recommended):

```bash
cd /opt/pivot_brain
python3 tools/dhan_token.py setup      # prompts without echo: client ID, PIN, TOTP base32 secret (API key/secret: leave blank)
```

Option B, copy from the laptop where they already work:

```bash
sh deploy/pack_credentials.sh                      # laptop: writes ~/dhan_credentials.tgz (mode 600)
scp ~/dhan_credentials.tgz user@server:~
tar xzf ~/dhan_credentials.tgz -C ~ && chmod 700 ~/.dhan && chmod 600 ~/.dhan/* && rm ~/dhan_credentials.tgz   # server
```

Then generate and verify a token from the server (the token must be issued after the IP was whitelisted):

```bash
python3 tools/dhan_token.py auto       # renews a valid token, else generates one with TOTP → ~/.dhan/access_token
python3 tools/dhan_token.py verify     # prints the profile incl. tokenValidity (24 h from issue)
```

If `setup` says it has no terminal, use `DHAN_PIN=… python3 tools/dhan_token.py setup --from-env` (stored client ID and TOTP secret are kept).

---

## 5. Telegram alerts (optional, strongly recommended for live)

1. Create a bot with @BotFather and copy the bot token.
2. Open the bot in Telegram and send it any message.
3. On the server:

```bash
python3 tools/paper_trade.py --telegram-setup <BOT_TOKEN>    # finds your chat id, saves ~/.dhan/telegram.json, sends a test
python3 tools/paper_trade.py --telegram-test
```

You will receive: trader start/stop, every ENTRY and EXIT (tagged PAPER or LIVE), day stop, refused signals, feed warnings, live order failures, and the end-of-session summary with the running capital.

---

## 6. Data bootstrap

The brain needs the previous session's bars for its pivots; the journal uses the trailing 60 sessions for mood labels. Refresh the shipped files:

```bash
python3 tools/fetch_recent.py          # appends missing sessions from Dhan to datasets/raw/dhan/nifty_*_ext.csv
```

Run nightly (cron below). The option scrip master downloads itself when older than 24 h.

---

## 7. Preflight (read-only, no orders)

```bash
python3 tools/live_broker.py           # token, funds, stray option positions, weekly brake → "OK …" or "BLOCKED …"
python3 tools/paper_trade.py --live --dry-run --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --replay --date <last trading day>
```

The dry run replays a whole session through the live code path with synthetic fills and places nothing; it must end with a session report and no traceback. Confirm Dhan sees the whitelisted IP (expect `PRIMARY_MATCH` and `ordersAllowed: true`):

```bash
python3 -c "import sys; sys.path.insert(0,'tools'); import live_broker as lb; print(lb._call('GET','/ip/getIP'))"
```

---

## 8. Starting (manual)

Order: token → feed → trader(s). Start the feed before 09:15; the traders can start any time after and wait for bars.

```bash
cd /opt/pivot_brain; D=$(date +%Y-%m-%d); mkdir -p paper/logs logs
python3 tools/dhan_token.py auto                                                      # 1. token
nohup python3 tools/dhan_feed.py > paper/logs/feed_$D.log 2>&1 &                       # 2. feed
# 3a. paper trader (no orders)
nohup python3 tools/paper_trade.py --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/paper_$D.log > paper/logs/paper_stdout_$D.log 2>&1 &
# 3b. live trader (REAL ORDERS) — only when you mean it
nohup python3 tools/paper_trade.py --live --i-accept-live-risk --brains PIVOT --pivot-itm 2 --pivot-cap 15000 --feed --log paper/logs/live_$D.log > paper/logs/live_stdout_$D.log 2>&1 &
```

A healthy start in the logs:

```
feed: nearest expiry 2026-10-27, 522 contracts in the scrip master
feed: backfilled N completed 1m bars from the historical API
feed: connected 09:0x:xx
PAPER TRADING (no orders) · NIFTY · <date> · brains ['PIVOT+ITM2'] · …
preflight OK: … token ok … available funds ₹… · no open option positions     (live)
live capital from Dhan: available funds ₹…                                     (live)
LIVE TRADING — REAL ORDERS · NIFTY · <date> · brains ['PIVOT+ITM2'] · …        (live)
09:16:01  bar 09:16 index 2xxxx.x VWAP 2xxxx.x · open: none · equity ₹…
```

Or the script, which does all of the above with the right checks:

```bash
LIVE=0 sh deploy/run_session.sh        # feed + paper
LIVE=1 sh deploy/run_session.sh        # feed + paper + live
```

Paper and live are separate processes with separate ledgers and run side by side.

---

## 9. Dashboard

```bash
sudo cp deploy/pivot-dashboard.service /etc/systemd/system/
sudo systemctl enable --now pivot-dashboard
```

It binds to 127.0.0.1:8765 only. From your laptop: `ssh -L 8765:127.0.0.1:8765 user@server`, then open http://127.0.0.1:8765. Working pages: Overview, Paper trading (start/stop paper and live, chart, trades), Token, Journal, Jobs. Backtests and Data pages are empty on this deployment.

---

## 10. Automation (cron)

```bash
crontab deploy/crontab.txt && crontab -l
```

| time (IST) | job |
|---|---|
| 08:30 Mon–Fri | `dhan_token.py auto` |
| 09:00 Mon–Fri | `run_session.sh` with `LIVE=0` (set `LIVE=1` in the file to go live automatically) |
| 15:40 Mon–Fri | `stop_session.sh` (the traders squared off at 15:15) |
| 15:45 Mon–Fri | `fetch_recent.py` |
| 20:30 daily | `dhan_token.py auto` |

Holidays need nothing: no bars arrive, nothing trades, the processes exit after 15:35.

---

## 11. Daily routine and what to watch

1. 08:45: token job ran (`logs/token.log`), dashboard token chip green.
2. 09:00–09:16: "Paper trader started" (and "LIVE trader started") on Telegram; feed log says "connected".
3. During the day every signal produces one of: `ENTRY …`, `skipped (too expensive)` (lot above ₹15,000), `refused: stop … exceeds the 120-pt risk limit`, `skipped (position open)`, `signal noted but stale`. These are the rule working.
4. 15:15 open positions close (TIME). 15:30 the session report is written (`paper/SESSION_<date>.md`, live under `paper/live/`), the journal rebuilds, Telegram gets the summary with the running capital.
5. Ledgers: `paper/TRADES_<date>.csv`, `paper/live/TRADES_<date>.csv` (every fill, charges, net), `paper/JOURNAL.md`.

Act on: `feed snapshot is N s old` (feed died: restart it), `WARNING feed ticks are N s old` (socket stalled; the watchdog reconnects itself), `LIVE BUY FAILED` (read the reason: IP, funds, token), `LIVE SELL FAILED … EXIT MANUALLY` (close the position on the Dhan app, then stop the live trader).

---

## 12. Stopping and emergencies

```bash
sh deploy/stop_session.sh                      # feed + traders (normally after 15:35)
pkill -f "tools/paper_trade.py --live"          # live trader only
pkill -f "tools/paper_trade.py --brains"        # paper trader only
```

Stopping the live trader does not close an open live position. With a position open: close it on the Dhan app first, then stop the trader; otherwise its later sell could open a short. Restarting during the day is safe: the trader reloads today's state and never re-enters an old signal.

Order-path tests: `python3 tools/live_order_test.py --probe` (limit + cancel, zero cost) then `--roundtrip` (real buy and sell of a cheap far-OTM contract, ≈ ₹100–₹200). Manual test trade through the running live trader: `python3 tools/live_manual.py CE --sl-prem 8 --tp-prem 12`.

---

## 13. Updating the code

Copy the changed files (or `git pull`), run `python3 -m compileall -q engine tools`, then restart only what changed: the feed any time (it backfills), a trader when flat.

---

## 14. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `preflight BLOCKED … insufficient funds` | available balance < ₹12,000 | add funds |
| `LIVE BUY FAILED … DH-905 Invalid IP` | server IP not whitelisted, or token issued before the whitelist | whitelist the IPv4, then `dhan_token.py auto`; `/ip/getIP` must say PRIMARY_MATCH |
| `LIVE BUY FAILED … insufficient funds` (RMS) | another position holds margin | keep one position in the account while the trader runs |
| HTTP 401 / token invalid | token expired | `python3 tools/dhan_token.py auto`; check cron |
| `feed snapshot is N s old` | feed daemon not running | start it (step 8) |
| `WARNING feed ticks are N s old` | WebSocket stalled | watchdog reconnects within 20 s and backfills; if repeated, check the network |
| `skipped (too expensive)` | lot > ₹15,000 | by design (volatility filter; raising it tested worse) |
| `refused: stop … exceeds the 120-pt risk limit` | gap-open signal with a huge stop | by design |
| `signal noted but stale` | trader started after the signal bar | by design |
| dashboard "Failed: Unknown" | old dashboard process | `sudo systemctl restart pivot-dashboard` |
