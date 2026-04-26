"""
EMA Trend Analyzer for Options Detective (nate.tps.pro)

Fetches OHLCV data from Schwab API and calculates 8/21/55 EMAs using pandas-ta.
Identifies bullish trend alignment: EMA_8 > EMA_21 > EMA_55.

Usage:
    python strategies/ema_trend_analysis.py

Set environment variables:
    SCHWAB_API_KEY=<your_app_key>
    SCHWAB_API_SECRET=<your_app_secret>
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pandas_ta as ta
from schwabdev import Client


def fetch_price_history(symbol: str, client, period_type='month', period=6,
                        frequency_type='daily', frequency=1) -> pd.DataFrame:
    """Fetch raw OHLCV candles from Schwab and return as DataFrame."""
    resp = client.price_history(
        symbol=symbol,
        period_type=period_type,
        period=period,
        frequency_type=frequency_type,
        frequency=frequency
    )
    data = resp.json()
    if 'candles' not in data:
        raise ValueError(f"No candle data returned: {data}")
    df = pd.DataFrame(data['candles'])
    # Convert datetime from epoch seconds
    df['datetime'] = pd.to_datetime(df['datetime'], unit='s', utc=True)
    df.set_index('datetime', inplace=True)
    return df


def calculate_emas(df: pd.DataFrame, lengths=(8, 21, 55)) -> pd.DataFrame:
    """Calculate EMAs for the given lengths and append to DataFrame."""
    for length in lengths:
        df.ta.ema(length=length, append=True)
    return df


def detect_upward_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Detect upward trend: EMA_8 > EMA_21 > EMA_55."""
    df['Upward_Trend'] = (
        (df['EMA_8'] > df['EMA_21']) &
        (df['EMA_21'] > df['EMA_55'])
    )
    return df


def main():
    # Load credentials from environment
    api_key = os.getenv('SCHWAB_API_KEY')
    api_secret = os.getenv('SCHWAB_API_SECRET')

    if not api_key or not api_secret:
        print("ERROR: SCHWAB_API_KEY and SCHWAB_API_SECRET must be set in environment.", file=sys.stderr)
        print("Example: export SCHWAB_API_KEY='xxx'; export SCHWAB_API_SECRET='yyy'", file=sys.stderr)
        sys.exit(1)

    # Initialize client
    client = Client(api_key, api_secret)

    # Analyze SPY (configurable)
    symbol = os.getenv('ANALYSIS_SYMBOL', 'SPY')
    print(f"Fetching {symbol} price history...")

    df = fetch_price_history(symbol, client)

    print(f"Rows fetched: {len(df)}  date range: {df.index.min()} to {df.index.max()}")

    # Calculate EMAs
    df = calculate_emas(df)

    # Detect trend
    df = detect_upward_trend(df)

    # Display tail
    cols = ['close', 'open', 'high', 'low', 'volume', 'EMA_8', 'EMA_21', 'EMA_55', 'Upward_Trend']
    print("\n=== Latest Data ===")
    print(df[cols].tail(10).to_string())

    # Summary stats
    trend_pct = df['Upward_Trend'].mean() * 100
    print(f"\nUpward trend rate: {trend_pct:.1f}% of days")
    print(f"Currently upward: {df['Upward_Trend'].iloc[-1]}")

    # Optional: Save to CSV
    if '--save' in sys.argv:
        outfile = f'output/ema_trend_{symbol}_{datetime.now():%Y%m%d}.csv'
        os.makedirs('output', exist_ok=True)
        df.to_csv(outfile)
        print(f"\nSaved full dataset to: {outfile}")


if __name__ == '__main__':
    main()
