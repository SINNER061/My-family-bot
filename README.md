# 🤖 Self-Healing Telegram Bot — v2.0

A production-grade, always-on Telegram bot for **Replit** built with **python-telegram-bot v21** and full asyncio support.  
Designed to survive crashes, network drops, Telegram rate-limits, and Replit sleep cycles — automatically.

---

## ✅ What's Fixed in v2.0

| # | Bug | Fix |
|---|-----|-----|
| 1 | `asyncio.run()` in restart loop → "Event loop is closed" errors | Each attempt uses `new_event_loop()` + explicit cleanup |
| 2 | Conflict (409) — two instances cause retry storm | Detected, waits 35 s, restarts cleanly |
| 3 | `InvalidToken` causes infinite retry | Detected immediately → `sys.exit(1)` with clear message |
| 4 | `update.message` / `effective_user` = None → AttributeError crash | All handlers guard with `if not update.message: return` |
| 5 | `SystemExit` / `KeyboardInterrupt` caught by generic except | Handled separately; propagates correctly |
| 6 | `heartbeat_task` dies silently on any error | Wrapped in try/except; logs and continues |
| 7 | Infinite crash-loop with no escape | `MAX_CONSECUTIVE_FAILS = 20` safety valve |
| 8 | `poll_interval` conflicts with long-poll timeout | Removed from `start_polling`; set only on builder |
| 9 | Race condition in `_state` dict (multi-thread) | `threading.Lock` on all reads/writes |
| 10 | Flask retries forever on port-busy error | OSError detected; tries next port, then exits thread |

---

## 📁 Project Structure

```
.
├── main.py          ← Entry point: self-healing loop + all bot handlers
├── keep_alive.py    ← Flask server (UptimeRobot pings) + health watchdog
├── requirements.txt ← Python dependencies
├── .gitignore       ← Excludes .env, logs, __pycache__, etc.
├── README.md        ← This file
└── bot.log          ← Runtime log — auto-rotated, git-ignored
```

---

## 🏗️ Architecture

```
Replit process
│
├─ main.py  (main thread)
│   └─ _run_forever()                ← outer while-loop with back-off
│       └─ loop.run_until_complete(_run_bot_once())
│           ├─ Telegram polling      ← Application + Updater (PTB v21)
│           └─ _heartbeat_task()     ← async task, ticks every 20 s
│
├─ Flask thread (daemon)             ← keep_alive._run_flask()
│   └─ / · /health · /restart
│
└─ Watchdog thread (daemon)          ← keep_alive._watchdog()
    ├─ checks heartbeat every 30 s
    └─ sets restart_requested if stuck or silent > 90 s
```

**Restart flow:**
1. Watchdog sets `restart_requested = True`
2. Main loop wakes, calls `_stop_application()` + `shutdown()`
3. Event loop closed and recreated cleanly
4. New `Application` built and polling resumes

---

## 🚀 Setup on Replit

### Step 1 — Import the repository

> **Create Repl → Import from GitHub** → paste the repo URL → click Import

### Step 2 — Set your Bot Token (Secret)

1. Open **Tools → Secrets** (🔒 icon in sidebar)
2. Click **+ New Secret**
   - **Key:** `BOT_TOKEN`
   - **Value:** your token from [@BotFather](https://t.me/BotFather)

> ⚠️ Never put your token directly in code or commit it to Git.

### Step 3 — Set the Run command

In **`.replit`** file or the Run button settings:
```
run = "python main.py"
```

Or in the Shell tab before clicking Run:
```bash
pip install -r requirements.txt
python main.py
```

### Step 4 — Click ▶️ Run

Expected startup output:
```
2024-01-01 12:00:00 | INFO     | bot       | ════ Self-Healing Telegram Bot — v2.0 starting ════
2024-01-01 12:00:00 | INFO     | keep_alive| Flask keep-alive server starting on port 8080
2024-01-01 12:00:00 | INFO     | keep_alive| Watchdog started — check every 30 s, heartbeat timeout 90 s.
2024-01-01 12:00:01 | INFO     | bot       | ━━━ Bot start attempt #1 (consecutive_fails=0) ━━━
2024-01-01 12:00:02 | INFO     | bot       | Bot is now polling. Ctrl+C to stop.
```

---

## 🏓 Keep Alive with UptimeRobot

Free-tier Repls sleep after ~30 minutes of inactivity. Prevent this:

1. Go to [uptimerobot.com](https://uptimerobot.com) → **Add New Monitor**
2. Settings:

| Field | Value |
|-------|-------|
| Monitor Type | HTTP(s) |
| Friendly Name | My Telegram Bot |
| URL | `https://<your-repl>.<username>.repl.co/health` |
| Monitoring Interval | 30 seconds |

### Available HTTP endpoints

| Endpoint | Method | Response |
|----------|--------|----------|
| `/health` | GET | `{"ok": true}` — use this for UptimeRobot |
| `/` | GET | Full JSON status (uptime, heartbeat age, restart count) |
| `/restart` | POST | Force an immediate bot restart |

**Find your Replit URL:** look at the **Webview** tab — copy the URL shown there.

---

## ⚙️ Configuration Reference

### main.py

```python
_BASE_RETRY_DELAY      = 5    # seconds — initial back-off after crash
_MAX_RETRY_DELAY       = 120  # seconds — maximum back-off ceiling
_CONFLICT_WAIT         = 35   # seconds — wait after a 409 Conflict error
_MAX_CONSECUTIVE_FAILS = 20   # give up after this many back-to-back failures
```

### keep_alive.py

```python
_HEARTBEAT_TIMEOUT   = 90    # seconds — no heartbeat → request restart
_NOT_RUNNING_TIMEOUT = 180   # seconds — bot never started → request restart
_CHECK_INTERVAL      = 30    # seconds — between watchdog ticks
```

---

## 🛠️ Adding Your Own Commands

```python
# In main.py, add a handler function:
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text("pong! 🏓")

# Then register it inside _build_application():
app.add_handler(CommandHandler("ping", cmd_ping))
```

---

## 📦 GitHub Workflow

### First push (already done via Replit)
```bash
git init
git add .
git commit -m "feat: self-healing bot v2.0"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

### Update after changes
```bash
git add .
git commit -m "fix: describe what changed"
git push
```

### Pull latest into Replit Shell
```bash
git pull origin main
pip install -r requirements.txt   # if dependencies changed
```

---

## 📋 Reading Logs

Logs print to both console and `bot.log` (rotated at 5 MB, 3 backups kept).

```bash
# Live tail in Replit Shell
tail -f bot.log

# Last 50 lines
tail -50 bot.log

# Search for errors only
grep ERROR bot.log
```

---

## ⚠️ Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `BOT_TOKEN is not set` | Secret missing | Tools → Secrets → add `BOT_TOKEN` |
| `InvalidToken` in logs | Wrong token | Re-generate from @BotFather |
| `Conflict (409)` repeating | Two Repls running | Stop the other Repl/instance |
| Bot not responding to messages | Check `bot.log` for errors | Run `grep ERROR bot.log` in Shell |
| UptimeRobot showing down | Repl crashed or not started | Check console, click Run again |
| Port already in use | Previous process still running | Kill it: `pkill -f main.py` |
| `MAX_CONSECUTIVE_FAILS reached` | Persistent crash | Fix the root error, restart Repl |

---

## 📜 License

MIT — free to use, attribution appreciated.
