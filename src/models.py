"""
Database models for Options Detective.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from typing import Optional


class Base(DeclarativeBase):
    pass


class StrategyScan(Base):
    """Stored results of strategy scans."""
    __tablename__ = "strategy_scans"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    expiration: Mapped[str] = mapped_column(String(20), nullable=False)
    days_to_expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Strategy details
    max_profit: Mapped[float] = mapped_column(Float, nullable=False)
    max_loss: Mapped[float] = mapped_column(Float, nullable=False)
    net_credit: Mapped[float] = mapped_column(Float, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    iv_rank: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    
    legs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    was_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    user_action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class PaperPosition(Base):
    """Paper trading positions."""
    __tablename__ = "paper_positions"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="open")
    
    legs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    @property
    def total_pnl(self) -> float:
        return self.unrealized_pnl + self.realized_pnl
    
    @property
    def is_open(self) -> bool:
        return self.status == "open"


class UserSettings(Base):
    """User risk parameters."""
    __tablename__ = "user_settings"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    
    max_position_size_pct: Mapped[float] = mapped_column(Float, default=0.05)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=0.02)
    max_position_per_symbol: Mapped[int] = mapped_column(Integer, default=2)
    auto_close_hours_before_expiry: Mapped[int] = mapped_column(Integer, default=4)
    
    enable_iron_condor: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_covered_call: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_csp: Mapped[bool] = mapped_column(Boolean, default=True)
    
    discord_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    email_alerts: Mapped[bool] = mapped_column(Boolean, default=False)


class BacktestResult(Base):
    """Stored backtest results."""
    __tablename__ = "backtest_results"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=True)  # JSON of params used
    
    # Performance metrics
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    average_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    sortino_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False)
    avg_days_held: Mapped[float] = mapped_column(Float, nullable=False)
    
    # Additional metrics
    best_trade: Mapped[float] = mapped_column(Float, nullable=False)
    worst_trade: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_return: Mapped[float] = mapped_column(Float, default=0.0)
    alpha: Mapped[float] = mapped_column(Float, default=0.0)
    beta: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Metadata
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # Cross-checked


class OptimizationResult(Base):
    """Optimization runs and parameter suggestions."""
    __tablename__ = "optimization_results"
    
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    optimization_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g., "grid_search", "genetic", "walk_forward"
    
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False)  # Best params
    metric_optimized: Mapped[str] = mapped_column(String(50), nullable=False)
    # e.g., "sharpe_ratio", "win_rate", "total_pnl"
    
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    backtest_result_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("backtest_results.id"))
    
    training_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    training_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    test_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    test_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    was_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# Missing import at top of file needed
from typing import Optional
