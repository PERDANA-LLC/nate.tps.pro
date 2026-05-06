#!/usr/bin/env python3
"""
Nate Token Manager — handles Schwab OAuth token lifecycle.

TWO JOBS:
  1. Access token refresh (every 30 min, automatic)
  2. Refresh token expiry monitoring (7-day cycle, notifications)

Schwab does NOT rotate refresh tokens on grant_type=refresh_token.
After 7 days, a full browser OAuth is required. This script handles
everything else automatically and notifies when manual re-auth is needed.

Usage:
    source .env.trade
    python3 token_manager.py              # normal: refresh access token
    python3 token_manager.py --status     # show token health
    python3 token_manager.py --reauth     # run right after manual browser OAuth
    python3 token_manager.py --force      # force access token refresh

Cron (every 30 min):
    */30 * * * * cd /root/nate.tps.pro && source .env.trade && .venv/bin/python token_manager.py
"""

import base64
import json
import os
import sqlite3
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

PROJ_ROOT = Path(__file__).resolve().parent
TOKENS_DB = PROJ_ROOT / "tokens" / "schwab_tokens.db"
TOKEN_JSON = PROJ_ROOT / "schwab-auth" / "token.json"
LOG_FILE = PROJ_ROOT / "logs" / "token_manager.log"


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_secrets():
    """Load Schwab credentials from .env and shared notifier secrets."""
    load_dotenv(PROJ_ROOT / ".env")
    if (PROJ_ROOT / ".env.trade").exists():
        load_dotenv(PROJ_ROOT / ".env.trade", override=True)

    for k in ["SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET"]:
        if not os.getenv(k):
            raise EnvironmentError(f"{k} not set")

    # Load shared notifier
    shared = Path.home() / ".hermes" / ".env"
    if shared.exists():
        load_dotenv(shared, override=False)


def get_tokens_from_db():
    """Read token state from schwab_tokens.db."""
    db = sqlite3.connect(str(TOKENS_DB))
    try:
        row = db.execute(
            "SELECT access_token_issued, refresh_token_issued, access_token, refresh_token FROM schwabdev"
        ).fetchone()
        if not row:
            return None
        return {
            "at_issued": datetime.fromisoformat(row[0]),
            "rt_issued": datetime.fromisoformat(row[1]),
            "access_token": row[2],
            "refresh_token": row[3],
        }
    finally:
        db.close()


def refresh_access_token(refresh_token: str) -> dict | None:
    """Call Schwab's /oauth/token with grant_type=refresh_token.
    Returns token JSON dict or None on failure."""
    app_key = os.environ["SCHWAB_CLIENT_ID"]
    app_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    auth = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()

    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "--max-time", "30",
                "-X", "POST",
                "https://api.schwabapi.com/v1/oauth/token",
                "-H", f"Authorization: Basic {auth}",
                "-H", "Content-Type: application/x-www-form-urlencoded",
                "-d", f"grant_type=refresh_token&refresh_token={refresh_token}",
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        if result.returncode != 0:
            log(f"Curl failed: {result.stderr[:200]}")
            return None

        data = json.loads(result.stdout)
        if "access_token" not in data:
            log(f"Schwab error: {result.stdout[:300]}")
            return None
        return data
    except Exception as e:
        log(f"Token refresh exception: {e}")
        return None


def save_tokens(at_issued: datetime, rt_issued: datetime, data: dict):
    """Persist tokens to DB and token.json."""
    # DB
    db = sqlite3.connect(str(TOKENS_DB))
    try:
        db.execute("DELETE FROM schwabdev")
        db.execute(
            "INSERT INTO schwabdev (access_token_issued, refresh_token_issued, access_token, refresh_token, id_token, expires_in, token_type, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                at_issued.isoformat(),
                rt_issued.isoformat(),
                data.get("access_token", ""),
                data.get("refresh_token", ""),
                data.get("id_token", ""),
                data.get("expires_in", 1800),
                data.get("token_type", "Bearer"),
                data.get("scope", "api"),
            ),
        )
        db.commit()
    finally:
        db.close()
    # token.json (for schwab-py executor)
    token_json = {
        "access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", ""),
        "expires_in": data.get("expires_in", 1800),
        "token_type": data.get("token_type", "Bearer"),
        "scope": data.get("scope", "api"),
        "id_token": data.get("id_token", ""),
    }
    with open(TOKEN_JSON, "w") as f:
        json.dump(token_json, f)


def send_notification(message: str):
    """Send to Telegram + Discord via shared notifier."""
    try:
        shared = Path.home() / ".hermes" / "shared"
        sys.path.insert(0, str(shared))
        from notifier import _telegram_send, _discord_send
        _telegram_send(message)
        _discord_send(message)
    except Exception as e:
        log(f"Notify failed: {e}")


# ── status ────────────────────────────────────────────────────────
def cmd_status():
    load_secrets()
    tokens = get_tokens_from_db()
    if not tokens:
        log("No tokens in DB")
        return

    now = datetime.now(timezone.utc)
    at_age = now - tokens["at_issued"]
    rt_expires = tokens["rt_issued"] + timedelta(days=7)
    rt_remaining = rt_expires - now

    print(f"Access token: issued {tokens['at_issued'].strftime('%Y-%m-%d %H:%M:%S')} UTC ({at_age} ago)")
    print(f"  Expires in: {timedelta(seconds=1800) - at_age}")
    print(f"Refresh token: issued {tokens['rt_issued'].strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Expires: {rt_expires.strftime('%Y-%m-%d %H:%M:%S')} UTC ({rt_remaining} remaining)")

    if rt_remaining < timedelta(hours=2):
        print("⚠️  URGENT: Refresh token expires in < 2 hours!")
    elif rt_remaining < timedelta(days=1):
        print("⚠️  Refresh token expires within 24 hours")
    elif rt_remaining < timedelta(days=3):
        print("⚠️  Refresh token expires within 3 days")
    else:
        print("✓ Tokens healthy")


# ── refresh access token ───────────────────────────────────────────
def cmd_refresh(force: bool = False):
    load_secrets()
    tokens = get_tokens_from_db()
    if not tokens:
        log("FATAL: No tokens in DB — full OAuth re-auth required")
        send_notification("🚨 NATE: No Schwab tokens in DB — manual re-auth needed!")
        return False

    now = datetime.now(timezone.utc)
    at_age = (now - tokens["at_issued"]).total_seconds()

    # Only refresh if access token is older than 25 min (or forced)
    if not force and at_age < 25 * 60:
        log(f"Access token still fresh ({at_age:.0f}s old) — skipping")
        return True

    # Refresh
    log("Refreshing access token …")
    data = refresh_access_token(tokens["refresh_token"])
    if not data:
        log("Access token refresh FAILED")
        rt_expires = tokens["rt_issued"] + timedelta(days=7)
        rt_remaining = rt_expires - now
        if rt_remaining < timedelta(0):
            send_notification("🚨 NATE: Schwab refresh token EXPIRED — manual OAuth re-auth required!")
        else:
            send_notification(f"⚠️ NATE: Access token refresh failed. Refresh token expires in {rt_remaining}")
        return False

    # Save
    save_tokens(now, tokens["rt_issued"], data)
    log(f"Access token refreshed (expires in {data.get('expires_in')}s)")

    # Check refresh token health
    rt_expires = tokens["rt_issued"] + timedelta(days=7)
    rt_remaining = rt_expires - now

    if rt_remaining < timedelta(hours=1):
        send_notification("🚨 URGENT: Nate Schwab refresh token expires in < 1 hour! Re-auth NOW.")
    elif rt_remaining < timedelta(hours=24) and rt_remaining > timedelta(hours=23):
        send_notification(f"⚠️ Nate Schwab refresh token expires TOMORROW ({rt_expires.strftime('%b %d %H:%M')} UTC). Plan re-auth.")
    elif rt_remaining < timedelta(days=3) and rt_remaining > timedelta(days=2, hours=23):
        send_notification(f"📅 Nate Schwab refresh token expires in 3 days ({rt_expires.strftime('%b %d')}). Plan re-auth.")

    return True


# ── re-auth helper ─────────────────────────────────────────────────
def cmd_reauth():
    """Run this AFTER completing manual browser OAuth.
    Takes the callback URL from the user and saves tokens."""
    load_secrets()

    callback_url = input("Paste the full callback URL from your browser: ").strip()
    if not callback_url or "code=" not in callback_url:
        log("ERROR: Invalid callback URL (must contain ?code=...)")
        return

    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(callback_url)
    code = parse_qs(parsed.query).get("code", [None])[0]
    if not code:
        log("ERROR: No authorization code found in URL")
        return

    app_key = os.environ["SCHWAB_CLIENT_ID"]
    app_secret = os.environ["SCHWAB_CLIENT_SECRET"]
    callback = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
    auth = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()

    result = subprocess.run(
        [
            "curl", "-s", "--max-time", "30",
            "-X", "POST", "https://api.schwabapi.com/v1/oauth/token",
            "-H", f"Authorization: Basic {auth}",
            "-H", "Content-Type: application/x-www-form-urlencoded",
            "-d", f"grant_type=authorization_code&code={code}&redirect_uri={callback}",
        ],
        capture_output=True, text=True, timeout=35,
    )

    data = json.loads(result.stdout)
    if "access_token" not in data:
        log(f"Token exchange failed: {result.stdout[:300]}")
        return

    now = datetime.now(timezone.utc)
    save_tokens(now, now, data)
    log(f"✓ Re-auth complete! New refresh token issued: {now}")
    log(f"  Expires: {now + timedelta(days=7)}")
    send_notification("✅ Nate Schwab re-auth SUCCESS — tokens fresh for 7 days.")


# ── CLI ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(LOG_FILE.parent, exist_ok=True)

    if "--status" in sys.argv or "-s" in sys.argv:
        cmd_status()
    elif "--reauth" in sys.argv or "-r" in sys.argv:
        cmd_reauth()
    elif "--force" in sys.argv or "-f" in sys.argv:
        load_secrets()
        if cmd_refresh(force=True):
            log("✓ Forced access token refresh complete")
        else:
            sys.exit(1)
    else:
        load_secrets()
        if cmd_refresh():
            log("✓ Token manager: OK")
        else:
            sys.exit(1)
