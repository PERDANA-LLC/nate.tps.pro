# Paper Trading System — Plan

**Project**: nate.tps.pro | **Created**: 2026-04-28 | **Status**: 📋 Planning

---

## 1. Goal

Add paper trading (simulated buy/sell) to the TPS scanner Discord bot.  
Live trading uses `schwab_client.py` when Schwab approves the app.  
Paper mode = JSON file state, no real money. Same commands work in both modes.

---

## 2. Current Architecture (What Exists)

| File | Role | Trading? |
|------|------|----------|
| `tps_scan.py` (80KB) | Core scanner: `TPS_SCAN(symbol, client) -> DataFrame` with `KPI_SCORE(0-8)`, `KPI_PERFECT`, market context | ❌ |
| `discord_bot.py` (12KB) | Discord bot: `/scan`, `/watchlist`, `/add`, `/remove`, `/status` | ❌ |
| `telegram_bot.py` (16KB) | Telegram bot: same + scheduled watchlist alerts | ❌ |
| `schwab_client.py` (12KB) | Schwab OAuth + `get_client()` → `schwabdev.Client` singleton (works for data, order API available but not wrapped) | ⚠️ Blocked |
| `fmp_client.py` (11KB) | FMP API: `quote()`, `price_history()` — data only, no orders | ❌ |
| `.env` | Keys + config | N/A |

**Key integration contract**: `TPS_SCAN()` returns a DataFrame indexed by datetime with columns: `close`, `EMA_8/21/55`, `Upward_Trend`, `bull_flag`, `pennant`, `SQZPRO_ON_*`, `squeeze_fired`, `momo_cyan`, `KPI_SCORE`, `KPI_PERFECT`, `vwap_v*`, `pcall_*`, `tick_*`, `vxx_*`.

---

## 3. What We Build

### 3.1 New Files

#### A. `paper_trader.py` (~250 lines)

**Responsibility**: Simulated trading engine. JSON-file state. No external APIs.

```
PaperTrader:
  __init__(capital=10000, state_file="paper_state.json")
  ── data ──
  get_portfolio()        → {cash, equity, positions, total_pnl}
  get_positions()        → [{symbol, qty, avg_price, last_price, pnl}]
  get_orders()           → [{id, symbol, side, qty, price, status, time}]
  ── actions ──
  buy(symbol, qty, price)→ order_id (market simulation: fills at price)
  sell(symbol, qty, price)→ order_id
  close_position(symbol) → order_id (full liquidation)
  ── internal ──
  _save() / _load()      → atomic JSON writes
  _update_prices(quotes) → mark-to-market all positions
```

**State JSON schema**:
```json
{
  "capital": 10000.0,
  "cash": 8523.45,
  "positions": {
    "SPY": {"qty": 2, "avg_price": 567.80, "orders": ["ord-001"], "open_time": "..."}
  },
  "orders": [
    {"id": "ord-001", "symbol": "SPY", "side": "buy", "qty": 2, "price": 567.80, "status": "filled", "time": "..."}
  ],
  "pnl_history": [...]
}
```

#### B. `broker_interface.py` (~80 lines)

**Responsibility**: Abstract protocol — same API whether paper or live.  

```python
class BrokerInterface(ABC):
    @abstractmethod
    def buy(self, symbol, qty, price) -> OrderResult: ...
    @abstractmethod
    def sell(self, symbol, qty, price) -> OrderResult: ...
    @abstractmethod
    def get_portfolio(self) -> Portfolio: ...
    @abstractmethod
    def get_orders(self) -> list[OrderResult]: ...
    @abstractmethod
    def close_position(self, symbol) -> OrderResult: ...

def get_broker(mode="paper") -> BrokerInterface:
    """Factory: 'paper' → PaperTrader, 'live' → SchwabBroker (future)."""
```

### 3.2 Modified Files

#### C. `discord_bot.py` — Add trading commands

New commands (5 additions, ~150 lines):

| Command | Description |
|---------|-------------|
| `/trade SPY buy 2` | Execute buy (paper fills at last close) |
| `/trade SPY sell 2` | Execute sell |
| `/portfolio` | Show cash, positions, total equity, P&L |
| `/orders` | List recent trades with status |
| `/close SPY` | Close all position in symbol |
| `/autotrade on 6` | Enable auto-trade when KPI >= threshold |

**How `/trade` works**:
1. Parse: `/trade SPY buy 5`
2. Run `TPS_SCAN(symbol)` to get latest price (`df.iloc[-1].close`)
3. Call `broker.buy(symbol, qty, price)` or `broker.sell()`
4. Format order confirmation (embed with P&L summary)

#### D. `.env` / config additions

```env
# Paper Trading
PAPER_INITIAL_CAPITAL=10000
PAPER_MODE=true              # true=paper, false=live (needs Schwab)
PAPER_STATE_FILE=paper_state.json

# Auto-trade
AUTO_TRADE_THRESHOLD=6       # KPI_SCORE >= 6 triggers trade
AUTO_TRADE_POSITION_PCT=20   # % of capital per position
```

#### E. `telegram_bot.py` (optional — Phase 2)

Add same `/portfolio`, `/orders` commands to Telegram bot. Lower priority.

---

## 4. Execution Order

| # | Task | File | Est. Effort |
|---|------|------|-------------|
| 1 | Create `paper_trader.py` with full state machine + tests | NEW | 🟡 Medium |
| 2 | Create `broker_interface.py` with ABC + factory | NEW | 🟢 Small |
| 3 | Add `/portfolio`, `/orders` to discord_bot.py | MOD | 🟢 Small |
| 4 | Add `/trade buy/sell`, `/close` to discord_bot.py | MOD | 🟡 Medium |
| 5 | Add `/autotrade` + signal pipeline | MOD | 🟡 Medium |
| 6 | Integration test: scan → buy → portfolio → sell → P&L | TEST | 🟡 Medium |

---

## 5. Risk / Edge Cases

| Risk | Mitigation |
|------|------------|
| Multiple bots accessing same state file | Use `fcntl.flock` or single process assumption |
| Price stale between scan and trade | Fill at scan price (snapshot), note timestamp |
| Short selling paper logic | Skip initially — long only |
| Schwab order API differences | `BrokerInterface` abstracts; `SchwabBroker` adapter later |
| JSON corruption on crash | Atomic write (write to .tmp → os.rename) |
| Position sizing > available cash | Reject with clear error message |

---

## 6. Exit Criteria

- [ ] `/portfolio` shows cash + positions with live P&L
- [ ] `/trade SPY buy 2` creates order, deducts cash, adds position
- [ ] `/trade SPY sell 1` reduces position, adds cash, shows realized P&L
- [ ] `/close SPY` liquidates full position
- [ ] `/autotrade on 7` triggers buy when KPI_SCORE hits 7+
- [ ] State survives bot restart (JSON file)
- [ ] `broker_interface.get_broker("paper")` and `get_broker("live")` both import cleanly

---

## 7. Open Questions for User

1. **Position sizing**: Fixed share count (`/trade SPY buy 5`) or % of capital (`/trade SPY buy 20%`)?
2. **Auto-trade**: Only when KPI_PERFECT, or any KPI_SCORE >= threshold?
3. **Short selling**: Skip for now, add later?
4. **Discord-only or Telegram too**: Start with Discord, then mirror to Telegram?
