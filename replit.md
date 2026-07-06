# Matrix-Family Telegram Bot — v4.0

## Project Overview

A production-ready Telegram bot for the Matrix-Family community with:
- **Reply keyboard navigation** (bottom keyboard, no commands except /start)
- **17-rank military ladder** from Members → Staff Leader
- **Points economy**: earn by activities, spend on rank upgrades
- **Point request flow**: users submit proof → admins approve/reject → points/ranks applied
- **Betting system**: P2P wagering by username
- **Leaderboard**: sorted by points, auto-rewards Top 3 every 22 days
- **Admin panel**: add/remove admins, broadcast, set rank/points, ban/remove users, backup/restore
- **SQLite persistence** with automatic backup support
- **Self-healing polling loop**: retries on network errors, conflicts, and unknown exceptions

## Stack

- Python 3.11
- python-telegram-bot v21.6 (async, PTB)
- Flask 3.0.3 (keep-alive / health endpoint)
- SQLite (family.db)

## Running

Set the `BOT_TOKEN` secret (from @BotFather), then the "Telegram Bot" workflow starts automatically.

```
python3 main.py
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | All bot logic: handlers, DB, state machine, self-healing loop |
| `keep_alive.py` | Flask server for UptimeRobot pings + watchdog heartbeat |
| `family.db` | SQLite database (auto-created on first run) |
| `backups/` | DB backup files (created via admin panel) |
| `bot.log` | Rotating log file |

## First-time Setup

1. Add `BOT_TOKEN` to Replit Secrets
2. Click Run (or restart the "Telegram Bot" workflow)
3. Send `/start` to the bot in Telegram
4. The first user to send `/start` automatically becomes the Owner (👑)

## Rank System

Members → Bronze (50P) → Silver (70P) → Gold (100P) → Diamond (120P) → Sentry (150P) →
Soldier (170P) → Grenadier (200P) → Sergeant (240P) → Colonel (300P) → Lieutenant (350P) →
Ranger (400P) → Fusilier (450P) → Gunner (550P) → Marine (650P) → Major (750P) →
Brigadier (900P) → Staff Leader (manual, Owner decides)

## User Preferences

- Persian-first UI, military rank names in English
- Reply keyboard buttons (no inline keyboard for main navigation)
- Every menu has ⬅️ بازگشت and 🏠 منوی اصلی
- Owner panel visible to admins too; destructive actions (ban, remove, reset) restricted to Owner
