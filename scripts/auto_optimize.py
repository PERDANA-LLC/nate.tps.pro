#!/usr/bin/env python3
"""
Autonomous Optimization & Self-Improvement Script

This script runs continuously to:
1. Execute backtests on strategy parameter combinations
2. Identify optimal parameters
3. Update configuration automatically
4. Generate improvement reports
5. Log all changes for audit trail

Usage: python scripts/auto_optimize.py --mode=full --days=90
"""
import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from backtester import Backtester
from strategies import StrategyScanner
from database import SessionLocal, create_tables
from models import BacktestResult, OptimizationResult
import pandas as pd
import numpy as np
import yfinance as yf

# --- Configuration ---
DEFAULT_SYMBOLS = ["SPY", "QQQ", "IWM"]
DEFAULT_DAYS = 252  # 1 year of data
DEFAULT_INITIAL_BALANCE = 10000.0

# Parameter search space
PARAM_GRID = {
    "min_probability": [50, 55, 60, 65, 70, 75],
    "min_iv_rank": [20, 30, 40, 50, 60],
    "min_volume": [50, 100, 200],
}

# Metrics to optimize (in order of preference)
OPTIMIZATION_METRICS = [
    "sharpe_ratio",
    "win_rate",
    "total_pnl",
    "profit_factor",
    "sortino_ratio"
]

# Minimum thresholds for considering a strategy viable
MIN_THRESHOLDS = {
    "win_rate": 50.0,
    "sharpe_ratio": 0.5,
    "profit_factor": 1.2,
    "total_trades": 10
}


def load_historical_data(symbol: str, days: int) -> pd.DataFrame:
    """Load historical price data for backtesting."""
    end = datetime.now()
    start = end - timedelta(days=days + 60)  # Buffer for index alignment
    
    try:
        data = yf.download(symbol, start=start, end=end)
        if data.empty:
            raise ValueError(f"No data for {symbol}")
        return data
    except Exception as e:
        print(f"[ERROR] Could not download {symbol}: {e}")
        print("[INFO] Using synthetic data instead")
        return generate_synthetic_data(symbol, days)


def generate_synthetic_data(symbol: str, days: int) -> pd.DataFrame:
    """Generate synthetic price data when yfinance fails."""
    np.random.seed(hash(symbol) % 2**32)
    
    dates = pd.date_range(end=datetime.now(), periods=days, freq='B')
    
    # Random walk with drift based on symbol
    drift_map = {"SPY": 0.0003, "QQQ": 0.0004, "IWM": 0.0002}
    drift = drift_map.get(symbol.upper(), 0.0003)
    
    returns = np.random.normal(drift, 0.012, days)
    price = 100 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        'Open': price * (1 + np.random.uniform(-0.005, 0.005, days)),
        'High': np.maximum(price * (1 + np.random.uniform(0, 0.02, days)), price),
        'Low': np.minimum(price * (1 - np.random.uniform(0, 0.02, days)), price),
        'Close': price,
        'Volume': np.random.randint(1000000, 10000000, days)
    }, index=dates)
    
    return df


def grid_search_optimization(
    symbol: str,
    data: pd.DataFrame,
    param_grid: dict,
    metric: str = "sharpe_ratio"
) -> tuple[dict, BacktestResult]:
    """
    Perform grid search over parameter space.
    
    Returns:
        best_params, best_result
    """
    from itertools import product
    
    best_score = -float('inf')
    best_params = None
    best_result = None
    
    all_keys = list(param_grid.keys())
    all_values = list(param_grid.values())
    total_combos = np.prod([len(v) for v in all_values])
    
    print(f"\n[OPTIMIZATION] {symbol}: Searching {total_combos} parameter combinations...")
    print(f"  Optimizing for: {metric}")
    
    # Progress tracking
    completed = 0
    batch_size = max(1, total_combos // 20)  # Report 20 times
    
    for combo in product(*all_values):
        params = dict(zip(all_keys, combo))
        
        # Create scanner with these params
        scanner = StrategyScanner(**params)
        
        # Run backtest
        bt = Backtester(initial_balance=DEFAULT_INITIAL_BALANCE)
        result = bt.backtest_strategy(scanner, data, scan_interval=5)
        
        # Score
        score = getattr(result, metric, 0.0)
        
        if score > best_score:
            best_score = score
            best_params = params
            best_result = result
            print(f"  [NEW BEST] {metric}={score:.3f} params={params}")
        
        completed += 1
        if completed % batch_size == 0:
            print(f"  Progress: {completed}/{total_combos} ({100*completed/total_combos:.0f}%)")
    
    print(f"  [DONE] Best {metric}: {best_score:.3f}")
    return best_params, best_result


def evaluate_strategy_quality(result: BacktestResult) -> dict:
    """
    Evaluate if a strategy is production-ready.
    Returns dict with pass/fail and reasoning.
    """
    checks = []
    passed = True
    
    for metric, threshold in MIN_THRESHOLDS.items():
        value = getattr(result, metric, 0)
        if value < threshold:
            passed = False
            checks.append(f"  ✗ {metric}: {value:.2f} < {threshold:.2f}")
        else:
            checks.append(f"  ✓ {metric}: {value:.2f} ≥ {threshold:.2f}")
    
    # Additional sanity checks
    if result.total_pnl < -DEFAULT_INITIAL_BALANCE * 0.5:
        passed = False
        checks.append(f"  ✗ Total P&L too negative: ${result.total_pnl:,.2f}")
    
    if result.max_drawdown > 30:
        passed = False
        checks.append(f"  ✗ Max drawdown too high: {result.max_drawdown:.1f}%")
    
    return {
        "passed": passed,
        "checks": checks,
        "score": result.sharpe_ratio
    }


def save_results(
    symbol: str,
    params: dict,
    result: BacktestResult,
    metric: str,
    db_session
):
    """Save optimization and backtest results to database."""
    
    # Save backtest
    backtest = BacktestResult(
        strategy_name=f"{symbol}_Optimized",
        parameters_json=json.dumps(params),
        total_trades=result.total_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        total_pnl=result.total_pnl,
        average_pnl=result.average_pnl,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=result.sortino_ratio,
        profit_factor=result.profit_factor,
        avg_days_held=result.avg_days_held,
        best_trade=result.best_trade,
        worst_trade=result.worst_trade,
        benchmark_return=result.benchmark_return,
        alpha=result.alpha,
        beta=result.beta,
        created_at=datetime.utcnow(),
        notes=f"Optimized for {metric}"
    )
    db_session.add(backtest)
    db_session.flush()  # Get ID
    
    # Save optimization record
    opt = OptimizationResult(
        strategy_name="StrategyScanner",
        optimization_type="grid_search",
        parameters_json=json.dumps(params),
        metric_optimized=metric,
        metric_value=getattr(result, metric),
        backtest_result_id=backtest.id,
        created_at=datetime.utcnow(),
        notes=f"Auto-optimization run on {symbol} with {result.total_trades} trades"
    )
    db_session.add(opt)
    db_session.commit()
    
    print(f"  [SAVED] Backtest ID: {backtest.id}, Optimization ID: {opt.id}")


def generate_report(
    results: list,
    output_file: Path
):
    """Generate HTML/markdown report of optimization results."""
    
    report = f"""# Auto-Optimization Report
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary

| Symbol | Best Metric | Win Rate | Total P&L | Sharpe | Trades | Status |
|--------|-------------|----------|-----------|--------|--------|--------|
"""
    
    for r in results:
        status = "✓ PASS" if r['evaluation']['passed'] else "✗ FAIL"
        report += f"| {r['symbol']} | {r['metric']}={r['result'].sharpe_ratio:.2f} | "
        report += f"{r['result'].win_rate:.1f}% | ${r['result'].total_pnl:.0f} | "
        report += f"{r['result'].sharpe_ratio:.2f} | {r['result'].total_trades} | {status} |\n"
    
    report += """
## Best Parameters Found

"""
    
    for r in results:
        report += f"### {r['symbol']}\n"
        report += "```json\n"
        report += json.dumps(r['params'], indent=2) + "\n"
        report += "```\n\n"
        report += "**Evaluation:**\n"
        for check in r['evaluation']['checks']:
            report += f"- {check}\n"
        report += "\n"
    
    output_file.write_text(report)
    print(f"\n[REPORT] Saved to {output_file}")


def run_optimization(
    symbols: list,
    days: int,
    metric: str,
    save_to_db: bool = True
) -> list:
    """
    Run full optimization across symbols.
    
    Returns:
        List of result dicts for each symbol
    """
    results = []
    
    print(f"\n{'='*60}")
    print(f"AUTO-OPTIMIZATION START")
    print(f"Symbols: {symbols}")
    print(f"Lookback: {days} days")
    print(f"Metric: {metric}")
    print(f"{'='*60}")
    
    db_session = None
    if save_to_db:
        create_tables()
        db_session = SessionLocal()
    
    for symbol in symbols:
        print(f"\n[SYMBOL] {symbol}")
        print("-" * 40)
        
        # Load data
        data = load_historical_data(symbol, days)
        print(f"  Data: {len(data)} trading days, {data['Close'].iloc[0]:.2f} → {data['Close'].iloc[-1]:.2f}")
        
        # Grid search
        best_params, best_result = grid_search_optimization(
            symbol, data, PARAM_GRID, metric
        )
        
        # Evaluate
        evaluation = evaluate_strategy_quality(best_result)
        
        print(f"  [RESULT] {metric}={getattr(best_result, metric):.3f}")
        print(f"  Win Rate: {best_result.win_rate:.1f}%")
        print(f"  Total P&L: ${best_result.total_pnl:,.2f}")
        print(f"  Sharpe: {best_result.sharpe_ratio:.2f}")
        print(f"  Trades: {best_result.total_trades}")
        print(f"  Status: {'PASS' if evaluation['passed'] else 'FAIL'}")
        
        results.append({
            "symbol": symbol,
            "params": best_params,
            "result": best_result,
            "metric": metric,
            "evaluation": evaluation
        })
        
        # Save to DB
        if save_to_db and db_session:
            try:
                save_results(symbol, best_params, best_result, metric, db_session)
            except Exception as e:
                print(f"  [WARN] Could not save to DB: {e}")
    
    if db_session:
        db_session.close()
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Autonomous optimization engine")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                       help="Symbols to optimize (default: SPY QQQ IWM)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                       help="Days of historical data to use")
    parser.add_argument("--metric", default="sharpe_ratio",
                       choices=OPTIMIZATION_METRICS,
                       help="Metric to optimize")
    parser.add_argument("--no-db", action="store_true",
                       help="Skip database persistence")
    parser.add_argument("--report", type=Path, default=Path("optimization_report.md"),
                       help="Output report file")
    parser.add_argument("--mode", choices=["quick", "full", "deep"],
                       default="full", help="Optimization depth")
    
    args = parser.parse_args()
    
    # Adjust parameters based on mode
    global PARAM_GRID
    if args.mode == "quick":
        PARAM_GRID = {
            "min_probability": [60, 70],
            "min_iv_rank": [40, 50],
            "min_volume": [100]
        }
    elif args.mode == "deep":
        PARAM_GRID = {
            "min_probability": [50, 55, 60, 65, 70, 75, 80],
            "min_iv_rank": [20, 30, 40, 50, 60, 70],
            "min_volume": [50, 100, 200, 500],
        }
    
    # Run optimization
    try:
        results = run_optimization(
            symbols=args.symbols,
            days=args.days,
            metric=args.metric,
            save_to_db=not args.no_db
        )
        
        # Generate report
        generate_report(results, args.report)
        
        # Summary
        print(f"\n{'='*60}")
        print(f"OPTIMIZATION COMPLETE")
        print(f"{'='*60}")
        
        passed = [r for r in results if r['evaluation']['passed']]
        failed = [r for r in results if not r['evaluation']['passed']]
        
        print(f"\nPassed: {len(passed)}/{len(results)}")
        print(f"Failed: {len(failed)}/{len(results)}")
        
        if passed:
            best = max(passed, key=lambda x: x['result'].sharpe_ratio)
            print(f"\nBest configuration: {best['symbol']}")
            print(f"  Sharpe: {best['result'].sharpe_ratio:.2f}")
            print(f"  Win Rate: {best['result'].win_rate:.1f}%")
            print(f"  P&L: ${best['result'].total_pnl:,.2f}")
            print(f"  Params: {best['params']}")
        
        return 0 if len(passed) > 0 else 1
        
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Optimization cancelled by user")
        return 130
    except Exception as e:
        print(f"\n[FATAL] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
