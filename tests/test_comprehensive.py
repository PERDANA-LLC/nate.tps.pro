"""
Comprehensive test suite for Options Detective.
Run with: pytest tests/ -v --cov=src --cov-report=html
"""
import pytest
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.greeks import calculate_greeks, probability_of_profit, calculate_iv_rank
from src.strategies import StrategyScanner, OptionLeg, Strategy
from src.backtester import Backtester, BacktestResult, Trade
from src.paper_trader import PaperTrader
from src.models import Base, StrategyScan, PaperPosition

# Test database setup
TEST_DB_URL = "sqlite:///./test_options_detective.db"
engine = create_engine(TEST_DB_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module")
def db_session():
    """Create test database."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()
    # Cleanup
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_option_chain():
    """Generate sample option chain for SPY."""
    spot = 450.0
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    
    calls = [
        {
            "symbol": f"SPY_{expiry}_440_C",
            "strike": 440.0,
            "expiration": expiry,
            "bid": 11.50,
            "ask": 11.70,
            "volume": 1500,
            "open_interest": 8500,
            "delta": 0.65,
            "gamma": 0.02,
            "theta": -0.15,
            "vega": 0.30,
            "implied_volatility": 0.22
        },
        {
            "symbol": f"SPY_{expiry}_445_C",
            "strike": 445.0,
            "expiration": expiry,
            "bid": 8.40,
            "ask": 8.60,
            "volume": 2200,
            "open_interest": 12000,
            "delta": 0.58,
            "gamma": 0.025,
            "theta": -0.18,
            "vega": 0.32,
            "implied_volatility": 0.21
        },
        {
            "symbol": f"SPY_{expiry}_450_C",
            "strike": 450.0,
            "expiration": expiry,
            "bid": 5.80,
            "ask": 6.00,
            "volume": 3500,
            "open_interest": 18000,
            "delta": 0.50,
            "gamma": 0.03,
            "theta": -0.20,
            "vega": 0.35,
            "implied_volatility": 0.20
        },
        {
            "symbol": f"SPY_{expiry}_455_C",
            "strike": 455.0,
            "expiration": expiry,
            "bid": 3.90,
            "ask": 4.10,
            "volume": 2800,
            "open_interest": 9500,
            "delta": 0.42,
            "gamma": 0.028,
            "theta": -0.19,
            "vega": 0.33,
            "implied_volatility": 0.21
        }
    ]
    
    puts = [
        {
            "symbol": f"SPY_{expiry}_440_P",
            "strike": 440.0,
            "expiration": expiry,
            "bid": 3.40,
            "ask": 3.60,
            "volume": 1200,
            "open_interest": 7200,
            "delta": -0.35,
            "gamma": 0.02,
            "theta": -0.14,
            "vega": 0.28,
            "implied_volatility": 0.22
        },
        {
            "symbol": f"SPY_{expiry}_445_P",
            "strike": 445.0,
            "expiration": expiry,
            "bid": 5.00,
            "ask": 5.20,
            "volume": 1800,
            "open_interest": 11000,
            "delta": -0.42,
            "gamma": 0.025,
            "theta": -0.17,
            "vega": 0.31,
            "implied_volatility": 0.21
        },
        {
            "symbol": f"SPY_{expiry}_450_P",
            "strike": 450.0,
            "expiration": expiry,
            "bid": 7.10,
            "ask": 7.30,
            "volume": 2500,
            "open_interest": 15000,
            "delta": -0.50,
            "gamma": 0.03,
            "theta": -0.20,
            "vega": 0.35,
            "implied_volatility": 0.20
        },
        {
            "symbol": f"SPY_{expiry}_455_P",
            "strike": 455.0,
            "expiration": expiry,
            "bid": 9.80,
            "ask": 10.00,
            "volume": 2100,
            "open_interest": 8900,
            "delta": -0.58,
            "gamma": 0.028,
            "theta": -0.19,
            "vega": 0.33,
            "implied_volatility": 0.21
        }
    ]
    
    return {
        "calls": calls,
        "puts": puts,
        "spot_price": spot,
        "expiration": expiry
    }


# ========== Greeks Tests ==========

class TestGreeks:
    """Tests for options Greeks calculations."""
    
    def test_call_greeks_atm(self):
        """Test ATM call greeks."""
        result = calculate_greeks(
            spot_price=100,
            strike_price=100,
            days_to_expiry=30,
            risk_free_rate=0.05,
            option_type='call',
            implied_volatility=0.25
        )
        
        assert result['price'] > 0
        assert 0 < result['delta'] < 1
        assert result['gamma'] >= 0
        assert result['theta'] < 0  # Theta decay
        assert result['vega'] > 0
    
    def test_put_greeks_atm(self):
        """Test ATM put greeks."""
        result = calculate_greeks(
            spot_price=100,
            strike_price=100,
            days_to_expiry=30,
            risk_free_rate=0.05,
            option_type='put',
            implied_volatility=0.25
        )
        
        assert result['price'] > 0
        assert -1 < result['delta'] < 0
        assert result['gamma'] >= 0
    
    def test_greeks_at_expiry(self):
        """Test greeks at expiration (theta/vega/gamma should be ~0)."""
        result = calculate_greeks(
            spot_price=100,
            strike_price=100,
            days_to_expiry=0,
            risk_free_rate=0.05,
            option_type='call',
            implied_volatility=0.25
        )
        
        # At expiry, only intrinsic value remains
        assert result['price'] == 0  # ATM call expires worthless
        assert result['delta'] in [0, 1]  # Either 0 or 1
        
    def test_greeks_itm_call(self):
        """Test deep ITM call delta approaches 1."""
        result = calculate_greeks(
            spot_price=150,
            strike_price=100,
            days_to_expiry=30,
            risk_free_rate=0.05,
            option_type='call',
            implied_volatility=0.25
        )
        
        assert result['delta'] > 0.9
    
    def test_greeks_otm_put(self):
        """Test deep OTM put delta approaches 0 (negative but small magnitude)."""
        result = calculate_greeks(
            spot_price=100,
            strike_price=150,
            days_to_expiry=30,
            risk_free_rate=0.05,
            option_type='put',
            implied_volatility=0.25
        )
        
        assert result['delta'] > -0.1  # Close to 0
    
    def test_probability_of_profit_iron_condor(self):
        """Test POP for Iron Condor."""
        pop = probability_of_profit(0.2, 'call', 'Iron Condor')
        assert 70 <= pop <= 85
    
    def test_probability_of_profit_covered_call(self):
        """Test POP for Covered Call."""
        pop = probability_of_profit(0.3, 'call', 'Covered Call')
        assert 65 <= pop <= 80
    
    def test_probability_of_profit_csp(self):
        """Test POP for Cash-Secured Put."""
        pop = probability_of_profit(-0.2, 'put', 'Cash-Secured Put')
        assert 75 <= pop <= 90
    
    def test_iv_rank_calculation(self):
        """Test IV rank calculation."""
        historical = [0.15, 0.18, 0.20, 0.22, 0.25, 0.30, 0.35, 0.40]
        current = 0.28
        rank = calculate_iv_rank(current, historical)
        # 0.28 is between 0.25 (idx 4) and 0.30 (idx 5) out of 8 values
        # 4/8 = 50% base + a bit
        assert 50 <= rank <= 70


# ========== Strategy Scanner Tests ==========

class TestStrategyScanner:
    """Tests for strategy scanning logic."""
    
    def test_scanner_initialization(self):
        """Test scanner can be created with default parameters."""
        scanner = StrategyScanner()
        assert scanner.min_probability == 50.0
        assert scanner.min_volume == 100
        assert scanner.min_iv_rank == 30.0
    
    def test_iron_condor_found(self, sample_option_chain):
        """Test that Iron Condor opportunities are found."""
        scanner = StrategyScanner(min_probability=50, min_volume=100, min_iv_rank=30)
        strategies = scanner.scan_symbol("SPY", sample_option_chain, 450.0)
        
        assert len(strategies) > 0
        types_found = [s.strategy_type for s in strategies]
        assert "Iron Condor" in types_found
    
    def test_covered_call_found(self, sample_option_chain):
        """Test Covered Call scanning."""
        scanner = StrategyScanner(min_probability=40, min_volume=100, min_iv_rank=20)
        strategies = scanner.scan_symbol("SPY", sample_option_chain, 450.0)
        
        types_found = [s.strategy_type for s in strategies]
        assert "Covered Call" in types_found
    
    def test_csp_found(self, sample_option_chain):
        """Test Cash-Secured Put scanning."""
        scanner = StrategyScanner(min_probability=60, min_volume=100, min_iv_rank=20)
        strategies = scanner.scan_symbol("SPY", sample_option_chain, 450.0)
        
        types_found = [s.strategy_type for s in strategies]
        assert "Cash-Secured Put" in types_found
    
    def test_strategy_scoring_logic(self, sample_option_chain):
        """Test that strategies are properly scored."""
        scanner = StrategyScanner()
        strategies = scanner.scan_symbol("SPY", sample_option_chain, 450.0)
        
        if strategies:
            best = strategies[0]
            assert best.score > 0
            assert 0 <= best.probability <= 100
            assert best.max_profit > 0
            assert best.max_loss > 0
            # Credit strategies: net credit > 0
            if best.strategy_type != "Covered Call":  # CC also net credit
                assert best.net_credit > 0


# ========== Backtester Tests ==========

class TestBacktester:
    """Tests for backtesting engine."""
    
    def test_backtester_initialization(self):
        """Test backtester creation."""
        bt = Backtester(initial_balance=50000)
        assert bt.initial_balance == 50000
        assert bt.commission == 0.65
    
    def test_mock_data_generation(self):
        """Test historical data generation."""
        bt = Backtester()
        start = datetime.now() - timedelta(days=100)
        end = datetime.now()
        
        df = bt.load_historical_data("SPY", start, end)
        
        assert len(df) > 50
        assert 'Close' in df.columns
        assert 'Open' in df.columns
        assert 'High' in df.columns
        assert 'Low' in df.columns
    
    def test_option_chain_simulation(self):
        """Test option chain generation."""
        bt = Backtester()
        chain = bt.simulate_option_chain(
            spot_price=450,
            days_to_expiry=30,
            iv_rank=50
        )
        
        assert 'calls' in chain
        assert 'puts' in chain
        assert len(chain['calls']) > 0
        assert len(chain['puts']) > 0
        
        # Check structure
        call = chain['calls'][0]
        assert 'strike' in call
        assert 'bid' in call
        assert 'ask' in call
        assert 'delta' in call
        assert call['bid'] <= call['ask']
    
    def test_backtest_run(self, sample_option_chain):
        """Test full backtest execution."""
        import pandas as pd
        
        bt = Backtester(initial_balance=10000)
        
        # Create mock historical data
        dates = pd.date_range(start='2024-01-01', periods=100, freq='B')
        data = pd.DataFrame({
            'Open': np.random.uniform(440, 460, 100),
            'High': np.random.uniform(440, 460, 100),
            'Low': np.random.uniform(440, 460, 100),
            'Close': np.linspace(445, 455, 100),  # Trending up
            'Volume': np.random.randint(1000000, 5000000, 100)
        }, index=dates)
        
        scanner = StrategyScanner(min_probability=60)
        result = bt.backtest_strategy(scanner, data, scan_interval=5)
        
        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0
        assert result.win_rate >= 0
        assert result.win_rate <= 100
    
    def test_metric_calculations(self):
        """Test performance metric edge cases."""
        bt = Backtester()
        
        # Create simple trades
        trades = [
            Trade(entry_date=datetime.now(), exit_date=datetime.now(),
                  symbol="SPY", strategy_type="IC", entry_price=1.0, exit_price=None,
                  quantity=1, pnl=100),
            Trade(entry_date=datetime.now(), exit_date=datetime.now(),
                  symbol="SPY", strategy_type="IC", entry_price=1.0, exit_price=None,
                  quantity=1, pnl=-50),
        ]
        
        equity_curve = [10000, 10100, 10050]
        perf = bt._calculate_metrics(trades, 10000, equity_curve, None)
        
        assert perf.total_trades == 2
        assert 30 <= perf.win_rate <= 70  # 1 win, 1 loss = 50%
        assert perf.total_pnl == 50
    
    def test_grid_search(self):
        """Test parameter grid search."""
        import pandas as pd
        
        bt = Backtester(initial_balance=10000)
        
        data = pd.DataFrame({
            'Close': np.random.normal(100, 5, 100),
            'Open': np.random.normal(100, 5, 100),
            'High': np.random.normal(105, 5, 100),
            'Low': np.random.normal(95, 5, 100),
            'Volume': np.random.randint(1000000, 5000000, 100)
        }, index=pd.date_range('2024-01-01', periods=100))
        
        param_grid = {
            'min_probability': [60, 70],
            'min_iv_rank': [40, 50]
        }
        
        best_params, best_result = bt.optimize_parameters_grid(
            StrategyScanner,
            data,
            param_grid,
            metric='total_pnl'
        )
        
        assert isinstance(best_params, dict)
        assert 'min_probability' in best_params
        assert best_result is not None


# ========== PaperTrader Tests ==========

class TestPaperTrader:
    """Tests for paper trading engine."""
    
    def test_paper_trader_initialization(self):
        """Test paper trader creation."""
        trader = PaperTrader(max_positions=3)
        assert trader.max_positions == 3
        assert trader.max_position_size == 0.05
    
    def test_position_lifecycle(self, db_session):
        """Test opening and closing a paper position."""
        trader = PaperTrader()
        
        # Simulate creating a paper position directly
        from src.models import PaperPosition
        from datetime import datetime
        
        pos = PaperPosition(
            symbol="SPY",
            strategy_type="Iron Condor",
            entry_price=2.15,
            quantity=1,
            legs_json='[{"strike": 440, "action": "sell"}, {"strike": 445, "action": "buy"}]',
            status="open"
        )
        db_session.add(pos)
        db_session.commit()
        
        assert pos.id is not None
        assert pos.status == "open"
        
        # Close
        pos.status = "closed"
        pos.closed_at = datetime.utcnow()
        pos.realized_pnl = 150.0
        db_session.commit()
        
        assert pos.status == "closed"
        assert pos.total_pnl == 150.0
    
    def test_performance_summary(self, db_session):
        """Test performance summary calculation."""
        trader = PaperTrader()
        
        # Add closed trades
        for i in range(5):
            pos = PaperPosition(
                symbol="SPY",
                strategy_type="IC",
                entry_price=2.0,
                quantity=1,
                unrealized_pnl=0.0,
                realized_pnl=100.0 if i < 3 else -50.0,
                status="closed",
                created_at=datetime.utcnow() - timedelta(days=i)
            )
            db_session.add(pos)
        
        # Add open position
        open_pos = PaperPosition(
            symbol="QQQ",
            strategy_type="CC",
            entry_price=1.50,
            quantity=1,
            unrealized_pnl=75.0,
            status="open"
        )
        db_session.add(open_pos)
        db_session.commit()
        
        summary = trader.get_performance_summary(db_session, days=30)
        
        assert summary['total_trades'] == 5
        assert summary['winning_trades'] == 3
        assert summary['losing_trades'] == 2
        assert summary['win_rate'] == 60.0
        assert summary['total_pnl'] == 250.0  # 3*100 - 2*50
        assert summary['open_positions'] == 1


# ========== Integration Tests ==========

class TestIntegration:
    """End-to-end integration tests."""
    
    def test_full_scan_pipeline(self, db_session):
        """Test scanning → trade → backtest pipeline."""
        from src.main import generate_mock_option_chain
        from src.strategies import StrategyScanner
        
        # 1. Generate data & scan
        chain = generate_mock_option_chain("SPY", 30)
        scanner = StrategyScanner()
        strategies = scanner.scan_symbol("SPY", chain, 450.0)
        
        assert len(strategies) > 0
        
        # 2. Save top scan to DB
        top = strategies[0]
        record = StrategyScan(
            symbol=top.symbol,
            strategy_type=top.strategy_type,
            expiration=top.expiration,
            days_to_expiry=top.days_to_expiry,
            max_profit=top.max_profit,
            max_loss=top.max_loss,
            net_credit=top.net_credit,
            probability=top.probability,
            iv_rank=top.iv_rank,
            score=top.score,
            legs_json='[]'
        )
        db_session.add(record)
        db_session.commit()
        
        assert record.id is not None
        
        # 3. Create paper trade from scan
        pos = PaperPosition(
            symbol=record.symbol,
            strategy_type=record.strategy_type,
            entry_price=record.net_credit,
            quantity=1,
            legs_json=record.legs_json,
            status="open"
        )
        db_session.add(pos)
        db_session.commit()
        
        assert pos.id is not None
    
    def test_backtest_and_optimization_cycle(self):
        """Test that backtest produces optimizable parameters."""
        import pandas as pd
        
        bt = Backtester(initial_balance=100000)
        
        # Generate synthetic price series with known characteristics
        np.random.seed(12345)
        dates = pd.date_range('2023-01-01', periods=252*2, freq='B')  # 2 years
        
        # Mean-reverting process (good for IC)
        returns = np.random.normal(0, 0.015, len(dates))
        price = 450 * np.exp(np.cumsum(returns))
        
        data = pd.DataFrame({
            'Close': price,
            'Open': price * (1 + np.random.uniform(-0.005, 0.005, len(dates))),
            'High': price * (1 + np.random.uniform(0, 0.01, len(dates))),
            'Low': price * (1 - np.random.uniform(0, 0.01, len(dates))),
            'Volume': np.random.randint(2000000, 8000000, len(dates))
        }, index=dates)
        
        scanner = StrategyScanner(min_probability=60)
        
        # Run backtest
        result = bt.backtest_strategy(scanner, data, scan_interval=5)
        
        assert result.total_trades > 0
        assert -20000 <= result.total_pnl <= 50000  # Reasonable bounds
        assert -50 <= result.sharpe_ratio <= 5.0
    
    def test_walk_forward_analysis(self):
        """Test walk-forward optimization."""
        import pandas as pd
        
        bt = Backtester(initial_balance=10000)
        scanner = StrategyScanner()
        
        # 3 years data
        dates = pd.date_range('2022-01-01', periods=252*3, freq='B')
        data = pd.DataFrame({
            'Close': np.random.normal(100, 2, len(dates)).cumsum() + 100,
            'Open': np.random.normal(100, 2, len(dates)),
            'High': np.random.normal(105, 2, len(dates)),
            'Low': np.random.normal(95, 2, len(dates)),
            'Volume': np.random.randint(1000000, 5000000, len(dates))
        }, index=dates)
        
        wfa = bt.walk_forward_analysis(
            strategy=scanner,
            historical_data=data,
            train_days=252,
            test_days=63
        )
        
        assert 'periods' in wfa
        assert len(wfa['periods']) >= 4  # At least 4 out-of-sample periods


# ========== Regression Tests ==========

class TestRegression:
    """Regression tests to catch regressions."""
    
    def test_greeks_consistency(self):
        """Call and put greeks sum to certain relationships."""
        gs = calculate_greeks(100, 100, 30, 0.05, 'call', 0.25)
        gp = calculate_greeks(100, 100, 30, 0.05, 'put', 0.25)
        
        # Put-Call parity approximation
        assert abs(gs['delta'] - (1 + gp['delta'])) < 0.01
    
    def test_scanner_deterministic(self, sample_option_chain):
        """Scanner should produce consistent results with same data."""
        scanner1 = StrategyScanner(min_probability=65)
        scanner2 = StrategyScanner(min_probability=65)
        
        results1 = scanner1.scan_symbol("SPY", sample_option_chain, 450.0)
        results2 = scanner2.scan_symbol("SPY", sample_option_chain, 450.0)
        
        # Should be same (though order might vary if sorted by same score)
        assert len(results1) == len(results2)
        if results1:
            assert results1[0].strategy_type == results2[0].strategy_type


# ========== Performance Tests ==========

class TestPerformance:
    """Performance/load tests."""
    
    def test_backtest_speed(self):
        """Ensure backtest runs in reasonable time."""
        import time
        import pandas as pd
        
        bt = Backtester()
        
        # 1 year of data
        dates = pd.date_range('2023-01-01', periods=252, freq='B')
        data = pd.DataFrame({
            'Close': np.random.normal(100, 1, 252).cumsum() + 100,
            'Open': np.random.normal(100, 1, 252),
            'High': np.random.normal(105, 1, 252),
            'Low': np.random.normal(95, 1, 252),
            'Volume': np.random.randint(1000000, 5000000, 252)
        }, index=dates)
        
        start = time.time()
        bt.backtest_strategy(StrategyScanner(), data)
        duration = time.time() - start
        
        assert duration < 10.0  # Should complete in under 10 seconds


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
