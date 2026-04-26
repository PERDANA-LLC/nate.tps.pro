"""
Automated Paper Trading Engine
Simulates real trading with realistic P&L tracking and risk management.
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
import threading
import time
import logging

from .database import SessionLocal
from .models import PaperPosition, StrategyScan, UserSettings
from .greeks import calculate_greeks, probability_of_profit
from .strategies import StrategyScanner, OptionLeg, Strategy
from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class PaperTradeOrder:
    """Represents a paper trade order."""
    symbol: str
    strategy_type: str
    legs: List[OptionLeg]
    quantity: int
    entry_net_credit: float
    max_profit: float
    max_loss: float
    probability: float
    scan_id: int
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PaperTrader:
    """
    Automated paper trading simulator.
    
    Features:
    - Automatic trade execution based on scan results
    - Real-time P&L calculation using option pricing models
    - Risk management (position limits, daily loss limits)
    - Trade lifecycle management (entry, monitoring, exit)
    - Performance tracking
    """
    
    def __init__(
        self,
        max_positions: int = 5,
        max_position_size_pct: float = 0.05,
        daily_loss_limit_pct: float = 0.02,
        profit_target_pct: float = 0.50,  # 50% of max profit
        stop_loss_pct: float = 0.50,  # 50% of max loss
        enable_auto_rebalance: bool = False
    ):
        """
        Initialize paper trader.
        
        Args:
            max_positions: Maximum concurrent positions
            max_position_size_pct: Max portfolio % per position
            daily_loss_limit_pct: Max daily loss before halting
            profit_target_pct: Close at % of max profit
            stop_loss_pct: Close at % of max loss hit
            enable_auto_rebalance: Close losing positions to free capital
        """
        self.max_positions = max_positions
        self.max_position_size = max_position_size_pct
        self.daily_loss_limit = daily_loss_limit_pct
        self.profit_target = profit_target_pct
        self.stop_loss = stop_loss_pct
        self.auto_rebalance = enable_auto_rebalance
        
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.daily_pnl = 0.0
        
        # Strategy scanner with defaults (will be updated from config)
        self.scanner = StrategyScanner(
            min_probability=settings.max_position_size * 100 if hasattr(settings, 'max_position_size') else 50.0,
            min_volume=100,
            min_iv_rank=30.0
        )
        
        logger.info(f"PaperTrader initialized: max {max_positions} positions, "
                   f"{max_position_size_pct:.1%} per position")
    
    def start(self):
        """Start the paper trading monitor."""
        if self.running:
            logger.warning("PaperTrader already running")
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="PaperTrader-Monitor"
        )
        self.monitor_thread.start()
        logger.info("PaperTrader started")
    
    def stop(self):
        """Stop the paper trading monitor."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("PaperTrader stopped")
    
    def _monitor_loop(self):
        """Background thread that monitors positions and looks for signals."""
        while self.running:
            try:
                db = SessionLocal()
                
                # Check daily loss limit
                if self.daily_pnl <= -self.initial_balance * self.daily_loss_limit:
                    logger.warning(f"Daily loss limit hit: {self.daily_pnl:.2f}. Halting trading for today.")
                    time.sleep(900)  # Sleep 15 min before retry
                    continue
                
                # Check open positions for exit signals
                self._check_exits(db)
                
                # Look for new opportunities if under max positions
                open_count = db.query(PaperPosition).filter(
                    PaperPosition.status == "open"
                ).count()
                
                if open_count < self.max_positions:
                    self._scan_for_trades(db)
                
                db.close()
                
                # Sleep before next check
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Monitor loop error: {e}", exc_info=True)
                time.sleep(60)
    
    def _check_exits(self, db: Session):
        """Check open positions for exit conditions."""
        open_positions = db.query(PaperPosition).filter(
            PaperPosition.status == "open"
        ).all()
        
        for pos in open_positions:
            try:
                # Get current option prices (would use real-time data)
                # For mock: simulate price movement
                current_value = self._simulate_current_value(pos)
                pos.current_price = current_value
                
                # Calculate unrealized P&L
                entry_cost = pos.entry_price * pos.quantity * 100
                current_value_total = current_value * pos.quantity * 100
                pos.unrealized_pnl = current_value_total - entry_cost
                
                # Check exit conditions
                days_open = (datetime.utcnow() - pos.created_at).days
                max_profit = abs(pos.max_profit or pos.entry_price * 100)
                max_loss = abs(pos.max_loss or pos.entry_price * 100)
                
                # 1. Profit target
                if pos.unrealized_pnl >= max_profit * self.profit_target:
                    self._close_position(pos, db, "profit_target")
                    self.daily_pnl += pos.unrealized_pnl
                    logger.info(f"Profit target hit: Position {pos.id} closed with ${pos.unrealized_pnl:.2f}")
                
                # 2. Stop loss
                elif pos.unrealized_pnl <= -max_loss * self.stop_loss:
                    self._close_position(pos, db, "stop_loss")
                    self.daily_pnl += pos.unrealized_pnl
                    logger.warning(f"Stop loss hit: Position {pos.id} closed with ${pos.unrealized_pnl:.2f}")
                
                # 3. Near expiration (close 3 days before expiry)
                elif days_open >= 27:  # 30 DTE - 3 days
                    self._close_position(pos, db, "expiration_near")
                    self.daily_pnl += pos.unrealized_pnl
                    logger.info(f"Near expiry: Position {pos.id} closed")
                
                # 4. Extreme adverse movement
                if pos.unrealized_pnl < -self.initial_balance * 0.05:
                    logger.warning(f"Large loss on position {pos.id}: ${pos.unrealized_pnl:.2f}")
                    if self.auto_rebalance:
                        self._close_position(pos, db, "adverse_move")
                        self.daily_pnl += pos.unrealized_pnl
                
            except Exception as e:
                logger.error(f"Error checking position {pos.id}: {e}")
        
        db.commit()
    
    def _simulate_current_value(self, position: PaperPosition) -> float:
        """
        Simulate current option value based on time decay and price movement.
        
        In production, this would fetch real market data.
        """
        # Simplified: decay towards 0 at expiration
        days_held = (datetime.utcnow() - position.created_at).days
        dte_remaining = max(0, 30 - days_held)  # Assume 30 DTE originally
        
        if dte_remaining <= 0:
            # Expired - check intrinsic value
            # For demo: return 0
            return 0.01
        
        # Time decay: option loses value linearly (rough approximation)
        time_decay_factor = dte_remaining / 30.0
        
        # Add some random volatility
        import random
        noise = random.uniform(-0.1, 0.1)
        
        current = position.entry_price * time_decay_factor + noise
        return max(0.01, current)
    
    def _scan_for_trades(self, db: Session):
        """Scan for new trade opportunities and execute best one."""
        from .strategies import StrategyScanner, serialize_legs
        
        # Get recent scans
        recent_scans = db.query(StrategyScan).order_by(
            StrategyScan.created_at.desc()
        ).limit(20).all()
        
        if not recent_scans:
            logger.debug("No scans available for trading")
            return
        
        # Filter for high-quality setups
        candidates = []
        for scan in recent_scans:
            # Check if already traded
            existing = db.query(PaperPosition).filter(
                PaperPosition.legs_json.like(f'%{scan.id}%')
            ).first()
            
            if existing:
                continue
            
            # Check risk parameters
            if scan.probability < 60:
                continue
            
            if scan.max_loss / scan.max_profit > 3:
                continue  # Poor risk/reward
            
            # Score based on POP and ROI
            roi = (scan.net_credit * 100) / abs(scan.max_loss) * 100 if scan.max_loss else 0
            score = scan.probability * 0.5 + min(roi, 50) * 0.5
            
            candidates.append((score, scan))
        
        if not candidates:
            logger.debug("No suitable trade candidates found")
            return
        
        # Pick best candidate
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_scan = candidates[0][1]
        
        # Execute trade
        self._execute_trade(best_scan, db)
    
    def _execute_trade(self, scan: StrategyScan, db: Session):
        """Execute a paper trade from a strategy scan."""
        from .strategies import OptionLeg
        
        # Determine quantity based on risk management
        account_value = self.initial_balance
        position_max = account_value * self.max_position_size
        
        # Estimate margin requirement
        margin_required = abs(scan.max_loss) / 100  # per contract
        max_contracts = int(position_max / margin_required)
        quantity = max(1, min(max_contracts, 2))  # Max 2 contracts for safety
        
        # Create paper position
        position = PaperPosition(
            symbol=scan.symbol,
            strategy_type=scan.strategy_type,
            entry_price=scan.net_credit if scan.net_credit > 0 else -scan.net_credit,
            quantity=quantity,
            legs_json=scan.legs_json,
            status="open",
            max_profit=scan.max_profit,
            max_loss=scan.max_loss
        )
        
        db.add(position)
        db.commit()
        db.refresh(position)
        
        logger.info(f"Executed paper trade: {scan.strategy_type} {scan.symbol} "
                   f"x{quantity} @ {scan.net_credit:.2f} (ID: {position.id})")
        
        # Log to trade journal
        self._log_trade("OPEN", position)
    
    def _close_position(self, position: PaperPosition, db: Session, reason: str):
        """Close a paper position."""
        position.status = "closed"
        position.closed_at = datetime.utcnow()
        position.close_reason = reason
        
        logger.info(f"Closed position {position.id} ({position.strategy_type}) "
                   f"P&L: ${position.unrealized_pnl:.2f}")
    
    def _log_trade(self, action: str, position: PaperPosition):
        """Log trade to file for audit trail."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action,
            "position_id": position.id,
            "symbol": position.symbol,
            "strategy": position.strategy_type,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "status": position.status
        }
        
        log_file = "/tmp/options_detective/logs/trade_journal.jsonl"
        import json, os
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    
    def get_positions(self, db: Session) -> List[PaperPosition]:
        """Get all open positions."""
        return db.query(PaperPosition).filter(
            PaperPosition.status == "open"
        ).order_by(PaperPosition.created_at.desc()).all()
    
    def get_performance_summary(self, db: Session, days: int = 30) -> Dict[str, Any]:
        """
        Get performance summary for paper trading.
        
        Args:
            db: Database session
            days: Lookback period in days
            
        Returns:
            Dict with performance metrics
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        # Closed positions
        closed = db.query(PaperPosition).filter(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= cutoff
        ).all()
        
        # Open positions
        open_positions = db.query(PaperPosition).filter(
            PaperPosition.status == "open"
        ).all()
        
        # Calculate stats
        total_trades = len(closed)
        winning = [p for p in closed if p.total_pnl > 0]
        losing = [p for p in closed if p.total_pnl <= 0]
        
        win_rate = len(winning) / total_trades * 100 if total_trades else 0
        
        total_pnl = sum(p.total_pnl for p in closed)
        avg_pnl = total_pnl / total_trades if total_trades else 0
        
        unrealized = sum(p.unrealized_pnl for p in open_positions)
        
        # Average hold time
        hold_times = [(p.closed_at - p.created_at).days for p in closed if p.closed_at]
        avg_hold = np.mean(hold_times) if hold_times else 0
        
        # Best/worst trades
        pnls = [p.total_pnl for p in closed]
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0
        
        return {
            "period_days": days,
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "average_pnl": round(avg_pnl, 2),
            "avg_hold_days": round(avg_hold, 1),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "open_positions": len(open_positions),
            "daily_avg_pnl": round(total_pnl / days, 2) if days > 0 else 0
        }
