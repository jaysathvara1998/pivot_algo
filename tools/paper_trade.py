#!/usr/bin/env python3
"""Automated PAPER trading on NIFTY weekly ATM options with TWO independent brains on one ₹50,000 account. No orders are ever placed.
  Brain 1 "VWAP"  — Skill 39 A: VWAP pullback touch-and-hold, exit P2 (stop VWAP∓R moving with VWAP, target = run extreme), 1m bars.
  Brain 2 "PIVOT" — Skill 44: EMA 9 crossing a daily Fibonacci pivot (P/R1/R2/S1/S2) on 5m bars, stop = previous 5m candle extreme, target 1:3.
  Brain 3 "EXPIRY" — Skill 48 (admitted 2026-10-14): on NIFTY weekly-expiry days only, at the 13:00 close buy the VWAP-side option (|close − VWAP| ≥ 15), stop 25 pts, exit 15:15; 2 strikes in the money.
Each brain holds at most one position; both may be open at once. Fills at the signal minute's option close + ₹0.50 (exit −₹0.50), costs per leg as the backtests,
1 lot per trade if premium × lot ≤ ₹12,500, day stop −₹5,000 (combined), max 4 entries per brain, entries 09:20/09:45–15:00, everything flat by 15:15.
Live:   python3 tools/paper_trade.py [--brains PIVOT,EXPIRY] [--r 7] [--rr 3]      Replay: python3 tools/paper_trade.py --date 2026-10-09 --replay
Telegram: --telegram-setup BOT_TOKEN once (private chat: everything); --telegram-channel @name adds a channel for members (start / entry / exit / day stop / session summary only). Files: paper/TRADES_<date>.csv, paper/STATE_<date>.json, paper/SESSION_<date>.md."""
from __future__ import annotations

import argparse, csv, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from engine.ohlc import Candle  # noqa: E402
from engine.vwappb import pullback_signals  # noqa: E402
from engine.emavwap import session_twap, kbars, ema  # noqa: E402
from engine.pivots import pivot_cross_signals, daily_pivots  # noqa: E402
from engine.premium.ironfly import lot_on  # noqa: E402
from engine.monitor import load_1m  # noqa: E402

IST = ZoneInfo("Asia/Kolkata"); M1 = timedelta(minutes=1); SID = "13"; SEG_OPT = "NSE_FNO"
LIVE_DELTA = {0: 0.50, 1: 0.60, 2: 0.70, 3: 0.78}      # option delta by strikes in the money, used to map index stop/target distances onto the premium for super-order legs
MAX_RISK_PTS = 120.0                                    # refuse any signal whose index stop is further than this (six-month backtest: median 26 pts, 95th pct 74, max 108; the 2026-10-21 feed-gap artefact was 157)
CAPITAL = 50_000.0; MAX_PREMIUM_SHARE = 0.25; DAY_STOP = -5_000.0; MAX_ENTRIES = 4; SLIP = 0.50; T_EXIT = dtime(15, 15); LAST_ENTRY = dtime(15, 0)
TG_FILE = Path.home() / ".dhan" / "telegram.json"


# ---------------- Dhan ----------------
def token():
    t = os.environ.get("DHAN_ACCESS_TOKEN") or (Path.home() / ".dhan" / "access_token").read_text().strip()
    return t or sys.exit("no token")


def post(path, body):
    req = urllib.request.Request("https://api.dhan.co/v2" + path, data=json.dumps(body).encode(), headers={"access-token": token(), "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def bars_for(d):
    r = post("/charts/intraday", {"securityId": SID, "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "oi": False, "fromDate": d.isoformat(), "toDate": d.isoformat()})
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        t = datetime.fromtimestamp(ts, IST)
        if t.date() == d:
            out.append(Candle(t, r["open"][i], r["high"][i], r["low"][i], r["close"][i], M1))
    return out


def atm_for(d):
    """{minute: {'CE','PE','strike','book': {(leg, strike): close}}} — rolling ATM plus ATM±1..±3 for both legs."""
    out = {}
    for leg in ("CALL", "PUT"):
        L = "CE" if leg == "CALL" else "PE"
        for off in ("ATM", "ATM+1", "ATM+2", "ATM+3", "ATM-1", "ATM-2", "ATM-3"):
            r = post("/charts/rollingoption", {"exchangeSegment": SEG_OPT, "interval": 1, "securityId": SID, "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1, "strike": off, "drvOptionType": leg,
                                               "requiredData": ["open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"], "fromDate": d.isoformat(), "toDate": (d + timedelta(days=1)).isoformat()})
            dd = (r.get("data") or {}).get("ce" if leg == "CALL" else "pe") or {}
            for i, ts in enumerate(dd.get("timestamp") or []):
                t = datetime.fromtimestamp(ts, IST)
                if t.date() != d:
                    continue
                row = out.setdefault(t.strftime("%H:%M"), {"book": {}}); row["book"][(L, dd["strike"][i])] = dd["close"][i]
                if off == "ATM":
                    row[L] = dd["close"][i]; row["strike"] = dd["strike"][i]
            time.sleep(0.25)
    return out


def prev_session_bars(d):
    """Previous session's 1m bars: from the local store if present, else from Dhan (looking back up to 7 days)."""
    stored = {}
    for c in load_1m("NIFTY"):
        if c.timestamp.date() < d:
            stored.setdefault(c.timestamp.date(), []).append(c)
    if stored:
        last = max(stored); return stored[last]
    for k in range(1, 8):
        b = bars_for(d - timedelta(days=k))
        if b:
            return b
    return []


def leg_cost(lot, p_in, p_out):
    """Per-unit round-trip charges for one bought option lot, NSE, Dhan flat-brokerage plan (rates in force since 1 Oct 2024):
    brokerage ₹20 per order × 2 + 18 % GST · STT 0.1 % of the sell premium · NSE transaction charge 0.03503 % of premium both sides + 18 % GST ·
    SEBI turnover fee ₹10 per crore both sides + GST · stamp duty 0.003 % of the buy premium. Slippage is applied to the fill price, not here.
    Applied identically to paper fills and live fills (the live ledger's cost_inr column), so both ledgers are net of charges."""
    brokerage = 2 * 20 * 1.18 / lot
    stt = 0.001 * p_out
    exch = 0.0003503 * 1.18 * (p_in + p_out)
    sebi = 0.000001 * 1.18 * (p_in + p_out)
    stamp = 0.00003 * p_in
    return brokerage + stt + exch + sebi + stamp


def option_price(atm, k, leg, strike):
    row = atm.get(k)
    return row.get("book", {}).get((leg, strike)) if row else None


# ---------------- Telegram ----------------
def tg_config():
    """(bot_token, private chat id, channel or None). The private chat gets everything; the channel (optional, for other members) gets only the public signals: start, ENTRY, EXIT, day stop, session summary."""
    c = json.loads(TG_FILE.read_text()) if TG_FILE.exists() else {}
    tok = os.environ.get("TELEGRAM_BOT_TOKEN") or c.get("bot_token"); chat = os.environ.get("TELEGRAM_CHAT_ID") or c.get("chat_id"); chan = os.environ.get("TELEGRAM_CHANNEL") or c.get("channel")
    return (tok, chat, chan or None) if tok and chat else (None, None, None)


def _tg_post(tok, chat_id, text):
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("ok", False)


def tg_send(text, public=False):
    """Sends to the private chat; with public=True also to the channel if one is configured. Returns True if at least one delivery succeeded."""
    if os.environ.get("TRADINGALGO_NO_TG"):
        return False
    tok, chat, chan = tg_config()
    if not tok:
        return False
    ok = False
    for target in ([chat] + ([chan] if public and chan else [])):
        try:
            ok = _tg_post(tok, target, text) or ok
        except Exception as e:
            print(f"telegram send to {target} failed: {str(e)[:100]}", flush=True)
    return ok


def tg_setup(bot_token):
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=15) as r:
            upd = json.loads(r.read())
    except Exception as e:
        sys.exit(f"getUpdates failed: {e}")
    chats = {}
    for u in upd.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        if m.get("chat"):
            chats[m["chat"]["id"]] = m["chat"].get("username") or m["chat"].get("title") or m["chat"].get("first_name")
    if not chats:
        sys.exit("no chats found: send any message to your bot first, then run again")
    chat_id = next(iter(chats)) if len(chats) == 1 else int(input(f"several chats {chats}; chat id to use: "))
    keep = json.loads(TG_FILE.read_text()) if TG_FILE.exists() else {}
    TG_FILE.parent.mkdir(mode=0o700, exist_ok=True); TG_FILE.write_text(json.dumps({**keep, "bot_token": bot_token, "chat_id": chat_id})); os.chmod(TG_FILE, 0o600)
    print("saved", TG_FILE, "test", "sent" if tg_send("TradingAlgo paper trader connected (2 brains: VWAP, PIVOT).") else "FAILED")


def tg_channel(channel):
    """Add (or with 'off' remove) a Telegram channel that receives the public signals so other members can follow. The bot must be an administrator of the channel with permission to post.
    channel: @publicname, or the numeric id (-100…) for a private channel, or an invite-less private channel id from getUpdates after one post in it."""
    c = json.loads(TG_FILE.read_text()) if TG_FILE.exists() else {}
    if not c.get("bot_token"):
        sys.exit("run --telegram-setup BOT_TOKEN first (the private chat stays the operator's channel for warnings and failures)")
    if channel.strip().lower() in ("off", "none", ""):
        c.pop("channel", None); TG_FILE.write_text(json.dumps(c)); os.chmod(TG_FILE, 0o600); print("channel removed; alerts go to the private chat only"); return
    ch = channel.strip()
    if not ch.startswith("@") and not re.fullmatch(r"-?\d+", ch):
        ch = "@" + ch
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{c['bot_token']}/getChat?chat_id={urllib.parse.quote(ch)}", timeout=15) as r:
            info = json.loads(r.read()).get("result", {})
    except Exception as e:
        sys.exit(f"getChat failed for {ch}: {str(e)[:160]}\n  make the bot an administrator of the channel (with 'Post messages'), then retry; for a private channel use its numeric id (-100…)")
    if info.get("type") not in ("channel", "supergroup", "group"):
        sys.exit(f"{ch} is a {info.get('type')} chat, not a channel or group")
    c["channel"] = info.get("id", ch); TG_FILE.write_text(json.dumps(c)); os.chmod(TG_FILE, 0o600)
    try:
        sent = _tg_post(c["bot_token"], c["channel"], f"TradingAlgo signals connected to this channel ({info.get('title') or ch}). You will see trader start, every ENTRY and EXIT, day stops and the session summary.")
    except Exception as e:
        sent = False; print(f"test post failed: {str(e)[:160]} — is the bot an admin with 'Post messages'?")
    print("saved", TG_FILE, f"channel {info.get('title') or ch} (id {c['channel']})", "test post", "sent" if sent else "FAILED")


# ---------------- Brains ----------------
class Brain:
    """A brain yields entries from completed bars and judges exits each minute. Subclasses implement signals() and exit_check()."""
    name = "BASE"; first_entry = dtime(9, 20); touch = False

    def __init__(self, R, prev_bars):
        self.R = R; self.prev_bars = prev_bars

    def signals(self, bars, done):                          # -> [(i1m, side, stop_level, target_level, note)] from completed bars[:done]
        return []

    def exit_check(self, pos, bars, j, tw):                  # -> reason or None, judged on completed bar j
        side = pos["side"]; b = bars[j]
        if self.touch:                                       # Part G: stop / target orders fill on a touch of the level (bar extreme), at the level
            adverse = b.low if side > 0 else b.high; favour = b.high if side > 0 else b.low
            hit_stop = side * (adverse - pos["stop"]) <= 0; hit_tgt = pos["target"] is not None and side * (favour - pos["target"]) >= 0
            if hit_stop and hit_tgt:                         # both inside one bar: the order nearer the open fills first
                hit_tgt = abs(b.open - pos["target"]) < abs(b.open - pos["stop"]); hit_stop = not hit_tgt
            if hit_stop:
                pos["fill_index"] = pos["stop"]; return "STOP"
            if hit_tgt:
                pos["fill_index"] = pos["target"]; return "TARGET"
            if b.timestamp.time() >= T_EXIT:
                return "TIME"
            return None
        if side * (b.close - pos["stop"]) <= 0:
            return "STOP"
        if pos["target"] is not None and side * (b.close - pos["target"]) >= 0:
            return "TARGET"
        if getattr(self, "touch_target", False) and pos["target"] is not None:        # Part N (2026-10-23): target filled on a TOUCH (bar extreme), stop still on the close
            favour = b.high if side > 0 else b.low
            if side * (favour - pos["target"]) >= 0:
                pos["fill_index"] = pos["target"]; return "TARGET"
        if b.timestamp.time() >= T_EXIT:
            return "TIME"
        return None


class VwapBrain(Brain):
    """Skill 39 A, exit P2: stop = VWAP ∓ R (moves with VWAP), target = run extreme (≥ R)."""
    name = "VWAP"; first_entry = dtime(9, 45)

    def signals(self, bars, done):
        refs = [self.R] * len(bars); tw = session_twap(bars); out = []
        for i, side, ext, v, first in pullback_signals(bars[:done], refs, "A"):
            e = bars[i].close; tgt = ext if side * (ext - e) >= self.R else e + side * self.R
            out.append((i, side, (v - self.R) if side > 0 else (v + self.R), tgt, "first pullback" if first else ""))
        return out

    def exit_check(self, pos, bars, j, tw):
        side = pos["side"]; v = tw[j]; pos["stop"] = (v - self.R) if side > 0 else (v + self.R)      # moving stop
        return super().exit_check(pos, bars, j, tw)


class PivotBrain(Brain):
    """Skill 44 on 5m: EMA 9 crosses a daily Fibonacci pivot; stop = previous 5m candle's extreme; target = entry + rr × risk."""
    name = "PIVOT"; K = 5

    def __init__(self, R, prev_bars, rr=3.0, itm_steps=0, be_at_1r=False, confirm=False, trail=None, tf=5, struct_trail=None):
        super().__init__(R, prev_bars); self.rr = rr; self.strike_offset_steps = itm_steps; self.be_at_1r = be_at_1r; self.confirm = confirm; self.K = tf
        self.rr_by_level = {}; self.skip_levels = set(); self.be_by_level = {}; self.ladder = None; self.reenter = False; self._arm = None
        self.price_cross = False; self.next_pivot_min_r = 0.0; self.level_target = False; self.noprog = None; self.ladder_wide = False   # Part J options (all off in the running rule)
        self.recross = False   # Part M (2026-10-22): allow a second signal on the same level/side in a day (the tested rule takes only the first)
        self.retest = None   # Part L: {"bars": 6, "tol": 10, "pad": 5} → after the EMA cross wait for a 5m bar that pulls back to within `tol` pts of the level and closes back on the signal side; enter there, stop = that bar's extreme − pad   # reenter: after a TARGET exit, one continuation entry when a 5m close passes the old target in the same direction   # ladder: {"start": 3, "lag": 1} → no target; once the close reaches k R (k ≥ start) the stop steps to (k − lag) R   # be_by_level: {"R1": {"trigger": 2.0, "lock": 0.0}} → at 2 R move the stop to entry (+ lock R)                   # Part C variants (2026-10-16): per-level R:R, e.g. {"R1": 2, "S1": 2}; levels never traded, e.g. {"S2"}
        self.struct_trail = struct_trail                                  # None | "pivot" | "swing" | "both": no target, stop trails structure (SETUP_B.md Part E)
        self.levels = None
        if prev_bars:
            pH = max(b.high for b in prev_bars); pL = min(b.low for b in prev_bars); pC = prev_bars[-1].close; P = (pH + pL + pC) / 3; rng = pH - pL
            self.levels = sorted([P - rng, P - 0.618 * rng, P - 0.382 * rng, P, P + 0.382 * rng, P + 0.618 * rng, P + rng])
        self.trail = trail                                                # None, or dict(dist=multiple of initial risk, after=activation in R, target=True/False)
        self.name = ("PIVOT" if tf == 5 else f"PIVOT{tf}m") + ("".join(x for x, on in (("+ITM2", itm_steps), ("+BE", be_at_1r), ("+CONF", confirm), (f"+TRAIL{trail['dist']:g}{'' if trail['target'] else 'NT'}{'@' + str(trail['after']) if trail['after'] else ''}" if trail else "", trail), (f"+ST{struct_trail}" if struct_trail else "", struct_trail)) if on))

    def signals(self, bars, done):
        if self.price_cross:
            return self.price_cross_signals(bars, done)
        series = self.prev_bars + bars[:done]; bk = kbars(series, self.K); out = []
        pos = {b.timestamp: i for i, b in enumerate(bars)}
        last_done = bars[done - 1].timestamp if done else None
        for i, side, name, lvl, stop in pivot_cross_signals(bk, once_per_day=not self.recross):
            b5 = bk[i]
            if b5.timestamp.date() != bars[0].timestamp.date():
                continue
            ei = i
            if self.retest:                                               # Part L: retest entry
                found = None
                for n in range(i + 1, min(i + 1 + self.retest["bars"], len(bk))):
                    rb = bk[n]
                    if rb.timestamp.date() != b5.timestamp.date():
                        break
                    touched = (rb.low <= lvl + self.retest["tol"]) if side > 0 else (rb.high >= lvl - self.retest["tol"])
                    held = (rb.close > lvl) if side > 0 else (rb.close < lvl)
                    if touched and held:
                        found = n; break
                    if (side > 0 and rb.close < lvl - self.retest["tol"]) or (side < 0 and rb.close > lvl + self.retest["tol"]):
                        break                                             # the level failed before any retest
                if found is None:
                    continue
                ei = found; stop = (bk[found].low - self.retest["pad"]) if side > 0 else (bk[found].high + self.retest["pad"])
            if self.confirm:                                              # V3: the next 5m bar must close on the signal side of the level; enter at its close; stop = the signal bar's extreme
                if i + 1 >= len(bk) or bk[i + 1].timestamp.date() != b5.timestamp.date():
                    continue
                nb = bk[i + 1]
                if (side > 0 and nb.close <= lvl) or (side < 0 and nb.close >= lvl):
                    continue
                ei = i + 1; stop = b5.low if side > 0 else b5.high
            end_min = bk[ei].timestamp + timedelta(minutes=self.K - 1)
            if last_done is None or end_min > last_done:                  # 5m bar not complete yet
                continue
            i1 = pos.get(end_min)
            if i1 is None:
                continue
            e = bars[i1].close; risk = side * (e - stop)
            if risk <= 0 or name in self.skip_levels:
                continue
            rr = self.rr_by_level.get(name, self.rr)
            nxt = self.next_level(e, side)
            if self.next_pivot_min_r and nxt is not None and side * (nxt - e) < self.next_pivot_min_r * risk:
                continue                                                  # J2: the next pivot is too close for the trade to breathe
            tgt = None if ((self.trail and not self.trail["target"]) or self.struct_trail or self.ladder) else e + side * rr * risk
            if self.level_target and tgt is not None and nxt is not None and 1.5 * risk <= side * (nxt - e) < rr * risk:
                tgt = nxt                                                 # J3: take the next pivot when it sits between 1.5 R and 3 R
            out.append((i1, side, stop, tgt, f"EMA9 crossed {name} {lvl:.0f}" + (" (retest entry)" if self.retest else "") + (f" (1:{rr:g})" if rr != self.rr else "") + (" → level target" if (self.level_target and tgt == nxt) else "")))
        if self.reenter:
            out += self.continuation(bars, done)
        return out

    def next_level(self, e, side):
        if not self.levels:
            return None
        ahead = [L for L in self.levels if side * (L - e) > 0]
        return (min(ahead) if side > 0 else max(ahead)) if ahead else None

    def price_cross_signals(self, bars, done):
        """J1: the 5m CLOSE crosses a pivot and EMA 9 is already on that side (or gets there within 2 bars); entry at that bar's close; stop = previous 5m extreme."""
        series = self.prev_bars + bars[:done]; bk = kbars(series, self.K); e9 = ema([b.close for b in bk], 9); out = []
        pos = {b.timestamp: i for i, b in enumerate(bars)}; last_done = bars[done - 1].timestamp if done else None; today = bars[0].timestamp.date(); used = set()
        from engine.pivots import daily_pivots as _dp
        piv = _dp(bk)
        for i in range(10, len(bk)):
            b = bk[i]; pv = piv[i]
            if b.timestamp.date() != today or not pv or not (dtime(9, 20) <= b.timestamp.time() <= LAST_ENTRY):
                continue
            for name in ("P", "R1", "R2", "S1", "S2"):
                lvl = pv[name]; side = 1 if (bk[i - 1].close < lvl <= b.close) else (-1 if (bk[i - 1].close > lvl >= b.close) else 0)
                if not side or (name, side) in used:
                    continue
                for j in range(i, min(i + 3, len(bk))):                  # EMA 9 must be on the signal side at the cross bar or within the next two
                    if side * (e9[j] - lvl) > 0:
                        end_min = bk[j].timestamp + timedelta(minutes=self.K - 1)
                        if last_done is None or end_min > last_done:
                            break
                        i1 = pos.get(end_min)
                        if i1 is None:
                            break
                        used.add((name, side)); stop = bk[j - 1].low if side > 0 else bk[j - 1].high; e = bars[i1].close; risk = side * (e - stop)
                        if 0 < risk <= MAX_RISK_PTS and name not in self.skip_levels:
                            out.append((i1, side, stop, e + side * self.rr * risk, f"price crossed {name} {lvl:.0f}, EMA9 confirmed"))
                        break
        return out

    def on_exit(self, pos, why, k):
        if self.reenter and why == "TARGET" and pos.get("target") is not None:
            self._arm = {"side": pos["side"], "level": pos["target"], "after": k}

    def continuation(self, bars, done):
        """One re-entry: the first completed 5m bar after a target exit that closes beyond the old target in the trade's direction; stop = that bar's opposite extreme of the previous 5m bar, target 1:3."""
        if not self._arm:
            return []
        bk = kbars(bars[:done], self.K); pos = {b.timestamp: i for i, b in enumerate(bars)}
        for n in range(1, len(bk)):
            b = bk[n]; k = b.timestamp.strftime("%H:%M")
            if k <= self._arm["after"]:
                continue
            side = self._arm["side"]
            if side * (b.close - self._arm["level"]) > 0:
                end_min = b.timestamp + timedelta(minutes=self.K - 1); i1 = pos.get(end_min)
                if i1 is None or i1 > done - 1:
                    return []
                stop = bk[n - 1].low if side > 0 else bk[n - 1].high; e = bars[i1].close; risk = side * (e - stop)
                self._arm = None
                if risk <= 0 or risk > MAX_RISK_PTS:
                    return []
                return [(i1, side, stop, e + side * self.rr * risk, f"re-entry: 5m close beyond the {self._arm_note(self.rr)}")]
        return []

    @staticmethod
    def _arm_note(rr):
        return "previous target"

    @staticmethod
    def last_swing(bars, j, side, below):
        """Latest confirmed 3-bar fractal swing low (side>0) / high (side<0) at bars[k], k <= j-3, on the correct side of `below`."""
        for k in range(j - 3, 2, -1):
            if side > 0:
                if all(bars[k].low < bars[k + m].low for m in (1, 2, 3)) and all(bars[k].low < bars[k - m].low for m in (1, 2, 3)) and bars[k].low < below:
                    return bars[k].low
            else:
                if all(bars[k].high > bars[k + m].high for m in (1, 2, 3)) and all(bars[k].high > bars[k - m].high for m in (1, 2, 3)) and bars[k].high > below:
                    return bars[k].high
        return None

    def exit_check(self, pos, bars, j, tw):
        if self.struct_trail:                                             # Part E: trail on pivots and/or swings; stop only moves in the trade's favour
            side = pos["side"]; c = bars[j].close; cands = []
            if self.struct_trail in ("pivot", "both") and self.levels:
                e = pos["index_entry"]
                pos["hwm_c"] = max(pos.get("hwm_c", c), c) if side > 0 else min(pos.get("hwm_c", c), c)
                cleared = [L for L in self.levels if (side > 0 and e < L < pos["hwm_c"] and L < c) or (side < 0 and e > L > pos["hwm_c"] and L > c)]
                if cleared:
                    cands.append(max(cleared) if side > 0 else min(cleared))
            if self.struct_trail in ("swing", "both"):
                sw = self.last_swing(bars, j, side, c)
                if sw is not None:
                    cands.append(sw)
            for lvl in cands:
                if side * (lvl - pos["stop"]) > 0:
                    pos["stop"] = lvl; pos["trailing"] = True
            why = super().exit_check(pos, bars, j, tw)
            return "TRAIL" if (why == "STOP" and pos.get("trailing")) else why
        if self.trail:                                                    # Part C: trailing stop from the high-water mark of favourable closes
            side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"]); fav = side * (bars[j].close - pos["index_entry"])
            pos["hwm"] = max(pos.get("hwm", 0.0), fav)
            if pos["hwm"] >= self.trail["after"] * risk:
                new_stop = pos["index_entry"] + side * (pos["hwm"] - self.trail["dist"] * risk)
                if side * (new_stop - pos["stop"]) > 0:
                    pos["stop"] = new_stop; pos["trailing"] = True
            why = super().exit_check(pos, bars, j, tw)
            return "TRAIL" if (why == "STOP" and pos.get("trailing")) else why
        if self.noprog:                                                   # J4: if the trade has not closed ≥ r R in its favour within N 5m bars, exit at market
            side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"]); fav = side * (bars[j].close - pos["index_entry"])
            pos["mfe_c"] = max(pos.get("mfe_c", 0.0), fav)
            ent = datetime.combine(bars[j].timestamp.date(), datetime.strptime(pos["entry_time"], "%H:%M").time(), tzinfo=IST); elapsed = (bars[j].timestamp - ent).total_seconds() / 60
            if elapsed >= self.noprog["bars"] * self.K and pos["mfe_c"] < self.noprog["r"] * risk and bars[j].timestamp.time() < T_EXIT:
                return "NOPROG"
        if self.ladder and self.ladder_wide:                              # J5: the ladder applies only on wide-open days (first 30 min range ≥ 95 pts); otherwise the fixed target stands
            f30 = [b for b in bars if b.timestamp.time() < dtime(9, 45)]
            if len(f30) >= 25 and (max(b.high for b in f30) - min(b.low for b in f30)) < 95:
                if pos.get("target") is None:
                    side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"]); pos["target"] = pos["index_entry"] + side * self.rr * risk
                return super().exit_check(pos, bars, j, tw)
        if self.ladder:                                                   # Part H (2026-10-21): R-ladder trail — at each whole R level from `start`, stop = (level − lag) R; the position runs until the ladder stop or 15:15
            side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"])
            ext = (bars[j].high if side > 0 else bars[j].low) if self.ladder.get("touch") else bars[j].close    # user spec (2026-10-21): a TOUCH of the level arms the step
            fav = side * (ext - pos["index_entry"])
            lvl = int(fav // risk) if risk > 0 else 0
            if lvl >= self.ladder["start"]:
                new_stop = pos["index_entry"] + side * ((lvl - self.ladder["lag"]) * risk - self.ladder.get("buffer", 0.0))   # stop = the level reached, minus a few points of room
                if side * (new_stop - pos["stop"]) > 0:
                    pos["stop"] = new_stop; pos["trailing"] = True; pos["ladder_lvl"] = lvl
            why = super().exit_check(pos, bars, j, tw)
            return f"LADDER{pos.get('ladder_lvl', 0)}R" if (why == "STOP" and pos.get("trailing")) else why
        if self.be_at_1r and not pos.get("be_done"):                      # V2: stop to break-even once 1 R is reached
            side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"])
            if side * (bars[j].close - pos["index_entry"]) >= risk:
                pos["stop"] = pos["index_entry"]; pos["be_done"] = True
        if self.be_by_level and not pos.get("be_done"):                   # Part D (2026-10-16): per-level risk-free rule — at `trigger` R move the stop to entry + `lock` R, keep the 1:3 target
            import re as _re
            m = _re.search(r"crossed (P|R1|R2|S1|S2)", pos.get("note", "")); lvl = m.group(1) if m else None
            rule = self.be_by_level.get(lvl)
            if rule:
                side = pos["side"]; risk = side * (pos["index_entry"] - pos["stop_at_entry"])
                if side * (bars[j].close - pos["index_entry"]) >= rule["trigger"] * risk:
                    pos["stop"] = pos["index_entry"] + side * rule["lock"] * risk; pos["be_done"] = True
        why = super().exit_check(pos, bars, j, tw)
        return "BE_STOP" if (why == "STOP" and pos.get("be_done")) else why


class PivotRejectBrain(Brain):
    """Skill 45: 5m touch-and-reject at R1/R2/S1/S2 (daily Fibonacci pivots). variant 'A' any close-back, 'B' close in the far half; target 'H' midpoint to the next level, 'F' the next level."""
    name = "REJECT"; K = 5

    def __init__(self, R, prev_bars, variant="A", target="H", itm_steps=0):
        super().__init__(R, prev_bars); self.variant = variant; self.tgt = target; self.strike_offset_steps = itm_steps
        self.name = f"REJECT{variant}{target}" + ("+ITM2" if itm_steps else "")
        self.levels = None
        if prev_bars:
            pH = max(b.high for b in prev_bars); pL = min(b.low for b in prev_bars); pC = prev_bars[-1].close; P = (pH + pL + pC) / 3; rng = pH - pL
            self.levels = {"P": P, "R1": P + 0.382 * rng, "R2": P + 0.618 * rng, "S1": P - 0.382 * rng, "S2": P - 0.618 * rng}

    def signals(self, bars, done):
        if not self.levels:
            return []
        bk = kbars(bars[:done], self.K); pos = {b.timestamp: i for i, b in enumerate(bars)}; last_done = bars[done - 1].timestamp if done else None; out = []; seen = set()
        L = self.levels; pairs = (("R1", -1, L["R1"], L["P"]), ("R2", -1, L["R2"], L["R1"]), ("S1", 1, L["S1"], L["P"]), ("S2", 1, L["S2"], L["S1"]))
        for i, b in enumerate(bk):
            end_min = b.timestamp + timedelta(minutes=self.K - 1)
            if last_done is None or end_min > last_done:
                continue
            for name, side, lvl, nxt in pairs:
                if (name, side) in seen:
                    continue
                touched = (b.high >= lvl and b.close < lvl) if side < 0 else (b.low <= lvl and b.close > lvl)
                if not touched:
                    continue
                if self.variant == "B":
                    mid = (b.high + b.low) / 2
                    if (side < 0 and b.close > mid) or (side > 0 and b.close < mid):
                        continue
                seen.add((name, side)); i1 = pos.get(end_min)
                if i1 is None:
                    continue
                e = bars[i1].close; stop = b.high if side < 0 else b.low; risk = side * (e - stop)
                if risk <= 0:
                    continue
                if self.tgt == "H":
                    target = (lvl + nxt) / 2
                elif self.tgt == "F":
                    target = nxt
                elif self.tgt == "N":
                    target = None                                           # stop only, 15:15 exit (measurement mode)
                elif self.tgt.startswith("R"):
                    target = e + side * float(self.tgt[1:]) * risk           # multiple of the initial risk
                elif self.tgt.startswith("P"):
                    target = e + side * float(self.tgt[1:])                  # fixed points
                else:
                    target = (lvl + nxt) / 2
                if target is not None and side * (target - e) < self.R:
                    continue
                out.append((i1, side, stop, target, f"rejected {name} {lvl:.0f}"))
        return out


class OpenDriveBrain(Brain):
    """Skill 46: opening drive. At the 09:44 close, if |close − open| ≥ 60 and the first-30-min range ≥ 100, buy in the drive direction. exit 'S1' 50/100 pts, 'S2' stop = 30-min midpoint & 1:2, 'S3' structural stop, no target."""
    name = "DRIVE"; first_entry = dtime(9, 44)

    def __init__(self, R, prev_bars, exit="S2", itm_steps=0, min_move=60.0, min_range=100.0):
        super().__init__(R, prev_bars); self.ex = exit; self.strike_offset_steps = itm_steps; self.min_move = min_move; self.min_range = min_range
        self.name = f"DRIVE{exit}" + ("+ITM2" if itm_steps else "")

    def signals(self, bars, done):
        b = bars[:done]
        if not b or b[0].timestamp.time() != dtime(9, 15):
            return []
        i = next((k for k, x in enumerate(b) if x.timestamp.time() == dtime(9, 44)), None)
        if i is None:
            return []
        first = b[:i + 1]; D = first[-1].close - first[0].open; R30 = max(x.high for x in first) - min(x.low for x in first)
        if abs(D) < self.min_move or R30 < self.min_range:
            return []
        side = 1 if D > 0 else -1; e = first[-1].close
        if self.ex == "S1":
            stop, target = e - side * 50.0, e + side * 100.0
        else:
            mid = (max(x.high for x in first) + min(x.low for x in first)) / 2; risk = side * (e - mid)
            if risk < self.R:
                return []
            stop = mid; target = None if self.ex == "S3" else e + side * 2 * risk
        return [(i, side, stop, target, f"opening drive {D:+.0f} pts, range {R30:.0f}")]


class RangeBrain(Brain):
    """Skill 47: mean reversion inside the day's structure. mode 'Q' = first-hour-range quarter reversal → midpoint (all days or gated); 'V' = VWAP stretch ≥ 40 → back to VWAP (gated days)."""
    name = "RANGE"; first_entry = dtime(10, 0)

    def __init__(self, R, prev_bars, mode="Q", gated=False, itm_steps=0, med30=95.0):
        super().__init__(R, prev_bars); self.mode = mode; self.gated = gated; self.strike_offset_steps = itm_steps; self.med30 = med30
        self.name = f"RANGE{mode}{'g' if gated else ''}" + ("+ITM2" if itm_steps else "")
        self.pdh = max(b.high for b in prev_bars) if prev_bars else None; self.pdl = min(b.low for b in prev_bars) if prev_bars else None
        self.last_sig = -10 ** 9

    def gate_ok(self, bars):
        if not self.gated:
            return True
        f30 = [b for b in bars if b.timestamp.time() < dtime(9, 45)]
        if len(f30) < 25 or self.pdh is None:
            return False
        r30 = max(b.high for b in f30) - min(b.low for b in f30); o = bars[0].open
        return r30 <= self.med30 and self.pdl <= o <= self.pdh

    def signals(self, bars, done):
        b = bars[:done]
        if len(b) < 61 or not self.gate_ok(b):
            return []
        out = []; i = done - 1; x = b[i]; t = x.timestamp.time()
        if not (dtime(10, 15) <= t <= dtime(14, 30)) or i - self.last_sig < 30:
            return []
        if self.mode == "Q":
            first = [y for y in b if y.timestamp.time() < dtime(10, 15)]; orh = max(y.high for y in first); orl = min(y.low for y in first); w = orh - orl; mid = (orh + orl) / 2
            if w < 40:
                return []
            if orl <= x.close <= orl + 0.25 * w and x.close > x.open and mid - x.close >= self.R:
                self.last_sig = i; out.append((i, 1, orl - 5, mid, f"lower quarter of OR {orl:.0f}–{orh:.0f}"))
            elif orh - 0.25 * w <= x.close <= orh and x.close < x.open and x.close - mid >= self.R:
                self.last_sig = i; out.append((i, -1, orh + 5, mid, f"upper quarter of OR {orl:.0f}–{orh:.0f}"))
        else:
            tw = session_twap(b); dev = x.close - tw[i]
            if abs(dev) >= 40:
                side = -1 if dev > 0 else 1; self.last_sig = i; out.append((i, side, x.close - side * 25, tw[i], f"VWAP stretch {dev:+.0f}"))
        return out

    def exit_check(self, pos, bars, j, tw):
        if self.mode == "V":
            pos["target"] = tw[j]                                          # target follows VWAP
        return super().exit_check(pos, bars, j, tw)


class ExpiryBrain(Brain):
    """Skill 48: on NIFTY weekly-expiry days, at the 13:00 close buy the option on the VWAP side if |close − VWAP| ≥ 15; stop 25 pts, no target, exit 15:15."""
    name = "EXPIRY"; first_entry = dtime(13, 0)

    def __init__(self, R, prev_bars, itm_steps=0, dev_min=15.0, stop_pts=25.0):
        super().__init__(R, prev_bars); self.strike_offset_steps = itm_steps; self.dev_min = dev_min; self.stop_pts = stop_pts
        self.name = "EXPIRY" + ("+ITM2" if itm_steps else ""); self.fired = False

    def signals(self, bars, done):
        if self.fired or done < 2:
            return []
        b = bars[:done]; x = b[-1]
        if x.timestamp.strftime("%H:%M") != "13:00":
            return []
        from engine.session.labels import expiry_flags
        if not expiry_flags(x.timestamp.date()).get("nifty_weekly_expiry"):
            return []
        tw = session_twap(b); dev = x.close - tw[-1]
        if abs(dev) < self.dev_min:
            return []
        self.fired = True; side = 1 if dev > 0 else -1
        return [(done - 1, side, x.close - side * self.stop_pts, None, f"expiry 13:00 VWAP side {dev:+.0f}")]


def history_sessions(d, n):
    """Up to n prior sessions' 1m bars (oldest first) from the local store, for brains that need multi-day statistics."""
    by = {}
    for c in load_1m("NIFTY"):
        if c.timestamp.date() < d:
            by.setdefault(c.timestamp.date(), []).append(c)
    return [by[k] for k in sorted(by)[-n:]]


NOISE_CHECKS = ("09:44", "10:14", "10:44", "11:14", "11:44", "12:14", "12:44", "13:14", "13:44", "14:14", "14:44")   # completed bar ending at the half-hour


class NoiseBandBrain(Brain):
    """Skill 49 A (FK-49): Zarattini & Aziz 'Beat the Market' noise band. At each 30-min checkpoint buy CE above open±σ band / PE below; trail = max(VWAP, band); checks only at checkpoints; exit 15:15."""
    name = "NOISE"; first_entry = dtime(9, 44)

    def __init__(self, R, prev_bars, hist, lookback=14, itm_steps=0):
        super().__init__(R, prev_bars); self.L = lookback; self.strike_offset_steps = itm_steps; self.name = f"NOISE{lookback}" + ("+ITM2" if itm_steps else "")
        self.sigma = {}
        use = hist[-lookback:]
        for k in NOISE_CHECKS:
            vals = []
            for sess in use:
                o = sess[0].open; c = next((b.close for b in sess if b.timestamp.strftime("%H:%M") == k), None)
                if c is not None and o:
                    vals.append(abs(c / o - 1))
            self.sigma[k] = sum(vals) / len(vals) if vals else None
        self.prev_close = prev_bars[-1].close if prev_bars else None

    def bands(self, bars, k):
        s = self.sigma.get(k)
        if s is None or self.prev_close is None:
            return None
        o = bars[0].open; return (max(o, self.prev_close) * (1 + s), min(o, self.prev_close) * (1 - s))

    def signals(self, bars, done):
        i = done - 1; x = bars[i]; k = x.timestamp.strftime("%H:%M")
        if k not in NOISE_CHECKS:
            return []
        bd = self.bands(bars, k)
        if not bd:
            return []
        up, lo = bd; tw = session_twap(bars[:done])
        if x.close > up:
            return [(i, 1, max(tw[i], up), None, f"above noise band {up:.0f} (σ {self.sigma[k]*100:.2f}%)")]
        if x.close < lo:
            return [(i, -1, min(tw[i], lo), None, f"below noise band {lo:.0f} (σ {self.sigma[k]*100:.2f}%)")]
        return []

    def exit_check(self, pos, bars, j, tw):
        b = bars[j]; k = b.timestamp.strftime("%H:%M")
        if b.timestamp.time() >= T_EXIT:
            return "TIME"
        if k not in NOISE_CHECKS:
            return None
        bd = self.bands(bars, k)
        if bd:
            up, lo = bd; pos["stop"] = max(tw[j], up) if pos["side"] > 0 else min(tw[j], lo)
        if pos["side"] * (b.close - pos["stop"]) <= 0:
            return "STOP"
        return None


class ORB30Brain(Brain):
    """Skill 49 B (FK-50): 30-minute opening range (09:15–09:44), optional width filter OR ≥ 0.6 × ATR20, first 5m close beyond the range → CE/PE, stop opposite extreme, target 1.5 R, time exit 14:30, one trade/day."""
    name = "ORB30"; first_entry = dtime(9, 49)

    def __init__(self, R, prev_bars, hist, filtered=True, itm_steps=0, rr=1.5):
        super().__init__(R, prev_bars); self.filtered = filtered; self.rr = rr; self.strike_offset_steps = itm_steps; self.fired = False
        self.name = "ORB30" + ("f" if filtered else "") + ("+ITM2" if itm_steps else "")
        trs = []; pc = None
        for sess in hist[-21:]:
            h = max(b.high for b in sess); l = min(b.low for b in sess); c = sess[-1].close
            if pc is not None:
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            pc = c
        self.atr20 = sum(trs[-20:]) / len(trs[-20:]) if trs else None

    def signals(self, bars, done):
        if self.fired or done < 35:
            return []
        i = done - 1; x = bars[i]; t = x.timestamp.time()
        if x.timestamp.minute % 5 != 4 or t < dtime(9, 49) or t > dtime(14, 0):      # completed 5m bar = 1m bar ending at :04/:09/…
            return []
        orb = [b for b in bars[:done] if b.timestamp.time() < dtime(9, 45)]
        if len(orb) < 25:
            return []
        hi = max(b.high for b in orb); lo = min(b.low for b in orb); w = hi - lo
        if self.filtered and (self.atr20 is None or w < 0.6 * self.atr20):
            self.fired = True; return []                                                 # day does not qualify
        if x.close > hi:
            self.fired = True; e = x.close; return [(i, 1, lo, e + self.rr * (e - lo), f"5m close above 30-min OR {lo:.0f}–{hi:.0f} (w {w:.0f}, ATR20 {self.atr20 or 0:.0f})")]
        if x.close < lo:
            self.fired = True; e = x.close; return [(i, -1, hi, e - self.rr * (hi - e), f"5m close below 30-min OR {lo:.0f}–{hi:.0f} (w {w:.0f}, ATR20 {self.atr20 or 0:.0f})")]
        return []

    def exit_check(self, pos, bars, j, tw):
        if bars[j].timestamp.time() >= dtime(14, 30):
            return "TIME"
        return super().exit_check(pos, bars, j, tw)


class ManualBrain(Brain):
    """LIVE-mode test hook: opens ONE position only when the user writes paper/live/COMMAND_<date>.json via tools/live_manual.py
    (leg, stop and target in index points, strikes in the money). Exits use the same close-based stop/target logic as every brain. Never fires on its own."""
    name = "MANUAL"; first_entry = dtime(9, 16)

    def __init__(self, R, prev_bars, cmd_path):
        super().__init__(R, prev_bars); self.cmd_path = Path(cmd_path); self.strike_offset_steps = 0; self.used = False

    def signals(self, bars, done):
        if not self.cmd_path.exists() or done < 1:                     # one signal per command file; a failed order does not block the next command
            return []
        try:
            c = json.loads(self.cmd_path.read_text())
        except Exception:
            return []
        self.cmd_path.unlink()
        if c.get("action") == "exit":                                       # exit command with no position: ignore
            return []
        side = 1 if str(c.get("leg", "CE")).upper() == "CE" else -1; sl = float(c.get("sl", 15)); tp = float(c.get("tp", 15)); self.strike_offset_steps = int(c.get("itm", 2))
        self.prem_sl = float(c["sl_prem"]) if c.get("sl_prem") else None; self.prem_tp = float(c["tp_prem"]) if c.get("tp_prem") else None   # rupee distances on the premium for the broker legs
        i = done - 1; e = bars[i].close
        return [(i, side, e - side * sl, e + side * tp, f"MANUAL TEST (sl {sl:g} / tp {tp:g} pts, itm {self.strike_offset_steps})")]

    def exit_check(self, pos, bars, j, tw):
        """Normal close-based stop/target, plus an immediate exit when the user writes {"action": "exit"} (tools/live_manual.py --exit)."""
        if self.cmd_path.exists():
            try:
                if json.loads(self.cmd_path.read_text()).get("action") == "exit":
                    self.cmd_path.unlink(); return "MANUAL_EXIT"
            except Exception:
                pass
        return super().exit_check(pos, bars, j, tw)


# ---------------- Paper account ----------------
class Paper:
    def __init__(self, d, brains, out_dir, broker=None, start_capital=None):
        self.d = d; self.brains = brains; self.lot = lot_on("NIFTY", d); self.dir = out_dir; self.broker = broker; self.tag = "LIVE" if broker is not None else "PAPER"; self.super_orders = False
        self.capital = float(start_capital) if start_capital else CAPITAL   # LIVE: the account's available funds at the start of the session (from Dhan), not the config constant
        self.state_p = out_dir / f"STATE_{d}.json"; self.trades_p = out_dir / f"TRADES_{d}.csv"
        self.state = {"equity": self.capital, "start_capital": self.capital, "realised": 0.0, "halted": False, "brains": {b.name: {"entries": 0, "open": None, "seen": [], "realised": 0.0, "trades": []} for b in brains}}
        if self.state_p.exists():
            self.state = json.loads(self.state_p.read_text()); print(f"resumed: equity ₹{self.state['equity']:,.0f}")
            for b in brains:
                self.state["brains"].setdefault(b.name, {"entries": 0, "open": None, "seen": [], "realised": 0.0, "trades": []})
        if not self.trades_p.exists():
            with open(self.trades_p, "w", newline="") as fh:
                csv.writer(fh).writerow(["brain", "entry_time", "leg", "strike", "lot", "index_entry", "stop_at_entry", "target", "opt_entry", "exit_time", "exit_reason", "index_exit", "opt_exit", "index_move_pts", "gross_inr", "cost_inr", "net_inr", "equity_after", "note"])

    def save(self):
        self.state_p.write_text(json.dumps(self.state, indent=1, default=str))

    def say(self, msg):
        print(f"{datetime.now(IST).strftime('%H:%M:%S')}  {msg}", flush=True)

    def process(self, bars, atm, done):
        tw = session_twap(bars)
        for brain in self.brains:
            st_ = self.state["brains"][brain.name]; pos = st_["open"]
            if pos and pos.get("super") and self.broker is not None:    # LIVE super order: Dhan executes the exit legs; we book whichever leg traded, or time-exit at 15:15
                try:
                    stt = self.broker.super_status(pos["order_id"]); now_k = datetime.now(IST).strftime("%H:%M")
                    for nm, why in (("target", "TARGET"), ("stop", "STOP")):
                        leg = stt.get(nm) or {}
                        if str(leg.get("orderStatus", "")).upper() == "TRADED":
                            sold = float(leg.get("averageTradedPrice") or 0) or self.broker.fill_price(pos["sid"], "SELL") or float(leg.get("price") or 0) or pos.get("last_px") or pos["p_in"]
                            lvl = float(leg.get("price") or 0)                 # a "stop" that traded well above its level (or a "target" well below) was not the leg firing on price: label it so the ledger does not lie
                            if lvl and ((why == "STOP" and sold > lvl * 1.04) or (why == "TARGET" and sold < lvl * 0.96)):
                                why = "BROKER_EXIT"; tg_send(f"⚠️ LIVE {self.d}: {pos['leg']} {pos['strike']:.0f} closed by the broker at ₹{sold:.2f}, not at the {nm} leg (₹{lvl:.2f}) — manual exit from the Dhan app or a leg replaced outside the trader?")
                            self.close(brain, pos, now_k, why, bars[-1].close if bars else pos["index_entry"], None, f"broker leg {nm} traded at ₹{sold:.2f} (leg ₹{lvl:.2f})", broker_fill=sold); pos = None; break
                    if pos and datetime.now(IST).time() >= T_EXIT:
                        self.say(f"[{brain.name}] {now_k} time exit: cancelling broker legs {self.broker.super_cancel_exits(pos['order_id'])}")
                        self.close(brain, pos, now_k, "TIME", bars[-1].close if bars else pos["index_entry"], option_price(atm, bars[-1].timestamp.strftime("%H:%M"), pos["leg"], pos["strike"]) if bars else None, "time exit at market"); pos = None
                    elif pos and bars:
                        pxn = option_price(atm, bars[-1].timestamp.strftime("%H:%M"), pos["leg"], pos["strike"])
                        if pxn is not None:
                            pos["last_px"] = pxn
                except Exception as ex:
                    self.say(f"[{brain.name}] super-order status check failed: {str(ex)[:120]}")
                continue
            if pos:                                                   # manage the open position on completed bars after entry
                for j in range(done):
                    b = bars[j]; k = b.timestamp.strftime("%H:%M")
                    if k <= pos["entry_time"] or k in pos["checked"]:
                        continue
                    pos["checked"].append(k); why = brain.exit_check(pos, bars, j, tw); px = option_price(atm, k, pos["leg"], pos["strike"])
                    if px is not None:
                        pos["last_px"] = px; pos["last_k"] = k
                    if why:
                        if pos.get("fill_index") is not None and why in ("STOP", "TARGET"):
                            b_close_idx = pos.pop("fill_index")
                        else:
                            b_close_idx = b.close
                        if px is None:                                                     # strike left the book: use the intrinsic-value floor if it is above the last known price
                            intrinsic = max(0.0, (b.close - pos["strike"]) if pos["leg"] == "CE" else (pos["strike"] - b.close)); px_use = max(pos.get("last_px") or 0.0, intrinsic)
                            self.close(brain, pos, k, why, b_close_idx, px_use, f"strike off book; priced at max(last {pos.get('last_k')}, intrinsic {intrinsic:.1f})")
                        else:
                            self.close(brain, pos, k, why, b_close_idx, px, "")
                        break
            # every signal is marked seen whether or not it can be acted on; only a signal on the just-completed bar may open a position (never a stale one)
            fresh = []
            for sig in brain.signals(bars, done):
                k = bars[sig[0]].timestamp.strftime("%H:%M")
                if k in st_["seen"]:
                    continue
                st_["seen"].append(k)
                if sig[0] >= done - 2:
                    fresh.append(sig)
                else:
                    self.say(f"[{brain.name}] {k} signal noted but stale (bar {done - 1 - sig[0]} bars old) — not traded")
            if self.state["halted"]:
                continue
            if st_["open"]:
                for sig in fresh:
                    self.say(f"[{brain.name}] {bars[sig[0]].timestamp.strftime('%H:%M')} signal — skipped (position open)")
                continue
            for i, side, stop, target, note in fresh:
                k = bars[i].timestamp.strftime("%H:%M"); t = bars[i].timestamp.time()
                if not (brain.first_entry <= t <= LAST_ENTRY):
                    continue
                leg = "CE" if side > 0 else "PE"; row = atm.get(k, {}); K = row.get("strike"); e = bars[i].close
                if abs(e - stop) > MAX_RISK_PTS:                              # risk rule: a stop wider than this risks more than the day stop on one ITM-2 lot (six months: 5 such trades, 1 win, −₹13,972; 2026-10-21 feed artefact 157 pts)
                    self.say(f"[{brain.name}] {k} signal {leg} — refused: stop {abs(e - stop):.0f} pts from entry exceeds the {MAX_RISK_PTS:g}-pt risk limit (≈ day stop on one lot)"); tg_send(f"⚠️ {self.tag} {self.d}: {brain.name} {leg} signal refused — stop {abs(e - stop):.0f} pts exceeds the {MAX_RISK_PTS:g}-pt risk limit (one lot would risk ≈ ₹{abs(e - stop) * 0.7 * self.lot:,.0f}); if the feed had gaps the gap guard reports it separately"); continue
                steps = getattr(brain, "strike_offset_steps", 0)
                if K is not None and steps:
                    K = K - side * steps * 50.0                             # in the money: CE below ATM, PE above
                px = row.get("book", {}).get((leg, K)) if (K is not None and steps) else row.get(leg)
                if st_["entries"] >= MAX_ENTRIES:
                    self.say(f"[{brain.name}] {k} signal {leg} — skipped (max entries)"); continue
                if px is None or K is None:
                    self.say(f"[{brain.name}] {k} signal {leg} — skipped (no option price yet)"); st_["seen"].remove(k); continue
                p_in = px + SLIP
                cap = getattr(brain, "premium_cap", 15_000.0)               # ₹15,000 per lot (restored 2026-10-21 after the cap backtest); 0 = no cap
                if cap and p_in * self.lot > cap:
                    self.say(f"[{brain.name}] {k} signal {leg} {K:.0f} ₹{p_in:.2f} — skipped (too expensive)"); continue
                sid = order_id = None; super_legs = None
                if self.broker is not None:                                   # LIVE: real order, fill at the traded price
                    if not isinstance(brain, (PivotBrain, ManualBrain)):
                        self.say(f"[{brain.name}] {k} signal {leg} — refused: live orders are allowed for the PIVOT brain (and the user's manual test hook) only"); continue
                    if datetime.now(IST).time() > LAST_ENTRY:
                        self.say(f"[{brain.name}] {k} signal {leg} — skipped (live clock past last entry)"); continue
                    try:
                        sid = self.broker.resolve(K, leg)
                        if self.super_orders and target is not None:          # SUPER ORDER: Dhan holds the target and stop legs on the option premium and executes them tick by tick
                            delta = getattr(brain, "live_delta", None) or LIVE_DELTA.get(steps, 0.5)
                            d_stop = abs(e - stop); d_tgt = abs(target - e)
                            sl_prem = getattr(brain, "prem_sl", None); tp_prem = getattr(brain, "prem_tp", None)
                            fresh = self.broker.ltp(sid) or px                   # never trust the feed snapshot for the leg prices: ask Dhan for the LTP now
                            ref = fresh + SLIP; sl_d = sl_prem if sl_prem else delta * d_stop; tp_d = tp_prem if tp_prem else delta * d_tgt
                            stop_px = max(0.05, ref - sl_d); tgt_px = ref + tp_d
                            fill = self.broker.super_buy(sid, self.lot, fresh, tgt_px, stop_px, f"{brain.name[:5]}-{k.replace(':', '')}-S")
                            p_in = fill["price"] if fill["price"] > 0 else ref; order_id = fill["order_id"]; super_legs = {"target": fill["target"], "stop": fill["stop"], "delta": delta}
                            if fill["price"] > 0 and abs(fill["price"] - ref) >= 0.5:    # re-base both legs on the actual fill so the distances are what was asked for
                                try:
                                    self.broker.modify_super_leg(order_id, "STOP_LOSS_LEG", max(0.05, fill["price"] - sl_d)); self.broker.modify_super_leg(order_id, "TARGET_LEG", fill["price"] + tp_d)
                                    super_legs = {"target": round(fill["price"] + tp_d, 2), "stop": round(max(0.05, fill["price"] - sl_d), 2), "delta": delta}
                                except Exception as ex:
                                    self.say(f"[{brain.name}] {k} could not re-base the broker legs on the fill: {str(ex)[:120]}")
                        else:
                            fill = self.broker.buy(sid, self.lot, px, f"{brain.name[:5]}-{k.replace(':', '')}-B")
                            p_in = fill["price"] if fill["price"] > 0 else p_in; order_id = fill["order_id"]
                    except Exception as ex:
                        self.say(f"[{brain.name}] {k} LIVE BUY FAILED {leg} {K:.0f}: {str(ex)[:160]}"); tg_send(f"🚨 LIVE BUY FAILED {self.d} {leg} {K:.0f}: {str(ex)[:160]}"); continue
                st_["open"] = {"entry_time": k, "side": side, "leg": leg, "strike": K, "index_entry": e, "stop": stop, "stop_at_entry": stop, "target": target, "p_in": p_in, "checked": [], "last_px": px, "last_k": k, "note": note, "sid": sid, "order_id": order_id, "super": super_legs}
                if super_legs:
                    note = f"{note} · broker legs: target ₹{super_legs['target']:.2f} / stop ₹{super_legs['stop']:.2f} (delta {super_legs['delta']:.2f})"; st_["open"]["note"] = note
                st_["entries"] += 1; self.save()
                msg = f"[{brain.name}] {k} ENTRY BUY {leg} {K:.0f} × {self.lot} @ ₹{p_in:.2f} (₹{p_in * self.lot:,.0f}) index {e:.1f} stop {stop:.1f} target {('%.1f' % target) if target is not None else 'trail'} {note} · entries {st_['entries']}/{MAX_ENTRIES}{(' · order ' + order_id) if order_id else ''}{(' · broker legs target ₹%.2f / stop ₹%.2f' % (super_legs['target'], super_legs['stop'])) if super_legs else ''}"
                self.say(msg); tg_send(f"{'🟢' if self.tag == 'PAPER' else '💰'} {self.tag} ENTRY {self.d}\n{msg}", public=True); break

    def close(self, brain, pos, k, why, idx_px, opt_px, note, broker_fill=None):
        p_out = (opt_px if opt_px is not None else pos["p_in"]) - SLIP; lot = self.lot
        if broker_fill is not None:                                           # exit already executed by Dhan (super-order leg): book the traded price
            p_out = float(broker_fill)
        elif self.broker is not None and pos.get("sid"):                        # LIVE: real sell; if it fails the position stays open and the user is alerted
            fill = None
            for attempt in range(3):
                try:
                    fill = self.broker.sell(pos["sid"], lot, opt_px if opt_px is not None else pos.get("last_px") or pos["p_in"], f"{brain.name[:5]}-{k.replace(':', '')}-S"); break
                except Exception as ex:
                    self.say(f"[{brain.name}] {k} LIVE SELL attempt {attempt + 1} failed: {str(ex)[:160]}"); time.sleep(2)
            if fill is None:
                tg_send(f"🚨🚨 LIVE SELL FAILED {self.d} {pos['leg']} {pos['strike']:.0f} × {lot} — EXIT MANUALLY ON DHAN NOW (reason {why})"); self.say("LIVE SELL FAILED — position left open, manual exit required"); return
            p_out = fill["price"] if fill["price"] > 0 else p_out; note = (note + f" order {fill['order_id']}").strip()
        cost = leg_cost(lot, pos["p_in"], p_out) * lot; gross = (p_out - pos["p_in"]) * lot; net = gross - cost
        st_ = self.state["brains"][brain.name]; st_["realised"] += net; self.state["realised"] += net; self.state["equity"] += net; mv = pos["side"] * (idx_px - pos["index_entry"])
        row = [brain.name, pos["entry_time"], pos["leg"], f"{pos['strike']:.0f}", lot, f"{pos['index_entry']:.2f}", f"{pos['stop_at_entry']:.2f}", (f"{pos['target']:.2f}" if pos['target'] is not None else "trail"), f"{pos['p_in']:.2f}", k, why, f"{idx_px:.2f}", f"{p_out:.2f}", f"{mv:+.2f}", f"{gross:+.0f}", f"{cost:.0f}", f"{net:+.0f}", f"{self.state['equity']:.0f}", (pos.get("note", "") + " " + note).strip()]
        with open(self.trades_p, "a", newline="") as fh:
            csv.writer(fh).writerow(row)
        st_["trades"].append(dict(zip(["brain", "entry_time", "leg", "strike", "lot", "index_entry", "stop_at_entry", "target", "p_in", "exit_time", "why", "index_exit", "p_out", "move", "gross", "cost", "net", "equity", "note"], row))); st_["open"] = None
        if hasattr(brain, "on_exit"):
            brain.on_exit(pos, why, k)
        msg = f"[{brain.name}] {k} EXIT {why} SELL {pos['leg']} {pos['strike']:.0f} @ ₹{p_out:.2f} index {idx_px:.1f} ({mv:+.1f} pts) net ₹{net:+,.0f} (cost ₹{cost:.0f}) · brain ₹{st_['realised']:+,.0f} · account ₹{self.state['equity']:,.0f} {note}"
        self.say(msg); tg_send(f"{'🔴' if net < 0 else '🔵'} {self.tag} EXIT {why} {self.d}\n{msg}", public=True)
        if self.state["realised"] <= DAY_STOP and not self.state["halted"]:
            self.state["halted"] = True; m2 = f"DAY STOP reached (₹{self.state['realised']:+,.0f}): no further entries from any brain today"; self.say(m2); tg_send(f"⛔ {m2}", public=True)
        self.save()

    def force_close_all(self, bars, atm, note):
        k = bars[-1].timestamp.strftime("%H:%M")
        for brain in self.brains:
            pos = self.state["brains"][brain.name]["open"]
            if pos:
                self.close(brain, pos, k, "TIME", bars[-1].close, option_price(atm, k, pos["leg"], pos["strike"]) or pos.get("last_px"), note)

    def report(self):
        lines = [f"# {'LIVE' if self.broker is not None else 'Paper'} session — NIFTY — {self.d} — brains {', '.join(b.name for b in self.brains)}{' (PIVOT 1:' + format(next(b.rr for b in self.brains if hasattr(b, 'rr')), 'g') + ')' if any(hasattr(b, 'rr') for b in self.brains) else ''} — lot {self.lot}", "",
                 f"Account: start ₹{self.state.get('start_capital', self.capital):,.0f}{' (Dhan available funds at start)' if self.broker is not None else ''} → end ₹{self.state['equity']:,.0f} (net ₹{self.state['realised']:+,.0f}); day stop {'HIT' if self.state['halted'] else 'not hit'}.", ""]
        base = CAPITAL
        if self.broker is not None:                                        # LIVE running capital: from the first live session's recorded starting funds
            bp = self.dir / "CAPITAL_BASE.json"
            if not bp.exists():
                bp.write_text(json.dumps({"start_capital": self.capital, "date": self.d.isoformat()}))
            base = float(json.loads(bp.read_text()).get("start_capital", self.capital))
        run = base; run_by = {}
        for f in sorted(self.dir.glob("TRADES_*.csv")):                       # running capital across every session in this ledger (each session's own P&L starts from ₹50,000)
            try:
                for r in csv.DictReader(open(f, newline="")):
                    v = float(r["net_inr"]); run += v; run_by[r["brain"].split("+")[0]] = run_by.get(r["brain"].split("+")[0], 0.0) + v
            except Exception:
                pass
        lines += [f"Running capital since the first session: ₹{base:,.0f} → ₹{run:,.0f} ({run - base:+,.0f}){(' · Dhan available funds now ₹%s' % format(self.broker.funds(), ',.0f')) if self.broker is not None else ''}; by brain: " + ", ".join(f"{k} ₹{v:+,.0f}" for k, v in run_by.items()), ""]
        tg = [f"📋 {self.tag} SESSION {self.d} · today ₹{self.state['realised']:+,.0f} · running capital ₹{run:,.0f} ({run - base:+,.0f} since start)"]
        for brain in self.brains:
            st_ = self.state["brains"][brain.name]; tr = st_["trades"]; wins = sum(1 for t in tr if float(t["net"]) > 0); stops = sum(1 for t in tr if t["why"] == "STOP")
            lines += [f"## Brain {brain.name}: {len(tr)} trades, {wins} wins, {stops} stops, net ₹{st_['realised']:+,.0f}, entries {st_['entries']}/{MAX_ENTRIES}", "", "| entry | leg | strike | opt in | exit | reason | opt out | index move | net ₹ | note |", "|---|---|---|---|---|---|---|---|---|---|"]
            lines += [f"| {t['entry_time']} | {t['leg']} | {t['strike']} | {t['p_in']} | {t['exit_time']} | {t['why']} | {t['p_out']} | {t['move']} | {t['net']} | {t['note']} |" for t in tr] + [""]
            tg.append(f"{brain.name}: {len(tr)} trades, {wins} wins, net ₹{st_['realised']:+,.0f}" + "".join(f"\n  {t['entry_time']} {t['leg']} {t['strike']} {t['p_in']}→{t['p_out']} {t['why']} ₹{t['net']}" for t in tr))
        (self.dir / f"SESSION_{self.d}.md").write_text("\n".join(lines) + "\n"); print("\n".join(lines))
        if self.dir.name != "replay":
            tg_send("\n".join(tg), public=True)
        if self.dir.name not in ("replay", "live", "live_dryrun"):                          # paper session → rebuild the trading journal (the live ledger is kept separately in paper/live)
            try:
                import subprocess; subprocess.run([sys.executable, str(ROOT / "tools" / "journal.py")], cwd=str(ROOT), timeout=300); print("journal rebuilt", flush=True)
            except Exception as e:
                print(f"journal rebuild failed: {str(e)[:80]}", flush=True); tg_send(f"⚠️ journal rebuild failed: {str(e)[:80]}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=date.today().isoformat()); ap.add_argument("--r", type=float, default=7.0, help="VWAP brain reference range (points)"); ap.add_argument("--rr", type=float, default=3.0, help="PIVOT brain target multiple")
    ap.add_argument("--brains", default="VWAP,PIVOT"); ap.add_argument("--poll", type=int, default=60); ap.add_argument("--replay", action="store_true")
    ap.add_argument("--pivot-tf", type=int, default=5, help="PIVOT brain chart timeframe in minutes (5 = tested default; 1 and 3 were weaker in Skill 44)")
    ap.add_argument("--pivot-itm", type=int, default=0, help="PIVOT brain: buy this many strikes in the money (0 = ATM; 2 = V1)"); ap.add_argument("--pivot-cap", type=float, default=15000.0, help="premium cap per lot in ₹ for every brain; 0 = no cap. ₹15,000 is the tested rule (removing it turned +₹32.9k into −₹1.6k on six months: expensive premiums mark high-volatility days where close-based stops overrun)")
    ap.add_argument("--log", default=None, help="append all output to this file as well")
    ap.add_argument("--feed", action="store_true", help="use the live WebSocket feed snapshot (tools/dhan_feed.py must be running) instead of polling the historical API; acts within seconds of a bar close and fills at the live option LTP")
    ap.add_argument("--telegram-setup", metavar="BOT_TOKEN"); ap.add_argument("--telegram-test", action="store_true", help="send a test message to the private chat"); ap.add_argument("--telegram-test-channel", action="store_true", help="send a test message to the channel only"); ap.add_argument("--telegram-channel", metavar="CHANNEL", help="@name or -100… id of a channel the bot administers; public signals are posted there too ('off' to remove)")
    ap.add_argument("--live", action="store_true", help="LIVE MODE: place real 1-lot market orders on Dhan for every entry/exit (ledger in paper/live/). Requires --i-accept-live-risk.")
    ap.add_argument("--i-accept-live-risk", action="store_true", help="required with --live"); ap.add_argument("--no-super", action="store_true", help="with --live: plain market orders and our own close-based exits instead of Dhan super orders (broker-side target/stop legs)"); ap.add_argument("--dry-run", action="store_true", help="with --live: run the live code path with synthetic fills and no orders")
    a = ap.parse_args(); d = date.fromisoformat(a.date)
    if a.live and not a.i_accept_live_risk and not a.dry_run:
        sys.exit("refusing to start live mode without --i-accept-live-risk")
    out = ROOT / "paper" / ("live_dryrun" if (a.live and a.dry_run) else ("replay" if a.replay else ("live" if a.live else ""))); out.mkdir(parents=True, exist_ok=True)   # dry runs never touch the live ledger
    if a.log:
        class _Tee:
            def __init__(self, path): self.f = open(path, "a"); self.o = sys.stdout
            def write(self, x): self.o.write(x); self.f.write(x); self.f.flush()
            def flush(self): self.o.flush(); self.f.flush()
        sys.stdout = _Tee(a.log)
    if a.telegram_setup:
        tg_setup(a.telegram_setup); return
    if a.telegram_channel is not None:
        tg_channel(a.telegram_channel); return
    if a.telegram_test:
        print("sent to private chat" if tg_send(f"TradingAlgo test alert {datetime.now(IST).strftime('%H:%M:%S')}") else "not sent (configure with --telegram-setup BOT_TOKEN)"); return
    if a.telegram_test_channel:
        tok, _, chan = tg_config()
        if not tok or not chan:
            sys.exit("no channel configured (add one with --telegram-channel @name)")
        try:
            print(f"sent to channel {chan}" if _tg_post(tok, chan, f"TradingAlgo channel test {datetime.now(IST).strftime('%H:%M:%S')} — signals will appear here") else f"not sent to channel {chan}")
        except Exception as e:
            sys.exit(f"channel send failed: {str(e)[:160]} — is the bot still an admin with 'Post messages'?")
        return
    if a.replay:
        for f in (out / f"STATE_{d}.json", out / f"TRADES_{d}.csv"):
            if f.exists():
                f.unlink()
    prev = prev_session_bars(d); piv = daily_pivots(kbars(prev + [], 5))[-1] if prev else None
    brains = []
    for nm in a.brains.split(","):
        if nm.strip().upper() == "VWAP":
            brains.append(VwapBrain(a.r, prev))
        elif nm.strip().upper() == "PIVOT":
            pb = PivotBrain(a.r, prev, a.rr, itm_steps=a.pivot_itm, tf=a.pivot_tf)
            pb.premium_cap = a.pivot_cap
            brains.append(pb)
        elif nm.strip().upper() == "EXPIRY":
            eb = ExpiryBrain(a.r, prev, itm_steps=2); eb.premium_cap = a.pivot_cap; brains.append(eb)   # Skill 48 admitted cell: ITM-2, cap ₹15,000, fires only on NIFTY weekly-expiry days at 13:00
    broker = None
    if a.live:
        from live_broker import DhanBroker, preflight
        ok, msg = preflight(d, a.dry_run)
        print(("preflight OK: " if ok else "preflight BLOCKED: ") + msg, flush=True)
        if not ok:
            tg_send(f"⛔ LIVE start blocked {d}: {msg}"); sys.exit(1)
        broker = DhanBroker(d, dry_run=a.dry_run)
        if a.replay:
            for f in (out / f"STATE_{d}.json", out / f"TRADES_{d}.csv"):
                if f.exists():
                    f.unlink()
    start_cap = None
    if broker is not None and not a.dry_run:
        try:
            start_cap = broker.funds(); print(f"live capital from Dhan: available funds ₹{start_cap:,.0f}", flush=True)
        except Exception as ex:
            print(f"could not read funds from Dhan ({str(ex)[:80]}); using the state file or ₹{CAPITAL:,.0f}", flush=True)
    P = Paper(d, brains, out, broker=broker, start_capital=start_cap); tg_on = tg_config()[0] is not None
    if a.live:
        P.super_orders = not a.no_super
        print(f"live exits: {'Dhan SUPER ORDERS — target and stop legs held by the broker on the premium, executed on every tick' if P.super_orders else 'our own close-based index exits with market sells'}", flush=True)
    print(f"{'LIVE TRADING — REAL ORDERS' if (a.live and not a.dry_run) else ('LIVE DRY RUN (no orders)' if a.live else 'PAPER TRADING (no orders)')} · NIFTY · {d} · brains {[b.name for b in brains]} · R={a.r:g} · PIVOT 1:{a.rr:g} · lot {P.lot} · {'REPLAY' if a.replay else 'SESSION'} · telegram {'ON' if tg_on else 'off'}", flush=True)
    if a.live and (len(brains) != 1 or not isinstance(brains[0], PivotBrain)):
        print(f"live mode is restricted to the PIVOT brain alone (got {[b.name for b in brains]}); EXPIRY, VWAP and every other setup stay paper-only", flush=True); sys.exit(1)
    if a.live:
        mb = ManualBrain(a.r, prev, out / f"COMMAND_{d}.json"); mb.premium_cap = a.pivot_cap; brains.append(mb)        # P.brains is this same list
        P.state["brains"].setdefault(mb.name, {"entries": 0, "open": None, "seen": [], "realised": 0.0, "trades": []})
        print("manual test hook armed: a position opens only if you run tools/live_manual.py (writes " + str(out / f"COMMAND_{d}.json") + ")", flush=True)
    if prev:
        pv = daily_pivots(kbars(prev, 5))
        print(f"previous session {prev[0].timestamp.date()}: H {max(b.high for b in prev):.1f} L {min(b.low for b in prev):.1f} C {prev[-1].close:.1f}", flush=True)
    if not a.replay:
        tg_send(f"▶️ {'LIVE trader started — REAL ORDERS' if (a.live and not a.dry_run) else 'Paper trader started'} {d} · brains {', '.join(b.name for b in brains)} · R={a.r:g} · PIVOT 1:{a.rr:g} · equity ₹{P.state['equity']:,.0f}", public=True)
    if a.replay:
        bars = bars_for(d); atm = atm_for(d)
        for done in range(1, len(bars) + 1):
            P.process(bars, atm, done)
        P.force_close_all(bars, atm, "forced at last bar"); P.report(); return
    feed_p = ROOT / "paper" / f"FEED_{d}.json"; atm_feed = {}; last_done = None          # the feed snapshot is shared by the paper and live processes
    while True:
        if a.feed:
            try:
                j = json.loads(feed_p.read_text()); age = (datetime.now(IST) - datetime.fromisoformat(j["written"])).total_seconds()
                if age > 30:
                    P.say(f"feed snapshot is {int(age)} s old — is tools/dhan_feed.py running?"); time.sleep(5); continue
                try:                                                             # the file can be fresh while the socket is dead: warn when the last index tick is old
                    tick_age = (datetime.now(IST) - datetime.combine(d, datetime.strptime(j["ltt"], "%H:%M:%S").time(), tzinfo=IST)).total_seconds() if j.get("ltt") else 0
                    if tick_age > 90 and dtime(9, 16) <= datetime.now(IST).time() <= dtime(15, 29) and int(time.time()) % 60 < 6:
                        P.say(f"WARNING feed ticks are {int(tick_age)} s old (last tick {j['ltt']}) — bars may be missing; the feed daemon backfills gaps every 60 s")
                except Exception:
                    pass
                bars = [Candle(datetime.combine(d, datetime.strptime(t, "%H:%M").time(), tzinfo=IST), o, h, l, c, M1) for t, o, h, l, c in j["bars"]]
                if len(bars) >= 2:                                               # gap guard: if any minute in the last 15 is missing, wait for the feed's backfill rather than signal on broken candles
                    have = {b.timestamp.strftime("%H:%M") for b in bars}; last = bars[-1].timestamp; gaps = [(last - timedelta(minutes=i)).strftime("%H:%M") for i in range(1, 15) if (last - timedelta(minutes=i)).time() >= dtime(9, 15) and (last - timedelta(minutes=i)).strftime("%H:%M") not in have]
                    if gaps:
                        if int(time.time()) % 30 < 6:
                            P.say(f"feed has missing minutes {gaps[:5]} — waiting for the gap backfill before acting on signals")
                        time.sleep(5); continue
                if j.get("forming"):
                    t, o, h, l, c = j["forming"]; bars.append(Candle(datetime.combine(d, datetime.strptime(t, "%H:%M").time(), tzinfo=IST), o, h, l, c, M1))
                # option book: per completed minute from the feed's minute snapshots; the current minute from live LTPs (= realistic fill at detection time)
                for m, strikes in j.get("options_minute", {}).items():
                    atm_feed.setdefault(m, {"book": {}, "strike": None})
                    for k, v in strikes.items():
                        for leg, px in v.items():
                            atm_feed[m]["book"][(leg, float(k))] = px
                cur_m = bars[-1].timestamp.strftime("%H:%M") if bars else None
                if cur_m and j.get("options"):
                    row = {"book": {}, "strike": j.get("atm")}
                    for k, v in j["options"].items():
                        for leg, px in v.items():
                            row["book"][(leg, float(k))] = px
                    row["CE"] = row["book"].get(("CE", j.get("atm"))); row["PE"] = row["book"].get(("PE", j.get("atm"))); atm_feed[cur_m] = row
                    if len(bars) > 1:                                                 # the just-completed bar is priced at the live LTP when first seen (fill at detection), else keep its minute snapshot
                        km = bars[-2].timestamp.strftime("%H:%M")
                        if km not in atm_feed or not atm_feed[km].get("strike"):
                            atm_feed[km] = dict(row)
                for m, row in atm_feed.items():
                    if row.get("strike") is None:
                        row["strike"] = j.get("atm")
                    if row["strike"] is not None:
                        row.setdefault("CE", row["book"].get(("CE", row["strike"]))); row.setdefault("PE", row["book"].get(("PE", row["strike"])))
                atm = atm_feed
            except Exception as e:
                P.say(f"feed read error: {str(e)[:120]}"); time.sleep(5); continue
        else:
            try:
                bars = bars_for(d); atm = atm_for(d)
            except Exception as e:
                P.say(f"fetch error: {str(e)[:120]}"); time.sleep(a.poll); continue
        if bars:
            try:                                                           # live snapshot for the dashboard chart (bars + latest ATM quote)
                lastk = bars[-1].timestamp.strftime("%H:%M"); row = atm.get(lastk) or atm.get(bars[-2].timestamp.strftime("%H:%M")) if len(bars) > 1 else atm.get(lastk)
                (((ROOT / "paper") if not a.live else out) / f"LIVE_{d}.json").write_text(json.dumps({"written": datetime.now(IST).isoformat(timespec="seconds"), "date": d.isoformat(), "bars": [[b.timestamp.strftime("%H:%M"), b.open, b.high, b.low, b.close] for b in bars],
                                                               "atm": {"strike": row.get("strike"), "CE": row.get("CE"), "PE": row.get("PE")} if row else None}))
            except Exception as e:
                P.say(f"snapshot write failed: {str(e)[:80]}")
            P.process(bars, atm, len(bars) - 1); tw = session_twap(bars)
            if a.feed and last_done == bars[-1].timestamp:
                continue
            last_done = bars[-1].timestamp
            opens = "; ".join(f"{b.name}: {st['open']['leg']} {int(st['open']['strike'])} @ {st['open']['p_in']:.2f} last {st['open'].get('last_px')}" for b in brains for st in [P.state['brains'][b.name]] if st["open"]) or "none"
            P.say(f"bar {bars[-1].timestamp.strftime('%H:%M')} index {bars[-1].close:.1f} VWAP {tw[-1]:.1f} · open: {opens} · equity ₹{P.state['equity']:,.0f}")
            if bars[-1].timestamp.time() >= dtime(15, 29):
                P.force_close_all(bars, atm, "forced at session end"); P.report(); break
        time.sleep(5 if a.feed else a.poll)


if __name__ == "__main__":
    main()
