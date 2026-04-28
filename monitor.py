#!/usr/bin/env python3
"""
monitor.py — Playwright-based trade alert monitor for Daily Profits Live trades channel.

Usage:
    python monitor.py --env-file /path/to/.env
    python monitor.py [--headless|--headed] [--interval SECONDS]

    Env vars (or load from .env):
      DPL_USER_LOGIN_EMAIL / DPL_EMAIL    — account email
      DPL_USER_PASSWORD  / DPL_PASSWORD   — account password
      DPL_URL            — target trades page URL (default: ?channel=trades)
      DPL_MASTER_LOGIN_PAGE              — auth entrypoint (default: auth.mtacommandcenter.com)

Architecture:
    1. Launches headless Chromium via Playwright
    2. Signs into watch.dailyprofitslive.com (MTA Command Center auth)
    3. Saves browser state (cookies/localStorage) to avoid re-login
    4. Sets up DOM mutation observer on the trades channel page
    5. Prints new trade alerts to stdout in real time
    6. On restart, reuses saved state; re-authenticates only if expired
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame

# ---------------------------------------------------------------------------
# Env loading (optional dotenv)
# ---------------------------------------------------------------------------

def _load_env(path: str | None = None):
    """Try python-dotenv; graceful fallback if not installed."""
    if path:
        try:
            from dotenv import load_dotenv
            load_dotenv(path, override=True)
            return True
        except ImportError:
            pass
    return False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOGIN_URL = os.environ.get("DPL_MASTER_LOGIN_PAGE",
                           "https://watch.dailyprofitslive.com/login")
TRADES_URL = os.environ.get("DPL_URL",
                            "https://watch.dailyprofitslive.com/?channel=trades")
STATE_FILE = Path(__file__).parent / ".dpl_state.json"

EMAIL = os.environ.get("DPL_USER_LOGIN_EMAIL",
                        os.environ.get("DPL_EMAIL", ""))
PASSWORD = os.environ.get("DPL_USER_PASSWORD",
                           os.environ.get("DPL_PASSWORD", ""))

DEFAULT_INTERVAL = 5  # seconds between polls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitor] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

async def load_state(context: BrowserContext) -> bool:
    """Try to load saved browser state. Returns True if loaded successfully."""
    if not STATE_FILE.exists():
        return False
    try:
        state = json.loads(STATE_FILE.read_text())
        cookies = state.get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)
            log.info("Loaded saved cookies — verifying session…")
            return True
    except Exception:
        pass
    return False


async def save_state(context: BrowserContext):
    """Save browser cookies to disk for session reuse."""
    try:
        cookies = await context.cookies()
        STATE_FILE.write_text(json.dumps({"cookies": cookies, "saved_at": datetime.now(timezone.utc).isoformat()}))
        log.info("Session state saved.")
    except Exception as e:
        log.warning(f"Failed to save state: {e}")


async def is_authenticated(page: Page) -> bool:
    """Return True if the current page is not redirecting to login."""
    await asyncio.sleep(1)
    url = page.url
    # After auth, we should be on the trades page, not login
    if "/login" in url or "auth.mtacommandcenter" in url:
        return False
    return True


async def do_login(page: Page) -> bool:
    """
    1. Navigate directly to DPL_MASTER_LOGIN_PAGE
    2. Fill credentials, submit
    3. Wait for auth to complete
    Returns True on success.
    """
    if not EMAIL or not PASSWORD:
        log.error("DPL_USER_LOGIN_EMAIL and DPL_USER_PASSWORD env vars required for login.")
        return False

    log.info("Navigating to MTA login page…")
    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    # Fill credentials
    log.info("Filling credentials…")
    email_input = page.locator("#email")
    password_input = page.locator("#password")

    try:
        await email_input.wait_for(state="visible", timeout=10000)
    except Exception:
        log.error("Email input not visible. Current URL: %s", page.url)
        return False

    await email_input.fill(EMAIL)
    await asyncio.sleep(0.3)
    await password_input.fill(PASSWORD)
    await asyncio.sleep(0.5)

    # Submit
    submit_btn = page.locator("button:has-text('Sign In')")
    await submit_btn.click()

    # Wait for redirect
    await asyncio.sleep(5)
    final_url = page.url
    log.info("Post-login URL: %s", final_url)

    if "login" not in final_url.lower():
        log.info("Login successful.")
        return True

    log.warning("Login may have failed. Current URL: %s", final_url)
    return False


async def authenticate(context: BrowserContext) -> bool:
    """1. Try saved state → 2. Fresh login via DPL_MASTER_LOGIN_PAGE → 3. goto DPL_URL"""
    page = await context.new_page()

    # Try saved state first
    if await load_state(context):
        await page.goto(TRADES_URL, wait_until="networkidle", timeout=30000)
        if await is_authenticated(page):
            log.info("Session valid — monitoring trades directly.")
            return True
        log.info("Session expired — re-authenticating…")
        await page.close()
        page = await context.new_page()

    # 1. Login via DPL_MASTER_LOGIN_PAGE
    if not await do_login(page):
        await page.close()
        return False

    # 2. Goto DPL_URL (trades channel)
    log.info("Navigating to DPL trades channel…")
    await page.goto(TRADES_URL, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)

    await save_state(context)
    return True


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------

async def scrape_alerts(page: Page) -> list[dict]:
    """
    Scrape trade alerts from the page. Adapt selectors to the actual DOM.

    Strategy: Try multiple common patterns. The trades page likely has
    a list of cards/rows with trade details (ticker, action, price, etc.).
    Returns list of alert dicts with at least 'id' (or hash) for dedup.
    """
    alerts = []

    # We don't know the exact DOM yet — probe on first run and adapt
    # Strategy 1: Look for message/chat bubbles (common for alerts)
    messages = await page.locator("[class*='message'], [class*='alert'], "
                                   "[class*='trade'], [class*='notification'], "
                                   "[class*='chat'], [class*='post'], "
                                   "[class*='card'], [class*='row'], "
                                   "[class*='entry'], [class*='item']").all()

    if not messages:
        # Strategy 2: Look for any text blocks that might be trades
        # Just log all visible text and let the caller decide
        body_text = await page.inner_text("body")
        # Simple heuristic: look for ticker-like patterns
        import re
        ticker_pattern = re.compile(r'\b[A-Z]{1,5}\b')
        for line in body_text.split('\n'):
            line = line.strip()
            if line and len(line) > 10:
                alerts.append({
                    "id": str(hash(line)),
                    "text": line,
                    "time": datetime.now(timezone.utc).isoformat()
                })

    for el in messages:
        try:
            text = (await el.inner_text()).strip()
            if not text or len(text) < 3:
                continue
            html = await el.inner_html()
            alert_id = str(hash(text + html[:100]))
            alerts.append({
                "id": alert_id,
                "text": text,
                "time": datetime.now(timezone.utc).isoformat(),
                "element": html[:300],
            })
        except Exception:
            continue

    return alerts


# ---------------------------------------------------------------------------
# Mutation Observer (JS-injected, real-time)
# ---------------------------------------------------------------------------

MUTATION_OBSERVER_JS = """
() => {
    if (window.__dplMonitorSetup) return 'already-setup';
    window.__dplMonitorSetup = true;
    window.__dplNewAlerts = [];

    const callback = (mutationsList) => {
        for (const mutation of mutationsList) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType === 1) { // Element node
                    const text = (node.innerText || node.textContent || '').trim();
                    if (text.length > 5) {
                        window.__dplNewAlerts.push({
                            text: text.slice(0,500),
                            html: node.outerHTML?.slice(0,800) || '',
                            time: new Date().toISOString()
                        });
                    }
                }
            }
        }
    };

    const observer = new MutationObserver(callback);
    observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: false,
        attributes: false
    });
    return 'setup-complete';
}
"""


async def flush_mutation_alerts(page: Page) -> list[dict]:
    """Retrieve and clear alerts collected by the JS mutation observer."""
    try:
        raw = await page.evaluate("""
            () => {
                const alerts = window.__dplNewAlerts || [];
                window.__dplNewAlerts = [];
                return alerts;
            }
        """)
        seen = set()
        deduped = []
        for a in raw:
            hid = str(hash(a.get("text", "")))
            if hid not in seen:
                seen.add(hid)
                deduped.append({"id": hid, "text": a.get("text", ""),
                                "time": a.get("time", ""), "element": a.get("html", "")})
        return deduped
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

class AlertMonitor:
    """Main monitor: persists connection, feeds new alerts via callback."""

    def __init__(self, interval: float = 5.0, headless: bool = True):
        self.interval = interval
        self.headless = headless
        self.seen_ids: set[str] = set()
        self.running = False
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def start(self, on_alert=None):
        """Start monitoring. `on_alert(alert_dict)` called for each new alert."""
        self.running = True

        async with async_playwright() as p:
            self._browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"),
            )

            # Authenticate
            ok = await authenticate(self._context)
            if not ok:
                log.error("Authentication failed. Exiting.")
                return

            # Page is already on trades channel from authenticate() — inject observer
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()


            # Inject mutation observer
            result = await page.evaluate(MUTATION_OBSERVER_JS)
            log.info("Mutation observer: %s", result)

            # Initial scrape for existing content
            existing = await scrape_alerts(page)
            for a in existing:
                self.seen_ids.add(a["id"])

            # Wait for the iframe/channel to load
            await asyncio.sleep(2)

            # Main loop
            log.info("Monitoring started. Press Ctrl+C to stop.")
            while self.running:
                try:
                    # Collect from mutation observer (real-time)
                    new = await flush_mutation_alerts(page)

                    # Fallback: periodic scrape
                    if not new:
                        current = await scrape_alerts(page)
                        new = [a for a in current if a["id"] not in self.seen_ids]

                    for alert in new:
                        if alert["id"] not in self.seen_ids:
                            self.seen_ids.add(alert["id"])
                            if on_alert:
                                on_alert(alert)
                            else:
                                self._print_alert(alert)

                    # Check if still on the right page (session may expire)
                    if "/login" in page.url:
                        log.warning("Session expired — re-authenticating…")
                        ok = await do_login(page)
                        if not ok:
                            break
                        await page.goto(TRADES_URL, wait_until="networkidle", timeout=30000)
                        await page.evaluate(MUTATION_OBSERVER_JS)

                    await asyncio.sleep(self.interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.error("Loop error: %s", e)
                    await asyncio.sleep(self.interval * 2)

            await self._context.close()

    def stop(self):
        self.running = False

    @staticmethod
    def _print_alert(alert: dict):
        """Default alert handler: print to stdout."""
        ts = alert.get("time", "")[:19]
        text = alert.get("text", "").replace("\n", " | ")
        log.info(f"🟢 NEW ALERT [{ts}] {text}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    import argparse
    ap = argparse.ArgumentParser(description="Daily Profits Live Trade Alert Monitor")
    ap.add_argument("--headless", action="store_true", default=True,
                    help="Run in headless mode (default)")
    ap.add_argument("--headed", action="store_true",
                    help="Run with visible browser")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"Poll interval in seconds (default {DEFAULT_INTERVAL})")
    ap.add_argument("--env-file", type=str, default=None,
                    help="Path to .env file with DPL_USER_LOGIN_EMAIL/DPL_USER_PASSWORD")
    ap.add_argument("--dump-state", action="store_true",
                    help="Dump saved state to stdout")
    ap.add_argument("--clear-state", action="store_true",
                    help="Clear saved browser state")
    return ap.parse_args()


async def amain():
    args = parse_args()

    # Load .env first — overrides any pre-existing env vars
    env_path = args.env_file
    if not env_path:
        # Auto-detect: look for .env in common locations
        candidates = [
            Path("/Volumes/181TB/Perdana-LLC/nate.tps.pro/.env"),
            Path.cwd() / ".env",
            Path(__file__).parent / ".env",
        ]
        for c in candidates:
            if c.exists():
                env_path = str(c)
                break
    if env_path:
        if _load_env(env_path):
            log.info("Loaded .env: %s", env_path)
            # Refresh globals after dotenv override
            global EMAIL, PASSWORD, TRADES_URL, LOGIN_URL
            EMAIL = os.environ.get("DPL_USER_LOGIN_EMAIL",
                                    os.environ.get("DPL_EMAIL", ""))
            PASSWORD = os.environ.get("DPL_USER_PASSWORD",
                                       os.environ.get("DPL_PASSWORD", ""))
            TRADES_URL = os.environ.get("DPL_URL", TRADES_URL)
            LOGIN_URL = os.environ.get("DPL_MASTER_LOGIN_PAGE", LOGIN_URL)
        else:
            log.warning("python-dotenv not installed — relying on existing env vars.")
    else:
        log.info("No .env file found — using existing env vars.")

    if args.clear_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print(f"✅ Cleared {STATE_FILE}")
        return

    if args.dump_state:
        if STATE_FILE.exists():
            print(STATE_FILE.read_text())
        else:
            print("No saved state.")
        return

    headless = not args.headed
    monitor = AlertMonitor(interval=args.interval, headless=headless)

    def _sig_handler():
        log.info("Shutting down…")
        monitor.stop()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            pass

    await monitor.start()


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
