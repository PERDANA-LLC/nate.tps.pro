# Nate — Options Detective (tps.pro)

Automated options trading signal scanner and strategy engine.

## Current Features

### TPS Framework

Nate implements the **TPS** (Trend-Pattern-Squeeze) scanner for high-probability options setups:

- **T — TREND**: EMA alignment (8/21/55) from Schwab API
- **P — PATTERN**: Bull flag & bull pennant detection (vectorized rolling regression)
- **S — SQUEEZE**: TTM Squeeze Pro via `pandas-ta.squeeze_pro` (Bollinger Bands inside Keltner Channels)

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
# (reuse same venv from above)
python strategies/pattern_detection.py
```

Output columns added:
- `res_slope` / `sup_slope` — regression slopes of resistance/support
- `res_r2` / `sup_r2` — fit quality (R²)
- `bull_flag` — parallel downtrend channels on both highs & lows
- `bull_pennant` — converging channels (resistance down, support up)

Parameters: `window=10` (default), `r2_threshold=0.8` (tunable in script).

---

### Unified TPS Scanner (T + P + S)

Runs the full TPS stack in a single pass and outputs a consolidated signal table.

```bash
python strategies/tps_scanner.py --symbol SPY --window 10 --r2 0.8 --show 15 --save
```

**Output columns (in addition to OHLCV + EMAs):**
- Pattern regression: `res_slope`, `sup_slope`, `res_r2`, `sup_r2`
- Pattern flags: `bull_flag`, `bull_pennant`
- TTM Squeeze Pro raw: `SQZPRO_ON_NARROW`, `SQZPRO_ON_NORMAL`, `SQZPRO_ON_WIDE`, `SQZPRO_OFF_WIDE`
- Derived squeeze flags:
  - `ttm_squeeze` — True when ANY squeeze channel is active (NARROW/NORMAL/WIDE)
  - `ttm_squeeze_fired` — True on the bar the squeeze releases (momentum impulse)
- Composite: `tps_all` (all T, P, S true), `tps_score` (sum of component flags: 0–4)

**Summary printed to console:**
- Trend days %, Flag/Pennant hit rates
- TTM Squeeze ON % (squeeze in progress) and Fired % (breakout events)
- Current bar state and TPS Score (0–4)
- Optional CSV export via `--save`

**Note:** `tps_all` requires `Upward_Trend` AND (bull flag OR pennant) AND `ttm_squeeze` (squeeze active). Use `ttm_squeeze_fired` as a timing trigger for entries.

## Project Structure

```
nate.tps.pro/
├── strategies/
│   ├── ema_trend_analysis.py   # EMA alignment scanner
│   └── requirements.txt        # Python dependencies
└── README.md                   # This file
```

More strategies (Iron Condor, Covered Call, CSP) will be added incrementally.
