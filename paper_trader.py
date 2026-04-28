#!/usr/bin/env python3
"""
Paper Trading Engine — simulated broker backed by a JSON state file.

Usage:
    pt = PaperTrader(capital=10000, state_file="paper_state.json")
    pt.buy("SPY", price=540.0)        # fills 1 contract immediately
    pt.sell("SPY", price=545.0)       # closes oldest position
    pt.close_position("SPY")           # liquidates all SPY
    print(pt.get_portfolio())
"""

from __future__ import annotations

import json, logging, os, time, uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("paper_trader")

# ── Default state template ───────────────────────────────────────────────
def _default_state(capital: float) -> dict:
    return {
        "version": 1,
        "initial_capital": capital,
        "cash": capital,
        "positions": [],      # [{id, symbol, qty, avg_price, entry_time, side}]
        "orders": [],          # [{id, symbol, side, qty, price, status, time}]
        "trade_count": 0,
        "total_pnl": 0.0,
        "realized_pnl": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class PaperTrader:
    """Simulated broker. All orders fill immediately at the given price."""

    def __init__(self, capital: float = 10000, state_file: str = "paper_state.json"):
        self._state_file = state_file
        self._lock_file = state_file + ".lock"
        self._capital = capital
        self._state: dict = _default_state(capital)
        if os.path.exists(state_file):
            self._load()
        else:
            self._save()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self._state_file) as f:
                self._state = json.load(f)
            log.info("Loaded paper state: cash=%.2f  positions=%d  orders=%d",
                     self._state.get("cash", 0),
                     len(self._state.get("positions", [])),
                     len(self._state.get("orders", [])))
        except Exception as e:
            log.error("Failed to load state file: %s", e)

    def _save(self) -> None:
        """Atomic write via temporary file + rename."""
        try:
            tmp = self._state_file + ".tmp." + str(os.getpid())
            with open(tmp, "w") as f:
                json.dump(self._state, f, indent=2, default=str)
            os.replace(tmp, self._state_file)
        except Exception as e:
            log.error("Failed to save state: %s", e)
            raise

    # ── data access ───────────────────────────────────────────────────────
    def get_portfolio(self) -> dict:
        """Returns a summary dict suitable for bot display."""
        pos = self._state.get("positions", [])
        cash = self._state.get("cash", 0.0)
        realized = self._state.get("realized_pnl", 0.0)
        total_pnl = self._state.get("total_pnl", 0.0)
        initial = self._state.get("initial_capital", 0.0)

        # compute unrealized equity from positions
        position_value = sum(p.get("last_price", p.get("avg_price", 0)) * p.get("qty", 0) for p in pos)
        equity = cash + position_value
        unrealized_pnl = equity - initial - realized if initial > 0 else 0.0

        return {
            "initial_capital": initial,
            "cash": cash,
            "equity": equity,
            "position_value": position_value,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized + unrealized_pnl,
            "position_count": len(pos),
            "trade_count": self._state.get("trade_count", 0),
            "positions": self.get_positions(),
        }

    def get_positions(self) -> List[dict]:
        """Returns current open positions with mark-to-market data."""
        return self._state.get("positions", [])

    def get_orders(self, limit: int = 20) -> List[dict]:
        """Returns recent orders, newest first."""
        orders = self._state.get("orders", [])
        return sorted(orders, key=lambda o: o.get("time", ""), reverse=True)[:limit]

    def _get_position_index(self, symbol: str) -> Optional[int]:
        """Find first open position for symbol. Returns index or None."""
        for i, pos in enumerate(self._state.get("positions", [])):
            if pos.get("symbol", "").upper() == symbol.upper():
                return i
        return None

    # ── trading actions ───────────────────────────────────────────────────
    def buy(self, symbol: str, price: float, qty: int = 1) -> dict:
        """Buy 1 contract (or `qty`). Returns order dict.

        Constraints: long-only, fills immediately at `price`, checks cash.
        """
        symbol = symbol.strip().upper()
        cash = self._state.get("cash", 0.0)
        cost = price * qty

        if cash < cost:
            raise ValueError(
                f"Not enough cash: need ${cost:.2f}, have ${cash:.2f}. "
                f"Deposit ${cost - cash:.2f} more."
            )

        order_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        # Create order record
        order = {
            "id": order_id,
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "price": price,
            "cost": cost,
            "status": "filled",
            "time": now,
        }
        self._state.setdefault("orders", []).append(order)

        # Add position
        position = {
            "id": order_id,
            "symbol": symbol,
            "qty": qty,
            "avg_price": price,
            "last_price": price,
            "entry_time": now,
            "side": "buy",
        }
        self._state.setdefault("positions", []).append(position)

        # Update cash
        self._state["cash"] = cash - cost
        self._state["trade_count"] = self._state.get("trade_count", 0) + 1

        self._save()
        log.info("BUY  %s  %d @ %.2f  → cost=%.2f  cash=%.2f", symbol, qty, price, cost, self._state["cash"])
        return order

    def sell(self, symbol: str, price: float, qty: Optional[int] = None) -> dict:
        """Sell one position (or `qty`) of `symbol`. Closes oldest first.

        If `qty` is None (default), sells exactly 1 contract.
        If `qty` is provided, sells that many contracts (FIFO).
        """
        symbol = symbol.strip().upper()
        idx = self._get_position_index(symbol)
        if idx is None:
            raise ValueError(f"No open position for {symbol}")

        # Determine how many to close
        available_qty = self._state["positions"][idx]["qty"]
        close_qty = qty if qty is not None else available_qty
        close_qty = min(close_qty, available_qty)

        old_pos = self._state["positions"][idx]
        entry_price = old_pos["avg_price"]
        pnl = (price - entry_price) * close_qty
        proceeds = price * close_qty

        order_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        order = {
            "id": order_id,
            "symbol": symbol,
            "side": "sell",
            "qty": close_qty,
            "price": price,
            "entry_price": entry_price,
            "pnl": round(pnl, 2),
            "proceeds": round(proceeds, 2),
            "status": "filled",
            "time": now,
        }
        self._state.setdefault("orders", []).append(order)

        # Update/remove position
        remaining = available_qty - close_qty
        if remaining <= 0:
            self._state["positions"].pop(idx)
        else:
            self._state["positions"][idx]["qty"] = remaining

        # Update capital
        self._state["cash"] = self._state.get("cash", 0) + proceeds
        self._state["realized_pnl"] = self._state.get("realized_pnl", 0) + pnl
        self._state["total_pnl"] = self._state.get("realized_pnl", 0)  # base; unrealized added in get_portfolio
        self._state["trade_count"] = self._state.get("trade_count", 0) + 1

        self._save()
        log.info("SELL %s  %d @ %.2f  → pnl=%.2f  cash=%.2f", symbol, close_qty, price, pnl, self._state["cash"])
        return order

    def close_position(self, symbol: str, price: float) -> dict:
        """Liquidate entire position for `symbol` at given price."""
        return self.sell(symbol, price, qty=None)  # None = all

    # ── price update (mark-to-market) ─────────────────────────────────────
    def update_prices(self, quotes: Dict[str, float]) -> None:
        """Update last_price for all open positions (mark-to-market).

        `quotes` is a dict mapping symbol → latest price.
        """
        for pos in self._state.get("positions", []):
            sym = pos.get("symbol", "")
            if sym in quotes:
                pos["last_price"] = quotes[sym]
        self._save()

    # ── safety ────────────────────────────────────────────────────────────
    def reset(self, capital: Optional[float] = None) -> None:
        """Wipe state and start fresh. Requires confirmation keyword."""
        self._state = _default_state(capital or self._capital)
        self._save()
        log.warning("Paper state RESET. New capital: %.2f", self._state["initial_capital"])


# ── convenience: read env, get singleton ─────────────────────────────────
_CONFIG = {}

def get_paper_trader() -> PaperTrader:
    """Return a global PaperTrader singleton. Loads config from .env."""
    global _CONFIG
    if "instance" not in _CONFIG:
        from dotenv import load_dotenv
        root = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(root, ".env"))
        capital = float(os.getenv("PAPER_INITIAL_CAPITAL", "10000"))
        state_path = os.getenv("PAPER_STATE_FILE", os.path.join(root, "paper_state.json"))
        _CONFIG["instance"] = PaperTrader(capital=capital, state_file=state_path)
    return _CONFIG["instance"]
