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

import pandas as pd

# Local imports (same repo)
from strategies.ema_trend_analysis import fetch_price_history, calculate_emas, detect_upward_trend
from strategies.pattern_detection import detect_patterns


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


def run_tps_scanner(symbol: str, client, ema_lengths: tuple = (8, 21, 55),
                     pattern_window: int = 10, r2_threshold: float = 0.8) -> pd.DataFrame:
    """
    Full TPS scan on a single symbol.

    Steps:
    1. Fetch OHLCV from Schwab
    2. Compute EMAs → Upward_Trend flag
    3. Run pattern detection → bull_flag, bull_pennant
    4. Stub TTM Squeeze → ttm_squeeze (False until implemented)

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

    # ---- S: SQUEEZE (stub) ----
    df = compute_tmm_squeeze(df)

    # ---- Composite Signals ----
    df['tps_all'] = (
        df['Upward_Trend'] &
        (df['bull_flag'] | df['bull_pennant']) &
        df['ttm_squeeze']  # currently False → composite always False until squeeze implemented
    )

    # Individual signal strength (weighted count)
    df['tps_score'] = (
        df['Upward_Trend'].astype(int) +
        df['bull_flag'].astype(int) +
        df['bull_pennant'].astype(int) +
        df['ttm_squeeze'].astype(int)
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
        r2_threshold=args.r2
    )

    # Display latest rows
    cols = [
        'close', 'EMA_8', 'EMA_21', 'EMA_55', 'Upward_Trend',
        'bull_flag', 'bull_pennant',
        'SQZPRO_ON_NARROW', 'SQZPRO_ON_NORMAL', 'SQZPRO_ON_WIDE',
        'ttm_squeeze', 'ttm_squeeze_fired', 'tps_score'
    ]

    print(f"=== Latest {args.show} rows ===")
    print(df[cols].tail(args.show).to_string())

    # Summary
    total = len(df)
    print(f"\n--- Summary for {args.symbol} ---")
    print(f"Total bars: {total}")
    print(f"Upward Trend days : {df['Upward_Trend'].sum():>5}  ({df['Upward_Trend'].mean()*100:>5.1f}%)")
    print(f"Bull Flag signals  : {df['bull_flag'].sum():>5}  ({df['bull_flag'].mean()*100:>5.1f}%)")
    print(f"Bull Pennant sigs  : {df['bull_pennant'].sum():>5}  ({df['bull_pennant'].mean()*100:>5.1f}%)")
    print(f"TTM Squeeze ON     : {df['ttm_squeeze'].sum():>5}  ({df['ttm_squeeze'].mean()*100:>5.1f}%)")
    print(f"TTM Squeeze FIRED  : {df['ttm_squeeze_fired'].sum():>5}  ({df['ttm_squeeze_fired'].mean()*100:>5.1f}%)")

    # Combined signal
    tps_count = df['tps_all'].sum()
    print(f"Full TPS alignment : {tps_count:>5}  ({tps_count/total*100 if total else 0:>5.1f}%)")

    current = df.iloc[-1]
    print(f"\nCurrent state (latest bar):")
    print(f"  Upward_Trend    : {current['Upward_Trend']}")
    print(f"  Bull Flag       : {current['bull_flag']}")
    print(f"  Bull Pennant    : {current['bull_pennant']}")
    print(f"  TTM Squeeze ON  : {current['ttm_squeeze']}")
    print(f"  TTM Squeeze OFF : {current['ttm_squeeze_fired']}  ← momentum impulse")
    print(f"  TPS Score       : {int(current['tps_score'])} / 4")

    if args.save:
        outfile = f'output/tps_scan_{args.symbol}_{datetime.now():%Y%m%d_%H%M%S}.csv'
        os.makedirs('output', exist_ok=True)
        df.to_csv(outfile)
        print(f"\nFull dataset saved to: {outfile}")


if __name__ == '__main__':
    main()
