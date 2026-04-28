
#!/usr/bin/env python3
"""
Telegram bot for Project Nate — TPS Scanner.
Dual-mode: on-demand /scan + scheduled watchlist alerts.

Usage:
    source .venv/bin/activate
    python telegram_bot.py
"""

import asyncio, json, logging, os, sys, traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, JobQueue,
)

# ── project root on path so we can import tps_scan ──────────────
PROJ_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ_ROOT))

load_dotenv(PROJ_ROOT / ".env")

from tps_scan import TPS_SCAN, compute_mtf_squeeze  # noqa: E402

# ── config ──────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nate_bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS_RAW = os.getenv("TELEGRAM_CHAT_IDS", "")
AUTHORIZED_CHATS = set()

for cid in CHAT_IDS_RAW.split(","):
    cid = cid.strip()
    if cid:
        try:
            AUTHORIZED_CHATS.add(int(cid))
        except ValueError:
            log.warning("Bad chat id in env: %s", cid)

CONFIG_PATH = PROJ_ROOT / "telegram_config.json"
DEFAULT_CONFIG = {
    "watchlist": [],
    "scan_interval_hours": 4,
    "alert_threshold": 5,          # KPI_SCORE >= this triggers alert
    "next_scan_at": None,
}

# ── helpers ─────────────────────────────────────────────────────

def load_config() -> dict:
    """Load telegram_config.json, fall back to defaults."""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        log.exception("Failed to load config")
    return {**DEFAULT_CONFIG}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, default=str))


def _auth_only(func):
    """Decorator: only respond to authorized chat IDs."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat is None:
            return
        cid = update.effective_chat.id
        if cid not in AUTHORIZED_CHATS:
            await update.message.reply_text("⛔ Unauthorized.")
            return
        return await func(update, context)
    return wrapper


def _format_tps_row(row) -> str:
    """Format the latest TPS_SCAN row into a compact Telegram message."""
    px = row.get("close", "?")
    try:
        px = f"{float(px):.2f}"
    except Exception:
        px = str(px)

    lines = []
    lines.append(f"💰 Close: **${px}**")

    # TREND
    ut = row.get("Upward_Trend", False)
    dt = row.get("Downward_Trend", False)
    trend = "🟢 UP" if ut else ("🔴 DOWN" if dt else "⚪ NEUTRAL")
    ema8  = row.get("EMA_8", 0)
    ema21 = row.get("EMA_21", 0)
    ema55 = row.get("EMA_55", 0)
    try:
        lines.append(f"📈 Trend: {trend}  EMA: {ema8:.2f} > {ema21:.2f} > {ema55:.2f}")
    except Exception:
        lines.append(f"📈 Trend: {trend}")

    # PATTERN
    bf = row.get("bull_flag", False)
    pn = row.get("pennant", False)
    if bf:
        lines.append("🚩 Pattern: **BULL FLAG** 🟢")
    elif pn:
        lines.append("🚩 Pattern: **PENNANT** 🟡")

    # SQUEEZE
    sqz_narrow = row.get("SQZPRO_ON_NARROW", False)
    sqz_normal = row.get("SQZPRO_ON_NORMAL", False)
    sqz_wide   = row.get("SQZPRO_ON_WIDE", False)
    sqz_fired  = row.get("squeeze_fired", False)
    momo_cyan  = row.get("momo_cyan", False)
    if sqz_narrow:
        sqz = "🔵 NARROW"
    elif sqz_normal:
        sqz = "🟢 NORMAL"
    elif sqz_wide:
        sqz = "🔴 WIDE"
    else:
        sqz = "⚪ OFF"
    sqz_f = "🔥 FIRED" if sqz_fired else ""
    mc    = "💠 Cyan" if momo_cyan else ""
    lines.append(f"📐 Squeeze: {sqz} {sqz_f} {mc}".strip())

    # VWAP
    vwap = row.get("vwap")
    v_up = row.get("vwap_uptrend_setup", False)
    v_x  = row.get("vwap_cross_up", False)
    v_b  = row.get("vwap_cross_with_burst", False)
    v_bu = row.get("volume_burst", False)
    v_details = []
    if vwap is not None:
        try:
            v_details.append(f"VWAP={float(vwap):.2f}")
        except Exception:
            v_details.append(f"VWAP={vwap}")
    if v_up:  v_details.append("UptrendSetup")
    if v_x:   v_details.append("CrossUp")
    if v_b:   v_details.append("X+Burst")
    if v_bu:  v_details.append("VolBurst")
    if v_details:
        lines.append(f"📊 VWAP: {', '.join(v_details)}")

    # SHORT INTEREST
    sf  = row.get("short_float_pct")
    sr  = row.get("short_ratio")
    sqk = row.get("short_squeeze_ok", False)
    si_parts = []
    if sf is not None:
        try: si_parts.append(f"Float={float(sf):.1f}%")
        except: pass
    if sr is not None:
        try: si_parts.append(f"Ratio={float(sr):.1f}")
        except: pass
    if sqk:
        si_parts.append("⚠️ SqueezeOk")
    if si_parts:
        lines.append(f"📉 Short: {', '.join(si_parts)}")

    # KPI
    ks = row.get("KPI_SCORE", 0)
    kp = row.get("KPI_PERFECT", False)
    kpi_str = f"⭐ KPI: **{int(ks)}/8**"
    if kp:
        kpi_str += " 🏆 PERFECT! 🏆"
    lines.append(kpi_str)

    # MARKET CONTEXT (brief)
    macro = []
    if "spy_corr" in row:
        try: macro.append(f"SPY β={float(row['spy_corr']):.2f}")
        except: pass
    if "qqq_regime" in row:
        macro.append(f"QQQ={row['qqq_regime']}")
    if "vix_regime" in row:
        macro.append(f"VIX={row['vix_regime']}")
    if "vix_strategy_bias" in row:
        macro.append(f"({row['vix_strategy_bias']})")
    if macro:
        lines.append(f"🌐 Market: {' '.join(macro)}")

    return "\n".join(lines)


# ── bot commands ─────────────────────────────────────────────────

@_auth_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Project Nate — TPS Bot**\n\n"
        "TREND + PATTERN + SQUEEZE scanner.\n\n"
        "Commands:\n"
        "/scan SYMBOL — scan a ticker\n"
        "/watchlist — show list\n"
        "/add SYMBOL — add to watchlist\n"
        "/remove SYMBOL — remove\n"
        "/status — bot status\n"
        "/set_interval HOURS — schedule interval\n"
        "/help — this message",
        parse_mode=ParseMode.MARKDOWN,
    )


@_auth_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


@_auth_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = "".join(context.args).strip().upper() if context.args else ""
    if not symbol:
        await update.message.reply_text("Usage: /scan SYMBOL  (e.g. /scan AAPL)")
        return

    msg = await update.message.reply_text(f"🔍 Scanning {symbol}…")

    try:
        df = await asyncio.to_thread(TPS_SCAN, symbol)
    except Exception as e:
        await msg.edit_text(f"❌ Scan failed for {symbol}: {e}")
        log.exception("Scan failed: %s", symbol)
        return

    if df.empty:
        await msg.edit_text(f"⚠️ No data for {symbol}")
        return

    row = df.iloc[-1]
    text = f"📋 **{symbol}**  (last bar: {df.index[-1].strftime('%Y-%m-%d')})\n\n{_format_tps_row(row)}"

    # Append MTF squeeze if available
    try:
        mtf = await asyncio.to_thread(compute_mtf_squeeze, symbol)
        if not mtf.empty:
            mtf_lines = []
            for _, r2 in mtf.iterrows():
                tf = r2.get("timeframe", "?")
                on = r2.get("squeeze_on", False)
                na = r2.get("on_narrow", False)
                fi = r2.get("squeeze_fired", False)
                cy = r2.get("momo_cyan", False)
                parts = [f"`{tf}`"]
                if na: parts.append("🔵N")
                elif on: parts.append("🟢S")
                else: parts.append("⚪")
                if fi: parts.append("🔥")
                if cy: parts.append("💠")
                mtf_lines.append(" ".join(parts))
            if mtf_lines:
                text += f"\n\n🕐 **MTF Squeeze:**\n{chr(10).join(mtf_lines)}"
    except Exception:
        pass

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


@_auth_only
async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    wl = cfg.get("watchlist", [])
    if not wl:
        await update.message.reply_text("📭 Watchlist is empty. Use /add SYMBOL")
        return

    interval = cfg.get("scan_interval_hours", 4)
    lines = [
        f"📋 **Watchlist**  (scans every {interval}h)\n",
        *[f"• {s}" for s in wl],
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@_auth_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = "".join(context.args).strip().upper() if context.args else ""
    if not symbol:
        await update.message.reply_text("Usage: /add SYMBOL")
        return

    cfg = load_config()
    wl = cfg.get("watchlist", [])
    if symbol in wl:
        await update.message.reply_text(f"✅ {symbol} already in watchlist.")
        return

    wl.append(symbol)
    cfg["watchlist"] = wl
    save_config(cfg)
    await update.message.reply_text(f"➕ Added {symbol}. Watchlist now: {', '.join(wl)}")


@_auth_only
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = "".join(context.args).strip().upper() if context.args else ""
    if not symbol:
        await update.message.reply_text("Usage: /remove SYMBOL")
        return

    cfg = load_config()
    wl = cfg.get("watchlist", [])
    if symbol not in wl:
        await update.message.reply_text(f"❌ {symbol} not in watchlist.")
        return

    wl.remove(symbol)
    cfg["watchlist"] = wl
    save_config(cfg)
    await update.message.reply_text(f"➖ Removed {symbol}. Watchlist: {', '.join(wl) if wl else 'empty'}")


@_auth_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    wl = cfg.get("watchlist", [])
    interval = cfg.get("scan_interval_hours", 4)
    threshold = cfg.get("alert_threshold", 5)
    next_scan = cfg.get("next_scan_at", "not scheduled")

    text = (
        f"🤖 **Bot Status**\n"
        f"• Watchlist: {len(wl)} symbols {wl if wl else ''}\n"
        f"• Scan interval: {interval}h\n"
        f"• Alert threshold: KPI >= {threshold}\n"
        f"• Next scheduled scan: {next_scan}\n"
        f"• Authorized chats: {len(AUTHORIZED_CHATS)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@_auth_only
async def cmd_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hours = float(context.args[0]) if context.args else 4
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /set_interval HOURS  (e.g. /set_interval 6)")
        return

    if hours < 0.5:
        await update.message.reply_text("Minimum interval is 0.5 hours (30 min)")
        return

    cfg = load_config()
    cfg["scan_interval_hours"] = hours
    save_config(cfg)

    # Reschedule jobs
    jobs = context.job_queue.jobs()
    for j in jobs:
        if j.name == "watchlist_scan":
            j.schedule_removal()

    _schedule_scan(context.job_queue, hours)

    await update.message.reply_text(f"⏱ Scan interval set to {hours}h")


# ── scheduled scan ───────────────────────────────────────────────

async def _do_watchlist_scan(context: ContextTypes.DEFAULT_TYPE):
    """Scan every symbol in the watchlist. Alert if KPI >= threshold."""
    cfg = load_config()
    wl = cfg.get("watchlist", [])
    threshold = cfg.get("alert_threshold", 5)

    if not wl:
        log.info("Scheduled scan: watchlist empty, skipping.")
        return

    log.info("Scheduled scan: %s", wl)

    for symbol in wl:
        try:
            df = await asyncio.to_thread(TPS_SCAN, symbol)
        except Exception as e:
            log.warning("Scheduled scan failed for %s: %s", symbol, e)
            continue

        if df.empty:
            continue

        row = df.iloc[-1]
        ks = int(row.get("KPI_SCORE", 0))
        kp = bool(row.get("KPI_PERFECT", False))

        if ks < threshold and not kp:
            continue  # below threshold — skip alert

        text = f"🚨 **ALERT: {symbol}** — KPI **{ks}/8**"
        if kp:
            text += " 🏆 PERFECT!"
        text += f"\n\n{_format_tps_row(row)}"

        for cid in AUTHORIZED_CHATS:
            try:
                await context.bot.send_message(
                    chat_id=cid,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                log.exception("Failed to send alert to %s", cid)

    # Update next-scan timestamp
    cfg["next_scan_at"] = datetime.now(timezone.utc).isoformat()
    save_config(cfg)


def _schedule_scan(job_queue: JobQueue, interval_hours: float):
    """Schedule the watchlist scan job."""
    existing = [j for j in job_queue.jobs() if j.name == "watchlist_scan"]
    for j in existing:
        j.schedule_removal()

    job_queue.run_repeating(
        _do_watchlist_scan,
        interval=interval_hours * 3600,
        first=30,  # first scan 30s after startup
        name="watchlist_scan",
    )
    log.info("Scheduled watchlist scan every %.1f hours", interval_hours)


# ── startup ──────────────────────────────────────────────────────

async def post_init(app: Application):
    """Set bot commands menu and schedule scans."""
    commands = [
        BotCommand("start", "Welcome + help"),
        BotCommand("help", "Show help"),
        BotCommand("scan", "Scan a ticker — /scan AAPL"),
        BotCommand("watchlist", "Show watchlist"),
        BotCommand("add", "Add to watchlist — /add AAPL"),
        BotCommand("remove", "Remove from watchlist — /remove AAPL"),
        BotCommand("status", "Bot status"),
        BotCommand("set_interval", "Set scan interval in hours"),
    ]
    await app.bot.set_my_commands(commands)

    cfg = load_config()
    interval = cfg.get("scan_interval_hours", 4)
    if cfg.get("watchlist"):
        _schedule_scan(app.job_queue, interval)

    log.info("Bot started. Watchlist: %s, interval: %.1fh", cfg["watchlist"], interval)


def main():
    if not TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    if not AUTHORIZED_CHATS:
        log.warning("No TELEGRAM_CHAT_IDS configured — bot will reject all messages!")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("set_interval", cmd_set_interval))

    log.info("Starting polling…")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
