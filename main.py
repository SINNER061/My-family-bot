"""
main.py — Self-healing Telegram Bot  (python-telegram-bot v21, asyncio)

Fixed vs. previous version
───────────────────────────
  [BUG #1]  asyncio.run() replaced with new_event_loop() + run_until_complete()
            so the event loop is explicitly managed and closed cleanly on restart.
  [BUG #2]  Conflict (409) error → waits 30 s then restarts (no retry storm).
  [BUG #3]  InvalidToken → logs clearly and exits immediately (no endless retry).
  [BUG #4]  All handlers guard against update.message / effective_user being None.
  [BUG #5]  BaseException caught in outer loop so SystemExit propagates correctly.
  [BUG #6]  heartbeat_task wrapped in try/except so it never dies silently.
  [BUG #7]  Consecutive failure counter → exits after MAX_CONSECUTIVE_FAILURES.
  [BUG #8]  poll_interval removed from start_polling (set on builder instead).
  [BUG #9]  Graceful updater+application stop extracted to helper to avoid
            calling stop() twice inside async with.
"""

# ── std-lib ────────────────────────────────────────────────────────────────────
import asyncio
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ── third-party ────────────────────────────────────────────────────────────────
from telegram import Update
from telegram.error import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidToken,
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


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return   # already configured (guard against double-call on restart)
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_MB * 1024 * 1024,
        backupCount=LOG_BACKUPS,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


_setup_logging()
logger = logging.getLogger("bot")

# ══════════════════════════════════════════════════════════════════════════════
# 2. CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    logger.critical(
        "BOT_TOKEN is not set!\n"
        "  → Replit: Tools → Secrets → add key BOT_TOKEN with your token value.\n"
        "  → Local:  export BOT_TOKEN='your-token-here'"
    )
    sys.exit(1)

# Retry / resilience settings
_BASE_RETRY_DELAY      = 5      # seconds
_MAX_RETRY_DELAY       = 120    # seconds (exponential back-off ceiling)
_CONFLICT_WAIT         = 35     # seconds to wait after a 409 Conflict
_MAX_CONSECUTIVE_FAILS = 20     # give up after this many back-to-back crashes

# Signals main loop to exit cleanly when InvalidToken is detected inside async context
_invalid_token_flag = threading.Event()

# ══════════════════════════════════════════════════════════════════════════════
# 3. BOT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:          # [FIX #4] guard
        return
    user = update.effective_user
    name = user.first_name if user else "there"
    logger.info("/start from user_id=%s", user.id if user else "?")
    await update.message.reply_html(
        f"👋 سلام <b>{name}</b>!\n\n"
        "من یک ربات خودترمیم هستم — همیشه آنلاینم 💪\n\n"
        "/help — راهنما\n"
        "/status — وضعیت ربات"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:          # [FIX #4]
        return
    await update.message.reply_text(
        "دستورات موجود:\n"
        "/start   — شروع\n"
        "/help    — راهنما\n"
        "/status  — وضعیت سرور و ربات\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:          # [FIX #4]
        return
    s = keep_alive.get_state()
    now    = datetime.now(timezone.utc)
    uptime = (now - s["start_time"]).total_seconds()
    h, rem = divmod(int(uptime), 3600)
    m, sec = divmod(rem, 60)
    last_hb = s["last_heartbeat"]
    hb_ago  = (
        f"{round((now - last_hb).total_seconds())} ثانیه پیش"
        if last_hb else "هنوز ثبت نشده"
    )
    await update.message.reply_text(
        f"✅ ربات فعال است\n"
        f"⏱ آپتایم: {h:02d}:{m:02d}:{sec:02d}\n"
        f"💓 آخرین heartbeat: {hb_ago}\n"
        f"🔄 وضعیت polling: {'فعال' if s['bot_running'] else 'غیرفعال'}\n"
        f"🔁 تعداد ری‌استارت‌ها: {s['restart_count']}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo handler — replace or extend with your own logic."""
    if not update.message or not update.effective_user:   # [FIX #4]
        return
    text = update.message.text or ""
    logger.debug("Message from user_id=%s: %.80s", update.effective_user.id, text)
    await update.message.reply_text(f"دریافت شد: {text}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — classifies and responds to every Telegram error."""
    err = context.error

    # [FIX #2] Conflict — two instances running; back off and let outer loop restart
    if isinstance(err, Conflict):
        logger.error(
            "Conflict (409): another bot instance is already polling. "
            "Waiting %s s before restart.", _CONFLICT_WAIT,
        )
        keep_alive.request_restart(reason="Conflict 409")
        await asyncio.sleep(_CONFLICT_WAIT)
        return

    # [FIX #3] Bad token — signal main loop via flag instead of sys.exit() in async context
    if isinstance(err, InvalidToken):
        logger.critical(
            "InvalidToken: BOT_TOKEN is wrong. Fix it in Secrets then restart."
        )
        _invalid_token_flag.set()   # triggers clean exit in _run_bot_once()
        return

    if isinstance(err, RetryAfter):
        logger.warning("Rate-limited — sleeping %.0f s.", err.retry_after)
        await asyncio.sleep(err.retry_after)

    elif isinstance(err, TimedOut):
        logger.warning("Request timed out — PTB will retry automatically.")

    elif isinstance(err, NetworkError):
        logger.warning("Network error: %s — PTB will retry.", err)

    elif isinstance(err, Forbidden):
        logger.info("Bot blocked by user (Forbidden).")

    elif isinstance(err, BadRequest):
        logger.warning("Bad API request: %s", err)

    elif isinstance(err, TelegramError):
        logger.error("TelegramError: %s", err, exc_info=True)

    else:
        logger.error("Non-Telegram error in handler: %s", err, exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4. APPLICATION FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def _build_application() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(20)
        .read_timeout(30)          # long-poll window
        .write_timeout(20)
        .pool_timeout(20)
        .build()
    )
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    return app


# ══════════════════════════════════════════════════════════════════════════════
# 5. HEARTBEAT TASK  [FIX #6]
# ══════════════════════════════════════════════════════════════════════════════

async def _heartbeat_task() -> None:
    """Ticks every 20 s; wrapped in try/except so it never dies silently."""
    logger.debug("Heartbeat task started.")
    while True:
        try:
            keep_alive.heartbeat()
        except Exception as exc:             # [FIX #6] never let this task die
            logger.error("Heartbeat error: %s", exc)
        await asyncio.sleep(20)


# ══════════════════════════════════════════════════════════════════════════════
# 6. GRACEFUL SHUTDOWN HELPER
# ══════════════════════════════════════════════════════════════════════════════

async def _stop_application(application: Application) -> None:
    """Stop updater → stop application, swallowing errors so shutdown always runs."""
    try:
        if application.updater and application.updater.running:
            await application.updater.stop()
    except Exception as exc:
        logger.warning("Error stopping updater: %s", exc)

    try:
        if application.running:
            await application.stop()
    except Exception as exc:
        logger.warning("Error stopping application: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# 7. SINGLE BOT RUN (one attempt)
# ══════════════════════════════════════════════════════════════════════════════

async def _run_bot_once() -> None:
    """
    One full bot lifecycle: initialize → start → poll → (watchdog/error) → stop.
    Raises on unrecoverable errors so the outer loop can decide whether to retry.
    """
    logger.info("Building Telegram Application …")
    application = _build_application()

    hb_task: asyncio.Task | None = None
    keep_alive.set_bot_running(True)

    try:
        await application.initialize()
        await application.start()

        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            # poll_interval is NOT passed here — [FIX #8]
            # PTB v21 uses the timeouts set on the builder.
        )

        logger.info("Bot is now polling. Ctrl+C to stop.")

        # Start heartbeat after polling is confirmed running
        hb_task = asyncio.get_running_loop().create_task(
            _heartbeat_task(), name="heartbeat",
        )

        # Main wait loop — wakes every second to check watchdog flag and polling health
        while True:
            await asyncio.sleep(1)

            # [review fix #2] InvalidToken detected asynchronously → exit immediately
            if _invalid_token_flag.is_set():
                logger.critical("InvalidToken flag set — exiting.")
                sys.exit(1)

            # [review fix #1] Verify polling is actually running, not just heartbeat
            if not application.updater.running:
                logger.error(
                    "Updater polling has stopped unexpectedly — triggering restart."
                )
                keep_alive.request_restart(reason="updater.running=False")

            if keep_alive.clear_restart_flag():
                logger.warning("Restart flag set — stopping current run.")
                break

    finally:
        # Cancel heartbeat first
        if hb_task and not hb_task.done():
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        await _stop_application(application)   # [FIX #9] single controlled stop

        try:
            await application.shutdown()
        except Exception as exc:
            logger.warning("Error during shutdown: %s", exc)

        keep_alive.set_bot_running(False)
        logger.info("Bot run finished cleanly.")


# ══════════════════════════════════════════════════════════════════════════════
# 8. OUTER SELF-HEALING LOOP  [FIX #1, #5, #7]
# ══════════════════════════════════════════════════════════════════════════════

def _run_forever() -> None:
    """
    Outer loop: manages event loops, catches every exception, retries with
    exponential back-off, and gives up after MAX_CONSECUTIVE_FAILS failures.
    """
    retry_delay        = _BASE_RETRY_DELAY
    attempt            = 0
    consecutive_fails  = 0

    while True:
        attempt += 1
        logger.info("━━━ Bot start attempt #%d (consecutive_fails=%d) ━━━",
                    attempt, consecutive_fails)

        # [FIX #1] Explicit event loop per attempt — avoids "loop is closed" errors
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(_run_bot_once())
            # Normal exit (watchdog restart or graceful stop)
            consecutive_fails = 0
            retry_delay = _BASE_RETRY_DELAY
            logger.info("Bot run ended normally — restarting in %d s.", retry_delay)
            time.sleep(retry_delay)

        except KeyboardInterrupt:           # [FIX #5] let Ctrl+C exit cleanly
            logger.info("KeyboardInterrupt — exiting.")
            sys.exit(0)

        except SystemExit:                  # [FIX #5] let sys.exit() propagate
            raise

        except InvalidToken:               # [FIX #3] wrong token → exit immediately
            logger.critical(
                "InvalidToken: check BOT_TOKEN in Replit Secrets. Exiting."
            )
            sys.exit(1)

        except Conflict:                   # [FIX #2] 409 — wait longer, then retry
            consecutive_fails += 1
            logger.error(
                "Conflict (409) — another instance may be running. "
                "Waiting %d s before retry.", _CONFLICT_WAIT,
            )
            time.sleep(_CONFLICT_WAIT)

        except (NetworkError, TimedOut) as exc:
            consecutive_fails += 1
            logger.warning("Network issue: %s — retry in %d s.", exc, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

        except TelegramError as exc:
            consecutive_fails += 1
            logger.error("TelegramError: %s — retry in %d s.", exc, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

        except Exception as exc:
            consecutive_fails += 1
            logger.exception("Unexpected crash (attempt %d): %s", attempt, exc)
            logger.info("Restarting in %d s …", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)

        finally:
            # Always close the loop so resources are freed  [FIX #1]
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                pass
            loop.close()

        # [FIX #7] Safety valve — exit if bot is in a crash-loop
        if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
            logger.critical(
                "Reached %d consecutive failures — stopping to prevent CPU spin. "
                "Fix the underlying error, then restart the Repl.",
                _MAX_CONSECUTIVE_FAILS,
            )
            sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# 9. ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _handle_sigterm(_sig, _frame) -> None:
    logger.info("SIGTERM received — shutting down gracefully.")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)

    logger.info("════════════════════════════════════════════")
    logger.info(" Self-Healing Telegram Bot — v2.0 starting ")
    logger.info("════════════════════════════════════════════")

    # Start Flask keep-alive + watchdog threads (background, non-blocking)
    keep_alive.start()

    # Block forever with self-healing
    _run_forever()
