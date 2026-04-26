# Nate — Options Detective (tps.pro)

Automated options trading signal scanner and strategy engine.

## Current Features

### TPS Framework

Nate implements the **TPS** (Trend-Pattern-Squeeze) scanner for high-probability options setups:

- **T — TREND**: EMA alignment (8/21/55) from Schwab API
- **P — PATTERN**: Bull flag & bull pennant detection (vectorized rolling regression)
- **S — SQUEEZE**: TTM Squeeze Pro via `pandas-ta.squeeze_pro` (Bollinger Bands inside Keltner Channels)

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

### Unified TPS Scanner (T + P + S + Short)

Runs the full TPS stack in a single pass and outputs a consolidated signal table with short interest context.

```bash
python strategies/tps_scanner.py --symbol SPY --window 10 --r2 0.8 --show 15 --save
```

**Output columns (OHLCV + all signals):**
| Category | Columns |
|---|---|
| EMAs | `EMA_8`, `EMA_21`, `EMA_55`, `Upward_Trend` |
| Pattern regression | `res_slope`, `sup_slope`, `res_r2`, `sup_r2` |
| Pattern flags | `bull_flag`, `bull_pennant` |
| Squeeze raw | `SQZPRO_ON_NARROW`, `SQZPRO_ON_NORMAL`, `SQZPRO_ON_WIDE`, `SQZPRO_OFF_WIDE` |
| Squeeze derived | `ttm_squeeze` (any ON), `ttm_squeeze_fired` (breakout) |
| Short interest | `short_float_pct`, `short_ratio`, `short_data_source`, `short_as_of_date` |
| Composite | `tps_all` (T & (P) & S), `tps_score` (technical sum 0–4) |

**Console summary:**
- Upward Trend days %, Bull Flag/Pennant hit rates
- TTM Squeeze ON % and Fired % (breakout events)
- Short Float % and Short Ratio (days to cover) with as-of date
- TPS Score (0–4) and Full TPS alignment count

**Note:** `tps_all` requires `Upward_Trend` AND (`bull_flag` OR `bull_pennant`) AND `ttm_squeeze` (squeeze active). Short metrics are informational/fundamental filters that can be layered on top for trade selection (e.g., high short_float_pct + squeeze firing = stronger breakout potential).

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

## Project Structure

```
nate.tps.pro/
├── strategies/
│   ├── ema_trend_analysis.py   # EMA alignment (T)
│   ├── pattern_detection.py    # Bull flag & pennant (P)
│   ├── tps_scanner.py          # Unified TPS + Short run
│   ├── short_interest.py       # Short Float % & Short Ratio
│   └── requirements.txt        # Python dependencies
└── README.md                   # This file
```
