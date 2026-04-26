#!/usr/bin/env python3
"""
Test Runner for Options Detective
Runs all test suites with proper setup and reporting.

Usage:
  python run_tests.py              # Run all tests
  python run_tests.py --fast       # Quick smoke tests only
  python run_tests.py --coverage   # With coverage report
  python run_tests.py --watch      # Watch mode (auto-rerun on changes)
  python run_tests.py --parallel   # Run tests in parallel (xdist)
"""
import subprocess
import sys
import argparse
from pathlib import Path
import time

PROJECT_ROOT = Path(__file__).parent
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"

# Test files
TEST_FILES = [
    "tests/test_greeks.py",
    "tests/test_comprehensive.py",
]

# Markers for test categorization
MARKERS = {
    "unit": "Unit tests (fast, isolated)",
    "integration": "Integration tests (slow, multi-component)",
    "slow": "Slow tests (> 1s)",
    "backtest": "Backtest-related tests",
    "performance": "Performance/benchmark tests",
}


def run_command(cmd, capture=True):
    """Run shell command and return exit code."""
    print(f"\n{'='*60}")
    print(f"$ {' '.join(cmd)}")
    print('='*60)
    
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
    else:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Options Detective Test Runner")
    parser.add_argument("--fast", action="store_true", help="Run only fast tests (smoke)")
    parser.add_argument("--coverage", action="store_true", help="Generate coverage report")
    parser.add_argument("--parallel", "-n", type=int, default=0, help="Run N workers in parallel")
    parser.add_argument("--watch", action="store_true", help="Watch mode - rerun on file changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--marker", "-m", default="", help="Only run tests with marker")
    parser.add_argument("--failfast", "-x", action="store_true", help="Stop at first failure")
    parser.add_argument("--pdb", action="store_true", help="Drop into debugger on failure")
    parser.add_argument("--output", "-o", default="test-results", help="JUnit XML output dir")
    args = parser.parse_args()
    
    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]
    
    # Add verbosity
    if args.verbose:
        cmd.append("-vv")
    else:
        cmd.append("-v")
    
    # Add markers
    if args.marker:
        cmd.extend(["-m", args.marker])
    elif args.fast:
        cmd.extend(["-m", "not slow and not performance and not integration"])
    
    # Parallel execution
    if args.parallel:
        cmd.extend(["-n", str(args.parallel)])
    
    # PDB on failure
    if args.pdb:
        cmd.append("--pdb")
    
    # Fail fast
    if args.failfast:
        cmd.append("-x")
    
    # Coverage
    if args.coverage:
        cmd.extend([
            "--cov=src",
            "--cov-report=html",
            "--cov-report=term-missing",
            "--cov-report=xml",
        ])
    
    # JUnit output
    cmd.extend([f"--junitxml={args.output}/results.xml", "--junit-prefix=test"])
    
    # Add test files or directory
    if args.fast:
        cmd.append("tests/test_greeks.py")
    else:
        cmd.extend(TEST_FILES)
    
    # Add extra args at end
    cmd.append("--tb=short")
    
    # Run tests
    start = time.time()
    
    if args.watch:
        print("[WATCH MODE] Press Ctrl+C to stop. Watching for changes...")
        # Simple watch implementation
        last_mtime = 0
        try:
            while True:
                # Get latest file mtime
                files = list(TESTS_DIR.glob("*.py")) + list(SRC_DIR.glob("*.py"))
                current_mtime = max(f.stat().st_mtime for f in files)
                
                if current_mtime > last_mtime:
                    last_mtime = current_mtime
                    print(f"\n[{time.strftime('%H:%M:%S')}] Change detected, running tests...")
                    
                    exit_code = run_command(cmd, capture=False)
                    
                    if exit_code == 0:
                        print("\n✓ All tests passed")
                    else:
                        print(f"\n✗ Tests failed with code {exit_code}")
                    
                    print("\nWaiting for changes...")
                
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n[WATCH] Stopped")
            return 0
    else:
        # Single run
        exit_code = run_command(cmd, capture=False)
    
    elapsed = time.time() - start
    
    print(f"\n{'='*60}")
    print(f"Test run completed in {elapsed:.1f}s")
    print(f"Exit code: {exit_code}")
    
    if args.coverage:
        print("\nCoverage report: file://" + str(PROJECT_ROOT / "htmlcov" / "index.html"))
    
    # Generate summary
    if exit_code == 0:
        print("\n✓ All tests passed successfully")
    else:
        print("\n✗ Some tests failed")
    
    return exit_code


def print_test_info():
    """Print available test suites."""
    print("Available Test Suites:")
    print("  Unit tests:        pytest tests/test_greeks.py")
    print("  Strategy tests:    pytest tests/test_comprehensive.py::TestStrategyScanner")
    print("  Backtester tests:  pytest tests/test_comprehensive.py::TestBacktester")
    print("  PaperTrader tests: pytest tests/test_comprehensive.py::TestPaperTrader")
    print("  Integration tests: pytest tests/test_comprehensive.py::TestIntegration")
    print("  Regression tests:  pytest tests/test_comprehensive.py::TestRegression")
    print("  Performance tests: pytest tests/test_comprehensive.py::TestPerformance")
    print("\nQuick run: python run_tests.py --fast")
    print("Full suite: python run_tests.py --coverage")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "info"]:
        print_test_info()
    else:
        sys.exit(main())
