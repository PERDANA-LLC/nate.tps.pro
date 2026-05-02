#!/usr/bin/env python3
"""
Headless Playwright poller. Monitors DailyProfitsLive for trade alerts.
Color-based detection: green=BTO, red=STC, blue=skip.
Posts new alerts to local webhook server.
"""
import re
import time
import json
import hashlib
import datetime
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
import sys

load_dotenv(Path(__file__).resolve().parent.parent / ".env.trade")

ALERT_SERVICE_URL = os.environ.get(
    "ALERT_SERVICE_URL",
    "https://watch.dailyprofitslive.com/?channel=trades"
)
LOCAL_WEBHOOK = "http://127.0.0.1:8765/alert"
POLL_INTERVAL = 90

BASE_DIR = Path(__file__).resolve().parent.parent
SEEN_FILE = BASE_DIR / "trade-log" / "seen_hashes.txt"
SEEN_FILE.parent.mkdir(exist_ok=True)

GREEN_COLORS = {
    "#1a5c2e", "#2d7a3e", "#1e7a34", "#166534",
    "rgb(22,101,52)", "green", "#155724", "#198754",
}
RED_COLORS = {
    "#7f1d1d", "#991b1b", "#b91c1c", "#dc2626",
    "rgb(185,28,28)", "red", "#842029", "#dc3545",
}


def load_seen():
    return set(SEEN_FILE.read_text().splitlines()) if SEEN_FILE.exists() else set()


def save_seen(seen):
    SEEN_FILE.write_text("\n".join(sorted(seen)))


def alert_hash(raw: str) -> str:
    bucket = (
        datetime.datetime.now().strftime("%Y%m%d%H")
        + str(int(datetime.datetime.now().minute / 5))
    )
    return hashlib.md5(f"{raw.strip().upper()}{bucket}".encode()).hexdigest()


def get_card_color(element) -> str:
    try:
        color = element.evaluate("el => window.getComputedStyle(el).backgroundColor")
        return (color or "").lower().replace(" ", "")
    except Exception:
        return ""


def color_to_action(color: str) -> str:
    for g in GREEN_COLORS:
        if g.replace(" ", "") in color:
            return "BTO"
    for r in RED_COLORS:
        if r.replace(" ", "") in color:
            return "STC"
    return "SKIP"


def scrape_alerts(page) -> list:
    page.goto(ALERT_SERVICE_URL, wait_until="networkidle", timeout=30000)
    results = []
    for selector in [
        ".alert-card", ".trade-card", ".signal-card",
        "[data-alert]", ".card", "article", "li",
    ]:
        try:
            cards = page.query_selector_all(selector)
            if not cards:
                continue
            for card in cards:
                text = card.inner_text().strip()
                if not text:
                    continue
                color = get_card_color(card)
                action = color_to_action(color)
                if action == "SKIP":
                    continue
                results.append({"raw": text, "card_action": action})
            if results:
                break
        except Exception:
            continue
    return results


def main():
    seen = load_seen()
    print(f"[{datetime.datetime.now()}] Poller started. URL: {ALERT_SERVICE_URL}")
    print(f"[{datetime.datetime.now()}] Interval: {POLL_INTERVAL}s")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        SESSION_FILE = BASE_DIR / "schwab-auth" / "feed_session.json"
        context = browser.new_context(
            storage_state=str(SESSION_FILE) if SESSION_FILE.exists() else None
        )

        login_page = context.new_page()
        login_page.goto(
            "https://watch.dailyprofitslive.com/login",
            wait_until="networkidle",
            timeout=30000,
        )
        if "login" in login_page.url.lower() or login_page.query_selector("#email"):
            login_page.fill("#email", os.environ["FEED_USER"])
            login_page.fill("#password", os.environ["FEED_PASS"])
            login_page.click("button[type=submit]")
            login_page.wait_for_load_state("networkidle")
            context.storage_state(path=str(SESSION_FILE))
            print(f"[poller] Logged in as {os.environ['FEED_USER']}")
        login_page.close()

        while True:
            try:
                page = context.new_page()
                cards = scrape_alerts(page)
                page.close()

                for card in cards:
                    raw = card["raw"]
                    card_action = card["card_action"]
                    h = alert_hash(raw)
                    if h not in seen:
                        seen.add(h)
                        save_seen(seen)
                        print(f"[{datetime.datetime.now()}] NEW [{card_action}]: {raw[:80]}")
                        try:
                            resp = requests.post(
                                LOCAL_WEBHOOK,
                                json={"raw_alert": raw, "card_action": card_action},
                                timeout=5,
                            )
                            print(f"  -> {resp.json()}")
                        except Exception as e:
                            print(f"  -> webhook error: {e}")
            except Exception as e:
                print(f"[{datetime.datetime.now()}] scrape error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
