# -*- coding: utf-8 -*-
import os, sqlite3, threading, time
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InputFile, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest

# ========= بيئة التشغيل =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
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
          premium INTEGER DEFAULT 0
        );
        """)
        _db().commit()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT id, lang, premium FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            c.execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "lang": "ar", "premium": 0}
        return {"id": r["id"], "lang": r["lang"], "premium": r["premium"]}

def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),))
        _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])

# ========= ثوابت =========
OWNER_ID = 6468743821                         # حسابك فقط
MAIN_CHANNEL_USERNAME = "Ferp0ks"             # يوزر القناة العام بدون @
MAIN_CHANNEL_LINK = "https://t.me/Ferp0ks"    # زر الانضمام
OWNER_DEEP_LINK = "tg://user?id=6468743821"   # رابط محادثتك المباشر

WELCOME_PHOTO = "assets/ferpoks.jpg"
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "هنا تلاقي مصادر وأدوات للتجارة الإلكترونية، بايثون، الأمن السيبراني وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

# ========= الأقسام (free/vip) =========
# ملاحظة: photo اختياري (رابط صورة مباشر). local_file لإرسال ملف محلي بدلاً من الرابط.
SECTIONS = {
    # --- مجانية ---
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

    # --- VIP ---
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
}

# ========= نصوص =========
def tr(k: str) -> str:
    M = {
        "follow_gate": "🔐 يجب الاشتراك بالقناة أولاً.",
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "owner_contact": "📨 تواصل مع الإدارة",
        "subscribe_10": "💳 ترقية إلى VIP بـ 10$",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "access_ok": "✅ تم تفعيل اشتراكك.",
        "back": "↩️ رجوع",
        "need_admin": "⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @Ferp0ks.",
    }
    return M.get(k, k)

# ========= كاش عضوية القناة =========
_member_cache = {}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    now = time.time()
    cached = _member_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        chat_ref = f"@{MAIN_CHANNEL_USERNAME}" if MAIN_CHANNEL_USERNAME else None
        cm = await context.bot.get_chat_member(chat_ref, user_id)
        ok = cm.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        ok = False
    _member_cache[user_id] = (ok, now + 600)
    return ok

# ========= تعديل آمن =========
async def safe_edit(q, text: str | None = None, kb: InlineKeyboardMarkup | None = None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb)
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg or "لم يتم تعديل" in msg:
            if kb is not None and text is not None:
                try:
                    await q.edit_message_reply_markup(reply_markup=kb)
                except BadRequest:
                    pass
        else:
            raise

# ========= لوحات الأزرار =========
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

# ========= أوامر / =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(str(update.effective_user.id))

# ========= /start =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    user_get(uid)

    # ترحيب
    if Path(WELCOME_PHOTO).exists():
        with open(WELCOME_PHOTO, "rb") as f:
            await context.bot.send_photo(update.effective_chat.id, InputFile(f), caption=WELCOME_TEXT_AR)
    else:
        await update.message.reply_text(WELCOME_TEXT_AR)

    # تحقق العضوية
    if not await is_member(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        await update.message.reply_text(tr("need_admin"))
        return

    # قائمة + الأقسام (تظهر للجميع بعد الاشتراك)
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# ========= الأزرار =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    # تحقق
    if q.data == "verify":
        if await is_member(context, uid):
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك أو تعذّر التحقق.\nانضم ثم اضغط تحقّق.\n\n" + tr("need_admin"), gate_kb())
        return

    # باقي الأزرار تتطلب اشتراك قناة
    if not await is_member(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", gate_kb())
        return

    if q.data == "myinfo":
        name = q.from_user.full_name
        uid_txt = str(uid)
        txt = f"👤 اسمك: {name}\n🆔 معرفك: {uid_txt}\n\n— شارك المعرف مع الإدارة للترقية إلى VIP."
        await safe_edit(q, txt, bottom_menu_kb(uid))
        return

    if q.data == "upgrade":
        await safe_edit(q, "💳 ترقية إلى VIP بـ 10$.\nتواصل مع الإدارة لإتمام الترقية:", vip_prompt_kb())
        return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", bottom_menu_kb(uid))
        return

    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", sections_list_kb())
        return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", sections_list_kb())
            return

        # مجاني أو VIP؟
        is_free = bool(sec.get("is_free"))
        is_allowed = is_free or (user_is_premium(uid) or uid == OWNER_ID)

        title, desc, link = sec["title"], sec["desc"], sec["link"]
        local = sec.get("local_file")
        photo = sec.get("photo")

        if not is_allowed:
            # مقفول للمشتركين VIP
            await safe_edit(q, f"🔒 {title}\n\n{tr('access_denied')}\n\n💳 السعر: 10$ — راسل الإدارة للترقية.", vip_prompt_kb())
            return

        # مفتوح
        text = f"{title}\n\n{desc}\n\n🔗 الرابط المباشر:\n{link}"
        if local and Path(local).exists():
            await safe_edit(q, f"{title}\n\n{desc}", section_back_kb())
            with open(local, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=f"{title}\n\n🔗 {link}")
        elif photo:
            await safe_edit(q, f"{title}\n\n{desc}", section_back_kb())
            try:
                await q.message.reply_photo(photo=photo, caption=f"{title}\n\n🔗 {link}")
            except Exception:
                await q.message.reply_text(text, reply_markup=section_back_kb())
        else:
            await safe_edit(q, text, section_back_kb())
        return

# ========= أوامر المدير (لك فقط) =========
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

# أي رسالة نصية من غير مشترك → بوابة الاشتراك
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_member(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())

# تنظيف Webhook + ضبط أوامر /
async def on_startup(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    # عامة للجميع
    await app.bot.set_my_commands(
        [BotCommand("start", "بدء"), BotCommand("help", "مساعدة")],
        scope=BotCommandScopeDefault()
    )
    # خاصة بك فقط
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
                BotCommand("id", "معرّفك"),
                BotCommand("grant", "منح صلاحية VIP"),
                BotCommand("revoke", "سحب صلاحية VIP"),
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.run_polling()

if __name__ == "__main__":
    main()
   
