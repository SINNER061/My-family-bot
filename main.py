"""
main.py — Self-healing Telegram Bot (python-telegram-bot v20+, asyncio)

Architecture
────────────
  ┌─ keep_alive.py ─────────────────────────────────┐
  │  • Flask HTTP server   → UptimeRobot pings       │
  │  • Health watchdog     → detects stuck bot       │
  └──────────────────────────────────────────────────┘
  ┌─ main.py ───────────────────────────────────────┐
  │  • outer while-loop    → restarts bot on crash   │
  │  • asyncio Application → telegram polling        │
  │  • heartbeat()         → proves bot is alive     │
  └──────────────────────────────────────────────────┘
"""

# ── std-lib ────────────────────────────────────────────────────────────────────
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ── third-party ────────────────────────────────────────────────────────────────
from telegram import Update
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── local ──────────────────────────────────────────────────────────────────────
import keep_alive

# ══════════════════════════════════════════════════════════════════════════════
# 1. LOGGING SETUP
# ══════════════════════════════════════════════════════════════════════════════
LOG_FILE    = "bot.log"
LOG_FORMAT  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE    = "%Y-%m-%d %H:%M:%S"
LOG_MAX_MB  = 5
LOG_BACKUPS = 3


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler — max 5 MB × 3 backups
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_MB * 1024 * 1024, backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


setup_logging()
logger = logging.getLogger("bot")

# ══════════════════════════════════════════════════════════════════════════════
# 2. CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

if not BOT_TOKEN:
    logger.critical(
        "BOT_TOKEN is not set! Add it in Replit → Tools → Secrets as BOT_TOKEN."
    )
    sys.exit(1)

# How long to wait between restart attempts (seconds); doubles on repeated failures
_BASE_RETRY_DELAY = 5
_MAX_RETRY_DELAY  = 120

# ══════════════════════════════════════════════════════════════════════════════
# 3. BOT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"
    logger.info("/start from user_id=%s", user.id if user else "?")
    await update.message.reply_html(
        f"👋 سلام <b>{name}</b>!\n\n"
        "من یک ربات خودترمیم هستم. همیشه آنلاینم 💪\n\n"
        "/help — راهنما\n"
        "/status — وضعیت ربات"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "دستورات موجود:\n"
        "/start   — شروع\n"
        "/help    — راهنما\n"
        "/status  — وضعیت سرور و ربات\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = keep_alive.get_state()
    uptime = (datetime.now(timezone.utc) - state["start_time"]).total_seconds()
    h, rem  = divmod(int(uptime), 3600)
    m, s    = divmod(rem, 60)
    last_hb = state["last_heartbeat"]
    hb_ago  = (
        f"{round((datetime.now(timezone.utc) - last_hb).total_seconds())} ثانیه پیش"
        if last_hb else "نامشخص"
    )
    await update.message.reply_text(
        f"✅ ربات فعال است\n"
        f"⏱ آپتایم: {h:02d}:{m:02d}:{s:02d}\n"
        f"💓 آخرین heartbeat: {hb_ago}\n"
        f"🔄 وضعیت polling: {'فعال' if state['bot_running'] else 'غیرفعال'}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo handler — replace with your own logic."""
    text = update.message.text or ""
    logger.debug("Message from user_id=%s: %s", update.effective_user.id, text[:80])
    await update.message.reply_text(f"دریافت شد: {text}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global telegram error handler — logs and handles recoverable errors."""
    err = context.error
    if isinstance(err, RetryAfter):
        logger.warning("Rate-limited by Telegram — waiting %.0f s", err.retry_after)
        await asyncio.sleep(err.retry_after)
    elif isinstance(err, TimedOut):
        logger.warning("Request timed out (will retry automatically).")
    elif isinstance(err, NetworkError):
        logger.warning("Network error: %s", err)
        await asyncio.sleep(5)
    elif isinstance(err, Forbidden):
        logger.warning("Bot was blocked by user.")
    elif isinstance(err, BadRequest):
        logger.warning("Bad request: %s", err)
    else:
        logger.error("Unhandled TelegramError: %s", err, exc_info=err)

# ══════════════════════════════════════════════════════════════════════════════
# 4. APPLICATION FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def build_application() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(15)
        .pool_timeout(15)
        .build()
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    return app

# ══════════════════════════════════════════════════════════════════════════════
# 5. HEARTBEAT TASK
# ══════════════════════════════════════════════════════════════════════════════

async def heartbeat_task() -> None:
    """Sends a heartbeat to keep_alive every 20 seconds."""
    while True:
        keep_alive.heartbeat()
        await asyncio.sleep(20)

# ══════════════════════════════════════════════════════════════════════════════
# 6. SINGLE BOT RUN (one attempt)
# ══════════════════════════════════════════════════════════════════════════════

async def run_bot_once() -> None:
    """
    Initialise → start polling → wait until stopped / watchdog restart → shutdown.
    Raises on unrecoverable errors so the outer loop can retry.
    """
    logger.info("Building Telegram Application …")
    application = build_application()

    # Start heartbeat task alongside polling
    loop = asyncio.get_running_loop()
    hb_task = loop.create_task(heartbeat_task(), name="heartbeat")

    keep_alive.set_bot_running(True)
    logger.info("Starting polling …")

    try:
        async with application:
            await application.start()
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                poll_interval=1.0,
                timeout=30,
                read_timeout=30,
                write_timeout=15,
                connect_timeout=15,
                pool_timeout=15,
            )

            logger.info("Bot is polling. Press Ctrl+C to stop.")

            # Keep the coroutine alive; wake every second to check watchdog flag
            while True:
                await asyncio.sleep(1)
                if keep_alive.clear_restart_flag():
                    logger.warning("Watchdog requested restart — stopping current run.")
                    break

            await application.updater.stop()
            await application.stop()

    finally:
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        keep_alive.set_bot_running(False)
        logger.info("Bot stopped cleanly.")

# ══════════════════════════════════════════════════════════════════════════════
# 7. OUTER SELF-HEALING LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_forever() -> None:
    """
    Outer loop: starts the bot, catches any exception, waits, and retries.
    Implements exponential back-off with a ceiling.
    """
    retry_delay = _BASE_RETRY_DELAY
    attempt     = 0

    while True:
        attempt += 1
        logger.info("━━━ Bot start attempt #%d ━━━", attempt)
        try:
            asyncio.run(run_bot_once())
            # run_bot_once returned normally → watchdog restart or graceful stop
            retry_delay = _BASE_RETRY_DELAY   # reset back-off
            logger.info("Bot run ended normally — restarting in %d s …", retry_delay)
            time.sleep(retry_delay)

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received — exiting.")
            sys.exit(0)

        except (NetworkError, TimedOut) as exc:
            logger.warning("Network issue: %s — retrying in %d s …", exc, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

        except TelegramError as exc:
            logger.error("TelegramError: %s — retrying in %d s …", exc, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

        except Exception as exc:
            logger.exception("Unexpected crash (attempt %d): %s", attempt, exc)
            logger.info("Restarting in %d s …", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

# ══════════════════════════════════════════════════════════════════════════════
# 8. ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def handle_sigterm(_sig, _frame) -> None:
    logger.info("SIGTERM received — shutting down gracefully.")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)

    logger.info("════════════════════════════════════════")
    logger.info(" Self-Healing Telegram Bot — starting")
    logger.info("════════════════════════════════════════")

    # 1. Start keep-alive web server + watchdog thread (non-blocking)
    keep_alive.start()

    # 2. Block here forever with self-healing
    run_forever()
