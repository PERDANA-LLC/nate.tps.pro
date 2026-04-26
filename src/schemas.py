"""
Pydantic schemas for API requests/responses.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class ScanRequest(BaseModel):
    """Request body for /scan endpoint."""
    symbol: str = Field(..., min_length=1, max_length=10, description="Ticker symbol")
    strategy: Optional[str] = Field(None, description="Filter by strategy type")
    expiration_days: int = Field(default=30, ge=1, le=365, description="Days to expiration")
    limit: int = Field(default=10, ge=1, le=50, description="Max results to return")


class StrategyLeg(BaseModel):
    """Single leg of an options strategy."""
    strike: float
    expiration: str
    option_type: str  # call/put
    action: str  # buy/sell
    premium: float
    delta: float
    quantity: int = 1


class StrategyResponse(BaseModel):
    """Single strategy in scan results."""
    rank: int
    strategy: str
    symbol: str
    probability: float  # POP percentage
    max_profit: float
    max_loss: float
    net_credit: float
    score: float
    expiration: str
    days_to_expiry: int
    id: Optional[int] = None


class ScanResponse(BaseModel):
    """Response from /scan endpoint."""
    symbol: str
    count: int
    strategies: List[StrategyResponse]


class StrategySummary(BaseModel):
    """Compact summary for /strategies list."""
    id: int
    symbol: str
    strategy_type: str
    probability: float
    net_credit: float
    score: float
    created_at: datetime


class PaperTradeRequest(BaseModel):
    """Request to create paper trade."""
    scan_id: int = Field(..., description="ID of strategy scan to execute")
    quantity: int = Field(default=1, ge=1, le=100)


class PaperTradeResponse(BaseModel):
    """Response from paper trade creation."""
    position_id: int
    symbol: str
    strategy: str
    entry_price: float
    quantity: int
    message: str


class PositionSummary(BaseModel):
    """Summary of open position."""
    id: int
    symbol: str
    strategy: str
    entry_price: float
    current_price: Optional[float]
    unrealized_pnl: float
    days_open: int


class AccountSummary(BaseModel):
    """Account P&L summary."""
    initial_balance: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    open_positions: int
    closed_positions: int
