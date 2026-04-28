"""
TPS Scanner for project nate.

TPS = TREND + PATTERN + SQUEEZE
  - TREND  : EMA stack (8 > 21 > 55 = upward, reverse = downward)
  - PATTERN: bull flag / bull pennant via vectorized rolling regression
  - SQUEEZE: TTM Squeeze Pro (3 Keltner Channels: wide / normal / narrow)
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv
from schwabdev import Client

load_dotenv()


def _get_client() -> Client:
    """Build a schwabdev Client from environment variables."""
    app_key = os.getenv("SCHWAB_APP_KEY")
    app_secret = os.getenv("SCHWAB_APP_SECRET")
    callback_url = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    if not app_key or not app_secret:
        raise RuntimeError(
            "Missing SCHWAB_APP_KEY / SCHWAB_APP_SECRET. "
            "Copy .env.example to .env and fill them in."
        )
    return Client(app_key, app_secret, callback_url)


def _fetch_daily_candles(
    client: Client,
    symbol: str,
    months: int = 6,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV candles for `symbol` over the last `months` months.

    Returns a DataFrame with columns:
        datetime, open, high, low, close, volume
    indexed by a tz-naive UTC timestamp.
    """
    resp = client.price_history(
        symbol=symbol,
        periodType="month",
        period=months,
        frequencyType="daily",
        frequency=1,
    )
    payload = resp.json()
    candles = payload.get("candles", [])
    if not candles:
        raise RuntimeError(f"No candles returned for {symbol}: {payload}")

    df = pd.DataFrame(candles)
    # Schwab returns 'datetime' as epoch ms
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    df = df.set_index("datetime").sort_index()
    return df


def _compute_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA_8/21/55 and Upward_Trend / Downward_Trend booleans in-place."""
    df.ta.ema(length=8, append=True)
    df.ta.ema(length=21, append=True)
    df.ta.ema(length=55, append=True)
    df["Upward_Trend"] = (df["EMA_8"] > df["EMA_21"]) & (df["EMA_21"] > df["EMA_55"])
    df["Downward_Trend"] = (df["EMA_8"] < df["EMA_21"]) & (df["EMA_21"] < df["EMA_55"])
    return df


def fast_rolling_patterns(
    df: pd.DataFrame,
    window: int = 10,
    r2_threshold: float = 0.8,
    parallel_tol: float = 0.2,
) -> pd.DataFrame:
    """
    Vectorized rolling detection of bull flag and bull pennant consolidations.

    Fits two simple linear regressions over the last `window` bars:
        - resistance line on highs
        - support  line on lows

    Adds these columns in-place:
        res_slope, sup_slope : slopes per bar
        bull_flag            : both lines sloping down with similar slope (parallel)
        pennant              : resistance down, support up (converging)

    Parameters
    ----------
    df : DataFrame with 'high' and 'low' columns
    window : rolling window length (bars)
    r2_threshold : minimum R^2 required for a valid fit on each line
    parallel_tol : max |res_slope - sup_slope| to be considered parallel (bull flag)
    """
    x = np.arange(window)
    x_dev = x - x.mean()
    x_var_sum = np.sum(x_dev ** 2)
    weights = x_dev / x_var_sum            # constant slope weights
    x_var = np.var(x, ddof=0)              # constant X variance for R^2

    # Vectorized slope via 1D convolution (np.convolve reverses kernel)
    res_slope = np.convolve(df["high"].values, weights[::-1], mode="valid")
    sup_slope = np.convolve(df["low"].values, weights[::-1], mode="valid")

    # Vectorized R^2: R^2 = (slope^2 * Var(X)) / Var(Y)
    res_y_var = df["high"].rolling(window).var(ddof=0).dropna().values
    sup_y_var = df["low"].rolling(window).var(ddof=0).dropna().values
    res_r2 = (res_slope ** 2 * x_var) / (res_y_var + 1e-8)
    sup_r2 = (sup_slope ** 2 * x_var) / (sup_y_var + 1e-8)

    # Pad leading NaNs so arrays align with df
    pad = np.full(window - 1, np.nan)
    df["res_slope"] = np.concatenate((pad, res_slope))
    df["sup_slope"] = np.concatenate((pad, sup_slope))
    res_r2 = np.concatenate((pad, res_r2))
    sup_r2 = np.concatenate((pad, sup_r2))

    valid_fit = (res_r2 >= r2_threshold) & (sup_r2 >= r2_threshold)

    df["bull_flag"] = (
        valid_fit
        & (df["res_slope"] < 0)
        & (df["sup_slope"] < 0)
        & (np.abs(df["res_slope"] - df["sup_slope"]) < parallel_tol)
    )
    df["pennant"] = (
        valid_fit
        & (df["res_slope"] < 0)
        & (df["sup_slope"] > 0)
    )
    return df


def _compute_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    """
    Append TTM Squeeze Pro columns via pandas_ta.squeeze_pro.

    Columns added (1 = active, 0 = inactive):
        SQZPRO_ON_NARROW  high-intensity (tight) squeeze
        SQZPRO_ON_NORMAL  standard squeeze
        SQZPRO_ON_WIDE    low-intensity squeeze
        SQZPRO_OFF_WIDE   squeeze fired / released
        SQZPRO_NO         no squeeze
    Plus the underlying SQZPRO momentum histogram (SQZPRO_*).
    """
    df.ta.squeeze_pro(append=True)

    # Convenience boolean: any squeeze currently on
    on_cols = [c for c in ("SQZPRO_ON_NARROW", "SQZPRO_ON_NORMAL", "SQZPRO_ON_WIDE")
               if c in df.columns]
    if on_cols:
        df["squeeze_on"] = df[on_cols].fillna(0).astype(bool).any(axis=1)
    if "SQZPRO_OFF_WIDE" in df.columns:
        df["squeeze_fired"] = df["SQZPRO_OFF_WIDE"].fillna(0).astype(bool)
    return df


def _compute_vwap_volume(
    df: pd.DataFrame,
    vwap_window: int = 20,
    slope_lookback: int = 3,
    near_band: float = 0.02,
    vol_window: int = 20,
    vol_burst_mult: float = 1.5,
) -> pd.DataFrame:
    """
    Append rolling-VWAP and volume-burst columns.

    True session-anchored VWAP only makes sense intraday; for daily candles
    we use a **rolling VWAP** over ``vwap_window`` bars:

        VWAP_t = sum_{i=t-N+1..t}(typical_i * volume_i) / sum_{i}(volume_i)

    where ``typical = (high + low + close) / 3``.

    Columns added
    -------------
    vwap                  rolling VWAP series
    vwap_slope_up         bool, vwap > vwap.shift(slope_lookback)
    price_above_vwap      bool, close > vwap
    price_near_vwap       bool, 0 < (close - vwap) / vwap <= near_band
                          ("just above")
    vwap_uptrend_setup    bool, price_above & price_near & slope_up
                          (KPI gate 7)
    vwap_cross_up         bool, prev_close <= prev_vwap & close > vwap
    volume_burst          bool, volume > vol_burst_mult * rolling-mean(volume,
                          vol_window)
    vwap_cross_with_burst bool, vwap_cross_up AND volume_burst on same bar
                          (KPI gate 8 — "shorts getting liquidated")
    """
    required = {"high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"_compute_vwap_volume requires columns {missing}")

    typ = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (typ * df["volume"]).rolling(vwap_window, min_periods=vwap_window).sum()
    vv = df["volume"].rolling(vwap_window, min_periods=vwap_window).sum()
    vwap = pv / vv
    df["vwap"] = vwap

    df["vwap_slope_up"] = (vwap > vwap.shift(slope_lookback)).fillna(False)
    df["price_above_vwap"] = (df["close"] > vwap).fillna(False)
    rel = (df["close"] - vwap) / vwap
    df["price_near_vwap"] = ((rel > 0) & (rel <= near_band)).fillna(False)

    df["vwap_uptrend_setup"] = (
        df["price_above_vwap"]
        & df["price_near_vwap"]
        & df["vwap_slope_up"]
    )

    cross_up = (df["close"] > vwap) & (df["close"].shift(1) <= vwap.shift(1))
    df["vwap_cross_up"] = cross_up.fillna(False)

    avg_vol = df["volume"].rolling(vol_window, min_periods=vol_window).mean()
    df["volume_burst"] = (df["volume"] > vol_burst_mult * avg_vol).fillna(False)

    df["vwap_cross_with_burst"] = df["vwap_cross_up"] & df["volume_burst"]
    return df


def _fetch_short_interest_fmp(symbol: str, api_key: str) -> dict:
    """
    Fetch latest short-interest record from Financial Modeling Prep.

    Endpoint (Premium tier required):
        GET https://financialmodelingprep.com/api/v4/short-interest
            ?symbol={symbol}&apikey={api_key}

    FMP returns a list of bi-weekly FINRA records (newest first); we take
    the first one. Field names per FMP docs:
        - shortPercentOfFloat : fraction OR percent depending on instrument
                                — FMP exposes it as a number like 23.5
                                  (already percent). We pass through as-is.
        - daysToCover         : days to cover

    Returns dict with short_float_pct / short_ratio (None on failure).
    """
    import urllib.request
    import urllib.parse
    import json

    url = (
        "https://financialmodelingprep.com/api/v4/short-interest?"
        + urllib.parse.urlencode({"symbol": symbol, "apikey": api_key})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"short_float_pct": None, "short_ratio": None}

    if not isinstance(data, list) or not data:
        return {"short_float_pct": None, "short_ratio": None}

    rec = data[0]  # newest record
    spf = rec.get("shortPercentOfFloat")
    dtc = rec.get("daysToCover")

    # FMP's shortPercentOfFloat is already in percent units (e.g. 23.5).
    # Guard against the rare case where it slips in as a fraction (<1.0).
    if spf is not None:
        spf = float(spf)
        if spf < 1.0:
            spf *= 100.0

    return {
        "short_float_pct": spf,
        "short_ratio": float(dtc) if dtc is not None else None,
    }


def _fetch_short_interest_yf(symbol: str) -> dict:
    """yfinance fallback. Returns the same dict shape as the FMP fetcher."""
    try:
        import yfinance as yf
    except ImportError:
        return {"short_float_pct": None, "short_ratio": None}

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return {"short_float_pct": None, "short_ratio": None}

    spf = info.get("shortPercentOfFloat")  # fraction, e.g. 0.235
    sr = info.get("shortRatio")            # days to cover

    return {
        "short_float_pct": float(spf) * 100.0 if spf is not None else None,
        "short_ratio": float(sr) if sr is not None else None,
    }


def _fetch_short_interest(symbol: str) -> dict:
    """
    Pluggable short-interest fetcher.

    Priority:
      1. Financial Modeling Prep (if FMP_API_KEY env var is set)
         -> /api/v4/short-interest, FINRA bi-weekly, Premium tier required.
      2. yfinance fallback (free, current snapshot only).
      3. {None, None} -> KPI short-interest gates evaluate to False.

    Schwab's Developer API does not expose short-interest data, so we never
    consult it here.

    Returns
    -------
    dict with keys:
        short_float_pct : float | None  (percent, e.g. 23.5 means 23.5%)
        short_ratio     : float | None  (days-to-cover)
    """
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if api_key:
        out = _fetch_short_interest_fmp(symbol, api_key)
        if out["short_float_pct"] is not None or out["short_ratio"] is not None:
            return out
        # Otherwise fall through to yfinance.

    return _fetch_short_interest_yf(symbol)


def KPI(
    df: pd.DataFrame,
    short_float_min: float = 20.0,
    short_ratio_min: float = 5.0,
    cross_lookback: int = 3,
) -> pd.DataFrame:
    """
    Setup-Checklist KPI for the "perfect" bullish alignment used by
    long-call / bull-put-spread option setups.

    Perfect setup requires ALL of:
      1. TREND is up                  : Upward_Trend == True
      2. PATTERN is bull flag/pennant : bull_flag OR pennant
      3. TTM Squeeze Pro Orange dot   : SQZPRO_ON_NARROW == 1
         (orange = narrow = high compression)
      4. Momentum Histogram Cyan      : SQZPRO > 0 AND SQZPRO rising
         (cyan = above zero AND increasing vs prior bar)
      5. Short Float %                 : > short_float_min  (default 20.0)
      6. Short Ratio (days to cover)   : > short_ratio_min  (default 5.0)
      7. VWAP uptrend setup            : price just above upward-sloping VWAP
         (vwap_uptrend_setup == True on the bar)
      8. VWAP-cross Volume Burst       : a vwap_cross_with_burst event
         occurred within the last ``cross_lookback`` bars (inclusive)
         — "shorts getting liquidated"

    Short-interest gates require the columns ``short_float_pct`` and
    ``short_ratio`` already broadcast onto df (TPS_SCAN does this via
    ``_fetch_short_interest``). If they are missing or NaN, those two
    gates evaluate to False (conservative).

    VWAP/volume gates require ``_compute_vwap_volume`` columns
    (``vwap_uptrend_setup``, ``vwap_cross_with_burst``). If missing,
    those gates also evaluate to False.

    Why ``cross_lookback`` for gate 8?
        A VWAP-cross-with-burst is a single-bar event; requiring it on
        the *exact* same bar as all other gates (squeeze, pattern, etc.)
        almost never aligns. Allowing the cross to have happened within
        the last few bars (default 3) captures "we just crossed and the
        shorts are now bleeding" — which is the actual trade trigger.

    Adds columns:
      - momo_cyan          : bool, cyan-histogram condition
      - short_squeeze_ok   : bool, both short-interest gates pass
      - vwap_setup_ok      : bool, gate 7 (= vwap_uptrend_setup)
      - vwap_burst_recent  : bool, gate 8 (rolling-OR over cross_lookback)
      - KPI_PERFECT        : bool, all 8 conditions met on that bar
      - KPI_SCORE          : int 0..8, count of conditions met

    Operates in place and also returns the DataFrame for chaining.
    """
    required = ["Upward_Trend", "bull_flag", "pennant", "SQZPRO_ON_NARROW"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"KPI requires columns {missing}. "
            "Run TPS_SCAN (trend + pattern + squeeze) before KPI."
        )

    # Locate the momentum histogram column produced by pandas_ta.squeeze_pro.
    # In pandas-ta it is typically "SQZPRO_20_2.0_20_2_1.5_1" (parameters in the
    # name). Find it heuristically: the SQZPRO_* column that is NOT a flag.
    flag_cols = {
        "SQZPRO_ON_WIDE", "SQZPRO_ON_NORMAL", "SQZPRO_ON_NARROW",
        "SQZPRO_OFF_WIDE", "SQZPRO_OFF", "SQZPRO_NO",
    }
    momo_candidates = [
        c for c in df.columns
        if c.startswith("SQZPRO") and c not in flag_cols
    ]
    if not momo_candidates:
        raise KeyError(
            "Could not find SQZPRO momentum histogram column. "
            "Ensure pandas_ta.squeeze_pro produced the histogram value column."
        )
    momo_col = momo_candidates[0]
    momo = df[momo_col]

    trend_up = df["Upward_Trend"].fillna(False).astype(bool)
    pattern_ok = (
        df["bull_flag"].fillna(False).astype(bool)
        | df["pennant"].fillna(False).astype(bool)
    )
    high_compression = df["SQZPRO_ON_NARROW"].fillna(0).astype(bool)
    momo_cyan = (momo > 0) & (momo > momo.shift(1))
    momo_cyan = momo_cyan.fillna(False)

    df["momo_cyan"] = momo_cyan

    # ---- 5 & 6. short-interest gates -------------------------------------
    # short_float_pct stored as percent (e.g. 23.5 == 23.5%)
    if "short_float_pct" in df.columns:
        sf = pd.to_numeric(df["short_float_pct"], errors="coerce")
        sf_ok = (sf > short_float_min).fillna(False)
    else:
        sf_ok = pd.Series(False, index=df.index)

    if "short_ratio" in df.columns:
        sr = pd.to_numeric(df["short_ratio"], errors="coerce")
        sr_ok = (sr > short_ratio_min).fillna(False)
    else:
        sr_ok = pd.Series(False, index=df.index)

    short_squeeze_ok = sf_ok & sr_ok
    df["short_squeeze_ok"] = short_squeeze_ok

    # ---- 7. VWAP uptrend setup -------------------------------------------
    if "vwap_uptrend_setup" in df.columns:
        vwap_setup_ok = df["vwap_uptrend_setup"].fillna(False).astype(bool)
    else:
        vwap_setup_ok = pd.Series(False, index=df.index)
    df["vwap_setup_ok"] = vwap_setup_ok

    # ---- 8. VWAP-cross + Volume Burst within last cross_lookback bars ----
    if "vwap_cross_with_burst" in df.columns:
        cwb = df["vwap_cross_with_burst"].fillna(False).astype(bool)
        # rolling-OR over the trailing window (inclusive of current bar)
        win = max(int(cross_lookback), 1)
        vwap_burst_recent = (
            cwb.rolling(window=win, min_periods=1).max().astype(bool)
        )
    else:
        vwap_burst_recent = pd.Series(False, index=df.index)
    df["vwap_burst_recent"] = vwap_burst_recent

    df["KPI_PERFECT"] = (
        trend_up
        & pattern_ok
        & high_compression
        & momo_cyan
        & short_squeeze_ok
        & vwap_setup_ok
        & vwap_burst_recent
    )
    df["KPI_SCORE"] = (
        trend_up.astype(int)
        + pattern_ok.astype(int)
        + high_compression.astype(int)
        + momo_cyan.astype(int)
        + sf_ok.astype(int)
        + sr_ok.astype(int)
        + vwap_setup_ok.astype(int)
        + vwap_burst_recent.astype(int)
    )
    return df


def TPS_SCAN(
    symbol: str = "SPY",
    months: int = 6,
    pattern_window: int = 10,
    r2_threshold: float = 0.8,
    parallel_tol: float = 0.2,
    client: Optional[Client] = None,
) -> pd.DataFrame:
    """
    Run the TPS scan for a single symbol.

    Implements:
      - TREND  : EMA_8/21/55 + Upward_Trend / Downward_Trend
      - PATTERN: bull_flag / pennant (vectorized rolling regression)
      - SQUEEZE: TTM Squeeze Pro (TBD)

    Parameters
    ----------
    symbol : str
        Ticker, e.g. "SPY".
    months : int
        Lookback window in months for daily candles.
    pattern_window : int
        Rolling window length used for bull-flag / pennant detection.
    r2_threshold : float
        Minimum R^2 for a valid trendline fit on highs and lows.
    parallel_tol : float
        Max |res_slope - sup_slope| considered "parallel" (bull flag).
    client : schwabdev.Client, optional
        Pre-built client. If None, builds one from env vars.

    Returns
    -------
    pandas.DataFrame
        Indexed by datetime, with OHLCV + trend cols + pattern cols.
    """
    if client is None:
        client = _get_client()

    df = _fetch_daily_candles(client, symbol, months=months)
    df = _compute_trend(df)
    df = fast_rolling_patterns(
        df,
        window=pattern_window,
        r2_threshold=r2_threshold,
        parallel_tol=parallel_tol,
    )
    df = _compute_squeeze(df)
    df = _compute_vwap_volume(df)

    # Broadcast short-interest scalars onto every bar so KPI can vectorize.
    short = _fetch_short_interest(symbol)
    df["short_float_pct"] = short["short_float_pct"]
    df["short_ratio"] = short["short_ratio"]

    df = KPI(df)

    # ---- SPY correlation / beta (broadcast scalars onto every bar) -------
    # For SPY itself this is trivially (1.0, 1.0) — skip the API call.
    if symbol.upper() == "SPY":
        df["spy_corr"] = 1.0
        df["spy_beta"] = 1.0
    else:
        try:
            spy_daily = _fetch_daily_candles(client, "SPY", months=months)
            corr, beta = calculate_correlation_and_beta(df, spy_daily)
            df["spy_corr"] = corr
            df["spy_beta"] = beta
        except Exception as e:  # noqa: BLE001 — never fail the whole scan
            print(f"[warn] SPY correlation failed for {symbol}: {e}")
            df["spy_corr"] = float("nan")
            df["spy_beta"] = float("nan")

    # ---- QQQ correlation / beta + regime (broadcast scalars onto every bar)
    try:
        qqq_daily = _fetch_daily_candles(client, "QQQ", months=months)
        if symbol.upper() == "QQQ":
            df["qqq_corr"] = 1.0
            df["qqq_beta"] = 1.0
        else:
            q_corr, q_beta = calculate_correlation_and_beta(df, qqq_daily)
            df["qqq_corr"] = q_corr
            df["qqq_beta"] = q_beta

        trend = analyze_qqq_trend(qqq_df=qqq_daily)
        df["qqq_price"]  = trend["price"]
        df["qqq_sma20"]  = trend["sma20"]
        df["qqq_rsi"]    = trend["rsi"]
        df["qqq_regime"] = trend["regime"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] QQQ analysis failed for {symbol}: {e}")
        df["qqq_corr"]   = float("nan")
        df["qqq_beta"]   = float("nan")
        df["qqq_price"]  = float("nan")
        df["qqq_sma20"]  = float("nan")
        df["qqq_rsi"]    = float("nan")
        df["qqq_regime"] = "neutral"

    # ---- VIX volatility regime + correlation (broadcast scalars) ----------
    try:
        vix_daily = _fetch_vix_daily(client, months=months)

        # Correlation of stock vs VIX daily returns
        vix_corr_info = compute_vix_correlation(
            symbol, months=months, client=client,
            stock_df=df_daily if "df_daily" in locals() else None,
            vix_df=vix_daily,
        )

        # Current regime from latest close (most recent VIX bar)
        latest_vix = float(vix_daily["close"].iloc[-1])
        regime_info = analyze_vix_regime(vix_value=latest_vix)

        df["vix_level"]         = regime_info["vix"]
        df["vix_regime"]        = regime_info["regime"]
        df["vix_strategy_bias"] = regime_info["strategy_bias"]
        df["vix_corr"]          = vix_corr_info["correlation"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] VIX analysis failed for {symbol}: {e}")
        df["vix_level"]         = float("nan")
        df["vix_regime"]        = "normal_vol"
        df["vix_strategy_bias"] = "neutral"
        df["vix_corr"]          = float("nan")

    # ---- NYSE breadth ($ADD) confirmation -----------------------
    # Same row-broadcast pattern: a single market-wide snapshot
    # used to gate directional signals during scan-time review.
    try:
        breadth_info = analyze_breadth(client=client)
        df["add_level"]     = breadth_info["add"]
        df["add_slope"]     = breadth_info["slope"]
        df["add_improving"] = breadth_info["improving"]
        df["add_regime"]    = breadth_info["regime"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] Breadth analysis failed for {symbol}: {e}")
        df["add_level"]     = float("nan")
        df["add_slope"]     = float("nan")
        df["add_improving"] = False
        df["add_regime"]    = "neutral"

    # ---- $PCALL contrarian sentiment ----------------------------
    # Same row-broadcast pattern: a single market-wide snapshot
    # used to align/veto directional signals at scan time.
    try:
        pcall_info = analyze_pcall_sentiment(client=client)
        df["pcall_value"]   = pcall_info["pcall"]
        df["pcall_regime"]  = pcall_info["regime"]
        df["pcall_bias"]    = pcall_info["contrarian_bias"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] $PCALL analysis failed for {symbol}: {e}")
        df["pcall_value"]   = float("nan")
        df["pcall_regime"]  = "neutral"
        df["pcall_bias"]    = "neutral"

    # ---- $TICK intraday exhaustion ------------------------------
    # Single live snapshot at scan time. The streaming poller
    # (TickPoller) is the right tool for sub-minute timing.
    try:
        tick_info = analyze_tick_sentiment(client=client)
        df["tick_value"]   = tick_info["tick"]
        df["tick_regime"]  = tick_info["regime"]
        df["tick_bias"]    = tick_info["contrarian_bias"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] $TICK analysis failed for {symbol}: {e}")
        df["tick_value"]   = float("nan")
        df["tick_regime"]  = "neutral"
        df["tick_bias"]    = "neutral"

    # ---- VXX volatility fade ------------------------------------
    # Daily-cadence regime: panic_subsiding -> equities BULLISH
    # (fade vol spike), panic_active -> BEARISH (vol still
    # expanding). Pulls a small VXX history once per scan.
    try:
        vxx_info = analyze_vxx_extreme(client=client)
        df["vxx_price"]    = vxx_info["price"]
        df["vxx_rsi"]      = vxx_info["rsi"]
        df["vxx_ema5"]     = vxx_info["ema5"]
        df["vxx_regime"]   = vxx_info["regime"]
        df["vxx_bias"]     = vxx_info["contrarian_bias"]
    except Exception as e:  # noqa: BLE001 — never fail the whole scan
        print(f"[warn] VXX analysis failed for {symbol}: {e}")
        df["vxx_price"]    = float("nan")
        df["vxx_rsi"]      = float("nan")
        df["vxx_ema5"]     = float("nan")
        df["vxx_regime"]   = "neutral"
        df["vxx_bias"]     = "neutral"

    return df


# ============================================================================
# SPY CORRELATION / BETA
# ============================================================================
#
# Pearson correlation and beta of a stock vs SPY using daily % returns.
# Beta = Cov(stock, spy) / Var(spy).
#
# These are *scalar* metrics over the full overlap window, not per-bar
# series — TPS_SCAN broadcasts them as constants onto each row so they
# play nicely with the rest of the per-bar KPI table.
# ============================================================================


def calculate_correlation_and_beta(
    df_stock: pd.DataFrame,
    df_spy: pd.DataFrame,
) -> tuple[float, float]:
    """
    Compute (correlation, beta) of df_stock vs df_spy using daily close returns.

    Both DataFrames must be indexed by datetime and contain a ``close``
    column (the shape returned by ``_fetch_daily_candles``). Dates are
    inner-joined so unequal lookbacks are tolerated.

    Returns
    -------
    (correlation, beta) : tuple of float
        ``nan`` for either value if there is insufficient overlap or
        SPY return variance is zero.
    """
    if "close" not in df_stock.columns or "close" not in df_spy.columns:
        raise KeyError("Both inputs require a 'close' column.")

    returns_stock = df_stock["close"].pct_change().dropna()
    returns_spy = df_spy["close"].pct_change().dropna()

    data = pd.concat([returns_stock, returns_spy], axis=1, join="inner")
    data.columns = ["stock_returns", "spy_returns"]
    data = data.dropna()

    if len(data) < 2:
        return float("nan"), float("nan")

    correlation = data["stock_returns"].corr(data["spy_returns"])

    spy_variance = data["spy_returns"].var()
    if spy_variance == 0 or pd.isna(spy_variance):
        return float(correlation), float("nan")

    covariance = data.cov().iloc[0, 1]
    beta = covariance / spy_variance

    return float(correlation), float(beta)


def compute_spy_correlation(
    symbol: str,
    months: int = 6,
    client: Optional[Client] = None,
    stock_df: Optional[pd.DataFrame] = None,
    spy_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Convenience wrapper: fetch (or reuse) daily candles for ``symbol`` and
    SPY, then return ``{"symbol", "correlation", "beta", "n_obs"}``.

    Pass ``stock_df`` and/or ``spy_df`` to skip the corresponding fetch
    (e.g. when called from TPS_SCAN which already has the stock df).
    """
    if client is None and (stock_df is None or spy_df is None):
        client = _get_client()

    if stock_df is None:
        stock_df = _fetch_daily_candles(client, symbol, months=months)
    if spy_df is None:
        spy_df = _fetch_daily_candles(client, "SPY", months=months)

    corr, beta = calculate_correlation_and_beta(stock_df, spy_df)

    overlap = pd.concat(
        [stock_df["close"].pct_change(), spy_df["close"].pct_change()],
        axis=1, join="inner",
    ).dropna()

    return {
        "symbol": symbol.upper(),
        "correlation": corr,
        "beta": beta,
        "n_obs": int(len(overlap)),
    }


# ============================================================================
# QQQ TREND / CORRELATION
# ============================================================================
#
# QQQ is the tech-sector regime gauge. Two complementary views:
#
#   1. analyze_qqq_trend()    — QQQ's own price vs SMA20 + RSI14, returning
#                                a regime label ("bullish" / "bearish" /
#                                "neutral"). Mirrors the user's reference
#                                strategy snippet.
#
#   2. compute_qqq_correlation() — Pearson correlation and beta of any
#                                  symbol vs QQQ over the same lookback,
#                                  reusing calculate_correlation_and_beta.
#
# Both are wired into TPS_SCAN so each row of the scan carries:
#   qqq_corr, qqq_beta, qqq_price, qqq_sma20, qqq_rsi, qqq_regime
# ============================================================================


# Regime thresholds: bullish requires price > SMA20 *and* RSI not yet
# overbought; bearish requires price < SMA20 *and* RSI not yet oversold.
QQQ_RSI_OVERBOUGHT = 70.0
QQQ_RSI_OVERSOLD = 30.0


def analyze_qqq_trend(
    client: Optional[Client] = None,
    qqq_df: Optional[pd.DataFrame] = None,
    months: int = 3,
) -> dict:
    """
    Trend / regime read on QQQ using 20-day SMA and 14-day RSI on daily bars.

    Returns
    -------
    dict with keys:
        price, sma20, rsi, regime
    where ``regime`` ∈ {"bullish", "bearish", "neutral"}:
        bullish  — price > SMA20 and RSI < 70   → favor bull put spreads
        bearish  — price < SMA20 and RSI > 30   → favor bear call spreads
        neutral  — otherwise (no action)
    """
    if qqq_df is None:
        if client is None:
            client = _get_client()
        qqq_df = _fetch_daily_candles(client, "QQQ", months=months)

    close = pd.to_numeric(qqq_df["close"])
    sma20 = ta.sma(close, length=20)
    rsi14 = ta.rsi(close, length=14)

    price = float(close.iloc[-1])
    sma_v = float(sma20.iloc[-1]) if pd.notna(sma20.iloc[-1]) else float("nan")
    rsi_v = float(rsi14.iloc[-1]) if pd.notna(rsi14.iloc[-1]) else float("nan")

    if pd.isna(sma_v) or pd.isna(rsi_v):
        regime = "neutral"
    elif price > sma_v and rsi_v < QQQ_RSI_OVERBOUGHT:
        regime = "bullish"
    elif price < sma_v and rsi_v > QQQ_RSI_OVERSOLD:
        regime = "bearish"
    else:
        regime = "neutral"

    return {
        "price":  price,
        "sma20":  sma_v,
        "rsi":    rsi_v,
        "regime": regime,
    }


def compute_qqq_correlation(
    symbol: str,
    months: int = 6,
    client: Optional[Client] = None,
    stock_df: Optional[pd.DataFrame] = None,
    qqq_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Pearson correlation and beta of ``symbol`` vs QQQ over the daily window.

    Returns ``{"symbol", "correlation", "beta", "n_obs"}``. Pass
    pre-fetched DataFrames to skip the API calls.
    """
    if client is None and (stock_df is None or qqq_df is None):
        client = _get_client()

    if stock_df is None:
        stock_df = _fetch_daily_candles(client, symbol, months=months)
    if qqq_df is None:
        qqq_df = _fetch_daily_candles(client, "QQQ", months=months)

    corr, beta = calculate_correlation_and_beta(stock_df, qqq_df)

    overlap = pd.concat(
        [stock_df["close"].pct_change(), qqq_df["close"].pct_change()],
        axis=1, join="inner",
    ).dropna()

    return {
        "symbol":      symbol.upper(),
        "correlation": corr,
        "beta":        beta,
        "n_obs":       int(len(overlap)),
    }


# ============================================================================
# VIX VOLATILITY REGIME / CORRELATION
# ============================================================================
#
# VIX is the market's implied-volatility gauge. Two complementary views:
#
#   1. analyze_vix_regime() — current $VIX level vs static thresholds,
#         returning a regime label (high_vol / low_vol / normal_vol) plus a
#         strategy bias (premium_selling / premium_buying / neutral) used to
#         route option strategies — sell credit spreads when IV is rich,
#         buy debit spreads when IV is cheap.
#
#   2. compute_vix_correlation() — Pearson correlation of any stock's daily
#         returns vs VIX daily returns. Most equities are negatively
#         correlated to VIX (~ -0.4 to -0.7); a positive corr is a regime
#         tell (defensive / inverse-VIX / hedged names).
#
# Schwab's symbol for the CBOE Volatility Index is "$VIX". We try Schwab
# first and fall back to yfinance ("^VIX") so the helpers stay usable even
# when the Schwab session can't quote indices.
# ============================================================================

VIX_HIGH_THRESHOLD = 25.0   # > -> premium-selling regime (rich IV)
VIX_LOW_THRESHOLD  = 15.0   # < -> premium-buying regime (cheap IV)


def _fetch_vix_daily(
    client: Optional[Client],
    months: int = 6,
) -> pd.DataFrame:
    """
    Daily $VIX candles. Tries Schwab native ($VIX) first, then yfinance
    (^VIX). Returns a DataFrame with at least a 'close' column, indexed by
    a tz-naive datetime.
    """
    # Path 1: Schwab native
    if client is not None:
        try:
            return _fetch_daily_candles(client, "$VIX", months=months)
        except Exception:
            pass

    # Path 2: yfinance fallback
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "VIX history unavailable: Schwab returned no candles and "
            "yfinance is not installed."
        ) from e

    period = f"{max(int(months), 1)}mo"
    df = yf.Ticker("^VIX").history(
        period=period, interval="1d", auto_adjust=False
    )
    if df.empty:
        raise RuntimeError("yfinance returned empty VIX history")

    df = df.rename(columns={c: c.lower() for c in df.columns})
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "datetime"
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep]


def get_vix_level(client: Optional[Client] = None) -> float:
    """
    Latest VIX last price. Tries Schwab quote('$VIX') first (with a couple
    of common symbol aliases), then falls back to the most recent close
    from _fetch_vix_daily().
    """
    if client is not None:
        for symbol in ("$VIX", "VIX", "$VIX.X"):
            try:
                resp = client.quote(symbol)
                payload = resp.json() if hasattr(resp, "json") else resp
                # schwabdev: {"$VIX": {"quote": {"lastPrice": ...}, ...}}
                node = payload.get(symbol) or next(iter(payload.values()), {})
                quote = node.get("quote", node) if isinstance(node, dict) else {}
                px = (
                    quote.get("lastPrice")
                    or quote.get("mark")
                    or quote.get("closePrice")
                )
                if px is not None:
                    return float(px)
            except Exception:
                continue

    # Fallback: last close of historical fetch
    df = _fetch_vix_daily(client, months=1)
    return float(df["close"].iloc[-1])


def analyze_vix_regime(
    client: Optional[Client] = None,
    vix_value: Optional[float] = None,
    high: float = VIX_HIGH_THRESHOLD,
    low: float = VIX_LOW_THRESHOLD,
) -> dict:
    """
    Map the current VIX level to a volatility regime + option-strategy bias.

    Returns
    -------
    dict
        {
          "vix":            float,
          "regime":         "high_vol" | "low_vol" | "normal_vol",
          "strategy_bias":  "premium_selling" | "premium_buying" | "neutral",
          "high_threshold": float,
          "low_threshold":  float,
        }
    """
    if vix_value is None:
        vix_value = get_vix_level(client)
    vix_value = float(vix_value)

    if vix_value > high:
        regime, bias = "high_vol", "premium_selling"
    elif vix_value < low:
        regime, bias = "low_vol", "premium_buying"
    else:
        regime, bias = "normal_vol", "neutral"

    return {
        "vix":            vix_value,
        "regime":         regime,
        "strategy_bias":  bias,
        "high_threshold": float(high),
        "low_threshold":  float(low),
    }


def compute_vix_correlation(
    symbol: str,
    months: int = 6,
    client: Optional[Client] = None,
    stock_df: Optional[pd.DataFrame] = None,
    vix_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Pearson correlation of `symbol` daily returns vs VIX daily returns.

    Beta-vs-VIX is intentionally omitted — VIX is mean-reverting and not a
    useful sizing reference, so only the correlation coefficient is exposed.

    Returns
    -------
    dict {"symbol", "correlation", "n_obs"}
    """
    if stock_df is None:
        stock_df = _fetch_daily_candles(client, symbol, months=months)
    if vix_df is None:
        vix_df = _fetch_vix_daily(client, months=months)

    rs = stock_df["close"].pct_change()
    rv = vix_df["close"].pct_change()
    aligned = pd.concat([rs, rv], axis=1, join="inner").dropna()
    aligned.columns = ["stock", "vix"]

    if len(aligned) < 2:
        corr = float("nan")
    else:
        corr = float(aligned["stock"].corr(aligned["vix"]))

    return {
        "symbol":      symbol.upper(),
        "correlation": corr,
        "n_obs":       int(len(aligned)),
    }


# ============================================================================
# NYSE BREADTH ($ADD) — ADVANCERS MINUS DECLINERS
# ============================================================================
#
# $ADD is the live NYSE Advance/Decline line: number of advancing issues
# minus declining issues. It's a *confirmation* indicator — it doesn't
# generate signals, it filters them. Two views:
#
#   1. analyze_breadth() — current $ADD level + 5-bar slope on the last 15
#         readings, returning a regime tag (strong_bull / strong_bear /
#         weak_bull / weak_bear / neutral) plus an `improving` flag.
#
#   2. confirm_breadth_signal() — pure gate: takes ("BULLISH"/"BEARISH",
#         breadth_dict) and returns (allowed: bool, reason: str). Mirrors
#         the user's playbook:
#             BULLISH  needs $ADD > +200 AND improving
#             BEARISH  needs $ADD < -200 AND NOT improving
#
#   3. BreadthPoller — a tiny stateful helper for streaming use cases that
#         match the original snippet's `deque(maxlen=15)` polling loop.
#
# Schwab symbol for the NYSE A/D line is "$ADD". Historical 1-min candles
# are pulled via _fetch_intraday_candles() so the slope can be computed
# from a fresh scan even before any deque has been built up.
# ============================================================================

ADD_STRONG_BULL_THRESHOLD = 200    # $ADD > this AND improving → strong_bull
ADD_STRONG_BEAR_THRESHOLD = -200   # $ADD < this AND falling   → strong_bear
BREADTH_HISTORY_LEN       = 15     # keep last 15 readings (matches snippet)
BREADTH_SLOPE_WINDOW      = 5      # slope computed over last 5 readings


def _fetch_add_intraday(client, days: int = 1, freq_minutes: int = 1) -> pd.DataFrame:
    """
    Fetch intraday $ADD candles. Falls back to an empty DataFrame on failure
    so callers can degrade gracefully (e.g. fall back to live quote only).
    """
    try:
        return _fetch_intraday_candles(
            client, "$ADD", days=days, freq_minutes=freq_minutes
        )
    except Exception:
        return pd.DataFrame()


def get_add_level(client=None):
    """
    Latest $ADD reading. Tries Schwab quote() against common aliases, then
    falls back to the last close of the intraday candle stream.
    """
    if client is not None:
        for symbol in ("$ADD", "ADD", "$ADD.X"):
            try:
                resp = client.quote(symbol)
                payload = resp.json() if hasattr(resp, "json") else resp
                node = payload.get(symbol) or next(iter(payload.values()), {})
                quote = node.get("quote", node)
                px = quote.get("lastPrice") or quote.get("mark") or quote.get("closePrice")
                if px is not None:
                    return float(px)
            except Exception:
                continue
    df = _fetch_add_intraday(client, days=1, freq_minutes=1)
    if df.empty or "close" not in df.columns:
        return float("nan")
    return float(df["close"].iloc[-1])


def _breadth_slope(history) -> float:
    """np.polyfit slope over the trailing BREADTH_SLOPE_WINDOW readings."""
    y = list(history)[-BREADTH_SLOPE_WINDOW:]
    if len(y) < 2:
        return float("nan")
    x = np.arange(len(y))
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def analyze_breadth(
    client=None,
    history=None,
    slope_window: int = BREADTH_SLOPE_WINDOW,
):
    """
    Snapshot of NYSE breadth.

    `history` may be a list/deque of recent $ADD readings. If omitted, the
    function pulls the last ~15 minutes of $ADD candles from Schwab.

    Returns:
        {
          "add":         float,   # latest reading
          "slope":       float,   # slope of last `slope_window` readings
          "improving":   bool,    # slope > 0
          "regime":      str,     # strong_bull / weak_bull / neutral /
                                  # weak_bear / strong_bear
          "n_obs":       int,
        }
    """
    if history is None:
        df = _fetch_add_intraday(client, days=1, freq_minutes=1)
        if df.empty or "close" not in df.columns:
            history = []
        else:
            history = df["close"].tail(BREADTH_HISTORY_LEN).tolist()

    history = list(history)
    add_val = float(history[-1]) if history else float("nan")

    # Allow caller-passed slope_window override
    window = max(2, int(slope_window))
    y = history[-window:]
    if len(y) >= 2:
        x = np.arange(len(y))
        slope = float(np.polyfit(x, y, 1)[0])
    else:
        slope = float("nan")

    improving = slope > 0 if not np.isnan(slope) else False

    if np.isnan(add_val):
        regime = "neutral"
    elif add_val > ADD_STRONG_BULL_THRESHOLD and improving:
        regime = "strong_bull"
    elif add_val < ADD_STRONG_BEAR_THRESHOLD and not improving:
        regime = "strong_bear"
    elif add_val > 0:
        regime = "weak_bull"
    elif add_val < 0:
        regime = "weak_bear"
    else:
        regime = "neutral"

    return {
        "add":       add_val,
        "slope":     slope,
        "improving": bool(improving),
        "regime":    regime,
        "n_obs":     len(history),
    }


def confirm_breadth_signal(technical_signal: str, breadth: dict):
    """
    Gate a directional technical signal against current breadth.

    Args:
        technical_signal: "BULLISH" or "BEARISH" (case-insensitive).
        breadth: dict from analyze_breadth().

    Returns:
        (allowed: bool, reason: str)

    Rules (from the user's playbook):
        BULLISH allowed iff add > +200 AND improving
        BEARISH allowed iff add < -200 AND NOT improving
    """
    sig = (technical_signal or "").upper()
    add = breadth.get("add", float("nan"))
    improving = bool(breadth.get("improving", False))

    if np.isnan(add):
        return False, "breadth unavailable"

    if sig == "BULLISH":
        if add > ADD_STRONG_BULL_THRESHOLD and improving:
            return True, f"strong breadth: $ADD={add:.0f}, improving"
        return False, (
            f"weak/deteriorating breadth: $ADD={add:.0f}, "
            f"improving={improving}"
        )

    if sig == "BEARISH":
        if add < ADD_STRONG_BEAR_THRESHOLD and not improving:
            return True, f"weak breadth confirmed: $ADD={add:.0f}, falling"
        return False, (
            f"breadth too strong for short: $ADD={add:.0f}, "
            f"improving={improving}"
        )

    return False, f"unknown signal '{technical_signal}'"


class BreadthPoller:
    """
    Stateful $ADD poller for streaming/intraday loops.

    Mirrors the snippet's `deque(maxlen=15)` pattern but cleanly wrapped:

        poller = BreadthPoller(client)
        while is_market_open():
            poller.poll()
            breadth = poller.snapshot()
            allowed, why = confirm_breadth_signal(my_signal, breadth)
            ...
            time.sleep(60)
    """

    def __init__(self, client=None, maxlen: int = BREADTH_HISTORY_LEN):
        from collections import deque
        self.client  = client
        self.history = deque(maxlen=maxlen)

    def poll(self):
        """Read one live $ADD value and append to the rolling window."""
        val = get_add_level(self.client)
        if not np.isnan(val):
            self.history.append(val)
        return val

    def snapshot(self):
        """Run analyze_breadth() against the current rolling window."""
        return analyze_breadth(history=list(self.history))


# ============================================================================
# PUT/CALL RATIO ($PCALL) — CONTRARIAN SENTIMENT
# ============================================================================
#
# $PCALL is the CBOE total put/call ratio. Unlike VIX (direct vol gauge) or
# $ADD (direct breadth gauge), $PCALL is a *contrarian* sentiment signal:
#
#     PCALL > 1.10  →  high put activity, crowd is *afraid*  → contrarian
#                       BULLISH (long calls / short puts).
#     PCALL < 0.70  →  high call activity, crowd is *greedy* → contrarian
#                       BEARISH (long puts / short calls).
#     0.70 ≤ PCALL ≤ 1.10 → neutral, no edge.
#
# Three helpers:
#
#   1. analyze_pcall_sentiment() — current $PCALL → regime ("fear" /
#         "greed" / "neutral") plus a contrarian bias label.
#
#   2. confirm_pcall_signal() — pure gate that tells you whether a
#         technical signal *aligns* with the contrarian read (alignment
#         is the strongest setup; opposition is the veto).
#
#   3. PCallScheduler — convenience wrapper for the user's "run near 15:45
#         ET each day" job pattern; uses the `schedule` library if it's
#         installed, otherwise falls back to a plain time-of-day check.
#
# The Schwab symbol is "$PCALL". Some accounts can't pull intraday candles
# for it, so the fetcher degrades to the live quote alone.
# ============================================================================

PCALL_FEAR_THRESHOLD  = 1.10   # > → contrarian bullish (fear)
PCALL_GREED_THRESHOLD = 0.70   # < → contrarian bearish (greed)


def _fetch_pcall_intraday(client, days: int = 1, freq_minutes: int = 30) -> pd.DataFrame:
    """
    Best-effort intraday $PCALL candles. Returns empty DataFrame on failure
    so callers can degrade to live-quote-only mode.
    """
    try:
        return _fetch_intraday_candles(
            client, "$PCALL", days=days, freq_minutes=freq_minutes
        )
    except Exception:
        return pd.DataFrame()


def get_pcall_value(client=None):
    """
    Latest $PCALL reading. Tries Schwab quote() against common aliases,
    then falls back to last close of intraday candles. Returns NaN if
    nothing resolves (caller must handle).
    """
    if client is not None:
        for symbol in ("$PCALL", "PCALL", "$PCALL.X"):
            try:
                resp = client.quote(symbol)
                payload = resp.json() if hasattr(resp, "json") else resp
                node = payload.get(symbol) or next(iter(payload.values()), {})
                quote = node.get("quote", node)
                px = quote.get("lastPrice") or quote.get("mark") or quote.get("closePrice")
                if px is not None:
                    return float(px)
            except Exception:
                continue
    df = _fetch_pcall_intraday(client, days=1, freq_minutes=30)
    if df.empty or "close" not in df.columns:
        return float("nan")
    return float(df["close"].iloc[-1])


def analyze_pcall_sentiment(
    client=None,
    pcall_value=None,
    fear_threshold: float = PCALL_FEAR_THRESHOLD,
    greed_threshold: float = PCALL_GREED_THRESHOLD,
):
    """
    Map current $PCALL to a contrarian sentiment regime.

    Returns:
        {
          "pcall":            float,
          "regime":           "fear" | "greed" | "neutral",
          "contrarian_bias":  "bullish" | "bearish" | "neutral",
          "fear_threshold":   float,
          "greed_threshold":  float,
        }
    """
    if pcall_value is None:
        pcall_value = get_pcall_value(client)
    pcall_value = float(pcall_value)

    if np.isnan(pcall_value):
        regime, bias = "neutral", "neutral"
    elif pcall_value > fear_threshold:
        regime, bias = "fear", "bullish"        # contrarian: fade fear → buy
    elif pcall_value < greed_threshold:
        regime, bias = "greed", "bearish"       # contrarian: fade greed → sell
    else:
        regime, bias = "neutral", "neutral"

    return {
        "pcall":           pcall_value,
        "regime":          regime,
        "contrarian_bias": bias,
        "fear_threshold":  fear_threshold,
        "greed_threshold": greed_threshold,
    }


def confirm_pcall_signal(technical_signal: str, pcall_info: dict):
    """
    Gate a directional technical signal against the contrarian $PCALL read.

    Args:
        technical_signal: "BULLISH" or "BEARISH" (case-insensitive).
        pcall_info:       dict from analyze_pcall_sentiment().

    Returns:
        (allowed: bool, reason: str)

    Logic:
        Aligned with contrarian bias  → allowed  (strong sentiment edge)
        Opposite to contrarian bias   → vetoed   (fighting the fade)
        Neutral $PCALL                → allowed  (no sentiment edge,
                                                  pass through)
    """
    sig  = (technical_signal or "").upper()
    bias = (pcall_info or {}).get("contrarian_bias", "neutral")
    pc   = (pcall_info or {}).get("pcall", float("nan"))

    if np.isnan(pc):
        return True, "pcall unavailable — pass-through"

    if bias == "neutral":
        return True, f"$PCALL={pc:.2f} neutral — pass-through"

    if sig == "BULLISH" and bias == "bullish":
        return True, f"contrarian bullish confirmed: $PCALL={pc:.2f} (fear)"
    if sig == "BEARISH" and bias == "bearish":
        return True, f"contrarian bearish confirmed: $PCALL={pc:.2f} (greed)"

    return False, (
        f"signal '{sig}' fights contrarian bias='{bias}' "
        f"($PCALL={pc:.2f})"
    )


class PCallScheduler:
    """
    Convenience wrapper around the user's `schedule.every().day.at(...)`
    pattern. Uses the `schedule` library if installed, else falls back
    to a plain HH:MM check inside a sleep loop.

    Example (mirrors the user snippet):

        sched = PCallScheduler(client, run_at="15:45")
        sched.run(callback=lambda info: my_router(info))
    """

    def __init__(self, client=None, run_at: str = "15:45"):
        self.client = client
        self.run_at = run_at  # "HH:MM" 24h local time

    def _job(self, callback):
        info = analyze_pcall_sentiment(self.client)
        if callback is not None:
            callback(info)
        else:
            print(
                f"[pcall] {info['pcall']:.2f} regime={info['regime']} "
                f"bias={info['contrarian_bias']}"
            )

    def run(self, callback=None):
        """Block forever, firing once per day at `run_at`."""
        try:
            import schedule  # type: ignore
            schedule.every().day.at(self.run_at).do(self._job, callback=callback)
            while True:
                schedule.run_pending()
                time.sleep(60)
        except ImportError:
            # Fallback: pure stdlib HH:MM check
            from datetime import datetime
            fired_today = None
            while True:
                now = datetime.now().strftime("%H:%M")
                today = datetime.now().date()
                if now == self.run_at and fired_today != today:
                    self._job(callback)
                    fired_today = today
                time.sleep(30)


# ============================================================================
# NYSE TICK ($TICK) -- INTRADAY EXHAUSTION TIMING
# ============================================================================
#
# $TICK is the live count of NYSE issues last traded on an uptick minus
# those on a downtick. It oscillates around zero all day; the *extreme*
# readings are what matter:
#
#     $TICK <= -900   ->  panic flush     -> contrarian BULLISH (bounce)
#     $TICK >= +900   ->  euphoric spike  -> contrarian BEARISH (fade)
#     -900 < TICK < +900 -> routine tape  -> no edge
#
# This is a pure *timing* signal -- it tells you WHEN the tape has just
# exhausted itself, not WHAT to trade. Pair with the per-bar setup
# (KPI_PERFECT) and the slower regime gauges (VIX, $ADD, $PCALL) for the
# strongest stack.
#
# Three helpers + one streaming class:
#
#   1. analyze_tick_sentiment()  -- current $TICK -> regime
#         ("panic_flush" / "euphoric_spike" / "neutral") plus a
#         contrarian bias label.
#
#   2. confirm_tick_signal()     -- pure gate that lets a directional
#         technical signal pass only when $TICK is *not* already
#         exhausted in the opposite direction.
#
#   3. TickPoller                -- deque-backed streaming wrapper
#         (default maxlen=60 ~ last 60 polls) for the 1-second
#         intraday loop the user snippet uses.
#
# The Schwab symbol is "$TICK". Some accounts cannot pull intraday
# candles for it, so the fetcher degrades to live quote alone.
# ============================================================================

TICK_BUY_EXTREME    = -900   # <= -> contrarian bullish (panic flush)
TICK_SELL_EXTREME   = +900   # >= -> contrarian bearish (euphoric spike)
TICK_HISTORY_LEN    = 60     # default rolling window for TickPoller


def _fetch_tick_intraday(client, days: int = 1, freq_minutes: int = 1) -> pd.DataFrame:
    """Best-effort intraday $TICK candles. Empty on failure."""
    try:
        return _fetch_intraday_candles(
            client, "$TICK", days=days, freq_minutes=freq_minutes
        )
    except Exception:
        return pd.DataFrame()


def get_tick_value(client=None):
    """Latest $TICK reading; falls back to last intraday close; NaN if none."""
    if client is not None:
        for symbol in ("$TICK", "TICK", "$TICK.X", "$TICK-NY"):
            try:
                resp = client.quote(symbol)
                payload = resp.json() if hasattr(resp, "json") else resp
                node = payload.get(symbol) or next(iter(payload.values()), {})
                quote = node.get("quote", node)
                px = quote.get("lastPrice") or quote.get("mark") or quote.get("closePrice")
                if px is not None:
                    return float(px)
            except Exception:
                continue
    df = _fetch_tick_intraday(client, days=1, freq_minutes=1)
    if df.empty or "close" not in df.columns:
        return float("nan")
    return float(df["close"].iloc[-1])


def analyze_tick_sentiment(
    client=None,
    tick_value=None,
    buy_extreme: float = TICK_BUY_EXTREME,
    sell_extreme: float = TICK_SELL_EXTREME,
):
    """Map current $TICK to a contrarian intraday-timing regime."""
    if tick_value is None:
        tick_value = get_tick_value(client)
    tick_value = float(tick_value)

    if np.isnan(tick_value):
        regime, bias = "neutral", "neutral"
    elif tick_value <= buy_extreme:
        regime, bias = "panic_flush", "bullish"
    elif tick_value >= sell_extreme:
        regime, bias = "euphoric_spike", "bearish"
    else:
        regime, bias = "neutral", "neutral"

    return {
        "tick":            tick_value,
        "regime":          regime,
        "contrarian_bias": bias,
        "buy_extreme":     buy_extreme,
        "sell_extreme":    sell_extreme,
    }


def confirm_tick_signal(technical_signal: str, tick_info: dict):
    """Gate a directional technical signal against contrarian $TICK read.

    Returns (allowed, reason).
    """
    sig  = (technical_signal or "").upper()
    bias = (tick_info or {}).get("contrarian_bias", "neutral")
    tv   = (tick_info or {}).get("tick", float("nan"))

    if np.isnan(tv):
        return True, "tick unavailable -- pass-through"
    if bias == "neutral":
        return True, f"$TICK={tv:+.0f} neutral -- pass-through"
    if sig == "BULLISH" and bias == "bullish":
        return True, f"contrarian bullish confirmed: $TICK={tv:+.0f} (panic flush)"
    if sig == "BEARISH" and bias == "bearish":
        return True, f"contrarian bearish confirmed: $TICK={tv:+.0f} (euphoric spike)"
    return False, (
        f"signal '{sig}' fights contrarian bias='{bias}' "
        f"($TICK={tv:+.0f})"
    )


class TickPoller:
    """Streaming wrapper around get_tick_value for the 1-second loop.

    Stores the last maxlen readings in a deque so callers can compute
    slope / rolling extremes cheaply. evaluate() polls once and
    returns the analyze_tick_sentiment dict.
    """

    def __init__(
        self,
        client=None,
        maxlen: int = TICK_HISTORY_LEN,
        buy_extreme: float = TICK_BUY_EXTREME,
        sell_extreme: float = TICK_SELL_EXTREME,
    ):
        from collections import deque
        self.client       = client
        self.history      = deque(maxlen=maxlen)
        self.buy_extreme  = buy_extreme
        self.sell_extreme = sell_extreme

    def poll(self) -> float:
        v = get_tick_value(self.client)
        self.history.append(v)
        return v

    def latest(self) -> float:
        return self.history[-1] if self.history else float("nan")

    def evaluate(self) -> dict:
        v = self.poll()
        return analyze_tick_sentiment(
            tick_value=v,
            buy_extreme=self.buy_extreme,
            sell_extreme=self.sell_extreme,
        )


# ============================================================================
# VXX VOLATILITY-FADE -- PANIC SUBSIDING REGIME
# ============================================================================
#
# VXX is the iPath VIX short-term futures ETN. Like $VIX, it explodes
# when fear hits the tape; unlike $VIX, it bleeds in contango and is
# directly tradable. Two questions matter:
#
#     1. Is volatility currently elevated?      RSI(14, daily) > 70
#     2. Has the spike already rolled over?     close < EMA(5, daily)
#
# When BOTH are true the panic is subsiding -- VXX is set to mean-revert
# lower while equities snap back. This is the classic "fade the spike"
# setup the user snippet automates with a bear call spread.
#
# Regime taxonomy (daily bars):
#
#     panic_subsiding : RSI > 70 AND price < EMA5   -> equities BULLISH
#     panic_active    : RSI > 70 AND price >= EMA5  -> equities BEARISH
#     vol_crushed     : RSI < 30                    -> complacency, neutral
#     neutral         : everything else
# ============================================================================

VXX_RSI_PANIC      = 70.0   # > -> volatility spike (panic)
VXX_RSI_CRUSHED    = 30.0   # < -> complacency (no fade edge)
VXX_EMA_LEN        = 5
VXX_RSI_LEN        = 14
VXX_REFRESH_SECS   = 86400  # daily cadence per the user snippet


def _fetch_vxx_daily(
    client: Optional[Client] = None,
    months: int = 3,
) -> pd.DataFrame:
    """Daily VXX candles. Tries Schwab native first, then yfinance."""
    if client is not None:
        try:
            return _fetch_daily_candles(client, "VXX", months=months)
        except Exception:
            pass
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "VXX history unavailable: Schwab returned no candles and "
            "yfinance is not installed."
        ) from e
    period = f"{max(int(months), 1)}mo"
    df = yf.Ticker("VXX").history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError("VXX history empty from yfinance.")
    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["open", "high", "low", "close", "volume"]]


def get_vxx_metrics(
    client=None,
    df: Optional[pd.DataFrame] = None,
    months: int = 3,
):
    """Return (price, rsi14, ema5) for the latest VXX daily bar."""
    if df is None:
        df = _fetch_vxx_daily(client=client, months=months)
    close = pd.to_numeric(df["close"], errors="coerce")
    rsi = ta.rsi(close, length=VXX_RSI_LEN)
    ema = ta.ema(close, length=VXX_EMA_LEN)
    return (
        float(close.iloc[-1]),
        float(rsi.iloc[-1]) if rsi is not None and len(rsi) else float("nan"),
        float(ema.iloc[-1]) if ema is not None and len(ema) else float("nan"),
    )


def analyze_vxx_extreme(
    client=None,
    df: Optional[pd.DataFrame] = None,
    price: Optional[float] = None,
    rsi: Optional[float] = None,
    ema5: Optional[float] = None,
    rsi_panic: float = VXX_RSI_PANIC,
    rsi_crushed: float = VXX_RSI_CRUSHED,
) -> dict:
    """Map current VXX state to a contrarian regime for equity timing."""
    if price is None or rsi is None or ema5 is None:
        price, rsi, ema5 = get_vxx_metrics(client=client, df=df)

    if pd.isna(price) or pd.isna(rsi) or pd.isna(ema5):
        regime, bias = "neutral", "neutral"
    elif rsi >= rsi_panic and price < ema5:
        regime, bias = "panic_subsiding", "bullish"
    elif rsi >= rsi_panic:
        regime, bias = "panic_active", "bearish"
    elif rsi <= rsi_crushed:
        regime, bias = "vol_crushed", "neutral"
    else:
        regime, bias = "neutral", "neutral"

    return {
        "price":            float(price),
        "rsi":              float(rsi),
        "ema5":             float(ema5),
        "regime":           regime,
        "contrarian_bias":  bias,
        "rsi_panic":        float(rsi_panic),
    }


def confirm_vxx_signal(technical_signal: str, vxx_info: dict):
    """Gate a directional technical signal against VXX volatility regime.

    Returns (allowed: bool, reason: str).
    """
    sig    = (technical_signal or "").lower()
    regime = vxx_info.get("regime", "neutral")
    bias   = vxx_info.get("contrarian_bias", "neutral")
    rsi    = vxx_info.get("rsi", float("nan"))
    price  = vxx_info.get("price", float("nan"))
    ema5   = vxx_info.get("ema5", float("nan"))

    tag = f"VXX rsi={rsi:.1f} px={price:.2f} ema5={ema5:.2f}"

    if regime == "neutral":
        return True, f"{tag} neutral -- pass-through"
    if regime == "panic_subsiding" and sig == "bullish":
        return True, f"contrarian bullish confirmed: {tag} (panic subsiding)"
    if regime == "panic_active" and sig == "bearish":
        return True, f"bearish confirmed: {tag} (vol still expanding)"
    if regime == "vol_crushed":
        return True, f"{tag} complacency -- pass-through"

    return False, f"signal={sig} blocked by VXX {regime} (bias={bias}); {tag}"


class VxxPoller:
    """Daily-cadence wrapper around analyze_vxx_extreme.

    The user reference loop sleeps 86 400 s after a fade entry; this
    class caches the last evaluation for `refresh_secs` and only
    re-fetches the candle history when the cache is stale, so callers
    can poll it on a tighter loop without hammering Schwab.
    """

    def __init__(
        self,
        client=None,
        refresh_secs: float = VXX_REFRESH_SECS,
        rsi_panic: float = VXX_RSI_PANIC,
        rsi_crushed: float = VXX_RSI_CRUSHED,
    ):
        self.client       = client
        self.refresh_secs = float(refresh_secs)
        self.rsi_panic    = float(rsi_panic)
        self.rsi_crushed  = float(rsi_crushed)
        self._last_ts     = 0.0
        self._last_info: dict = {}

    def evaluate(self, force: bool = False) -> dict:
        import time as _t
        now = _t.time()
        if force or not self._last_info or (now - self._last_ts) >= self.refresh_secs:
            self._last_info = analyze_vxx_extreme(
                client=self.client,
                rsi_panic=self.rsi_panic,
                rsi_crushed=self.rsi_crushed,
            )
            self._last_ts = now
        return self._last_info

    @property
    def latest(self) -> dict:
        return dict(self._last_info)




# ============================================================================
# MULTI-TIMEFRAME (MTF) SQUEEZE
# ============================================================================
#
# Per-bar daily squeeze (computed in TPS_SCAN above) tells you the state
# *today*. MTF squeeze tells you whether **multiple timeframes** are
# compressed at the same time — the classic TTM "stack" — and is read on
# the **most recent bar** of each timeframe.
#
# Default frame list (per spec):
#     W, D, 195, 130, 78, 60, 30, 15, 10, 5    (minutes for the integers)
#
# Sourcing strategy
# -----------------
# Schwab natively supports minute frequencies {1, 5, 10, 15, 30} and
# daily / weekly / monthly. The custom widths 60/78/130/195 are produced
# by resampling a base minute frame:
#
#     60-min   <- resample 30-min  (60 / 30 = 2 sub-bars per bar)
#     78-min   <- resample 1-min   (1/5 of a 390-min RTH session)
#     130-min  <- resample 5-min   (130 / 5 = 26 sub-bars per bar)
#     195-min  <- resample 5-min   (half-session, 195 / 5 = 39 sub-bars)
#
# Base minute frames are fetched once and cached, so the network cost is
# bounded to one call per native frequency we touch (plus one daily + one
# weekly call) regardless of how many derived widths the caller asks for.
# ============================================================================

MTF_TIMEFRAMES = ["W", "D", 195, 130, 78, 60, 30, 15, 10, 5]

# How to source each timeframe:
#   ("native_d",)                        -> Schwab daily call
#   ("native_w",)                        -> Schwab weekly call
#   ("native_min", freq_minutes)         -> Schwab minute call at that frequency
#   ("resample", base_min, target_min)   -> fetch base_min, resample to target_min
_TF_PLAN = {
    "W":  ("native_w",),
    "D":  ("native_d",),
    195:  ("resample", 5, 195),
    130:  ("resample", 5, 130),
    78:   ("resample", 1, 78),
    60:   ("resample", 30, 60),
    30:   ("native_min", 30),
    15:   ("native_min", 15),
    10:   ("native_min", 10),
    5:    ("native_min", 5),
}


def _fetch_intraday_candles(
    client: Client,
    symbol: str,
    freq_minutes: int,
    days: int = 10,
) -> pd.DataFrame:
    """Fetch native minute OHLCV candles from Schwab.

    Schwab supports freq_minutes in {1, 5, 10, 15, 30}. ``days`` is the
    lookback window in days (Schwab caps 1-min history at ~48 days).
    """
    resp = client.price_history(
        symbol=symbol,
        periodType="day",
        period=days,
        frequencyType="minute",
        frequency=freq_minutes,
    )
    payload = resp.json()
    candles = payload.get("candles", [])
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    df = df.set_index("datetime").sort_index()
    return df


def _fetch_weekly_candles(
    client: Client,
    symbol: str,
    years: int = 2,
) -> pd.DataFrame:
    """Fetch weekly OHLCV candles from Schwab."""
    resp = client.price_history(
        symbol=symbol,
        periodType="year",
        period=years,
        frequencyType="weekly",
        frequency=1,
    )
    payload = resp.json()
    candles = payload.get("candles", [])
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    df = df.set_index("datetime").sort_index()
    return df


def _resample_minutes(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Resample an OHLCV minute DataFrame to ``target_minutes`` bars.

    Uses right-labelled / right-closed bars so the timestamp marks bar
    *close* (consistent with most charting platforms).
    """
    if df.empty:
        return df
    rule = f"{target_minutes}min"
    out = (
        df.resample(rule, label="right", closed="right")
          .agg({
              "open":   "first",
              "high":   "max",
              "low":    "min",
              "close":  "last",
              "volume": "sum",
          })
          .dropna(subset=["open", "close"])
    )
    return out


def _latest_squeeze_state(df_tf: pd.DataFrame) -> dict:
    """Run _compute_squeeze on a per-timeframe df and return the last bar's state."""
    df_tf = _compute_squeeze(df_tf)

    # momo histogram: same heuristic as KPI — SQZPRO_* column that is NOT a flag
    flag_cols = {
        "SQZPRO_ON_WIDE", "SQZPRO_ON_NORMAL", "SQZPRO_ON_NARROW",
        "SQZPRO_OFF_WIDE", "SQZPRO_OFF", "SQZPRO_NO",
    }
    momo_candidates = [
        c for c in df_tf.columns
        if c.startswith("SQZPRO") and c not in flag_cols
    ]
    if momo_candidates:
        momo = df_tf[momo_candidates[0]]
        cyan_series = (momo > 0) & (momo > momo.shift(1))
    else:
        cyan_series = pd.Series(False, index=df_tf.index)

    last = df_tf.iloc[-1]
    return {
        "bar_time":      df_tf.index[-1],
        "close":         float(last.get("close", float("nan"))),
        "on_narrow":     bool(last.get("SQZPRO_ON_NARROW", 0)),
        "on_normal":     bool(last.get("SQZPRO_ON_NORMAL", 0)),
        "on_wide":       bool(last.get("SQZPRO_ON_WIDE", 0)),
        "squeeze_on":    bool(last.get("squeeze_on", False)),
        "squeeze_fired": bool(last.get("squeeze_fired", False)),
        "momo_cyan":     bool(cyan_series.iloc[-1]),
    }


def compute_mtf_squeeze(
    symbol: str = "SPY",
    timeframes=None,
    client: Optional[Client] = None,
    daily_df: Optional[pd.DataFrame] = None,
    min_bars: int = 25,
) -> pd.DataFrame:
    """
    Compute TTM Squeeze Pro state across multiple timeframes.

    Parameters
    ----------
    symbol : str
        Ticker, e.g. "SPY".
    timeframes : list, optional
        Subset / superset of MTF_TIMEFRAMES. Strings "W"/"D" or integer
        minute widths. Defaults to MTF_TIMEFRAMES.
    client : schwabdev.Client, optional
        Reuse an authenticated client; one is built if omitted.
    daily_df : DataFrame, optional
        If you already fetched daily candles (e.g. inside TPS_SCAN), pass
        them here to avoid a second API call for the "D" frame.
    min_bars : int
        Minimum bars required for a valid squeeze state on a frame
        (squeeze_pro Keltner Channels need ~20 bars of history).

    Returns
    -------
    DataFrame indexed by timeframe (in input order) with columns:
        bar_time, close,
        on_narrow, on_normal, on_wide,
        squeeze_on, squeeze_fired, momo_cyan
        [error]   populated only when a frame failed to source enough bars

    Read the **rows** (each is a timeframe). The "stack" is a column-wise
    AND across rows, e.g. ``df["on_narrow"].all()`` for "every frame in
    the stack is in a tight squeeze right now".
    """
    if timeframes is None:
        timeframes = MTF_TIMEFRAMES
    if client is None:
        client = _get_client()

    # Caches: never fetch the same base frame twice across the loop.
    minute_cache: dict = {}     # freq_minutes -> df
    weekly_cache: Optional[pd.DataFrame] = None
    daily_cache: Optional[pd.DataFrame] = daily_df

    rows = []
    for tf in timeframes:
        plan = _TF_PLAN.get(tf)
        if plan is None:
            rows.append({"timeframe": tf, "error": "unknown_timeframe"})
            continue

        kind = plan[0]
        try:
            if kind == "native_w":
                if weekly_cache is None:
                    weekly_cache = _fetch_weekly_candles(client, symbol)
                df_tf = weekly_cache.copy()
            elif kind == "native_d":
                if daily_cache is None:
                    daily_cache = _fetch_daily_candles(client, symbol, months=12)
                df_tf = daily_cache.copy()
            elif kind == "native_min":
                fm = plan[1]
                if fm not in minute_cache:
                    minute_cache[fm] = _fetch_intraday_candles(
                        client, symbol, fm, days=10
                    )
                df_tf = minute_cache[fm].copy()
            elif kind == "resample":
                base_m, target_m = plan[1], plan[2]
                if base_m not in minute_cache:
                    # 1-min Schwab cap is ~48d; 5/30-min are looser.
                    days = 10 if base_m == 1 else 20
                    minute_cache[base_m] = _fetch_intraday_candles(
                        client, symbol, base_m, days=days
                    )
                df_tf = _resample_minutes(minute_cache[base_m], target_m)
            else:
                rows.append({"timeframe": tf, "error": f"bad_plan:{kind}"})
                continue
        except Exception as e:  # noqa: BLE001 — surface failure per frame
            rows.append({"timeframe": tf, "error": f"fetch:{e!s}"})
            continue

        if df_tf is None or df_tf.empty or len(df_tf) < min_bars:
            rows.append({
                "timeframe": tf,
                "error": f"insufficient_bars:{0 if df_tf is None else len(df_tf)}",
            })
            continue

        try:
            state = _latest_squeeze_state(df_tf)
        except Exception as e:  # noqa: BLE001
            rows.append({"timeframe": tf, "error": f"squeeze:{e!s}"})
            continue

        state["timeframe"] = tf
        rows.append(state)

    out = pd.DataFrame(rows)
    if "timeframe" in out.columns:
        out = out.set_index("timeframe")
    return out


def mtf_squeeze_summary(mtf: pd.DataFrame) -> dict:
    """Reduce a compute_mtf_squeeze() DataFrame to scalar stack metrics.

    Returns a dict suitable for broadcasting onto another DataFrame or
    logging:
        n_frames           : total timeframes evaluated successfully
        n_on_narrow        : count of frames with orange-dot (tight) squeeze
        n_squeeze_on       : count of frames with any squeeze active
        n_squeeze_fired    : count of frames that just fired (off-wide)
        n_momo_cyan        : count of frames with cyan momentum
        all_on_narrow      : True if every successful frame has orange dot
        all_squeeze_on     : True if every successful frame is in squeeze
    """
    if mtf.empty:
        return {
            "n_frames": 0, "n_on_narrow": 0, "n_squeeze_on": 0,
            "n_squeeze_fired": 0, "n_momo_cyan": 0,
            "all_on_narrow": False, "all_squeeze_on": False,
        }
    valid = mtf
    if "error" in mtf.columns:
        valid = mtf[mtf["error"].isna()] if mtf["error"].notna().any() else mtf
    n = len(valid)

    def _count(col):
        return int(valid[col].fillna(False).astype(bool).sum()) if col in valid.columns else 0

    return {
        "n_frames":        n,
        "n_on_narrow":     _count("on_narrow"),
        "n_squeeze_on":    _count("squeeze_on"),
        "n_squeeze_fired": _count("squeeze_fired"),
        "n_momo_cyan":     _count("momo_cyan"),
        "all_on_narrow":   bool(n > 0 and _count("on_narrow") == n),
        "all_squeeze_on":  bool(n > 0 and _count("squeeze_on") == n),
    }


if __name__ == "__main__":
    out = TPS_SCAN("SPY")
    cols = [
        "close",
        "Upward_Trend",
        "bull_flag", "pennant",
        "SQZPRO_ON_NARROW", "SQZPRO_ON_NORMAL", "SQZPRO_ON_WIDE",
        "squeeze_fired",
        "momo_cyan",
        "short_float_pct", "short_ratio", "short_squeeze_ok",
        "vwap", "vwap_setup_ok", "vwap_burst_recent",
        "KPI_SCORE", "KPI_PERFECT",
    ]
    cols = [c for c in cols if c in out.columns]
    print(out[cols].tail(15))
    perfect = out[out.get("KPI_PERFECT", False) == True]
    print(f"\nPerfect-setup bars: {len(perfect)}")
    if len(perfect):
        print(perfect[cols].tail())

    # ------------------------------------------------------------------
    # SPY correlation / beta (broadcast scalars; same value on every row)
    # ------------------------------------------------------------------
    if "spy_corr" in out.columns and "spy_beta" in out.columns:
        corr = out["spy_corr"].iloc[-1]
        beta = out["spy_beta"].iloc[-1]
        print(f"\nCorrelation to SPY: {corr:.2f}, Beta: {beta:.2f}")

    # ------------------------------------------------------------------
    # QQQ correlation / beta + tech-sector regime
    # ------------------------------------------------------------------
    if "qqq_corr" in out.columns and "qqq_beta" in out.columns:
        q_corr   = out["qqq_corr"].iloc[-1]
        q_beta   = out["qqq_beta"].iloc[-1]
        q_price  = out["qqq_price"].iloc[-1]
        q_sma    = out["qqq_sma20"].iloc[-1]
        q_rsi    = out["qqq_rsi"].iloc[-1]
        q_regime = out["qqq_regime"].iloc[-1]
        print(f"Correlation to QQQ: {q_corr:.2f}, Beta: {q_beta:.2f}")
        print(
            f"QQQ trend  -> price={q_price:.2f}  SMA20={q_sma:.2f}  "
            f"RSI14={q_rsi:.2f}  regime={q_regime}"
        )

    # ------------------------------------------------------------------
    # VIX volatility regime + correlation
    # ------------------------------------------------------------------
    if "vix_level" in out.columns and "vix_regime" in out.columns:
        v_level  = out["vix_level"].iloc[-1]
        v_regime = out["vix_regime"].iloc[-1]
        v_bias   = out["vix_strategy_bias"].iloc[-1]
        v_corr   = out["vix_corr"].iloc[-1]
        print(
            f"VIX        -> level={v_level:.2f}  regime={v_regime}  "
            f"bias={v_bias}  corr(stock,VIX)={v_corr:+.2f}"
        )

    # ------------------------------------------------------------------
    # NYSE breadth ($ADD) confirmation
    # ------------------------------------------------------------------
    if "add_level" in out.columns and "add_regime" in out.columns:
        a_level = out["add_level"].iloc[-1]
        a_slope = out["add_slope"].iloc[-1]
        a_imp   = out["add_improving"].iloc[-1]
        a_reg   = out["add_regime"].iloc[-1]
        print(
            f"Breadth    -> $ADD={a_level:+.0f}  slope={a_slope:+.2f}  "
            f"improving={a_imp}  regime={a_reg}"
        )

    # ------------------------------------------------------------------
    # $PCALL contrarian sentiment
    # ------------------------------------------------------------------
    if "pcall_value" in out.columns and "pcall_regime" in out.columns:
        p_val  = out["pcall_value"].iloc[-1]
        p_reg  = out["pcall_regime"].iloc[-1]
        p_bias = out["pcall_bias"].iloc[-1]
        print(
            f"$PCALL     -> value={p_val:.2f}  regime={p_reg}  "
            f"contrarian_bias={p_bias}"
        )

    # ------------------------------------------------------------------
    # $TICK intraday exhaustion
    # ------------------------------------------------------------------
    if "tick_value" in out.columns and "tick_regime" in out.columns:
        t_val  = out["tick_value"].iloc[-1]
        t_reg  = out["tick_regime"].iloc[-1]
        t_bias = out["tick_bias"].iloc[-1]
        print(
            f"$TICK      -> value={t_val:+.0f}  regime={t_reg}  "
            f"contrarian_bias={t_bias}"
        )

    # ------------------------------------------------------------------
    # VXX volatility fade
    # ------------------------------------------------------------------
    if "vxx_regime" in out.columns and "vxx_rsi" in out.columns:
        v_px   = out["vxx_price"].iloc[-1]
        v_rsi  = out["vxx_rsi"].iloc[-1]
        v_ema  = out["vxx_ema5"].iloc[-1]
        v_reg  = out["vxx_regime"].iloc[-1]
        v_bias = out["vxx_bias"].iloc[-1]
        print(
            f"VXX        -> px={v_px:.2f}  rsi={v_rsi:.1f}  ema5={v_ema:.2f}  "
            f"regime={v_reg}  contrarian_bias={v_bias}"
        )

    # ------------------------------------------------------------------
    # Multi-timeframe squeeze snapshot (W, D, 195, 130, 78, 60, 30, 15, 10, 5)
    # ------------------------------------------------------------------
    print("\n=== MTF Squeeze (latest bar of each timeframe) ===")
    mtf = compute_mtf_squeeze("SPY")
    print(mtf)
    print("\nStack summary:", mtf_squeeze_summary(mtf))