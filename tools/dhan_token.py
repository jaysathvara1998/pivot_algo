#!/usr/bin/env python3
"""Obtain, renew or verify a Dhan access token (stdlib only). The token is written to ~/.dhan/access_token (mode 600), never into this repository.

Methods (Dhan v2 authentication docs, 2026-10):
  totp    POST https://auth.dhan.co/app/generateAccessToken?dhanClientId=..&pin=..&totp=..   → 24-hour token. Needs DHAN_CLIENT_ID, DHAN_PIN and either DHAN_TOTP_SECRET
          (base32 secret from the authenticator setup; the code is computed here, RFC 6238) or --totp CODE typed from the authenticator app.
  renew   GET  https://api.dhan.co/v2/RenewToken  (headers access-token, dhanClientId)         → fresh 24-hour token from a still-active one.
  oauth   12-month token via API key/secret: 1) POST https://auth.dhan.co/app/generate-consent?client_id=..  2) open the printed login URL, log in; the redirect URL
          you configured on web.dhan.co receives ?tokenId=..  3) paste the tokenId → GET https://auth.dhan.co/app/consumeApp-consent?tokenId=..  Needs DHAN_API_KEY, DHAN_API_SECRET.
  verify  GET  https://api.dhan.co/v2/profile → tokenValidity and segments.
  setup   store client id / PIN / TOTP secret (and optional API key+secret) in ~/.dhan/credentials.json, mode 600, prompted without echo.
  auto    verify the saved token; renew it if < 6 h remain; if invalid, generate a new one via TOTP; optional --then-update runs tools/daily_update.py --fetch.
  install-launchd  macOS LaunchAgent that runs `auto` at 08:30 and 20:30 daily (--times to change; --then-update to fetch data after the evening run).

Usage:
  DHAN_CLIENT_ID=1113788351 DHAN_PIN=123456 DHAN_TOTP_SECRET=BASE32SECRET python3 tools/dhan_token.py totp
  DHAN_CLIENT_ID=1113788351 DHAN_PIN=123456 python3 tools/dhan_token.py totp --totp 482913
  python3 tools/dhan_token.py renew
  DHAN_API_KEY=.. DHAN_API_SECRET=.. DHAN_CLIENT_ID=.. python3 tools/dhan_token.py oauth
  python3 tools/dhan_token.py verify
Then: export DHAN_ACCESS_TOKEN=$(cat ~/.dhan/access_token)   (tools/daily_update.py also reads that file automatically when the variable is unset)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".dhan" / "access_token"


def totp_code(secret_b32: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """RFC 6238 TOTP (SHA-1), as used by Dhan's authenticator setup."""
    key = base64.b32decode(secret_b32.strip().replace(" ", "").upper() + "=" * (-len(secret_b32.strip().replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    msg = struct.pack(">Q", counter); h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F; code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _call(method: str, url: str, headers: dict | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {url.split('?')[0]} → HTTP {e.code}: {e.read().decode()[:300]}")
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f"{method} {url.split('?')[0]} → {e}")


class ApiError(RuntimeError):
    pass


CRED_FILE = Path.home() / ".dhan" / "credentials.json"
LOG_FILE = Path.home() / ".dhan" / "token.log"


def creds() -> dict:
    """Credentials from the environment, else ~/.dhan/credentials.json (mode 600, written by `setup`)."""
    c = json.loads(CRED_FILE.read_text()) if CRED_FILE.exists() else {}
    for k in ("DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET", "DHAN_API_KEY", "DHAN_API_SECRET"):
        if os.environ.get(k):
            c[k] = os.environ[k]
    return c


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    with open(LOG_FILE, "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    print(msg)


def save(token: str) -> None:
    TOKEN_FILE.parent.mkdir(mode=0o700, exist_ok=True); TOKEN_FILE.write_text(token); os.chmod(TOKEN_FILE, 0o600)
    print(f"token saved to {TOKEN_FILE} (mode 600)\nexport DHAN_ACCESS_TOKEN=$(cat {TOKEN_FILE})")


def load() -> str:
    tok = os.environ.get("DHAN_ACCESS_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    return tok or sys.exit("no token: set DHAN_ACCESS_TOKEN or run totp / oauth first")


def need(name: str) -> str:
    return creds().get(name) or sys.exit(f"{name} not set (environment or ~/.dhan/credentials.json via `setup`)")


def cmd_totp(a):
    cid, pin = need("DHAN_CLIENT_ID"), need("DHAN_PIN")
    code = a.totp or totp_code(need("DHAN_TOTP_SECRET"))
    q = urllib.parse.urlencode({"dhanClientId": cid, "pin": pin, "totp": code})
    r = _call("POST", f"https://auth.dhan.co/app/generateAccessToken?{q}")
    tok = r.get("accessToken") or sys.exit(f"no accessToken in response: {r}")
    print(f"client {r.get('dhanClientId')} {r.get('dhanClientName', '')} · expires {r.get('expiryTime')}"); save(tok)


def cmd_renew(a):
    tok = load(); cid = need("DHAN_CLIENT_ID")
    r = _call("GET", "https://api.dhan.co/v2/RenewToken", headers={"access-token": tok, "dhanClientId": cid})
    new = r.get("accessToken") or sys.exit(f"renewal failed: {r}")
    print(f"renewed · expires {r.get('expiryTime')}"); save(new)


def cmd_oauth(a):
    key, sec, cid = need("DHAN_API_KEY"), need("DHAN_API_SECRET"), need("DHAN_CLIENT_ID")
    h = {"app_id": key, "app_secret": sec}
    if not a.token_id:
        r = _call("POST", f"https://auth.dhan.co/app/generate-consent?client_id={cid}", headers=h)
        consent = r.get("consentAppId") or sys.exit(f"no consentAppId: {r}")
        print("1) open this URL, log in with your Dhan credentials and 2FA:")
        print(f"   https://auth.dhan.co/login/consentApp-login?consentAppId={consent}")
        print("2) you will be redirected to your configured Redirect URL with ?tokenId=...  Re-run:")
        print(f"   DHAN_API_KEY=.. DHAN_API_SECRET=.. DHAN_CLIENT_ID={cid} python3 tools/dhan_token.py oauth --token-id <tokenId>")
        return
    r = _call("GET", f"https://auth.dhan.co/app/consumeApp-consent?tokenId={urllib.parse.quote(a.token_id)}", headers=h)
    tok = r.get("accessToken") or sys.exit(f"no accessToken: {r}")
    print(f"12-month token issued · expires {r.get('expiryTime')}"); save(tok)


def cmd_verify(a):
    r = _call("GET", "https://api.dhan.co/v2/profile", headers={"access-token": load()})
    print(json.dumps({k: r.get(k) for k in ("dhanClientId", "tokenValidity", "activeSegment", "dataPlan", "dataValidity")}, indent=1))


def cmd_setup(a):
    """Store client id, PIN and TOTP secret (and optionally API key/secret) in ~/.dhan/credentials.json, mode 600, prompted without echo."""
    import getpass
    c = json.loads(CRED_FILE.read_text()) if CRED_FILE.exists() else {}
    if a.from_env:
        for k in ("DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET", "DHAN_API_KEY", "DHAN_API_SECRET"):
            if os.environ.get(k):
                c[k] = os.environ[k].strip()
        if not (c.get("DHAN_CLIENT_ID") and c.get("DHAN_PIN")):
            sys.exit("--from-env needs at least DHAN_CLIENT_ID and DHAN_PIN in the environment")
    elif not sys.stdin.isatty():
        sys.exit("setup needs an interactive terminal for its prompts (run it in Terminal.app), or pass values with --from-env (values already stored in ~/.dhan/credentials.json are kept, so only the missing one is needed):\n"
                 "  DHAN_PIN=... python3 tools/dhan_token.py setup --from-env\n"
                 "  DHAN_CLIENT_ID=... DHAN_PIN=... DHAN_TOTP_SECRET=... python3 tools/dhan_token.py setup --from-env")
    for k, prompt, secret in (() if a.from_env else (("DHAN_CLIENT_ID", "Dhan client ID", False), ("DHAN_PIN", "Dhan PIN", True), ("DHAN_TOTP_SECRET", "TOTP base32 secret (from 'Enable TOTP' on web.dhan.co → DhanHQ APIs; blank to skip)", True),
                              ("DHAN_API_KEY", "API key (optional, for the 12-month OAuth token; blank to skip)", True), ("DHAN_API_SECRET", "API secret (optional; blank to skip)", True))):
        cur = " [set]" if c.get(k) else ""
        v = (getpass.getpass if secret else input)(f"{prompt}{cur}: ").strip()
        if v:
            c[k] = v
    CRED_FILE.parent.mkdir(mode=0o700, exist_ok=True); CRED_FILE.write_text(json.dumps(c)); os.chmod(CRED_FILE, 0o600)
    if c.get("DHAN_TOTP_SECRET"):
        print(f"current TOTP code from the stored secret: {totp_code(c['DHAN_TOTP_SECRET'])}  (should match your authenticator app)")
    print(f"saved {CRED_FILE} (mode 600). Test with: python3 tools/dhan_token.py auto")


def _validity_hours(tok: str) -> float | None:
    """Hours until the token's JWT exp claim, or None if unreadable."""
    try:
        payload = tok.split(".")[1]; payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return (exp - time.time()) / 3600 if exp else None
    except Exception:
        return None


def cmd_auto(a):
    """Keep a valid token on disk: verify → renew if it expires within --renew-within hours → else generate via TOTP. Exit 0 if a valid token is on disk afterwards."""
    c = creds(); cid = c.get("DHAN_CLIENT_ID")
    tok = os.environ.get("DHAN_ACCESS_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    status = "none"
    if tok:
        try:
            _call("GET", "https://api.dhan.co/v2/profile", headers={"access-token": tok}); status = "valid"
        except ApiError as e:
            status = "invalid"; log(f"auto: current token invalid ({str(e)[:80]})")
    hrs = _validity_hours(tok) if status == "valid" else None
    if status == "valid" and (hrs is None or hrs > a.renew_within):
        log(f"auto: token valid, {hrs:.1f} h left — nothing to do" if hrs is not None else "auto: token valid — nothing to do")
    elif status == "valid" and cid:
        try:
            r = _call("GET", "https://api.dhan.co/v2/RenewToken", headers={"access-token": tok, "dhanClientId": cid}); new = r.get("accessToken")
            if new:
                save(new); log(f"auto: renewed ({hrs:.1f} h were left) · expires {r.get('expiryTime')}")
            else:
                raise ApiError(f"renew returned {r}")
        except ApiError as e:
            log(f"auto: renew failed ({str(e)[:80]}); trying TOTP"); status = "invalid"
    if status != "valid" or (hrs is not None and hrs <= a.renew_within and not TOKEN_FILE.exists()):
        if not (cid and c.get("DHAN_PIN") and c.get("DHAN_TOTP_SECRET")):
            log("auto: cannot generate — TOTP credentials not configured. Run `python3 tools/dhan_token.py setup` once (needs 'Enable TOTP' on web.dhan.co → DhanHQ APIs), or generate a token on web.dhan.co and save it.")
            sys.exit(2)
        try:
            q = urllib.parse.urlencode({"dhanClientId": cid, "pin": c["DHAN_PIN"], "totp": totp_code(c["DHAN_TOTP_SECRET"])})
            r = _call("POST", f"https://auth.dhan.co/app/generateAccessToken?{q}"); new = r.get("accessToken")
            if not new:
                raise ApiError(f"no accessToken: {r}")
            save(new); log(f"auto: generated via TOTP · expires {r.get('expiryTime')}")
        except ApiError as e:
            log(f"auto: TOTP generation failed ({str(e)[:120]})"); sys.exit(3)
    if a.then_update:
        import subprocess
        here = Path(__file__).resolve().parent
        log("auto: running tools/daily_update.py --fetch")
        rc = subprocess.call([sys.executable, str(here / "daily_update.py"), "--fetch"], cwd=str(here.parent))
        log(f"auto: daily_update exit {rc}")


def cmd_install_launchd(a):
    """Write and load a macOS LaunchAgent that runs `auto` at the given times each day (default 08:30 and 20:30) and after a successful token also fetches the day's data at 20:30."""
    here = Path(__file__).resolve(); label = "com.tradingalgo.dhan-token"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    cal = "".join(f"<dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>" for h, m in a.times)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{here}</string><string>auto</string><string>--renew-within</string><string>{a.renew_within}</string>{'<string>--then-update</string>' if a.then_update else ''}</array>
<key>StartCalendarInterval</key><array>{cal}</array>
<key>WorkingDirectory</key><string>{here.parent.parent}</string>
<key>StandardOutPath</key><string>{Path.home() / '.dhan' / 'launchd.out'}</string>
<key>StandardErrorPath</key><string>{Path.home() / '.dhan' / 'launchd.err'}</string>
<key>RunAtLoad</key><false/>
</dict></plist>"""
    plist.parent.mkdir(parents=True, exist_ok=True); plist.write_text(xml)
    import subprocess
    subprocess.call(["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL); rc = subprocess.call(["launchctl", "load", str(plist)])
    print(f"{'loaded' if rc == 0 else 'written (load failed, rc=' + str(rc) + ')'} {plist}\nruns `auto` at {', '.join(f'{h:02d}:{m:02d}' for h, m in a.times)} daily; log: {LOG_FILE}\nremove with: launchctl unload {plist} && rm {plist}")


def _hhmm(s):
    h, m = s.split(":"); return int(h), int(m)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("totp"); t.add_argument("--totp", help="6-digit code from the authenticator app (otherwise computed from DHAN_TOTP_SECRET)")
    sub.add_parser("renew"); o = sub.add_parser("oauth"); o.add_argument("--token-id"); sub.add_parser("verify"); sp = sub.add_parser("setup"); sp.add_argument("--from-env", action="store_true", help="take DHAN_CLIENT_ID / DHAN_PIN / DHAN_TOTP_SECRET (and optional API key/secret) from the environment instead of prompting")
    au = sub.add_parser("auto"); au.add_argument("--renew-within", type=float, default=6.0, help="renew when fewer than this many hours remain (default 6)"); au.add_argument("--then-update", action="store_true", help="run tools/daily_update.py --fetch after a valid token is on disk")
    il = sub.add_parser("install-launchd"); il.add_argument("--times", type=_hhmm, nargs="+", default=[(8, 30), (20, 30)], help="HH:MM times (default 08:30 20:30)"); il.add_argument("--renew-within", type=float, default=6.0); il.add_argument("--then-update", action="store_true")
    a = ap.parse_args()
    try:
        {"totp": cmd_totp, "renew": cmd_renew, "oauth": cmd_oauth, "verify": cmd_verify, "setup": cmd_setup, "auto": cmd_auto, "install-launchd": cmd_install_launchd}[a.cmd](a)
    except ApiError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
