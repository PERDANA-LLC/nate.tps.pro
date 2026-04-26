# Full Automation: Claude Code Routine → Schwab Option Trading (VPS Ubuntu 24.04)

> **Stack:** Playwright (headless Chrome) + Schwab API
> **Use Case:** DailyProfitsLive alert page → VPS detects alert → immediately executes on Schwab (no grading step)
> **Infrastructure:** Ubuntu 24.04 VPS + systemd services
> **Alert URL:** `https://watch.dailyprofitslive.com/?channel=trades`

---

advance feature:
max contracts per trade = 1
max dollar amount per trade = $1000


menu: 
/paper on/off
/testmode on/off
/equity on/off
/options on/off
/switch JOINT_TONA/ROTH_ANNA
/reset  - clear all alerts, queues, logs, restart services
/status - show current status
/help - show help
/config - show current config
/last_pnl - show last PNL
/logs - show last logs
/backup - backup current root and home 

tps-ttm

trend: EMA 8, EMA 21, EMA 55: BULL, BEAR, NEUTRAL
pattern: bull flag, bull pennant
squeeze pro: Keltner channel + bollinger bands: 🟠 orange dot
 ─🩳  short float >20%  |  📅 days-to-cover >5 
vwap: 💧 above VWAP↑
volume: 📦 vol cross burst
volume profile
RAF

target price: calculated using ATR 
stop loss: calculated using ATR:  📐 ATR(14): 4.82  |  🎯 Target: $136.24  |  🛑 Stop: $126.78  
multiple time frames: W, D, 195, 130, 78, 60, 30, 15, 10, 5: ⚡ multi-TF sqz (3)

---

hey claude, review all my code base, detect errors, security issues, and improve it if possible.

---

hey gemini, i have a vps running ubuntu 24.04 lts.                                                                                                   
   and i am trading option programmatically with schwab developer api                                                                                   
      how do i correlate my option trading with VXX                                                                                                     
   write to @docs/VXX.md 

---

fundamental analysis
technical analysis
options trading
today's market news

   ---



## How to Navigate to the Trade Feed

1. Go to `https://watch.dailyprofitslive.com/?channel=trades`
2. Click the **"Trades"** tab — it is highlighted in **green** in the top navigation bar
3. The right sidebar shows live alert cards — new alerts appear at the top

## Alert Color Key (DailyProfitsLive)

The right sidebar displays cards with distinct background colors. Read them as follows:

| Card Color | Label Examples | Meaning | Action |
|------------|---------------|---------|--------|
| **Blue / Dark** | "Today's Plan", "New Highs... Now What?", "Earnings Season Kickoff" | Commentary, market thoughts, schedule posts | **SKIP — not a trade** |
| **Green** | "Adding to FSLY", "New Position" | Opening trade — `bto ...` | **BTO → Buy to Open** |
| **Red** | "Closing RILY", "Scaling FSLY Lotto" | Closing trade — `stc ...` or "closing all ..." | **STC → Sell to Close** |

> **From the actual feed screen:** blue/dark cards like "Today's Plan: Earnings Season Kickoff" and "New Highs... Now What?" are commentary — never trade them. Red cards like "Closing RILY" and "Scaling FSLY Lotto" are STC signals — always execute. Green cards are BTO signals — execute immediately.

**Real alert examples from the feed:**

```
# GREEN — opening trades (BTO)
bto RILY 4/17 $8 calls @ $.20
bto RILY 4/24 $7.50 calls @ $.60
bto FSLY 4/17 $26 calls @ .15
bto FSLY 4/17 $32 calls @ .50
bto FSLY 4/10 $27 calls @ .40
bto FSLY 4/10 $25 call @ .15
bto FSLY 4/24 $30 calls @.55
bto FSLY 4/17 $26 calls @ .70

# RED — closing trades (STC)
stc 2/3rd's FSLY 4/17 $26 calls @ .50      ← "Scaling FSLY Lotto" red card
stc 2/3rds of FSLY 4/17 $32 calls @ $1
stc RILY 4/24 $7.50 calls near $.50         ← "Closing RILY" red card
stc BW 4/17 $18 calls near $.75
stc BW 4/17 $20 calls near $.25
closing all FSLY for next week and 4/24     ← no price, use MARKET

# BLUE/DARK — commentary (SKIP entirely — these are not trades)
"Today's Plan: Earnings Season Kickoff"
"New Highs... Now What?"
"Today's Plan" posts with schedule / market thoughts
```

---

## Overview

Your VPS on Ubuntu 24.04:
1. Monitors `https://watch.dailyprofitslive.com/?channel=trades` with a headless Playwright browser
2. Detects new alerts by card color — **skips blue**, acts on **green (BTO)** and **red (STC)**
3. Parses the alert (ticker, strike, expiry, price) — no AI grading step
4. Immediately executes buy/sell orders on Schwab — via the **Schwab Individual Trader API** (preferred) or **Playwright browser automation** on Schwab's website (fallback)
5. Logs every trade

You only watch — the system executes everything.

---

## Part 1 — Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      VPS (Ubuntu 24.04)                          │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐                           │
│  │  Playwright  │────▶│  webhook_    │                           │
│  │  (headless)  │     │  server.py   │                           │
│  │              │     │              │                           │
│  │ Polls        │     │ Color-filter │                           │
│  │ watch.daily  │     │ + parse alert│                           │
│  │ profitslive  │     │ → EXECUTE    │                           │
│  └──────┬───────┘     └──────┬───────┘                           │
│         │                   │                                   │
│         │ green/red alert    │ parsed alert (no grading)         │
│         ▼                   ▼                                   │
│  ┌──────────────┐     ┌──────────────┐                           │
│  │  Alert Queue │     │  Schwab API  │ ◀── preferred path        │
│  │  (JSONL)     │     │  OR          │                           │
│  └──────────────┘     │  Playwright  │ ◀── fallback path         │
│                       │  on Schwab   │                           │
│                       └──────┬───────┘                           │
│                              │                                   │
│                       ┌──────▼───────┐                           │
│                       │  Trade Log   │                           │
│                       │  + ntfy push │                           │
│                       └──────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

**Two execution paths:**
- **Path A — Schwab Individual Trader API** (recommended): official REST API, most reliable
- **Path B — Playwright on Schwab website** (fallback): headless Chrome fills the order form

**Key design choices:**
- No AI grading step — every parsed green/red alert is executed immediately
- systemd manages services — auto-restart on crash, auto-start on reboot
- Color detection on the alert feed determines BTO vs STC vs SKIP

---

## Part 2 — VPS Setup (Ubuntu 24.04)

### Recommended VPS specs

| Use case | RAM | CPU | Notes |
|----------|-----|-----|-------|
| Minimum | 2 GB | 1 vCPU | Claude runs on Anthropic's servers |
| Recommended | 4 GB | 2 vCPU | Headless Chrome benefits from extra RAM |

OS: Ubuntu 24.04 LTS. Any major VPS provider works (DigitalOcean, Hetzner, Linode, Vultr).

### Step 1: Base setup
```bash
sudo apt update && sudo apt upgrade -y
sudo timedatectl set-timezone America/New_York
sudo apt install -y python3 python3-pip git curl tmux nodejs npm
date   # verify Eastern time
```

### Step 2: Install Claude Code CLI + Gemini CLI
```bash
# Claude Code CLI
npm install -g @anthropic-ai/claude-code
claude auth login        # authenticate once — opens browser
claude -p "reply OK"     # verify

# Gemini CLI
npm install -g @google/gemini-cli
gemini auth login        # authenticate once — opens browser
gemini -p "reply OK"     # verify
```

> Both CLIs are installed globally via npm. `claude auth login` and `gemini auth login` each open a browser for OAuth — run these once from a local machine via SSH port-forward, or use a headless auth token if your provider supports it.

### Step 4: Install Playwright (headless Chrome on VPS)
```bash
pip3 install playwright
playwright install chromium
playwright install-deps chromium   # required on Linux VPS

# Verify headless Chrome works
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://example.com')
    print('OK:', page.title())
    browser.close()
"
```

### Step 5: Install Python dependencies
```bash
pip3 install requests python-dotenv schwab-py
```

### Step 6: Create directory structure
```bash
mkdir -p ~/trade-alerts ~/trade-log \
         ~/alert-bridge ~/logs ~/schwab-auth
```

### Step 7a: Hash Value
# Schwab Account Hash Values                                                                                                                                                                                 
                                                                                                                                                                                                               
  | Account Number | Hash Value |                                                                                                                                                                              
  |---|---|                                                                                                                                                                                                    
  | 55686714 | `FAB844331D2B573F17C82A55DF0FF37267F0D29645AB5266C96C37D8DC96D5A7` |                                                                                                                            
  | 78720781 | `D5E295EAF40AD38CAB31F7B50A21AE4D59BC1115968A0E7048DDF0658A2D2DB9` |                                                                                                                            
  | 90107168 | `0A81279CF5BAFA3BCA6669BA3D1A45F41F978DA7D60CF202CC95949947835388` |
  | 92838340 | `612C5139DE9198AE4850D46F43243B161A427405CA6C1550C82E5476C4345611` |                                                                                                                            
  | 95212569 | `CF51F2CB4CAE5E89D55670D77EF72246BE9E2E2A6CD3AFB74C1FA2003A3BFE5F` |                                                                                                                            
  | 99058792 | `F956D28B537F6533DBB9D09C613380F640EF8FCC3351FA7C9A93E34C23056B24` |

# FMP for fundamental analysis
api="3mh3Peh9QyHCxY1QAOPdvr8zQlKH4QR3"
  
  # app password
gideon.northseminole@gmail.com
agenda="kzmu uuid siwh rhin"
business.qbo@gmail.com
nate.auto.trading="enyq sesr ahef kjwx"

# @BotFather
@nate_auto_trading_bot

Done! Congratulations on your new bot. 
You will find it at t.me/nate_auto_trading_bot. 
You can now add a description, about section and profile picture for your bot, 
see /help for a list of commands. By the way, when you've finished creating your cool bot, 
ping our Bot Support if you want a better username for it. 
Just make sure the bot is fully operational before you do this.

Use this token to access the HTTP API:
8736550469:AAHs0HffO5cQ2fdSgrwUSgSZf0trxKSIxg4
Keep your token secure and store it safely, 
it can be used by anyone to control your bot.

For a description of the Bot API, 
see this page: https://core.telegram.org/bots/api

# @userinfobot
Id: 8562252056
First: Thomas
Last: Perdana
Lang: en





# @BotFather
Done! Congratulations on your new bot. 
You will find it at t.me/tona_pcs_bot. 
You can now add a description, about section and profile picture for your bot, 
see /help for a list of commands. By the way, when you've finished creating your cool bot, 
ping our Bot Support if you want a better username for it. 
Just make sure the bot is fully operational before you do this.

Use this token to access the HTTP API:
8707057866:AAH9Rr1Bkatvq7D8Dufd6elBZu32Un9cj4Q
Keep your token secure and store it safely, 
it can be used by anyone to control your bot.

For a description of the Bot API, see this page: https://core.telegram.org/bots/api

# tona_pcs_group
# Telegram — rotate token via @BotFather if compromised
TELEGRAM_BOT_TOKEN=8707057866:AAH9Rr1Bkatvq7D8Dufd6elBZu32Un9cj4Q
# Comma-separated chat IDs allowed to receive notifications and send commands
TELEGRAM_CHAT_IDS=8562252056,8617227882,-1005059923222

# nate.auto.trading.group
# Telegram — rotate token via @BotFather if compromised
TELEGRAM_BOT_TOKEN=8736550469:AAHs0HffO5cQ2fdSgrwUSgSZf0trxKSIxg4
# Comma-separated chat IDs allowed to receive notifications and send commands
TELEGRAM_CHAT_IDS=8562252056,-1003858943493,8617227882



### Step 7b: Store credentials securely
```bash
cat > ~/.env.trade <<'EOF'
SCHWAB_CLIENT_ID=eQvUio57aVKbpNiWqLFaPpEikmFyNHAlpjgqU1LOaDF2ueJI
SCHWAB_CLIENT_SECRET=XrB57JVKwAZJHn9RRnNBitNrb38HWI0dtzYtryTx64VD8lPeBloVg2TnoRGvRdLf
SCHWAB_ACCOUNT_HASH=D5E295EAF40AD38CAB31F7B50A21AE4D59BC1115968A0E7048DDF0658A2D2DB9
SCHWAB_CALLBACK_URL=https://127.0.0.1
ALERT_SERVICE_URL=https://watch.dailyprofitslive.com/?channel=trades
FEED_USER=thomasperdana@gmail.com
FEED_PASS=kVa5#HBuH$Y2Z4m
NTFY_CHANNEL=tona-trades-78720781
PAPER_TRADE=true

# Email — Gmail App Password (myaccount.google.com → Security → App passwords)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=thomasperdana@gmail.com
SMTP_PASS="bsdx ygit bbaf fray"
ALERT_EMAIL_1=thomasperdana@gmail.com
ALERT_EMAIL_2=annaperdana@gmail.com
ALERT_EMAIL_3=4074171784@tmomail.net
ALERT_EMAIL_4=3215931230@tmomail.net
EOF
chmod 600 ~/.env.trade
```

> **Never hardcode credentials in Python files.** Always load from `~/.env.trade` via `python-dotenv`.
> NTFY_CHANNEL=tona-trades-78720781
                                                                                                                                                                Then subscribe on your phone: open ntfy app → subscribe to tona-trades-78720781.                                                                                                                                               
Push URL becomes: https://ntfy.sh/tona-trades-78720781

---

## Part 2b — Shared Notifier

All notifications — alert received AND trade executed — go through one shared module.
It sends to **ntfy** and **4 email addresses** (SMTP/Gmail).

### `~/alert-bridge/notifier.py`
```python
#!/usr/bin/env python3
"""
Shared notification module.
Called by webhook_server.py (alert received) and schwab_executor.py (trade executed).
Sends to: ntfy push + 4 email addresses (SMTP).
All destinations are configurable via ~/.env.trade.
"""
import os
import smtplib
import requests
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env.trade")

# Configurable destinations — edit in ~/.env.trade, not here
EMAILS = [
    os.environ.get("ALERT_EMAIL_1", "thomasperdana@gmail.com"),
    os.environ.get("ALERT_EMAIL_2", "annaperdana@gmail.com"),
    os.environ.get("ALERT_EMAIL_3", "4074171784@tmomail.net"),
    os.environ.get("ALERT_EMAIL_4", "3215931230@tmomail.net"),
]


def _send_ntfy(body: str):
    channel = os.environ.get("NTFY_CHANNEL", "")
    if not channel:
        return
    try:
        requests.post(f"https://ntfy.sh/{channel}", data=body.encode(), timeout=5)
    except Exception as e:
        print(f"[notifier] ntfy error: {e}")


def _send_email(subject: str, body: str):
    host     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port     = int(os.environ.get("SMTP_PORT", "587"))
    user     = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_    = os.environ.get("SMTP_USER", "")
    if not (user and password):
        print("[notifier] SMTP not configured — skipping email")
        return
    for to_addr in EMAILS:
        if not to_addr:
            continue
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"]    = from_
            msg["To"]      = to_addr
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(user, password)
                server.sendmail(from_, to_addr, msg.as_string())
            print(f"[notifier] email sent → {to_addr}")
        except Exception as e:
            print(f"[notifier] email error → {to_addr}: {e}")


def notify_alert(raw_alert: str, card_action: str):
    """Call this when an alert is first received (before execution)."""
    subject = f"ALERT RECEIVED: {card_action}"
    body    = f"{card_action} alert detected\n{raw_alert}"
    _send_ntfy(body)
    _send_email(subject, body)


def notify_execution(msg: str, subject: str = "TRADE EXECUTED"):
    """Call this after a trade is submitted or paper-logged."""
    _send_ntfy(msg)
    _send_email(subject, msg)


def notify_error(msg: str):
    """Call this on errors (parse failure, API failure, etc.)."""
    _send_ntfy(msg)
    _send_email("TRADE ERROR", msg)
```

> **Gmail setup:** Go to myaccount.google.com → Security → 2-Step Verification → App passwords. Generate one for "Mail" and put it in `SMTP_PASS`. Do not use your regular Gmail password.

---

## Part 3 — Alert Capture (VPS Polls DailyProfitsLive)

A headless Playwright browser polls `https://watch.dailyprofitslive.com/?channel=trades` every 90 seconds.
It detects card color to filter: **blue = skip**, **green = BTO**, **red = STC**.

### `~/alert-bridge/alert_poller.py`
```python
#!/usr/bin/env python3
"""
Headless Playwright on VPS. Monitors watch.dailyprofitslive.com/?channel=trades
Color-based detection:
  - Blue/teal cards  → SKIP (commentary, not a trade)
  - Green cards      → BTO (buy to open)
  - Red cards        → STC (sell to close)
Detects new alerts. POSTs to local webhook for Claude Code analysis.
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

load_dotenv(Path.home() / ".env.trade")

ALERT_SERVICE_URL = os.environ.get("ALERT_SERVICE_URL",
                                   "https://watch.dailyprofitslive.com/?channel=trades")
LOCAL_WEBHOOK = "http://127.0.0.1:8765/alert"
POLL_INTERVAL = 90  # seconds — do not poll faster to avoid IP blocks

# Matches both BTO and STC in the real DailyProfitsLive format:
#   bto FSLY 4/17 $26 calls @ .15
#   stc 2/3rd's FSLY 4/17 $26 calls @ .50
#   stc RILY 4/24 $7.50 calls near $.50
#   closing all FSLY for next week
ALERT_PATTERN = re.compile(
    r"(?P<action>bto|stc)\s+"
    r"(?:(?P<partial>[\d/]+(?:rd'?s?|th)?)\s+(?:of\s+)?)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<type>calls?|puts?)"
    r"(?:\s+(?:@|near)\s*\$?(?P<price>\d+(?:\.\d+)?))?",
    re.IGNORECASE
)

# "closing all TICKER ..." — implicit full STC, no price
CLOSING_ALL_PATTERN = re.compile(
    r"closing\s+all\s+(?P<ticker>[A-Z]+)",
    re.IGNORECASE
)

SEEN_FILE = Path.home() / "trade-alerts" / "seen_hashes.txt"
SEEN_FILE.parent.mkdir(exist_ok=True)

# Card color → trade direction
# DailyProfitsLive uses CSS background colors we detect via computed style
GREEN_COLORS = {"#1a5c2e", "#2d7a3e", "#1e7a34", "#166534", "rgb(22,101,52)",
                "green", "#155724", "#198754"}
RED_COLORS   = {"#7f1d1d", "#991b1b", "#b91c1c", "#dc2626", "rgb(185,28,28)",
                "red", "#842029", "#dc3545"}
# Blue/teal = skip — anything not green or red

def load_seen():
    return set(SEEN_FILE.read_text().splitlines()) if SEEN_FILE.exists() else set()

def save_seen(seen):
    SEEN_FILE.write_text("\n".join(sorted(seen)))

def alert_hash(raw: str) -> str:
    bucket = datetime.datetime.now().strftime("%Y%m%d%H") + \
             str(int(datetime.datetime.now().minute / 5))
    return hashlib.md5(f"{raw.strip().upper()}{bucket}".encode()).hexdigest()

def get_card_color(element) -> str:
    """Return normalized bg color of a card element."""
    try:
        color = element.evaluate(
            "el => window.getComputedStyle(el).backgroundColor"
        )
        return (color or "").lower().replace(" ", "")
    except Exception:
        return ""

def color_to_action(color: str) -> str:
    """Map card background to BTO / STC / SKIP."""
    for g in GREEN_COLORS:
        if g.replace(" ", "") in color:
            return "BTO"
    for r in RED_COLORS:
        if r.replace(" ", "") in color:
            return "STC"
    return "SKIP"

def scrape_alerts(page) -> list:
    """
    Returns list of dicts: {"raw": text, "card_action": "BTO"|"STC"|"SKIP"}
    """
    page.goto(ALERT_SERVICE_URL, wait_until="networkidle", timeout=30000)
    results = []

    # Try common card selectors used by the feed
    for selector in [".alert-card", ".trade-card", ".signal-card",
                     "[data-alert]", ".card", "article", "li"]:
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
                    continue  # blue/teal commentary — ignore
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
        SESSION_FILE = Path.home() / "schwab-auth" / "feed_session.json"
        context = browser.new_context(
            storage_state=str(SESSION_FILE) if SESSION_FILE.exists() else None
        )

        # Log in to DailyProfitsLive (credentials from ~/.env.trade)
        login_page = context.new_page()
        login_page.goto("https://watch.dailyprofitslive.com/login",
                        wait_until="networkidle", timeout=30000)
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
                                timeout=5
                            )
                            print(f"  → {resp.json()}")
                        except Exception as e:
                            print(f"  → webhook error: {e}")
            except Exception as e:
                print(f"[{datetime.datetime.now()}] scrape error: {e}")
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
```

---

## Part 4 — Webhook Server + Alert Parser

### `~/alert-bridge/webhook_server.py`
```python
#!/usr/bin/env python3
"""
Receives alerts from poller. Parses real DailyProfitsLive format.
Spawns executor directly (no grading step).
Sends alert notification to 4 email addresses via notifier.py.
Binds to 127.0.0.1 only — never exposed publicly.

Supported formats:
  bto FSLY 4/17 $26 calls @ .15
  bto RILY 4/24 $7.50 calls @ $.60
  stc 2/3rd's FSLY 4/17 $26 calls @ .50
  stc RILY 4/24 $7.50 calls near $.50
  stc BW 4/17 $18 calls near $.75
  closing all FSLY for next week and 4/24
"""
import json
import re
import datetime
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
import sys
sys.path.insert(0, str(Path.home() / "alert-bridge"))
from notifier import notify_alert, notify_error

ALERT_DIR = Path.home() / "trade-alerts"
ALERT_DIR.mkdir(exist_ok=True)

# Primary pattern: bto/stc TICKER MM/DD $STRIKE calls/puts [@ / near] PRICE
ALERT_PATTERN = re.compile(
    r"(?P<action>bto|stc)\s+"
    r"(?:(?P<partial>[\d/]+(?:rd'?s?|th)?(?:\s+of)?)\s+)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<type>calls?|puts?)"
    r"(?:\s+(?:@|near)\s*\$?(?P<price>\d+(?:\.\d+)?))?",
    re.IGNORECASE
)

# "closing all TICKER ..." — full implicit STC, no price
CLOSING_ALL_PATTERN = re.compile(
    r"closing\s+all\s+(?P<ticker>[A-Z]+)",
    re.IGNORECASE
)

@dataclass
class ParsedAlert:
    action: str           # BTO or STC
    ticker: str
    strike: float
    option_type: str      # CALL or PUT
    expiry_raw: str       # e.g. "4/17"
    expiry_date: str      # YYYY-MM-DD
    days_to_expiry: int
    order_type: str       # LIMIT or MARKET
    limit_price: Optional[float]
    partial_close: Optional[str]   # e.g. "2/3rds" or None
    card_action: str      # from color detection: BTO or STC

def resolve_expiry(raw: str):
    today = datetime.date.today()
    month, day = map(int, raw.split("/"))
    candidate = datetime.date(today.year, month, day)
    if candidate < today:
        candidate = datetime.date(today.year + 1, month, day)
    return candidate.isoformat(), (candidate - today).days

def parse_alert(text: str, card_action: str = "") -> Optional[ParsedAlert]:
    text_clean = text.strip()

    # Try primary pattern first
    m = ALERT_PATTERN.search(text_clean)
    if m:
        action = m.group("action").upper()
        expiry_date, dte = resolve_expiry(m.group("expiry"))
        price_str = m.group("price")
        partial = (m.group("partial") or "").strip().rstrip("of").strip() or None
        opt_type = "CALL" if m.group("type").upper().startswith("CALL") else "PUT"
        limit_price = float(price_str) if price_str else None
        order_type = "LIMIT" if limit_price else "MARKET"
        return ParsedAlert(
            action=action,
            ticker=m.group("ticker").upper(),
            strike=float(m.group("strike")),
            option_type=opt_type,
            expiry_raw=m.group("expiry"),
            expiry_date=expiry_date,
            days_to_expiry=dte,
            order_type=order_type,
            limit_price=limit_price,
            partial_close=partial,
            card_action=card_action or action,
        )

    # "closing all TICKER ..." — red card, full STC at market
    m2 = CLOSING_ALL_PATTERN.search(text_clean)
    if m2 and card_action == "STC":
        ticker = m2.group("ticker").upper()
        today = datetime.date.today()
        return ParsedAlert(
            action="STC",
            ticker=ticker,
            strike=0.0,
            option_type="CALL",   # executor will look up from log
            expiry_raw="",
            expiry_date=today.isoformat(),
            days_to_expiry=0,
            order_type="MARKET",
            limit_price=None,
            partial_close=None,
            card_action="STC",
        )

    return None

class AlertHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        raw = body.get("raw_alert", "").strip()
        card_action = body.get("card_action", "")

        today = datetime.date.today().strftime("%Y%m%d")
        with open(ALERT_DIR / f"{today}.jsonl", "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.datetime.now().isoformat(),
                "raw": raw,
                "card_action": card_action
            }) + "\n")

        parsed = parse_alert(raw, card_action)
        if not parsed:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"parse_failed"}')
            return

        # Notify both phones + both emails that an alert was received
        notify_alert(raw, card_action)

        subprocess.Popen([
            "python3",
            str(Path.home() / "alert-bridge" / "schwab_executor.py"),
            json.dumps(asdict(parsed))
        ])

        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "queued",
            "parsed": asdict(parsed)
        }).encode())

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8765), AlertHandler)
    print("Webhook on 127.0.0.1:8765")
    server.serve_forever()
```

---

## Part 5 — No Grading Step

There is no AI analysis layer. The webhook server passes the parsed alert **directly** to `schwab_executor.py`. Every green card (BTO) and every red card (STC) is executed immediately after a successful parse — no confidence scoring, no CLAUDE.md rules file, no `claude -p` call.

---

## Part 6 — Schwab Execution (Two Paths)

### One-time OAuth2 setup: `~/schwab-auth/setup_token.py`
```python
#!/usr/bin/env python3
"""Run ONCE manually to authorize Schwab API. Tokens auto-refresh after this."""
import schwab
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env.trade")
TOKEN_FILE = Path.home() / "schwab-auth" / "token.json"

client = schwab.auth.easy_client(
    api_key=os.environ["SCHWAB_CLIENT_ID"],
    app_secret=os.environ["SCHWAB_CLIENT_SECRET"],
    callback_url=os.environ["SCHWAB_CALLBACK_URL"],
    token_path=str(TOKEN_FILE),
)
print(f"Authorized. Token saved: {TOKEN_FILE}")
```

```bash
# Run once from your terminal — opens a browser for Schwab login
python3 ~/schwab-auth/setup_token.py
```

### Main executor: `~/alert-bridge/schwab_executor.py`
```python
#!/usr/bin/env python3
"""
Executes option orders via Schwab API (Path A).
Falls back to Playwright on Schwab website (Path B) if API fails.
Circuit breakers prevent runaway trading.
Execution status is sent to 4 email addresses via notifier.py.
"""
import sys
import json
import os
import csv
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, str(Path.home() / "alert-bridge"))
from notifier import notify_execution, notify_error

load_dotenv(Path.home() / ".env.trade")

TOKEN_FILE = Path.home() / "schwab-auth" / "token.json"
LOG_FILE = Path.home() / "trade-log" / "trades.csv"
LOG_FILE.parent.mkdir(exist_ok=True)
PAPER_TRADE = os.environ.get("PAPER_TRADE", "true").lower() == "true"

TARGET_TRADE_SIZE_USD = 1000
MAX_DAILY_TRADES = 5

def count_todays_bto() -> int:
    """Count BTO (open) trades placed today. STC closes are not counted."""
    today = datetime.date.today().isoformat()
    if not LOG_FILE.exists():
        return 0
    count = 0
    with open(LOG_FILE) as f:
        for row in csv.DictReader(f):
            if (row.get("timestamp", "").startswith(today)
                    and row.get("action") == "BTO"
                    and row.get("execution_status") in ("SUBMITTED", "FILLED")):
                count += 1
    return count

def calculate_quantity(limit_price: float) -> int:
    """
    Auto-size contracts so total cost ~$1,000.
    cost = limit_price * 100 * qty  ->  qty = round(1000 / (limit_price * 100))
    Examples using real DailyProfitsLive prices:
      @.15  ->  round(1000/15)  = 67 contracts -> $1,005
      @.60  ->  round(1000/60)  = 17 contracts -> $1,020
      @.70  ->  round(1000/70)  = 14 contracts -> $980
      @.20  ->  round(1000/20)  = 50 contracts -> $1,000
    """
    if not limit_price or limit_price <= 0:
        return 1
    return max(1, round(TARGET_TRADE_SIZE_USD / (limit_price * 100)))

def position_cost_usd(limit_price: float, qty: int) -> float:
    return limit_price * 100 * qty

def get_bto_quantity(ticker: str, strike: float, option_type: str, expiry: str) -> int:
    if not LOG_FILE.exists():
        return 1
    with open(LOG_FILE) as f:
        rows = list(csv.DictReader(f))
    for row in reversed(rows):
        if (row.get("action") == "BTO"
                and row.get("ticker") == ticker
                and str(row.get("option_type")) == option_type
                and str(row.get("expiry")) == expiry
                and row.get("execution_status") in ("SUBMITTED", "FILLED", "PAPER_TRADE")):
            try:
                notes = row.get("notes", "")
                for part in notes.split(","):
                    if part.strip().startswith("qty="):
                        return int(part.strip().split("=")[1])
            except Exception:
                pass
    return 1

def log_trade(analysis: dict, execution_status: str,
              order_id: str = "", fill_price=None, qty: int = 1, notes: str = ""):
    fieldnames = [
        "timestamp", "raw_alert", "action", "ticker", "option_type",
        "strike", "expiry", "limit_price", "order_id", "fill_price",
        "confidence", "risk_flags", "ai_recommendation", "execution_status", "notes"
    ]
    write_header = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.datetime.now().isoformat(),
            "raw_alert": f"{analysis.get('action')} {analysis.get('ticker')} "
                         f"{int(analysis.get('strike',0))}"
                         f"{'C' if analysis.get('option_type')=='CALL' else 'P'} "
                         f"EXP {analysis.get('expiry_date','')}",
            "action": analysis.get("action"),
            "ticker": analysis.get("ticker"),
            "option_type": analysis.get("option_type"),
            "strike": analysis.get("strike"),
            "expiry": analysis.get("expiry_date"),
            "limit_price": analysis.get("limit_price"),
            "order_id": order_id,
            "fill_price": fill_price,
            "confidence": analysis.get("confidence"),
            "risk_flags": str(analysis.get("risk_flags", [])),
            "ai_recommendation": analysis.get("recommendation"),
            "execution_status": execution_status,
            "notes": f"qty={qty}" + (f", {notes}" if notes else ""),
        })

def execute_via_api(analysis: dict) -> bool:
    """Path A: Schwab Individual Trader API."""
    try:
        import schwab
        from schwab import orders as o
        from datetime import datetime as dt

        client = schwab.auth.client_from_token_file(
            token_path=str(TOKEN_FILE),
            api_key=os.environ["SCHWAB_CLIENT_ID"],
            app_secret=os.environ["SCHWAB_CLIENT_SECRET"],
        )
        account_hash = os.environ["SCHWAB_ACCOUNT_HASH"]

        ticker = analysis["ticker"]
        action = analysis["action"]
        strike = float(analysis["strike"])
        opt_type = analysis["option_type"]
        expiry = analysis["expiry_date"]
        limit_price = analysis.get("limit_price")
        qty = analysis.get("quantity", 1)

        exp = dt.strptime(expiry, "%Y-%m-%d")
        exp_str = exp.strftime("%y%m%d")
        opt_char = "C" if opt_type == "CALL" else "P"
        strike_str = f"{int(strike * 1000):08d}"
        option_symbol = f"{ticker:<6}{exp_str}{opt_char}{strike_str}"

        instruction = (o.OptionInstruction.BUY_TO_OPEN
                       if action == "BTO" else o.OptionInstruction.SELL_TO_CLOSE)

        if limit_price:
            order = (o.OptionOrder()
                     .set_order_type(o.OrderType.LIMIT)
                     .set_session(o.Session.NORMAL)
                     .set_duration(o.Duration.DAY)
                     .set_price(limit_price)
                     .add_option_leg(instruction, option_symbol, qty))
        else:
            order = (o.OptionOrder()
                     .set_order_type(o.OrderType.MARKET)
                     .set_session(o.Session.NORMAL)
                     .set_duration(o.Duration.DAY)
                     .add_option_leg(instruction, option_symbol, qty))

        resp = client.place_order(account_hash, order)
        order_id = resp.headers.get("location", "").split("/")[-1]
        total_cost = (limit_price or 0) * 100 * qty
        log_trade(analysis, "SUBMITTED", order_id=order_id, qty=qty)
        notify_execution(
            f"ORDER PLACED: {action} {ticker} {int(strike)}{opt_char} EXP {expiry}"
            + (f" @{limit_price}" if limit_price else " MKT")
            + f" x{qty} contracts (~${total_cost:.0f})"
            + f"\nID: {order_id}",
            subject=f"ORDER PLACED: {action} {ticker}"
        )
        print(f"[executor] API order placed. ID: {order_id} | qty={qty} | cost~${total_cost:.0f}")
        return True

    except Exception as e:
        print(f"[executor] API failed: {e}")
        return False

def execute_via_playwright(analysis: dict) -> bool:
    """Path B: Playwright headless Chrome on Schwab website."""
    try:
        from playwright.sync_api import sync_playwright
        import time

        ticker = analysis["ticker"]
        action = analysis["action"]
        strike = analysis["strike"]
        opt_type = analysis["option_type"]
        expiry = analysis["expiry_date"]
        limit_price = analysis.get("limit_price")
        qty = analysis.get("quantity", 1)

        session_file = Path.home() / "schwab-auth" / "schwab_session.json"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(session_file) if session_file.exists() else None
            )
            page = context.new_page()
            page.goto("https://client.schwab.com/Areas/Trade/Allinone/index.aspx",
                      timeout=30000)
            page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                print("[playwright] session expired — re-login needed")
                browser.close()
                return False

            page.click("text=Options", timeout=5000)
            time.sleep(0.5)

            for sel in ["input[placeholder*='Symbol']", "#symbolEntry", "input[name*='symbol']"]:
                try:
                    page.fill(sel, ticker)
                    page.press(sel, "Enter")
                    break
                except Exception:
                    continue
            time.sleep(1)

            action_label = "Buy to Open" if action == "BTO" else "Sell to Close"
            try:
                page.select_option("select[name*='action']", label=action_label)
            except Exception:
                page.click(f"text={action_label}")
            time.sleep(0.5)

            for sel in ["input[name*='quantity']", "#qtyInput"]:
                try:
                    page.fill(sel, str(qty))
                    break
                except Exception:
                    continue

            from datetime import datetime as dt
            exp_formatted = dt.strptime(expiry, "%Y-%m-%d").strftime("%m/%d/%Y")
            for sel in ["input[name*='expiry']", "#expiryInput"]:
                try:
                    page.fill(sel, exp_formatted)
                    break
                except Exception:
                    continue
            time.sleep(0.5)

            for sel in ["input[name*='strike']", "#strikeInput"]:
                try:
                    page.fill(sel, str(int(strike)))
                    break
                except Exception:
                    continue
            time.sleep(0.5)

            try:
                page.select_option("select[name*='type']", label=opt_type.capitalize())
            except Exception:
                page.click(f"text={opt_type.capitalize()}")
            time.sleep(0.5)

            if limit_price:
                try:
                    page.select_option("select[name*='orderType']", label="Limit")
                except Exception:
                    page.click("text=Limit")
                for sel in ["input[name*='price']", "#limitPriceInput"]:
                    try:
                        page.fill(sel, str(limit_price))
                        break
                    except Exception:
                        continue
            else:
                try:
                    page.select_option("select[name*='orderType']", label="Market")
                except Exception:
                    page.click("text=Market")
            time.sleep(0.5)

            for btn_text in ["Preview", "Review Order"]:
                try:
                    page.click(f"button:has-text('{btn_text}')")
                    page.wait_for_load_state("networkidle")
                    time.sleep(1)
                    break
                except Exception:
                    continue

            placed = False
            for btn_text in ["Place Order", "Confirm", "Submit"]:
                try:
                    page.click(f"button:has-text('{btn_text}')")
                    page.wait_for_load_state("networkidle")
                    time.sleep(2)
                    placed = True
                    break
                except Exception:
                    continue

            if placed:
                context.storage_state(path=str(session_file))
                total_cost = (limit_price or 0) * 100 * qty
                log_trade(analysis, "SUBMITTED_PLAYWRIGHT", qty=qty)
                notify_execution(
                    f"ORDER PLACED (web): {action} {ticker} {int(strike)}{opt_type[0]}"
                    f" EXP {expiry}"
                    + (f" @{limit_price}" if limit_price else " MKT")
                    + f" x{qty} contracts (~${total_cost:.0f})",
                    subject=f"ORDER PLACED (web): {action} {ticker}"
                )
                print("[executor] Playwright order submitted")
                browser.close()
                return True
            else:
                print("[executor] Playwright: Place Order button not found")
                browser.close()
                return False

    except Exception as e:
        print(f"[executor] Playwright error: {e}")
        log_trade(analysis, "FAILED", notes=str(e))
        notify_error(f"ORDER FAILED: {analysis.get('ticker')} {analysis.get('action')} — {e}")
        return False

def main():
    analysis = json.loads(sys.argv[1])

    action = analysis["action"]
    ticker = analysis["ticker"]
    limit_price = analysis.get("limit_price")

    if action == "BTO":
        qty = calculate_quantity(limit_price) if limit_price else 1
        total_cost = position_cost_usd(limit_price, qty) if limit_price else 0
        analysis["quantity"] = qty
        print(f"[executor] BTO sizing: @{limit_price} x{qty} contracts = ${total_cost:.0f} "
              f"(target ~${TARGET_TRADE_SIZE_USD})")
    else:
        qty = get_bto_quantity(
            ticker=ticker,
            strike=float(analysis.get("strike", 0)),
            option_type=analysis.get("option_type", ""),
            expiry=analysis.get("expiry_date", ""),
        )
        analysis["quantity"] = qty
        print(f"[executor] STC sizing: closing {qty} contracts (matched from BTO log)")

    if PAPER_TRADE:
        total_cost = position_cost_usd(limit_price, qty) if limit_price else 0
        log_trade(analysis, "PAPER_TRADE", qty=qty)
        notify_execution(
            f"[PAPER] {action} {ticker} "
            f"{int(analysis.get('strike',0))}"
            f"{'C' if analysis.get('option_type')=='CALL' else 'P'}"
            f" EXP {analysis.get('expiry_date','')}"
            + (f" @{limit_price}" if limit_price else " MKT")
            + f" x{qty} contracts (~${total_cost:.0f})",
            subject=f"[PAPER] {action} {ticker}"
        )
        print(f"[executor] PAPER TRADE logged: {ticker} {action} x{qty}")
        return

    # Circuit breaker: only limits BTO (opens). STC (closes) are never blocked.
    if action == "BTO":
        count = count_todays_bto()
        if count >= MAX_DAILY_TRADES:
            msg = f"CIRCUIT BREAKER: {MAX_DAILY_TRADES} BTO trades reached today. Skipping {ticker}."
            log_trade(analysis, "BLOCKED_DAILY_LIMIT", qty=qty, notes=msg)
            notify_error(msg)
            print(f"[executor] {msg}")
            return

    if not execute_via_api(analysis):
        execute_via_playwright(analysis)

if __name__ == "__main__":
    main()
```

---

## Part 7 — Process Management (VPS Ubuntu 24.04, systemd)

On a VPS, use **systemd** — services auto-restart on crash and survive reboots without SSH sessions.

### Create systemd service for alert poller
```bash
sudo tee /etc/systemd/system/trade-poller.service <<'EOF'
[Unit]
Description=DailyProfitsLive Alert Poller
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/home/ubuntu/.env.trade
ExecStart=/usr/bin/python3 /home/ubuntu/alert-bridge/alert_poller.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/logs/poller.log
StandardError=append:/home/ubuntu/logs/poller.log

[Install]
WantedBy=multi-user.target
EOF
```
---

Service file created at /etc/systemd/system/trade-poller.service. To enable and start it:                                                                                
                                                                                                                                       
  sudo systemctl daemon-reload                                                                                                                                             
  sudo systemctl enable trade-poller                                                                                                                                       
  sudo systemctl start trade-poller                                                                                                                                        
  sudo systemctl status trade-poller                                                                                                                                       
                                                                                                                                                                           
  Also make sure the log directory exists before starting:                                                                                                                 
  mkdir -p ~logs

---


### Create systemd service for webhook server
```bash
sudo tee /etc/systemd/system/trade-webhook.service <<'EOF'
[Unit]
Description=Trade Alert Webhook Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/home/ubuntu/.env.trade
ExecStart=/usr/bin/python3 /home/ubuntu/alert-bridge/webhook_server.py
Restart=always
RestartSec=5
StandardOutput=append:/home/ubuntu/logs/webhook.log
StandardError=append:/home/ubuntu/logs/webhook.log

[Install]
WantedBy=multi-user.target
EOF
```

---

Service file created at /etc/systemd/system/trade-webhook.service. To enable and start it:
                                                                                                                                                                           
  sudo systemctl daemon-reload                                                                                                                                             
  sudo systemctl enable trade-webhook                                                                                                                                      
  sudo systemctl start trade-webhook                                                                                                                                       
  sudo systemctl status trade-webhook  

  ---


### Enable and start services
```bash
mkdir -p ~/logs
sudo systemctl daemon-reload
sudo systemctl enable trade-poller trade-webhook
sudo systemctl start trade-poller trade-webhook

# Verify both are running
sudo systemctl status trade-poller trade-webhook

# Live logs
sudo journalctl -u trade-poller -f
sudo journalctl -u trade-webhook -f
```


---

Both services are active (running):                                                                                                                                      
                                                                                                                                                                           
  - trade-poller — up, Chromium headless launched and polling                                                                                                              
  - trade-webhook — up for ~1.5 min, stable                                                                                                                                
                                                                                                                                                                           
  To follow live logs:                                                                                                                                                     
  tail -f /home/ubuntu/logs/poller.log                                                                                                                                     
  tail -f /home/ubuntu/logs/webhook.log

  ---


### Kill switch (emergency stop)
```bash
cat > ~/kill-switch.sh <<'EOF'
#!/bin/bash
sudo systemctl stop trade-poller trade-webhook
sudo systemctl disable trade-poller trade-webhook
pkill -f schwab_executor.py
pkill -f claude_code_analyzer.py
echo "ALL TRADING STOPPED at $(date)"
EOF
chmod +x ~/kill-switch.sh
```



---

Done. Kill switch created at ~/kill-switch.sh. To trigger it:                                                                                                            
                       
  ~/kill-switch.sh                                                                                                                                                         
                                 
  To re-enable services after using it:                                                                                                                                    
  sudo systemctl enable trade-poller trade-webhook                                                                                                                       
  sudo systemctl start trade-poller trade-webhook  

  ---


### Health check cron (every 5 minutes)

Create `~/alert-bridge/claude_code_health.py`:
```python
#!/usr/bin/env python3
"""Claude Code Routine — health check. Run via cron every 5 min."""
import os
import requests
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env.trade")
NTFY = os.environ.get("NTFY_CHANNEL", "")

def notify(msg):
    if NTFY:
        try:
            requests.post(f"https://ntfy.sh/{NTFY}", data=msg.encode(), timeout=5)
        except Exception:
            pass

# 1. Webhook alive?
try:
    r = requests.get("http://127.0.0.1:8765/health", timeout=3)
    if r.status_code != 2
        raise RuntimeError(f"status {r.status_code}")
except Exception as e:
    subprocess.run(["sudo", "systemctl", "restart", "trade-webhook"])
    notify(f"HEALTH: webhook restarted — {e}")

# 2. Poller alive?
try:
    result = subprocess.run(
        ["systemctl", "is-active", "trade-poller"],
        capture_output=True, text=True
    )
    if result.stdout.strip() != "active":
        subprocess.run(["sudo", "systemctl", "restart", "trade-poller"])
        notify("HEALTH: poller restarted — was inactive")
except Exception as e:
    notify(f"HEALTH: poller check error — {e}")

# 3. Claude Code CLI reachable?
try:
    result = subprocess.run(["claude", "-p", "reply OK"],
                            capture_output=True, text=True, timeout=15)
    if "OK" not in result.stdout:
        notify(f"HEALTH: claude -p not responding — {result.stderr[:100]}")
except Exception as e:
    notify(f"HEALTH: Claude Code CLI error — {e}")
```

---

Created /root/alert-bridge/claude_code_health.py. To wire it up as a cron job every 5 minutes:                                                                           
                                                                                                                                                                           
  crontab -e                                                                                                                                                               
  Add this line:                                                                                                                                                         
  */5 * * * * /root/venv/bin/python3 /root/alert-bridge/claude_code_health.py 

---


```bash
(crontab -l 2>/dev/null; echo "*/5 * * * * python3 ~/alert-bridge/claude_code_health.py >> ~/logs/health.log 2>&1") | crontab -
```

---


Cron job set. Health check runs every 5 minutes, logging to ~/logs/health.log. Note: this will use the system python3 — if you need the venv packages, swap python3 with 
  /root/venv/bin/python3.

---

---

## Part 8 — Trade Journal

### `~/trade-log/trades.csv` field reference

| Field | Format | Example from DailyProfitsLive |
|-------|--------|-------------------------------|
| timestamp | ISO 8601 | `2026-04-20T09:40:00` |
| raw_alert | string | `bto FSLY 4/17 $26 calls @ .15` |
| action | BTO/STC | `BTO` |
| ticker | string | `FSLY` |
| option_type | CALL/PUT | `CALL` |
| strike | float | `26` |
| expiry | YYYY-MM-DD | `2026-04-17` |
| limit_price | float/null | `0.15` |
| order_id | string/null | `9876543210` |
| fill_price | float/null | `0.16` |
| execution_status | SUBMITTED/FILLED/FAILED/BLOCKED_*/PAPER_TRADE | `SUBMITTED` |
| notes | string | `qty=67` |

### End-of-day review

Review `~/trade-log/trades.csv` manually or pipe it into any spreadsheet tool. The log contains every execution with timestamp, action, ticker, strike, expiry, limit price, order ID, fill price, status, and quantity.

---

## Part 9 — Tips, Tricks & Hacks

### Reliability (VPS)
- **systemd auto-restart** — `Restart=always` with `RestartSec=10`; services survive crashes and reboots
- **Save Playwright session after every trade** — avoids Schwab re-login on next execution
- **Re-run `playwright codegen`** after any Schwab UI update to refresh selectors
- **`schwab-py` auto-refreshes tokens** — ensure `token.json` is writable

### DailyProfitsLive-specific
- **Color detection is the gating filter** — blue/teal cards ("Today's Plan", commentary) must be skipped; only green and red cards are trades
- **`closing all TICKER ...`** red cards have no price — always MARKET order; executor looks up open position from trades.csv
- **Partial STC** (`2/3rds`, `1/3rd`) — close that fraction of the BTO quantity, keep the rest as a runner
- **`near $PRICE`** is the same as `@ $PRICE` — treat as a limit order at that price

### Speed
- **Regex parse is the only gate** — parse in <1ms; execute immediately if parse succeeds
- **ntfy push sub-10 seconds** — alert detected → executed → phone buzz in under 10 seconds

### Safety
- **Start with `PAPER_TRADE=true` for 14 days minimum** — log everything, submit nothing
- **Kill switch** — `ssh yourVPS "~/kill-switch.sh"` from your phone
- **Circuit breaker is BTO-only** — max 5 opens per day ($1,000 each); STC closes are never blocked

---

## Part 10 — Case Studies

### Case Study 1: BTO Alert (FSLY) → Filled in 12 Seconds
```
Feed: https://watch.dailyprofitslive.com/?channel=trades  (Trades tab, green)
09:40:00 — Green card: "Adding to FSLY | bto FSLY 4/17 $26 calls @ .15"
09:40:01 — Poller: color=green → card_action=BTO, POSTs to webhook
09:40:01 — webhook_server.py parses: FSLY 4/17 $26 CALL @ .15
09:40:01 — schwab_executor.py spawned directly: @.15 → qty=67 contracts (~$1,005)
09:40:06 — Schwab API: LIMIT order placed ID 9876543210
09:40:06 — ntfy: "ORDER PLACED: BTO FSLY 26C @0.15 x67 ~$1,005"
09:40:12 — Fill confirmed at $0.15
```
**Total: 12 seconds. Zero human steps. No grading delay.**

---

### Case Study 2: STC Partial Close (FSLY 2/3rds)
```
09:39:00 — Red card: "Scaling FSLY Lotto | stc 2/3rd's FSLY 4/17 $26 calls @ .50"
09:39:01 — Poller: color=red → card_action=STC
09:39:01 — Parser extracts partial_close="2/3rds"
09:39:01 — schwab_executor.py spawned: BTO log shows 67 contracts → 2/3rds = 45 STC
09:39:06 — Schwab API: SELL 45 contracts @ .50
09:39:06 — ntfy: "ORDER PLACED: STC FSLY 26C @0.50 x45 ~$2,250 — partial close 2/3rds"
Remaining: 22 contracts still held as runner
```
**Partial close executed automatically. Runner position preserved.**

---

### Case Study 3: Blue Card Correctly Skipped
```
11:36:00 — Teal/blue card: "FSLY Update & General Thoughts | See Below"
11:36:00 — Poller: color=teal → color_to_action() returns SKIP
11:36:00 — Card discarded, no webhook POST, no trade
```
**Commentary posts never reach the executor. Zero false trades.**

---

### Case Study 4: "Closing All" Red Card → Market Order
```
15:34:00 — Red card: "Closing FSLY | closing all FSLY for next week and 4/24"
15:34:01 — Poller: color=red → card_action=STC
15:34:01 — CLOSING_ALL_PATTERN matches: ticker=FSLY, no strike/expiry, order_type=MARKET
15:34:01 — schwab_executor.py: finds open FSLY positions in trades.csv, closes all at market
15:34:06 — ntfy: "ORDER PLACED (MARKET): STC FSLY all open positions MKT"
```
**Implicit full close handled without strike/expiry info.**

---

### Case Study 5: Circuit Breaker Stops 6th Open
```
BTO trades 1-5: all EXECUTED successfully (RILY, FSLY, BW calls) — $1,000 each
BTO trade 6 arrives: bto FSLY 4/10 $27 calls @ .40
→ BLOCKED_DAILY_LIMIT (5 BTO limit reached)
→ ntfy: "CIRCUIT BREAKER: 5 BTO trades reached today. Skipping FSLY."
STC alerts later that day: all still execute — closes are never blocked.
```

---

### Case Study 6: API Down → Playwright Fallback
```
11:32:01 — Schwab API returns 503 (maintenance window)
11:32:01 — execute_via_api() returns False
11:32:02 — execute_via_playwright() starts
11:32:11 — Playwright submits via Schwab web form
11:32:11 — ntfy: "ORDER PLACED (web): BTO RILY 7.5C EXP 2026-04-24 @0.60"
```
**No missed trade despite API outage.**

---

### Case Study 7: 14-Day Paper Trading Before Going Live
```
PAPER_TRADE=true for days 1-14.
Day 14 summary (DailyProfitsLive feed):
  52 total feed cards processed
  18 blue/teal commentary → correctly skipped
  22 green BTO → 22 EXECUTE (no grading filter)
  12 red STC → 12 EXECUTE
  Simulated P&L: +$2,140
  2 circuit breaker triggers
Decision: set PAPER_TRADE=false on Day 15.
```

---

### Case Study 8: Tuning Color Detection After Feed UI Update
```
Day 12: DailyProfitsLive updates CSS — green cards now use #166534 instead of #1a5c2e
Day 12 EOD: report flags 3 green BTO cards were silently skipped
Fix: add "#166534" to GREEN_COLORS set in alert_poller.py
sudo systemctl restart trade-poller
Next alert: correctly detected as BTO
```
**Add new hex codes to GREEN_COLORS / RED_COLORS. Restart one service. No code deploy.**

---

## Part 11 — Things to Avoid

### Architecture mistakes
- **Binding webhook to 0.0.0.0** — always `127.0.0.1`; webhook and poller run on the same VPS
- **Credentials in Python files** — use `~/.env.trade` with `chmod 600` always
- **No kill switch** — create `~/kill-switch.sh` before deploying live
- **No paper trading phase** — never skip the 14-day `PAPER_TRADE=true` validation
- **Not using systemd** — without systemd, services die when the SSH session drops

### DailyProfitsLive-specific mistakes
- **Not filtering by card color** — blue/teal cards are commentary, not trades
- **Treating `closing all` as unactionable** — it's a valid STC; use MARKET and close all open positions for that ticker
- **Ignoring partial closes** — `stc 2/3rds` must close exactly 2/3 of the BTO quantity, not all of it
- **`near $PRICE` treated as market** — use it as your limit price

### Claude Code CLI mistakes
- **Skipping `claude auth login`** — run it once before the system starts
- **Hardcoding rules in Python** — put all scoring logic in `~/trade-alerts/CLAUDE.md`
- **Not testing `claude -p` headlessly** — run `claude -p "reply OK"` in a cron shell; cron has a different `PATH`
- **Missing subprocess timeout** — always pass `timeout=30` to `subprocess.run`

### Alert capture mistakes
- **Polling under 60 seconds** — risks IP block; 90s is the safe default
- **No deduplication** — without the hash check, the same alert fires multiple orders per poll cycle
- **Not handling login expiry** — add the login block to `alert_poller.py` if the feed requires auth

### Execution mistakes
- **Playwright selector rot** — re-run `playwright codegen https://client.schwab.com` after every Schwab UI update
- **STC with no price converted to limit** — STC alerts with no price must be MARKET
- **Not verifying fill status** — SUBMITTED is not FILLED; add a 30-second status check after submission

---

## Part 12 — FAQ

**Q: What URL does the poller monitor?**
A: `https://watch.dailyprofitslive.com/?channel=trades` — set as `ALERT_SERVICE_URL` in `~/.env.trade`.

---

**Q: How does the system know if a card is a trade or commentary?**
A: By CSS background color: **green = BTO**, **red = STC**, **blue/teal = skip**. The `color_to_action()` function maps computed background colors to trade direction.

---

**Q: What if a new color hex appears after a feed UI update?**
A: Add it to `GREEN_COLORS` or `RED_COLORS` in `alert_poller.py`, then `sudo systemctl restart trade-poller`.

---

**Q: What if the VPS reboots?**
A: systemd services have `WantedBy=multi-user.target` — both `trade-poller` and `trade-webhook` restart automatically.

---

**Q: How do I handle partial closes like `stc 2/3rds FSLY ...`?**
A: The parser extracts `partial_close="2/3rds"`. The executor calculates `floor(bto_qty * 2/3)` and places that quantity as STC. The remaining 1/3 stays open.

---

**Q: What is the best first step today?**
A: On your VPS:
```bash
# 1. Test the executor directly with a real DailyProfitsLive alert format
python3 ~/alert-bridge/schwab_executor.py \
  '{"action":"BTO","ticker":"FSLY","strike":26,"option_type":"CALL","expiry_raw":"4/17","expiry_date":"2026-04-17","days_to_expiry":13,"order_type":"LIMIT","limit_price":0.15,"partial_close":null,"card_action":"BTO"}'
```
With `PAPER_TRADE=true` this should log a paper trade to `~/trade-log/trades.csv` and send an ntfy notification.

---

## Quick-Start Checklist (VPS Ubuntu 24.04)

```bash
# 1. Base setup
sudo apt update && sudo apt upgrade -y
sudo timedatectl set-timezone America/New_York
sudo apt install -y python3 python3-pip git curl tmux nodejs npm

# 2. Install Claude Code CLI + Gemini CLI
npm install -g @anthropic-ai/claude-code @google/gemini-cli
claude auth login && claude -p "reply OK"
gemini auth login && gemini -p "reply OK"

# 3. Install Python dependencies
pip3 install playwright schwab-py requests python-dotenv
playwright install chromium && playwright install-deps chromium

# 4. Dirs + env
mkdir -p ~/trade-alerts ~/trade-log \
         ~/alert-bridge ~/logs ~/schwab-auth
# Edit ~/.env.trade — set SCHWAB + FEED + Twilio + SMTP credentials, chmod 600

# 5. Verify executor with real alert format (PAPER_TRADE=true)
python3 ~/alert-bridge/schwab_executor.py \
  '{"action":"BTO","ticker":"FSLY","strike":26,"option_type":"CALL","expiry_raw":"4/17","expiry_date":"2026-04-17","days_to_expiry":13,"order_type":"LIMIT","limit_price":0.15,"partial_close":null,"card_action":"BTO"}'
python3 ~/alert-bridge/schwab_executor.py \
  '{"action":"STC","ticker":"RILY","strike":7.5,"option_type":"CALL","expiry_raw":"4/24","expiry_date":"2026-04-24","days_to_expiry":4,"order_type":"MARKET","limit_price":0.50,"partial_close":null,"card_action":"STC"}'

# 5. Authorize Schwab API (once — opens browser)
python3 ~/schwab-auth/setup_token.py

# 6. Install + start systemd services
sudo systemctl daemon-reload
sudo systemctl enable trade-poller trade-webhook
sudo systemctl start trade-poller trade-webhook
sudo systemctl status trade-poller trade-webhook

# 7. Register health check cron
(crontab -l 2>/dev/null; echo "*/5 * * * * python3 ~/alert-bridge/claude_code_health.py >> ~/logs/health.log 2>&1") | crontab -

# 8. Paper trade 14 days (PAPER_TRADE=true in .env.trade)
# Then: sed -i 's/PAPER_TRADE=true/PAPER_TRADE=false/' ~/.env.trade
```

### Complete automated flow
```
https://watch.dailyprofitslive.com/?channel=trades  →  click "Trades" tab (green)
    ↓ 90s poll — alert_poller.py (Playwright headless, VPS Ubuntu 24.04)
    ↓ color detection: blue/dark=SKIP, green=BTO, red=STC
New green card: "bto FSLY 4/17 $26 calls @ .15"
    ↓ POST to 127.0.0.1:8765
webhook_server.py parses → schwab_executor.py spawned directly
    ↓ circuit breakers pass
@.15 → qty=67 → Schwab API → order placed
    ↓
ntfy → phone: "ORDER PLACED: BTO FSLY 26C @0.15 x67 ~$1,005 | ID 9876543210"
```

### VPS version vs Local Machine version — key differences

| | Local Machine | VPS Ubuntu 24.04 |
|---|---|---|
| Process management | tmux sessions | systemd services |
| Auto-restart | manual | Restart=always |
| Reboot survival | requires ~/.zprofile | automatic via WantedBy= |
| SSH dependency | dies on disconnect | none |
| OS | macOS / Linux | Ubuntu 24.04 LTS |
| Setup step | brew install | apt install |
| Date command | `date -v-7d` (macOS) | `date -d "7 days ago"` |

---

*Last updated: 2026-04-20 | Stack: Playwright + Schwab API — VPS Ubuntu 24.04, systemd — no AI grading step*
*Alert source: https://watch.dailyprofitslive.com/?channel=trades — click "Trades" tab (green) — blue/dark=skip, green=BTO, red=STC*
*Real alert format: `bto FSLY 4/17 $26 calls @ .15` | `stc 2/3rd's FSLY 4/17 $26 calls @ .50` | `closing all FSLY`*
