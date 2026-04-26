# Nate — Options Detective (tps.pro)

Automated options trading signal scanner and strategy engine.

## Current Features

### TPS Framework

Nate implements the **TPS+V** (Trend-Pattern-Squeeze-VWAP) scanner for high-probability options setups:

- **T — TREND**: EMA alignment (8/21/55) from Schwab API
- **P — PATTERN**: Bull flag & bull pennant detection (vectorized rolling regression)
- **S — SQUEEZE**: TTM Squeeze Pro via `pandas-ta.squeeze_pro` (Bollinger Bands inside Keltner Channels)
- **V — VWAP**: Price above upward-sloping VWAP confirms intraday/stacked buying pressure
- **Vol — VOLUME BURST**: Volume spike coinciding with VWAP cross (shorts covering)
- **MTF — MULTI-TIMEFRAME**: Squeeze conformance across weekly, daily, and intraday intervals (default W,D,195,130,78,60,30,15,10,5)

**Augmentation:** Short Interest metrics (Short Float %, Short Ratio) provide fundamental context to TPS signals.

---

### EMA Trend Analyzer (T)

Fetches price history from Schwab API and identifies bullish EMA alignment (8 > 21 > 55).

```bash
# Export credentials
export SCHWAB_API_KEY="***"
export SCHWAB_API_SECRET="***"
export ANALYSIS_SYMBOL="SPY"  # optional, default: SPY

# Run analyzer
pip install -r strategies/requirements.txt
python strategies/ema_trend_analysis.py

# Save results to CSV
python strategies/ema_trend_analysis.py --save
```

Output includes:
- Latest OHLCV + EMA(8/21/55) values
- `Upward_Trend` boolean column
- Trend rate statistics

---

### Pattern Detector (P)

Vectorized bull flag and bull pennant recognition using rolling linear regression on high/low channels.

```bash
python strategies/pattern_detection.py
```

Output columns added:
- `res_slope` / `sup_slope` — regression slopes of resistance/support
- `res_r2` / `sup_r2` — fit quality (R²)
- `bull_flag` — parallel downtrend channels on both highs & lows
- `bull_pennant` — converging channels (resistance down, support up)

Parameters: `window=10` (default), `r2_threshold=0.8` (tunable in script).

---

### Unified TPS Scanner (T + P + S + V + Vol + Short)

Runs the full TPS+V stack in a single pass and outputs a consolidated signal table with short interest context.

```bash
# Basic scan
python strategies/tps_scanner.py --symbol SPY --window 10 --r2 0.8 --show 15 --save

# VWAP & volume burst tuning
python strategies/tps_scanner.py --symbol SPY --vwap-window 20 --volume-multiplier 2.0

# Short interest filters: >20% short float + >3 days to cover
python strategies/tps_scanner.py --symbol SPY --min-short-float 20 --min-short-ratio 3
```

**Output columns (OHLCV + all signals):**
| Category | Columns |
|---|---|
| EMAs | `EMA_8`, `EMA_21`, `EMA_55`, `Upward_Trend` |
| Pattern regression | `res_slope`, `sup_slope`, `res_r2`, `sup_r2` |
| Pattern flags | `bull_flag`, `bull_pennant` |
| Squeeze raw | `SQZPRO_ON_NARROW`, `SQZPRO_ON_NORMAL`, `SQZPRO_ON_WIDE`, `SQZPRO_OFF_WIDE` |
| Squeeze derived | `ttm_squeeze` (any ON), `ttm_squeeze_fired` (breakout) |
| VWAP | `vwap` (rolling), `vwap_rising` (slope > 0), `price_above_vwap` |
| Volume burst | `volume_ratio` (x avg), `volume_burst` (≥ threshold), `vwap_crossed`, `volume_burst_on_cross` |
| Multi-timeframe Squeeze | `sqz_<tf>`, `sqz_<tf>_fired` (per timeframe in `--mtf`); aggregates `mtf_squeeze_count`, `mtf_squeeze_any`, `mtf_squeeze_all` |
| Short interest | `short_float_pct`, `short_ratio`, `short_data_source`, `short_as_of_date` |
| Filter overlay | `short_filter_met` — True when short thresholds pass (if any filters enabled) |
| Composite | `tps_all` (T&P&S), `tps_vwap_all` (T&P&S&V), `perfect_setup` (all six), `tps_score` (sum 0–6) |

**Console summary:**
- Upward Trend days %, Bull Flag/Pennant hit rates
- TTM Squeeze ON % and Fired % (breakout events)
- VWAP Bullish % and Volume Burst on Cross % (shorts covering)
- MTF Squeeze Count/Any/All (multi-timeframe confluence)
- Short Float % and Short Ratio (days to cover) with as-of date
- TPS Score (0–6) and Perfect Setup count
- Optional Short Filter PASS/FAIL status

**Scoring:**
- `tps_score` counts active signals among: Upward_Trend, bull_flag, bull_pennant, ttm_squeeze, vwap_bullish, volume_burst_on_cross → range 0–6.
- `perfect_setup` requires all six conditions True simultaneously.
- `tps_all` retains original TPS-only definition (T & (P) & S) for backwards compatibility.

**Note:** `tps_all` requires `Upward_Trend` AND (`bull_flag` OR `bull_pennant`) AND `ttm_squeeze` (squeeze active). Short metrics are informational/fundamental filters that can be layered on top for trade selection (e.g., high short_float_pct + squeeze firing = stronger breakout potential).

**CLI short filters:** Use `--min-short-float` and/or `--min-short-ratio` to restrict output to symbols meeting those thresholds. The scanner adds a `short_filter_met` column (PASS/FAIL) and prints the filter status in the summary. This does not affect `tps_all`; it's an external overlay for watchlist generation.

**Multi-timeframe squeeze:** Use `--mtf` to specify which timeframes to evaluate for squeeze confluence (default: `W,D,195,130,78,60,30,15,10,5`). The scanner adds `sqz_<tf>` and `sqz_<tf>_fired` columns per timeframe and aggregate flags `mtf_squeeze_count`, `mtf_squeeze_any`, and `mtf_squeeze_all`.
---

### Short Interest Analyzer (Fundamental)

Standalone module to fetch/enrich short metrics. Currently uses deterministic mock data for development.

```bash
python strategies/short_interest.py
```

**Columns added:**
- `short_float_pct` — percentage of float shares sold short (0–100%)
- `short_ratio` — days to cover; short interest ÷ average daily volume
- `short_data_source` — `'mock'` or real API name when connected
- `short_as_of_date` — date of the short interest data

**To connect real data:** Replace `_fetch_short_metrics_mock()` in `strategies/short_interest.py` with calls to:
- FINRA Short Interest API
- Nasdaq short interest endpoint
- Or Schwab fundamentals (if short fields available)

---

## The "Perfect" Setup (Long Call / Bull Put Spread)

The strongest Nate signal combines technical compression with fundamental short pressure and a volume confirmation:

```
TTM Squeeze Pro   : Orange dot (High Compression)
Short Metrics     : >20% Float Short  +  >5 Days to Cover
VWAP              : Price sitting just above an upward-sloping VWAP
Volume            : Volume Burst occurs right as price crosses VWAP
                    (shorts getting liquidated)
```

When all three align:
- Squeeze creates pent-up momentum (low volatility phase)
- Heavy short interest ensures supply shock on breakout
- Price above rising VWAP shows sustained buying pressure
- Volume spike on VWAP cross confirms short-covering acceleration

Best for:
- **Long calls** (directional bullish)
- **Bull put spreads** (defined-risk credit with high probability)

---

## Project Structure

```
nate.tps.pro/
├── strategies/
│   ├── ema_trend_analysis.py   # EMA alignment (T)
│   ├── pattern_detection.py    # Bull flag & pennant (P)
│   ├── tps_scanner.py          # Unified TPS + VWAP + Volume + Short + MTF run
│   ├── short_interest.py       # Short Float % & Short Ratio
│   ├── mtf_squeeze.py          # Multi-timeframe squeeze analysis
│   └── requirements.txt        # Python dependencies
└── README.md                   # This file
```

---

## Environment

```bash
# Install dependencies
pip install -r strategies/requirements.txt

# Set credentials (Schwab Developer API)
export SCHWAB_API_KEY="your_key"
export SCHWAB_API_SECRET="your_secret"

# Optional: set default symbol
export ANALYSIS_SYMBOL="SPY"
```

---

## Roadmap

- [x] EMA trend detection (T)
- [x] Bull flag / pennant patterns (P)
- [x] TTM Squeeze Pro integration (S)
- [x] Short interest data (fundamental overlay)
- [x] VWAP + volume burst (V + Vol)
- [x] CLI filters: `--min-short-float`, `--min-short-ratio`
- [x] Multi-timeframe squeeze (MTF) — weekly, daily, intraday squeeze confluence
- [ ] Backtesting engine for TPS/VWAP signals
- [ ] Multi-symbol watchlist scan
- [ ] Real-time alerts (webhook / Telegram)
- [ ] Additional options strategies: Iron Condor, Covered Call, CSP
