"""
Unified TPS Scanner — combines Trend, Pattern, and (stub) Squeeze analysis.

Outputs a single DataFrame with all signal flags for a given symbol.

Usage:
    python strategies/tps_scanner.py --symbol SPY --window 10 --r2 0.8

Environment:
    SCHWAB_API_KEY, SCHWAB_API_SECRET (required)
"""

import os
import sys
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

# Local imports (same repo)
from strategies.ema_trend_analysis import fetch_price_history, calculate_emas, detect_upward_trend
from strategies.pattern_detection import detect_patterns
from strategies.short_interest import enrich_with_short_metrics
import numpy as np


def compute_tmm_squeeze(df: pd.DataFrame, squeeze_window: int = 20) -> pd.DataFrame:
    """
    TTM Squeeze Pro (S) computation using pandas-ta's squeeze_pro.

    The squeeze_pro indicator adds columns:
      SQZPRO_ON_WIDE, SQZPRO_ON_NORMAL, SQZPRO_ON_NARROW  — squeeze intensity (0/1)
      SQZPRO_OFF_WIDE                                      — squeeze fired (0/1)
      SQZPRO_NO                                            — no squeeze

    We derive a unified boolean `ttm_squeeze` = True when ANY squeeze is ON
    (NARROW, NORMAL, or WIDE).
    """
    # pandas-ta adds the squeeze_pro columns in-place
    df.ta.squeeze_pro(append=True)

    # Unified squeeze flag: True if any squeeze channel is active
    squeeze_on = (
        (df.get('SQZPRO_ON_NARROW', 0) == 1) |
        (df.get('SQZPRO_ON_NORMAL', 0) == 1) |
        (df.get('SQZPRO_ON_WIDE', 0) == 1)
    )
    df['ttm_squeeze'] = squeeze_on

    # Optional: track fired events separately
    df['ttm_squeeze_fired'] = df.get('SQZPRO_OFF_WIDE', 0) == 1

    return df


def calculate_vwap(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Compute rolling VWAP (Volume Weighted Average Price) and its slope.

    VWAP = cumulative(close * volume) / cumulative(volume) over a window.
    Also computes a linear slope on the VWAP values to detect upward/downward bias.
    """
    if 'volume' not in df.columns:
        df['vwap'] = df['close']
        df['vwap_slope'] = 0.0
        return df

    # Rolling VWAP: sum(price*volume)/sum(volume) over window
    pxvol = df['close'] * df['volume']
    num = pxvol.rolling(window=window, min_periods=window).sum()
    den = df['volume'].rolling(window=window, min_periods=window).sum()
    df['vwap'] = num / den

    # Slope: linear regression on the last `window` VWAP values (1 = positive, 0 = flat/negative)
    # Vectorized rolling linear regression using numpy polyfit equivalent via manual math
    def _slope(series: pd.Series) -> float:
        if len(series.dropna()) < 3:
            return 0.0
        x = np.arange(len(series))
        y = series.values
        xy = np.dot(x, y)
        xx = np.dot(x, x)
        m = (len(x) * xy - np.sum(x) * np.sum(y)) / (len(x) * xx - np.sum(x)**2)
        return m

    df['vwap_slope'] = df['vwap'].rolling(window=min(5, window), min_periods=3).apply(_slope, raw=True)
    # Normalize slope sign: True if slope > 0
    df['vwap_rising'] = df['vwap_slope'] > 0

    return df


def detect_volume_burst(df: pd.DataFrame,
                        volume_multiplier: float = 2.0,
                        volume_window: int = 20) -> pd.DataFrame:
    """
    Detect volume bursts — bars where volume significantly exceeds its recent average.

    Parameters
    ----------
    volume_multiplier : float
        Threshold as multiple of average volume (e.g. 2.0 = 2x average).
    volume_window : int
        Lookback window for computing average volume.

    Returns
    -------
    DataFrame with 'volume_burst' (bool) and 'volume_ratio' columns.
    """
    avg_vol = df['volume'].rolling(window=volume_window, min_periods=1).mean()
    df['volume_ratio'] = df['volume'] / avg_vol
    df['volume_burst'] = df['volume_ratio'] >= volume_multiplier
    return df


def run_tps_scanner(symbol: str, client, ema_lengths: tuple = (8, 21, 55),
                     pattern_window: int = 10, r2_threshold: float = 0.8,
                     vwap_window: int = 20, volume_multiplier: float = 2.0) -> pd.DataFrame:
    """
    Full TPS scan on a single symbol.

    Steps:
    1. Fetch OHLCV from Schwab
    2. Compute EMAs → Upward_Trend flag
    3. Run pattern detection → bull_flag, bull_pennant
    4. Compute TTM Squeeze Pro (pandas-ta) → ttm_squeeze, ttm_squeeze_fired
    5. Enrich with short interest (Short Float %, Short Ratio)
    6. Compute VWAP (rolling) and slope → vwap, vwap_rising
    7. Detect Volume Bursts → volume_burst, volume_ratio

    Returns
    -------
    pd.DataFrame with all TPS signal columns, indexed by datetime.
    """
    # ---- T: TREND ----
    df = fetch_price_history(symbol, client)
    df = calculate_emas(df, lengths=ema_lengths)
    df = detect_upward_trend(df)

    # ---- P: PATTERN ----
    df = detect_patterns(df, window=pattern_window, r2_threshold=r2_threshold)

    # ---- S: SQUEEZE ----
    df = compute_tmm_squeeze(df)

    # ---- FUNDAMENTAL: Short Interest ----
    df = enrich_with_short_metrics(df, symbol)

    # ---- V: VWAP ----
    df = calculate_vwap(df, window=vwap_window)

    # ---- VOLUME: Volume Burst ----
    df = detect_volume_burst(df, volume_multiplier=volume_multiplier, volume_window=vwap_window)

    # Derived VWAP flags
    df['price_above_vwap'] = df['close'] > df['vwap']
    df['vwap_crossed'] = (
        (df['close'] >= df['vwap']) &
        (df['close'].shift(1) < df['vwap'].shift(1))
    )

    # ---- Composite Signals (technical only) ----
    # V-confluence: price above VWAP AND VWAP rising
    df['vwap_bullish'] = df['price_above_vwap'] & df['vwap_rising']

    # Volume burst aligned with VWAP cross (shorts covering)
    df['volume_burst_on_cross'] = df['volume_burst'] & df['vwap_crossed']

    # Original TPS (T+P+S only) — kept for backwards compatibility
    df['tps_all'] = (
        df['Upward_Trend'] &
        (df['bull_flag'] | df['bull_pennant']) &
        df['ttm_squeeze']
    )

    # TPS + VWAP confluence: all three TPS signals PLUS vwap_bullish
    df['tps_vwap_all'] = (
        df['tps_all'] &
        df['vwap_bullish']
    )

    # Full TPS + VWAP + Volume burst (the "perfect" setup)
    df['perfect_setup'] = df['tps_vwap_all'] & df['volume_burst_on_cross']

    # Individual signal strength (sum of binary flags: 0–6)
    df['tps_score'] = (
        df['Upward_Trend'].astype(int) +
        df['bull_flag'].astype(int) +
        df['bull_pennant'].astype(int) +
        df['ttm_squeeze'].astype(int) +
        df['vwap_bullish'].astype(int) +
        df['volume_burst_on_cross'].astype(int)
    )

    return df


def main():
    parser = argparse.ArgumentParser(
        description='TPS (Trend-Pattern-Squeeze) Scanner for Nate'
    )
    parser.add_argument('--symbol', default=os.getenv('ANALYSIS_SYMBOL', 'SPY'),
                        help='Symbol to scan (default: SPY)')
    parser.add_argument('--window', type=int, default=10,
                        help='Pattern detection window (default: 10)')
    parser.add_argument('--r2', type=float, default=0.8,
                        help='Pattern R² threshold (default: 0.8)')
    parser.add_argument('--vwap-window', type=int, default=20,
                        help='VWAP lookback window (default: 20)')
    parser.add_argument('--volume-multiplier', type=float, default=2.0,
                        help='Volume burst threshold as multiple of average (default: 2.0)')
    parser.add_argument('--min-short-float', type=float, default=None,
                        help='Minimum Short Float %% to flag (e.g. 20.0). Default: no filter')
    parser.add_argument('--min-short-ratio', type=float, default=None,
                        help='Minimum Short Ratio (days to cover) to flag (e.g. 3.0). Default: no filter')
    parser.add_argument('--show', type=int, default=15,
                        help='Number of latest rows to display (default: 15)')
    parser.add_argument('--save', action='store_true',
                        help='Save full results to CSV')
    args = parser.parse_args()

    # Load credentials
    api_key = os.getenv('SCHWAB_API_KEY')
    api_secret = os.getenv('SCHWAB_API_SECRET')

    if not api_key or not api_secret:
        print("ERROR: SCHWAB_API_KEY and SCHWAB_API_SECRET must be set.", file=sys.stderr)
        sys.exit(1)

    from schwabdev import Client
    client = Client(api_key, api_secret)

    print(f"Running TPS scan on {args.symbol}...\n")

    df = run_tps_scanner(
        symbol=args.symbol,
        client=client,
        ema_lengths=(8, 21, 55),
        pattern_window=args.window,
        r2_threshold=args.r2,
        vwap_window=args.vwap_window,
        volume_multiplier=args.volume_multiplier
    )

    # Get latest row once (for summary & current-state)
    current = df.iloc[-1]

    # -------------------------------------------------
    # Short Interest Filters (optional)
    # -------------------------------------------------
    short_filter_met = True
    if args.min_short_float is not None:
        short_filter_met &= current['short_float_pct'] >= args.min_short_float
    if args.min_short_ratio is not None:
        short_filter_met &= current['short_ratio'] >= args.min_short_ratio

    # Annotate DataFrame with short-filter flag (useful for saved CSV)
    df['short_filter_met'] = (df['short_float_pct'] >= (args.min_short_float or 0)) &                              (df['short_ratio'] >= (args.min_short_ratio or 0))

    # Display latest rows
    cols = [
        'close', 'EMA_8', 'EMA_21', 'EMA_55', 'Upward_Trend',
        'bull_flag', 'bull_pennant',
        'SQZPRO_ON_NARROW', 'SQZPRO_ON_NORMAL', 'SQZPRO_ON_WIDE',
        'ttm_squeeze', 'ttm_squeeze_fired',
        'vwap', 'vwap_rising', 'price_above_vwap',
        'volume', 'volume_ratio', 'volume_burst', 'vwap_crossed', 'volume_burst_on_cross',
        'short_float_pct', 'short_ratio', 'short_data_source',
        'short_filter_met',
        'tps_score', 'tps_vwap_all', 'perfect_setup'
    ]

    print(f"=== Latest {args.show} rows ===")
    print(df[cols].tail(args.show).to_string())

    # Summary
    total = len(df)
    print(f"\n--- Summary for {args.symbol} ---")
    print(f"Total bars: {total}")
    print(f"Upward Trend days    : {df['Upward_Trend'].sum():>5}  ({df['Upward_Trend'].mean()*100:>5.1f}%)")
    print(f"Bull Flag signals    : {df['bull_flag'].sum():>5}  ({df['bull_flag'].mean()*100:>5.1f}%)")
    print(f"Bull Pennant sigs    : {df['bull_pennant'].sum():>5}  ({df['bull_pennant'].mean()*100:>5.1f}%)")
    print(f"TTM Squeeze ON       : {df['ttm_squeeze'].sum():>5}  ({df['ttm_squeeze'].mean()*100:>5.1f}%)")
    print(f"TTM Squeeze FIRED    : {df['ttm_squeeze_fired'].sum():>5}  ({df['ttm_squeeze_fired'].mean()*100:>5.1f}%)")
    print(f"VWAP Bullish (↑VWAP) : {df['vwap_bullish'].sum():>5}  ({df['vwap_bullish'].mean()*100:>5.1f}%)  [price > VWAP & VWAP rising]")
    print(f"Volume Burst on Cross: {df['volume_burst_on_cross'].sum():>5}  ({df['volume_burst_on_cross'].mean()*100:>5.1f}%)  [shorts covering]")
    print(f"Short Float %        : {current['short_float_pct']:>5.1f}%  (as of {current['short_as_of_date']})")
    print(f"Short Ratio          : {current['short_ratio']:>5.2f} days")
    print(f"Short Data Source    : {current['short_data_source']}")
    if args.min_short_float is not None or args.min_short_ratio is not None:
        filter_status = "PASS" if short_filter_met else "FAIL"
        print(f"Short Filter         : {filter_status}  (float ≥ {args.min_short_float or 'any'}, ratio ≥ {args.min_short_ratio or 'any'})")

    # Combined signals
    tps_count = df['tps_all'].sum() if 'tps_all' in df.columns else 0
    tps_vwap_count = df['tps_vwap_all'].sum() if 'tps_vwap_all' in df.columns else 0
    perfect_count = df['perfect_setup'].sum() if 'perfect_setup' in df.columns else 0
    print(f"Full TPS alignment   : {tps_count:>5}  ({tps_count/total*100 if total else 0:>5.1f}%)")
    print(f"TPS + VWAP bullish  : {tps_vwap_count:>5}  ({tps_vwap_count/total*100 if total else 0:>5.1f}%)")
    print(f"Perfect setup       : {perfect_count:>5}  ({perfect_count/total*100 if total else 0:>5.1f}%)  ← orange squeeze + 20%+ short + >5 DTC + price > ↑VWAP + volume burst on cross")

    print(f"\nCurrent state (latest bar):")
    print(f"  Upward_Trend        : {current['Upward_Trend']}")
    print(f"  Bull Flag           : {current['bull_flag']}")
    print(f"  Bull Pennant        : {current['bull_pennant']}")
    print(f"  TTM Squeeze ON      : {current['ttm_squeeze']}")
    print(f"  TTM Squeeze OFF     : {current['ttm_squeeze_fired']}  ← momentum impulse")
    print(f"  VWAP                : {current['vwap']:.2f}")
    print(f"  VWAP Rising         : {current['vwap_rising']}")
    print(f"  Price Above VWAP    : {current['price_above_vwap']}")
    print(f"  Volume Burst        : {current['volume_burst']}  (ratio={current['volume_ratio']:.2f}x)")
    print(f"  VWAP Crossed        : {current['vwap_crossed']}")
    print(f"  Burst on Cross      : {current['volume_burst_on_cross']}  ← shorts covering")
    print(f"  Short Float %       : {current['short_float_pct']}%")
    print(f"  Short Ratio         : {current['short_ratio']} days to cover")
    if args.min_short_float is not None or args.min_short_ratio is not None:
        print(f"  Short Filter        : {'✓ PASS' if short_filter_met else '✗ FAIL'}")
    print(f"  TPS Score           : {int(current['tps_score'])} / 6")

    if args.save:
        outfile = f'output/tps_scan_{args.symbol}_{datetime.now():%Y%m%d_%H%M%S}.csv'
        os.makedirs('output', exist_ok=True)
        df.to_csv(outfile)
        print(f"\nFull dataset saved to: {outfile}")


if __name__ == '__main__':
    main()
