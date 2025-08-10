# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio
from pathlib import Path
from dotenv import load_dotenv

# OpenAI اختياري
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InputFile, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatMemberStatus, ChatAction
from telegram.error import BadRequest

# ========= الإعدادات =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = 6468743821

# 🔁 دعم تغيير اسم القناة بسهولة (الأول هو الحالي)
MAIN_CHANNEL_USERNAMES = ["ferpokss", "Ferp0ks"]   # بدون @
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

def need_admin_text() -> str:
    return f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @{MAIN_CHANNEL_USERNAMES[0]}."

OWNER_DEEP_LINK = f"tg://user?id={OWNER_ID}"

WELCOME_PHOTO = "assets/ferpoks.jpg"
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "هنا تلاقي مصادر وأدوات للتجارة الإلكترونية، بايثون، الأمن السيبراني وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

CHANNEL_ID = None  # سيتم حله عند الإقلاع

# ========= قاعدة البيانات =========
_conn_lock = threading.Lock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
    return conn

def migrate_db():
    with _conn_lock:
        c = _db().cursor()
        c.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in c.fetchall()}
        if "verified_ok" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;")
        if "verified_at" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;")
        _db().commit()

def init_db():
    with _conn_lock:
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        _db().commit()
    migrate_db()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            c.execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0}
        return dict(r)

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?",
                      (1 if ok else 0, int(time.time()), str(uid)))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])
def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),)); _db().commit()
def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),)); _db().commit()

def ai_set_mode(uid: int|str, mode: str|None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id, mode, updated_at) VALUES (?, ?, strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=strftime('%s','now')",
            (str(uid), mode)
        ); _db().commit()
def ai_get_mode(uid: int|str):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
        r = c.fetchone(); return r["mode"] if r else None

# ========= نصوص =========
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
    }
    return M.get(k, k)

# ========= الأقسام =========
SECTIONS = {
    "suppliers_pack": {"title": "📦 بكج الموردين (مجاني)", "desc": "ملف شامل لأرقام الموردين.", "link": "https://docs.google.com/document/d/...", "photo": None, "is_free": True},
    "python_zero": {"title": "🐍 بايثون من الصفر (مجاني)", "desc": "تعلم بايثون مجانًا.", "link": "https://...", "photo": None, "is_free": True},
    "ai_hub": {"title": "🧠 الذكاء الاصطناعي (VIP)", "desc": "مركز أدوات AI.", "link": "https://t.me/ferpokss", "photo": None, "is_free": False},
}

# ========= لوحات الأزرار =========
def gate_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],[InlineKeyboardButton(tr("check_btn"), callback_data="verify")]])
def bottom_menu_kb(uid: int): return InlineKeyboardMarkup([[InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],[InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")],[InlineKeyboardButton("📨 تواصل", url=OWNER_DEEP_LINK)]])
def sections_list_kb(): 
    rows = [[InlineKeyboardButton(("🟢" if sec.get("is_free") else "🔒") + " " + sec['title'], callback_data=f"sec_{k}")] for k, sec in SECTIONS.items()]
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)
def ai_hub_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],[InlineKeyboardButton("🖼️ توليد صورة", callback_data="ai_image")],[InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]])
def ai_stop_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔚 إنهاء", callback_data="ai_stop")],[InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]])

# ========= on_startup =========
async def on_startup(app):
    global CHANNEL_ID
    await app.bot.delete_webhook(drop_pending_updates=True)
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id
            break
        except Exception as e:
            print(f"[startup] فشل {u}: {e}")

# ========= main =========
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("مرحباً!")))
    app.run_polling()

if __name__ == "__main__":
    main()

