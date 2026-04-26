# Options Detective - 5-Minute Quickstart

## TL;DR - Get Running NOW

```bash
cd /tmp/options_detective

# 1. Install dependencies (2 min)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Setup database (10 sec)
python -c "from src.database import create_tables; create_tables()"

# 3. Run! (5 sec)
uvicorn src.main:app --reload --port 8000
```

Open http://localhost:8000 - you're ready to scan!

---

## What You Just Got

**Options Detective** is an automated options trading scanner with:
- Iron Condor, Covered Call, Cash-Secured Put scanners
- Paper trading simulation ($10K virtual)
- Mobile-responsive dashboard
- Schwab API integration ready (just add credentials)

---

## Key Files

| File | Purpose |
|------|---------|
| `src/main.py` | FastAPI app | 
| `src/schwab_api.py` | Schwab API wrapper |
| `src/strategies.py` | Strategy logic |
| `static/index.html` | Dashboard UI |
| `static/app.js` | Frontend logic |

---

## Next Steps

1. **Run a test scan**
   - Go to http://localhost:8000
   - Enter "SPY" and click "Scan Now"
   - See 10 strategy suggestions

2. **Connect Schwab API** (optional)
   - Edit `.env` with your Schwab credentials
   - Comment/uncomment auth in `src/main.py`

3. **Deploy to VPS**
   ```bash
   bash deploy.sh --docker   # Containerized
   bash deploy.sh --service  # Systemd service
   ```

---

## Architecture Dashboard

```
┌─────────────────┐
│   User Browser  │
└────────┬────────┘
         │ HTTP
    ┌────▼─────┐
    │FastAPI   │
    │(src/main)│
    └────┬─────┘
         │
    ┌────▼─────────────────────┐
    │ Strategy Scanner         │
    │ (strategies.py)          │
    └────┬─────────────────────┘
         │
    ┌────▼────────────┐   ┌─────────────┐
    │ Mock Data       │   │ Schwab API  │
    │ (for demo)      │   │ (production)│
    └─────────────────┘   └─────────────┘
         │
    ┌────▼────────────┐
    │ SQLite /        │
    │ PostgreSQL      │
    └─────────────────┘
```

---

## Support

- **Full docs:** See README.md
- **API docs:** http://localhost:8000/docs (when running)
- **Issues:** Check logs with `journalctl -u options-detective -f`

---

**You now have a complete options trading SaaS ready to launch.**
