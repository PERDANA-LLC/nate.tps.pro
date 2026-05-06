#!/usr/bin/env python3
"""
Receives alerts from poller. Parses DailyProfitsLive format.
Spawns schwab_executor.py directly — no grading step.
Sends alert notification via notifier.py.
"""
import json
import re
import datetime
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
import sys

sys.path.insert(0, str(Path.home() / ".hermes" / "shared"))
from notifier import notify_alert

BASE_DIR = Path(__file__).resolve().parent.parent
ALERT_DIR = BASE_DIR / "trade-log"
ALERT_DIR.mkdir(exist_ok=True)

ALERT_PATTERN = re.compile(
    r"(?P<action>bto|stc)\s+"
    r"(?:\((?P<quantity>\d+)\)\s+)?"  # optional quantity like "(2)"
    r"(?:\((?:"
    r"(?P<partial_paren>[\d/]+|half|quarter|third)(?:rd'?s?|th)?(?:\s+of)?"
    r")\)\s+)?"
    r"(?:(?P<partial>[\d/]+|half|quarter|third)(?:rd'?s?|th)?(?:\s+of)?\s+)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<type>calls?|puts?)"
    r"(?:\s+(?:@|near)\s*\$?(?P<price>\d*\.?\d+))?",
    re.IGNORECASE,
)

# STC alerts often omit strike and type — price-only format
# e.g. "stc (half) TWLO 5/15 $4.10"
STC_PATTERN = re.compile(
    r"stc\s+"
    r"(?:\((?P<partial>[\d/]+|half|quarter|third)(?:rd'?s?|th)?(?:\s+of)?\)\s+)?"
    r"(?:(?P<partial2>[\d/]+|half|quarter|third)(?:rd'?s?|th)?(?:\s+of)?\s+)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$?(?P<price>\d+\.?\d*)",
    re.IGNORECASE,
)

CLOSING_ALL_PATTERN = re.compile(
    r"closing\s+all\s+(?P<ticker>[A-Z]+)", re.IGNORECASE
)


@dataclass
class ParsedAlert:
    action: str
    ticker: str
    strike: float
    option_type: str
    expiry_raw: str
    expiry_date: str
    days_to_expiry: int
    order_type: str
    limit_price: Optional[float]
    partial_close: Optional[str]
    card_action: str


def resolve_expiry(raw: str):
    today = datetime.date.today()
    month, day = map(int, raw.split("/"))
    candidate = datetime.date(today.year, month, day)
    # Only roll forward if it's more than 30 days in the past
    # (trades from yesterday or earlier this week stay in current year)
    if candidate < today - datetime.timedelta(days=30):
        candidate = datetime.date(today.year + 1, month, day)
    return candidate.isoformat(), (candidate - today).days


def parse_alert(text: str, card_action: str = "") -> Optional[ParsedAlert]:
    text_clean = text.strip()

    m = ALERT_PATTERN.search(text_clean)
    if m:
        action = m.group("action").upper()
        expiry_date, dte = resolve_expiry(m.group("expiry"))
        price_str = m.group("price")
        partial = _clean_partial(m.group("partial") or m.group("partial_paren"))
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

    # Try STC price-only format: "stc (half) TWLO 5/15 $4.10"
    m2 = STC_PATTERN.search(text_clean)
    if m2 and card_action == "STC":
        ticker = m2.group("ticker").upper()
        expiry_raw = m2.group("expiry")
        expiry_date, dte = resolve_expiry(expiry_raw)
        limit_price = float(m2.group("price")) if m2.group("price") else None
        partial = _clean_partial(m2.group("partial") or m2.group("partial2"))

        # Look up strike and option_type from open positions in trade journal
        strike, option_type = _lookup_position(ticker, expiry_date)
        order_type = "LIMIT" if limit_price else "MARKET"
        return ParsedAlert(
            action="STC",
            ticker=ticker,
            strike=strike,
            option_type=option_type or "CALL",
            expiry_raw=expiry_raw,
            expiry_date=expiry_date,
            days_to_expiry=dte,
            order_type=order_type,
            limit_price=limit_price,
            partial_close=partial,
            card_action="STC",
        )

    m3 = CLOSING_ALL_PATTERN.search(text_clean)
    if m3 and card_action == "STC":
        ticker = m3.group("ticker").upper()
        today = datetime.date.today()
        return ParsedAlert(
            action="STC",
            ticker=ticker,
            strike=0.0,
            option_type="CALL",
            expiry_raw="",
            expiry_date=today.isoformat(),
            days_to_expiry=0,
            order_type="MARKET",
            limit_price=None,
            partial_close=None,
            card_action="STC",
        )

    return None


def _clean_partial(val: str | None) -> str | None:
    """Clean a partial-close string. Handles 'half', '1/2', '3rd of', etc."""
    if not val:
        return None
    v = val.strip()
    # Remove trailing " of" but NOT via rstrip("of") which eats letters
    if v.endswith(" of"):
        v = v[:-3]
    # Normalize
    v = v.strip().lower()
    if v == "half":
        v = "1/2"
    elif v == "quarter":
        v = "1/4"
    elif v == "third":
        v = "1/3"
    return v or None


def _lookup_position(ticker: str, expiry_date: str):
    """Find strike and option_type for an open position from trades.csv.
    Returns (strike, option_type) or (0.0, None) if not found."""
    import csv as _csv
    journal = BASE_DIR / "trade-log" / "trades.csv"
    if not journal.exists():
        return 0.0, None
    try:
        with open(journal) as f:
            for row in _csv.DictReader(f):
                if (
                    row.get("ticker", "").upper() == ticker
                    and row.get("expiry", "") == expiry_date
                    and row.get("action") == "BTO"
                ):
                    return float(row.get("strike", 0)), row.get("option_type", "CALL")
    except Exception:
        pass
    return 0.0, None


def _process_correction(parsed, raw: str):
    """Background: notify about correction — no execution."""
    from notifier import notify_correction
    notify_correction(parsed.ticker, raw)


def _process_alert(parsed, raw: str, card_action: str):
    """Background: notify + spawn executor. Never blocks the HTTP response."""
    notify_alert(raw, card_action)
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "schwab_executor.py"),
            json.dumps(asdict(parsed)),
        ]
    )


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
        is_correction = body.get("is_correction", False)
        trade_date = body.get("trade_date", "")

        today = datetime.date.today().strftime("%Y%m%d")
        with open(ALERT_DIR / f"{today}.jsonl", "a") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "raw": raw,
                        "card_action": card_action,
                        "is_correction": is_correction,
                        "trade_date": trade_date,
                    }
                )
                + "\n"
            )

        parsed = parse_alert(raw, card_action)
        if not parsed:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"parse_failed"}')
            return

        # ── Respond INSTANTLY — poller has a 5s timeout ──
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(json.dumps({"status": "received", "parsed": asdict(parsed)}).encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True  # Close socket when do_POST returns

        # ── Heavy work runs in background — NEVER block the response ──
        if is_correction:
            threading.Thread(
                target=_process_correction, args=(parsed, raw), daemon=True
            ).start()
        else:
            threading.Thread(
                target=_process_alert, args=(parsed, raw, card_action), daemon=True
            ).start()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8765), AlertHandler)
    print("Webhook server on 127.0.0.1:8765")
    server.serve_forever()
