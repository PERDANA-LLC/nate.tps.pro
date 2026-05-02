#!/usr/bin/env python3
"""
Schwab token expiry watcher.
Checks refresh token expiry daily. Notifies Telegram + Discord
when the token will expire in ≤ 2 days. Only alerts once per cycle.
"""
import os, sys, json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ / "alert-bridge"))
from notifier import _telegram_send, _discord_send, _ts

REFRESH_WINDOW_DAYS = 7
WARN_THRESHOLD_DAYS = 2
STATE_FILE = PROJ / "logs" / "token-expiry-state.json"

def get_refresh_issued():
    """Read refresh_token_issued from schwab_tokens.db."""
    db = PROJ / "tokens" / "schwab_tokens.db"
    if not db.exists():
        print(f"[token-watch] DB not found: {db}")
        return None
    conn = sqlite3.connect(str(db))
    c = conn.cursor()
    c.execute("SELECT refresh_token_issued FROM schwabdev")
    row = c.fetchone()
    conn.close()
    if not row:
        print("[token-watch] No refresh_token_issued in DB")
        return None
    return datetime.fromisoformat(row[0])

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def main():
    issued = get_refresh_issued()
    if not issued:
        print("[token-watch] Cannot read token — skipping")
        return

    expires = issued + timedelta(days=REFRESH_WINDOW_DAYS)
    now = datetime.now(timezone.utc)
    remaining = expires - now
    days_left = remaining.total_seconds() / 86400

    state = load_state()
    last_warned_key = issued.isoformat()  # unique per token cycle

    print(f"[token-watch] Issued: {issued}, Expires: {expires}, Remaining: {days_left:.1f}d")

    if days_left <= WARN_THRESHOLD_DAYS and days_left > 0:
        if state.get("last_warned") == last_warned_key:
            print("[token-watch] Already warned for this cycle — skipping")
            return

        msg = (
            f"⚠️ **Schwab Token Expiry Warning**\n"
            f"Refresh token expires in **{days_left:.0f} day(s)**\n"
            f"Expires: {expires.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Re-auth required before then.\n"
            f"`{_ts()}`"
        )
        print(f"[token-watch] SENDING WARNING: {days_left:.0f} days left")
        _telegram_send(msg)
        _discord_send(msg)

        state["last_warned"] = last_warned_key
        save_state(state)

    elif days_left <= 0:
        if state.get("last_warned") != last_warned_key:
            msg = (
                f"🚨 **Schwab Token EXPIRED**\n"
                f"Refresh token expired {abs(days_left):.0f} day(s) ago.\n"
                f"Manual re-auth required.\n"
                f"`{_ts()}`"
            )
            _telegram_send(msg)
            _discord_send(msg)
            state["last_warned"] = last_warned_key
            save_state(state)
    else:
        # Token was refreshed (new issued date) — reset state
        if state.get("last_warned") != last_warned_key:
            state["last_warned"] = ""
            save_state(state)
            print("[token-watch] New token cycle detected — state reset")

if __name__ == "__main__":
    main()
