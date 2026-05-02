# Project Nate — Developer Guide

> Internal architecture, codebase map, and development workflows for **nate.tps.pro**.

---

## Table of Contents

1. [Codebase Map](#codebase-map)
2. [Data Flow](#data-flow)
3. [Key Files Deep Dive](#key-files-deep-dive)
4. [Environment & Setup](#environment--setup)
5. [Adding Features](#adding-features)
6. [Testing](#testing)
7. [Deployment](#deployment)
8. [Cron Jobs](#cron-jobs)
9. [Debugging](#debugging)

---

## Codebase Map

```
nate.tps.pro/
├── alert-bridge/              # Core trade execution pipeline
│   ├── alert_poller.py        # Playwright scraper — DPL → webhook
│   ├── webhook_server.py      # HTTP server :8765 — parse + spawn executor
│   ├── schwab_executor.py     # Order execution (API + Playwright fallback)
│   ├── notifier.py            # Telegram + Discord notification module
│   ├── trade-poller.service   # systemd unit for alert_poller
│   └── trade-webhook.service  # systemd unit for webhook_server
│
├── schwab_client.py           # Schwab OAuth + token management (schwabdev)
├── tps_scan.py                # TPS scanner (screener, not execution)
├── token-expiry-watch.py      # Token expiry watchdog → notifications
│
├── paper_trader.py            # Simulated trading engine
├── broker_interface.py        # Paper/live dispatch layer
│
├── discord_bot.py             # Discord bot (/scan, /watchlist, etc.)
├── telegram_bot.py            # Telegram bot (same commands)
├── monitor.py                 # System health monitor
│
├── fmp_client.py              # Financial Modeling Prep API client
├── oauth_capture_server.py    # OAuth redirect capture (local HTTPS)
├── direct_token_exchange.py   # Manual token exchange helper
├── watchlist_builder.py       # Build watchlists from scans
│
├── .env.trade                 # Secrets + config (gitignored)
├── .env.example               # Template (tracked)
├── requirements.txt           # Python dependencies
├── kill-switch.sh             # Emergency stop script
│
├── tokens/                    # Schwab OAuth tokens + certs (gitignored)
├── schwab-auth/               # Browser session state (gitignored)
├── trade-log/                 # Trade CSV + JSONL logs
│   ├── trades.csv
│   └── YYYYMMDD.jsonl
├── logs/                      # Service + cron logs
├── plan_paper_trading/        # Design docs
└── paper_state.json           # Paper trading state
```

### Dependency Graph

```
alert_poller.py ──→ notifier.py (telegram_send, discord_send)
     │                    │
     │ POST /webhook      │
     ▼                    │
webhook_server.py ───────┘  (notify_alert)
     │
     │ spawns subprocess
     ▼
schwab_executor.py ──→ notifier.py  (notify_execution, notify_error)
     │
     ├── Path A: schwab-py (API)
     └── Path B: Playwright (web)
```

---

## Data Flow

### 1. Alert Detection

`alert_poller.py` runs a headless Chromium browser via Playwright. It:

1. Navigates to `https://watch.dailyprofitslive.com/`
2. Logs in with credentials from `FEED_USER` / `FEED_PASS`
3. Waits for trade cards to load
4. Detects card type by CSS class:
   - `bg-green-*` → **BTO** (Buy to Open)
   - `bg-red-*` → **STC** (Sell to Close)
5. Extracts trade details from card `innerText`
6. Hashes the alert text to deduplicate (avoids double-execution)
7. POSTs new alerts to `http://127.0.0.1:8765/webhook`
8. Sleeps for `POLL_INTERVAL_SECONDS` (default 90s)

### 2. Alert Parsing

`webhook_server.py` receives the POST and:

1. Logs raw alert to `trade-log/YYYYMMDD.jsonl`
2. Parses with regex `ALERT_PATTERN`:
   ```
   BTO SPY 5/16 $725 calls @3.50
   STC (2) AAPL 5/23 $230 puts near 4.20
   closing all TSLA
   ```
3. Resolves expiry dates (handles year rollover)
4. If `is_correction=true` → notify correction, **skip execution**
5. Otherwise → `notify_alert()` then spawn `schwab_executor.py` as subprocess

### 3. Order Execution

`schwab_executor.py` runs as a standalone process:

1. **Sizing:** Calculates contract quantity from `MAX_DOLLAR_PER_TRADE` / option price
2. **Cap:** Applies `MAX_CONTRACTS_PER_TRADE` limit
3. **Paper mode:** Logs to CSV, sends simulated fill notification
4. **Live mode:**
   - Path A: Schwab API (`schwab-py`) — `client.place_order()`
   - If Path A fails: Path B — Playwright on Schwab website
   - After placing: polls order status every 5s up to 6 times
   - Sends fill/reject/working notification

### 4. Circuit Breakers

Before live execution, the executor checks:

| Breaker | Env Var | Effect |
|---------|---------|--------|
| Daily BTO limit | `MAX_DAILY_TRADES=5` | Blocks after N BTO trades |
| Contract cap | `MAX_CONTRACTS_PER_TRADE=1` | Caps quantity per order |
| Dollar cap | `MAX_DOLLAR_PER_TRADE=1000` | Controls position sizing |
| Risk mode | `RISK_LIMIT_MODE=contracts` | How the cap is applied |

### 5. Notifications

All notifications flow through `notifier.py`:

| Function | Telegram | Discord | When |
|----------|----------|---------|------|
| `notify_alert()` | 🔔 HTML | ✅ Markdown | Alert detected |
| `notify_execution()` | ✅ HTML | ✅ Markdown | Order placed/filled |
| `notify_error()` | 🚨 HTML | 🚨 Markdown | Failures, breakers |
| `notify_correction()` | 📝 HTML | 📝 Markdown | Revised alerts |
| `notify_fill()` | Varies | Varies | Fill status (new) |

All Telegram messages use HTML parse mode. Discord uses Markdown. Character limits: Telegram 4096, Discord 2000.

---

## Key Files Deep Dive

### `alert-bridge/schwab_executor.py` (539 lines)

The execution engine. Key functions:

| Function | Purpose |
|----------|---------|
| `calculate_quantity(limit_price)` | Size based on dollar target |
| `cap_quantity(qty)` | Apply contract cap |
| `count_todays_bto()` | Daily trade counter |
| `execute_via_api(analysis, qty)` | Path A — Schwab API |
| `execute_via_playwright(analysis, qty)` | Path B — web automation |
| `poll_order_status(client, hash, id)` | Post-execution fill polling (new) |
| `notify_fill(analysis, qty, id, status)` | Fill notification dispatch (new) |
| `log_trade(analysis, status, ...)` | CSV trade logger |
| `main()` | Entry point — sizing → paper/live → execute |

### `alert-bridge/alert_poller.py`

The DPL scraper. Key patterns:

- CSS class detection for BTO/STC coloring
- SHA256 hashing for deduplication (prevents double-execution on re-scrape)
- Session persistence via `schwab-auth/feed_session.json`
- Weekend awareness: checks day-of-week to skip Saturday/Sunday

### `alert-bridge/webhook_server.py`

Simple `http.server` on port 8765. Endpoints:

| Path | Method | Purpose |
|------|--------|---------|
| `/webhook` | POST | Receive alert, parse, spawn executor |
| `/health` | GET | Health check → `{"status":"ok"}` |

### `schwab_client.py`

Schwab OAuth singleton (`schwabdev` library). Key behaviors:
- Self-signed cert generation for local HTTPS callback
- Headless OAuth flow (opens browser, captures redirect automatically)
- Token storage in `tokens/schwab_tokens.db` (SQLite)
- Auto-refresh of access tokens

### `notifier.py`

Shared notification module. Loaded by all components via:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifier import notify_execution, notify_error
```

Reads `.env.trade` directly (not via env inheritance).

---

## Environment & Setup

### Prerequisites

- Python 3.11+ with venv
- Playwright browsers: `playwright install chromium`
- Systemd (for service management)
- Schwab developer app (API key + secret)

### Quick Start

```bash
cd /root/nate.tps.pro
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Copy and edit config
cp .env.example .env.trade
# Fill in all required values

# Test Schwab connectivity
set -a && source .env.trade && set +a
.venv/bin/python schwab_client.py

# Install systemd services
cp alert-bridge/trade-poller.service /etc/systemd/system/
cp alert-bridge/trade-webhook.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trade-webhook trade-poller
```

### Environment Variables

All in `.env.trade`. See [USER_GUIDE.md](USER_GUIDE.md#configuration-reference) for full reference.

The executor and poller **must** have `.env.trade` in their working directory. They call `load_dotenv()` explicitly.

---

## Adding Features

### Adding a New Notification Type

1. Add a function to `notifier.py`:
   ```python
   def notify_new_event(msg: str):
       _telegram_send(f"🆕 <b>NEW EVENT</b>\n{msg}")
       _discord_send(f"🆕 **NEW EVENT**\n{msg}")
   ```
2. Import and call from the component that needs it.

### Adding a New Circuit Breaker

1. Add env var to `.env.trade`
2. Read in `schwab_executor.py` global section
3. Add check in `main()` or `execute_via_api()`
4. Call `notify_error()` and `log_trade(status="BLOCKED_...")`

### Adding a New Data Source

1. Create a new client file (like `fmp_client.py`)
2. Use `load_dotenv()` for credentials
3. Keep it stateless (no global singletons unless thread-safe)
4. Import into the scanner or poller as needed

### Modifying the Alert Parser

The regex is in `webhook_server.py`:
```python
ALERT_PATTERN = re.compile(
    r"(?P<action>bto|stc)\s+"
    r"(?:\((?P<quantity>\d+)\)\s+)?"
    r"(?:(?P<partial>[\d/]+(?:rd'?s?|th)?(?:\s+of)?)\s+)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<type>calls?|puts?)"
    r"(?:\s+(?:@|near)\s*\$?(?P<price>\d*\.?\d+))?",
    re.IGNORECASE,
)
```

Test with:
```bash
cd /root/nate.tps.pro
.venv/bin/python -c "
from alert-bridge.webhook_server import parse_alert
print(parse_alert('BTO SPY 5/16 \$725 calls @3.50'))
"
```

---

## Testing

### Test Schwab Connectivity

```bash
cd /root/nate.tps.pro
set -a && source .env.trade && set +a
.venv/bin/python schwab_client.py
# Should print: ✓ SPY quote: $XXX.XX
```

### Test Alert Parsing

```bash
curl -X POST http://127.0.0.1:8765/webhook \
  -H "Content-Type: application/json" \
  -d '{"raw_alert":"BTO SPY 5/16 $725 calls @3.50","card_action":"BTO"}'
```

### Test Paper Execution

```bash
cd /root/nate.tps.pro/alert-bridge
set -a && source ../.env.trade && set +a
.venv/bin/python -c "
import json
from schwab_executor import main
import sys
sys.argv = ['test', json.dumps({
    'action': 'BTO', 'ticker': 'SPY', 'strike': 725,
    'option_type': 'CALL', 'expiry_date': '2026-05-16',
    'limit_price': 3.50
})]
main()
"
# Check paper_state.json and trade-log/trades.csv
```

### Test Notifications

```bash
cd /root/nate.tps.pro
.venv/bin/python -c "
import sys; sys.path.insert(0, 'alert-bridge')
from notifier import _telegram_send, _discord_send
_telegram_send('🧪 Test notification from Project Nate')
_discord_send('🧪 Test notification from Project Nate')
"
```

---

## Deployment

### Systemd Services

Both services are enabled and survive reboots:

```bash
systemctl status trade-poller trade-webhook
```

Service dependency chain:
```
trade-webhook.service (no deps)
         ↑
trade-poller.service (Requires= trade-webhook.service)
```

Startup order matters: webhook must be running before poller.

### Updating Code

The webhook spawns `schwab_executor.py` as a fresh subprocess on every alert — no restart needed for executor changes.

For poller or webhook changes:

```bash
systemctl restart trade-poller trade-webhook
```

### Health Checks

```bash
# Webhook health
curl http://127.0.0.1:8765/health

# Service status
systemctl is-active trade-poller trade-webhook

# Token status
.venv/bin/python token-expiry-watch.py
```

---

## Cron Jobs

| Job | Schedule | Script | Log |
|-----|----------|--------|-----|
| Token keepalive | Daily 8am UTC | `schwab_client.py` | `logs/token-keepalive.log` |
| Token watchdog | Daily 10am UTC | `token-expiry-watch.py` | `logs/token-watch.log` |
| VPS backup | Last day of month, 1pm EST | `/root/backup-full.sh` | `/root/backup/backup-cron.log` |

View all:
```bash
crontab -l
```

---

## Debugging

### View Service Logs

```bash
# Real-time
journalctl -u trade-poller -f
journalctl -u trade-webhook -f

# Last 100 lines
journalctl -u trade-poller -n 100 --no-pager
```

### Check Trade Logs

```bash
# Today's alerts
cat trade-log/$(date +%Y%m%d).jsonl | jq .

# Trade history
column -t -s, trade-log/trades.csv | head -20
```

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Poller exits immediately | DPL credentials wrong | Check `FEED_USER`/`FEED_PASS` in `.env.trade` |
| Poller can't find webhook | Webhook not running | `systemctl start trade-webhook` |
| Executor exits silently | Missing env var | Check `SCHWAB_CLIENT_ID` etc. in `.env.trade` |
| API orders fail | Token expired or wrong account | Re-run `schwab_client.py`, check `SCHWAB_ACCOUNT_HASH` |
| Playwright falls back | API unavailable or token issue | Check executor logs for "API failed" errors |
| Double notifications | Duplicate alert hashing failed | Check `trade-log/seen_hashes.txt` |
| "file changed as we read it" | Live filesystem writes during backup | Normal, not an error — backup continues |
