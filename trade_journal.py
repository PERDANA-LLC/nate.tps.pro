#!/usr/bin/env python3
"""
Trade Journal — unified view of all executed trades with P&L and cumulative balance.

Reads trade-log/trades.csv and builds:
  - Per-trade P&L (STC exit price vs matching BTO entry)
  - Cumulative balance (starts at $10,000 paper capital)
  - Open positions summary

Usage:
  .venv/bin/python trade_journal.py              # full journal
  .venv/bin/python trade_journal.py --open        # open positions only
  .venv/bin/python trade_journal.py --csv         # export as CSV
  .venv/bin/python trade_journal.py --summary     # summary stats
"""
import csv
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

BASE = Path(__file__).resolve().parent
CSV_PATH = BASE / "trade-log" / "trades.csv"
STARTING_CAPITAL = 10000.0
OPTION_MULTIPLIER = 100


@dataclass
class Trade:
    timestamp: str
    action: str          # BTO or STC
    ticker: str
    option_type: str     # CALL or PUT
    strike: float
    expiry: str
    limit_price: Optional[float]
    fill_price: Optional[float]
    execution_status: str
    qty: int
    order_id: str
    position_key: str = ""   # ticker_strike_type_expiry

    @property
    def effective_price(self) -> float:
        """Use fill_price if available (live), otherwise limit_price (paper)."""
        if self.fill_price is not None and self.fill_price > 0:
            return self.fill_price
        return self.limit_price or 0.0

    @property
    def cost(self) -> float:
        return self.effective_price * OPTION_MULTIPLIER * self.qty


@dataclass
class JournalEntry:
    """One row in the journal — either a standalone BTO or a matched BTO+STC pair."""
    opened: str          # timestamp
    ticker: str
    strike: float
    opt_type: str
    expiry: str
    action: str          # BTO or STC (for open positions: BTO)
    qty: int
    entry_price: float
    entry_cost: float
    exit_price: Optional[float] = None
    exit_cost: Optional[float] = None
    closed: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    balance: float = 0.0


def load_trades() -> list[Trade]:
    if not CSV_PATH.exists():
        return []

    trades = []
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = row.get("action", "").strip()
            if action not in ("BTO", "STC"):
                continue

            status = row.get("execution_status", "").strip()
            # Skip non-trade status rows (corrections, circuit breakers etc)
            if status in ("BLOCKED_DAILY_LIMIT", "FAILED", "REJECTED", "CANCELED", "EXPIRED", "", "CORRECTION"):
                # Still include if it's a PAPER_TRADE or FILLED etc
                if status not in ("PAPER_TRADE", "SUBMITTED", "FILLED", "PARTIAL_FILL", "SUBMITTED_PLAYWRIGHT"):
                    continue

            # Parse qty from notes field: "qty=5" or "qty=5, partial 3/5"
            notes = row.get("notes", "")
            qty = 1
            for part in notes.split(","):
                part = part.strip()
                if part.startswith("qty="):
                    try:
                        qty = int(part.split("=")[1])
                    except ValueError:
                        pass
                    break

            # Parse prices
            lp = row.get("limit_price", "").strip()
            fp = row.get("fill_price", "").strip()
            limit_price = float(lp) if lp else None
            fill_price = float(fp) if fp else None

            strike = row.get("strike", "0").strip()
            try:
                strike_f = float(strike)
            except ValueError:
                strike_f = 0.0

            trade = Trade(
                timestamp=row.get("timestamp", ""),
                action=action,
                ticker=row.get("ticker", "???"),
                option_type=row.get("option_type", "CALL"),
                strike=strike_f,
                expiry=row.get("expiry", ""),
                limit_price=limit_price,
                fill_price=fill_price,
                execution_status=status,
                qty=qty,
                order_id=row.get("order_id", ""),
            )
            trade.position_key = f"{trade.ticker}_{trade.strike}_{trade.option_type}_{trade.expiry}"
            trades.append(trade)

    return trades


def build_journal(trades: list[Trade]) -> list[JournalEntry]:
    """Match BTO → STC and calculate P&L. FIFO matching."""
    journal: list[JournalEntry] = []
    # open positions: position_key → list of (entry_price, qty_remaining, timestamp)
    open_positions: dict[str, list[dict]] = defaultdict(list)
    balance = STARTING_CAPITAL

    for t in sorted(trades, key=lambda x: x.timestamp):
        key = t.position_key

        if t.action == "BTO":
            cost = t.effective_price * OPTION_MULTIPLIER * t.qty
            balance -= cost

            # Track open position for later STC matching
            open_positions[key].append({
                "entry_price": t.effective_price,
                "qty": t.qty,
                "timestamp": t.timestamp,
            })

            journal.append(JournalEntry(
                opened=t.timestamp,
                ticker=t.ticker,
                strike=t.strike,
                opt_type=t.option_type,
                expiry=t.expiry,
                action="BTO",
                qty=t.qty,
                entry_price=t.effective_price,
                entry_cost=cost,
                balance=round(balance, 2),
            ))

        elif t.action == "STC":
            exit_price = t.effective_price
            exit_qty_remaining = t.qty
            total_exit = 0.0
            total_entry = 0.0
            matched_qty = 0
            matched_timestamps = []

            queue = open_positions.get(key, [])
            while queue and exit_qty_remaining > 0:
                lot = queue[0]
                match_qty = min(exit_qty_remaining, lot["qty"])
                total_entry += lot["entry_price"] * OPTION_MULTIPLIER * match_qty
                total_exit += exit_price * OPTION_MULTIPLIER * match_qty
                lot["qty"] -= match_qty
                exit_qty_remaining -= match_qty
                matched_qty += match_qty
                matched_timestamps.append(lot["timestamp"])
                if lot["qty"] <= 0:
                    queue.pop(0)

            pnl = total_exit - total_entry
            pnl_pct = (pnl / total_entry * 100) if total_entry > 0 else 0
            balance += total_exit

            if matched_qty == 0:
                print(f"[journal] ⚠ STC {t.ticker} {t.strike}{t.option_type[0]} "
                      f"has no matching BTO — skipped", file=sys.stderr)
                continue

            journal.append(JournalEntry(
                opened=matched_timestamps[0] if matched_timestamps else "?",
                ticker=t.ticker,
                strike=t.strike,
                opt_type=t.option_type,
                expiry=t.expiry,
                action="STC",
                qty=matched_qty,
                entry_price=total_entry / (OPTION_MULTIPLIER * matched_qty) if matched_qty else 0,
                entry_cost=total_entry,
                exit_price=exit_price,
                exit_cost=total_exit,
                closed=t.timestamp,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                balance=round(balance, 2),
            ))

            # If more exit qty than matching BTO (shouldn't happen but handle)
            if exit_qty_remaining > 0:
                print(f"[journal] ⚠ STC {t.ticker} {t.strike}{t.option_type[0]} "
                      f"has {exit_qty_remaining} unmatched qty (no BTO found)", file=sys.stderr)

    return journal


def get_open_positions(trades: list[Trade]) -> dict:
    """Return currently open positions (BTO not yet closed by STC)."""
    positions: dict[str, dict] = {}
    for t in sorted(trades, key=lambda x: x.timestamp):
        key = t.position_key
        if t.action == "BTO":
            if key not in positions:
                positions[key] = {"ticker": t.ticker, "strike": t.strike,
                                  "type": t.option_type, "expiry": t.expiry,
                                  "qty": 0, "total_cost": 0.0, "avg_price": 0.0,
                                  "opened": t.timestamp}
            positions[key]["qty"] += t.qty
            positions[key]["total_cost"] += t.effective_price * OPTION_MULTIPLIER * t.qty
        elif t.action == "STC":
            if key in positions:
                positions[key]["qty"] -= t.qty
                if positions[key]["qty"] <= 0:
                    del positions[key]

    for p in positions.values():
        if p["qty"] > 0:
            p["avg_price"] = round(p["total_cost"] / (OPTION_MULTIPLIER * p["qty"]), 2)

    return positions


def format_journal(journal: list[JournalEntry], mode: str = "full") -> str:
    lines = []

    if mode == "open":
        lines.append(f"{'Ticker':<6} {'Strike':>7} {'T':<1} {'Expiry':<10} {'Qty':>4} {'Avg$':>7} {'Cost':>8} {'Opened'}")
        lines.append("-" * 60)
        # This mode is handled separately in get_open_positions
        return "\n".join(lines)

    lines.append(f"{'Opened':<20} {'Ticker':<6} {'Str':>6} {'T':<1} {'Exp':<10} {'Act':<3} {'Qty':>4} {'Entry$':>8} {'Exit$':>8} {'P&L':>10} {'P&L%':>7} {'Balance':>11}")
    lines.append("-" * 125)

    for e in journal:
        opt_char = e.opt_type[0] if e.opt_type else "?"
        if e.action == "BTO":
            lines.append(
                f"{e.opened[:19]:<20} {e.ticker:<6} {e.strike:>6.0f} {opt_char:<1} {e.expiry:<10} "
                f"{e.action:<3} {e.qty:>4} {e.entry_price:>8.2f} {'':>8} {'':>10} {'':>7} ${e.balance:>10,.2f}"
            )
        else:
            pnl_str = f"${e.pnl:+,.2f}" if e.pnl else ""
            pnl_pct_str = f"{e.pnl_pct:+.1f}%" if e.pnl_pct else ""
            lines.append(
                f"{e.opened[:19]:<20} {e.ticker:<6} {e.strike:>6.0f} {opt_char:<1} {e.expiry:<10} "
                f"{e.action:<3} {e.qty:>4} {e.entry_price:>8.2f} {e.exit_price or 0:>8.2f} "
                f"{pnl_str:>10} {pnl_pct_str:>7} ${e.balance:>10,.2f}"
            )

    return "\n".join(lines)


def format_csv(journal: list[JournalEntry]) -> str:
    """Export journal as CSV."""
    header = "opened,ticker,strike,type,expiry,action,qty,entry_price,entry_cost,exit_price,exit_cost,closed,pnl,pnl_pct,balance"
    rows = [header]
    for e in journal:
        rows.append(
            f"{e.opened},{e.ticker},{e.strike},{e.opt_type},{e.expiry},"
            f"{e.action},{e.qty},{e.entry_price},{e.entry_cost},"
            f"{e.exit_price or ''},{e.exit_cost or ''},{e.closed or ''},"
            f"{e.pnl or ''},{e.pnl_pct or ''},{e.balance}"
        )
    return "\n".join(rows)


def print_open(trades: list[Trade]):
    positions = get_open_positions(trades)
    if not positions:
        print("No open positions.")
        return

    total_value = 0.0
    lines = []
    lines.append(f"{'Ticker':<6} {'Strike':>7} {'T':<1} {'Expiry':<10} {'Qty':>4} {'Avg$':>8} {'Cost':>9} {'Opened'}")
    lines.append("-" * 60)
    for key, p in sorted(positions.items()):
        cost = p["total_cost"]
        total_value += cost
        lines.append(
            f"{p['ticker']:<6} {p['strike']:>7.0f} {p['type'][0]:<1} {p['expiry']:<10} "
            f"{p['qty']:>4} {p['avg_price']:>8.2f} ${cost:>8,.0f} {p['opened'][:19]}"
        )
    lines.append("-" * 60)
    lines.append(f"{'':>38} {'Total':<5} ${total_value:>8,.0f}")
    print("\n".join(lines))


def print_summary(journal: list[JournalEntry], trades: list[Trade]):
    closed = [e for e in journal if e.action == "STC"]
    wins = [e for e in closed if (e.pnl or 0) > 0]
    losses = [e for e in closed if (e.pnl or 0) < 0]
    total_pnl = sum(e.pnl or 0 for e in closed)
    balance = journal[-1].balance if journal else STARTING_CAPITAL

    print(f"=== TRADE SUMMARY ===")
    print(f"Starting capital:  ${STARTING_CAPITAL:>10,.2f}")
    print(f"Current balance:   ${balance:>10,.2f}")
    print(f"Total P&L:         ${total_pnl:>+10,.2f}  ({(total_pnl/STARTING_CAPITAL)*100:+.1f}%)")
    print(f"Total trades:      {len(journal):>10}")
    print(f"  BTO entries:     {len([e for e in journal if e.action=='BTO']):>10}")
    print(f"  STC (closed):    {len(closed):>10}")
    print(f"Win rate:          {len(wins)}/{len(closed)} ({len(wins)/len(closed)*100:.0f}%)" if closed else "Win rate:          N/A")
    if wins:
        print(f"Avg win:           ${sum(e.pnl or 0 for e in wins)/len(wins):>+10,.2f}")
    if losses:
        print(f"Avg loss:          ${sum(e.pnl or 0 for e in losses)/len(losses):>+10,.2f}")
    if wins and losses:
        avg_win = sum(e.pnl or 0 for e in wins) / len(wins)
        avg_loss = abs(sum(e.pnl or 0 for e in losses) / len(losses))
        print(f"Profit factor:     {avg_win/avg_loss:>10.2f}" if avg_loss > 0 else "")

    # Open positions
    positions = get_open_positions(trades)
    if positions:
        print(f"\nOpen positions:    {len(positions)}")
        for key, p in sorted(positions.items()):
            print(f"  {p['ticker']} {p['strike']:.0f}{p['type'][0]} {p['expiry']} x{p['qty']} "
                  f"@${p['avg_price']:.2f}  cost=${p['total_cost']:,.0f}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    trades = load_trades()

    if not trades:
        print("No trades found in", CSV_PATH)
        return

    if mode == "--open":
        print_open(trades)
        return

    journal = build_journal(trades)

    if mode == "--summary":
        print_summary(journal, trades)
        return

    if mode == "--csv":
        print(format_csv(journal))
        return

    # Default: full journal
    print(format_journal(journal))
    print()
    print_summary(journal, trades)


if __name__ == "__main__":
    main()
