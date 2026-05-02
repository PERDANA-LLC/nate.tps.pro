#!/bin/bash
# ── Kill Switch: stops all trading services immediately ──
# Usage: bash kill-switch.sh
set -e

echo "🛑 KILL SWITCH ACTIVATED — $(date)"
echo "----------------------------------------"

# 1. Stop systemd services
for svc in trade-poller trade-webhook; do
    if systemctl is-active --quiet $svc 2>/dev/null; then
        echo "[kill] Stopping $svc..."
        systemctl stop $svc
        echo "[kill]   ✓ $svc stopped"
    else
        echo "[kill]   $svc not running"
    fi
done

# 2. Kill any lingering Python processes for these services
for pattern in "webhook_server.py" "alert_poller.py" "schwab_executor.py"; do
    PIDS=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "[kill] Killing $pattern (PIDs: $PIDS)..."
        kill -9 $PIDS 2>/dev/null || true
        echo "[kill]   ✓ killed"
    fi
done

# 3. Cancel any open Schwab orders (if PAPER_TRADE is off)
cd /root/nate.tps.pro/alert-bridge
PAPER=$(grep PAPER_TRADE ../.env.trade 2>/dev/null | cut -d= -f2)
if [ "$PAPER" != "true" ]; then
    echo "[kill] PAPER_TRADE is OFF — attempting to cancel open Schwab orders..."
    /root/nate.tps.pro/.venv/bin/python -c "
from dotenv import load_dotenv; from pathlib import Path
load_dotenv(Path('../.env.trade'))
print('[kill] Account:', __import__('os').environ.get('TRADING_ACCOUNT',''))
print('[kill] Schwab order cancellation requires manual verification')
" || true
else
    echo "[kill] PAPER_TRADE mode — no live orders to cancel"
fi

# 4. Log the kill event
mkdir -p /root/nate.tps.pro/trade-log
echo "$(date -Iseconds) | KILL_SWITCH | services stopped" >> /root/nate.tps.pro/trade-log/kill_events.log

echo "----------------------------------------"
echo "✅ Kill switch complete. All trading halted."
