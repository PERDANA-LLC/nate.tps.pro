# Nate — Options Detective (tps.pro)

Automated options trading signal scanner and strategy engine.

## Current Features

### TPS Framework

Nate implements the **TPS** (Trend-Pattern-Squeeze) scanner for high-probability options setups:

- **T — TREND**: EMA alignment (8/21/55) from Schwab API
- **P — PATTERN**: Bull flag & bull pennant detection (vectorized rolling regression)
- **S — SQUEEZE**: TTM Squeeze Pro (Bollinger Bands contracted inside Keltner Channels) – coming soon

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

## Project Structure

```
nate.tps.pro/
├── strategies/
│   ├── ema_trend_analysis.py   # EMA alignment scanner
│   └── requirements.txt        # Python dependencies
└── README.md                   # This file
```

More strategies (Iron Condor, Covered Call, CSP) will be added incrementally.
