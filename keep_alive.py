"""
Keep-Alive server + Watchdog for Matrix-Family Bot
Flask HTTP server on port 8000 — ping /health every 5 min via UptimeRobot
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from flask import Flask, jsonify

log = logging.getLogger("keep_alive")

app = Flask(__name__)

# ── Shared state (thread-safe) ────────────────────────────────────
_lock = threading.Lock()
_bot_started_at: Optional[float] = None
_last_activity: float = time.time()
_start_time: float = time.time()


def record_activity() -> None:
    global _last_activity
    with _lock:
        _last_activity = time.time()


def set_bot_started() -> None:
    global _bot_started_at
    with _lock:
        _bot_started_at = time.time()


# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "bot": "Matrix-Family Bot v3.0",
        "time": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/health")
def health():
    with _lock:
        uptime = time.time() - _start_time
        idle = time.time() - _last_activity
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    return jsonify({
        "status": "healthy",
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
        "idle_seconds": round(idle),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


# ── Flask thread ──────────────────────────────────────────────────
def _run_flask() -> None:
    port = int(os.environ.get("BOT_PORT", 8000))
    log.info("Keep-alive server starting on port %d …", port)
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except OSError as e:
        log.error("Flask failed to bind port %d: %s", port, e)


# ── Watchdog thread ───────────────────────────────────────────────
def _watchdog() -> None:
    """Logs a heartbeat every 60 s so Replit keeps the process alive."""
    while True:
        with _lock:
            idle = time.time() - _last_activity
            uptime = time.time() - _start_time
        log.info("♥ Heartbeat — uptime %.0fs | idle %.0fs", uptime, idle)
        time.sleep(60)


# ── Public API ────────────────────────────────────────────────────
def start_keep_alive() -> None:
    """Start Flask + watchdog in daemon threads (call once at bot startup)."""
    threading.Thread(target=_run_flask, daemon=True, name="flask").start()
    threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()
    log.info("Keep-alive threads started.")
