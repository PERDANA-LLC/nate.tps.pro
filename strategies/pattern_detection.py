"""
Pattern Detection Module for Nate (TPS: Pattern)

Detects bull flags and bull pennants using vectorized linear regression
on rolling high/low windows.

Math:
- Slope = Cov(x,y) / Var(x) via convolution (O(n) sliding window)
- R² = (slope² * Var(X)) / Var(Y)  → measures linear fit quality
- Bull Flag: both slopes negative, nearly parallel (|slope_high - slope_low| < threshold)
- Bull Pennant: resistance slope negative, support slope positive (converging)

Usage:
    from strategies.pattern_detection import detect_patterns
    df = detect_patterns(df, window=10, r2_threshold=0.8)
    print(df[['bull_flag', 'pennant']].tail())
"""

import numpy as np
import pandas as pd


def fast_rolling_patterns(df: pd.DataFrame, window: int = 10, r2_threshold: float = 0.8) -> pd.DataFrame:
    """
    Vectorized detection of bull flag and bull pennant patterns.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: 'high', 'low' (and optionally 'close')
    window : int
        Rolling window size for pattern detection (default 10)
    r2_threshold : float
        Minimum R-squared for considering a linear fit valid (default 0.8)

    Returns
    -------
    pd.DataFrame
        Original DataFrame with added columns:
        - res_slope, sup_slope : regression slopes on high/low
        - bull_flag : bool — parallel downtrend on high & low (flag)
        - pennant : bool — converging high & low (pennant/wedge)
    """
    if len(df) < window:
        raise ValueError(f"DataFrame length {len(df)} is less than window {window}")

    x = np.arange(window)
    x_dev = x - x.mean()
    x_var_sum = np.sum(x_dev**2)
    weights = x_dev / x_var_sum  # normalized weights for slope (derivative of linear regression)

    x_var = np.var(x, ddof=0)    # variance of X for R² denominator

    # 1. Vectorized slope via 1D convolution
    #   slope ≈ Σ((x - x̄) * (y - ȳ)) / Σ((x - x̄)²)
    #   With pre-centered X and normalized weights: slope = Σ(weights * y)
    res_slope = np.convolve(df['high'].values, weights[::-1], mode='valid')
    sup_slope = np.convolve(df['low'].values, weights[::-1], mode='valid')

    # 2. Vectorized R-squared
    #   R² = (slope² * Var(X)) / Var(Y)  for simple linear regression
    res_y_var = df['high'].rolling(window).var(ddof=0).dropna().values
    sup_y_var = df['low'].rolling(window).var(ddof=0).dropna().values

    # Avoid division by zero
    res_r2 = (res_slope**2 * x_var) / (res_y_var + 1e-8)
    sup_r2 = (sup_slope**2 * x_var) / (sup_y_var + 1e-8)

    # 3. Pad beginnings with NaN to align with original index
    pad = np.full(window - 1, np.nan)
    df['res_slope'] = np.concatenate((pad, res_slope))
    df['sup_slope'] = np.concatenate((pad, sup_slope))
    df['res_r2'] = np.concatenate((pad, res_r2))
    df['sup_r2'] = np.concatenate((pad, sup_r2))

    # 4. Pattern Logic
    valid_fit = (df['res_r2'] >= r2_threshold) & (df['sup_r2'] >= r2_threshold)

    # Bull Flag: parallel downward channels on both high and low slopes
    df['bull_flag'] = (
        valid_fit &
        (df['res_slope'] < 0) &
        (df['sup_slope'] < 0) &
        (abs(df['res_slope'] - df['sup_slope']) < 0.2)  # near-parallel
    )

    # Bull Pennant: resistance slopes down, support slopes up (converging)
    df['bull_pennant'] = (
        valid_fit &
        (df['res_slope'] < 0) &
        (df['sup_slope'] > 0)
    )

    return df


def detect_patterns(df: pd.DataFrame, window: int = 10, r2_threshold: float = 0.8) -> pd.DataFrame:
    """Alias for fast_rolling_patterns for cleaner imports."""
    return fast_rolling_patterns(df, window, r2_threshold)


if __name__ == '__main__':
    # Demo: generate mock data and run detection
    np.random.seed(42)
    dates = pd.date_range('2025-01-01', periods=100, freq='D')

    # Mock uptrend with bull flag around day 50
    close = 100 + np.cumsum(np.random.randn(100) * 2)
    high = close + np.random.rand(100) * 2
    low = close - np.random.rand(100) * 2

    df = pd.DataFrame({'high': high, 'low': low, 'close': close}, index=dates)

    result = detect_patterns(df, window=10)
    print("Latest pattern flags:")
    print(result[['close', 'res_slope', 'sup_slope', 'bull_flag', 'bull_pennant']].tail(15))

    flag_days = result['bull_flag'].sum()
    pennant_days = result['bull_pennant'].sum()
    print(f"\nBull flag signals: {int(flag_days)}")
    print(f"Bull pennant signals: {int(pennant_days)}")
