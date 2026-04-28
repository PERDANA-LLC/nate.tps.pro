#!/usr/bin/env python3
"""
Broker Interface — abstract protocol for paper/live swap.

Paper (default):    get_broker("paper")      → PaperTrader (local JSON)
Live  (future):     get_broker("live")        → SchwabTrader (schwab_client)
Live  (alt):        get_broker("fmp_paper")   → PaperTrader but with FMP price fills

The bot code always calls `get_broker()` and never cares which is active.
"""

from __future__ import annotations

import os, logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

log = logging.getLogger("broker")

# ── Abstract Broker ─────────────────────────────────────────────────────
class AbstractBroker(ABC):
    """Interface that all brokers (paper or live) must satisfy."""

    @abstractmethod
    def buy(self, symbol: str, price: float, qty: int = 1) -> dict: ...
    @abstractmethod
    def sell(self, symbol: str, price: float, qty: Optional[int] = None) -> dict: ...
    @abstractmethod
    def close_position(self, symbol: str, price: float) -> dict: ...
    @abstractmethod
    def get_portfolio(self) -> dict: ...
    @abstractmethod
    def get_positions(self) -> List[dict]: ...
    @abstractmethod
    def get_orders(self, limit: int = 20) -> List[dict]: ...
    @abstractmethod
    def update_prices(self, quotes: Dict[str, float]) -> None: ...
    @abstractmethod
    def reset(self, capital: Optional[float] = None) -> None: ...


# ── Factory ─────────────────────────────────────────────────────────────
_BROKER_INSTANCE: Optional[AbstractBroker] = None
_BROKER_MODE: str = ""

def get_broker(mode: Optional[str] = None) -> AbstractBroker:
    """Return the broker singleton. Mode is read from .env PAPER_MODE once.

    `mode` can be "paper", "live", or "fmp_paper".
    If PAPER_MODE is "false" or "live", returns SchwabTrader (future).
    Otherwise returns PaperTrader.
    """
    global _BROKER_INSTANCE, _BROKER_MODE

    if _BROKER_INSTANCE is not None and (mode is None or mode == _BROKER_MODE):
        return _BROKER_INSTANCE

    if mode is None:
        from dotenv import load_dotenv
        root = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(root, ".env"))
        paper_mode = os.getenv("PAPER_MODE", "true").lower()
        mode = "live" if paper_mode in ("false", "live", "0") else "paper"

    _BROKER_MODE = mode
    if mode == "paper":
        from paper_trader import get_paper_trader
        _BROKER_INSTANCE = get_paper_trader()
    elif mode == "live":
        # TODO: when schwab_client order methods are wrapped
        raise NotImplementedError(
            "Live broker (Schwab) not yet available. "
            "Set PAPER_MODE=true in .env to use paper trading."
        )
    else:
        raise ValueError(f"Unknown broker mode: {mode}")

    log.info("Broker mode: %s", _BROKER_MODE)
    return _BROKER_INSTANCE


def reset_broker() -> None:
    """Reset the singleton (e.g. after config change)."""
    global _BROKER_INSTANCE, _BROKER_MODE
    _BROKER_INSTANCE = None
    _BROKER_MODE = ""
