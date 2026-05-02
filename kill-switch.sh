#!/bin/bash
# Emergency kill switch for trade alert system
sudo systemctl stop trade-poller trade-webhook 2>/dev/null
sudo systemctl disable trade-poller trade-webhook 2>/dev/null
pkill -f schwab_executor.py 2>/dev/null
echo "ALL TRADING STOPPED at $(date)"
