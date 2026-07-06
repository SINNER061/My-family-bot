"""
Matrix-Family Telegram Bot — v4.0
Persian-first · Reply Keyboards · Rank System · Points · Betting · Admin Panel · SQLite
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from telegram import (
    BotCommand,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.error import Conflict, InvalidToken, NetworkError

from keep_alive import start_keep_alive

# ─────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bot")

# ─────────────────────────── Config ─────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
DB_PATH = Path("family.db")
BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)
_start_time = time.time()

# ─────────────────────────── Ranks ──────────────────────────────
RANKS = [
    "Members", "Bronze", "Silver", "Gold", "Diamond",
    "Sentry", "Soldier", "Grenadier", "Sergeant", "Colonel",
    "Lieutenant", "Ranger", "Fusilier", "Gunner", "Marine",
    "Major", "Brigadier", "Staff Leader",
]
# Cost in points to advance TO this rank from the previous one (0 = not purchasable)
RANK_COSTS = [0, 50, 70, 100, 120, 150, 170, 200, 240, 300, 350, 400, 450, 550, 650, 750, 900, 0]
MAX_AUTO_RANK = 16  # Brigadier is highest auto-purchasable rank; Staff Leader needs manual approval
RANK_TABLE = (
    "📜 <b>رنک‌های Matrix-Family</b>\n\n"
    "⚔ Members → Bronze        <b>50P</b>\n"
    "⚔ Bronze → Silver          <b>70P</b>\n"
    "⚔ Silver → Gold            <b>100P</b>\n"
    "⚔ Gold → Diamond           <b>120P</b>\n"
    "⚔ Diamond → Sentry         <b>150P</b>\n"
    "⚔ Sentry → Soldier         <b>170P</b>\n"
    "⚔ Soldier → Grenadier      <b>200P</b>\n"
    "⚔ Grenadier → Sergeant     <b>240P</b>\n"
    "⚔ Sergeant → Colonel       <b>300P</b>\n"
    "⚔ Colonel → Lieutenant     <b>350P</b>\n"
    "⚔ Lieutenant → Ranger      <b>400P</b>\n"
    "⚔ Ranger → Fusilier        <b>450P</b>\n"
    "⚔ Fusilier → Gunner        <b>550P</b>\n"
    "⚔ Gunner → Marine          <b>650P</b>\n"
    "⚔ Marine → Major           <b>750P</b>\n"
    "⚔ Major → Brigadier        <b>900P</b>\n"
    "⚔ Brigadier → Staff Leader  <i>تصمیم مالک</i>"
)

# ─────────────────────────── Activities ─────────────────────────
ACTIVITIES = [
    {"name": "Member From Faction",          "points": 50,  "bonus_ranks": 0},
    {"name": "Manager From Faction",         "points": 100, "bonus_ranks": 1},
    {"name": "Sub From Faction",             "points": 120, "bonus_ranks": 1},
    {"name": "Gun 1K",                       "points": 30,  "bonus_ranks": 0},
    {"name": "Top Week In Faction",          "points": 80,  "bonus_ranks": 1},
    {"name": "Top Sub In Season",            "points": 180, "bonus_ranks": 2},
    {"name": "Top Leader In Season",         "points": 300, "bonus_ranks": 2},
    {"name": "Family Meeting Participation", "points": 50,  "bonus_ranks": 0},
    {"name": "Family Event Winner",          "points": 40,  "bonus_ranks": 0},
    {"name": "Matrix Family Advertisement",  "points": 30,  "bonus_ranks": 0},
]

# ─────────────────────────── States ─────────────────────────────
(
    IDLE,
    REQ_ACTIVITY,
    REQ_PROOF,
    BET_USERNAME,
    BET_AMOUNT,
    ADM_ADD_ADMIN,
    ADM_REMOVE_ADMIN,
    ADM_BROADCAST,
    ADM_SET_RANK_USER,
    ADM_SET_RANK_VALUE,
    ADM_SET_POINTS_USER,
    ADM_SET_POINTS_VALUE,
    ADM_BAN_USER,
    ADM_REMOVE_USER,
    ADM_RESTORE,
) = range(15)

# ─────────────────────────── Button text ────────────────────────
B_PROFILE    = "👤 پروفایل"
B_REQ        = "⭐ درخواست امتیاز"
B_UPGRADE    = "🎖 ارتقای رنک"
B_LEADER     = "🏆 لیدربرد"
B_BET        = "🎲 شرط‌بندی"
B_RANKS      = "📜 رنک‌های فمیلی"
B_ABOUT      = "ℹ️ About"
B_ADMIN      = "⚙️ پنل مدیریت"
B_BACK       = "⬅️ بازگشت"
B_HOME       = "🏠 منوی اصلی"
B_ADD_ADM    = "➕ Add Admin"
B_REM_ADM    = "➖ Remove Admin"
B_BROADCAST  = "📢 Broadcast"
B_STATS      = "📊 Statistics"
B_BACKUP     = "💾 Backup"
B_RESTORE    = "♻️ Restore Backup"
B_SET_RANK   = "👤 Set User Rank"
B_SET_PTS    = "⭐ Set User Points"
B_BAN        = "🚫 Ban User"
B_REM_USER   = "🗑 Remove User"
B_RESET_PTS  = "🔄 Reset All Points"

# ─────────────────────────── Database ───────────────────────────
def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with get_db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT    DEFAULT '',
            first_name      TEXT    DEFAULT '',
            rank_idx        INTEGER DEFAULT 0,
            points          INTEGER DEFAULT 0,
            is_admin        INTEGER DEFAULT 0,
            is_banned       INTEGER DEFAULT 0,
            total_requests  INTEGER DEFAULT 0,
            approved_req    INTEGER DEFAULT 0,
            rejected_req    INTEGER DEFAULT 0,
            joined_at       TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS point_requests (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            activity_idx   INTEGER NOT NULL,
            file_id        TEXT    NOT NULL,
            file_type      TEXT    NOT NULL,
            status         TEXT    DEFAULT 'pending',
            reviewer_id    INTEGER,
            review_at      TEXT,
            reject_reason  TEXT,
            created_at     TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            player_a   INTEGER NOT NULL,
            player_b   INTEGER NOT NULL,
            amount     INTEGER NOT NULL,
            status     TEXT    DEFAULT 'pending',
            winner     INTEGER,
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS leaderboard_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_at    TEXT,
            snapshot    TEXT
        );
        CREATE TABLE IF NOT EXISTS admin_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id   INTEGER,
            action     TEXT,
            target_id  INTEGER,
            detail     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)
    log.info("Database initialised at %s", DB_PATH)


# ── Settings ──────────────────────────────────────────────────────
def get_setting(key: str) -> Optional[str]:
    with get_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def get_owner_id() -> Optional[int]:
    v = get_setting("owner_id")
    return int(v) if v else None


def claim_owner_atomic(user_id: int) -> bool:
    with get_db() as con:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('owner_id',?)", (str(user_id),))
        row = con.execute("SELECT value FROM settings WHERE key='owner_id'").fetchone()
        return row is not None and int(row["value"]) == user_id


# ── Users ─────────────────────────────────────────────────────────
def upsert_user(user) -> None:
    with get_db() as con:
        con.execute("""
            INSERT INTO users(user_id, username, first_name)
            VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user.id, user.username or "", user.first_name or ""))


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    username = username.lstrip("@").lower()
    with get_db() as con:
        return con.execute("SELECT * FROM users WHERE LOWER(username)=?", (username,)).fetchone()


def get_all_users() -> list:
    with get_db() as con:
        return con.execute("SELECT * FROM users ORDER BY joined_at DESC").fetchall()


def get_leaderboard(limit: int = 20) -> list:
    with get_db() as con:
        return con.execute(
            "SELECT * FROM users WHERE is_banned=0 ORDER BY points DESC, rank_idx DESC LIMIT ?",
            (limit,)
        ).fetchall()


def add_points(user_id: int, amount: int) -> int:
    with get_db() as con:
        con.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, user_id))
        row = con.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["points"]


def set_points(user_id: int, amount: int) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET points=? WHERE user_id=?", (amount, user_id))


def set_rank(user_id: int, rank_idx: int) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET rank_idx=? WHERE user_id=?", (rank_idx, user_id))


def set_admin(user_id: int, state: bool) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET is_admin=? WHERE user_id=?", (1 if state else 0, user_id))


def set_banned(user_id: int, state: bool) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if state else 0, user_id))


def remove_user(user_id: int) -> None:
    with get_db() as con:
        con.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM point_requests WHERE user_id=?", (user_id,))


def reset_all_points() -> None:
    with get_db() as con:
        con.execute("UPDATE users SET points=0")


def inc_total_requests(user_id: int) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET total_requests=total_requests+1 WHERE user_id=?", (user_id,))


# ── Point requests ────────────────────────────────────────────────
def create_request(user_id: int, activity_idx: int, file_id: str, file_type: str) -> int:
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO point_requests(user_id,activity_idx,file_id,file_type) VALUES(?,?,?,?)",
            (user_id, activity_idx, file_id, file_type),
        )
        return cur.lastrowid


def get_request(req_id: int) -> Optional[sqlite3.Row]:
    with get_db() as con:
        return con.execute("SELECT * FROM point_requests WHERE id=?", (req_id,)).fetchone()


def update_request(req_id: int, status: str, reviewer_id: int, reject_reason: str = "") -> None:
    with get_db() as con:
        con.execute(
            "UPDATE point_requests SET status=?,reviewer_id=?,review_at=datetime('now'),reject_reason=? WHERE id=?",
            (status, reviewer_id, reject_reason, req_id),
        )


# ── Bets ──────────────────────────────────────────────────────────
def create_bet(player_a: int, player_b: int, amount: int) -> int:
    with get_db() as con:
        cur = con.execute(
            "INSERT INTO bets(player_a,player_b,amount) VALUES(?,?,?)",
            (player_a, player_b, amount),
        )
        return cur.lastrowid


def get_bet(bet_id: int) -> Optional[sqlite3.Row]:
    with get_db() as con:
        return con.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()


def update_bet(bet_id: int, status: str, winner: Optional[int] = None) -> None:
    with get_db() as con:
        con.execute("UPDATE bets SET status=?,winner=? WHERE id=?", (status, winner, bet_id))


def has_pending_bet(user_id: int) -> bool:
    with get_db() as con:
        row = con.execute(
            "SELECT id FROM bets WHERE (player_a=? OR player_b=?) AND status='pending'",
            (user_id, user_id),
        ).fetchone()
        return row is not None


# ── Leaderboard history ───────────────────────────────────────────
def save_leaderboard_snapshot() -> None:
    rows = get_leaderboard(10)
    snapshot = json.dumps([
        {"user_id": r["user_id"], "first_name": r["first_name"],
         "rank_idx": r["rank_idx"], "points": r["points"]}
        for r in rows
    ], ensure_ascii=False)
    with get_db() as con:
        con.execute("INSERT INTO leaderboard_history(cycle_at,snapshot) VALUES(datetime('now'),?)",
                    (snapshot,))


# ── Admin logs ────────────────────────────────────────────────────
def log_admin_action(admin_id: int, action: str, target_id: int = 0, detail: str = "") -> None:
    with get_db() as con:
        con.execute(
            "INSERT INTO admin_logs(admin_id,action,target_id,detail) VALUES(?,?,?,?)",
            (admin_id, action, target_id, detail),
        )


# ── Permissions ───────────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return get_owner_id() == user_id


def is_admin_or_owner(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    u = get_user(user_id)
    return bool(u and u["is_admin"])


def is_banned(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u["is_banned"])


# ─────────────────────────── Keyboards ──────────────────────────
def _kb(buttons: list[list[str]], resize: bool = True) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t) for t in row] for row in buttons],
        resize_keyboard=resize,
        input_field_placeholder="گزینه‌ای انتخاب کنید…",
    )


def main_menu_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [B_PROFILE, B_REQ],
        [B_UPGRADE, B_LEADER],
        [B_BET, B_RANKS],
        [B_ABOUT],
    ]
    if is_admin_or_owner(user_id):
        rows.append([B_ADMIN])
    return _kb(rows)


def nav_kb(extra: list[list[str]] | None = None) -> ReplyKeyboardMarkup:
    """Navigation keyboard with optional extra rows. Never mutates the input list."""
    rows = list(extra) if extra else []   # FIX: copy to avoid mutating caller's list
    rows.append([B_BACK, B_HOME])
    return _kb(rows)


def activity_kb() -> ReplyKeyboardMarkup:
    rows = [[a["name"]] for a in ACTIVITIES]
    rows.append([B_BACK, B_HOME])
    return _kb(rows)


def admin_panel_kb() -> ReplyKeyboardMarkup:
    """Admin panel keyboard — every menu has ⬅️ Back and 🏠 Home per spec."""
    return _kb([
        [B_ADD_ADM, B_REM_ADM],
        [B_BROADCAST, B_STATS],
        [B_BACKUP, B_RESTORE],
        [B_SET_RANK, B_SET_PTS],
        [B_BAN, B_REM_USER],
        [B_RESET_PTS],
        [B_BACK, B_HOME],   # FIX: spec requires every menu to have Back + Home
    ])


# ─────────────────────────── Helpers ────────────────────────────
def rank_name(idx: int) -> str:
    return RANKS[idx] if 0 <= idx < len(RANKS) else "Unknown"


def leaderboard_position(user_id: int) -> int:
    with get_db() as con:
        rows = con.execute(
            "SELECT user_id FROM users WHERE is_banned=0 ORDER BY points DESC, rank_idx DESC"
        ).fetchall()
    for i, r in enumerate(rows, 1):
        if r["user_id"] == user_id:
            return i
    return 0


def profile_text(u: sqlite3.Row) -> str:
    pos = leaderboard_position(u["user_id"])
    owner_id = get_owner_id()
    role = ""
    if owner_id and u["user_id"] == owner_id:
        role = "  👑 مالک"
    elif u["is_admin"]:
        role = "  🛡 ادمین"
    return (
        f"👤 <b>به پروفایل Matrix-Family خوش آمدید</b>\n\n"
        f"┌──────────────────────\n"
        f"│ 📛 نام: <b>{u['first_name']}</b>{role}\n"
        f"│ 🆔 یوزرنیم: @{u['username'] or '—'}\n"
        f"│ 🔢 Telegram ID: <code>{u['user_id']}</code>\n"
        f"│ 🎖 رنک: <b>{rank_name(u['rank_idx'])}</b>\n"
        f"│ ⭐ امتیاز: <b>{u['points']:,}</b>\n"
        f"│ 🏆 رتبه لیدربرد: <b>#{pos}</b>\n"
        f"│ 📤 کل درخواست‌ها: {u['total_requests']}\n"
        f"│ ✅ تایید شده: {u['approved_req']}\n"
        f"│ ❌ رد شده: {u['rejected_req']}\n"
        f"│ 📅 عضو از: {u['joined_at'][:10]}\n"
        f"└──────────────────────"
    )


def stats_text() -> str:
    all_users = get_all_users()
    total = len(all_users)
    admins = sum(1 for u in all_users if u["is_admin"])
    banned = sum(1 for u in all_users if u["is_banned"])
    total_pts = sum(u["points"] for u in all_users)
    uptime = time.time() - _start_time
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    with get_db() as con:
        pending_req = con.execute(
            "SELECT COUNT(*) as c FROM point_requests WHERE status='pending'"
        ).fetchone()["c"]
    return (
        f"📊 <b>Statistics — Matrix-Family Bot v4.0</b>\n\n"
        f"👥 کل اعضا: <b>{total}</b>\n"
        f"🛡 ادمین‌ها: <b>{admins}</b>\n"
        f"🚫 بن‌شده: <b>{banned}</b>\n"
        f"⭐ کل امتیازها: <b>{total_pts:,}</b>\n"
        f"📥 درخواست‌های معلق: <b>{pending_req}</b>\n"
        f"⏱ آپتایم: <b>{h:02d}:{m:02d}:{s:02d}</b>"
    )


def _resolve_user(text: str) -> Optional[sqlite3.Row]:
    """Resolve User ID (numeric) or @username to a user row."""
    text = text.strip().lstrip("@")
    if text.isdigit():
        return get_user(int(text))
    return get_user_by_username(text)


# ── Notification helpers ───────────────────────────────────────────
async def _notify_owner(bot, text: str) -> None:
    """Send owner a notification — used for admin action logging."""
    owner_id = get_owner_id()
    if owner_id:
        try:
            await bot.send_message(owner_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("Could not notify owner: %s", e)


async def _get_admin_recipients() -> list[int]:
    owner_id = get_owner_id()
    recipients: set[int] = set()
    if owner_id:
        recipients.add(owner_id)
    with get_db() as con:
        rows = con.execute("SELECT user_id FROM users WHERE is_admin=1").fetchall()
    for r in rows:
        recipients.add(r["user_id"])
    return list(recipients)


# ─────────────────────────── Ban guard ──────────────────────────
async def _ban_guard(update: Update) -> bool:
    """Returns True if user is banned (caller should abort). Sends notice and ends."""
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 حساب شما مسدود شده است.")
        return True
    return False


# ─────────────────────────── /start ─────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return IDLE
    user = update.effective_user
    upsert_user(user)

    if is_banned(user.id):
        await update.message.reply_text("🚫 حساب شما مسدود شده است.")
        return ConversationHandler.END

    # First-time owner claim
    if not get_owner_id():
        won = claim_owner_atomic(user.id)
        if won:
            set_rank(user.id, len(RANKS) - 1)  # Staff Leader
            log.info("Owner set: user_id=%s (%s)", user.id, user.first_name)
            await update.message.reply_text(
                "👑 <b>تبریک! شما اولین مالک این ربات شدید.</b>\n"
                "از دکمه ⚙️ پنل مدیریت برای کنترل کامل استفاده کنید.",
                reply_markup=main_menu_kb(user.id),
                parse_mode=ParseMode.HTML,
            )
            return IDLE

    await update.message.reply_text(
        f"🏠 <b>Matrix-Family Bot</b>\nسلام <b>{user.first_name}</b>! 👋\nاز منوی زیر انتخاب کنید:",
        reply_markup=main_menu_kb(user.id),
        parse_mode=ParseMode.HTML,
    )
    return IDLE


# ─────────────────────────── IDLE dispatcher ─────────────────────
async def idle_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return IDLE
    user = update.effective_user
    text = update.message.text or ""

    if await _ban_guard(update):
        return ConversationHandler.END

    upsert_user(user)

    # ── Check if this admin is waiting to enter a rejection reason ──
    if "awaiting_reject_req_id" in ctx.user_data and is_admin_or_owner(user.id):
        req_id = ctx.user_data.pop("awaiting_reject_req_id")
        reason = text.strip() or "—"
        req = get_request(req_id)
        if req and req["status"] == "rejecting":
            update_request(req_id, "rejected", user.id, reason)
            with get_db() as con:
                con.execute(
                    "UPDATE users SET rejected_req=rejected_req+1 WHERE user_id=?",
                    (req["user_id"],)
                )
            log_admin_action(user.id, "reject_request", req["user_id"],
                             f"req#{req_id}: {reason}")
            act = ACTIVITIES[req["activity_idx"]]
            try:
                await ctx.bot.send_message(
                    req["user_id"],
                    f"❌ <b>درخواست امتیاز شما رد شد</b>\n\n"
                    f"فعالیت: {act['name']}\n"
                    f"دلیل: <i>{reason}</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                log.warning("Could not notify user %s of rejection: %s", req["user_id"], e)
            # Notify owner of admin action (if admin did the rejection)
            if not is_owner(user.id):
                u_row = get_user(req["user_id"])
                name = u_row["first_name"] if u_row else str(req["user_id"])
                await _notify_owner(
                    ctx.bot,
                    f"🛡 ادمین <b>{user.first_name}</b> درخواست #{req_id} کاربر <b>{name}</b> را رد کرد.\n"
                    f"دلیل: {reason}"
                )
        await update.message.reply_text(
            "✅ دلیل رد به کاربر ارسال شد.",
            reply_markup=main_menu_kb(user.id),
        )
        return IDLE

    # ── Main menu buttons ──────────────────────────────────────────
    if text == B_PROFILE:
        u = get_user(user.id)
        await update.message.reply_text(
            profile_text(u), reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text == B_RANKS:
        await update.message.reply_text(
            RANK_TABLE, reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text == B_LEADER:
        rows = get_leaderboard(20)
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>به پنل لیدربرد Matrix-Family خوش آمدید</b>\n"]
        if not rows:
            lines.append("هنوز اعضایی ثبت نشده‌اند.")
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(
                f"{medal} <b>{r['first_name']}</b>  @{r['username'] or '—'}\n"
                f"   🎖 {rank_name(r['rank_idx'])}  |  ⭐ {r['points']:,}P"
            )
        await update.message.reply_text(
            "\n".join(lines), reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text == B_ABOUT:
        await update.message.reply_text(
            "ℹ️ <b>Matrix-Family Bot</b>\n\n"
            "<b>هدف:</b>\n"
            "این ربات سیستم امتیاز و رتبه‌بندی خانواده Matrix را مدیریت می‌کند.\n\n"
            "<b>سیستم امتیاز:</b>\n"
            "با انجام فعالیت‌های مختلف امتیاز کسب کنید. درخواست ارسال کنید، "
            "ادمین تایید می‌کند، امتیاز دریافت می‌کنید.\n\n"
            "<b>سیستم رنک:</b>\n"
            "با امتیاز کافی رنک خود را ارتقا دهید — از Members تا Staff Leader.\n\n"
            "<b>شرط‌بندی:</b>\n"
            "با اعضای دیگر شرط ببندید. برنده امتیاز می‌گیرد، بازنده کم می‌کند.\n\n"
            "<b>لیدربرد:</b>\n"
            "هر ۲۲ روز یک‌بار ۳ نفر برتر جایزه می‌گیرند (300/200/100 امتیاز).\n\n"
            "👨‍💻 <b>Developer:</b> سپهر (ماتریکس) — @oovqx",
            reply_markup=nav_kb(),
            parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text == B_UPGRADE:
        u = get_user(user.id)
        cur_idx = u["rank_idx"]
        rn = rank_name(cur_idx)

        if cur_idx >= len(RANKS) - 1:
            await update.message.reply_text(
                f"🏆 رنک فعلی: <b>{rn}</b>\nشما به بالاترین رنک رسیده‌اید!",
                reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
            )
            return IDLE

        if cur_idx >= MAX_AUTO_RANK:
            await update.message.reply_text(
                f"🎖 رنک فعلی: <b>{rn}</b>\n"
                "ارتقا به Staff Leader نیاز به تایید مالک دارد.",
                reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
            )
            return IDLE

        next_idx = cur_idx + 1
        cost = RANK_COSTS[next_idx]
        balance = u["points"]
        can = balance >= cost

        info = (
            f"🎖 <b>ارتقای رنک</b>\n\n"
            f"رنک فعلی: <b>{rn}</b>\n"
            f"رنک بعدی: <b>{rank_name(next_idx)}</b>\n"
            f"هزینه: <b>{cost:,}P</b>\n"
            f"موجودی شما: <b>{balance:,}P</b>\n\n"
        )

        if can:
            with get_db() as con:
                result = con.execute(
                    "UPDATE users SET points=points-?,rank_idx=? "
                    "WHERE user_id=? AND rank_idx=? AND points>=?",
                    (cost, next_idx, user.id, cur_idx, cost),
                )
            if result.rowcount:
                log.info("Rank upgrade: user=%s → %s (cost %sP)", user.id, next_idx, cost)
                await update.message.reply_text(
                    info + f"🎉 <b>تبریک! به رنک {rank_name(next_idx)} ارتقا یافتید!</b>\n"
                    f"{cost:,}P از موجودی شما کسر شد.",
                    reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
                )
            else:
                await update.message.reply_text(
                    info + "❌ ارتقا انجام نشد — وضعیت تغییر کرده، دوباره تلاش کنید.",
                    reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
                )
        else:
            await update.message.reply_text(
                info + f"❌ امتیاز کافی ندارید. {cost - balance:,}P بیشتر نیاز دارید.",
                reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
            )
        return IDLE

    elif text == B_REQ:
        await update.message.reply_text(
            "⭐ <b>به پنل درخواست امتیاز Matrix-Family خوش آمدید</b>\n\n"
            "فعالیت مورد نظر را انتخاب کنید:",
            reply_markup=activity_kb(),
            parse_mode=ParseMode.HTML,
        )
        return REQ_ACTIVITY

    elif text == B_BET:
        if has_pending_bet(user.id):
            await update.message.reply_text(
                "⚠️ شما یک شرط‌بندی فعال دارید. ابتدا آن را تمام کنید.",
                reply_markup=nav_kb(),
            )
            return IDLE
        await update.message.reply_text(
            "🎲 <b>شرط‌بندی</b>\n\nیوزرنیم حریف را وارد کنید (مثال: username یا @username):",
            reply_markup=nav_kb(),
            parse_mode=ParseMode.HTML,
        )
        return BET_USERNAME

    # ── Admin panel entry ──────────────────────────────────────────
    elif text == B_ADMIN:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        badge = "👑 مالک" if is_owner(user.id) else "🛡 ادمین"
        await update.message.reply_text(
            f"⚙️ <b>پنل مدیریت — Matrix-Family</b>\n<i>{badge}</i>\n\nگزینه‌ای انتخاب کنید:",
            reply_markup=admin_panel_kb(),
            parse_mode=ParseMode.HTML,
        )
        return IDLE

    # ── Admin panel buttons ────────────────────────────────────────
    elif text == B_ADD_ADM:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین اضافه کند.")
            return IDLE
        await update.message.reply_text(
            "➕ <b>Add Admin</b>\n\nUser ID یا یوزرنیم کاربر را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_ADD_ADMIN

    elif text == B_REM_ADM:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین حذف کند.")
            return IDLE
        await update.message.reply_text(
            "➖ <b>Remove Admin</b>\n\nUser ID یا یوزرنیم ادمین را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_REMOVE_ADMIN

    elif text == B_BROADCAST:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            "📢 <b>Broadcast</b>\n\nمتن پیام را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_BROADCAST

    elif text == B_STATS:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            stats_text(), reply_markup=admin_panel_kb(), parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text == B_BACKUP:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"family_{ts}.db"
        shutil.copy2(DB_PATH, backup_path)
        log_admin_action(user.id, "backup", 0, str(backup_path))
        log.info("Backup created: %s by user %s", backup_path, user.id)
        await update.message.reply_text(
            "💾 در حال ارسال فایل پشتیبان…", reply_markup=admin_panel_kb()
        )
        with open(backup_path, "rb") as f:
            await ctx.bot.send_document(
                user.id, f,
                filename=backup_path.name,
                caption=f"💾 <b>Backup — {ts}</b>",
                parse_mode=ParseMode.HTML,
            )
        return IDLE

    elif text == B_RESTORE:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ فقط مالک می‌تواند بازیابی کند.")
            return IDLE
        await update.message.reply_text(
            "♻️ <b>Restore Backup</b>\n\n"
            "فایل پشتیبان (.db) را ارسال کنید.\n"
            "<b>⚠️ این عمل داده‌های فعلی را جایگزین می‌کند!</b>",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_RESTORE

    elif text == B_SET_RANK:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            "👤 <b>Set User Rank</b>\n\nUser ID یا یوزرنیم کاربر را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_SET_RANK_USER

    elif text == B_SET_PTS:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            "⭐ <b>Set User Points</b>\n\nUser ID یا یوزرنیم کاربر را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_SET_POINTS_USER

    elif text == B_BAN:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            "🚫 <b>Ban User</b>\n\nUser ID یا یوزرنیم کاربر را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_BAN_USER

    elif text == B_REM_USER:
        if not is_admin_or_owner(user.id):
            await update.message.reply_text("⛔ دسترسی ندارید.")
            return IDLE
        await update.message.reply_text(
            "🗑 <b>Remove User</b>\n\nUser ID یا یوزرنیم کاربر را وارد کنید:",
            reply_markup=nav_kb(), parse_mode=ParseMode.HTML,
        )
        return ADM_REMOVE_USER

    elif text == B_RESET_PTS:
        if not is_owner(user.id):
            await update.message.reply_text("⛔ فقط مالک می‌تواند این کار را انجام دهد.")
            return IDLE
        # FIX: Confirmation step via inline keyboard before destructive action
        confirm_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ بله، ریست کن", callback_data="reset_pts_confirm"),
            InlineKeyboardButton("❌ لغو",           callback_data="reset_pts_cancel"),
        ]])
        await update.message.reply_text(
            "⚠️ <b>هشدار!</b>\n\n"
            "این عمل امتیاز <b>همه اعضا</b> را صفر می‌کند و قابل بازگشت نیست.\n\n"
            "آیا مطمئن هستید؟",
            reply_markup=confirm_kb,
            parse_mode=ParseMode.HTML,
        )
        return IDLE

    elif text in (B_BACK, B_HOME):
        await update.message.reply_text(
            "🏠 منوی اصلی", reply_markup=main_menu_kb(user.id),
        )
        return IDLE

    else:
        await update.message.reply_text(
            "❓ گزینه شناخته نشد. از منوی زیر استفاده کنید:",
            reply_markup=main_menu_kb(user.id),
        )
        return IDLE


# ─────────────────────────── Reset Points callbacks ──────────────
async def cb_reset_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not is_owner(user.id):
        await q.answer("⛔ فقط مالک می‌تواند.", show_alert=True)
        return
    reset_all_points()
    log_admin_action(user.id, "reset_all_points")
    log.info("All points reset by owner %s", user.id)
    await q.edit_message_text("🔄 <b>امتیاز همه اعضا صفر شد.</b>", parse_mode=ParseMode.HTML)


async def cb_reset_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("❌ عملیات ریست لغو شد.")


# ─────────────────────────── Point request flow ──────────────────
async def req_activity(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return REQ_ACTIVITY
    user = update.effective_user
    if await _ban_guard(update):
        return ConversationHandler.END
    text = update.message.text or ""

    if text in (B_BACK, B_HOME):
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu_kb(user.id))
        return IDLE

    act_idx = next((i for i, a in enumerate(ACTIVITIES) if a["name"] == text), None)
    if act_idx is None:
        await update.message.reply_text(
            "❓ فعالیت شناخته نشد. یکی از گزینه‌های زیر را انتخاب کنید:",
            reply_markup=activity_kb(),
        )
        return REQ_ACTIVITY

    ctx.user_data["req_activity_idx"] = act_idx
    act = ACTIVITIES[act_idx]
    await update.message.reply_text(
        f"📎 <b>فعالیت انتخابی:</b> {act['name']}\n"
        f"⭐ امتیاز: <b>{act['points']}P</b>"
        + (f"\n🎖 بونوس رنک: +{act['bonus_ranks']}" if act["bonus_ranks"] else "")
        + "\n\n📸 حالا مدرک (عکس، ویدیو یا فایل) را ارسال کنید:",
        reply_markup=nav_kb(),
        parse_mode=ParseMode.HTML,
    )
    return REQ_PROOF


async def req_proof(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return REQ_PROOF
    user = update.effective_user
    # FIX: Ban check in sub-state — user could be banned while mid-upload
    if await _ban_guard(update):
        return ConversationHandler.END

    msg = update.message

    if msg.text and msg.text in (B_BACK, B_HOME):
        ctx.user_data.pop("req_activity_idx", None)
        await msg.reply_text("🏠 منوی اصلی", reply_markup=main_menu_kb(user.id))
        return IDLE

    file_id = None
    file_type = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        file_type = "video"
    elif msg.document:
        file_id = msg.document.file_id
        file_type = "document"

    if not file_id:
        await msg.reply_text(
            "⚠️ لطفاً یک عکس، ویدیو یا فایل ارسال کنید:", reply_markup=nav_kb(),
        )
        return REQ_PROOF

    act_idx = ctx.user_data.pop("req_activity_idx", 0)
    req_id = create_request(user.id, act_idx, file_id, file_type)
    inc_total_requests(user.id)

    u = get_user(user.id)
    act = ACTIVITIES[act_idx]

    approve_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"approve_{req_id}"),
        InlineKeyboardButton("❌ رد",   callback_data=f"reject_{req_id}"),
    ]])
    notice = (
        f"📥 <b>درخواست امتیاز جدید #{req_id}</b>\n\n"
        f"👤 کاربر: {u['first_name']} (@{u['username'] or '—'})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🏷 فعالیت: <b>{act['name']}</b>\n"
        f"⭐ امتیاز: <b>{act['points']}P</b>"
        + (f"\n🎖 بونوس رنک: +{act['bonus_ranks']}" if act["bonus_ranks"] else "")
    )

    for uid in await _get_admin_recipients():
        try:
            if file_type == "photo":
                await ctx.bot.send_photo(uid, file_id, caption=notice,
                                         parse_mode=ParseMode.HTML, reply_markup=approve_kb)
            elif file_type == "video":
                await ctx.bot.send_video(uid, file_id, caption=notice,
                                         parse_mode=ParseMode.HTML, reply_markup=approve_kb)
            else:
                await ctx.bot.send_document(uid, file_id, caption=notice,
                                            parse_mode=ParseMode.HTML, reply_markup=approve_kb)
        except Exception as e:
            log.warning("Could not notify admin %s of request: %s", uid, e)

    await msg.reply_text(
        f"✅ <b>درخواست شما ثبت شد!</b>\n\n"
        f"شماره درخواست: <b>#{req_id}</b>\n"
        f"فعالیت: {act['name']}\n\n"
        "پس از بررسی توسط ادمین، نتیجه به شما اطلاع داده خواهد شد.",
        reply_markup=main_menu_kb(user.id),
        parse_mode=ParseMode.HTML,
    )
    return IDLE


# ─────────────────────────── Approve / Reject callbacks ──────────
async def cb_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    admin = q.from_user

    if not is_admin_or_owner(admin.id):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    req_id = int(q.data.split("_")[1])

    # Atomic transition: only first admin wins
    with get_db() as con:
        result = con.execute(
            "UPDATE point_requests SET status='approved', reviewer_id=?, review_at=datetime('now') "
            "WHERE id=? AND status='pending'",
            (admin.id, req_id),
        )
        if result.rowcount == 0:
            req = con.execute("SELECT status FROM point_requests WHERE id=?", (req_id,)).fetchone()
            status = req["status"] if req else "unknown"
            await q.answer(f"این درخواست قبلاً بررسی شده ({status}).", show_alert=True)
            return
        req = con.execute("SELECT * FROM point_requests WHERE id=?", (req_id,)).fetchone()
        u = con.execute("SELECT * FROM users WHERE user_id=?", (req["user_id"],)).fetchone()
        if not u:
            await q.answer("کاربر یافت نشد.", show_alert=True)
            return
        act = ACTIVITIES[req["activity_idx"]]
        con.execute(
            "UPDATE users SET points=points+?, approved_req=approved_req+1 WHERE user_id=?",
            (act["points"], req["user_id"])
        )
        new_pts = con.execute(
            "SELECT points FROM users WHERE user_id=?", (req["user_id"],)
        ).fetchone()["points"]
        new_rank_idx = u["rank_idx"]
        if act["bonus_ranks"] > 0:
            new_rank_idx = min(u["rank_idx"] + act["bonus_ranks"], len(RANKS) - 1)
            con.execute("UPDATE users SET rank_idx=? WHERE user_id=?",
                        (new_rank_idx, req["user_id"]))

    log_admin_action(admin.id, "approve_request", req["user_id"],
                     f"req#{req_id} +{act['points']}P +{act['bonus_ranks']}rank")
    log.info("Approved req#%s for user %s: +%sP +%srank",
             req_id, req["user_id"], act["points"], act["bonus_ranks"])

    # Notify user
    try:
        notif = (
            f"✅ <b>درخواست امتیاز شما تایید شد!</b>\n\n"
            f"فعالیت: {act['name']}\n"
            f"⭐ امتیاز دریافتی: <b>+{act['points']}P</b>\n"
            f"💰 موجودی جدید: <b>{new_pts:,}P</b>"
        )
        if act["bonus_ranks"]:
            notif += f"\n🎖 رنک جدید: <b>{rank_name(new_rank_idx)}</b>"
        await ctx.bot.send_message(req["user_id"], notif, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("Could not notify user %s of approval: %s", req["user_id"], e)

    # Notify owner if an admin (not owner) did this
    if not is_owner(admin.id):
        u_row = get_user(req["user_id"])
        name = u_row["first_name"] if u_row else str(req["user_id"])
        await _notify_owner(
            ctx.bot,
            f"🛡 ادمین <b>{admin.first_name}</b> درخواست #{req_id} کاربر <b>{name}</b> را تایید کرد.\n"
            f"فعالیت: {act['name']} | +{act['points']}P"
        )

    # Update admin message caption
    try:
        existing = q.message.caption or ""
        await q.edit_message_caption(
            existing + f"\n\n✅ <b>تایید شد توسط</b> {admin.first_name}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass


async def cb_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    admin = q.from_user

    if not is_admin_or_owner(admin.id):
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    req_id = int(q.data.split("_")[1])

    # Atomically reserve the rejection so no other admin can race
    with get_db() as con:
        result = con.execute(
            "UPDATE point_requests SET status='rejecting', reviewer_id=?, review_at=datetime('now') "
            "WHERE id=? AND status='pending'",
            (admin.id, req_id),
        )
        if result.rowcount == 0:
            req = con.execute("SELECT status FROM point_requests WHERE id=?", (req_id,)).fetchone()
            status = req["status"] if req else "unknown"
            await q.answer(f"این درخواست قبلاً بررسی شده ({status}).", show_alert=True)
            return

    try:
        existing = q.message.caption or ""
        await q.edit_message_caption(
            existing + f"\n\n⏳ در انتظار دلیل رد از {admin.first_name}…",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass

    ctx.user_data["awaiting_reject_req_id"] = req_id
    await ctx.bot.send_message(
        admin.id,
        f"✏️ دلیل رد درخواست <b>#{req_id}</b> را بنویسید:",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────── Betting flow ────────────────────────
async def bet_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return BET_USERNAME
    user = update.effective_user
    if await _ban_guard(update):
        return ConversationHandler.END
    text = (update.message.text or "").strip()

    if text in (B_BACK, B_HOME):
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu_kb(user.id))
        return IDLE

    username = text.lstrip("@")
    if not username:
        await update.message.reply_text("❌ یوزرنیم نامعتبر است. دوباره وارد کنید:")
        return BET_USERNAME

    opponent = get_user_by_username(username)
    if not opponent:
        await update.message.reply_text(
            f"❌ کاربر @{username} در دیتابیس ربات یافت نشد.\n"
            "کاربر باید ابتدا /start را زده باشد.",
            reply_markup=nav_kb(),
        )
        return BET_USERNAME

    if opponent["user_id"] == user.id:
        await update.message.reply_text("❌ نمی‌توانید با خودتان شرط‌بندی کنید.")
        return BET_USERNAME

    if is_banned(opponent["user_id"]):
        await update.message.reply_text("❌ این کاربر مسدود شده است.")
        return BET_USERNAME

    if has_pending_bet(opponent["user_id"]):
        await update.message.reply_text("❌ این کاربر یک شرط‌بندی فعال دارد.")
        return BET_USERNAME

    ctx.user_data["bet_opponent_id"] = opponent["user_id"]
    u = get_user(user.id)
    await update.message.reply_text(
        f"🎲 حریف: <b>{opponent['first_name']}</b> (@{opponent['username'] or '—'})\n"
        f"موجودی شما: <b>{u['points']:,}P</b>\n\n"
        "مقدار شرط (امتیاز) را وارد کنید:",
        reply_markup=nav_kb(),
        parse_mode=ParseMode.HTML,
    )
    return BET_AMOUNT


async def bet_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return BET_AMOUNT
    user = update.effective_user
    if await _ban_guard(update):
        return ConversationHandler.END
    text = (update.message.text or "").strip()

    if text in (B_BACK, B_HOME):
        ctx.user_data.pop("bet_opponent_id", None)
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu_kb(user.id))
        return IDLE

    try:
        amount = int(text.replace(",", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ مقدار نامعتبر. یک عدد مثبت وارد کنید:")
        return BET_AMOUNT

    u = get_user(user.id)
    if u["points"] < amount:
        await update.message.reply_text(
            f"❌ امتیاز کافی ندارید.\nموجودی: {u['points']:,}P  |  شرط: {amount:,}P"
        )
        return BET_AMOUNT

    opponent_id = ctx.user_data.get("bet_opponent_id")
    if not opponent_id:
        await update.message.reply_text("❌ خطا. دوباره از شرط‌بندی شروع کنید.",
                                        reply_markup=main_menu_kb(user.id))
        return IDLE

    opponent_u = get_user(opponent_id)
    if not opponent_u or opponent_u["points"] < amount:
        await update.message.reply_text(
            f"❌ حریف امتیاز کافی ندارد (موجودی: {opponent_u['points'] if opponent_u else 0:,}P).",
            reply_markup=main_menu_kb(user.id),
        )
        ctx.user_data.pop("bet_opponent_id", None)
        return IDLE

    if has_pending_bet(user.id) or has_pending_bet(opponent_id):
        await update.message.reply_text(
            "❌ یک شرط‌بندی در حال انجام وجود دارد.",
            reply_markup=main_menu_kb(user.id)
        )
        return IDLE

    bet_id = create_bet(user.id, opponent_id, amount)
    accept_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"bet_accept_{bet_id}"),
        InlineKeyboardButton("❌ رد",   callback_data=f"bet_reject_{bet_id}"),
    ]])
    try:
        await ctx.bot.send_message(
            opponent_id,
            f"🎲 <b>درخواست شرط‌بندی!</b>\n\n"
            f"از طرف: <b>{u['first_name']}</b> (@{u['username'] or '—'})\n"
            f"مقدار شرط: <b>{amount:,}P</b>\n\n"
            "قبول می‌کنید؟",
            reply_markup=accept_kb,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.warning("Could not send bet request to opponent %s: %s", opponent_id, e)
        update_bet(bet_id, "cancelled")
        await update.message.reply_text(
            "❌ نمی‌توان به حریف پیام ارسال کرد.", reply_markup=main_menu_kb(user.id)
        )
        ctx.user_data.pop("bet_opponent_id", None)
        return IDLE

    ctx.user_data.pop("bet_opponent_id", None)
    await update.message.reply_text(
        f"✅ درخواست شرط‌بندی ({amount:,}P) ارسال شد. منتظر پاسخ حریف باشید.",
        reply_markup=main_menu_kb(user.id),
    )
    return IDLE


async def cb_bet_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    bet_id = int(q.data.split("_")[2])

    with get_db() as con:
        bet = con.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not bet or bet["status"] != "pending":
            await q.edit_message_text("⚠️ این شرط‌بندی دیگر فعال نیست.")
            return
        if bet["player_b"] != user.id:
            await q.answer("این درخواست برای شما نیست.", show_alert=True)
            return

        amount = bet["amount"]
        pa = con.execute("SELECT * FROM users WHERE user_id=?", (bet["player_a"],)).fetchone()
        pb = con.execute("SELECT * FROM users WHERE user_id=?", (bet["player_b"],)).fetchone()

        if not pa or not pb or pa["points"] < amount or pb["points"] < amount:
            con.execute("UPDATE bets SET status='cancelled' WHERE id=?", (bet_id,))
            await q.edit_message_text("❌ یکی از بازیکنان امتیاز کافی ندارد. شرط‌بندی لغو شد.")
            try:
                await ctx.bot.send_message(bet["player_a"],
                                           "❌ شرط‌بندی به دلیل امتیاز ناکافی لغو شد.")
            except Exception:
                pass
            return

        # Atomic: pick winner + settle in one transaction
        winner_id = random.choice([bet["player_a"], bet["player_b"]])
        loser_id = bet["player_b"] if winner_id == bet["player_a"] else bet["player_a"]

        con.execute("UPDATE bets SET status='resolved', winner=? WHERE id=? AND status='pending'",
                    (winner_id, bet_id))
        con.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, winner_id))
        con.execute("UPDATE users SET points=points-? WHERE user_id=?", (amount, loser_id))
        w_pts = con.execute("SELECT points FROM users WHERE user_id=?",
                            (winner_id,)).fetchone()["points"]
        l_pts = con.execute("SELECT points FROM users WHERE user_id=?",
                            (loser_id,)).fetchone()["points"]
        winner_u = con.execute("SELECT * FROM users WHERE user_id=?", (winner_id,)).fetchone()
        loser_u  = con.execute("SELECT * FROM users WHERE user_id=?", (loser_id,)).fetchone()

    result_text = (
        f"🎲 <b>نتیجه شرط‌بندی #{bet_id}</b>\n\n"
        f"🏆 برنده: <b>{winner_u['first_name']}</b>  +{amount:,}P  (موجودی: {w_pts:,}P)\n"
        f"💸 بازنده: <b>{loser_u['first_name']}</b>  -{amount:,}P  (موجودی: {l_pts:,}P)\n\n"
    )
    rematch_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Rematch", callback_data=f"bet_rematch_{bet_id}"),
    ]])

    for uid in [bet["player_a"], bet["player_b"]]:
        try:
            await ctx.bot.send_message(
                uid, result_text + "برای بازی مجدد روی Rematch بزنید:",
                reply_markup=rematch_kb, parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.warning("Could not send bet result to %s: %s", uid, e)

    await q.edit_message_text("✅ شرط‌بندی پذیرفته شد!", reply_markup=None)
    log.info("Bet#%s resolved: winner=%s amount=%s", bet_id, winner_id, amount)


async def cb_bet_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    bet_id = int(q.data.split("_")[2])

    bet = get_bet(bet_id)
    if not bet or bet["status"] != "pending":
        await q.edit_message_text("⚠️ این شرط‌بندی دیگر فعال نیست.")
        return
    if bet["player_b"] != user.id:
        await q.answer("این درخواست برای شما نیست.", show_alert=True)
        return

    update_bet(bet_id, "rejected")
    await q.edit_message_text("❌ شرط‌بندی رد شد.", reply_markup=None)
    try:
        await ctx.bot.send_message(bet["player_a"],
                                   f"❌ حریف شرط‌بندی #{bet_id} را رد کرد.")
    except Exception:
        pass


async def cb_bet_rematch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    old_bet_id = int(q.data.split("_")[2])
    old_bet = get_bet(old_bet_id)
    if not old_bet:
        await q.answer("شرط‌بندی اصلی یافت نشد.", show_alert=True)
        return

    amount = old_bet["amount"]
    if user.id == old_bet["player_a"]:
        opponent_id = old_bet["player_b"]
    elif user.id == old_bet["player_b"]:
        opponent_id = old_bet["player_a"]
    else:
        await q.answer("شما در این شرط‌بندی نبودید.", show_alert=True)
        return

    u = get_user(user.id)
    opp = get_user(opponent_id)
    if not u or not opp:
        await q.answer("کاربر یافت نشد.", show_alert=True)
        return
    if u["points"] < amount or opp["points"] < amount:
        await q.edit_message_text(
            "❌ یکی از بازیکنان امتیاز کافی برای Rematch ندارد.", reply_markup=None
        )
        return
    if has_pending_bet(user.id) or has_pending_bet(opponent_id):
        await q.edit_message_text("❌ یک شرط‌بندی فعال وجود دارد.", reply_markup=None)
        return

    new_bet_id = create_bet(user.id, opponent_id, amount)
    accept_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"bet_accept_{new_bet_id}"),
        InlineKeyboardButton("❌ رد",   callback_data=f"bet_reject_{new_bet_id}"),
    ]])
    try:
        await ctx.bot.send_message(
            opponent_id,
            f"🔄 <b>Rematch از {u['first_name']}!</b>\nمقدار: <b>{amount:,}P</b>",
            reply_markup=accept_kb, parse_mode=ParseMode.HTML,
        )
        await q.edit_message_text("✅ درخواست Rematch ارسال شد.", reply_markup=None)
    except Exception as e:
        log.warning("Rematch notify failed: %s", e)
        update_bet(new_bet_id, "cancelled")
        await q.edit_message_text("❌ ارسال درخواست Rematch ناموفق بود.", reply_markup=None)


# ─────────────────────────── Admin flows ─────────────────────────
def _admin_back(text: str) -> bool:
    return text in (B_BACK, B_HOME)


async def adm_add_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text(
            "⚙️ پنل مدیریت", reply_markup=admin_panel_kb()
        )
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. User ID یا @username وارد کنید:")
        return ADM_ADD_ADMIN
    if is_owner(target["user_id"]):
        await update.message.reply_text("❌ مالک نیاز به ادمین شدن ندارد.",
                                        reply_markup=admin_panel_kb())
        return IDLE
    set_admin(target["user_id"], True)
    log_admin_action(user.id, "add_admin", target["user_id"])
    try:
        await ctx.bot.send_message(target["user_id"],
                                   "🛡 شما به عنوان <b>ادمین</b> منصوب شدید!",
                                   parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ {target['first_name']} (@{target['username'] or '—'}) ادمین شد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_remove_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. User ID یا @username وارد کنید:")
        return ADM_REMOVE_ADMIN
    set_admin(target["user_id"], False)
    log_admin_action(user.id, "remove_admin", target["user_id"])
    try:
        await ctx.bot.send_message(target["user_id"], "🔔 دسترسی ادمین شما حذف شد.")
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ دسترسی ادمین {target['first_name']} حذف شد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    users = get_all_users()
    sent, failed = 0, 0
    for u in users:
        if u["is_banned"]:
            continue
        try:
            await ctx.bot.send_message(
                u["user_id"],
                f"📢 <b>اطلاعیه Matrix-Family:</b>\n\n{text}",
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            failed += 1
    log_admin_action(user.id, "broadcast", 0, f"sent={sent}, failed={failed}")
    # Notify owner if admin broadcast
    if not is_owner(user.id):
        await _notify_owner(
            ctx.bot,
            f"📢 ادمین <b>{user.first_name}</b> یک Broadcast ارسال کرد.\n"
            f"✉️ موفق: {sent} | ❌ ناموفق: {failed}"
        )
    await update.message.reply_text(
        f"✅ Broadcast ارسال شد.\n✉️ موفق: {sent}  |  ❌ ناموفق: {failed}",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_set_rank_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. دوباره وارد کنید:")
        return ADM_SET_RANK_USER
    ctx.user_data["adm_target_uid"] = target["user_id"]
    rank_list = "\n".join(f"{i}. {r}" for i, r in enumerate(RANKS))
    await update.message.reply_text(
        f"👤 کاربر: <b>{target['first_name']}</b> (رنک فعلی: {rank_name(target['rank_idx'])})\n\n"
        f"شماره رنک جدید (۰–{len(RANKS)-1}):\n{rank_list}",
        reply_markup=nav_kb(),
        parse_mode=ParseMode.HTML,
    )
    return ADM_SET_RANK_VALUE


async def adm_set_rank_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        ctx.user_data.pop("adm_target_uid", None)
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    try:
        rank_idx = int(text)
        if not (0 <= rank_idx < len(RANKS)):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ عدد ۰ تا {len(RANKS)-1} وارد کنید:")
        return ADM_SET_RANK_VALUE
    target_uid = ctx.user_data.pop("adm_target_uid", None)
    if not target_uid:
        await update.message.reply_text("❌ خطا. دوباره شروع کنید.", reply_markup=admin_panel_kb())
        return IDLE
    set_rank(target_uid, rank_idx)
    log_admin_action(user.id, "set_rank", target_uid, f"rank_idx={rank_idx}")
    try:
        await ctx.bot.send_message(target_uid,
                                   f"🎖 رنک شما به <b>{rank_name(rank_idx)}</b> تغییر کرد.",
                                   parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ رنک کاربر {target_uid} به {rank_name(rank_idx)} تنظیم شد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_set_points_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. دوباره وارد کنید:")
        return ADM_SET_POINTS_USER
    ctx.user_data["adm_target_uid"] = target["user_id"]
    await update.message.reply_text(
        f"👤 کاربر: <b>{target['first_name']}</b> (موجودی: {target['points']:,}P)\n\n"
        "امتیاز جدید را وارد کنید:",
        reply_markup=nav_kb(),
        parse_mode=ParseMode.HTML,
    )
    return ADM_SET_POINTS_VALUE


async def adm_set_points_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        ctx.user_data.pop("adm_target_uid", None)
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    try:
        points = int(text.replace(",", ""))
        if points < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ یک عدد صفر یا مثبت وارد کنید:")
        return ADM_SET_POINTS_VALUE
    target_uid = ctx.user_data.pop("adm_target_uid", None)
    if not target_uid:
        await update.message.reply_text("❌ خطا. دوباره شروع کنید.", reply_markup=admin_panel_kb())
        return IDLE
    set_points(target_uid, points)
    log_admin_action(user.id, "set_points", target_uid, f"points={points}")
    try:
        await ctx.bot.send_message(target_uid,
                                   f"⭐ امتیاز شما به <b>{points:,}P</b> تنظیم شد.",
                                   parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ امتیاز کاربر {target_uid} به {points:,}P تنظیم شد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_ban_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. دوباره وارد کنید:")
        return ADM_BAN_USER
    if is_owner(target["user_id"]):
        await update.message.reply_text("❌ نمی‌توان مالک را بن کرد.", reply_markup=admin_panel_kb())
        return IDLE
    set_banned(target["user_id"], True)
    log_admin_action(user.id, "ban", target["user_id"])
    if not is_owner(user.id):
        await _notify_owner(
            ctx.bot,
            f"🚫 ادمین <b>{user.first_name}</b> کاربر <b>{target['first_name']}</b> "
            f"(ID:{target['user_id']}) را بن کرد."
        )
    await update.message.reply_text(
        f"🚫 کاربر {target['first_name']} (ID:{target['user_id']}) بن شد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_remove_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if _admin_back(text):
        await update.message.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    target = _resolve_user(text)
    if not target:
        await update.message.reply_text("❌ کاربر یافت نشد. دوباره وارد کنید:")
        return ADM_REMOVE_USER
    if is_owner(target["user_id"]):
        await update.message.reply_text("❌ نمی‌توان مالک را حذف کرد.", reply_markup=admin_panel_kb())
        return IDLE
    saved_name = target["first_name"]
    saved_id = target["user_id"]
    remove_user(saved_id)
    log_admin_action(user.id, "remove_user", saved_id)
    if not is_owner(user.id):
        await _notify_owner(
            ctx.bot,
            f"🗑 ادمین <b>{user.first_name}</b> کاربر <b>{saved_name}</b> (ID:{saved_id}) را حذف کرد."
        )
    await update.message.reply_text(
        f"✅ کاربر {saved_name} (ID:{saved_id}) حذف شد.\n"
        "کاربر می‌تواند با /start بازگردد.",
        reply_markup=admin_panel_kb(),
    )
    return IDLE


async def adm_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    msg = update.message
    if msg.text and msg.text in (B_BACK, B_HOME):
        await msg.reply_text("⚙️ پنل مدیریت", reply_markup=admin_panel_kb())
        return IDLE
    if not msg.document:
        await msg.reply_text("⚠️ لطفاً فایل .db ارسال کنید:", reply_markup=nav_kb())
        return ADM_RESTORE
    doc: Document = msg.document
    if not doc.file_name.endswith(".db"):
        await msg.reply_text("❌ فایل باید با پسوند .db باشد.", reply_markup=nav_kb())
        return ADM_RESTORE
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        old_backup = BACKUP_DIR / f"pre_restore_{ts}.db"
        shutil.copy2(DB_PATH, old_backup)
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(DB_PATH))
        log_admin_action(user.id, "restore", 0, doc.file_name)
        log.info("Database restored from %s by user %s", doc.file_name, user.id)
        await msg.reply_text(
            f"✅ <b>بازیابی موفق!</b>\n"
            f"دیتابیس از <code>{doc.file_name}</code> بازیابی شد.\n"
            f"نسخه قبلی در <code>{old_backup.name}</code> ذخیره شد.",
            reply_markup=admin_panel_kb(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.error("Restore failed: %s", e)
        await msg.reply_text(f"❌ بازیابی ناموفق: {e}", reply_markup=admin_panel_kb())
    return IDLE


# ─────────────────────────── Leaderboard auto-reward ─────────────
def _leaderboard_reward_loop(app_ref: dict) -> None:
    """Background thread: every 22 days save snapshot + reward top 3.
    Uses asyncio.run_coroutine_threadsafe with the event loop captured in post_init."""
    CYCLE_DAYS = 22
    BONUS_POINTS = [300, 200, 100]

    while True:
        try:
            last = get_setting("last_leaderboard_cycle")
            if last:
                last_dt = datetime.fromisoformat(last)
                next_dt = last_dt + timedelta(days=CYCLE_DAYS)
                wait = (next_dt - datetime.now()).total_seconds()
                if wait > 0:
                    time.sleep(min(wait, 3600))
                    continue

            rows = get_leaderboard(3)
            app: Application = app_ref.get("app")
            loop = app_ref.get("loop")  # captured in post_init via asyncio.get_running_loop()

            if not (rows and app and loop and loop.is_running()):
                # Loop not ready yet — defer; will retry after next sleep(3600)
                log.warning("Leaderboard cycle deferred: loop not ready (rows=%s, loop=%s)",
                             len(rows) if rows else 0, loop)
                time.sleep(3600)
                continue

            # Save snapshot before rewarding
            save_leaderboard_snapshot()

            rewards_ok = True
            for i, row in enumerate(rows):
                pts = BONUS_POINTS[i] if i < len(BONUS_POINTS) else 0
                add_points(row["user_id"], pts)
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        app.bot.send_message(
                            row["user_id"],
                            f"🏆 <b>جایزه لیدربرد #{i+1}!</b>\n"
                            f"+{pts:,}P به موجودی شما اضافه شد.\n"
                            f"رتبه: <b>#{i+1}</b> از ۲۲ روز گذشته",
                            parse_mode=ParseMode.HTML,
                        ),
                        loop,
                    )
                    future.result(timeout=15)
                except Exception as e:
                    log.warning("Leaderboard reward notify failed for %s: %s",
                                row["user_id"], e)
                    rewards_ok = False

            # Only mark cycle complete after rewards are distributed
            if rewards_ok:
                set_setting("last_leaderboard_cycle", datetime.now().isoformat())
                log.info("Leaderboard 22-day cycle completed.")
            else:
                log.warning("Leaderboard cycle had notify failures; cycle NOT marked complete.")
        except Exception as e:
            log.error("Leaderboard reward loop error: %s", e)
        time.sleep(3600)


# ─────────────────────────── Bot setup ──────────────────────────
def build_app(app_ref: dict) -> Application:

    async def _post_init(app: Application) -> None:
        """Closure: runs after Application init — captures event loop + registers commands.
        This is the only reliable moment to get the running loop for the leaderboard thread."""
        app_ref["loop"] = asyncio.get_running_loop()
        log.info("Event loop captured in post_init.")
        await app.bot.set_my_commands([
            BotCommand("start", "شروع / منوی اصلی"),
        ])
        log.info("Bot command /start registered.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            IDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, idle_handler)],
            REQ_ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_activity)],
            REQ_PROOF: [
                MessageHandler(
                    (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT)
                    & ~filters.COMMAND,
                    req_proof,
                )
            ],
            BET_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_username)],
            BET_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_amount)],
            ADM_ADD_ADMIN:     [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_admin)],
            ADM_REMOVE_ADMIN:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_remove_admin)],
            ADM_BROADCAST:     [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_broadcast)],
            ADM_SET_RANK_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_rank_user)],
            ADM_SET_RANK_VALUE:[MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_rank_value)],
            ADM_SET_POINTS_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_points_user)],
            ADM_SET_POINTS_VALUE:[MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_points_value)],
            ADM_BAN_USER:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_ban_user)],
            ADM_REMOVE_USER:[MessageHandler(filters.TEXT & ~filters.COMMAND, adm_remove_user)],
            ADM_RESTORE: [
                MessageHandler(
                    (filters.Document.ALL | filters.TEXT) & ~filters.COMMAND,
                    adm_restore,
                )
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
        name="main_conv",
        persistent=False,
    )

    app.add_handler(conv)

    # Callback query handlers — work regardless of conversation state
    app.add_handler(CallbackQueryHandler(cb_approve,      pattern=r"^approve_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_reject,       pattern=r"^reject_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_accept,   pattern=r"^bet_accept_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_reject,   pattern=r"^bet_reject_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_rematch,  pattern=r"^bet_rematch_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_reset_confirm,pattern=r"^reset_pts_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_reset_cancel, pattern=r"^reset_pts_cancel$"))

    return app


# ─────────────────────────── Entry point ────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN is not set! Add it to Replit Secrets.")
        sys.exit(1)

    init_db()
    start_keep_alive()

    banner = "Matrix-Family Bot v4.0 starting"
    log.info("═" * len(banner))
    log.info(" %s ", banner)
    log.info("═" * len(banner))

    consecutive_fails = 0
    MAX_FAILS = 10
    app_ref: dict = {}

    while True:
        log.info("━━━ Bot start attempt #%d (fails=%d) ━━━",
                 consecutive_fails + 1, consecutive_fails)
        try:
            app = build_app(app_ref)
            app_ref["app"] = app

            # Start leaderboard reward background thread (daemon, started once)
            if not app_ref.get("reward_thread_started"):
                t = threading.Thread(
                    target=_leaderboard_reward_loop, args=(app_ref,), daemon=True, name="lb_reward"
                )
                t.start()
                app_ref["reward_thread_started"] = True

            log.info("Bot is now polling…")
            # Loop is captured inside post_init (asyncio.get_running_loop()) which fires
            # after the Application event loop is started — do NOT set it here.
            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            consecutive_fails = 0  # reset only after clean exit

        except InvalidToken:
            log.critical("InvalidToken — check BOT_TOKEN secret. Exiting.")
            sys.exit(1)

        except Conflict:
            consecutive_fails += 1
            log.error("Conflict 409 — another instance running. Waiting 15s… (fail #%d)",
                      consecutive_fails)
            time.sleep(15)

        except NetworkError as e:
            consecutive_fails += 1
            wait = min(5 * consecutive_fails, 60)
            log.warning("NetworkError: %s — retry in %ds (fail #%d)", e, wait, consecutive_fails)
            time.sleep(wait)

        except KeyboardInterrupt:
            log.info("Shutdown requested. Goodbye!")
            break

        except Exception as e:
            consecutive_fails += 1
            wait = min(5 * consecutive_fails, 120)
            log.exception("Unhandled exception (fail #%d) — retry in %ds: %s",
                          consecutive_fails, wait, e)
            if consecutive_fails >= MAX_FAILS:
                log.critical("Too many consecutive failures (%d). Exiting.", consecutive_fails)
                sys.exit(1)
            time.sleep(wait)


if __name__ == "__main__":
    main()
