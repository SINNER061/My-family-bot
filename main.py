"""
Matrix-Family Telegram Bot — v3.0
Self-healing · Owner security · Rank system · Points · Admin panel · SQLite
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import Conflict, InvalidToken, NetworkError, TelegramError

from keep_alive import start_keep_alive

# ─────────────────────────── Logging ────────────────────────────
LOG_FILE = "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("bot")

# ─────────────────────────── Config ─────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
DB_PATH = Path("family.db")

# Rank ladder — points = incremental COST from previous rank (not cumulative)
RANKS: list[dict] = [
    {"name": "👤 عضو",      "emoji": "👤", "points": 0,   "special": None},
    {"name": "🥉 برنز",     "emoji": "🥉", "points": 70,  "special": None},
    {"name": "🥈 نقره",     "emoji": "🥈", "points": 150, "special": None},
    {"name": "🥇 طلا",      "emoji": "🥇", "points": 300, "special": None},
    {"name": "💎 الماس",    "emoji": "💎", "points": 500, "special": None},
    {"name": "⭐ VIP",      "emoji": "⭐", "points": 0,   "special": "تایید توسط لیدر"},
    {"name": "🛡 ادمین",    "emoji": "🛡", "points": 0,   "special": "انتصاب توسط مالک"},
    {"name": "👑 مالک",     "emoji": "👑", "points": 0,   "special": "تنها یک نفر"},
]
MAX_PURCHASABLE_RANK = 4   # Diamond is highest buyable rank


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
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            rank_idx   INTEGER DEFAULT 0,
            points     INTEGER DEFAULT 0,
            is_admin   INTEGER DEFAULT 0,
            joined_at  TEXT DEFAULT (datetime('now'))
        );
        """)
    log.info("Database initialised at %s", DB_PATH)


# ── Settings helpers ──────────────────────────────────────────────
def get_setting(key: str) -> Optional[str]:
    with get_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


def get_owner_id() -> Optional[int]:
    val = get_setting("owner_id")
    return int(val) if val else None


def claim_owner_atomic(user_id: int) -> bool:
    """Atomically insert owner_id only if no owner exists. Returns True if this caller won."""
    with get_db() as con:
        con.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('owner_id', ?)",
            (str(user_id),),
        )
        row = con.execute("SELECT value FROM settings WHERE key='owner_id'").fetchone()
        return row is not None and int(row["value"]) == user_id


# ── User helpers ──────────────────────────────────────────────────
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


def add_points(user_id: int, amount: int) -> int:
    with get_db() as con:
        con.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, user_id))
        row = con.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["points"]


def set_rank(user_id: int, rank_idx: int) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET rank_idx=? WHERE user_id=?", (rank_idx, user_id))


def set_admin(user_id: int, state: bool) -> None:
    with get_db() as con:
        con.execute("UPDATE users SET is_admin=?, rank_idx=? WHERE user_id=?",
                    (1 if state else 0, 6 if state else 0, user_id))


def get_leaderboard(limit: int = 10) -> list[sqlite3.Row]:
    with get_db() as con:
        return con.execute(
            "SELECT * FROM users ORDER BY rank_idx DESC, points DESC LIMIT ?", (limit,)
        ).fetchall()


def get_all_users() -> list[sqlite3.Row]:
    with get_db() as con:
        return con.execute("SELECT * FROM users ORDER BY joined_at DESC").fetchall()


# ── Permission checks ─────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return get_owner_id() == user_id


def is_admin_or_owner(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    u = get_user(user_id)
    return bool(u and u["is_admin"])


# ─────────────────────────── Keyboards ──────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 پروفایل من",   callback_data="profile"),
            InlineKeyboardButton("🏆 رتبه‌ها",       callback_data="ranks"),
        ],
        [
            InlineKeyboardButton("📊 امتیاز من",    callback_data="mypoints"),
            InlineKeyboardButton("🏅 تابلو برترین‌ها", callback_data="leaderboard"),
        ],
        [
            InlineKeyboardButton("⬆️ ارتقا رتبه",   callback_data="upgrade"),
            InlineKeyboardButton("ℹ️ راهنما",        callback_data="help"),
        ],
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 لیست اعضا",       callback_data="adm_members"),
            InlineKeyboardButton("🎁 دادن امتیاز",      callback_data="adm_givepoints"),
        ],
        [
            InlineKeyboardButton("🛡 انتصاب ادمین",     callback_data="adm_setadmin"),
            InlineKeyboardButton("⭐ تایید VIP",        callback_data="adm_setvip"),
        ],
        [
            InlineKeyboardButton("📢 اطلاع‌رسانی",      callback_data="adm_broadcast"),
            InlineKeyboardButton("📈 آمار ربات",        callback_data="adm_stats"),
        ],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]])


# ─────────────────────────── Helpers ────────────────────────────
def rank_info(idx: int) -> dict:
    return RANKS[idx] if 0 <= idx < len(RANKS) else RANKS[0]


def profile_text(u: sqlite3.Row) -> str:
    r = rank_info(u["rank_idx"])
    owner_id = get_owner_id()
    badge = ""
    if owner_id and u["user_id"] == owner_id:
        badge = "  👑 مالک"
    elif u["is_admin"]:
        badge = "  🛡 ادمین"
    return (
        f"╔══ 👤 پروفایل شما ══╗\n"
        f"┃ نام: <b>{u['first_name']}</b>{badge}\n"
        f"┃ یوزرنیم: @{u['username'] or '—'}\n"
        f"┃ رتبه: {r['name']}\n"
        f"┃ امتیاز: <b>{u['points']:,}</b>\n"
        f"┃ عضو از: {u['joined_at'][:10]}\n"
        f"╚══════════════════╝"
    )


# ─────────────────────────── Handlers ───────────────────────────

# /start
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user)
    log.info("/start from user_id=%s (%s)", user.id, user.first_name)
    await update.message.reply_text(
        f"سلام <b>{user.first_name}</b>! 👋\n"
        "به <b>Matrix Family Bot</b> خوش اومدی 🌟\n"
        "از منوی زیر انتخاب کن:",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


# /setowner  — one-time bootstrap, secure against hijack
async def cmd_setowner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user)
    existing = get_owner_id()

    # Rule 1: env override takes precedence
    env_owner = int(os.environ.get("OWNER_ID", "0"))
    if env_owner and env_owner != user.id:
        return  # silently ignore

    # Rule 2: DB owner exists and it's not us → silently ignore
    if existing and existing != user.id:
        return

    # Rule 3: already owner
    if existing == user.id:
        await update.message.reply_text("✅ شما قبلاً مالک این ربات هستید.")
        return

    # First-time claim — atomic: only one caller wins the race
    won = claim_owner_atomic(user.id)
    if not won:
        return  # another concurrent request got there first — silently ignore
    set_rank(user.id, 7)  # Owner rank
    log.info("Owner set: user_id=%s (%s)", user.id, user.first_name)
    await update.message.reply_text(
        "👑 تبریک! شما مالک این ربات شدید.\n"
        "از /admin برای پنل مدیریت استفاده کن.",
        parse_mode=ParseMode.HTML,
    )


# /admin
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user)
    if not is_admin_or_owner(user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    badge = "👑 مالک" if is_owner(user.id) else "🛡 ادمین"
    await update.message.reply_text(
        f"🔐 پنل مدیریت — <b>{badge}</b>\nیک گزینه انتخاب کنید:",
        reply_markup=admin_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


# /myinfo
async def cmd_myinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user)
    u = get_user(user.id)
    await update.message.reply_text(
        profile_text(u),
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )


# /ranks
async def cmd_ranks(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["🏆 <b>جدول رتبه‌ها</b>\n"]
    for i, r in enumerate(RANKS):
        if r["special"]:
            lines.append(f"{i}. {r['name']} — <i>ویژه: {r['special']}</i>")
        elif r["points"] == 0:
            lines.append(f"{i}. {r['name']} — رایگان")
        else:
            lines.append(f"{i}. {r['name']} — هزینه: <b>{r['points']:,}</b> امتیاز")
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )


# /leaderboard
async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rows = get_leaderboard(10)
    if not rows:
        await update.message.reply_text("هنوز اعضایی ثبت نشده‌اند.")
        return
    lines = ["🏅 <b>برترین اعضا</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(rows):
        m = medals[i] if i < 3 else f"{i+1}."
        r = rank_info(u["rank_idx"])
        lines.append(f"{m} {u['first_name']} — {r['emoji']} {u['points']:,} امتیاز")
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )


# /status
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uptime = time.time() - _start_time
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    owner_id = get_owner_id()
    member_count = len(get_all_users())
    await update.message.reply_text(
        f"🟢 <b>ربات آنلاین است</b>\n"
        f"⏱ آپتایم: <b>{h:02d}:{m:02d}:{s:02d}</b>\n"
        f"👥 اعضا: <b>{member_count}</b>\n"
        f"👑 مالک ثبت‌شده: {'✅' if owner_id else '❌ هنوز /setowner نزدید'}\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.HTML,
    )


# /help
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 <b>راهنمای دستورات</b>\n\n"
        "<b>عمومی:</b>\n"
        "/start — منوی اصلی\n"
        "/myinfo — پروفایل من\n"
        "/ranks — جدول رتبه‌ها\n"
        "/leaderboard — برترین اعضا\n"
        "/status — وضعیت ربات\n\n"
        "<b>ادمین/مالک:</b>\n"
        "/admin — پنل مدیریت\n"
        "/givepoints [user_id] [amount] — دادن امتیاز\n"
        "/setadmin [user_id] — انتصاب ادمین\n"
        "/removeadmin [user_id] — حذف ادمین\n"
        "/setvip [user_id] — تایید VIP\n"
        "/broadcast [پیام] — اطلاع‌رسانی\n\n"
        "<b>اولین راه‌اندازی:</b>\n"
        "/setowner — یک‌بار اجرا کنید تا مالک شوید",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )


# /givepoints [user_id] [amount]
async def cmd_givepoints(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_or_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("استفاده: /givepoints [user_id] [مقدار]")
        return
    try:
        uid, amount = int(args[0]), int(args[1])
    except ValueError:
        await update.message.reply_text("❌ مقادیر نامعتبر. باید عدد باشند.")
        return
    if amount <= 0:
        await update.message.reply_text("❌ مقدار امتیاز باید مثبت باشد.")
        return
    target = get_user(uid)
    if not target:
        await update.message.reply_text(f"❌ کاربر {uid} در دیتابیس ربات پیدا نشد.\nکاربر باید ابتدا /start را زده باشد.")
        return
    new_bal = add_points(uid, amount)
    await update.message.reply_text(
        f"✅ {amount:,} امتیاز به کاربر {uid} داده شد.\n"
        f"موجودی جدید: <b>{new_bal:,}</b>",
        parse_mode=ParseMode.HTML,
    )
    try:
        await ctx.bot.send_message(uid, f"🎁 <b>{amount:,} امتیاز</b> دریافت کردید!\nموجودی: {new_bal:,}", parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("Could not notify user %s about points: %s", uid, e)


# /setadmin [user_id]
async def cmd_setadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین منصوب کند.")
        return
    if not ctx.args:
        await update.message.reply_text("استفاده: /setadmin [user_id]")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return
    if not get_user(uid):
        await update.message.reply_text(f"❌ کاربر {uid} در دیتابیس یافت نشد.")
        return
    set_admin(uid, True)
    await update.message.reply_text(f"✅ کاربر {uid} به عنوان ادمین منصوب شد.")
    try:
        await ctx.bot.send_message(uid, "🛡 شما به عنوان <b>ادمین</b> منصوب شدید!", parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("Could not notify admin %s: %s", uid, e)


# /removeadmin [user_id]
async def cmd_removeadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین را حذف کند.")
        return
    if not ctx.args:
        await update.message.reply_text("استفاده: /removeadmin [user_id]")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return
    if not get_user(uid):
        await update.message.reply_text(f"❌ کاربر {uid} در دیتابیس یافت نشد.")
        return
    set_admin(uid, False)
    await update.message.reply_text(f"✅ دسترسی ادمین کاربر {uid} حذف شد.")


# /setvip [user_id]
async def cmd_setvip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_or_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    if not ctx.args:
        await update.message.reply_text("استفاده: /setvip [user_id]")
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return
    u = get_user(uid)
    if not u:
        await update.message.reply_text("❌ کاربر پیدا نشد.")
        return
    if u["rank_idx"] >= 5:
        await update.message.reply_text("کاربر قبلاً VIP یا بالاتر است.")
        return
    set_rank(uid, 5)
    await update.message.reply_text(f"✅ کاربر {uid} به ⭐ VIP ارتقا یافت.")
    try:
        await ctx.bot.send_message(uid, "⭐ تبریک! شما به رتبه <b>VIP</b> ارتقا یافتید!", parse_mode=ParseMode.HTML)
    except Exception:
        pass


# /broadcast [message]
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_or_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    if not ctx.args:
        await update.message.reply_text("استفاده: /broadcast [متن پیام]")
        return
    text = " ".join(ctx.args)
    users = get_all_users()
    sent, failed = 0, 0
    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], f"📢 <b>اطلاعیه:</b>\n\n{text}", parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ پیام ارسال شد.\n✉️ موفق: {sent}\n❌ ناموفق: {failed}")


# ─────────────────────────── Upgrade flow ───────────────────────
async def show_upgrade(update: Update, u: sqlite3.Row, edit: bool = False) -> None:
    cur_idx = u["rank_idx"]
    r_cur = rank_info(cur_idx)
    msg_obj = update.callback_query.message if edit else update.message

    if cur_idx >= MAX_PURCHASABLE_RANK:
        text = (
            f"رتبه فعلی شما: {r_cur['name']}\n\n"
            "شما به بالاترین رتبه قابل خرید رسیده‌اید! 🎉\n"
            "رتبه‌های بالاتر (VIP، ادمین) نیاز به تایید دارند."
        )
        kb = back_kb()
    else:
        next_idx = cur_idx + 1
        r_next = rank_info(next_idx)
        cost = r_next["points"]
        balance = u["points"]
        can_buy = balance >= cost
        text = (
            f"رتبه فعلی: {r_cur['name']}\n"
            f"رتبه بعدی: {r_next['name']}\n"
            f"هزینه ارتقا: <b>{cost:,}</b> امتیاز\n"
            f"موجودی شما: <b>{balance:,}</b> امتیاز\n\n"
            f"{'✅ می‌توانید ارتقا دهید!' if can_buy else '❌ امتیاز کافی ندارید.'}"
        )
        buttons = []
        if can_buy:
            buttons.append(InlineKeyboardButton(f"⬆️ ارتقا به {r_next['name']}", callback_data=f"buy_rank_{next_idx}"))
        buttons_row = [buttons] if buttons else []
        buttons_row.append([InlineKeyboardButton("🔙 برگشت", callback_data="back_main")])
        kb = InlineKeyboardMarkup(buttons_row)

    if edit:
        await msg_obj.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await msg_obj.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ─────────────────────────── Callback queries ────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    upsert_user(user)
    u = get_user(user.id)
    data = q.data

    # ── Main menu items ──────────────────────────────────────────
    if data == "back_main":
        await q.message.edit_text(
            f"سلام <b>{user.first_name}</b>! از منوی زیر انتخاب کن:",
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML,
        )

    elif data == "profile":
        await q.message.edit_text(profile_text(u), reply_markup=back_kb(), parse_mode=ParseMode.HTML)

    elif data == "mypoints":
        r = rank_info(u["rank_idx"])
        await q.message.edit_text(
            f"📊 <b>امتیاز شما</b>\n\n"
            f"💰 موجودی: <b>{u['points']:,}</b> امتیاز\n"
            f"🏆 رتبه: {r['name']}\n\n"
            "برای ارتقا روی ⬆️ ارتقا رتبه بزنید.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )

    elif data == "ranks":
        lines = ["🏆 <b>جدول رتبه‌ها</b>\n"]
        for i, r in enumerate(RANKS):
            if r["special"]:
                lines.append(f"{i+1}. {r['name']} — <i>{r['special']}</i>")
            elif r["points"] == 0:
                lines.append(f"{i+1}. {r['name']} — رایگان")
            else:
                lines.append(f"{i+1}. {r['name']} — هزینه: <b>{r['points']:,}</b> امتیاز")
        await q.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode=ParseMode.HTML)

    elif data == "leaderboard":
        rows = get_leaderboard(10)
        lines = ["🏅 <b>برترین اعضا</b>\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows):
            m = medals[i] if i < 3 else f"{i+1}."
            r = rank_info(row["rank_idx"])
            lines.append(f"{m} {row['first_name']} — {r['emoji']} {row['points']:,}")
        await q.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode=ParseMode.HTML)

    elif data == "upgrade":
        await show_upgrade(update, u, edit=True)

    elif data.startswith("buy_rank_"):
        next_idx = int(data.split("_")[-1])
        # Server-side validation — never trust client-sent rank index
        cur_u = get_user(user.id)  # fresh read
        if not cur_u:
            await q.answer("خطا: پروفایل شما یافت نشد.", show_alert=True)
            return
        if next_idx != cur_u["rank_idx"] + 1 or next_idx > MAX_PURCHASABLE_RANK:
            await q.answer("درخواست نامعتبر.", show_alert=True)
            return
        r_next = rank_info(next_idx)
        cost = r_next["points"]
        # Atomic deduct-and-upgrade: only succeeds when points still sufficient AND rank unchanged
        with get_db() as con:
            result = con.execute(
                """UPDATE users
                   SET points = points - ?, rank_idx = ?
                   WHERE user_id = ? AND rank_idx = ? AND points >= ?""",
                (cost, next_idx, user.id, cur_u["rank_idx"], cost),
            )
            rows_changed = result.rowcount
        if rows_changed == 0:
            await q.message.edit_text(
                "❌ ارتقا انجام نشد — امتیاز کافی ندارید یا رتبه تغییر کرده.",
                reply_markup=back_kb(),
            )
            return
        log.info("Rank upgrade: user_id=%s → rank %s (cost %s pts)", user.id, next_idx, cost)
        await q.message.edit_text(
            f"🎉 تبریک! به رتبه {r_next['name']} ارتقا یافتید!\n"
            f"هزینه: {cost:,} امتیاز کسر شد.",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )

    elif data == "help":
        await q.message.edit_text(
            "📖 <b>راهنما</b>\n\n"
            "• /start — منوی اصلی\n"
            "• /myinfo — پروفایل\n"
            "• /ranks — رتبه‌ها\n"
            "• /leaderboard — برترین‌ها\n"
            "• /status — وضعیت\n"
            "• /admin — پنل ادمین\n\n"
            "برای ارتقا رتبه امتیاز جمع کنید! 🏆",
            reply_markup=back_kb(),
            parse_mode=ParseMode.HTML,
        )

    # ── Admin panel items ─────────────────────────────────────────
    elif data == "adm_members":
        if not is_admin_or_owner(user.id):
            await q.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        rows = get_all_users()
        lines = [f"👥 <b>اعضا ({len(rows)} نفر)</b>\n"]
        for row in rows[:20]:
            r = rank_info(row["rank_idx"])
            lines.append(f"• {row['first_name']} — {r['emoji']} {row['points']:,} — ID:{row['user_id']}")
        if len(rows) > 20:
            lines.append(f"\n... و {len(rows)-20} نفر دیگر")
        await q.message.edit_text("\n".join(lines), reply_markup=admin_menu_kb(), parse_mode=ParseMode.HTML)

    elif data == "adm_stats":
        if not is_admin_or_owner(user.id):
            await q.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        rows = get_all_users()
        rank_counts = {}
        for row in rows:
            rn = RANKS[row["rank_idx"]]["name"]
            rank_counts[rn] = rank_counts.get(rn, 0) + 1
        lines = [f"📈 <b>آمار ربات</b>\n\nکل اعضا: <b>{len(rows)}</b>\n\nتوزیع رتبه:"]
        for rn, cnt in sorted(rank_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {rn}: {cnt} نفر")
        await q.message.edit_text("\n".join(lines), reply_markup=admin_menu_kb(), parse_mode=ParseMode.HTML)

    elif data in ("adm_givepoints", "adm_setadmin", "adm_setvip", "adm_broadcast"):
        if not is_admin_or_owner(user.id):
            await q.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        hints = {
            "adm_givepoints": "برای دادن امتیاز:\n/givepoints [user_id] [مقدار]",
            "adm_setadmin":   "برای انتصاب ادمین:\n/setadmin [user_id]",
            "adm_setvip":     "برای تایید VIP:\n/setvip [user_id]",
            "adm_broadcast":  "برای اطلاع‌رسانی:\n/broadcast [متن]",
        }
        await q.message.edit_text(hints[data], reply_markup=admin_menu_kb())


# ── Echo unrecognised messages ────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(user)
    await update.message.reply_text(
        "برای استفاده از ربات /start بزنید. 🤖",
        reply_markup=main_menu_kb(),
    )


# ─────────────────────────── Bot commands list ───────────────────
COMMANDS = [
    BotCommand("start",       "منوی اصلی"),
    BotCommand("myinfo",      "پروفایل من"),
    BotCommand("ranks",       "جدول رتبه‌ها"),
    BotCommand("leaderboard", "برترین اعضا"),
    BotCommand("status",      "وضعیت ربات"),
    BotCommand("help",        "راهنما"),
    BotCommand("admin",       "پنل مدیریت"),
    BotCommand("setowner",    "انتصاب مالک (یک‌بار)"),
    BotCommand("givepoints",  "دادن امتیاز [user_id] [مقدار]"),
    BotCommand("setadmin",    "انتصاب ادمین [user_id]"),
    BotCommand("removeadmin", "حذف ادمین [user_id]"),
    BotCommand("setvip",      "تایید VIP [user_id]"),
    BotCommand("broadcast",   "اطلاع‌رسانی به همه"),
]


# ─────────────────────────── Bot runner ─────────────────────────
_start_time = time.time()


async def _post_init(app: Application) -> None:
    """Called once after Application.initialize() — safe place for async setup."""
    await app.bot.set_my_commands(COMMANDS)
    log.info("Bot commands registered in Telegram menu.")


def run_bot() -> None:
    """Build and run the bot. PTB v21 run_polling() manages its own event loop."""
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

    # Register handlers
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("setowner",     cmd_setowner))
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("myinfo",       cmd_myinfo))
    app.add_handler(CommandHandler("ranks",        cmd_ranks))
    app.add_handler(CommandHandler("leaderboard",  cmd_leaderboard))
    app.add_handler(CommandHandler("status",       cmd_status))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("givepoints",   cmd_givepoints))
    app.add_handler(CommandHandler("setadmin",     cmd_setadmin))
    app.add_handler(CommandHandler("removeadmin",  cmd_removeadmin))
    app.add_handler(CommandHandler("setvip",       cmd_setvip))
    app.add_handler(CommandHandler("broadcast",    cmd_broadcast))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot is now polling. Ctrl+C to stop.")
    # run_polling() is synchronous in PTB v21 — it creates and manages its own event loop
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


# ─────────────────────────── Self-healing entry point ────────────
def main() -> None:
    if not BOT_TOKEN:
        log.critical("BOT_TOKEN is not set! Add it to Replit Secrets.")
        sys.exit(1)

    init_db()
    start_keep_alive()

    banner = "Matrix-Family Bot — v3.0 starting"
    log.info("═" * len(banner))
    log.info(" %s ", banner)
    log.info("═" * len(banner))

    consecutive_fails = 0
    MAX_FAILS = 10
    invalid_token_flag = False

    while True:
        if invalid_token_flag:
            log.critical("Invalid token — stopping. Check BOT_TOKEN secret.")
            sys.exit(1)

        log.info("━━━ Bot start attempt #%d (fails=%d) ━━━",
                 consecutive_fails + 1, consecutive_fails)
        try:
            run_bot()   # PTB v21 run_polling() manages its own event loop
            consecutive_fails = 0

        except InvalidToken:
            log.critical("InvalidToken — aborting.")
            invalid_token_flag = True

        except Conflict:
            log.error("Conflict 409 — another instance is running. Waiting 10s …")
            consecutive_fails += 1
            time.sleep(10)

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
