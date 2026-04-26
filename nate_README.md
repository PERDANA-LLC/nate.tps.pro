# Nate — Options Detective (tps.pro)

Automated options trading signal scanner and strategy engine.

## Current Features

### EMA Trend Analyzer

Fetches price history from Schwab API and identifies bullish EMA alignment (8 > 21 > 55).

```bash
# Export credentials
export SCHWAB_API_KEY="your_app_key"
export SCHWAB_API_SECRET="your_app_secret"
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

## Project Structure

```
nate.tps.pro/
├── strategies/
│   ├── ema_trend_analysis.py   # EMA alignment scanner
│   └── requirements.txt        # Python dependencies
└── README.md                   # This file
```

More strategies (Iron Condor, Covered Call, CSP) will be added incrementally.
