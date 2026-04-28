# Project **nate** — High-Level Overview

## 1. What is it?

A **stock & options setup scanner** — single Python file (`tps_scan.py`, 2207 lines) that runs TPS analysis on any ticker. It's a **screener, not a trading bot**: it tells you *when a setup is forming*, but does not place orders.

```
TPS_SCAN("SYMBOL")
  ↓ Schwab API (or yfinance fallback) → fetches daily candles
  ↓ Computes TREND + PATTERN + SQUEEZE
  ↓ Overlays market context (SPY/QQQ/VIX/breadth/sentiment/TICK/VXX)
  ↓ Runs 8-gate KPI checklist → KPI_SCORE (0-8) + KPI_PERFECT
  ↓ Prints a DataFrame of the last 15 bars + all context summaries
```

## 2. What does it trade?

**Stocks + stock options** — specifically looking for **bullish option setups**:

| Ticker type | Examples | What it finds |
|---|---|---|
| Broad market ETF | SPY, QQQ | Trend + squeeze alone (no short-interest) |
| High-short-interest stocks | GME, AMC-type names | Full 8-gate KPI (short squeeze candidates) |
| Any individual equity | AAPL, MSFT, TSLA | Full scan with SPY/QQQ correlation |

**Intended option plays:** Long calls, bull put spreads, debit call spreads — all directionally bullish.

## 3. TPS = What it scans for

### TREND — EMA Stack
```
EMA_8 > EMA_21 > EMA_55  →  Upward_Trend = True  (bullish)
EMA_8 < EMA_21 < EMA_55  →  Downward_Trend = True (bearish)
```
Clean, simple, no curve-fitting. If EMAs are tangled, trend is neutral.

### PATTERN — Bull Flags & Pennants
Uses **vectorized rolling regression** (least-squares on highs and lows over a 10-bar window, R² ≥ 0.80):
- **Bull Flag:** Parallel upward-sloping support and resistance (pole + flag)
- **Bull Pennant:** Converging lines (resistance slopes down, support slopes up)
- Both are continuation patterns — the trade fires when price breaks above the flag/pennant

### SQUEEZE — TTM Squeeze Pro
3 Keltner Channels (wide / normal / narrow) plus momentum histogram:
- **Red dot (wide):** High volatility, no squeeze
- **Yellow dot (normal):** Normal range
- **Orange dot (narrow):** Maximum compression — squeeze is ON — energy building
- **Green dot:** Squeeze fired — breakout happening
- **Cyan histogram:** Momentum turning positive *and rising* — confirms direction of breakout

## 4. The 8-Gate KPI (The "Perfect Setup" Checklist)

Scored 0-8 per bar. **KPI_PERFECT = all 8 pass:**

| Gate | Condition | What it means |
|---|---|---|
| 1 | **TREND up** | EMA stack bullish |
| 2 | **PATTERN** | Bull flag or pennant present |
| 3 | **SQUEEZE on narrow** | Orange dot = maximum compression |
| 4 | **Momo cyan** | Squeeze momentum histogram rising above zero |
| 5 | **Short float > 20%** | High short interest = squeeze fuel |
| 6 | **Short ratio > 5 days** | Days-to-cover = how painful for shorts |
| 7 | **VWAP uptrend setup** | Price just above upward-sloping VWAP |
| 8 | **VWAP-cross volume burst** | Crossed VWAP with volume surge within last 3 bars (shorts getting liquidated) |

Gates 5-6 require FMP API key for short-interest data (falls back to yfinance).

## 5. Market Context Overlays

Every scan also pulls these as row-level context on the DataFrame:

| Context | Source | What it tells you |
|---|---|---|
| **SPY correlation + beta** | 6-month daily returns | How much the stock moves with the market |
| **QQQ trend + correlation** | Price vs SMA20 + RSI14 | Tech sector regime (bullish/bearish/neutral) |
| **VIX regime** | $VIX quote (Schwab or yfinance) | High vol → sell premium; low vol → buy premium |
| **NYSE breadth ($ADD)** | Schwab market data | Advancing minus declining — broad participation |
| **$PCALL sentiment** | Put/Call ratio | Contrarian: high PCALL = fear (bullish), low = greed (bearish) |
| **$TICK exhaustion** | NYSE tick | Intraday buying/selling pressure extremes |
| **VXX fade** | Volatility ETN | VXX RSI extremes → mean-reversion trade bias |
| **Multi-TF squeeze** | 10 timeframes (Weekly→5min) | Squeeze state across all timeframes in one view |

## 6. Data Sources

| Source | Used for |
|---|---|
| **Schwab Developer API** (primary) | Daily candles, quotes, $VIX, $ADD, $TICK, $PCALL |
| **yfinance** (fallback) | ^VIX when Schwab can't quote indices; short interest fallback |
| **Financial Modeling Prep** (optional) | Short-interest data (Premium+ plan required for `/api/v4/short-interest`) |

Schwab API keys are already in `.env`.

## 7. How to run it

```bash
cd /Volumes/181TB/Perdana-LLC/nate.tps.pro
python tps_scan.py
```
That scans **SPY** (hardcoded default) and prints:
- Last 15 daily bars with all TPS + KPI columns
- Count of KPI_PERFECT bars
- SPY correlation & beta
- QQQ trend, correlation & beta
- VIX level & regime
- $ADD breadth
- $PCALL sentiment
- $TICK exhaustion
- VXX volatility fade
- Multi-timeframe squeeze panel (10 timeframes)

To scan other tickers, call from Python:
```python
from tps_scan import TPS_SCAN
df = TPS_SCAN("AAPL", months=6)
```

## 8. Differences from go-trader

| | go-trader | nate |
|---|---|---|
| **Type** | Automated trading bot | Manual setup scanner |
| **Runs** | Continuous scheduler | One-shot CLI |
| **Asset** | Crypto + futures | Stocks + options |
| **Executes?** | Yes (paper + live) | No — signals only |
| **Strategy count** | 50+ strategies | One 8-gate KPI (plus context filters) |
| **Stack** | Go + Python subprocess | Pure Python |
| **Key** | Many exchanges | Schwab Developer API only |
