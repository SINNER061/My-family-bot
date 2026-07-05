"""
keep_alive.py — Flask keep-alive server + thread-safe health watchdog.

Changes vs. previous version
─────────────────────────────
  • threading.Lock on all _state mutations  (fixes race condition)
  • Flask OSError detection                 (fixes infinite port-busy loop)
  • restart_count + last_restart_time in /status
  • Watchdog escalation: if bot_running stays False > 3 min → also flag
  • Cleaner start() idempotency guard
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify

logger = logging.getLogger("keep_alive")

# ── Thread-safe shared state ──────────────────────────────────────────────────
_lock = threading.Lock()

_state: dict = {
    "bot_running":       False,
    "last_heartbeat":    None,        # datetime UTC — written by main asyncio loop
    "restart_requested": False,
    "restart_count":     0,
    "last_restart_at":   None,
    "start_time":        datetime.now(timezone.utc),
}


# ── Public state accessors (all lock-protected) ───────────────────────────────

def get_state() -> dict:
    """Return a *copy* of the state dict — safe to read from any thread."""
    with _lock:
        return dict(_state)


def set_bot_running(value: bool) -> None:
    with _lock:
        _state["bot_running"] = value


def heartbeat() -> None:
    """Call from the bot loop every ~20 s to prove it is alive."""
    with _lock:
        _state["last_heartbeat"] = datetime.now(timezone.utc)


def request_restart(reason: str = "watchdog") -> None:
    with _lock:
        if not _state["restart_requested"]:   # avoid duplicate increments
            _state["restart_requested"] = True
            _state["restart_count"]    += 1
            _state["last_restart_at"]   = datetime.now(timezone.utc).isoformat()
            logger.warning("Restart requested — reason: %s (total: %d)",
                           reason, _state["restart_count"])


def clear_restart_flag() -> bool:
    """Atomically check-and-clear restart_requested. Returns True if it was set."""
    with _lock:
        if _state["restart_requested"]:
            _state["restart_requested"] = False
            return True
        return False


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.ERROR)          # suppress Flask access-log noise


@app.route("/")
def index():
    s = get_state()
    now = datetime.now(timezone.utc)
    uptime = (now - s["start_time"]).total_seconds()
    last_hb = s["last_heartbeat"]
    hb_ago = round((now - last_hb).total_seconds(), 1) if last_hb else None
    return jsonify({
        "status":                    "alive",
        "bot_running":               s["bot_running"],
        "uptime_seconds":            round(uptime),
        "last_heartbeat_seconds_ago": hb_ago,
        "restart_count":             s["restart_count"],
        "last_restart_at":           s["last_restart_at"],
        "timestamp":                 now.isoformat(),
    })


@app.route("/health")
def health():
    """Minimal endpoint for UptimeRobot / cron-job.org."""
    return jsonify({"ok": True}), 200


@app.route("/restart", methods=["POST"])
def trigger_restart():
    """Manual restart trigger — POST /restart."""
    request_restart(reason="manual HTTP request")
    return jsonify({"restarting": True}), 202


# ── Flask runner ──────────────────────────────────────────────────────────────

def _run_flask() -> None:
    port = int(os.environ.get("PORT", 8080))
    logger.info("Flask keep-alive server starting on port %s", port)
    consecutive_port_errors = 0

    while True:
        try:
            app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
        except OSError as exc:
            # Port already in use — try alternate port once, then give up
            msg = str(exc).lower()
            if "address already in use" in msg or "98" in msg or "10048" in msg:
                consecutive_port_errors += 1
                if consecutive_port_errors == 1:
                    port += 1
                    logger.warning("Port busy — trying port %s.", port)
                else:
                    logger.critical(
                        "Flask: cannot bind to any port after %d tries — "
                        "keep-alive disabled. Fix port conflict.",
                        consecutive_port_errors,
                    )
                    return          # exit thread; bot still runs, just no HTTP server
            else:
                logger.error("Flask OSError: %s — retrying in 5 s", exc)
            time.sleep(5)
        except Exception as exc:
            consecutive_port_errors = 0
            logger.error("Flask crashed: %s — restarting in 5 s", exc)
            time.sleep(5)


# ── Health watchdog ───────────────────────────────────────────────────────────
_HEARTBEAT_TIMEOUT   = 90    # s — stale heartbeat → bot is stuck
_NOT_RUNNING_TIMEOUT = 180   # s — bot_running=False for too long → restart
_CHECK_INTERVAL      = 30    # s between watchdog ticks

# Tracks when bot_running last became False so we don't use start_time heuristic
_not_running_since: datetime | None = None


def _watchdog() -> None:
    global _not_running_since

    logger.info(
        "Watchdog started — check every %s s, heartbeat timeout %s s.",
        _CHECK_INTERVAL, _HEARTBEAT_TIMEOUT,
    )
    time.sleep(20)   # let bot finish its startup before first check

    while True:
        try:
            s = get_state()
            now = datetime.now(timezone.utc)

            if s["bot_running"]:
                _not_running_since = None          # reset whenever bot is running

                last_hb = s["last_heartbeat"]
                if last_hb is None:
                    logger.warning("Watchdog: bot_running=True but no heartbeat received yet.")
                else:
                    age = (now - last_hb).total_seconds()
                    if age > _HEARTBEAT_TIMEOUT:
                        logger.error(
                            "Watchdog: heartbeat %.0f s old (limit %s s) — requesting restart.",
                            age, _HEARTBEAT_TIMEOUT,
                        )
                        request_restart(reason=f"heartbeat stale ({age:.0f}s)")

            else:
                # [FIX review#3] Track elapsed time since bot stopped, not since process start.
                # This catches the case where bot ran fine, then stopped and never restarted.
                if _not_running_since is None:
                    _not_running_since = now
                    logger.debug("Watchdog: bot_running=False — started idle timer.")
                else:
                    idle = (now - _not_running_since).total_seconds()
                    if idle > _NOT_RUNNING_TIMEOUT:
                        logger.error(
                            "Watchdog: bot_running=False for %.0f s — requesting restart.",
                            idle,
                        )
                        request_restart(reason=f"not running for {idle:.0f}s")
                        _not_running_since = now   # reset so we don't spam

        except Exception as exc:
            logger.error("Watchdog internal error: %s", exc, exc_info=True)

        time.sleep(_CHECK_INTERVAL)


# ── Public entry-point ────────────────────────────────────────────────────────
_flask_thread:    threading.Thread | None = None
_watchdog_thread: threading.Thread | None = None
_started = False


def start() -> None:
    """Start Flask + watchdog threads. Idempotent — safe to call multiple times."""
    global _flask_thread, _watchdog_thread, _started

    if _started and (
        (_flask_thread and _flask_thread.is_alive()) and
        (_watchdog_thread and _watchdog_thread.is_alive())
    ):
        return   # already running

    _started = True

    if not (_flask_thread and _flask_thread.is_alive()):
        _flask_thread = threading.Thread(
            target=_run_flask, name="flask-keepalive", daemon=True,
        )
        _flask_thread.start()
        logger.info("Flask thread started.")

    if not (_watchdog_thread and _watchdog_thread.is_alive()):
        _watchdog_thread = threading.Thread(
            target=_watchdog, name="health-watchdog", daemon=True,
        )
        _watchdog_thread.start()
        logger.info("Watchdog thread started.")
