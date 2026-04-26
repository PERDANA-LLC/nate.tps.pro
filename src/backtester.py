"""
Backtesting Engine for Options Detective
Tests strategies against historical data and calculates performance metrics.
"""
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
import json
import logging

from .greeks import calculate_greeks, probability_of_profit
from .models import StrategyScan, PaperPosition, Base

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single backtested or live trade."""
    entry_date: datetime
    exit_date: Optional[datetime]
    symbol: str
    strategy_type: str
    entry_price: float
    exit_price: Optional[float]
    quantity: int
    pnl: float = 0.0
    legs: List[Dict] = field(default_factory=list)
    status: str = "open"  # open, closed, cancelled
    max_drawdown: float = 0.0
    notes: str = ""


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    average_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_days_held: float
    best_trade: float
    worst_trade: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    benchmark_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "strategy_name": self.strategy_name,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 2),
            "total_pnl": round(self.total_pnl, 2),
            "average_pnl": round(self.average_pnl, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "profit_factor": round(self.profit_factor, 2),
            "avg_days_held": round(self.avg_days_held, 1),
            "best_trade": round(self.best_trade, 2),
            "worst_trade": round(self.worst_trade, 2),
            "benchmark_return": round(self.benchmark_return, 2),
            "alpha": round(self.alpha, 2),
            "beta": round(self.beta, 2),
        }


class Backtester:
    """
    Options strategy backtesting engine.
    
    Features:
    - Historical options data simulation
    - Multi-strategy backtesting
    - Performance metrics calculation
    - Benchmark comparison (SPY buy-and-hold)
    - Win/loss analysis
    """
    
    def __init__(
        self,
        initial_balance: float = 10000.0,
        commission_per_contract: float = 0.65,
        slippage: float = 0.05,
        risk_free_rate: float = 0.05
    ):
        """
        Initialize backtester.
        
        Args:
            initial_balance: Starting account balance
            commission_per_contract: Per-contract commission ($)
            slippage: Slippage as fraction of premium (0.05 = 5%)
            risk_free_rate: Annual risk-free rate for Sharpe calculation
        """
        self.initial_balance = initial_balance
        self.commission = commission_per_contract
        self.slippage = slippage
        self.risk_free_rate = risk_free_rate
        logger.info(f"Backtester initialized with ${initial_balance:,.2f} initial balance")
    
    def load_historical_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = "1d"
    ) -> pd.DataFrame:
        """
        Load historical price data for backtesting.
        
        Uses yfinance for historical data. Falls back to mock data if unavailable.
        """
        try:
            import yfinance as yf
            
            ticker = yf.Ticker(symbol)
            hist = ticker.history(
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d'),
                interval=interval
            )
            
            if hist.empty:
                raise ValueError(f"No data returned for {symbol}")
            
            logger.info(f"Loaded {len(hist)} days of {symbol} data")
            return hist
            
        except ImportError:
            logger.warning("yfinance not installed, using mock data")
            return self._generate_mock_data(symbol, start_date, end_date)
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
            return self._generate_mock_data(symbol, start_date, end_date)
    
    def _generate_mock_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> pd.DataFrame:
        """Generate synthetic price data for testing."""
        dates = pd.date_range(start=start_date, end=end_date, freq='B')
        n_days = len(dates)
        
        # Geometric Brownian Motion
        mu = 0.08  # 8% annual return
        sigma = 0.20  # 20% volatility
        dt = 1/252
        
        returns = np.random.normal(mu * dt, sigma * np.sqrt(dt), n_days)
        price = 100 * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame({
            'Open': price * (1 + np.random.uniform(-0.01, 0.01, n_days)),
            'High': price * (1 + np.random.uniform(0, 0.02, n_days)),
            'Low': price * (1 - np.random.uniform(0, 0.02, n_days)),
            'Close': price,
            'Volume': np.random.randint(1000000, 10000000, n_days)
        }, index=dates)
        
        logger.info(f"Generated {n_days} days of mock data for {symbol}")
        return df
    
    def simulate_option_chain(
        self,
        spot_price: float,
        days_to_expiry: int,
        iv_rank: float = 50.0,
        symbol: str = "SPY"
    ) -> Dict[str, List[Dict]]:
        """
        Simulate an option chain for a given spot price and DTE.
        
        This is a simplified model - real backtesting would need actual historical IV.
        """
        strikes = []
        step = 5 if spot_price > 100 else 2.5
        
        # Generate strikes from -$40 to +$40
        for i in range(-16, 17):
            strikes.append(round(spot_price + i * step, 2))
        
        calls = []
        puts = []
        
        # Time decay factor
        time_frac = max(days_to_expiry / 365.0, 1/365)
        
        for strike in strikes:
            moneyness = strike / spot_price
            
            # IV increases for OTM options
            iv_base = 0.20 + abs(1 - moneyness) * 0.30
            iv = iv_base * (0.5 + iv_rank / 100)
            
            # Delta calculation (approximation)
            if strike >= spot_price:
                # OTM/ATM call
                delta = max(0.01, np.exp(-(moneyness - 1)**2 / 0.1) * 0.5)
            else:
                # ITM call
                delta = min(0.99, 0.5 + (1 - moneyness) * 0.5)
            
            put_delta = delta - 1
            
            # Premium (Black-Scholes approximation)
            d1 = (np.log(spot_price / strike) + 0.05 * time_frac) / (iv * np.sqrt(time_frac))
            d2 = d1 - iv * np.sqrt(time_frac)
            
            from math import exp, sqrt, pi, log
            
            nd1 = (1 + np.erf(d1 / 1.414213562)) / 2
            nd2 = (1 + np.erf(d2 / 1.414213562)) / 2
            
            call_price = spot_price * nd1 - strike * exp(-0.05 * time_frac) * nd2
            put_price = strike * exp(-0.05 * time_frac) - spot_price * (1 - nd1)
            
            call_bid = round(call_price * 0.95, 2)
            call_ask = round(call_price * 1.05, 2)
            put_bid = round(put_price * 0.95, 2)
            put_ask = round(put_price * 1.05, 2)
            
            expiry = (datetime.now() + timedelta(days=days_to_expiry)).strftime('%Y-%m-%d')
            
            calls.append({
                "symbol": f"{symbol}_{expiry}_{strike}_C",
                "strike": strike,
                "expiration": expiry,
                "bid": call_bid,
                "ask": call_ask,
                "volume": max(100, int(np.random.uniform(500, 5000))),
                "open_interest": max(1000, int(np.random.uniform(10000, 50000))),
                "delta": round(delta, 4),
                "gamma": round(abs(delta) * 0.1, 6),
                "theta": round(-call_price * 0.1, 4),
                "vega": round(call_price * 0.5, 4),
                "implied_volatility": round(iv, 4)
            })
            
            puts.append({
                "symbol": f"{symbol}_{expiry}_{strike}_P",
                "strike": strike,
                "expiration": expiry,
                "bid": put_bid,
                "ask": put_ask,
                "volume": max(100, int(np.random.uniform(500, 5000))),
                "open_interest": max(1000, int(np.random.uniform(10000, 50000))),
                "delta": round(put_delta, 4),
                "gamma": round(abs(put_delta) * 0.1, 6),
                "theta": round(-put_price * 0.1, 4),
                "vega": round(put_price * 0.5, 4),
                "implied_volatility": round(iv, 4)
            })
        
        return {
            "calls": calls,
            "puts": puts,
            "spot_price": spot_price,
            "expiration": expiry,
            "timestamp": datetime.now()
        }
    
    def backtest_strategy(
        self,
        strategy,
        historical_data: pd.DataFrame,
        scan_interval: int = 5,  # days between scans
        holding_period: int = 21,  # days to hold position
        initial_balance: Optional[float] = None
    ) -> BacktestResult:
        """
        Backtest a strategy over historical data.
        
        Args:
            strategy: StrategyScanner instance with configured parameters
            historical_data: OHLCV DataFrame
            scan_interval: Days between scanning for new positions
            holding_period: Max days to hold a position
            initial_balance: Override default balance
            
        Returns:
            BacktestResult with performance metrics
        """
        balance = initial_balance or self.initial_balance
        trades: List[Trade] = []
        equity_curve = [balance]
        current_position: Optional[Trade] = None
        
        # Simulate IV rank over time (random walk)
        np.random.seed(42)
        iv_ranks = np.random.uniform(30, 80, len(historical_data))
        
        logger.info(f"Starting backtest: {len(historical_data)} days, ${balance:,.2f} initial")
        
        for i in range(0, len(historical_data), scan_interval):
            if i >= len(historical_data):
                break
            
            current_date = historical_data.index[i]
            spot_price = historical_data['Close'].iloc[i]
            
            # If we have an open position, check exit conditions
            if current_position:
                days_held = (current_date - current_position.entry_date).days
                
                # Exit conditions
                should_exit = False                exit_reason = ""
                
                # 1. Max holding period reached
                if days_held >= holding_period:
                    should_exit = True
                    exit_reason = "max_holding_period"
                
                # 2. Profit target hit (simulate with P&L)
                if current_position.pnl > 0 and current_position.pnl / balance > 0.02:
                    should_exit = True
                    exit_reason = "profit_target"
                
                # 3. Stop loss hit
                if current_position.pnl < -balance * 0.02:
                    should_exit = True
                    exit_reason = "stop_loss"
                
                if should_exit:
                    # Close position
                    current_position.exit_date = current_date
                    current_position.status = "closed"
                    current_position.notes = exit_reason
                    trades.append(current_position)
                    balance += current_position.pnl
                    current_position = None
            
            # If no position, look for new entry
            if not current_position and i < len(historical_data) - scan_interval:
                # Simulate option chain
                iv_rank = iv_ranks[i]
                chain = self.simulate_option_chain(
                    spot_price=spot_price,
                    days_to_expiry=30,
                    iv_rank=iv_rank * 100,
                    symbol=strategy.symbol if hasattr(strategy, 'symbol') else "SPY"
                )
                
                # Run strategy scan
                strategies = strategy.scan_symbol(
                    symbol="SPY",
                    option_chain=chain,
                    spot_price=spot_price
                )
                
                if strategies:
                    best = strategies[0]
                    
                    # Create trade
                    entry_cost = abs(best.net_credit) * 100 - self.commission * len(best.legs)
                    
                    trade = Trade(
                        entry_date=current_date,
                        exit_date=None,
                        symbol="SPY",
                        strategy_type=best.strategy_type,
                        entry_price=best.net_credit,
                        exit_price=None,
                        quantity=1,
                        pnl=0.0,
                        legs=[serialize_legs(l)[0] for l in best.legs],
                        status="open"
                    )
                    current_position = trade
                    
                    logger.debug(f"{current_date.date()}: Opened {best.strategy_type} @ {best.net_credit:.2f}")
        
        # Close any remaining position at end
        if current_position:
            current_position.exit_date = historical_data.index[-1]
            current_position.status = "closed"
            current_position.notes = "end_of_period"
            trades.append(current_position)
            balance += current_position.pnl
        
        # Calculate P&L for each trade
        # (In real backtest, would simulate option pricing changes)
        # For now, use a simplified Monte Carlo based on strategy stats
        self._calculate_trade_pnls(trades, strategy)
        
        # Build equity curve
        equity = initial_balance or self.initial_balance
        equity_curve = []
        for t in trades:
            equity += t.pnl
            equity_curve.append(equity)
        
        # Compute metrics
        result = self._calculate_metrics(
            trades=trades,
            initial_balance=initial_balance or self.initial_balance,
            equity_curve=equity_curve,
            benchmark_data=historical_data
        )
        
        logger.info(f"Backtest complete: {result.total_trades} trades, "
                   f"{result.win_rate:.1f}% win rate, ${result.total_pnl:,.2f} P&L")
        
        return result
    
    def _calculate_trade_pnls(self, trades: List[Trade], strategy):
        """Calculate P&L for each trade using simplified model."""
        for t in trades:
            # Base P&L on strategy's historical expectancy
            # For demo: random walk based on strategy edge
            base_pnl = abs(t.entry_price) * 100 * 0.5  # Assume half credit as average P&L
            volatility = abs(base_pnl) * 0.5
            
            # 60% win rate (typical for income strategies)
            if np.random.random() < 0.6:
                t.pnl = np.random.normal(abs(base_pnl), volatility)
            else:
                t.pnl = -np.random.normal(abs(base_pnl) * 1.5, volatility)
            
            # Subtract commissions
            t.pnl -= self.commission * len(t.legs)
    
    def _calculate_metrics(
        self,
        trades: List[Trade],
        initial_balance: float,
        equity_curve: List[float],
        benchmark_data: pd.DataFrame
    ) -> BacktestResult:
        """Calculate performance metrics from trade list."""
        if not trades:
            return BacktestResult(
                strategy_name="Unknown",
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                average_pnl=0.0,
                max_drawdown=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                profit_factor=0.0,
                avg_days_held=0.0,
                best_trade=0.0,
                worst_trade=0.0
            )
        
        # Basic stats
        pnls = [t.pnl for t in trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p <= 0]
        
        win_rate = len(winning) / len(trades) * 100
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / len(trades)
        
        # Best/worst
        best_trade = max(pnls) if pnls else 0
        worst_trade = min(pnls) if pnls else 0
        
        # Average days held
        days_held = [(t.exit_date - t.entry_date).days for t in trades]
        avg_days = np.mean(days_held) if days_held else 0
        
        # Max drawdown
        peak = initial_balance
        drawdowns = []
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            drawdowns.append(dd)
        max_dd = max(drawdowns) if drawdowns else 0
        
        # Sharpe ratio (annualized)
        if len(pnls) > 1:
            daily_returns = np.array(pnls) / initial_balance
            if daily_returns.std() > 0:
                sharpe = (daily_returns.mean() - self.risk_free_rate / 252) / daily_returns.std()
                sharpe = sharpe * np.sqrt(252)  # Annualize
            else:
                sharpe = 0.0
            
            # Sortino (downside deviation only)
            negative_returns = daily_returns[daily_returns < 0]
            if len(negative_returns) > 0 and negative_returns.std() > 0:
                sortino = (daily_returns.mean() - self.risk_free_rate / 252) / negative_returns.std()
                sortino = sortino * np.sqrt(252)
            else:
                sortino = 0.0
        else:
            sharpe = 0.0
            sortino = 0.0
        
        # Profit factor
        gross_profit = sum(winning)
        gross_loss = abs(sum(losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Benchmark comparison (SPY buy-and-hold)
        if len(benchmark_data) > 1:
            start_price = benchmark_data['Close'].iloc[0]
            end_price = benchmark_data['Close'].iloc[-1]
            benchmark_return = (end_price - start_price) / start_price * 100
            
            # Alpha/Beta (simplified - just relative performance)
            equity_return = (equity_curve[-1] - initial_balance) / initial_balance * 100
            alpha = equity_return - benchmark_return
            beta = 1.0  # Would need covariance calculation for real beta
        else:
            benchmark_return = 0.0
            alpha = 0.0
            beta = 0.0
        
        return BacktestResult(
            strategy_name=strategy.__class__.__name__,
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            total_pnl=total_pnl,
            average_pnl=avg_pnl,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            profit_factor=profit_factor,
            avg_days_held=avg_days,
            best_trade=best_trade,
            worst_trade=worst_trade,
            trades=trades,
            equity_curve=equity_curve,
            benchmark_return=benchmark_return,
            alpha=alpha,
            beta=beta
        )
    
    def optimize_parameters_grid(
        self,
        strategy_class,
        historical_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        metric: str = "sharpe_ratio"
    ) -> Tuple[Dict[str, Any], BacktestResult]:
        """
        Grid search parameter optimization.
        
        Args:
            strategy_class: StrategyScanner subclass to test
            historical_data: Historical OHLCV data
            param_grid: Dict of {param_name: [values]}
            metric: Metric to optimize
            
        Returns:
            (best_params, best_result)
        """
        from itertools import product
        
        best_score = -float('inf')
        best_params = None
        best_result = None
        
        # Generate all combinations
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        
        total_combos = np.prod([len(v) for v in values])
        logger.info(f"Grid search: {total_combos} parameter combinations")
        
        for i, combo in enumerate(product(*values)):
            params = dict(zip(keys, combo))
            
            # Create strategy instance with params
            strat = strategy_class(**params)
            
            # Run backtest
            result = self.backtest_strategy(strat, historical_data)
            
            # Score
            score = getattr(result, metric, 0.0)
            
            if score > best_score:
                best_score = score
                best_params = params
                best_result = result
                logger.debug(f"New best: {params} → {metric}={score:.2f}")
        
        logger.info(f"Optimization complete. Best {metric}: {best_score:.2f}")
        return best_params, best_result
    
    def walk_forward_analysis(
        self,
        strategy,
        historical_data: pd.DataFrame,
        train_days: int = 252,  # 1 year
        test_days: int = 63,  # 3 months
        metric: str = "sharpe_ratio"
    ) -> Dict[str, Any]:
        """
        Walk-forward optimization: train on rolling window, test on out-of-sample.
        
        Args:
            strategy: Strategy instance
            historical_data: Full historical dataset
            train_days: Days of data for training
            test_days: Days to test after training
            
        Returns:
            Dict with walk-forward results
        """
        results = []
        equity_curve = []
        current_balance = self.initial_balance
        
        logger.info(f"Walk-forward: {train_days}d train, {test_days}d test")
        
        for start in range(0, len(historical_data) - train_days - test_days, test_days):
            train_end = start + train_days
            test_end = train_end + test_days
            
            train_data = historical_data.iloc[start:train_end]
            test_data = historical_data.iloc[train_end:test_end]
            
            # Optimize on train set
            _, best_train = self.backtest_strategy(strategy, train_data)
            
            # Test on out-of-sample
            test_result = self.backtest_strategy(strategy, test_data)
            
            results.append({
                "train_start": historical_data.index[start],
                "test_start": historical_data.index[train_end],
                "train_metric": getattr(best_train, metric),
                "test_metric": getattr(test_result, metric),
                "test_pnl": test_result.total_pnl
            })
            
            current_balance += test_result.total_pnl
            equity_curve.append(current_balance)
        
        # Aggregate results
        total_pnl = current_balance - self.initial_balance
        avg_test_metric = np.mean([r["test_metric"] for r in results])
        
        return {
            "periods": results,
            "final_balance": current_balance,
            "total_pnl": total_pnl,
            "avg_test_metric": avg_test_metric,
            "equity_curve": equity_curve
        }
