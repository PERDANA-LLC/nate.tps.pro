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
import socket
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.trade")

sys.path.insert(0, str(Path.home() / ".hermes" / "shared"))
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


def sd_notify(state: str) -> bool:
    """Send a watchdog ping to systemd. No-op outside systemd."""
    sock_path = os.environ.get("NOTIFY_SOCKET", "")
    if not sock_path:
        return False
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(state.encode(), sock_path)
        sock.close()
        return True
    except Exception:
        return False


BROWSER_ARGS = [
    "--no-sandbox", "--disable-gpu",
    "--disable-dev-shm-usage", "--disable-setuid-sandbox",
]

# CSS classes for color detection
GREEN_CLASSES = ["bg-green-400", "bg-green-700", "bg-emerald-400", "bg-emerald-700"]
RED_CLASSES   = ["bg-red-400",   "bg-red-700",   "bg-rose-400",   "bg-rose-700"]
BLUE_CLASSES  = ["bg-blue-400",  "bg-blue-500",  "bg-blue-600",  "bg-blue-700",
                 "bg-sky-400",   "bg-sky-500",   "bg-sky-600",   "bg-sky-700",
                 "bg-cyan-400",  "bg-cyan-500",  "bg-cyan-600",  "bg-cyan-700"]


def load_seen() -> set:
    return set(SEEN_FILE.read_text().splitlines()) if SEEN_FILE.exists() else set()


def save_seen(seen: set):
    SEEN_FILE.write_text("\n".join(sorted(seen)))


def alert_hash(text: str) -> str:
    """Dedup by trade string within the same hour."""
    bucket = datetime.datetime.now().strftime("%Y%m%d%H")
    return hashlib.md5(f"{text.strip().upper()}{bucket}".encode()).hexdigest()


def detect_action(class_str: str) -> str | None:
    """Check CSS classes for green=BTO, red=STC, blue=BLUE_CARD (INFO ONLY)."""
    for g in GREEN_CLASSES:
        if g in class_str:
            return "BTO"
    for r in RED_CLASSES:
        if r in class_str:
            return "STC"
    # ⛔ BLUE_CARD = informational only — never forwarded to webhook/executor
    for b in BLUE_CLASSES:
        if b in class_str:
            return "BLUE_CARD"
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


_EXPIRY_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s+\$?\d+")


def is_option_expired(trade_line: str) -> bool:
    """Check if the option expiry in a trade line is before today."""
    m = _EXPIRY_RE.search(trade_line)
    if not m:
        return False
    month, day = int(m.group(1)), int(m.group(2))
    today = datetime.date.today()
    candidate = datetime.date(today.year, month, day)
    # If the date is more than 7 days in the past within the same year,
    # it's truly expired (not a trade from earlier this week)
    if candidate < today - datetime.timedelta(days=1):
        return True
    return False


def scrape_trades(page) -> list[dict]:
    """
    Navigate to DPL, click Trades tab, extract all trade cards + date.
    Returns list of {raw, card_action, trade_date}.
    """
    # Navigate to DPL
    page.goto(DPL_URL, wait_until="domcontentloaded", timeout=15000)
    page.set_default_timeout(30000)  # 30s max for any DOM op
    page.set_default_navigation_timeout(30000)
    time.sleep(3)

    # Click Trades tab (page defaults to Chat)
    try:
        trades_btn = page.query_selector("button:has-text('Trades')")
        if trades_btn:
            trades_btn.click()
            time.sleep(2)
    except Exception:
        pass

    # ── Extract date from Trades tab header ────────────────────
    trade_date = None
    today = datetime.date.today()
    # DPL shows dates like "May 5" — no year, no zero-padding
    # Generate all reasonable formats for comparison
    today_variants = {
        today.strftime("%B %-d"),            # "May 5"
        today.strftime("%B %d"),             # "May 05"
        today.strftime("%-m/%-d"),           # "5/5"
        today.strftime("%m/%d"),             # "05/05"
        today.strftime("%B %-d, %Y"),        # "May 5, 2026"
        today.strftime("%B %d, %Y"),         # "May 05, 2026"
        "Today", "today",
    }
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    def looks_like_date(text: str) -> bool:
        """Check if text resembles a date: contains a month name or 'Today'."""
        t = text.strip().lower()
        if t == "today":
            return True
        for month in month_names:
            if month.lower() in t:
                return True
        # Also match numeric dates like "5/4/2026" or "2026-05-04"
        import re as _re
        if _re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', t):
            return True
        return False

    try:
        # Try common date header patterns in the Trades tab
        for sel in [
            "h2", "h3",
            "[class*='date']", "[class*='Date']",
            "[class*='header'] h2", "[class*='header'] h3",
        ]:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text and len(text) < 60 and looks_like_date(text):
                    trade_date = text if text.lower() != "today" else "Today"
                    break

        # Explicitly check for "Today" text if no date found
        if not trade_date:
            today_el = page.query_selector("text='Today'") or page.query_selector("text=Today")
            if today_el:
                trade_date = "Today"
    except Exception:
        pass

    # Verify date matches system date
    if trade_date:
        # "Today" always matches
        if trade_date.lower() != "today" and trade_date not in today_variants:
            mismatch_msg = (
                f"⚠️ <b>DATE MISMATCH</b>\n"
                f"DPL Trades tab shows: <b>{trade_date}</b>\n"
                f"System date is: <b>{today.strftime('%B %-d, %Y')}</b>\n"
                f"The scraper may be reading stale data."
            )
            _telegram_send(mismatch_msg)
            _discord_send(mismatch_msg.replace("<b>", "**").replace("</b>", "**"))
            print(f"[{datetime.datetime.now()}] ⚠️ DATE MISMATCH: page={trade_date} system={today.strftime('%B %-d, %Y')}")
    else:
        # No date found on page — warn but continue
        trade_date = "Unknown"
        print(f"[{datetime.datetime.now()}] ⚠️ No date header found on Trades tab")

    # ── Force lazy-rendered cards into the DOM by scrolling ──────────
    # DPL uses a fixed-height viewport with an inner scrollable container
    # (div.flex overflow-y-scroll). Scrolling document.body does nothing.
    # Find the scrollable trade feed container and scroll it to bottom.
    scroll_container = page.evaluate("""() => {
        const candidates = document.querySelectorAll('[class*="overflow-y-scroll"], [class*="overflow-y-auto"]');
        for (const el of candidates) {
            if (el.scrollHeight > el.clientHeight + 50) return true;
        }
        return false;
    }""")

    if scroll_container:
        # Scroll the inner feed container to bottom, then back to top
        page.evaluate("""() => {
            const el = document.querySelector('[class*="overflow-y-scroll"], [class*="overflow-y-auto"]');
            if (el && el.scrollHeight > el.clientHeight + 50) {
                el.scrollTop = el.scrollHeight;
            }
        }""")
        time.sleep(1)
        page.evaluate("""() => {
            const el = document.querySelector('[class*="overflow-y-scroll"], [class*="overflow-y-auto"]');
            if (el) el.scrollTop = 0;
        }""")
        time.sleep(0.3)
    else:
        # Fallback: try body scrolling (unlikely to help on DPL)
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.3)

    results = []

    # Find ALL trade cards in one combined query — green, red, AND blue
    cards = page.query_selector_all(
        "[class*='bg-green-'], [class*='bg-red-'], "
        "[class*='bg-emerald-'], [class*='bg-rose-'], "
        "[class*='bg-blue-'], [class*='bg-sky-'], [class*='bg-cyan-']"
    )

    # ── Find today's starting marker: the first BLUE_CARD ────────────
    # DPL structure: "May 5" date label → blue card → today's trades.
    # Only process cards from the blue card onward; skip stale cards above.
    today_start = 0
    for idx, card in enumerate(cards):
        cls = card.get_attribute("class") or ""
        if any(b in cls for b in BLUE_CLASSES):
            today_start = idx
            break

    for card in cards[:today_start + 1]:  # include blue card itself
        try:
            class_str = card.get_attribute("class") or ""
            action = detect_action(class_str)
            if not action:
                continue

            card_text = card.inner_text().strip()
            trade_line = extract_trade_line(card_text)

            # BLUE_CARD: has no bto/stc line — use first meaningful text line
            if action == "BLUE_CARD":
                if not trade_line:
                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                    trade_line = lines[0] if lines else card_text[:100]
            elif not trade_line:
                continue  # Green/red cards MUST have bto/stc line

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
                "trade_date": trade_date,
            })
        except Exception:
            continue

    return results


def is_market_hours() -> bool:
    """Mon-Fri, 9:00am-5:00pm Eastern (auto-DST: 13-21 UTC EDT, 14-22 UTC EST)."""
    try:
        from zoneinfo import ZoneInfo
        eastern = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: check DST via localtime
        import time as _time
        is_dst = _time.localtime().tm_isdst == 1
        offset = 4 if is_dst else 5  # EDT=-4, EST=-5
        now_utc = datetime.datetime.utcnow()
        hour = (now_utc.hour - offset + 24) % 24 + now_utc.minute / 60.0
        if now_utc.weekday() >= 5:
            return False
        return 9.5 <= hour <= 16.0
    if eastern.weekday() >= 5:
        return False
    hour = eastern.hour + eastern.minute / 60.0
    return 9.5 <= hour <= 16.0


def notify_status(msg: str):
    """Send status notification to Telegram + Discord."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    full = f"🤖 <b>Project Nate Alert Service</b>\n{now} EST\n{msg}"
    _telegram_send(full)
    _discord_send(full.replace("<b>", "**").replace("</b>", "**"))


def notify_trade_date(date_label: str):
    """Send today's trade date to Telegram + Discord."""
    if date_label == "Unknown":
        msg = "📅 <b>Trades detected — date header not found on page</b>"
    else:
        msg = f"📅 <b>Trades for {date_label}</b>"
    _telegram_send(msg)
    _discord_send(msg.replace("<b>", "**").replace("</b>", "**"))


def notify_blue_card(raw_alert: str, trade_date: str):
    """Send blue card alert to Telegram + Discord. INFO ONLY — never executes."""
    msg = (
        f"🔵 <b>BLUE CARD — INFO ONLY</b>\n"
        f"Date: {trade_date}\n"
        f"<code>{raw_alert[:500]}</code>\n"
        f"⛔ DO NOT EXECUTE — informational card only"
    )
    _telegram_send(msg)
    _discord_send(msg.replace("<b>", "**").replace("</b>", "**").replace("<code>", "`").replace("</code>", "`"))


def capture_opening_balance():
    """Fetch Schwab cash balance at market open and save as daily budget basis."""
    try:
        token_file = BASE_DIR / "schwab-auth" / "token.json"
        if not token_file.exists():
            print("[poller] ⚠️ No token.json — can't capture opening balance")
            return
        with open(token_file) as f:
            t = json.load(f)
        h = os.environ.get("JOINT_TONA", "")
        if not h:
            print("[poller] ⚠️ JOINT_TONA not set")
            return
        r = requests.get(
            f"https://api.schwabapi.com/trader/v1/accounts/{h}",
            params={"fields": "positions"},
            headers={"Authorization": f"Bearer {t['access_token']}"},
            timeout=10)
        if r.ok:
            cash = float(r.json()["securitiesAccount"]["currentBalances"]["cashBalance"])
            budget = round(cash * float(os.environ.get("DAILY_BUDGET_PCT", "0.5")), 2)
            bal_file = BASE_DIR / "trade-log" / "opening_balance.txt"
            bal_file.parent.mkdir(exist_ok=True)
            bal_file.write_text(f"{cash}\n{budget}\n")
            print(f"[poller] 💰 Opening balance: ${cash:,.2f} → budget: ${budget:,.2f}")
        else:
            print(f"[poller] ⚠️ Opening balance fetch failed: {r.status_code}")
    except Exception as e:
        print(f"[poller] ⚠️ Opening balance error: {e}")


def main():
    if not DPL_USER or not DPL_PASS:
        print("[poller] FATAL: FEED_USER / FEED_PASS not set")
        sys.exit(1)

    seen = load_seen()
    last_notified_date = None  # Track date for once-per-day notifications
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
        if in_hours:
            notify_status("🟢 Service started — monitoring")
        else:
            print(f"[{datetime.datetime.now()}] Poller started — waiting for market hours")
        # outside-hours startup is silent (no push notification)

        # ── Poll loop ───────────────────────────────────────────────
        first_scrape = True  # suppress blue cards on initial scrape (stale cards from prior days)
        while True:
            try:
                now_in_hours = is_market_hours()

                # Market hours transition detection
                if now_in_hours and not in_hours:
                    notify_status("🟢 Market open at 9:30 AM EST — monitoring DPL trades")
                    capture_opening_balance()
                elif in_hours and not now_in_hours:
                    notify_status("🔴 Market closed at 4:00 PM EST — see you tomorrow")

                in_hours = now_in_hours

                if not now_in_hours:
                    sd_notify("WATCHDOG=1")  # ping: alive, just waiting
                    time.sleep(POLL_INTERVAL)
                    continue

                page = context.new_page()
                alerts = scrape_trades(page)
                page.close()
                sd_notify("WATCHDOG=1")  # ping systemd: poll cycle completed successfully

                for alert in alerts:
                    raw = alert["raw"]
                    card_action = alert["card_action"]
                    is_correction = alert.get("is_correction", False)
                    trade_date = alert.get("trade_date", "")

                    # ⛔ Skip expired options — stale cards stuck on DPL page
                    if is_option_expired(raw):
                        print(f"[{datetime.datetime.now()}] ⏰ EXPIRED (skipped) | {raw[:100]}")
                        continue

                    # Notify once per day when we see a new trade date
                    if trade_date and trade_date != last_notified_date:
                        notify_trade_date(trade_date)
                        last_notified_date = trade_date
                        print(f"[{datetime.datetime.now()}] 📅 Date: {trade_date}")

                    # ⛔ BLUE CARDS: notify Telegram+Discord, NEVER forward to webhook/executor ⛔
                    if card_action == "BLUE_CARD":
                        h = alert_hash(raw)
                        if h not in seen:
                            seen.add(h)
                            save_seen(seen)
                            if not first_scrape:
                                notify_blue_card(raw, trade_date)
                                print(f"[{datetime.datetime.now()}] 🔵 BLUE CARD (no-exec) | {trade_date}: {raw[:100]}")
                            else:
                                print(f"[{datetime.datetime.now()}] 🔵 BLUE CARD (silent seed) | {trade_date}: {raw[:100]}")
                        continue  # <-- HARD GUARD: skips webhook POST below

                    h = alert_hash(raw)
                    if h not in seen:
                        seen.add(h)
                        save_seen(seen)
                        label = f"CORRECTED [{card_action}]" if is_correction else f"NEW [{card_action}]"
                        print(f"[{datetime.datetime.now()}] 🚨 {label} | {trade_date}: {raw[:100]}")
                        # ⛔ BLUE_CARD already filtered above — only BTO/STC reach here ⛔
                        try:
                            resp = requests.post(
                                LOCAL_WEBHOOK,
                                json={
                                    "raw_alert": raw,
                                    "card_action": card_action,
                                    "is_correction": is_correction,
                                    "trade_date": trade_date,
                                },
                                timeout=5,
                            )
                            print(f"  ↳ webhook: {resp.status_code}")
                        except Exception as e:
                            print(f"  ↳ webhook error: {e}")

            except Exception as e:
                print(f"[{datetime.datetime.now()}] Poll error: {e}")
                sd_notify("WATCHDOG=1")  # ping: alive despite error — don't kill me

            first_scrape = False
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
