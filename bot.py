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

# ========= قاعدة البيانات =========
_conn_lock = threading.Lock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
    return conn

def init_db():
    with _conn_lock:
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        _db().commit()

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

# ========= نصوص قصيرة =========
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً (مفقود OPENAI_API_KEY).",
    }
    return M.get(k, k)

# ========= الأقسام =========
SECTIONS = {
    # مجانية
    "suppliers_pack": {
        "title": "📦 بكج الموردين (مجاني)",
        "desc": "ملف شامل لأرقام ومصادر الموردين.",
        "link": "https://docs.google.com/document/d/1rR2nJMUNDoj0cogeenVh9fYVs_ZTM5W0bl0PBIOVwL0/edit?tab=t.0",
        "photo": None, "is_free": True,
    },
    "python_zero": {
        "title": "🐍 بايثون من الصفر (مجاني)",
        "desc": "دليلك الكامل لتعلّم البايثون من الصفر حتى الاحتراف مجانًا 🤩👑",
        "link": "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf",
        "photo": None, "is_free": True,
    },
    "ecommerce_courses": {
        "title": "🛒 التجارة الإلكترونية (مجاني)",
        "desc": "حزمة دورات وشروحات تجارة إلكترونية (أكثر من 7 ملفات).",
        "link": "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link",
        "photo": None, "is_free": True,
    },
    # VIP
    "kash_malik": {
        "title": "♟️ كش ملك (VIP)",
        "desc": "قسم كش ملك – محتوى مميز.",
        "link": "https://drd3m.com/ref/ixeuw",
        "photo": None, "local_file": "assets/kash-malik.docx", "is_free": False,
    },
    "cyber_sec": {
        "title": "🛡️ الأمن السيبراني (VIP)",
        "desc": "الأمن السيبراني من الصفر \"Cyber security\" 🧑‍💻",
        "link": "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A",
        "photo": None, "is_free": False,
    },
    "canva_500": {
        "title": "🖼️ 500 دعوة Canva Pro (VIP)",
        "desc": "دعوات كانفا برو مدى الحياة.",
        "link": "https://digital-plus3.com/products/canva500?srsltid=AfmBOoq01P0ACvybFJkhb2yVBPSUPJadwrOw9LZmNxSUzWPDY8v_42C1",
        "photo": None, "is_free": False,
    },
    "dark_gpt": {
        "title": "🕶️ Dark GPT (VIP)",
        "desc": "أداة متقدمة، التفاصيل لاحقاً.",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
    },
    "adobe_win": {
        "title": "🎨 برامج Adobe (ويندوز) (VIP)",
        "desc": "روابط Adobe للويندوز (قريباً).",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
    },
    "ai_hub": {
        "title": "🧠 الذكاء الاصطناعي (VIP)",
        "desc": "مركز أدوات الذكاء الاصطناعي: دردشة AI + تحويل نص إلى صورة.",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
    },
}

# ========= لوحات الأزرار =========
def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])
def bottom_menu_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=OWNER_DEEP_LINK)],
    ])
def sections_list_kb():
    rows = []
    for k, sec in SECTIONS.items():
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{k}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)
def section_back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])
def vip_prompt_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ اشترك الآن / تواصل", url=OWNER_DEEP_LINK)],
        [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
    ])
def ai_hub_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],
        [InlineKeyboardButton("🖼️ تحويل نص إلى صورة", callback_data="ai_image")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])
def ai_stop_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 إنهاء وضع الذكاء الاصطناعي", callback_data="ai_stop")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])

# ========= تعديل آمن =========
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb)
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                if kb is not None:
                    await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest:
                pass
        else:
            print("safe_edit error:", e)

# ========= التحقق من العضوية (يدعم أكثر من @username) =========
_member_cache = {}  # {uid: (ok, expire)}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now:
            return cached[0]

    last_ok = False
    for attempt in range(1, retries + 1):
        for username in MAIN_CHANNEL_USERNAMES:
            try:
                cm = await context.bot.get_chat_member(f"@{username}", user_id)
                status = getattr(cm, "status", None)
                print(f"[is_member] try#{attempt} @{username} status={status} user={user_id}")
                ok = status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
                last_ok = ok
                if ok:
                    _member_cache[user_id] = (True, now + 60)
                    user_set_verify(user_id, True)
                    return True
            except Exception as e:
                print(f"[is_member] try#{attempt} @{username} ERROR: {e}")
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)

    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False)
    return False

# ========= AI (اختياري) =========
def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        r = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"system","content":"أجب بالعربية بإيجاز."},
                      {"role":"user","content":prompt}],
            temperature=0.7
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        return f"⚠️ تعذّر الحصول على رد: {e}"

def ai_image_url(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        img = client.images.generate(model="gpt-image-1", prompt=prompt, size="512x512")
        return img.data[0].url
    except Exception as e:
        return f"⚠️ تعذّر إنشاء الصورة: {e}"

# ========= أوامر =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة\n/debugverify – تشخيص التحقق\n/dv – تشخيص سريع")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    print(f"[debug_verify] from user={uid}")
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok} (check logs for details)")

# ========= /start =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    user_get(uid)

    # ترحيب
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e:
        print("[welcome] ERROR:", e)

    # تحقّق واضح: إمّا بوابة أو قائمة
    try:
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    except Exception as e:
        print("[start] is_member ERROR:", e)
        ok = False

    if not ok:
        try:
            await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
            await context.bot.send_message(chat_id, need_admin_text())
        except Exception as e:
            print("[start] gate send ERROR:", e)
        return

    try:
        await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
        await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())
    except Exception as e:
        print("[start] menu send ERROR:", e)

# ========= الأزرار =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك أو تعذّر التحقق.\nانضم ثم اضغط تحقّق.\n\n" + need_admin_text(), kb=gate_kb())
        return

    # لازم يكون مشترك
    if not await is_member(context, uid, retries=3, backoff=0.7):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n\n— شارك المعرف مع الإدارة للترقية إلى VIP.", kb=bottom_menu_kb(uid)); return
    if q.data == "upgrade":
        await safe_edit(q, "💳 ترقية إلى VIP بـ 10$.\nتواصل مع الإدارة لإتمام الترقية:", kb=vip_prompt_kb()); return
    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # AI
    if q.data == "ai_chat":
        if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID): await safe_edit(q, f"🔒 {SECTIONS['ai_hub']['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return
        ai_set_mode(uid, "ai_chat"); await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك الآن.", kb=ai_stop_kb()); return

    if q.data == "ai_image":
        if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID): await safe_edit(q, f"🔒 {SECTIONS['ai_hub']['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return
        ai_set_mode(uid, "ai_image"); await safe_edit(q, "🖼️ وضع توليد الصور مفعّل.\nأرسل وصف الصورة.", kb=ai_stop_kb()); return

    if q.data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb()); return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec: await safe_edit(q, "قريباً…", kb=sections_list_kb()); return

        if key == "ai_hub":
            if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
            if not (sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID):
                await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return
            await safe_edit(q, f"{sec['title']}\n\n{sec['desc']}\n\nاختر أداة:", kb=ai_hub_kb()); return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        title, desc, link = sec["title"], sec["desc"], sec["link"]
        local, photo = sec.get("local_file"), sec.get("photo")

        if not allowed:
            await safe_edit(q, f"🔒 {title}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return

        text = f"{title}\n\n{desc}\n\n🔗 الرابط المباشر:\n{link}"
        if local and Path(local).exists():
            await safe_edit(q, f"{title}\n\n{desc}", kb=section_back_kb())
            with open(local, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=f"{title}\n\n🔗 {link}")
        elif photo:
            await safe_edit(q, f"{title}\n\n{desc}", kb=section_back_kb())
            try:
                await q.message.reply_photo(photo=photo, caption=f"{title}\n\n🔗 {link}")
            except Exception:
                await q.message.reply_text(text, reply_markup=section_back_kb())
        else:
            await safe_edit(q, text, kb=section_back_kb())
        return

# ========= رسائل عامة =========
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_member(context, uid, retries=3, backoff=0.7):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode = ai_get_mode(uid)
    if mode == "ai_chat":
        t = (update.message.text or "").strip()
        if not t: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await update.message.reply_text(ai_chat_reply(t), reply_markup=ai_stop_kb()); return
    if mode == "ai_image":
        t = (update.message.text or "").strip()
        if not t: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
        url = ai_image_url(t)
        if isinstance(url, str) and url.startswith("http"):
            try:
                await update.message.reply_photo(photo=url, caption=f"✅ تم إنشاء الصورة بناءً على:\n{t}", reply_markup=ai_stop_kb())
            except Exception:
                await update.message.reply_text(url, reply_markup=ai_stop_kb())
        else:
            await update.message.reply_text(url, reply_markup=ai_stop_kb())
        return

    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# ========= أوامر المدير =========
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ تم تفعيل {context.args[0]}")
async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم إلغاء {context.args[0]}")

# ========= أخطاء عامة =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Error: {getattr(context, 'error', 'unknown')}")

# ========= الإقلاع =========
async def on_startup(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_my_commands(
        [
            BotCommand("start", "بدء"),
            BotCommand("help", "مساعدة"),
            BotCommand("debugverify", "تشخيص التحقق"),
            BotCommand("dv", "تشخيص سريع"),
        ],
        scope=BotCommandScopeDefault()
    )
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
                BotCommand("id", "معرّفك"),
                BotCommand("grant", "منح صلاحية VIP"),
                BotCommand("revoke", "سحب صلاحية VIP"),
                BotCommand("refreshcmds", "تحديث قائمة الأوامر"),
                BotCommand("debugverify", "تشخيص التحقق"),
                BotCommand("dv", "تشخيص سريع"),
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception:
        pass

def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    # أوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler(["debugverify","dv"], debug_verify))
    # أزرار
    app.add_handler(CallbackQueryHandler(on_button))
    # رسائل عامة
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    # أخطاء
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
