#!/usr/bin/env python3
"""
watchlist_builder.py — Daily NYSE+NASDAQ scanner for Project Nate.

Builds the watchlist by scanning stocks universe for:
  - TREND: Upward_Trend == True
  - PATTERN: bull_flag == True OR pennant == True
  - SQUEEZE: squeeze_on == True (RED = squeeze ON)

Universe source: nasdaqtrader.com (public FTP directory)
Saves results to telegram_config.json watchlist.

Usage:
    python watchlist_builder.py               # full scan
    python watchlist_builder.py --dry-run     # scan 50 stocks, don't save
    python watchlist_builder.py --top 20      # save top 20 only
"""

import json, logging, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set

import pandas as pd
import requests

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "telegram_config.json")
UNIVERSE_CACHE = os.path.join(ROOT, ".universe_cache.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BUILDER] %(message)s")
log = logging.getLogger("watchlist_builder")

# ── Universe: download NYSE + NASDAQ symbols from nasdaqtrader.com ────────
NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL  = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Basic pre-filter: skip penny stocks, ETFs, warrants, etc.
MIN_PRICE = 5.0          # ignore stocks below $5
MIN_VOLUME = 500_000     # min 10-day avg volume
BLACKLIST_SUFFIX = {"W", "U", "V", "R"}  # warrants, units, rights
BLACKLIST_PREFIX = set()
MAX_SYMBOLS = 500        # soft cap — scan at most this many to keep runtime short

def _download_and_parse(url: str, exchange_label: str) -> Set[str]:
    """Download a pipe-delimited symbol file, return set of symbols."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Failed to download {exchange_label} list: {e}")
        return set()

    symbols = set()
    lines = resp.text.strip().split("\n")
    # NASDAQ format: Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
    # Skip header line containing "Symbol"
    for line in lines:
        if "Symbol" in line and "Security Name" in line:
            continue
        if "File Creation Time" in line:
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        sym = parts[0].strip()
        # Skip test issues
        if len(parts) > 3 and parts[3].strip() == "Y":
            continue
        # Basic symbol validation: 1-5 uppercase letters
        if not (1 <= len(sym) <= 5 and sym.isalpha() and sym.isascii()):
            continue
        # Blacklist suffixes (warrants, units, etc.)
        if len(sym) > 1 and sym[-1] in BLACKLIST_SUFFIX:
            continue
        symbols.add(sym)

    return symbols


def fetch_universe(force_download: bool = False) -> List[str]:
    """Return NYSE+NASDAQ symbols, using cache if <24h old."""
    now = datetime.now(timezone.utc)

    if not force_download and os.path.exists(UNIVERSE_CACHE):
        try:
            with open(UNIVERSE_CACHE) as f:
                cache = json.load(f)
            cache_time = datetime.fromisoformat(cache.get("timestamp", ""))
            if (now - cache_time) < timedelta(hours=24):
                symbols = cache.get("symbols", [])
                if symbols:
                    log.info(f"Using cached universe: {len(symbols)} symbols ({cache_time.isoformat()})")
                    return symbols
        except Exception:
            pass

    log.info("Downloading universe from nasdaqtrader.com…")
    nasdaq = _download_and_parse(NASDAQ_URL, "NASDAQ")
    other  = _download_and_parse(OTHER_URL, "NYSE/AMEX")
    all_symbols = sorted(nasdaq | other)

    cache = {"timestamp": now.isoformat(), "symbols": all_symbols, "count": len(all_symbols)}
    with open(UNIVERSE_CACHE, "w") as f:
        json.dump(cache, f)

    log.info(f"Universe: {len(nasdaq)} NASDAQ + {len(other)} NYSE/AMEX → {len(all_symbols)} total")
    return all_symbols


# ── Fast price/volume pre-filter via Schwab ───────────────────────────────
def _prefilter_by_price_volume(symbols: List[str], client, max_workers: int = 10) -> List[str]:
    """Use Schwab batch quotes to filter out penny stocks and low volume."""
    if not symbols:
        return []

    def _batch(syms: List[str]) -> List[str]:
        try:
            resp = client.quotes(symbols=syms)
            if not resp or not hasattr(resp, 'json'):
                return []
            data = resp.json()
        except Exception as e:
            log.warning(f"Quote batch failed for {len(syms)} symbols: {e}")
            return []

        passed = []
        for sym in syms:
            try:
                q_data = data.get(sym, {})
                quote = q_data.get("quote", q_data)
                price = float(quote.get("lastPrice", quote.get("closePrice", 0)) or 0)
                vol = int(quote.get("totalVolume", 0) or 0)
                if price >= MIN_PRICE and vol >= MIN_VOLUME:
                    passed.append(sym)
            except Exception:
                continue
        return passed

    # Schwab batch limit is ~300 symbols
    batch_size = 200
    results = []
    with ThreadPoolExecutor(max_workers=min(max_workers, 5)) as ex:
        futures = {}
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            f = ex.submit(_batch, batch)
            futures[f] = batch

        for f in as_completed(futures):
            try:
                results.extend(f.result())
            except Exception as e:
                log.error(f"Prefilter future error: {e}")

    log.info(f"Prefilter: {len(symbols)} → {len(results)} after price≥${MIN_PRICE} & vol≥{MIN_VOLUME//1000}k")
    return results


# ── Single-stock TPS scan wrapper ─────────────────────────────────────────
def _scan_one(symbol: str, client) -> Optional[dict]:
    """Run TPS_SCAN for one symbol, return latest row dict or None."""
    from tps_scan import TPS_SCAN

    try:
        df = TPS_SCAN(symbol, months=6, client=client)
        if df is None or df.empty:
            return None
        row = df.iloc[-1].to_dict()
        row["_symbol"] = symbol
        return row
    except Exception as e:
        log.debug(f"TPS_SCAN({symbol}) failed: {e}")
        return None


# ── Main watchlist builder ─────────────────────────────────────────────────
def build_watchlist(
    client=None,
    max_workers: int = 6,
    top_n: Optional[int] = None,
    dry_run: bool = False,
    force_universe: bool = False,
) -> List[str]:
    """
    Build the watchlist.

    Returns list of symbols sorted by KPI_SCORE descending.
    """
    from schwab_client import get_client as _gc
    if client is None:
        client = _gc()

    # 1. Fetch universe
    universe = fetch_universe(force_download=force_universe)

    if dry_run:
        # Only scan first 50 for testing
        universe = universe[:50]
    elif len(universe) > MAX_SYMBOLS:
        # Soft cap: prefer higher-volume stocks (they come first typically)
        universe = universe[:MAX_SYMBOLS]
        log.info(f"Capping universe at {MAX_SYMBOLS} symbols")

    log.info(f"Step 1/3: Prefiltering {len(universe)} symbols by price/volume…")
    candidates = _prefilter_by_price_volume(universe, client)

    if dry_run and len(candidates) > 50:
        candidates = candidates[:50]

    # 2. TPS_SCAN in parallel
    log.info(f"Step 2/3: TPS_SCAN on {len(candidates)} candidates (max_workers={max_workers})…")
    results = []
    start = time.monotonic()
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_scan_one, sym, client): sym for sym in candidates}
        for f in as_completed(futures):
            completed += 1
            try:
                row = f.result()
                if row is not None:
                    results.append(row)
            except Exception as e:
                log.debug(f"Future error for {futures[f]}: {e}")

            if completed % 50 == 0 or completed == len(candidates):
                elapsed = time.monotonic() - start
                rate = completed / elapsed if elapsed > 0 else 0
                log.info(f"  … {completed}/{len(candidates)} scanned ({rate:.1f}/s), {len(results)} valid")

    elapsed = time.monotonic() - start
    log.info(f"Scanned {len(candidates)} stocks in {elapsed:.0f}s → {len(results)} valid results")

    # 3. Filter by criteria
    log.info(f"Step 3/3: Applying filter criteria…")
    matches = []
    for row in results:
        sym = row.get("_symbol", "???")
        trend_up   = row.get("Upward_Trend", False)
        bull_flag  = row.get("bull_flag", False)
        pennant    = row.get("pennant", False)
        squeeze_on = row.get("squeeze_on", False)

        if bool(trend_up) and (bool(bull_flag) or bool(pennant)) and bool(squeeze_on):
            matches.append(row)

    # Sort by KPI_SCORE descending
    matches.sort(key=lambda r: r.get("KPI_SCORE", 0), reverse=True)

    log.info(f"Filter result: {len(results)} scanned → {len(matches)} matched")

    # Show summary
    for i, row in enumerate(matches[:15]):
        sym = row["_symbol"]
        kpi = row.get("KPI_SCORE", 0)
        price = row.get("close", 0)
        flag_str = "FLAG" if row.get("bull_flag") else "PENN"
        log.info(f"  #{i+1:2d} {sym:5s}  KPI={kpi}/9  ${price:.2f}  {flag_str}")

    if len(matches) > 15:
        log.info(f"  … and {len(matches)-15} more")

    # 4. Build watchlist
    watchlist = [r["_symbol"] for r in matches]
    if top_n and len(watchlist) > top_n:
        watchlist = watchlist[:top_n]

    # Save to config
    if not dry_run:
        _save_watchlist(watchlist)
        log.info(f"✅ Watchlist saved: {len(watchlist)} symbols → {CONFIG_PATH}")
    else:
        log.info(f"[DRY RUN] Would save {len(watchlist)} symbols (not saved)")

    return watchlist


def _save_watchlist(symbols: List[str]):
    """Update telegram_config.json watchlist, preserving other config keys."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            config = json.load(f)

    old_count = len(config.get("watchlist", []))
    config["watchlist"] = symbols
    config.setdefault("scan_interval_hours", 4)
    config.setdefault("alert_threshold", 5)
    config["_watchlist_built_at"] = datetime.now(timezone.utc).isoformat()
    config["_watchlist_size"] = len(symbols)

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    log.info(f"Config updated: watchlist {old_count} → {len(symbols)} symbols")


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build TPS watchlist from NYSE+NASDAQ")
    ap.add_argument("--dry-run", action="store_true", help="Scan 50 stocks only, don't save")
    ap.add_argument("--top", type=int, default=None, help="Limit watchlist to top N by KPI")
    ap.add_argument("--workers", type=int, default=6, help="Parallel workers (default 6)")
    ap.add_argument("--force-universe", action="store_true", help="Redownload universe")
    ap.add_argument("--max", type=int, default=MAX_SYMBOLS, help=f"Max symbols to scan (default {MAX_SYMBOLS})")
    args = ap.parse_args()

    # Override MAX_SYMBOLS for this run
    MAX_SYMBOLS = args.max

    print(f"=== TPS Watchlist Builder ===")
    print(f"   Dry run: {args.dry_run}")
    print(f"   Top N: {args.top}")
    print(f"   Workers: {args.workers}")
    print(f"   Max symbols: {MAX_SYMBOLS}")
    print()

    wl = build_watchlist(
        dry_run=args.dry_run,
        top_n=args.top,
        max_workers=args.workers,
        force_universe=args.force_universe,
    )

    print(f"\nFinal watchlist ({len(wl)} symbols):")
    for i, sym in enumerate(wl, 1):
        print(f"  {i:3d}. {sym}")
