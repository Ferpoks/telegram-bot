# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio
from pathlib import Path

from dotenv import load_dotenv
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # لا نوقف البوت إن ما توفّر الباكدج

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

# ========= الإعدادات العامة =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في Environment Variables")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = 6468743821

# قناة الاشتراك (يوزر عام بدون @)
MAIN_CHANNEL_USERNAME = "Ferp0ks"
MAIN_CHANNEL_LINK = "https://t.me/Ferp0ks"

OWNER_DEEP_LINK = "tg://user?id=6468743821"

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
          lang TEXT DEFAULT 'ar',
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0
        );
        """)
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
        );
        """)
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
            return {"id": uid, "lang": "ar", "premium": 0, "verified_ok": 0, "verified_at": 0}
        return dict(r)

def user_set_verify(uid: int|str, ok: bool):
    uid = str(uid)
    now = int(time.time())
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?", (1 if ok else 0, now, uid))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])

def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),))
        _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),))
        _db().commit()

def ai_set_mode(uid: int|str, mode: str|None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id, mode, updated_at) VALUES (?, ?, strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=strftime('%s','now')",
            (str(uid), mode)
        )
        _db().commit()

def ai_get_mode(uid: int|str) -> str|None:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
        r = c.fetchone()
        return r["mode"] if r else None

# ========= النصوص =========
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "need_admin": "⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @Ferp0ks.",
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
        "photo": None,
        "is_free": True,
    },
    "python_zero": {
        "title": "🐍 بايثون من الصفر (مجاني)",
        "desc": "دليلك الكامل لتعلّم البايثون من الصفر حتى الاحتراف مجانًا 🤩👑",
        "link": "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf",
        "photo": None,
        "is_free": True,
    },
    "ecommerce_courses": {
        "title": "🛒 التجارة الإلكترونية (مجاني)",
        "desc": "حزمة دورات وشروحات تجارة إلكترونية (أكثر من 7 ملفات).",
        "link": "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link",
        "photo": None,
        "is_free": True,
    },

    # VIP
    "kash_malik": {
        "title": "♟️ كش ملك (VIP)",
        "desc": "قسم كش ملك – محتوى مميز.",
        "link": "https://drd3m.com/ref/ixeuw",
        "photo": None,
        "local_file": "assets/kash-malik.docx",
        "is_free": False,
    },
    "cyber_sec": {
        "title": "🛡️ الأمن السيبراني (VIP)",
        "desc": "الأمن السيبراني من الصفر \"Cyber security\" 🧑‍💻",
        "link": "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A",
        "photo": None,
        "is_free": False,
    },
    "canva_500": {
        "title": "🖼️ 500 دعوة Canva Pro (VIP)",
        "desc": "دعوات كانفا برو مدى الحياة.",
        "link": "https://digital-plus3.com/products/canva500?srsltid=AfmBOoq01P0ACvybFJkhb2yVBPSUPJadwrOw9LZmNxSUzWPDY8v_42C1",
        "photo": None,
        "is_free": False,
    },
    "dark_gpt": {
        "title": "🕶️ Dark GPT (VIP)",
        "desc": "أداة متقدمة، التفاصيل لاحقاً.",
        "link": "https://t.me/Ferp0ks",
        "photo": None,
        "is_free": False,
    },
    "adobe_win": {
        "title": "🎨 برامج Adobe (ويندوز) (VIP)",
        "desc": "روابط Adobe للويندوز (قريباً).",
        "link": "https://t.me/Ferp0ks",
        "photo": None,
        "is_free": False,
    },

    # مركز الذكاء الاصطناعي
    "ai_hub": {
        "title": "🧠 الذكاء الاصطناعي (VIP)",
        "desc": "مركز أدوات الذكاء الاصطناعي: دردشة AI + تحويل نص إلى صورة.",
        "link": "https://t.me/Ferp0ks",
        "photo": None,
        "is_free": False,
    },
}

# ========= أدوات مساعدة للواجهات =========
def gate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def bottom_menu_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=OWNER_DEEP_LINK)],
    ])

def sections_list_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, sec in SECTIONS.items():
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{key}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def section_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]
    ])

def vip_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ اشترك الآن / تواصل", url=OWNER_DEEP_LINK)],
        [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
    ])

def ai_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],
        [InlineKeyboardButton("🖼️ تحويل نص إلى صورة", callback_data="ai_image")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])

def ai_stop_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 إنهاء وضع الذكاء الاصطناعي", callback_data="ai_stop")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])

# ========= تعديل آمن للرسائل =========
async def safe_edit(q, text: str | None = None, kb: InlineKeyboardMarkup | None = None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb)
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        # تجاهل خطأ "لم يتم تعديل الرسالة"
        if "message is not modified" in str(e).lower():
            try:
                if kb is not None:
                    await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest:
                pass
        else:
            raise

# ========= التحقق من العضوية (فعلي + Retries + كاش 60ث) =========
_member_cache = {}  # {user_id: (ok, expire_ts)}

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force: bool = False, retries: int = 3, backoff: float = 0.7) -> bool:
    now = time.time()
    if not force:
        c = _member_cache.get(user_id)
        if c and c[1] > now:
            return c[0]

    last_ok = False
    for attempt in range(1, retries + 1):
        try:
            cm = await context.bot.get_chat_member(f"@{MAIN_CHANNEL_USERNAME}", user_id)
            status = getattr(cm, "status", None)
            print(f"[is_member] try#{attempt} status={status} user={user_id}")
            ok = status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
            last_ok = ok
            if ok:
                break
        except Exception as e:
            print(f"[is_member] try#{attempt} ERROR: {e}")
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)

    _member_cache[user_id] = (last_ok, now + 60)
    user_set_verify(user_id, last_ok)  # نخزن آخر نتيجة في DB
    return last_ok

# ========= ذكاء اصطناعي (يتعطل تلقائياً إن لم يوجد مفتاح) =========
def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "أجب بالعربية بإيجاز ووضوح."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return (resp.choices[0].message.content or "").strip()
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

# ========= الأوامر =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة\n/debugverify – تشخيص التحقق\n/dv – تشخيص سريع")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application)
    await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    print(f"[debug_verify] received from user={uid}")
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok} (check logs for details)")

# ========= /start (واضح: يا انضم/تحقق، يا قائمة+أقسام) =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    user_get(uid)  # ضمان سجل

    # 1) الترحيب
    if Path(WELCOME_PHOTO).exists():
        with open(WELCOME_PHOTO, "rb") as f:
            await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
    else:
        await context.bot.send_message(chat_id, WELCOME_TEXT_AR)

    # 2) تحقّق فوري الآن
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)

    if not ok:
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        await context.bot.send_message(chat_id, tr("need_admin"))
        return

    # 3) عرض القائمة والأقسام مباشرة
    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())

# ========= الأزرار =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    # زر التحقق
    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك أو تعذّر التحقق.\nانضم ثم اضغط تحقّق.\n\n" + tr("need_admin"), kb=gate_kb())
        return

    # حارس عام: لازم يكون مشترك
    if not await is_member(context, uid, retries=3, backoff=0.7):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "myinfo":
        name = q.from_user.full_name
        txt = f"👤 اسمك: {name}\n🆔 معرفك: {uid}\n\n— شارك المعرف مع الإدارة للترقية إلى VIP."
        await safe_edit(q, txt, kb=bottom_menu_kb(uid)); return

    if q.data == "upgrade":
        await safe_edit(q, "💳 ترقية إلى VIP بـ 10$.\nتواصل مع الإدارة لإتمام الترقية:", kb=vip_prompt_kb()); return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return

    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # الذكاء الاصطناعي
    if q.data == "ai_chat":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, f"🔒 {SECTIONS['ai_hub']['title']}\n\n{tr('access_denied')}\n\n💳 السعر: 10$ — راسل الإدارة للترقية.", kb=vip_prompt_kb()); return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك الآن.", kb=ai_stop_kb()); return

    if q.data == "ai_image":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, f"🔒 {SECTIONS['ai_hub']['title']}\n\n{tr('access_denied')}\n\n💳 السعر: 10$ — راسل الإدارة للترقية.", kb=vip_prompt_kb()); return
        ai_set_mode(uid, "ai_image")
        await safe_edit(q, "🖼️ وضع توليد الصور مفعّل.\nأرسل وصف الصورة بالعربية.", kb=ai_stop_kb()); return

    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb()); return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", kb=sections_list_kb()); return

        # مركز AI يفتح قائمة فرعية
        if key == "ai_hub":
            if not AI_ENABLED:
                await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
            if not (sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID):
                await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 السعر: 10$ — راسل الإدارة للترقية.", kb=vip_prompt_kb()); return
            await safe_edit(q, f"{sec['title']}\n\n{sec['desc']}\n\nاختر أداة:", kb=ai_hub_kb()); return

        is_free = bool(sec.get("is_free"))
        is_allowed = is_free or (user_is_premium(uid) or uid == OWNER_ID)
        title, desc, link = sec["title"], sec["desc"], sec["link"]
        local = sec.get("local_file")
        photo = sec.get("photo")

        if not is_allowed:
            await safe_edit(q, f"🔒 {title}\n\n{tr('access_denied')}\n\n💳 السعر: 10$ — راسل الإدارة للترقية.", kb=vip_prompt_kb()); return

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

# ========= أوامر المدير =========
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0])
    await update.message.reply_text(f"✅ تم تفعيل {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0])
    await update.message.reply_text(f"❌ تم إلغاء {context.args[0]}")

# ========= استقبال رسائل نصية عامة =========
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # لازم يكون مشترك
    if not await is_member(context, uid, retries=3, backoff=0.7):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        return

    # وضع AI؟
    mode = ai_get_mode(uid)
    if mode == "ai_chat":
        prompt = (update.message.text or "").strip()
        if not prompt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        reply = ai_chat_reply(prompt)
        await update.message.reply_text(reply, reply_markup=ai_stop_kb()); return

    if mode == "ai_image":
        prompt = (update.message.text or "").strip()
        if not prompt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
        url = ai_image_url(prompt)
        if isinstance(url, str) and url.startswith("http"):
            try:
                await update.message.reply_photo(photo=url, caption=f"✅ تم إنشاء الصورة بناءً على:\n{prompt}", reply_markup=ai_stop_kb())
            except Exception:
                await update.message.reply_text(f"{url}", reply_markup=ai_stop_kb())
        else:
            await update.message.reply_text(url, reply_markup=ai_stop_kb())
        return

    # ليس في وضع AI → أعرض القائمة والأقسام
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# ========= أخطاء عامة =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Error: {getattr(context, 'error', 'unknown')}")

# ========= الإقلاع =========
async def on_startup(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    # أوامر عامة
    await app.bot.set_my_commands(
        [
            BotCommand("start", "بدء"),
            BotCommand("help", "مساعدة"),
            BotCommand("debugverify", "تشخيص التحقق"),
            BotCommand("dv", "تشخيص سريع"),
        ],
        scope=BotCommandScopeDefault()
    )
    # أوامر للمالك فقط
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
    app.add_handler(CommandHandler(["debugverify", "dv"], debug_verify))

    # أزرار
    app.add_handler(CallbackQueryHandler(on_button))

    # رسائل نصيّة (بدون أوامر)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))

    # أخطاء
    app.add_error_handler(on_error)

    app.run_polling()

if __name__ == "__main__":
    main()
