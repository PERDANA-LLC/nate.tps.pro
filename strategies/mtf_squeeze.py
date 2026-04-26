"""
Multi-timeframe TTM Squeeze analysis.

Timeframes supported:
  - W  : weekly (resampled from daily)
  - D  : daily (uses existing daily squeeze)
  - N  : N-minute intraday (e.g., 5, 15, 30, 60 --- native Schwab intervals)
          Non-standard intervals (e.g., 195, 130, 78) are derived from 1-minute data.

Intraday handling:
  - Standard intervals (1,5,10,15,30,60): fetched directly from Schwab via frequency=N
  - Non-standard intervals (e.g., 195,130,78): fetched as 1-minute bars and resampled locally
  - All intraday fetches are capped at 10 days of history (Schwab limit)
  - Requires >=20 bars to compute squeeze; otherwise sqz_<tf> = False with a warning

For each timeframe, computes:
  - sqz_<tf>        : squeeze active (any SQZPRO_ON_*)
  - sqz_<tf>_fired  : squeeze breakout (SQZPRO_OFF_WIDE)

Aggregate columns:
  - mtf_squeeze_count : int, count of active squeezes across all TF
  - mtf_squeeze_any   : bool, any timeframe active
  - mtf_squeeze_all   : bool, all timeframes active
"""

import pandas as pd
import pandas_ta as ta
from typing import Dict, Tuple, List
from strategies.ema_trend_analysis import fetch_price_history


def _compute_squeeze_status(df: pd.DataFrame) -> Tuple[bool, bool]:
    """
    Compute TTM Squeeze Pro on the given DataFrame and return status of last bar.
    Returns (squeeze_active, squeeze_fired).
    """
    if len(df) < 20:
        return (False, False)
    df_temp = df.copy()
    df_temp.ta.squeeze_pro(append=True)
    last = df_temp.iloc[-1]
    squeeze_on = (
        (last.get('SQZPRO_ON_NARROW', 0) == 1) or
        (last.get('SQZPRO_ON_NORMAL', 0) == 1) or
        (last.get('SQZPRO_ON_WIDE', 0) == 1)
    )
    fired = (last.get('SQZPRO_OFF_WIDE', 0) == 1)
    return (bool(squeeze_on), bool(fired))


def _resample_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Resample daily OHLCV to weekly bars (Friday week-end).
    """
    if not isinstance(df_daily.index, pd.DatetimeIndex):
        raise ValueError("df_daily must have DatetimeIndex")
    weekly = df_daily.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    return weekly


def add_mtf_squeeze_columns(df_daily: pd.DataFrame, symbol: str, client,
                             timeframes: List[str]) -> None:
    """
    For each requested timeframe, compute the latest squeeze status and add columns
    to the daily DataFrame (constant across all rows). Also adds aggregate MTF columns.

    Added columns (per timeframe tf):
      - sqz_{tf}        : bool, squeeze active
      - sqz_{tf}_fired  : bool, squeeze breakout

    Aggregate columns:
      - mtf_squeeze_count : int, count of active squeezes across all TF
      - mtf_squeeze_any   : bool, any timeframe active
      - mtf_squeeze_all   : bool, all timeframes active
    """
    statuses: Dict[str, Tuple[bool, bool]] = {}

    for tf in timeframes:
        col_active = f'sqz_{tf}'
        col_fired = f'sqz_{tf}_fired'

        if tf == 'W':
            weekly = _resample_weekly(df_daily)
            if len(weekly) < 20:
                print(f"Warning: insufficient weekly data for {symbol} (need 20w, got {len(weekly)})", flush=True)
                active, fired = False, False
            else:
                active, fired = _compute_squeeze_status(weekly)
            df_daily[col_active] = active
            df_daily[col_fired] = fired
            statuses[tf] = (active, fired)

        elif tf == 'D':
            active = df_daily['ttm_squeeze'].iloc[-1] if 'ttm_squeeze' in df_daily.columns else False
            fired = df_daily['ttm_squeeze_fired'].iloc[-1] if 'ttm_squeeze_fired' in df_daily.columns else False
            df_daily[col_active] = active
            df_daily[col_fired] = fired
            statuses[tf] = (bool(active), bool(fired))

        else:
            # Minute timeframe: numeric string
            try:
                minutes = int(tf)
            except ValueError:
                print(f"Unknown timeframe spec: {tf}, skipping", flush=True)
                continue

            # Schwab-supported intraday frequencies
            SCHWAB_SUPPORTED = {1, 5, 10, 15, 30, 60}

            if minutes in SCHWAB_SUPPORTED:
                # Native Schwab interval — fetch directly
                minutes_per_day = 390
                bars_per_day = minutes_per_day // minutes if minutes > 0 else 0
                if bars_per_day == 0:
                    bars_per_day = 1
                days_needed = (20 + bars_per_day - 1) // bars_per_day
                days_needed = max(1, min(days_needed, 10))

                try:
                    df_min = fetch_price_history(
                        symbol,
                        client,
                        period_type='day',
                        period=days_needed,
                        frequency_type='minute',
                        frequency=minutes
                    )
                    if df_min is None or len(df_min) < 20:
                        print(f"Warning: insufficient minute data for {symbol} at {tf}min (got {len(df_min) if df_min is not None else 0} bars)", flush=True)
                        active, fired = False, False
                    else:
                        active, fired = _compute_squeeze_status(df_min)
                    df_daily[col_active] = active
                    df_daily[col_fired] = fired
                    statuses[tf] = (active, fired)
                except Exception as e:
                    print(f"Error fetching {tf}min data for {symbol}: {e}", flush=True)
                    df_daily[col_active] = False
                    df_daily[col_fired] = False
                    statuses[tf] = (False, False)

            else:
                # Non-standard interval — derive from 1-minute bars
                print(f"Note: {tf}min is not a native Schwab interval — deriving from 1-minute data", flush=True)

                # Fetch 1-minute data (max 10 days due to Schwab limits)
                try:
                    df_1min = fetch_price_history(
                        symbol,
                        client,
                        period_type='day',
                        period=10,
                        frequency_type='minute',
                        frequency=1
                    )
                    if df_1min is None or len(df_1min) < 20:
                        print(f"Warning: insufficient 1-minute data for {symbol} to derive {tf}min (got {len(df_1min) if df_1min is not None else 0} bars)", flush=True)
                        active, fired = False, False
                    else:
                        # Resample to target interval
                        rule = f'{minutes}T'
                        df_resampled = df_1min.resample(rule).agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }).dropna()

                        if len(df_resampled) < 20:
                            print(f"Warning: after resampling, {tf}min has only {len(df_resampled)} bars for {symbol} (need ≥20)", flush=True)
                            active, fired = False, False
                        else:
                            active, fired = _compute_squeeze_status(df_resampled)
                    df_daily[col_active] = active
                    df_daily[col_fired] = fired
                    statuses[tf] = (active, fired)
                except Exception as e:
                    print(f"Error deriving {tf}min from 1-minute data for {symbol}: {e}", flush=True)
                    df_daily[col_active] = False
                    df_daily[col_fired] = False
                    statuses[tf] = (False, False)

    # Aggregates
    actives = [v[0] for v in statuses.values()]
    df_daily['mtf_squeeze_count'] = sum(actives)
    df_daily['mtf_squeeze_any'] = any(actives)
    df_daily['mtf_squeeze_all'] = all(actives) if actives else False
