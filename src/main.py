"""
Options Detective - FastAPI Application
Main entry point for the options trading scanner MVP.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import datetime
import asyncio
import json

from .config import settings
from . import models, schemas, schwab_api, strategies, database

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.debug else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Options Detective...")
    from . import models  # Import to register models
    database.create_tables(models.Base)
    
    # Initialize Schwab API (would need auth token)
    # schwab.schwab.authenticate()  # Uncomment after setup
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")


app = FastAPI(
    title="Options Detective",
    description="Automated options trading scanner for Schwab users",
    version="0.1.0",
    lifespan=lifespan
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Database dependency
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- API Routes ---

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "Options Detective"}


@app.post("/api/scan", response_model=schemas.ScanResponse)
async def run_scan(
    request: schemas.ScanRequest,
    db: Session = Depends(get_db)
):
    """
    Run options strategy scan for a symbol.
    
    Args:
        request: ScanRequest with symbol and filters
        db: Database session
        
    Returns:
        List of scored strategies
    """
    try:
        logger.info(f"Scanning {request.symbol} for strategies...")
        
        # 1. Get option chain from Schwab
        # In production: use schwab.get_option_chain()
        # For demo: generate mock data
        
        mock_chain = generate_mock_option_chain(request.symbol, request.expiration_days)
        spot_price = mock_chain['spot_price']
        
        # 2. Score strategies
        scanner = strategies.StrategyScanner(min_probability=1.0, min_volume=0, min_iv_rank=0.0)
        found_strategies = scanner.scan_symbol(
            request.symbol,
            mock_chain,
            spot_price
        )
        
        # 3. Save top strategies to database
        saved = []
        for strat in found_strategies[:request.limit]:
            record = models.StrategyScan(
                symbol=strat.symbol,
                strategy_type=strat.strategy_type,
                expiration=strat.expiration,
                days_to_expiry=strat.days_to_expiry,
                max_profit=strat.max_profit,
                max_loss=strat.max_loss,
                net_credit=strat.net_credit,
                probability=strat.probability,
                iv_rank=strat.iv_rank,
                score=strat.score,
                legs_json=json.dumps(strategies.serialize_legs(strat.legs))
            )
            db.add(record)
            saved.append(record)
        
        db.commit()
        
        # Convert to response schema
        results = []
        for i, strat in enumerate(saved):
            results.append({
                "id": strat.id,
                "rank": i + 1,
                "strategy": strat.strategy_type,
                "symbol": strat.symbol,
                "probability": strat.probability,
                "max_profit": round(strat.max_profit, 2),
                "max_loss": round(strat.max_loss, 2),
                "net_credit": round(strat.net_credit, 2),
                "score": round(strat.score, 2),
                "expiration": strat.expiration,
                "days_to_expiry": strat.days_to_expiry
            })
        
        return schemas.ScanResponse(
            symbol=request.symbol,
            count=len(results),
            strategies=results
        )
        
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategies", response_model=List[schemas.StrategySummary])
def get_strategies(
    symbol: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Get recent strategy scans."""
    query = db.query(models.StrategyScan).order_by(
        models.StrategyScan.created_at.desc()
    )
    
    if symbol:
        query = query.filter(models.StrategyScan.symbol == symbol.upper())
    
    records = query.limit(limit).all()
    
    return [
        schemas.StrategySummary(
            id=r.id,
            symbol=r.symbol,
            strategy_type=r.strategy_type,
            probability=r.probability,
            net_credit=round(r.net_credit, 2),
            score=round(r.score, 2),
            created_at=r.created_at
        )
        for r in records
    ]


@app.post("/api/paper-trade", response_model=schemas.PaperTradeResponse)
def create_paper_trade(
    request: schemas.PaperTradeRequest,
    db: Session = Depends(get_db)
):
    """
    Create a paper trade from a strategy scan.
    Records the position for P&L tracking.
    """
    # Find the scan record
    scan = db.query(models.StrategyScan).filter(
        models.StrategyScan.id == request.scan_id
    ).first()
    
    if not scan:
        raise HTTPException(status_code=404, detail="Strategy scan not found")
    
    # Create paper position
    position = models.PaperPosition(
        symbol=scan.symbol,
        strategy_type=scan.strategy_type,
        entry_price=scan.net_credit if scan.net_credit > 0 else -scan.net_credit,
        quantity=request.quantity,
        legs_json=scan.legs_json,
        status="open"
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    
    return schemas.PaperTradeResponse(
        position_id=position.id,
        symbol=position.symbol,
        strategy=position.strategy_type,
        entry_price=position.entry_price,
        quantity=position.quantity,
        message="Paper trade created successfully"
    )


@app.get("/api/positions", response_model=List[schemas.PositionSummary])
def get_positions(db: Session = Depends(get_db)):
    """Get all open paper trading positions."""
    positions = db.query(models.PaperPosition).filter(
        models.PaperPosition.status == "open"
    ).all()
    
    return [
        schemas.PositionSummary(
            id=p.id,
            symbol=p.symbol,
            strategy=p.strategy_type,
            entry_price=p.entry_price,
            current_price=p.current_price or p.entry_price,
            unrealized_pnl=p.unrealized_pnl,
            days_open=(datetime.utcnow() - p.created_at).days
        )
        for p in positions
    ]


@app.delete("/api/positions/{position_id}")
def close_position(position_id: int, db: Session = Depends(get_db)):
    """Close a paper trading position."""
    position = db.query(models.PaperPosition).filter(
        models.PaperPosition.id == position_id
    ).first()
    
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    
    if position.status != "open":
        raise HTTPException(status_code=400, detail="Position already closed")
    
    # In production: calculate actual P&L based on current market
    # For MVP, mark as closed
    position.status = "closed"
    position.closed_at = datetime.utcnow()
    db.commit()
    
    return {"message": f"Position {position_id} closed"}


@app.get("/api/account")
def get_account_summary(db: Session = Depends(get_db)):
    """Get account summary including P&L."""
    from decimal import Decimal
    
    # Total P&L from closed positions
    closed_positions = db.query(models.PaperPosition).filter(
        models.PaperPosition.status == "closed"
    ).all()
    realized_pnl = sum(p.realized_pnl for p in closed_positions)
    
    # Unrealized from open positions
    open_positions = db.query(models.PaperPosition).filter(
        models.PaperPosition.status == "open"
    ).all()
    unrealized_pnl = sum(p.unrealized_pnl for p in open_positions)
    
    return {
        "initial_balance": settings.initial_balance,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(realized_pnl + unrealized_pnl, 2),
        "open_positions": len(open_positions),
        "closed_positions": len(closed_positions)
    }


# --- Dashboard Route ---

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the main dashboard."""
    with open("static/index.html", "r") as f:
        return f.read()


# Aux functions for mock data
def generate_mock_option_chain(symbol: str, dte: int) -> Dict:
    """Generate mock option chain for development/testing."""
    import random
    from datetime import datetime, timedelta
    
    expiry = (datetime.now() + timedelta(days=dte)).strftime('%Y-%m-%d')
    spot = {
        'SPY': 450.0,
        'QQQ': 380.0,
        'IWM': 200.0
    }.get(symbol.upper(), 100.0)
    
    # Generate strikes every $2.5 or $5
    if spot > 300:
        step = 10
    elif spot > 100:
        step = 5
    else:
        step = 2.5
    
    strikes = [spot - (i * step) for i in range(20, 0, -1)] +               [spot + (i * step) for i in range(1, 21)]
    
    calls = []
    puts = []
    
    for strike in strikes:
        moneyness = strike / spot
        
        # Approximate IV and delta based on moneyness
        iv = 0.20 + abs(1 - moneyness) * 0.30  # Higher OTM = higher IV
        time_frac = dte / 365
        
        # Simplified delta approximation
        if strike > spot:  # OTM call
            delta = math.exp(-(moneyness - 1)**2 / 0.1) * 0.5
        else:  # ITM call
            delta = 0.5 + (1 - moneyness) * 0.5
            
        # Put delta = call delta - 1
        put_delta = delta - 1
        
        # Premium approximation (very rough)
        call_premium = max(0.01, spot * iv * math.sqrt(time_frac) * abs(delta) * 100) / 100
        put_premium = max(0.01, spot * iv * math.sqrt(time_frac) * abs(put_delta) * 100) / 100
        
        call = {
            "symbol": f"{symbol}_{expiry}_{strike}_C",
            "strike": strike,
            "expiration": expiry,
            "bid": round(call_premium * 0.95, 2),
            "ask": round(call_premium * 1.05, 2),
            "volume": random.randint(100, 5000),
            "open_interest": random.randint(1000, 50000),
            "delta": round(delta, 4),
            "gamma": round(abs(delta) * 0.1, 6),
            "theta": round(-call_premium * 0.1, 4),
            "vega": round(call_premium * 0.5, 4),
            "implied_volatility": round(iv, 4)
        }
        
        put = {
            "symbol": f"{symbol}_{expiry}_{strike}_P",
            "strike": strike,
            "expiration": expiry,
            "bid": round(put_premium * 0.95, 2),
            "ask": round(put_premium * 1.05, 2),
            "volume": random.randint(100, 5000),
            "open_interest": random.randint(1000, 50000),
            "delta": round(put_delta, 4),
            "gamma": round(abs(put_delta) * 0.1, 6),
            "theta": round(-put_premium * 0.1, 4),
            "vega": round(put_premium * 0.5, 4),
            "implied_volatility": round(iv, 4)
        }
        
        calls.append(call)
        puts.append(put)
    
    return {
        "symbol": symbol,
        "spot_price": spot,
        "expiration": expiry,
        "calls": calls,
        "puts": puts
    }


import math  # Required for mock data generation
