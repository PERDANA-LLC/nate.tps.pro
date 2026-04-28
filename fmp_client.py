
"""
FMP (Financial Modeling Prep) client wrapper.
Mimics the schwabdev `Client` interface just enough for tps_scan.py
to work unchanged — only `quote()` and `price_history()`.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlencode

import requests

log = logging.getLogger("fmp_client")

FMP_BASE = "https://financialmodelingprep.com/api/v3"


class _FakeResponse:
    """Lightweight object whose .json() returns the stored dict."""
    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return self._data


class FMPClient:
    """
    Drop-in replacement for Schwab's `Client` for the two methods
    that tps_scan.py actually calls:
      - price_history(symbol, periodType, period, frequencyType, frequency)
      - quote(symbol)
    """

    # FMP rate limit: ~300 req/min for free tier; we sleep generously between calls
    _global_last_call = 0.0
    _MIN_INTERVAL = 0.30  # seconds

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("FMP API key is required")
        self._api_key = api_key
        self._session = requests.Session()
        # Cache small quote results for a few seconds to save calls
        self._quote_cache: Dict[str, tuple[float, dict]] = {}  # symbol -> (timestamp, data)

    @classmethod
    def _wait_if_needed(cls):
        """Simple rate limiter."""
        now = time.monotonic()
        gap = now - cls._global_last_call
        if gap < cls._MIN_INTERVAL:
            time.sleep(cls._MIN_INTERVAL - gap)
        cls._global_last_call = time.monotonic()

    # ------------------------------------------------------------------
    #  quote  —  real-time / delayed snapshot
    # ------------------------------------------------------------------
    def quote(self, symbol: str):
        """
        Return an object whose .json() returns a dict shaped like
        Schwab's quote response:
          { "<symbol>": { "quote": { "lastPrice": ..., "mark": ..., "closePrice": ... } } }
        """
        sym_clean = self._clean_symbol(symbol)

        # Check cache (5s TTL for quotes)
        cached_at, cached = self._quote_cache.get(sym_clean, (0, {}))
        if time.monotonic() - cached_at < 5:
            log.debug("quote(%s) → cache hit", sym_clean)
            return _FakeResponse(cached)

        self._wait_if_needed()
        try:
            resp = self._session.get(
                f"{FMP_BASE}/quote/{sym_clean}",
                params={"apikey": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning("quote(%s) failed: %s", sym_clean, exc)
            # Return empty shape → caller will fall back gracefully
            return _FakeResponse({sym_clean: {"quote": {}}})

        # FMP returns a list; extract first element
        if isinstance(payload, list) and payload:
            item = payload[0]
        elif isinstance(payload, dict):
            item = payload
        else:
            return _FakeResponse({sym_clean: {"quote": {}}})

        shaped = {
            sym_clean: {
                "quote": {
                    "lastPrice": item.get("price") or item.get("lastPrice"),
                    "mark": item.get("price") or item.get("lastPrice"),
                    "closePrice": item.get("previousClose"),
                }
            }
        }
        self._quote_cache[sym_clean] = (time.monotonic(), shaped)
        return _FakeResponse(shaped)

    # ------------------------------------------------------------------
    #  price_history  —  daily / minute / weekly candles
    # ------------------------------------------------------------------
    def price_history(
        self,
        symbol: str,
        periodType: str = "month",
        period: int = 6,
        frequencyType: str = "daily",
        frequency: int = 1,
    ):
        """
        Return an object whose .json() returns a dict shaped like
        Schwab's price-history response:
          { "candles": [ { "datetime": <ms>, "open", "high", "low", "close", "volume" }, ... ] }
        """
        sym_clean = self._clean_symbol(symbol)

        self._wait_if_needed()
        try:
            return self._price_history_fmp(sym_clean, periodType, period, frequencyType, frequency)
        except Exception as exc:
            log.warning(
                "price_history(%s, %s, %d, %s, %d) failed: %s",
                sym_clean, periodType, period, frequencyType, frequency, exc,
            )
            return _FakeResponse({"candles": []})

    def _price_history_fmp(self, sym, periodType, period, freqType, freq):
        """Handle all frequency/type combos FMP supports."""
        # --- Daily chart  ---
        if freqType == "daily":
            # FMP free tier: last ~5 yrs of daily data.
            # We request enough data and then slice on our side.
            from_ = _fmt_date(_months_ago(period if periodType == "month" else (period * 30)))
            to = _fmt_date(datetime.now(timezone.utc))
            resp = self._session.get(
                f"{FMP_BASE}/historical-price-full/{sym}",
                params={"from": from_, "to": to, "apikey": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = _extract_historical_rows(payload)
            return _FakeResponse({"candles": rows, "empty": len(rows) == 0})

        # --- Week / month pseudo support ---
        if freqType in ("weekly", "monthly"):
            # FMP has no direct weekly/monthly — we fetch daily and resample
            from_ = _fmt_date(_months_ago(period * 12 if periodType == "year" else period * 36))
            to = _fmt_date(datetime.now(timezone.utc))
            resp = self._session.get(
                f"{FMP_BASE}/historical-price-full/{sym}",
                params={"from": from_, "to": to, "apikey": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            daily_rows = _extract_historical_rows(payload)
            # Resample using pandas
            import pandas as pd
            if not daily_rows:
                return _FakeResponse({"candles": []})
            df = pd.DataFrame(daily_rows)
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
            df = df.set_index("datetime").sort_index()
            rule = "W" if freqType == "weekly" else "ME"
            ohlcv = df.resample(rule).agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna()
            rows = []
            for dt, row in ohlcv.iterrows():
                rows.append({
                    "datetime": int(dt.timestamp() * 1000),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
            return _FakeResponse({"candles": rows, "empty": len(rows) == 0})

        # --- Minute chart  ---
        if freqType == "minute":
            # FMP historical-chart/1min only gives ~5 days, so cap the range
            # tps_scan.py uses this for VXX (30-min bars) and ADD/TICK/PCALL (1-min bars)
            resp = self._session.get(
                f"{FMP_BASE}/historical-chart/1min/{sym}",
                params={"apikey": self._api_key},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list) or not payload:
                return _FakeResponse({"candles": []})
            rows = []
            for bar in payload:
                # FMP returns: {date, open, high, low, close, volume}
                dt = bar.get("date")
                if dt:
                    ms = int(pd.Timestamp(dt).timestamp() * 1000)
                    rows.append({
                        "datetime": ms,
                        "open": float(bar.get("open", 0)),
                        "high": float(bar.get("high", 0)),
                        "low": float(bar.get("low", 0)),
                        "close": float(bar.get("close", 0)),
                        "volume": float(bar.get("volume", 0)),
                    })
            return _FakeResponse({"candles": rows, "empty": len(rows) == 0})

        # Fallback
        return _FakeResponse({"candles": []})

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        """Normalise tps_scan's symbol probes into FMP-friendly tickers."""
        s = symbol.replace("$", "").replace(".X", "").replace(".x", "").strip().upper()
        # Map known Schwab index names → FMP names
        mapping = {
            "VIX": "^VIX",
            "ADD": "ADD",   # FMP likely doesn't support this
            "PCALL": "PCALL",
            "TICK": "TICK",
            "TICK-NY": "TICK",
            "VXX": "VXX",
        }
        return mapping.get(s, s)


# ------------------------------------------------------------------------
# module helpers
# ------------------------------------------------------------------------

def _fmt_date(dt):
    """yyyy-mm-dd"""
    return dt.strftime("%Y-%m-%d")


def _months_ago(n: int):
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(days=n * 31)


def _extract_historical_rows(payload) -> List[Dict]:
    """Extract rows from FMP historical-price-full response,
    converting to Schwab-style candle dicts."""
    import pandas as pd
    if isinstance(payload, dict):
        hist = payload.get("historical") or payload.get("historicalStockList") or []
    elif isinstance(payload, list):
        hist = payload
    else:
        return []
    rows = []
    for bar in hist:
        dt_str = bar.get("date")
        if not dt_str:
            continue
        try:
            ms = int(pd.Timestamp(dt_str).timestamp() * 1000)
        except Exception:
            continue
        rows.append({
            "datetime": ms,
            "open": float(bar.get("open", 0)),
            "high": float(bar.get("high", 0)),
            "low": float(bar.get("low", 0)),
            "close": float(bar.get("close", 0)),
            "volume": float(bar.get("volume", 0)),
        })
    return rows


# Quick self-test when run directly
if __name__ == "__main__":
    import os, sys
    logging.basicConfig(level=logging.DEBUG)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("FMP_API_KEY")
    c = FMPClient(key)
    print("\n--- quote SPY ---")
    q = c.quote("SPY").json()
    print(q)
    print("\n--- daily AAPL ---")
    ph = c.price_history("AAPL").json()
    print(f"  candles: {len(ph.get('candles',[]))}")
    print("\n--- minimal weekly SPY ---")
    ph2 = c.price_history("SPY", periodType="year", period=1, frequencyType="weekly", frequency=1).json()
    print(f"  weekly candles: {len(ph2.get('candles',[]))}")
    print("\n--- intraday SPY ---")
    ph3 = c.price_history("SPY", periodType="day", period=3, frequencyType="minute", frequency=1).json()
    print(f"  minute candles: {len(ph3.get('candles',[]))}")
