# Project Nate — User Guide

> **nate.tps.pro** — Automated options trade execution bot that monitors Nate Bear's DailyProfitsLive alerts and executes matching option orders via Schwab.

---

## Table of Contents

1. [What Is This?](#what-is-this)
2. [How It Works](#how-it-works)
3. [Notification Flow](#notification-flow)
4. [Paper Trading vs Live Trading](#paper-trading-vs-live-trading)
5. [Managing the System](#managing-the-system)
6. [Schwab Token Management](#schwab-token-management)
7. [Backup & Recovery](#backup--recovery)
8. [Configuration Reference](#configuration-reference)
9. [Troubleshooting](#troubleshooting)

---

## What Is This?

Project Nate is a **trade execution automation system**. It:

1. Monitors Nate Bear's [DailyProfitsLive](https://watch.dailyprofitslive.com/) trade alerts in real time
2. Parses option trade details (ticker, strike, expiry, call/put, limit price)
3. Executes matching option orders on your Schwab account
4. Notifies you on Telegram and Discord at every step

It is **not** a scanner or screener — it executes trades based on Nate Bear's alerts.

---

## How It Works

```
┌──────────────┐     POST /webhook     ┌───────────────┐
│ alert_poller │ ────────────────────→ │ webhook_server │
│  (Playwright)│                       │    (HTTP :8765)│
│  DPL scraper │                       │   parse + spawn│
└──────────────┘                       └───────┬───────┘
                                               │ spawns
                                               ▼
                                        ┌──────────────┐
                                        │   executor   │
                                        │  Path A: API  │
                                        │  Path B: Web  │
                                        └──────┬───────┘
                                               │
                                    ┌──────────┴──────────┐
                                    ▼                     ▼
                              ┌──────────┐         ┌──────────┐
                              │ Telegram │         │ Discord  │
                              │  (3 chats)│         │ (1 chan) │
                              └──────────┘         └──────────┘
```

### Component Details

| Component | File | Role |
|-----------|------|------|
| **Alert Poller** | `alert-bridge/alert_poller.py` | Headless browser scrapes DPL Trades tab every 90s |
| **Webhook Server** | `alert-bridge/webhook_server.py` | HTTP server on `:8765`, parses alerts, spawns executor |
| **Executor** | `alert-bridge/schwab_executor.py` | Places option orders via Schwab API or Playwright |
| **Notifier** | `alert-bridge/notifier.py` | Sends Telegram + Discord messages |
| **Token Watchdog** | `token-expiry-watch.py` | Warns 2 days before Schwab token expires |

### Execution Paths

- **Path A (Schwab API):** Preferred. Uses `schwab-py` library to place orders directly.
- **Path B (Playwright):** Fallback. Headless Chrome clicks through Schwab's web interface.
- **Paper Mode:** No real orders. Logs simulated trades to `trade-log/trades.csv`.

---

## Notification Flow

Every trade generates a sequence of notifications on both Telegram and Discord:

| # | Event | Emoji | Subject | When |
|---|-------|-------|---------|------|
| 1 | Alert detected on DPL | 🔔 | `ALERT RECEIVED` | Instant |
| 2 | Order submitted to Schwab | ✅ | `ORDER PLACED` | Instant (includes order ID) |
| 3a | Order filled | ✅ | `FILLED` | After polling (max 30s) |
| 3b | Partial fill | ⚠️ | `PARTIAL FILL` | After polling |
| 3c | Order rejected | 🚨 | `ORDER REJECTED` | After polling |
| 3d | Still working | ⏳ | `WORKING` | After 30s timeout |
| 4 | Correction detected | 📝 | `CORRECTION` | No trade executed |
| 5 | Circuit breaker | 🚨 | `TRADE ERROR` | Daily limit hit |

### Paper Mode Notifications

Paper mode adds `[PAPER]` prefix:
- `[PAPER] ORDER PLACED: BTO SPY ...`
- `[PAPER] FILLED: BTO SPY ...` (2s later)

---

## Paper Trading vs Live Trading

### Paper Mode (Default)

Set by `PAPER_TRADE=true` in `.env.trade`.

- All orders are **simulated** — no real money
- Trades logged to `trade-log/trades.csv` with status `PAPER_TRADE`
- State tracked in `paper_state.json`
- Risk limits still enforced (daily trade cap, contract cap)

### Live Mode

Set by `PAPER_TRADE=false` in `.env.trade`.

- Orders sent to Schwab via API (or Playwright fallback)
- Real money, real fills
- Circuit breakers active:
  - `MAX_DAILY_TRADES` (default 5 BTO per day)
  - `MAX_CONTRACTS_PER_TRADE` (default 1)
  - `MAX_DOLLAR_PER_TRADE` (default $1,000)

> ⚠️ **Before going live:** Verify paper mode works for at least one full trading week. Verify all notifications arrive. Test the kill switch.

---

## Managing the System

### Service Control

Both services run as systemd units:

```bash
# Check status
systemctl status trade-poller trade-webhook

# View logs
journalctl -u trade-poller -f
journalctl -u trade-webhook -f

# Restart
systemctl restart trade-poller trade-webhook

# Stop (emergency)
systemctl stop trade-poller trade-webhook
```

### Kill Switch

Immediately halts all trading:

```bash
bash /root/nate.tps.pro/kill-switch.sh
```

This stops both services, kills any lingering Python processes, and logs the event.

### Viewing Trade History

```bash
# CSV trade log
cat /root/nate.tps.pro/trade-log/trades.csv

# Daily JSONL alerts
cat /root/nate.tps.pro/trade-log/20260502.jsonl
```

---

## Schwab Token Management

### How Tokens Work

| Token | Lifetime | Managed By |
|-------|----------|------------|
| Access Token | 30 minutes | Auto-refreshed by `schwabdev` on every API call |
| Refresh Token | 7 days | Kept alive by daily keepalive cron |

### Automated Cron Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| **Keepalive** | Daily 8am UTC | Pings SPY quote → refreshes access token → keeps refresh token alive |
| **Watchdog** | Daily 10am UTC | Checks expiry → alerts Telegram + Discord 2 days before expiration |

### Manual Re-Auth

If the refresh token expires (7+ days without API calls), you need to re-authenticate:

```bash
cd /root/nate.tps.pro
set -a && source .env.trade && set +a
.venv/bin/python schwab_client.py
```

This opens a browser, walks through Schwab OAuth, and saves new tokens.

### Monitoring

```bash
# Check token status
cat /root/nate.tps.pro/logs/token-keepalive.log
cat /root/nate.tps.pro/logs/token-watch.log
```

---

## Backup & Recovery

### Automatic Backups

Full VPS backup runs automatically on the **last day of every month at 1pm EST**:

```bash
# Manual backup
bash /root/backup-full.sh

# View existing backups
ls -lh /root/backup/

# Verify integrity
md5sum /root/backup/vps-backup-*.tar.gz
```

Backups include `/root` and `/etc`. Three most recent are kept; older ones auto-rotated.

### Restoring

```bash
cd /
tar -xzf /root/backup/vps-backup-YYYY-MM-DD_HHMMSS.tar.gz
```

---

## Configuration Reference

All configuration is in `.env.trade`. Key variables:

### Schwab API

| Variable | Description |
|----------|-------------|
| `SCHWAB_CLIENT_ID` | Schwab API app key |
| `SCHWAB_CLIENT_SECRET` | Schwab API app secret |
| `SCHWAB_CALLBACK_URL` | OAuth redirect (default: `https://127.0.0.1:8182`) |
| `SCHWAB_ACCOUNT_HASH` | Trading account hash |
| `TRADING_ACCOUNT` | Named account (e.g., `JOINT_TONA`) |

### Trading Controls

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_TRADE` | `true` | Paper mode on/off |
| `MAX_DAILY_TRADES` | `5` | Max BTO trades per day |
| `MAX_CONTRACTS_PER_TRADE` | `1` | Max contracts per order |
| `MAX_DOLLAR_PER_TRADE` | `1000` | Max notional per trade |
| `RISK_LIMIT_MODE` | `contracts` | `contracts` or `dollars` |

### DPL Feed

| Variable | Description |
|----------|-------------|
| `FEED_USER` | DailyProfitsLive username |
| `FEED_PASS` | DailyProfitsLive password |
| `POLL_INTERVAL_SECONDS` | Poll frequency (default: 90) |

### Notifications

| Variable | Description |
|----------|-------------|
| `NOTIFY_TELEGRAM` | `true`/`false` |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated chat IDs |
| `NOTIFY_DISCORD` | `true`/`false` |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_ID` | Channel ID for trade alerts |

---

## Troubleshooting

### Trade poller not running

```bash
systemctl status trade-poller
journalctl -u trade-poller -n 50
```

Common issues: DPL credentials expired, network issues, Playwright browser needs reinstall.

### Orders not executing

1. Check paper/live mode: `grep PAPER_TRADE .env.trade`
2. Check circuit breaker: look for `BLOCKED_DAILY_LIMIT` in `trade-log/trades.csv`
3. Check Schwab token: `cat logs/token-keepalive.log`

### Notifications not arriving

1. Check service logs: `journalctl -u trade-webhook -n 20`
2. Test Telegram: `curl "https://api.telegram.org/bot<TOKEN>/getMe"`
3. Test Discord: verify bot has permission in the channel

### Token expired

Run manual re-auth or check keepalive cron:
```bash
crontab -l | grep token
```
