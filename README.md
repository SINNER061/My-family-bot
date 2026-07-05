# 🤖 Self-Healing Telegram Bot

A production-ready, always-on Telegram bot built with **python-telegram-bot v21**, designed to run on **Replit** with near-zero downtime.

---

## ✨ Features

| Feature | Details |
|---|---|
| **Self-healing** | Catches every exception, logs it, restarts automatically |
| **Exponential back-off** | Retry delay doubles on repeated failures (max 120 s) |
| **Health watchdog** | Background thread checks heartbeat every 30 s |
| **Keep-alive server** | Flask endpoint answers UptimeRobot / cron-job.org pings |
| **Rotating logs** | `bot.log` — max 5 MB × 3 backups, also prints to console |
| **Graceful shutdown** | Handles SIGTERM and KeyboardInterrupt cleanly |
| **Rate-limit aware** | Respects `RetryAfter` headers from Telegram |

---

## 📁 Project Structure

```
.
├── main.py          ← Entry point; self-healing outer loop + all bot handlers
├── keep_alive.py    ← Flask server + health watchdog thread
├── requirements.txt ← Python dependencies
├── .gitignore       ← Excludes secrets, logs, caches
├── README.md        ← This file
└── bot.log          ← Runtime log (git-ignored)
```

---

## 🚀 Setup on Replit

### 1 — Fork / Import

Click **Use Template** or import this repository directly:

> Replit → **Create Repl** → **Import from GitHub** → paste repo URL

### 2 — Set the Bot Token Secret

1. In Replit, open **Tools → Secrets** (🔒).
2. Add a new secret:
   - **Key:** `BOT_TOKEN`
   - **Value:** your token from [@BotFather](https://t.me/BotFather)

> ⚠️ **Never** put your token in code or commit it to Git.

### 3 — Configure the Run button

In Replit's `.replit` file (or **Run** settings), set:

```
run = "python main.py"
```

Or create a Shell command:

```bash
pip install -r requirements.txt && python main.py
```

### 4 — Click Run ▶️

The bot starts, Flask server comes up on port `8080`, and the health watchdog kicks in after 15 s.

---

## 🏓 Keep Alive with UptimeRobot

Replit free-tier Repls sleep after ~30 minutes of inactivity.  
Set up a free monitor on [UptimeRobot](https://uptimerobot.com) or [cron-job.org](https://cron-job.org) to ping the Flask server every **30–60 seconds**.

### Get your Replit URL

In the **Webview** tab, copy the URL — it looks like:

```
https://<repl-name>.<username>.repl.co
```

### UptimeRobot settings

| Field | Value |
|---|---|
| Monitor type | HTTP(s) |
| URL | `https://<your-repl>.repl.co/health` |
| Interval | 30 seconds |

### Available endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Full status JSON (uptime, heartbeat, bot_running) |
| `/health` | GET | Simple `{"ok": true}` — use this for UptimeRobot |
| `/restart` | POST | Trigger a manual bot restart |

---

## 🏗️ Architecture

```
Replit process
│
├─ main.py (main thread)
│   └─ run_forever()                ← outer while-loop (self-healing)
│       └─ asyncio.run(run_bot_once())
│           ├─ Telegram polling     ← python-telegram-bot Application
│           └─ heartbeat_task()     ← async task, ticks every 20 s
│
├─ Flask thread (daemon)            ← keep_alive._run_flask()
│   └─ answers HTTP pings
│
└─ Watchdog thread (daemon)         ← keep_alive._watchdog()
    └─ checks heartbeat every 30 s
    └─ sets restart_requested=True if bot is stuck
```

---

## 🔧 Customisation

### Add a new command

In `main.py`, add a handler function and register it:

```python
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong!")

# Inside build_application():
app.add_handler(CommandHandler("ping", cmd_ping))
```

### Change watchdog sensitivity

In `keep_alive.py`:

```python
_HEARTBEAT_TIMEOUT = 90   # seconds — raise if your bot is slow to process
_CHECK_INTERVAL    = 30   # seconds between watchdog checks
```

### Change back-off limits

In `main.py`:

```python
_BASE_RETRY_DELAY = 5    # initial wait (seconds) after a crash
_MAX_RETRY_DELAY  = 120  # maximum wait (seconds)
```

---

## 📦 GitHub Workflow

### First push

```bash
git init
git add .
git commit -m "feat: initial self-healing bot"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

### Update after changes

```bash
git add .
git commit -m "fix: describe what you changed"
git push
```

### Pull latest on Replit

```bash
git pull origin main
```

---

## 📋 Logs

Logs are written to both the console and `bot.log` (rotated automatically).  
`bot.log` is in `.gitignore` — never committed.

```
2024-01-01 12:00:00 | INFO     | bot       | ════ Self-Healing Telegram Bot — starting
2024-01-01 12:00:00 | INFO     | keep_alive| Flask keep-alive server starting on port 8080
2024-01-01 12:00:01 | INFO     | bot       | Bot is polling. Press Ctrl+C to stop.
```

---

## ⚠️ Troubleshooting

| Problem | Solution |
|---|---|
| `BOT_TOKEN is not set` | Add `BOT_TOKEN` in Replit → Tools → Secrets |
| Bot not responding | Check `bot.log` or Replit console for errors |
| Port already in use | Change `PORT` env var in Replit Secrets |
| UptimeRobot failing | Make sure the Repl is running and URL is correct |
| `Conflict: terminated by other getUpdates` | Only one instance should run — stop duplicate Repls |

---

## 📜 License

MIT — use freely, attribution appreciated.
