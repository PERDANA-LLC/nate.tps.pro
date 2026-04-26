"""
Scheduler for Continuous Scanning & Optimization
Runs periodic scans, backtests, and strategy optimization.
"""
import asyncio
from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
import logging
import json

from .database import SessionLocal, create_tables
from .models import StrategyScan, BacktestResult, OptimizationResult
from .strategies import StrategyScanner
from .backtester import Backtester, BacktestResult as BTResult
from .paper_trader import PaperTrader
from .config import settings

logger = logging.getLogger(__name__)


class ContinuousOptimizer:
    """
    Continuous optimization engine that:
    1. Runs strategy scans during market hours
    2. Backtests parameter optimizations nightly
    3. Updates strategy parameters based on performance
    4. Sends alerts for significant changes
    """
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.paper_trader = None
        self.backtester = None
        self.running = False
        
        logger.info("ContinuousOptimizer initialized")
    
    def start(self):
        """Start all scheduled tasks."""
        if self.running:
            logger.warning("Already running")
            return
        
        logger.info("Starting ContinuousOptimizer...")
        
        # Initialize components
        from .strategies import StrategyScanner
        self.scanner = StrategyScanner(
            min_probability=60,
            min_volume=100,
            min_iv_rank=40
        )
        
        self.backtester = Backtester(initial_balance=settings.initial_balance)
        self.paper_trader = PaperTrader()
        self.paper_trader.start()
        
        # Schedule jobs
        
        # 1. Pre-market scan (4:30 AM ET)
        self.scheduler.add_job(
            self.pre_market_scan,
            CronTrigger(hour=4, minute=30, timezone='America/New_York'),
            id='pre_market-scan',
            replace_existing=True
        )
        
        # 2. Market open scan (9:35 AM)
        self.scheduler.add_job(
            self.market_open_scan,
            CronTrigger(hour=9, minute=35, timezone='America/New_York'),
            id='market_open-scan',
            replace_existing=True
        )
        
        # 3. Mid-day scan (12:30 PM)
        self.scheduler.add_job(
            self.midday_scan,
            CronTrigger(hour=12, minute=30, timezone='America/New_York'),
            id='midday-scan',
            replace_existing=True
        )
        
        # 4. Pre-close scan (3:30 PM)
        self.scheduler.add_job(
            self.pre_close_scan,
            CronTrigger(hour=15, minute=30, timezone='America/New_York'),
            id='pre_close-scan',
            replace_existing=True
        )
        
        # 5. Nightly optimization (8:00 PM)
        self.scheduler.add_job(
            self.nightly_optimization,
            CronTrigger(hour=20, minute=0, timezone='America/New_York'),
            id='nightly-optimization',
            replace_existing=True
        )
        
        # 6. Weekly deep backtest (Sunday 10 PM)
        self.scheduler.add_job(
            self.weekly_backtest,
            CronTrigger(day_of_week='sun', hour=22, timezone='America/New_York'),
            id='weekly-backtest',
            replace_existing=True
        )
        
        # 7. Health check (every 5 min)
        self.scheduler.add_job(
            self.health_check,
            'interval',
            minutes=5,
            id='health-check',
            replace_existing=True
        )
        
        # 8. Daily performance report (after market close)
        self.scheduler.add_job(
            self.daily_report,
            CronTrigger(hour=16, minute=15, timezone='America/New_York'),
            id='daily-report',
            replace_existing=True
        )
        
        self.scheduler.start()
        self.running = True
        
        logger.info("All jobs scheduled successfully")
        
        # Run initial scan
        self.market_open_scan()
    
    def stop(self):
        """Stop all scheduled tasks."""
        self.running = False
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        if self.paper_trader:
            self.paper_trader.stop()
        logger.info("ContinuousOptimizer stopped")
    
    # ========== Scheduled Jobs ==========
    
    def pre_market_scan(self):
        """Early morning scan - identify overnight opportunities."""
        logger.info("Running pre-market scan...")
        try:
            # Scan major indices
            symbols = ["SPY", "QQQ", "IWM", "DIA"]
            for symbol in symbols:
                self._run_scan(symbol, expiration_days=45)
            
            logger.info("Pre-market scan complete")
        except Exception as e:
            logger.error(f"Pre-market scan failed: {e}")
    
    def market_open_scan(self):
        """Market open scan - primary scan for the day."""
        logger.info("Running market open scan...")
        try:
            for symbol in ["SPY", "QQQ"]:
                self._run_scan(symbol, expiration_days=30)
                self._run_scan(symbol, expiration_days=45)
            
            # Run paper trader scan
            self.paper_trader._scan_for_trades(SessionLocal())
            
            logger.info("Market open scan complete")
        except Exception as e:
            logger.error(f"Market open scan failed: {e}")
    
    def midday_scan(self):
        """Mid-day scan for intraday adjustments."""
        logger.info("Running midday scan...")
        # Check existing positions, adjust exits if needed
        # Look for new high-conviction setups
        pass
    
    def pre_close_scan(self):
        """Pre-close scan - final scan of the day."""
        logger.info("Running pre-close scan...")
        self.paper_trader._check_exits(SessionLocal())
    
    def nightly_optimization(self):
        """Nightly parameter optimization using recent data."""
        logger.info("Running nightly optimization...")
        try:
            db = SessionLocal()
            
            # Load recent backtest data
            recent_results = db.query(BacktestResult).order_by(
                BacktestResult.created_at.desc()
            ).limit(10).all()
            
            if recent_results:
                # Find best performing parameter sets
                best_params = self._extract_best_params(recent_results)
                
                # Update scanner configuration
                self._update_scanner_params(best_params)
                
                logger.info(f"Optimization complete. Updated params: {best_params}")
            
            db.close()
            
        except Exception as e:
            logger.error(f"Nightly optimization failed: {e}")
    
    def weekly_backtest(self):
        """Weekly comprehensive backtest with full parameter sweep."""
        logger.info("Running weekly backtest...")
        try:
            import yfinance as yf
            
            # Load 1 year of data
            end = datetime.now()
            start = end - timedelta(days=365)
            
            spy_data = yf.download("SPY", start=start, end=end)
            
            if spy_data.empty:
                logger.warning("No historical data available for backtest")
                return
            
            # Parameter grid
            param_grid = {
                "min_probability": [55, 60, 65, 70],
                "min_iv_rank": [30, 40, 50, 60],
                "risk_free_rate": [0.04, 0.05, 0.06]
            }
            
            # Grid search
            best_params, best_result = self.backtester.optimize_parameters_grid(
                strategy_class=StrategyScanner,
                historical_data=spy_data,
                param_grid=param_grid,
                metric="sharpe_ratio"
            )
            
            # Save results
            db = SessionLocal()
            backtest = BacktestResult(
                strategy_name="Weekly_Optimization",
                parameters=json.dumps(best_params),
                total_trades=best_result.total_trades,
                winning_trades=best_result.winning_trades,
                losing_trades=best_result.losing_trades,
                win_rate=best_result.win_rate,
                total_pnl=best_result.total_pnl,
                sharpe_ratio=best_result.sharpe_ratio,
                max_drawdown=best_result.max_drawdown,
                created_at=datetime.utcnow()
            )
            db.add(backtest)
            db.commit()
            db.close()
            
            logger.info(f"Weekly backtest complete. Sharpe: {best_result.sharpe_ratio:.2f}")
            
        except Exception as e:
            logger.error(f"Weekly backtest failed: {e}")
    
    def daily_report(self):
        """Generate daily performance report."""
        logger.info("Generating daily report...")
        try:
            db = SessionLocal()
            
            summary = self.paper_trader.get_performance_summary(db, days=1)
            
            logger.info(f"Daily P&L: ${summary['total_pnl']:.2f} "
                       f"({summary['win_rate']:.1f}% win rate, "
                       f"{summary['total_trades']} trades)")
            
            # Send alerts if thresholds hit
            if summary['total_pnl'] < -200:
                self._send_alert(f"⚠ Daily loss threshold: ${summary['total_pnl']:.2f}")
            
            db.close()
            
        except Exception as e:
            logger.error(f"Daily report failed: {e}")
    
    def health_check(self):
        """Health check every 5 minutes."""
        try:
            db = SessionLocal()
            
            # Check database
            db.execute("SELECT 1").fetchall()
            
            # Check paper trader
            open_count = db.query(PaperPosition).filter(
                PaperPosition.status == "open"
            ).count()
            
            logger.debug(f"Health OK - {open_count} open positions")
            
            db.close()
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            # Could trigger alert/restart
    
    # ========== Helper Methods ==========
    
    def _run_scan(self, symbol: str, expiration_days: int = 30):
        """Run a strategy scan and save results."""
        try:
            # Import here to avoid circular
            from .main import generate_mock_option_chain
            
            chain = generate_mock_option_chain(symbol, expiration_days)
            spot_price = chain['spot_price']
            
            strategies = self.scanner.scan_symbol(symbol, chain, spot_price)
            
            db = SessionLocal()
            for strat in strategies[:5]:  # Save top 5
                record = StrategyScan(
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
                    legs_json=json.dumps(serialize_legs(strat.legs))
                )
                db.add(record)
            
            db.commit()
            db.close()
            
            logger.info(f"Scan {symbol} {expiration_days}D: {len(strategies)} strategies found")
            
        except Exception as e:
            logger.error(f"Scan failed for {symbol}: {e}")
    
    def _extract_best_params(self, results: List[BacktestResult]) -> Dict[str, Any]:
        """Extract best parameters from backtest history."""
        # Sort by Sharpe
        sorted_results = sorted(results, key=lambda x: x.sharpe_ratio, reverse=True)
        best = sorted_results[0] if sorted_results else None
        
        if best and hasattr(best, 'parameters'):
            return json.loads(best.parameters)
        
        return {
            "min_probability": 60,
            "min_iv_rank": 50,
            "max_position_size": 0.05
        }
    
    def _update_scanner_params(self, params: Dict[str, Any]):
        """Update scanner configuration based on optimization."""
        self.scanner.min_probability = params.get("min_probability", 60)
        self.scanner.min_iv_rank = params.get("min_iv_rank", 50)
        logger.info(f"Scanner params updated: {params}")
    
    def _send_alert(self, message: str):
        """Send alert via Discord webhook or other channels."""
        webhook_url = settings.discord_webhook_url
        if webhook_url:
            try:
                import requests
                payload = {"content": message}
                requests.post(webhook_url, json=payload, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")


# Global instance
optimizer = ContinuousOptimizer()
