"""
Bot entry point.

Signals are posted to a fixed Telegram channel (config.CHANNEL_ID); the
command handlers below are for on-demand checks in a direct chat.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from analytics import performance_text
from auto_signal import auto_signal_job
from config import ADMIN_IDS, ASSETS, BOT_TOKEN, CHANNEL_ID, effective_decimals, require_env
from daily_summary import daily_summary_job
from data import get_candles, get_latest_price
from formatter import format_signal
from guards import status_text as guards_status_text
from logger_setup import setup_logging
from persistence import backend_name, is_degraded, postgres_required
from risk import calculate_trade
from strategy import get_signal
from trade_monitor import trade_monitor_job
from trade_tracker import get_open_trades, get_stats, history_text
from watchdog import watchdog_job

setup_logging()
logger = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not ADMIN_IDS:
        logger.error("ADMIN_IDS not configured — refusing commands")
        return False
    return uid in ADMIN_IDS


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("Unauthorized.")


# ── lifecycle ─────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    logger.info(f"[INIT] Persistence backend: {backend_name()}")
    if postgres_required() and (is_degraded() or backend_name() != "postgres"):
        raise RuntimeError(
            "Postgres is required but not ready. Set a working DATABASE_URL "
            "(Railway Postgres plugin) or ALLOW_JSON_PERSISTENCE=1 for local-only."
        )
    if is_degraded() or backend_name() != "postgres":
        logger.warning(
            "[INIT] Persistence is not durable Postgres. On Railway without "
            "DATABASE_URL / working JSONB saves, every redeploy WIPES open "
            "trades and history. Add a Postgres plugin."
        )

    open_n = len(get_open_trades())
    logger.info(f"[INIT] Recovered {open_n} open trade(s) from {backend_name()}")

    application.bot_data["_bg_tasks"] = [
        asyncio.create_task(auto_signal_job(application),   name="auto_signal"),
        asyncio.create_task(trade_monitor_job(application), name="trade_monitor"),
        asyncio.create_task(watchdog_job(application),      name="watchdog"),
        asyncio.create_task(daily_summary_job(application), name="daily_summary"),
    ]
    logger.info("[INIT] Background tasks started")


async def post_shutdown(application: Application) -> None:
    tasks = application.bot_data.get("_bg_tasks", [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("[SHUTDOWN] Goodbye.")


# ── helpers ───────────────────────────────────────────────────────────────

async def _build_result(candles: dict, asset: str = "btc") -> tuple[dict, int]:
    decimals = effective_decimals(asset, candles.get("price"))

    result = get_signal(
        candles["close"], candles["high"], candles["low"],
        candles["timeframes"], candles.get("volume"), candles.get("open"),
        decimals=decimals,
    )

    live_price = await asyncio.to_thread(get_latest_price, asset)
    if live_price is not None:
        candles["price"] = live_price
        decimals = effective_decimals(asset, live_price)
        if result["signal"] in ("BUY", "SELL"):
            result.update(calculate_trade(
                result["signal"], live_price, result.get("atr_value", 0),
                decimals=decimals, session_active=result.get("session_active", True),
            ))

    return result, decimals


def _asset_arg(context) -> str | None:
    return context.args[0].lower() if context.args else None


# ── command handlers ──────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    asset_lines = "\n".join(
        f"/{a}    — Manual {cfg['label']} signal" for a, cfg in ASSETS.items()
    )
    await update.message.reply_text(
        "🤖 MAHENDRA CRYPTO AI SIGNAL\n\n"
        "✅ Bot Online\n"
        "📡 AI Signal Engine Active\n"
        "📢 Auto signals are posted to the channel\n\n"
        "Commands:\n"
        f"{asset_lines}\n"
        "/signal  — Same as /btc\n"
        "/trend   — Trend summary (BTC)\n"
        "/stats   — Trade statistics (e.g. /stats sol)\n"
        "/perf    — Expectancy, profit factor, drawdown (e.g. /perf eth)\n"
        "/guards  — Risk-guard status\n"
        "/history — Last 10 trades (e.g. /history eth)"
    )


def _make_asset_handler(asset: str):
    cfg   = ASSETS[asset]
    label = cfg["label"]

    async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            await _deny(update)
            return
        try:
            candles = await asyncio.to_thread(get_candles, asset)
            result, decimals = await _build_result(candles, asset)
            text = format_signal(candles, result, decimals=decimals, label=label)
            text += (
                "\n\n⚠️ MANUAL / UNTRACKED — not saved; "
                "guards & news filter not applied"
            )
            await update.message.reply_text(text)
        except Exception as e:
            logger.exception(f"[CMD /{asset}] {e}")
            await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")

    return _handler


_asset_handlers = {a: _make_asset_handler(a) for a in ASSETS}
btc = _asset_handlers["btc"]


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await btc(update, context)


async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    try:
        candles = await asyncio.to_thread(get_candles, "btc")
        result, _ = await _build_result(candles, "btc")
        await update.message.reply_text(
            f"📊 1M Trend      : {result['trend_1m']}\n"
            f"📊 5M Trend      : {result['trend_5m']}\n"
            f"📊 15M Trend     : {result['trend_15m']}\n\n"
            f"📈 Trend Strength: {result['trend_strength']}\n"
            f"📢 Signal        : {result['signal']}\n"
            f"🤖 AI Score      : {result['ai_score']}\n"
            f"🎖 Grade         : {result['grade']}\n"
            f"🔥 Confidence    : {result['confidence']}%\n"
            f"📍 Market        : {result['market_status']}"
        )
    except Exception as e:
        logger.exception(f"[CMD /trend] {e}")
        await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    try:
        arg = _asset_arg(context)
        if arg and arg not in ASSETS:
            await update.message.reply_text(
                f"❌ Unknown asset '{arg}'. Valid: {', '.join(ASSETS)}"
            )
            return

        if arg:
            s = get_stats(asset=arg)
            await update.message.reply_text(
                f"📊 TRADE STATISTICS — {ASSETS[arg]['label']}\n\n"
                f"📈 Total Signals : {s['total']}\n"
                f"🔵 Open          : {s['open']}\n"
                f"🟢 BUY / 🔴 SELL : {s['buy']} / {s['sell']}\n\n"
                f"🎯 TP Hit        : {s['tp']}\n"
                f"⚪ Breakeven     : {s['be']}\n"
                f"🛑 SL Hit        : {s['sl']}\n\n"
                f"🏆 Win Rate      : {s['win_rate']}%  ({s['wins']}W / {s['losses']}L "
                f"of {s['closed']} closed)"
            )
            return

        s = get_stats()
        breakdown = []
        for a, cfg in ASSETS.items():
            a_stats = get_stats(asset=a)
            if a_stats["total"] == 0:
                continue
            breakdown.append(
                f"  {cfg['label']:<6} {a_stats['total']:>3} sig | "
                f"{a_stats['closed']:>3} closed | {a_stats['win_rate']}% win"
            )
        breakdown_text = "\n".join(breakdown) if breakdown else "  (no signals yet)"

        await update.message.reply_text(
            f"📊 TRADE STATISTICS (All Assets)\n\n"
            f"📈 Total Signals : {s['total']}\n"
            f"🔵 Open          : {s['open']}\n"
            f"🟢 BUY / 🔴 SELL : {s['buy']} / {s['sell']}\n\n"
            f"🎯 TP Hit        : {s['tp']}\n"
            f"⚪ Breakeven     : {s['be']}\n"
            f"🛑 SL Hit        : {s['sl']}\n\n"
            f"🏆 Win Rate      : {s['win_rate']}%  ({s['wins']}W / {s['losses']}L "
            f"of {s['closed']} closed)\n\n"
            f"Per-Asset:\n{breakdown_text}\n\n"
            f"Tip: /stats <asset> for detail (e.g. /stats sol), /perf for expectancy"
        )
    except Exception as e:
        logger.exception(f"[CMD /stats] {e}")
        await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")


async def perf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    try:
        arg = _asset_arg(context)
        if arg and arg not in ASSETS:
            await update.message.reply_text(
                f"❌ Unknown asset '{arg}'. Valid: {', '.join(ASSETS)}"
            )
            return
        await update.message.reply_text(performance_text(asset=arg))
    except Exception as e:
        logger.exception(f"[CMD /perf] {e}")
        await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")


async def guards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    try:
        await update.message.reply_text(guards_status_text())
    except Exception as e:
        logger.exception(f"[CMD /guards] {e}")
        await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await _deny(update)
        return
    try:
        arg = _asset_arg(context)
        if arg and arg not in ASSETS:
            await update.message.reply_text(
                f"❌ Unknown asset '{arg}'. Valid: {', '.join(ASSETS)}"
            )
            return
        await update.message.reply_text(history_text(asset=arg))
    except Exception as e:
        logger.exception(f"[CMD /history] {e}")
        await update.message.reply_text(f"❌ ERROR\n\n{type(e).__name__}: {e}")


# ── entry point ───────────────────────────────────────────────────────────

def main() -> None:
    require_env()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    for asset_name, handler_fn in _asset_handlers.items():
        app.add_handler(CommandHandler(asset_name, handler_fn))
    app.add_handler(CommandHandler("signal",  signal))
    app.add_handler(CommandHandler("trend",   trend))
    app.add_handler(CommandHandler("stats",   stats))
    app.add_handler(CommandHandler("perf",    perf))
    app.add_handler(CommandHandler("guards",  guards))
    app.add_handler(CommandHandler("history", history))

    logger.info("🚀 Mahendra Crypto AI Signal starting…")
    logger.info(f"[INIT] CHANNEL_ID configured; {len(ADMIN_IDS)} admin id(s)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
