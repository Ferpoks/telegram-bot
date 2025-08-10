# -*- coding: utf-8 -*-
import os, json, sqlite3, threading
from pathlib import Path
from urllib.parse import quote_plus
import time

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ChatMemberStatus

# ========= بيئة التشغيل =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود في Environment Variables")

# قاعدة البيانات (اضبط DB_PATH على Render إلى: /var/data/bot.db)
DB_PATH = os.getenv("DB_PATH", "bot.db")
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
        c = _db().cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            lang TEXT DEFAULT 'ar',
            premium INTEGER DEFAULT 0
        );
        """)
        _db().commit()

def user_get(uid: int | str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT id, lang, premium FROM users WHERE id=?", (uid,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "lang": "ar", "premium": 0}
        return {"id": row["id"], "lang": row["lang"], "premium": row["premium"]}

def user_set_lang(uid: int | str, lang: str):
    uid = str(uid)
    with _conn_lock:
        _db().execute("UPDATE users SET lang=? WHERE id=?", (lang, uid))
        _db().commit()

def user_is_premium(uid: int | str) -> bool:
    return bool(user_get(uid)["premium"])

def user_grant(uid: int | str):
    uid = str(uid)
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (uid,))
        _db().commit()

def user_revoke(uid: int | str):
    uid = str(uid)
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (uid,))
        _db().commit()

# دالة الترجمة
def tr_for_user(uid: int, key: str) -> str:
    u = user_get(uid)
    lang = u.get("lang", "ar")  # الحصول على اللغة من قاعدة البيانات
    return T.get(lang, T["ar"]).get(key, key)  # إرجاع الترجمة المناسبة

# ========= ثوابت قابلة للتعديل =========
MAIN_CHANNEL = "@ferpoks"  # <-- عدّلها ليوزر قناتك العامة
OWNER_CHANNEL = "https://t.me/ferpoks"  # قناة/وسيلة الدفع/التواصل
ADMIN_IDS = {6468743821}  # معرفك كمدير فقط (التعديل هنا)
OWNER_ID = 6468743821  # معرف الحساب الذي يمتلك صلاحية الأدمن

# هنا يتم تخزين الصورة المحلية في المشروع بدلاً من رابط غير صحيح
WELCOME_PHOTO = "assets/ferpoks.jpg"  # مسار الصورة المحلي
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "يمكنك معرفة كل ما تحتاجه لفتح متجر إلكتروني مثل أرخص المواقع وأرقام موردين الاشتراكات ومواقع زيادة المتابعين وكل ما يخص التاجر.\n"
    "🎯 لن تحتاج لشراء من أي متجر بعد الآن — يمكنك فعل كل شيء بنفسك."
)
WELCOME_TEXT_EN = (
    "Welcome to FERPOKS bot 🔥\n"
    "Learn everything you need to open an online store: cheapest sources, subscription suppliers, follower growth, and more.\n"
    "🎯 Do it yourself — no need to buy from others."
)

# ========== كاش عضوية القناة ==========
_member_cache = {}  # {user_id: (is_member, expire_ts)}

async def is_member(context, user_id: int) -> bool:
    now = time.time()
    cached = _member_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        cm = await context.bot.get_chat_member(MAIN_CHANNEL, user_id)
        ok = cm.status in ("member","administrator","creator")
    except Exception:
        ok = False
    _member_cache[user_id] = (ok, now + 600)  # 10 دقائق
    return ok

# ========= ترجمة =========
T = {
    "ar": {
        "hello_title": "👋 أهلاً بك!",
        "hello_body": WELCOME_TEXT_AR,
        "start_about": "هذا البوت خاص بقناة Ferpoks. يجب متابعة القناة الأساسية للتحدث مع البوت.",
        "follow_gate": "🔐 يجب الاشتراك بالقناة الأساسية أولاً.",
        "follow_btn": "📣 قناة المسؤول",
        "check_btn": "✅ تفعيل",
        "language": "🌐 اللغة",
        "arabic": "العربية",
        "english": "English",
        "owner_channel": "قناة المسؤول",
        "subscribe_10": "💳 اشتراك 10$",
        "sub_desc": "💳 اشتراك 10$ يمنحك الوصول الكامل لكل الأقسام 🌟",
        "main_menu": "اختر من القائمة:",
        "access_denied": "⚠️ لا تملك اشتراكًا مُفعّلاً بعد. تواصل مع المسؤول بعد الدفع.",
        "access_ok": "✅ تم تفعيل اشتراكك.",
        "lang_switched": "✅ تم تغيير اللغة.",
        "sections": "الأقسام المتاحة:",
        "back": "↩️ رجوع",
        "open": "فتح",
        "download": "تنزيل",
        "commands": "📜 الأوامر:\n/start – بدء البوت\n/id – رقمك\n/grant <id> (مدير)\n/revoke <id> (مدير)",
    },
    "en": {
        "hello_title": "👋 Welcome!",
        "hello_body": WELCOME_TEXT_EN,
        "start_about": "This bot belongs to Ferpoks channel. Join the main channel to chat.",
        "follow_gate": "🔐 Please join our main channel first.",
        "follow_btn": "📣 Owner channel",
        "check_btn": "✅ Verify",
        "language": "🌐 Language",
        "arabic": "العربية",
        "english": "English",
        "owner_channel": "Owner channel",
        "subscribe_10": "💳 Subscribe $10",
        "sub_desc": "💳 Subscribe $10 for full access to all sections 🌟",
        "main_menu": "Choose from the menu:",
        "access_denied": "⚠️ Your subscription is not active yet. Contact owner after payment.",
        "access_ok": "✅ Your subscription is active.",
        "lang_switched": "✅ Language switched.",
        "sections": "Available sections:",
        "back": "↩️ Back",
        "open": "Open",
        "download": "Download",
        "commands": "📜 Commands:\n/start – start bot\n/id – your id\n/grant <id> (admin)\n/revoke <id> (admin)",
    }
}

# === دالة gate_kb لعرض زر الاشتراك بالقناة ===
def gate_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr_for_user(uid, "follow_btn"), url=f"https://t.me/{MAIN_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(tr_for_user(uid, "check_btn"), callback_data="verify")]
    ])

# === القوائم ===
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    lang = user_get(uid).get("lang", "ar")
    def L(ar, en): return ar if lang == "ar" else en
    keyboard = [
        [InlineKeyboardButton(L("📦 بكج الموردين", "📦 Suppliers Pack"), callback_data="sec_suppliers_pack")],
        [InlineKeyboardButton(L("♟️ كش ملك", "♟️ Kash Malik"), callback_data="sec_kash_malik")],
        [InlineKeyboardButton(L("🛡️ الأمن السيبراني", "🛡️ Cyber Security"), callback_data="sec_cyber_sec")],
        [InlineKeyboardButton(L("🐍 البايثون من الصفر", "🐍 Python from scratch"), callback_data="sec_python_zero")],
        [InlineKeyboardButton(L("🎨 برامج الأدوبي (ويندوز)", "🎨 Adobe (Windows)"), callback_data="sec_adobe_win")],
        [InlineKeyboardButton(L("🛒 دورات التجارة الإلكترونية", "🛒 E-commerce courses"), callback_data="sec_ecommerce_courses")],
        [InlineKeyboardButton(L("🖼️ 500 دعوة كانفا برو", "🖼️ 500 Canva Pro invites"), callback_data="sec_canva_500")],
        [InlineKeyboardButton("🕶️ Dark GPT", callback_data="sec_dark_gpt")],
        [
            InlineKeyboardButton("📣 " + tr_for_user(uid, "owner_channel"), url=OWNER_CHANNEL),
            InlineKeyboardButton(tr_for_user(uid, "language"), callback_data="lang")
        ],
        [InlineKeyboardButton(tr_for_user(uid, "subscribe_10"), callback_data="subscribe")]
    ]
    
    # إضافة زر المسؤول فقط إذا كان هو نفسه
    if uid == OWNER_ID:
        keyboard.append([InlineKeyboardButton("🔧 خيارات المسؤول", callback_data="admin_options")])

    return InlineKeyboardMarkup(keyboard)

# === أوامر عامّة ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
    📜 الأوامر المتاحة:
    /start – بدء البوت
    /id – عرض معرف المستخدم
    /grant <id> – منح الصلاحية للمستخدم
    /revoke <id> – سحب الصلاحية من المستخدم
    """)

# رسالة البداية + صورة
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()  # تأكيد إنشاء الجدول
    uid = update.effective_user.id
    u = user_get(uid)  # ينشئ سجل للمستخدم لو غير موجود

    # إرسال صورة الترحيب إذا كانت موجودة
    if Path(WELCOME_PHOTO).exists():
        with open(WELCOME_PHOTO, "rb") as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=InputFile(f),
                caption=tr_for_user(uid, "hello_body")
            )
    else:
        await update.message.reply_text(tr_for_user(uid, "hello_body"))

    # بوابة الاشتراك بالقناة
    if not await is_member(context, uid):
        await update.message.reply_text(tr_for_user(uid, "follow_gate"), reply_markup=gate_kb(uid))
        return

    name = update.effective_user.full_name
    username = ("@" + update.effective_user.username) if update.effective_user.username else "—"
    about = tr_for_user(uid, "start_about")
    await update.message.reply_text(
        f"👋 {name} {username}\n{about}\n\n{tr_for_user(uid,'main_menu')}",
        reply_markup=main_menu_kb(uid)
    )

# === الأزرار ===
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    # التحقق من أن المستخدم هو المسؤول قبل عرض الخيارات
    if q.data == "admin_options" and uid == OWNER_ID:
        await q.edit_message_text("🔧 خيارات المسؤول:\n- إضافة/إلغاء الصلاحيات للمستخدمين\n- تعديل الأقسام")
        return

# === أوامر المدير ===
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>")
        return
    target = context.args[0]
    user_grant(target)
    await update.message.reply_text(f"✅ تم تفعيل الاشتراك للمستخدم {target}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>")
        return
    target = context.args[0]
    user_revoke(target)
    await update.message.reply_text(f"❌ تم إلغاء الاشتراك للمستخدم {target}")

# حذف أي Webhook قديم عند الإقلاع (لتجنّب Conflict)
async def on_startup(app):
    await app.bot.delete_webhook(drop_pending_updates=True)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))  # إضافة دالة المساعدة هنا
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__ == "__main__":
    main()
