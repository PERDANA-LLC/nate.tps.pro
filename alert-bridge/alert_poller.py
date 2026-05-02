#!/usr/bin/env python3
"""
Headless Playwright poller for DailyProfitsLive Trades tab.

Scrapes Nate Bear's trade cards using CSS class detection:
  - bg-green-400 / bg-green-700 → BTO
  - bg-red-400 / bg-red-700     → STC

Trade details are in card innerText (not images).
Posts new alerts to local webhook server.
"""
import re
import time
import json
import hashlib
import datetime
import requests
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.trade")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifier import _telegram_send, _discord_send

# ── Config ──────────────────────────────────────────────────────────
DPL_URL = "https://watch.dailyprofitslive.com/"
DPL_USER = os.environ.get("FEED_USER", "")
DPL_PASS = os.environ.get("FEED_PASS", "")
LOCAL_WEBHOOK = "http://127.0.0.1:8765/webhook"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "90"))

BASE_DIR = Path(__file__).resolve().parent.parent
SEEN_FILE = BASE_DIR / "trade-log" / "seen_hashes.txt"
SESSION_FILE = BASE_DIR / "schwab-auth" / "feed_session.json"
SEEN_FILE.parent.mkdir(exist_ok=True)
SESSION_FILE.parent.mkdir(exist_ok=True)

BROWSER_ARGS = [
    "--no-sandbox", "--disable-gpu",
    "--disable-dev-shm-usage", "--disable-setuid-sandbox",
]

# CSS classes for color detection
GREEN_CLASSES = ["bg-green-400", "bg-green-700", "bg-emerald-400", "bg-emerald-700"]
RED_CLASSES = ["bg-red-400", "bg-red-700", "bg-rose-400", "bg-rose-700"]


def load_seen() -> set:
    return set(SEEN_FILE.read_text().splitlines()) if SEEN_FILE.exists() else set()


def save_seen(seen: set):
    SEEN_FILE.write_text("\n".join(sorted(seen)))


def alert_hash(text: str) -> str:
    """Dedup by trade string within the same hour."""
    bucket = datetime.datetime.now().strftime("%Y%m%d%H")
    return hashlib.md5(f"{text.strip().upper()}{bucket}".encode()).hexdigest()


def detect_action(class_str: str) -> str | None:
    """Check CSS classes for green=BTO, red=STC."""
    for g in GREEN_CLASSES:
        if g in class_str:
            return "BTO"
    for r in RED_CLASSES:
        if r in class_str:
            return "STC"
    return None


def extract_trade_line(card_text: str) -> str | None:
    """
    Card text format:
      6:58 pm
      TWLO Calls
      bto (2) TWLO 5/15 $200 calls @ $2.05
      ...commentary...
    Extract the bto/stc line.
    """
    lines = card_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if re.match(r"(bto|stc)\s+", line, re.IGNORECASE):
            return line
    return None


def scrape_trades(page) -> list[dict]:
    """
    Navigate to DPL, click Trades tab, extract all trade cards.
    Returns list of {raw, card_action}.
    """
    # Navigate to DPL
    page.goto(DPL_URL, wait_until="domcontentloaded", timeout=15000)
    time.sleep(3)

    # Click Trades tab (page defaults to Chat)
    try:
        trades_btn = page.query_selector("button:has-text('Trades')")
        if trades_btn:
            trades_btn.click()
            time.sleep(2)
    except Exception:
        pass

    results = []

    # Find all trade cards by CSS class
    for selector in [
        "[class*='bg-green-']",
        "[class*='bg-red-']",
        "[class*='bg-emerald-']",
        "[class*='bg-rose-']",
    ]:
        cards = page.query_selector_all(selector)
        if not cards:
            continue

        for card in cards:
            try:
                class_str = card.get_attribute("class") or ""
                action = detect_action(class_str)
                if not action:
                    continue

                card_text = card.inner_text().strip()
                trade_line = extract_trade_line(card_text)
                if not trade_line:
                    continue

                # Check for correction indicators
                text_lower = card_text.lower()
                is_revised = "revised" in text_lower
                is_correction = is_revised or any(
                    kw in text_lower for kw in [
                        "correction:", "corrected:", "update:", "updated:",
                        "amended:", "modified:", "changed:", "edited:",
                        "revision:", "adjustment:", "adjusted:",
                    ]
                )

                results.append({
                    "raw": trade_line,
                    "card_action": action,
                    "is_correction": is_correction,
                })
            except Exception:
                continue

        if results:
            break

    return results


def is_market_hours() -> bool:
    """Mon-Fri, 9:00am-5:00pm EST (14:00-22:00 UTC)."""
    now = datetime.datetime.utcnow()
    if now.weekday() >= 5:
        return False
    hour = now.hour + now.minute / 60.0
    return 14.0 <= hour <= 22.0


def notify_status(msg: str):
    """Send status notification to Telegram + Discord."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    full = f"🤖 <b>Project Nate Alert Service</b>\n{now} EST\n{msg}"
    _telegram_send(full)
    _discord_send(full.replace("<b>", "**").replace("</b>", "**"))


def main():
    if not DPL_USER or not DPL_PASS:
        print("[poller] FATAL: FEED_USER / FEED_PASS not set")
        sys.exit(1)

    seen = load_seen()
    print(f"[{datetime.datetime.now()}] Poller starting")
    print(f"[{datetime.datetime.now()}] DPL: {DPL_URL}")
    print(f"[{datetime.datetime.now()}] Interval: {POLL_INTERVAL}s")
    print(f"[{datetime.datetime.now()}] Market hours only: Mon-Fri 9a-5p EST")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = browser.new_context(
            storage_state=str(SESSION_FILE) if SESSION_FILE.exists() else None
        )

        # ── Login ───────────────────────────────────────────────────
        login_page = context.new_page()
        login_page.goto(f"{DPL_URL}login", wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)

        # Clerk auth: click "Sign in" button to open modal
        if login_page.query_selector("button:has-text('Sign in')"):
            print("[poller] Opening Clerk auth modal...")
            login_page.click("button:has-text('Sign in')")
            time.sleep(2)

        if login_page.query_selector("#email"):
            print("[poller] Logging in...")
            try:
                login_page.fill("#email", DPL_USER)
                login_page.fill("#password", DPL_PASS)
                login_page.click("button[type=submit]")
                time.sleep(4)
                if "login" not in login_page.url.lower():
                    context.storage_state(path=str(SESSION_FILE))
                    print("[poller] ✅ Logged in")
                else:
                    print("[poller] ⚠️ Login may have failed")
            except Exception as e:
                print(f"[poller] ⚠️ Login error: {e}")
        else:
            print("[poller] Session reused (already logged in)")
        login_page.close()

        # ── Startup notification ─────────────────────────────────────
        in_hours = is_market_hours()
        notify_status("🟢 Service started" + (" — monitoring" if in_hours else " — waiting for 9am EST"))

        # ── Poll loop ───────────────────────────────────────────────
        while True:
            try:
                now_in_hours = is_market_hours()

                # Market hours transition detection
                if now_in_hours and not in_hours:
                    notify_status("🟢 Market open — monitoring DPL trades")
                elif in_hours and not now_in_hours:
                    notify_status("🔴 Market closed — see you tomorrow")

                in_hours = now_in_hours

                if not now_in_hours:
                    time.sleep(POLL_INTERVAL)
                    continue

                page = context.new_page()
                alerts = scrape_trades(page)
                page.close()

                for alert in alerts:
                    raw = alert["raw"]
                    card_action = alert["card_action"]
                    is_correction = alert.get("is_correction", False)
                    h = alert_hash(raw)
                    if h not in seen or is_correction:
                        if h not in seen:
                            seen.add(h)
                        save_seen(seen)
                        label = f"CORRECTED [{card_action}]" if is_correction else f"NEW [{card_action}]"
                        print(f"[{datetime.datetime.now()}] 🚨 {label}: {raw[:100]}")
                        try:
                            resp = requests.post(
                                LOCAL_WEBHOOK,
                                json={
                                    "raw_alert": raw,
                                    "card_action": card_action,
                                    "is_correction": is_correction,
                                },
                                timeout=5,
                            )
                            print(f"  ↳ webhook: {resp.status_code}")
                        except Exception as e:
                            print(f"  ↳ webhook error: {e}")

            except Exception as e:
                print(f"[{datetime.datetime.now()}] Poll error: {e}")

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
