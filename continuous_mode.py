#!/usr/bin/env python3
"""
Continuous Mode Daemon - Autonomous Options Detective

Runs continuously to:
1. Scan markets during trading hours
2. Execute paper trades automatically
3. Backtest strategies nightly
4. Optimize parameters
5. Send alerts on significant events
6. Maintain performance logs

Run with: python continuous_mode.py --daemon

Or integrate into main FastAPI app with:
  from scheduler import optimizer
  optimizer.start()
"""
import asyncio
import signal
import sys
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.scheduler import ContinuousOptimizer
from src.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/options_detective/logs/continuous.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ContinuousDaemon:
    """Main daemon for continuous operation."""
    
    def __init__(self):
        self.optimizer = None
        self.running = False
        self.shutdown_event = asyncio.Event()
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.shutdown_event.set()
    
    async def run(self):
        """Main daemon loop."""
        logger.info("Starting Options Detective Continuous Mode")
        logger.info(f"Paper trading: {settings.paper_trading_mode}")
        logger.info(f"Initial balance: ${settings.initial_balance:,.2f}")
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        try:
            # Start optimizer (includes paper trader)
            self.optimizer = ContinuousOptimizer()
            self.optimizer.start()
            
            logger.info("All systems initialized. Entering main loop...")
            logger.info("Scheduled jobs:")
            logger.info("  04:30 AM  - Pre-market scan")
            logger.info("  09:35 AM  - Market open scan")
            logger.info("  12:30 PM  - Midday scan")
            logger.info("  03:30 PM  - Pre-close scan")
            logger.info("  08:00 PM  - Nightly optimization")
            logger.info("  10:00 PM  - Weekly backtest (Sun)")
            logger.info("  Every 5m   - Health check")
            
            # Wait for shutdown signal
            while not self.shutdown_event.is_set():
                await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Fatal error in daemon: {e}", exc_info=True)
            raise
        finally:
            logger.info("Shutting down...")
            if self.optimizer:
                self.optimizer.stop()
            logger.info("Shutdown complete")


def main():
    """Entry point for daemon mode."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Options Detective Continuous Mode")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon (background)")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show schedule without starting")
    parser.add_argument("--log-level", default="INFO", 
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Logging level")
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Create logs directory
    Path('/tmp/options_detective/logs').mkdir(exist_ok=True)
    
    if args.dry_run:
        # Just show what would run
        from src.scheduler import ContinuousOptimizer
        opt = ContinuousOptimizer()
        print("\nScheduled jobs:")
        for job in opt.scheduler.get_jobs():
            print(f"  {job.next_run_time.strftime('%H:%M %Z')} - {job.id}")
        return
    
    daemon = ContinuousDaemon()
    
    if args.once:
        # Run one iteration (primarily for testing)
        logger.info("Single run mode")
        # Could trigger one scan here
    else:
        # Run asyncio loop
        try:
            asyncio.run(daemon.run())
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Daemon crashed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
