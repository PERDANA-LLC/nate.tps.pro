# Options Detective

AI-powered options trading scanner and automated execution platform for Charles Schwab users.

> **Status:** MVP in active development. Currently implements strategy scanning with mock data. Schwab API integration pending authentication.

---

## Features

### Strategy Scanner
- **Iron Condor** - High probability, range-bound market strategy
- **Covered Call** - Income generation on long stock positions  
- **Cash-Secured Put** - Bullish assignment strategy

### Dashboard
- Real-time strategy scoring and ranking
- Paper trading simulation
- Position tracking and P&L
- Mobile-responsive UI (Bootstrap 5)

### Automation (Future)
- Discord alerts for high-probability setups
- Automated trade execution via Schwab API
- Portfolio risk management
- Scheduled daily scans

---

## Tech Stack

**Backend:**
- FastAPI (Python 3.12)
- SQLAlchemy ORM
- PostgreSQL (SQLite for MVP)
- APScheduler (market hours scanning)

**Frontend:**
- Bootstrap 5 + vanilla JavaScript
- Responsive dashboard

**Infrastructure:**
- Docker + Docker Compose
- Deployable to any VPS/cloud

**External APIs:**
- Charles Schwab Developer API (sandbox + live)
- Discord Webhooks (alerts)

---

## Quick Start (Local Development)

### Prerequisites
- Python 3.12+
- pip
- Optional: Docker for containerized deployment

### 1. Clone & Setup
```bash
cd /tmp/options_detective

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scriptsctivate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your settings (Schwab API credentials coming soon)
```

### 3. Initialize Database
```bash
python -c "from src.database import create_tables; create_tables()"
```

### 4. Run Development Server
```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 in your browser.

---

## Docker Deployment (Production)

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop
docker-compose down
```

Access at http://your-vps-ip:8000

---

## Project Structure

```
options_detective/
├── src/
│   ├── __init__.py
│   ├── main.py           # FastAPI app & routes
│   ├── config.py         # Configuration & env vars
│   ├── database.py       # DB session & models
│   ├── models.py         # SQLAlchemy models
│   ├── schemas.py        # Pydantic schemas
│   ├── schwab_api.py     # Charles Schwab API wrapper
│   ├── strategies.py     # Strategy scoring logic
│   ├── greeks.py         # Black-Scholes calculator
│   └── api/              # API routers (future)
├── static/
│   ├── index.html        # Dashboard UI
│   └── app.js            # Frontend logic
├── data/                 # SQLite DB (development)
├── tests/               # Unit tests
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── README.md
└── .gitignore
```

---

## API Endpoints (MVP)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/` | GET | Dashboard (HTML) |
| `/api/scan` | POST | Run options scan |
| `/api/strategies` | GET | List recent scans |
| `/api/paper-trade` | POST | Create paper trade |
| `/api/positions` | GET | List open positions |
| `/api/positions/{id}` | DELETE | Close position |
| `/api/account` | GET | Account summary |

---

## Usage Example

### Running a Scan via API
```bash
curl -X POST http://localhost:8000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"symbol": "SPY", "expiration_days": 30, "limit": 10}'
```

Response:
```json
{
  "symbol": "SPY",
  "count": 10,
  "strategies": [
    {
      "rank": 1,
      "strategy": "Iron Condor",
      "symbol": "SPY",
      "probability": 72.5,
      "max_profit": 425.00,
      "max_loss": 575.00,
      "net_credit": 2.15,
      "score": 68.4,
      "expiration": "2024-02-16"
    }
  ]
}
```

---

## Strategy Scoring Logic

### Iron Condor
**Score = (Probability × 0.4) + (ROI × 0.3) + (IV Rank × 0.3)**

Requirements:
- Short leg delta: 0.10-0.30
- Liquidity: volume > 100
- IV Rank: > 50% (configurable)
- Net credit > 0

### Covered Call
Annualized yield > 8% required.

### Cash-Secured Put
Premium > $0.50, probability > 70%.

---

## Configuration

All configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | sqlite:///./db | Database connection |
| SCHWAB_CLIENT_ID | - | Schwab API client ID |
| SCHWAB_CLIENT_SECRET | - | Schwab API secret |
| SCHWAB_AUTH_MODE | sandbox | sandbox or live |
| PAPER_TRADING_MODE | True | Use paper trading |
| INITIAL_BALANCE | 10000 | Starting cash |
| MAX_POSITION_SIZE | 0.05 | 5% per position |
| DISCORD_WEBHOOK_URL | None | Alerts channel |

---

## Roadmap

### Phase 1 (Current - MVP)
- [x] Strategy scanner (3 strategies)
- [x] Paper trading simulation
- [x] Dashboard UI
- [x] Mock data generation
- [ ] Real Schwab API integration
- [ ] Database persistence (PostgreSQL)

### Phase 2 (Automation)
- [ ] Discord alert integration
- [ ] Scheduled daily scans
- [ ] Real-time market data feed
- [ ] Email notifications

### Phase 3 (Execution)
- [ ] Live Schwab order placement
- [ ] Risk management layer (position sizing, loss limits)
- [ ] Multi-account support
- [ ] Advanced strategies (Butterflies, Calendars)

### Phase 4 (Scale)
- [ ] Multi-device push notifications
- [ ] Historical backtesting UI
- [ ] Strategy customization builder
- [ ] Team/collaborative features
- [ ] Mobile app (React Native)

---

## Schwab API Integration Notes

To connect to real Charles Schwab data:

1. **Apply for API access** at [Schwab Developer Portal](https://developer.schwab.com/)
2. **Get credentials** (Client ID, Client Secret)
3. **Set callback URL** to `http://localhost:8000/callback` (or your domain)
4. For initial auth, visit:
   ```
   https://api.schwab.com/v1/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8000/callback&scope=read,intraday,trading
   ```
5. Exchange code for token (handled in `schwab_api.py`)

**Sandbox mode:** Uses mock data. Safe for development.

---

## Security Notes

- Never commit `.env` file (gitignored)
- Use strong SECRET_KEY in production
- `PAPER_TRADING_MODE=True` by default for safety
- All real trading requires explicit user permission
- Schwab API credentials encrypted at rest (future)

---

## Contributing

This is a solo project. Future: open source contributions welcome.

---

## License

MIT License - you can use this code for your own trading platform.

---

## Disclaimer

**This software is for educational purposes only.**

Options Detective is not financial advice. Trading options involves significant risk of loss. Past performance does not guarantee future results. Always do your own research and consult with a financial advisor before investing.

The authors assume no responsibility for any trading losses you may incur.

---

## Support

For issues, feature requests, or collaboration:
- GitHub Issues (when public)
- Or reach out directly

---

**Built with Claude Code + FastAPI + Schwab API**  
**by a retail trader, for retail traders**
