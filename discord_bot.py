
#!/usr/bin/env python3
"""
Discord bot for Project Nate — TPS Scanner.
Slash-command driven with scheduled watchlist alerts.
Shares telegram_config.json for watchlist/interval/alert_threshold.

Usage:
    source .venv/bin/activate
    python discord_bot.py
"""

import asyncio, json, logging, os, sys
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "telegram_config.json")
load_dotenv(os.path.join(ROOT, ".env"))

from schwab_client import get_client
from broker_interface import get_broker

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
TARGET_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DISCORD] %(message)s")
log = logging.getLogger("discord")

# ── Config helpers ────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"watchlist": ["SPY", "QQQ"], "scan_interval_hours": 4, "alert_threshold": 5}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Format scan result ────────────────────────────────────────────────────
def format_scan(symbol: str, row) -> str:
    """Format the latest row of a TPS_SCAN DataFrame into a Discord embed-friendly text."""
    lines = [f"**{symbol}**  —  ${row.get('close', 0):.2f}"]
    
    kpi = row.get("KPI_SCORE", 0)
    perfect = row.get("KPI_PERFECT", False)
    score_tag = "🏆 PERFECT" if perfect else f"⚡ {kpi}/9"
    lines.append(f"**KPI:** {score_tag}")
    
    trend = row.get("Upward_Trend", False)
    down = row.get("Downward_Trend", False)
    if trend:
        lines.append(f"📈 TREND: Upward  (EMA_8={row.get('EMA_8',0):.2f} > EMA_21={row.get('EMA_21',0):.2f} > EMA_55={row.get('EMA_55',0):.2f})")
    elif down:
        lines.append(f"📉 TREND: Downward")
    else:
        lines.append("↔️ TREND: Choppy")
    
    # Pattern
    flag = row.get("bull_flag", False)
    pennant = row.get("pennant", False)
    if flag:
        lines.append(f"🚩 PATTERN: Bull Flag  (r²={row.get('r2_flag',0):.2f}, slope={row.get('slope_flag_reg',0):.4f})")
    elif pennant:
        lines.append(f"📐 PATTERN: Pennant")
    else:
        lines.append("○ PATTERN: None")
    
    # Squeeze
    squeeze_on = row.get("squeeze_on", False)
    fired = row.get("squeeze_fired", False)
    on_narrow = row.get("on_narrow", False)
    if fired:
        lines.append("💥 SQUEEZE: FIRED")
    elif squeeze_on and on_narrow:
        lines.append("🟡 SQUEEZE: On (narrow)")
    elif squeeze_on:
        lines.append("🔸 SQUEEZE: On")
    else:
        lines.append("○ SQUEEZE: Off")
    
    # VWAP & volume
    vwap = row.get("vwap", 0)
    vol_ratio = row.get("vol_ratio", 1.0)
    if vwap:
        lines.append(f"VWAP: ${vwap:.2f}  |  Vol ratio: {vol_ratio:.1f}x")
    
    return "\n".join(lines)


async def run_tps_scan(symbol: str) -> Optional[dict]:
    """Run TPS_SCAN in a thread, return the latest row as a dict."""
    from tps_scan import TPS_SCAN
    
    def _block():
        try:
            df = TPS_SCAN(symbol, client=get_client())
            if df is None or df.empty:
                return None
            return df.iloc[-1].to_dict()
        except Exception as e:
            log.error(f"TPS_SCAN({symbol}) error: {e}")
            return None
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _block)


# ── Bot setup ─────────────────────────────────────────────────────────────
class TPSDiscordBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.config = load_config()
        self.alert_channel = None  # set on ready
    
    async def setup_hook(self):
        """Sync slash commands to the guild or globally."""
        await self.tree.sync()
        log.info("Slash commands synced")
        # Start background scan loop
        self.bg_scan_loop.start()
    
    @tasks.loop(hours=4)
    async def bg_scan_loop(self):
        """Scheduled watchlist scan + alert. Rebuilds watchlist daily."""
        config = load_config()
        threshold = config.get("alert_threshold", 5)

        # ── Daily watchlist rebuild ──
        try:
            from watchlist_builder import build_watchlist as _bw
            last_built = config.get("_watchlist_built_at", "")
            now_utc = datetime.now(timezone.utc)
            if not last_built or (now_utc - datetime.fromisoformat(last_built)).total_seconds() > 23 * 3600:
                log.info("🔄 Rebuilding watchlist (last built: %s)", last_built or "never")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _bw)
                config = load_config()  # reload after build
        except Exception as e:
            log.error("Watchlist builder error: %s", e)

        watchlist = config.get("watchlist", [])
        interval = config.get("scan_interval_hours", 4)
        
        # Update loop interval dynamically
        if self.bg_scan_loop.hours != interval:
            self.bg_scan_loop.change_interval(hours=interval)
        
        if not watchlist:
            log.info("Scheduled scan: watchlist empty, skipping")
            return
        
        channel = self.alert_channel
        if not channel and TARGET_CHANNEL_ID:
            channel = self.get_channel(TARGET_CHANNEL_ID)
            self.alert_channel = channel
        
        if not channel:
            log.warning("Scheduled scan: no alert channel set. Use /set_channel first.")
            return
        
        log.info(f"Scheduled scan of {len(watchlist)} symbols (threshold={threshold})…")
        alerts = []
        
        for sym in watchlist:
            row = await run_tps_scan(sym)
            if row is None:
                continue
            kpi = row.get("KPI_SCORE", 0)
            perfect = row.get("KPI_PERFECT", False)
            if kpi >= threshold or perfect:
                alerts.append((sym, row))
        
        if alerts:
            for sym, row in alerts:
                text = format_scan(sym, row)
                await channel.send(text)
            log.info(f"Sent {len(alerts)} alert(s)")
        else:
            log.info("Scheduled scan: no alerts triggered")
    
    @bg_scan_loop.before_loop
    async def before_scan(self):
        await self.wait_until_ready()
        # Delay first scan 30s after startup
        await asyncio.sleep(30)


bot = TPSDiscordBot()


# ── Slash Commands ────────────────────────────────────────────────────────
@bot.tree.command(name="scan", description="Run TPS_SCAN on a symbol")
@app_commands.describe(symbol="Stock symbol (e.g. AAPL, SPY, NVDA)")
async def cmd_scan(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    symbol = symbol.upper()
    row = await run_tps_scan(symbol)
    if row is None:
        await interaction.followup.send(f"❌ Could not scan **{symbol}**. Check symbol or try later.")
        return
    text = format_scan(symbol, row)
    await interaction.followup.send(text)


@bot.tree.command(name="watchlist", description="Show current watchlist")
async def cmd_watchlist(interaction: discord.Interaction):
    config = load_config()
    wl = config.get("watchlist", [])
    interval = config.get("scan_interval_hours", 4)
    threshold = config.get("alert_threshold", 5)
    
    lines = [
        f"**Watchlist** ({len(wl)} symbols)",
        f"Auto-scan every **{interval}h**, alert threshold **KPI ≥ {threshold}**",
        "",
        "`" + ", ".join(wl) + "`" if wl else "(empty)",
    ]
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="add", description="Add symbol to watchlist")
@app_commands.describe(symbol="Stock symbol to add")
async def cmd_add(interaction: discord.Interaction, symbol: str):
    config = load_config()
    sym = symbol.upper()
    if sym in config.get("watchlist", []):
        await interaction.response.send_message(f"⚠️ **{sym}** already in watchlist.")
        return
    config.setdefault("watchlist", []).append(sym)
    save_config(config)
    await interaction.response.send_message(f"✅ Added **{sym}** to watchlist (now {len(config['watchlist'])} symbols).")


@bot.tree.command(name="remove", description="Remove symbol from watchlist")
@app_commands.describe(symbol="Stock symbol to remove")
async def cmd_remove(interaction: discord.Interaction, symbol: str):
    config = load_config()
    sym = symbol.upper()
    wl = config.get("watchlist", [])
    if sym not in wl:
        await interaction.response.send_message(f"⚠️ **{sym}** not in watchlist.")
        return
    wl.remove(sym)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Removed **{sym}** (now {len(wl)} symbols).")


@bot.tree.command(name="status", description="Bot status overview")
async def cmd_status(interaction: discord.Interaction):
    config = load_config()
    wl = config.get("watchlist", [])
    interval = config.get("scan_interval_hours", 4)
    threshold = config.get("alert_threshold", 5)
    
    channel_name = "not set"
    if bot.alert_channel:
        channel_name = f"#{bot.alert_channel.name}"
    
    lines = [
        "**Bot Status**",
        f"• Alert channel: {channel_name}",
        f"• Watchlist: {len(wl)} symbols",
        f"• Scan interval: {interval}h",
        f"• Alert threshold: KPI ≥ {threshold}",
        f"• BG scan active: {bot.bg_scan_loop.is_running()}",
    ]
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="set_channel", description="Set the alert channel (this channel)")
async def cmd_set_channel(interaction: discord.Interaction):
    bot.alert_channel = interaction.channel
    await interaction.response.send_message(f"✅ Alert channel set to **#{interaction.channel.name}**.")


@bot.tree.command(name="set_interval", description="Set auto-scan interval in hours")
@app_commands.describe(hours="Interval in hours (1-24)")
async def cmd_set_interval(interaction: discord.Interaction, hours: int):
    if hours < 1 or hours > 24:
        await interaction.response.send_message("❌ Interval must be 1–24 hours.")
        return
    config = load_config()
    config["scan_interval_hours"] = hours
    save_config(config)
    await interaction.response.send_message(f"✅ Scan interval set to **{hours}h**.")


@bot.tree.command(name="set_threshold", description="Set KPI alert threshold")
@app_commands.describe(kpi="Minimum KPI score for alert (1-9)")
async def cmd_set_threshold(interaction: discord.Interaction, kpi: int):
    if kpi < 1 or kpi > 9:
        await interaction.response.send_message("❌ Threshold must be 1–9.")
        return
    config = load_config()
    config["alert_threshold"] = kpi
    save_config(config)
    await interaction.response.send_message(f"✅ Alert threshold set to **KPI ≥ {kpi}**.")


@bot.tree.command(name="help", description="Show help")
async def cmd_help(interaction: discord.Interaction):
    lines = [
        "**TPS Scanner — Discord Bot**",
        "",
        "`/scan SYMBOL` — Run TPS_SCAN on a ticker",
        "`/trade SYMBOL` — Buy 1 contract (paper)",
        "`/close SYMBOL` — Close position (paper)",
        "`/portfolio` — View P&L and positions",
        "`/orders` — Recent trade history",
        "`/autotrade SYMBOL` — Scan + buy if KPI > 5",
        "`/reset` — Reset paper trading account",
        "`/watchlist` — View current watchlist",
        "`/add SYMBOL` — Add to watchlist",
        "`/remove SYMBOL` — Remove from watchlist",
        "`/status` — Bot config overview",
        "`/set_channel` — Set this channel for alerts",
        "`/set_interval H` — Change scan interval (1–24h)",
        "`/set_threshold KPI` — Change alert threshold (1–9)",
        "",
        "Scheduled scans run automatically and alert when KPI ≥ threshold.",
        "Scoring: TREND(3) + PATTERN(3) + SQUEEZE(3) = KPI 0-9",
    ]
    await interaction.response.send_message("\n".join(lines))



# ── Trading Import ─────────────────────────────────────────────────────────
from broker_interface import get_broker


# ── Trading Helpers ───────────────────────────────────────────────────────
async def fetch_scan_price(symbol: str) -> dict:
    """Run TPS_SCAN and return {'price': float, 'kpi': int, 'error': str|None}"""
    row = await run_tps_scan(symbol)
    if row is None:
        return {"price": 0, "kpi": 0, "error": f"Could not scan {symbol}"}
    price = row.get("close", 0)
    if not price or price <= 0:
        return {"price": 0, "kpi": 0, "error": f"No price for {symbol}"}
    return {"price": price, "kpi": int(row.get("KPI_SCORE", 0)), "error": None}


def fmt_portfolio(pf: dict) -> str:
    lines = [
        f"**Paper Portfolio**  💰",
        f"Equity:   ${pf['equity']:,.2f}",
        f"Cash:     ${pf['cash']:,.2f}",
        f"Positions: {pf['position_count']}   |   Trades: {pf['trade_count']}",
        f"Realized P&L:  ${pf['realized_pnl']:+,.2f}",
        f"Unrealized:    ${pf['unrealized_pnl']:+,.2f}",
        f"**Total P&L:  ${pf['total_pnl']:+,.2f}**",
    ]
    if pf["positions"]:
        lines.append("")
        lines.append("**Open Positions:**")
        for p in pf["positions"]:
            lines.append(
                f"• {p['symbol']}  —  {p['qty']} @ ${p['avg_price']:.2f}  |  "
                f"Last: ${p['last_price']:.2f}  |  P&L: ${p['pnl']:+,.2f}"
            )
    return "\n".join(lines)


def fmt_orders(orders: list, limit: int = 10) -> str:
    if not orders:
        return "No orders yet."
    lines = [f"**Recent Orders** (last {min(len(orders), limit)})"]
    for o in orders[-limit:]:
        side = "🟢 BUY" if o["side"] == "buy" else "🔴 SELL"
        lines.append(
            f"`{o['id'][:8]}` {side} {o['symbol']} "
            f"{o['qty']} @ ${o['price']:.2f}  —  *{o['time'][:16].replace('T',' ')}*"
        )
    return "\n".join(lines)


# ── Trading Commands ─────────────────────────────────────────────────────
@bot.tree.command(name="trade", description="Buy 1 contract (paper trade)")
@app_commands.describe(symbol="Stock symbol to buy (e.g. SPY)")
async def cmd_trade(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    symbol = symbol.upper()
    broker = get_broker()

    r = await fetch_scan_price(symbol)
    if r["error"]:
        await interaction.followup.send(f"❌ {r['error']}")
        return

    # Check if already holding
    pos = broker.get_positions()
    if any(p["symbol"] == symbol for p in pos):
        await interaction.followup.send(
            f"⚠️ Already holding **{symbol}**. Use `/close {symbol}` first."
        )
        return

    order = broker.buy(symbol, r["price"])
    await interaction.followup.send(
        f"🟢 **Bought {symbol}**\n"
        f"Price: ${r['price']:.2f}  |  KPI: {r['kpi']}/9\n"
        f"Order: `{order['id'][:8]}`  |  Equity: ${broker.get_portfolio()['equity']:,.2f}"
    )


@bot.tree.command(name="close", description="Close position (paper trade)")
@app_commands.describe(symbol="Stock symbol to close")
async def cmd_close(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    symbol = symbol.upper()
    broker = get_broker()

    r = await fetch_scan_price(symbol)
    if r["error"]:
        await interaction.followup.send(f"❌ {r['error']}")
        return

    order = broker.close_position(symbol, r["price"])
    if order is None:
        await interaction.followup.send(f"⚠️ No position in **{symbol}**.")
        return

    pnl = order.get("pnl", 0)
    emoji = "🟢" if pnl >= 0 else "🔴"
    await interaction.followup.send(
        f"{emoji} **Closed {symbol}**\n"
        f"Exit: ${r['price']:.2f}  |  P&L: ${pnl:+,.2f}\n"
        f"Order: `{order['id'][:8]}`  |  Equity: ${broker.get_portfolio()['equity']:,.2f}"
    )


@bot.tree.command(name="portfolio", description="Show paper portfolio")
async def cmd_portfolio(interaction: discord.Interaction):
    broker = get_broker()
    # Update prices for open positions
    for pos in broker.get_positions():
        r = await fetch_scan_price(pos["symbol"])
        if not r["error"]:
            broker.update_prices({pos["symbol"]: r["price"]})

    pf = broker.get_portfolio()
    await interaction.response.send_message(fmt_portfolio(pf))


@bot.tree.command(name="orders", description="Show recent paper orders")
async def cmd_orders(interaction: discord.Interaction):
    broker = get_broker()
    orders = broker.get_orders(limit=10)
    await interaction.response.send_message(fmt_orders(orders))


@bot.tree.command(name="autotrade", description="Scan + buy if KPI > 5 (paper trade)")
@app_commands.describe(symbol="Stock symbol to scan & trade")
async def cmd_autotrade(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer(thinking=True)
    symbol = symbol.upper()
    broker = get_broker()

    r = await fetch_scan_price(symbol)
    if r["error"]:
        await interaction.followup.send(f"❌ {r['error']}")
        return

    kpi = r["kpi"]
    if kpi <= 5:
        await interaction.followup.send(
            f"⏸️ **{symbol}** KPI={kpi}/9 — below auto-trade threshold (6+). No trade."
        )
        return

    pos = broker.get_positions()
    if any(p["symbol"] == symbol for p in pos):
        await interaction.followup.send(
            f"⚠️ Already holding **{symbol}** (KPI={kpi}/9). Use `/close` first."
        )
        return

    order = broker.buy(symbol, r["price"])
    await interaction.followup.send(
        f"🤖 **Auto-traded {symbol}**\n"
        f"KPI: {kpi}/9  |  Price: ${r['price']:.2f}\n"
        f"Order: `{order['id'][:8]}`  |  Equity: ${broker.get_portfolio()['equity']:,.2f}"
    )


@bot.tree.command(name="reset_paper", description="Reset paper trading state")
async def cmd_reset_paper(interaction: discord.Interaction):
    from broker_interface import reset_broker
    reset_broker()
    broker = get_broker()
    broker.reset()
    await interaction.response.send_message(
        f"🔄 Paper trading reset. Initial capital: ${broker.get_portfolio()['initial_capital']:,.2f}"
    )

    log.info(f"Logged in as {bot.user.name} ({bot.user.id})")
    # Init Schwab client for live data
    try:
        get_client()
        log.info("Schwab client connected.")
    except Exception as e:
        log.warning("Schwab client unavailable (will run dry): %s", e)
    # Try to resolve alert channel from env
    if TARGET_CHANNEL_ID:
        ch = bot.get_channel(TARGET_CHANNEL_ID)
        if ch:
            bot.alert_channel = ch
            log.info(f"Alert channel from env: #{ch.name}")


def main():
    if not TOKEN:
        log.critical("DISCORD_BOT_TOKEN not set in .env")
        sys.exit(1)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
