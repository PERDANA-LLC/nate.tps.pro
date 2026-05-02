#!/usr/bin/env python3
"""
Executes option orders via Schwab API (Path A).
Falls back to Playwright on Schwab website (Path B) if API fails.
Circuit breakers prevent runaway trading.
"""
import sys
import json
import os
import csv
import datetime
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifier import notify_execution, notify_error

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env.trade")

TOKEN_FILE = BASE_DIR / "schwab-auth" / "token.json"
LOG_FILE = BASE_DIR / "trade-log" / "trades.csv"
LOG_FILE.parent.mkdir(exist_ok=True)
PAPER_TRADE = os.environ.get("PAPER_TRADE", "true").lower() == "true"

# ── Risk & sizing from env ──────────────────────────────────────────
TARGET_TRADE_SIZE_USD = int(os.environ.get("MAX_DOLLAR_PER_TRADE", "1000"))
MAX_DAILY_TRADES = int(os.environ.get("MAX_DAILY_TRADES", "5"))
MAX_CONTRACTS_PER_TRADE = int(os.environ.get("MAX_CONTRACTS_PER_TRADE", "0"))
RISK_MODE = os.environ.get("RISK_LIMIT_MODE", "contracts")  # "contracts" or "dollars"


def get_account_hash() -> str:
    """Resolve the active trading account from named accounts."""
    account_name = os.environ.get("TRADING_ACCOUNT", "")
    if account_name and account_name in os.environ:
        return os.environ[account_name]
    # fallback: explicit hash
    return os.environ.get("SCHWAB_ACCOUNT_HASH", "")


def cap_quantity(qty: int) -> int:
    """Apply contract cap if RISK_LIMIT_MODE is 'contracts' and MAX_CONTRACTS_PER_TRADE > 0."""
    if RISK_MODE == "contracts" and MAX_CONTRACTS_PER_TRADE > 0:
        capped = min(qty, MAX_CONTRACTS_PER_TRADE)
        if capped < qty:
            print(f"[executor] Contract cap: {qty} → {capped} (max {MAX_CONTRACTS_PER_TRADE})")
        return capped
    return qty


def count_todays_bto() -> int:
    today = datetime.date.today().isoformat()
    if not LOG_FILE.exists():
        return 0
    count = 0
    with open(LOG_FILE) as f:
        for row in csv.DictReader(f):
            if (
                row.get("timestamp", "").startswith(today)
                and row.get("action") == "BTO"
                and row.get("execution_status") in ("SUBMITTED", "FILLED")
            ):
                count += 1
    return count


def calculate_quantity(limit_price: float) -> int:
    if not limit_price or limit_price <= 0:
        return 1
    return max(1, round(TARGET_TRADE_SIZE_USD / (limit_price * 100)))


def position_cost_usd(limit_price: float, qty: int) -> float:
    return limit_price * 100 * qty


def get_bto_quantity(
    ticker: str, strike: float, option_type: str, expiry: str
) -> int:
    if not LOG_FILE.exists():
        return 1
    with open(LOG_FILE) as f:
        rows = list(csv.DictReader(f))
    for row in reversed(rows):
        if (
            row.get("action") == "BTO"
            and row.get("ticker") == ticker
            and str(row.get("option_type")) == option_type
            and str(row.get("expiry")) == expiry
            and row.get("execution_status") in ("SUBMITTED", "FILLED", "PAPER_TRADE")
        ):
            try:
                notes = row.get("notes", "")
                for part in notes.split(","):
                    if part.strip().startswith("qty="):
                        return int(part.strip().split("=")[1])
            except Exception:
                pass
    return 1


def resolve_partial_qty(bto_qty: int, partial_close: str) -> int:
    """stc 2/3rds -> floor(bto_qty * 2/3)"""
    if not partial_close:
        return bto_qty
    m = __import__("re").match(r"(\d+)/(\d+)", partial_close.replace("rd", "").replace("th", "").replace("'s", ""))
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        return max(1, int(bto_qty * num / den))
    return bto_qty


def log_trade(
    analysis: dict,
    execution_status: str,
    order_id: str = "",
    fill_price=None,
    qty: int = 1,
    notes: str = "",
):
    fieldnames = [
        "timestamp", "raw_alert", "action", "ticker", "option_type",
        "strike", "expiry", "limit_price", "order_id", "fill_price",
        "execution_status", "notes",
    ]
    write_header = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "raw_alert": f"{analysis.get('action')} {analysis.get('ticker')} "
                f"{int(analysis.get('strike', 0))}"
                f"{'C' if analysis.get('option_type') == 'CALL' else 'P'} "
                f"EXP {analysis.get('expiry_date', '')}",
                "action": analysis.get("action"),
                "ticker": analysis.get("ticker"),
                "option_type": analysis.get("option_type"),
                "strike": analysis.get("strike"),
                "expiry": analysis.get("expiry_date"),
                "limit_price": analysis.get("limit_price"),
                "order_id": order_id,
                "fill_price": fill_price,
                "execution_status": execution_status,
                "notes": f"qty={qty}" + (f", {notes}" if notes else ""),
            }
        )


def execute_via_api(analysis: dict, qty: int) -> bool:
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
        account_hash = get_account_hash()
        if not account_hash:
            print("[executor] No account hash configured — cannot trade")
            return False

        ticker = analysis["ticker"]
        action = analysis["action"]
        strike = float(analysis["strike"])
        opt_type = analysis["option_type"]
        expiry = analysis["expiry_date"]
        limit_price = analysis.get("limit_price")

        exp = dt.strptime(expiry, "%Y-%m-%d")
        exp_str = exp.strftime("%y%m%d")
        opt_char = "C" if opt_type == "CALL" else "P"
        strike_str = f"{int(strike * 1000):08d}"
        option_symbol = f"{ticker:<6}{exp_str}{opt_char}{strike_str}"

        instruction = (
            o.OptionInstruction.BUY_TO_OPEN
            if action == "BTO"
            else o.OptionInstruction.SELL_TO_CLOSE
        )

        if limit_price:
            order = (
                o.OptionOrder()
                .set_order_type(o.OrderType.LIMIT)
                .set_session(o.Session.NORMAL)
                .set_duration(o.Duration.DAY)
                .set_price(limit_price)
                .add_option_leg(instruction, option_symbol, qty)
            )
        else:
            order = (
                o.OptionOrder()
                .set_order_type(o.OrderType.MARKET)
                .set_session(o.Session.NORMAL)
                .set_duration(o.Duration.DAY)
                .add_option_leg(instruction, option_symbol, qty)
            )

        resp = client.place_order(account_hash, order)
        order_id = resp.headers.get("location", "").split("/")[-1]
        total_cost = (limit_price or 0) * 100 * qty
        log_trade(analysis, "SUBMITTED", order_id=order_id, qty=qty)
        notify_execution(
            f"ORDER PLACED: {action} {ticker} {int(strike)}{opt_char} EXP {expiry}"
            + (f" @{limit_price}" if limit_price else " MKT")
            + f" x{qty} contracts (~${total_cost:.0f})"
            + f"\nID: {order_id}",
            subject=f"ORDER PLACED: {action} {ticker}",
        )
        print(f"[executor] API order placed. ID: {order_id} | qty={qty} | cost~${total_cost:.0f}")
        return True

    except Exception as e:
        print(f"[executor] API failed: {e}")
        return False


def execute_via_playwright(analysis: dict, qty: int) -> bool:
    """Path B: Playwright headless Chrome on Schwab website."""
    try:
        from playwright.sync_api import sync_playwright

        ticker = analysis["ticker"]
        action = analysis["action"]
        strike = analysis["strike"]
        opt_type = analysis["option_type"]
        expiry = analysis["expiry_date"]
        limit_price = analysis.get("limit_price")

        session_file = BASE_DIR / "schwab-auth" / "schwab_session.json"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(session_file) if session_file.exists() else None
            )
            page = context.new_page()
            page.goto(
                "https://client.schwab.com/Areas/Trade/Allinone/index.aspx",
                timeout=30000,
            )
            page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                print("[playwright] session expired — re-login needed")
                browser.close()
                return False

            page.click("text=Options", timeout=5000)
            time.sleep(0.5)

            for sel in [
                "input[placeholder*='Symbol']",
                "#symbolEntry",
                "input[name*='symbol']",
            ]:
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
                page.select_option(
                    "select[name*='type']", label=opt_type.capitalize()
                )
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
                    subject=f"ORDER PLACED (web): {action} {ticker}",
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
    partial_close = analysis.get("partial_close")

    if action == "BTO":
        qty = calculate_quantity(limit_price) if limit_price else 1
        qty = cap_quantity(qty)
        total_cost = position_cost_usd(limit_price, qty) if limit_price else 0
        analysis["quantity"] = qty
        print(
            f"[executor] BTO sizing: @{limit_price} x{qty} contracts = ${total_cost:.0f} "
            f"(target ~${TARGET_TRADE_SIZE_USD})"
        )
    else:
        bto_qty = get_bto_quantity(
            ticker=ticker,
            strike=float(analysis.get("strike", 0)),
            option_type=analysis.get("option_type", ""),
            expiry=analysis.get("expiry_date", ""),
        )
        qty = resolve_partial_qty(bto_qty, partial_close) if partial_close else bto_qty
        qty = cap_quantity(qty)
        analysis["quantity"] = qty
        label = f"partial {partial_close} -> " if partial_close else ""
        print(f"[executor] STC sizing: {label}closing {qty}/{bto_qty} contracts")

    if PAPER_TRADE:
        total_cost = position_cost_usd(limit_price, qty) if limit_price else 0
        log_trade(analysis, "PAPER_TRADE", qty=qty)
        notify_execution(
            f"[PAPER] {action} {ticker} "
            f"{int(analysis.get('strike', 0))}"
            f"{'C' if analysis.get('option_type') == 'CALL' else 'P'}"
            f" EXP {analysis.get('expiry_date', '')}"
            + (f" @{limit_price}" if limit_price else " MKT")
            + f" x{qty} contracts (~${total_cost:.0f})",
            subject=f"[PAPER] {action} {ticker}",
        )
        print(f"[executor] PAPER TRADE logged: {ticker} {action} x{qty}")
        return

    if action == "BTO":
        count = count_todays_bto()
        if count >= MAX_DAILY_TRADES:
            msg = (
                f"CIRCUIT BREAKER: {MAX_DAILY_TRADES} BTO trades reached today. "
                f"Skipping {ticker}."
            )
            log_trade(analysis, "BLOCKED_DAILY_LIMIT", qty=qty, notes=msg)
            notify_error(msg)
            print(f"[executor] {msg}")
            return

    if not execute_via_api(analysis, qty):
        execute_via_playwright(analysis, qty)


if __name__ == "__main__":
    main()
