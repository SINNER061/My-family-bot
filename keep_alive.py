"""
keep_alive.py — Flask keep-alive server + bot health monitor.

Runs two background threads:
  1. Flask web server  → answers UptimeRobot / cron-job.org pings.
  2. Health watchdog   → restarts the bot if polling silently dies.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("keep_alive")

# ── Shared state (written by main.py, read here) ──────────────────────────────
_state: dict = {
    "bot_running": False,
    "last_heartbeat": None,   # datetime (UTC) set by main loop
    "restart_requested": False,
    "start_time": datetime.now(timezone.utc),
}


def get_state() -> dict:
    return _state


def set_bot_running(value: bool) -> None:
    _state["bot_running"] = value


def heartbeat() -> None:
    """Call from bot main-loop every iteration to prove it's alive."""
    _state["last_heartbeat"] = datetime.now(timezone.utc)


def request_restart() -> None:
    _state["restart_requested"] = True


def clear_restart_flag() -> bool:
    """Returns True (and clears the flag) if a restart was requested."""
    if _state["restart_requested"]:
        _state["restart_requested"] = False
        return True
    return False


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    uptime = (datetime.now(timezone.utc) - _state["start_time"]).total_seconds()
    last_hb = _state["last_heartbeat"]
    hb_ago = (
        round((datetime.now(timezone.utc) - last_hb).total_seconds(), 1)
        if last_hb else None
    )
    return jsonify(
        {
            "status": "alive",
            "bot_running": _state["bot_running"],
            "uptime_seconds": round(uptime),
            "last_heartbeat_seconds_ago": hb_ago,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/health")
def health():
    return jsonify({"ok": True}), 200


@app.route("/restart", methods=["POST"])
def trigger_restart():
    """Optional manual endpoint — e.g. POST /restart to force a bot restart."""
    request_restart()
    logger.warning("Manual restart requested via /restart endpoint.")
    return jsonify({"restarting": True}), 202


def _run_flask() -> None:
    port = int(os.environ.get("PORT", 8080))
    logger.info("Flask keep-alive server starting on port %s", port)
    while True:
        try:
            # use_reloader=False is critical inside a thread
            app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
        except Exception as exc:
            logger.error("Flask server crashed: %s — restarting in 5 s", exc)
            time.sleep(5)


# ── Health watchdog ───────────────────────────────────────────────────────────
_HEARTBEAT_TIMEOUT = 90   # seconds — if no heartbeat, assume bot is stuck
_CHECK_INTERVAL    = 30   # seconds between watchdog checks


def _watchdog() -> None:
    """Periodically checks bot liveness and sets restart_requested when needed."""
    logger.info("Health watchdog started (check every %s s, timeout %s s).",
                _CHECK_INTERVAL, _HEARTBEAT_TIMEOUT)
    time.sleep(15)  # give the bot time to start before first check

    while True:
        try:
            if _state["bot_running"]:
                last_hb = _state["last_heartbeat"]
                if last_hb is None:
                    logger.warning("Watchdog: bot_running=True but no heartbeat yet.")
                else:
                    age = (datetime.now(timezone.utc) - last_hb).total_seconds()
                    if age > _HEARTBEAT_TIMEOUT:
                        logger.error(
                            "Watchdog: last heartbeat %.0f s ago — requesting restart.",
                            age,
                        )
                        request_restart()
            else:
                logger.debug("Watchdog: bot is not running yet, skipping check.")
        except Exception as exc:
            logger.error("Watchdog error: %s", exc)

        time.sleep(_CHECK_INTERVAL)


# ── Public entry-point ────────────────────────────────────────────────────────
_flask_thread:    threading.Thread | None = None
_watchdog_thread: threading.Thread | None = None


def start() -> None:
    """Start Flask + watchdog threads (idempotent — safe to call multiple times)."""
    global _flask_thread, _watchdog_thread

    if _flask_thread is None or not _flask_thread.is_alive():
        _flask_thread = threading.Thread(target=_run_flask, name="flask-keepalive", daemon=True)
        _flask_thread.start()
        logger.info("Flask thread started.")

    if _watchdog_thread is None or not _watchdog_thread.is_alive():
        _watchdog_thread = threading.Thread(target=_watchdog, name="health-watchdog", daemon=True)
        _watchdog_thread.start()
        logger.info("Watchdog thread started.")
