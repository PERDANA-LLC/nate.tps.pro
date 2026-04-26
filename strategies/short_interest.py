"""
Short Interest Analysis for Nate (TPS augmentation)

Fetches short interest data (short float %, short ratio) from external sources.
Currently supports mock/synthetic data for development; can be extended to
real APIs (e.g., FINRA, Nasdaq, or Schwab fundamentals if available).

Metrics:
- short_float_pct: float percentage of shares short (0–100)
- short_ratio: days to cover; short interest / avg daily volume

Usage:
    from strategies.short_interest import enrich_with_short_metrics
    df = enrich_with_short_metrics(df, symbol='SPY')
"""

import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


# ── Mock/synthetic data source ──────────────────────────────────────────────
# Replace with real API calls when available (FINRA, Nasdaq, or Schwab fundamentals)

_MOCK_SHORT_CACHE = {}


def _fetch_short_metrics_mock(symbol: str) -> dict:
    """
    Generate deterministic mock short metrics for a symbol.
    In production, replace with:
      - FINRA Short Interest API
      - Nasdaq short interest endpoint
      - Schwab fundamentals (if field exists)
    """
    # Use symbol hash to get stable pseudorandom values
    h = hash(symbol.upper()) & 0xFFFFFFFF
    rng = np.random.RandomState(h)

    short_float_pct = rng.uniform(1.5, 25.0)   # 1.5% – 25%
    short_ratio = rng.uniform(1.2, 8.0)        # 1.2 – 8 days to cover

    return {
        'short_float_pct': round(short_float_pct, 2),
        'short_ratio': round(short_ratio, 2),
        'short_interest_shares': None,  # unknown in mock
        'float_shares': None,
        'avg_daily_volume': None,
        'as_of_date': (datetime.now() - timedelta(days=rng.randint(15, 45))).strftime('%Y-%m-%d'),
        'data_source': 'mock'
    }


def fetch_short_metrics(symbol: str, use_cache: bool = True) -> dict:
    """
    Fetch short interest metrics for a symbol.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g., 'SPY', 'AAPL')
    use_cache : bool
        Cache results in memory to avoid repeated lookups within same session.

    Returns
    -------
    dict with keys:
        short_float_pct, short_ratio, short_interest_shares, float_shares,
        avg_daily_volume, as_of_date, data_source
    """
    symbol = symbol.upper()

    if use_cache and symbol in _MOCK_SHORT_CACHE:
        return _MOCK_SHORT_CACHE[symbol]

    # Try real API (stub — insert real calls here when available)
    # Example patterns (uncomment & configure when real source ready):
    #
    # from finra_api import get_short_interest  # hypothetical
    # data = finra_api.get_short_interest(symbol)
    # return {
    #     'short_float_pct': data['short_pct'],
    #     'short_ratio': data['days_to_cover'],
    #     ...
    # }
    #
    # OR using Schwab fundamentals (if available):
    # resp = client.instrument fundamentals(symbol)
    # data = resp.json()
    # short_float_pct = data.get('shortPct', None)
    # ...

    # Fall back to mock until real API connected
    metrics = _fetch_short_metrics_mock(symbol)

    if use_cache:
        _MOCK_SHORT_CACHE[symbol] = metrics

    return metrics


def enrich_with_short_metrics(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Append short interest metrics to every row of a price DataFrame.

    The metrics are constant across the DataFrame (they are per-symbol,
    not per-bar). This function adds them as new scalar columns.

    Parameters
    ----------
    df : pd.DataFrame
        Price data (any frequency). Index is preserved.
    symbol : str
        Ticker to fetch short data for.

    Returns
    -------
    pd.DataFrame with additional columns:
        - short_float_pct : float
        - short_ratio : float
        - short_interest_shares : int or None
        - float_shares : int or None
        - avg_daily_volume : int or None
        - short_data_source : str ('mock' or real source name)
        - short_as_of_date : str (YYYY-MM-DD)
    """
    metrics = fetch_short_metrics(symbol)

    for key, value in metrics.items():
        col_name = f'short_{key}' if not key.startswith('short_') else key
        df[col_name] = value

    # Rename data_source to consistent column name
    df.rename(columns={'short_data_source': 'short_data_source'}, inplace=True)

    return df


# ── CLI demo ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import json

    symbols = ['SPY', 'AAPL', 'TSLA', 'QQQ']

    print("=== Short Interest Mock Data Demo ===\n")
    for sym in symbols:
        m = fetch_short_metrics(sym)
        print(f"{sym}:")
        print(f"  Short Float %   : {m['short_float_pct']}%")
        print(f"  Short Ratio     : {m['short_ratio']} days")
        print(f"  As of           : {m['as_of_date']}")
        print(f"  Source          : {m['data_source']}")
        print()

    # Demonstrate enrichment on a dummy price DataFrame
    dates = pd.date_range('2025-01-01', periods=5, freq='D')
    dummy = pd.DataFrame({'close': [100, 101, 99, 102, 103]}, index=dates)
    enriched = enrich_with_short_metrics(dummy, 'SPY')
    print("Enriched DataFrame (last row):")
    print(enriched.iloc[-1].to_string())
