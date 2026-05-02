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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifier import notify_alert

BASE_DIR = Path(__file__).resolve().parent.parent
ALERT_DIR = BASE_DIR / "trade-log"
ALERT_DIR.mkdir(exist_ok=True)

ALERT_PATTERN = re.compile(
    r"(?P<action>bto|stc)\s+"
    r"(?:\((?P<quantity>\d+)\)\s+)?"  # optional quantity like "(2)"
    r"(?:(?P<partial>[\d/]+(?:rd'?s?|th)?(?:\s+of)?)\s+)?"
    r"(?P<ticker>[A-Z]+)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<type>calls?|puts?)"
    r"(?:\s+(?:@|near)\s*\$?(?P<price>\d*\.?\d+))?",
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

    m2 = CLOSING_ALL_PATTERN.search(text_clean)
    if m2 and card_action == "STC":
        ticker = m2.group("ticker").upper()
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

        today = datetime.date.today().strftime("%Y%m%d")
        with open(ALERT_DIR / f"{today}.jsonl", "a") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "raw": raw,
                        "card_action": card_action,
                        "is_correction": is_correction,
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

        if is_correction:
            # Correction: log + notify, but do NOT execute
            from notifier import notify_correction
            notify_correction(parsed.ticker, raw)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "correction_logged", "parsed": asdict(parsed)}).encode())
            return

        notify_alert(raw, card_action)

        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "schwab_executor.py"),
                json.dumps(asdict(parsed)),
            ]
        )

        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"status": "queued", "parsed": asdict(parsed)}).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8765), AlertHandler)
    print("Webhook server on 127.0.0.1:8765")
    server.serve_forever()
