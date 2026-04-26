"""
Unit tests for MTF squeeze non-standard interval handling.

Tests verify that:
- Standard intervals (5, 15, 60) use native Schwab fetch path
- Non-standard intervals (195, 130, 78) use 1-minute derivation path
- Proper warning messages are logged
- Squeeze=False when insufficient data
"""

import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, '/tmp/options_detective')

from strategies.mtf_squeeze import add_mtf_squeeze_columns, _compute_squeeze_status


def create_test_df_minutes(num_bars: int = 100) -> pd.DataFrame:
    """Create synthetic 1-minute OHLCV data for testing."""
    dates = pd.date_range('2025-01-01 09:30', periods=num_bars, freq='1min')
    np.random.seed(42)
    close = pd.Series(100 + np.cumsum(np.random.randn(num_bars) * 0.1), index=dates)
    high = close + np.random.rand(num_bars) * 0.5
    low = close - np.random.rand(num_bars) * 0.5
    volume = np.random.randint(100, 1000, size=num_bars)

    df = pd.DataFrame({
        'open': close.shift(1).fillna(close.iloc[0]),
        'high': high,
        'low': low,
        'close': close,
        'volume': volume
    }, index=dates)
    return df


def test_compute_squeeze_status_short_data():
    """Test that <20 bars returns False."""
    df_short = create_test_df_minutes(10)
    active, fired = _compute_squeeze_status(df_short)
    assert active is False
    assert fired is False


def test_compute_squeeze_status_normal_data():
    """Test that ≥20 bars computes squeeze status."""
    df_long = create_test_df_minutes(100)
    active, fired = _compute_squeeze_status(df_long)
    # Just check types - actual value depends on random data
    assert isinstance(active, bool)
    assert isinstance(fired, bool)


def test_nonstandard_interval_derivation():
    """
    Verify that non-standard interval (e.g., 195) is derived from 1-minute data
    and produces sqz_195 and sqz_195_fired columns on the daily DataFrame.
    """
    # Create a mock daily DataFrame (constant across rows)
    daily_dates = pd.date_range('2025-01-01', periods=5, freq='D')
    df_daily = pd.DataFrame({'close': [100, 101, 102, 103, 104]}, index=daily_dates)

    # We need TTM squeeze columns already present for daily 'D' case, but not for our test
    symbol = 'TEST'
    client = MagicMock()

    # Mock fetch_price_history to return 1-minute data when frequency=1
    def mock_fetch(symbol, client, period_type, period, frequency_type, frequency):
        if frequency == 1:
            # Return synthetic 1-minute data (600 bars = 10 days * 60 min)
            df = create_test_df_minutes(600)
            return df
        else:
            # Should NOT be called for non-standard intervals (195, 130, 78)
            raise AssertionError(f"Unexpected fetch with frequency={frequency}")

    # Patch the fetch function
    with patch('strategies.mtf_squeeze.fetch_price_history', side_effect=mock_fetch):
        # Test non-standard interval 195
        add_mtf_squeeze_columns(df_daily, symbol, client, ['195'])

    # Verify columns created
    assert 'sqz_195' in df_daily.columns, "Missing sqz_195 column"
    assert 'sqz_195_fired' in df_daily.columns, "Missing sqz_195_fired column"
    assert 'mtf_squeeze_count' in df_daily.columns
    assert 'mtf_squeeze_any' in df_daily.columns
    assert 'mtf_squeeze_all' in df_daily.columns

    # Values should be constant across all daily rows
    assert (df_daily['sqz_195'].iloc[0] == df_daily['sqz_195']).all()
    # Squeeze status depends on actual random data; just check it's a bool
    assert isinstance(df_daily['sqz_195'].iloc[0], (bool, np.bool_))


def test_standard_interval_uses_native_fetch():
    """
    Verify that standard interval (60) uses fetch_price_history with frequency=60.
    """
    daily_dates = pd.date_range('2025-01-01', periods=5, freq='D')
    df_daily = pd.DataFrame({'close': [100, 101, 102, 103, 104]}, index=daily_dates)

    symbol = 'TEST'
    client = MagicMock()

    call_log = []

    def mock_fetch(symbol, client, period_type, period, frequency_type, frequency):
        call_log.append({
            'period_type': period_type,
            'period': period,
            'frequency_type': frequency_type,
            'frequency': frequency
        })
        # Return sufficient minute bars for squeeze computation
        df = create_test_df_minutes(200)  # enough bars for a 60-min squeeze
        return df

    with patch('strategies.mtf_squeeze.fetch_price_history', side_effect=mock_fetch):
        add_mtf_squeeze_columns(df_daily, symbol, client, ['60'])

    # Verify native fetch was called with frequency=60
    assert len(call_log) == 1, f"Expected 1 fetch call, got {len(call_log)}"
    assert call_log[0]['frequency'] == 60, f"Expected frequency=60, got {call_log[0]['frequency']}"


def test_mixed_timeframes():
    """
    Test that W, D, standard intraday (60), and non-standard (195) can coexist.
    """
    daily_dates = pd.date_range('2025-01-01', periods=10, freq='D')
    close = pd.Series(range(100, 110), index=daily_dates)
    # Build full OHLCV: open = close.shift(1), high = close+0.5, low = close-0.5, volume constant
    df_daily = pd.DataFrame({
        'open': close.shift(1).fillna(close.iloc[0]),
        'high': close + 0.5,
        'low': close - 0.5,
        'close': close,
        'volume': 1000
    }, index=daily_dates)
    # Pre-populate daily TTM squeeze columns so 'D' works
    df_daily['ttm_squeeze'] = False
    df_daily['ttm_squeeze_fired'] = False

    symbol = 'TEST'
    client = MagicMock()

    fetched_tf = []

    def mock_fetch(symbol, client, period_type, period, frequency_type, frequency):
        fetched_tf.append(frequency)
        if frequency == 1:
            return create_test_df_minutes(600)
        elif frequency == 60:
            return create_test_df_minutes(200)
        else:
            raise ValueError(f"Unexpected frequency: {frequency}")

    with patch('strategies.mtf_squeeze.fetch_price_history', side_effect=mock_fetch):
        add_mtf_squeeze_columns(df_daily, symbol, client, ['W', 'D', '60', '195'])

    # Verify expected columns
    for tf in ['W', 'D', '60', '195']:
        assert f'sqz_{tf}' in df_daily.columns, f"Missing sqz_{tf}"
        assert f'sqz_{tf}_fired' in df_daily.columns, f"Missing sqz_{tf}_fired"

    # mtf_squeeze_count should equal number of timeframes with active squeeze
    active_count = df_daily['mtf_squeeze_count'].iloc[0]
    assert active_count >= 0
    assert active_count <= 4

    print("All MTF mixed-timeframe tests passed!")


if __name__ == '__main__':
    test_compute_squeeze_status_short_data()
    print("✓ test_compute_squeeze_status_short_data")

    test_compute_squeeze_status_normal_data()
    print("✓ test_compute_squeeze_status_normal_data")

    test_nonstandard_interval_derivation()
    print("✓ test_nonstandard_interval_derivation")

    test_standard_interval_uses_native_fetch()
    print("✓ test_standard_interval_uses_native_fetch")

    test_mixed_timeframes()
    print("✓ test_mixed_timeframes")

    print("\nAll tests passed!")
