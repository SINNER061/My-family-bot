"""
Keep-Alive server + Watchdog for Matrix-Family Bot v4.0
Flask HTTP server — ping / or /health every 5 min via UptimeRobot
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify

log = logging.getLogger("keep_alive")

app = Flask(__name__)

# ── Shared state (thread-safe) ────────────────────────────────────
_lock = threading.Lock()
_last_activity: float = time.time()
_start_time: float = time.time()


def record_activity() -> None:
    global _last_activity
    with _lock:
        _last_activity = time.time()


# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Root path — plain text 200 for UptimeRobot / uptime monitors."""
    return Response("Matrix-Family Bot is running.", status=200, mimetype="text/plain")


@app.route("/health")
def health():
    with _lock:
        uptime = time.time() - _start_time
        idle = time.time() - _last_activity
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    return jsonify({
        "status": "healthy",
        "bot": "Matrix-Family Bot v4.0",
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
        "idle_seconds": round(idle),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/ping")
def ping():
    return Response("pong", status=200, mimetype="text/plain")


# ── Flask thread ──────────────────────────────────────────────────
def _run_flask() -> None:
    # Use PORT env var (Replit assigns it); fall back to 8000 for local/uptime-robot
    port = int(os.environ.get("PORT", os.environ.get("BOT_PORT", 8000)))
    log.info("Keep-alive server starting on port %d …", port)
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except OSError as e:
        # Port busy — try 8080 as fallback
        log.warning("Port %d busy (%s), trying 8080…", port, e)
        try:
            app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
        except OSError as e2:
            log.error("Flask failed to bind any port: %s", e2)


# ── Watchdog thread ───────────────────────────────────────────────
def _watchdog() -> None:
    """Heartbeat every 60 s — keeps Replit process alive and logs uptime."""
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
