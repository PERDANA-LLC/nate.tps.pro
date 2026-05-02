#!/usr/bin/env python3
"""
Shared notification module for trade alert system.
Sends to Telegram + Discord (configurable via NOTIFY_TELEGRAM / NOTIFY_DISCORD).

Telegram: uses bot token + chat IDs (supports groups, channels, DMs).
Discord:  uses bot token + channel ID (HTTP API).
"""
import os
import json
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.trade")

# ── Telegram ────────────────────────────────────────────────────────
TELEGRAM_ENABLED = os.environ.get("NOTIFY_TELEGRAM", "false").lower() == "true"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_IDS = [
    c.strip()
    for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",")
    if c.strip()
]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Discord ─────────────────────────────────────────────────────────
DISCORD_ENABLED = os.environ.get("NOTIFY_DISCORD", "false").lower() == "true"
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
DISCORD_API = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"


def _telegram_send(text: str):
    """Send to all configured Telegram chat IDs."""
    if not TELEGRAM_ENABLED or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        return
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            resp = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML"},
                timeout=10,
            )
            if not resp.ok:
                print(f"[notifier] Telegram error -> {chat_id}: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"[notifier] Telegram exception -> {chat_id}: {e}")


def _discord_send(text: str):
    """Send to the configured Discord channel."""
    if not DISCORD_ENABLED or not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        return
    try:
        resp = requests.post(
            DISCORD_API,
            json={"content": text[:2000]},
            headers={"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"},
            timeout=10,
        )
        if not resp.ok:
            print(f"[notifier] Discord error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[notifier] Discord exception: {e}")


# ── Public API ──────────────────────────────────────────────────────

def _ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S EST")


def notify_alert(raw_alert: str, card_action: str):
    """Called when a new alert is detected (before execution)."""
    ts = _ts()
    msg = f"🔔 <b>ALERT RECEIVED — {card_action}</b>\n<code>{raw_alert[:500]}</code>\n<code>{ts}</code>"
    _telegram_send(msg)
    _discord_send(msg.replace("<b>", "**").replace("</b>", "**").replace("<code>", "`").replace("</code>", "`"))


def notify_execution(msg: str, subject: str = "TRADE EXECUTED"):
    """Called after a trade is placed (paper or live)."""
    ts = _ts()
    telegram_msg = f"✅ <b>{subject}</b>\n{msg[:3800]}\n<code>{ts}</code>"
    _telegram_send(telegram_msg)
    discord_msg = f"✅ **{subject}**\n{msg[:1900]}\n`{ts}`"
    _discord_send(discord_msg)


def notify_error(msg: str):
    """Called when something fails (circuit breaker, API error, etc.)."""
    ts = _ts()
    telegram_msg = f"🚨 <b>TRADE ERROR</b>\n{msg[:4000]}\n<code>{ts}</code>"
    _telegram_send(telegram_msg)
    discord_msg = f"🚨 **TRADE ERROR**\n{msg[:1950]}\n`{ts}`"
    _discord_send(discord_msg)


def notify_correction(ticker: str, trade_line: str):
    """Called when a revised/corrected alert is detected."""
    ts = _ts()
    telegram_msg = f"📝 <b>CORRECTION — {ticker}</b>\n<code>{trade_line[:500]}</code>\n⚠️ No trade executed\n<code>{ts}</code>"
    _telegram_send(telegram_msg)
    discord_msg = f"📝 **CORRECTION — {ticker}**\n`{trade_line[:500]}`\n⚠️ No trade executed\n`{ts}`"
    _discord_send(discord_msg)
