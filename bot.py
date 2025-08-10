# -*- coding: utf-8 -*-
import os, json, sqlite3, threading
from pathlib import Path
from urllib.parse import quote_plus

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

# ========= ثوابت قابلة للتعديل =========
# ملاحظة: التحقق من الاشتراك يحتاج قناة "عامة" لها @username (مو رابط دعوة مؤقت)
MAIN_CHANNEL = "@ferpoks"  # <-- عدّلها ليوزر قناتك العامة
OWNER_CHANNEL = "https://t.me/ferpoks"  # قناة/وسيلة الدفع/التواصل
ADMIN_IDS = {6468743821}  # ضع معرفك كمدير

WELCOME_PHOTO = "assets/ferpoks.jpg"  # ضع الصورة داخل المشروع
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

PRICE_TEXT = "💳 اشتراك 10$ يمنحك الوصول الكامل لكل الأقسام 🌟"

# ===== روابط الأقسام =====
LINKS = {
    "suppliers_pack": {
        "title_ar": "📦 بكج الموردين",
        "title_en": "📦 Suppliers Pack",
        "desc_ar": "ملف شامل لأرقام ومصادر الموردين.",
        "desc_en": "A comprehensive suppliers pack.",
        "buttons": [
            ("فتح المستند", "https://docs.google.com/document/d/1rR2nJMUNDoj0cogeenVh9fYVs_ZTM5W0bl0PBIOVwL0/edit?tab=t.0"),
        ],
    },
    "kash_malik": {
        "title_ar": "♟️ كش ملك",
        "title_en": "♟️ Kash Malik",
        "desc_ar": "مرجع كبير يحتوي على أكثر من 1000 سطر حول التجارة والتواصل الاجتماعي.",
        "desc_en": "Big reference (1000+ lines) on commerce & social.",
        # لو عندك رابط مباشر:
        # "buttons": [("تنزيل الملف (رابط)", "PUT_DIRECT_LINK_HERE")],
        # ولو تريد رفع ملف محلي ضعه هنا:
        "local_file": "assets/kash-malik.docx",  # ضع ملفك f48ud....docx بهذا الاسم
    },
    "cyber_sec": {
        "title_ar": "🛡️ الأمن السيبراني",
        "title_en": "🛡️ Cyber Security",
        "desc_ar": "مراجع ودورات الأمن السيبراني.",
        "desc_en": "Cyber security references.",
        "buttons": [
            # تنبيه: روابط S3 موقّتة، قد تنتهي. الأفضل لاحقاً رفع دائم.
            ("ملف 1", "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250810%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250810T000214Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=aef54ed1c5d583f14beac04516dcf0c69059dfd3a3bf1f9618ea96310841d939"),
            ("ملف/مجلد 2", "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A"),
        ],
    },
    "python_zero": {
        "title_ar": "🐍 البايثون من الصفر",
        "title_en": "🐍 Python from scratch",
        "desc_ar": "ابدأ بايثون من الصفر بمراجع منظّمة.",
        "desc_en": "Start Python from scratch.",
        "buttons": [
            ("ملف PDF", "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250810%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250810T000415Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=d6a041d82021f272e48ba56510e8abc389c1ff27a01666a152d7b7363236e5a6"),
        ],
    },
    "adobe_win": {
        "title_ar": "🎨 برامج الأدوبي (ويندوز)",
        "title_en": "🎨 Adobe (Windows)",
        "desc_ar": "روابط برامج Adobe للويندوز (سنضيف الروابط لاحقاً).",
        "desc_en": "Adobe programs for Windows (links later).",
        "buttons": [
            ("قريباً", "https://t.me/ferpoks"),
        ],
    },
    "ecommerce_courses": {
        "title_ar": "🛒 دورات التجارة الإلكترونية",
        "title_en": "🛒 E-commerce courses",
        "desc_ar": "حزمة دورات وشروحات تجارة إلكترونية.",
        "desc_en": "E-commerce course bundle.",
        "buttons": [
            ("فتح المجلد", "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link"),
        ],
    },
    "canva_500": {
        "title_ar": "🖼️ 500 دعوة كانفا برو",
        "title_en": "🖼️ 500 Canva Pro invites",
        "desc_ar": "دعوات كانفا برو مدى الحياة.",
        "desc_en": "Lifetime Canva Pro invites.",
        "buttons": [
            ("زيارة الصفحة", "https://digital-plus3.com/products/canva500"),
        ],
    },
    "dark_gpt": {
        "title_ar": "🕶️ Dark GPT",
        "title_en": "🕶️ Dark GPT",
        "desc_ar": "أداة/رابط ستتم إضافته لاحقًا.",
        "desc_en": "Will be added later.",
        "buttons": [
            ("قريباً", "https://t.me/ferpoks"),
        ],
    },
}

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
        "sub_desc": PRICE_TEXT,
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
        "sub_desc": PRICE_TEXT,
        "main_menu": "Choose from the menu:",
        "access_denied": "⚠️ Your subscription is not active yet. Contact owner after payment.",
        "access_ok": "✅ Your subscription is active.",
        "lang_switched": "✅ Language switched.",
        "sections": "Available sections:",
        "back": "↩️ Back",
        "open": "Open",
        "download": "Download",
        "commands": "📜 Commands:\n/start – start bot\n/id – your id\n/grant <id> (admin)\n/revoke <id> (admin)",
    # === دوال ترجمة بسيطة ===
def tr_for_user(uid: int, key: str) -> str:
    u = user_get(uid)
    lang = u.get("lang", "ar")
    return T.get(lang, T["ar"]).get(key, key)

def title_for(sec: dict, uid: int) -> str:
    lang = user_get(uid).get("lang", "ar")
    return sec["title_ar"] if lang == "ar" else sec["title_en"]

def desc_for(sec: dict, uid: int) -> str:
    lang = user_get(uid).get("lang", "ar")
    return sec["desc_ar"] if lang == "ar" else sec["desc_en"]

# === عضوية القناة ===
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        cm = await context.bot.get_chat_member(MAIN_CHANNEL, user_id)
        return cm.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False

# === القوائم ===
def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    lang = user_get(uid).get("lang", "ar")
    def L(ar, en): return ar if lang == "ar" else en
    return InlineKeyboardMarkup([
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
    ])

def gate_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr_for_user(uid, "follow_btn"), url=f"https://t.me/{MAIN_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(tr_for_user(uid, "check_btn"), callback_data="verify")]
    ])

# === أوامر عامّة ===
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tr_for_user(update.effective_user.id, "commands"))

# رسالة البداية + صورة
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()  # تأكيد إنشاء الجدول
    uid = update.effective_user.id
    u = user_get(uid)  # ينشئ سجل للمستخدم لو غير موجود

    # أرسل صورة الترحيب إن وجدت
    if Path(WELCOME_PHOTO).exists():
        with open(WELCOME_PHOTO, "rb") as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=InputFile(f),
                caption=tr_for_user(uid, "hello_body"),
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

    # لغة
    if q.data == "lang":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇸🇦 " + T["ar"]["arabic"], callback_data="lang_ar"),
             InlineKeyboardButton("🇬🇧 " + T["ar"]["english"], callback_data="lang_en")],
            [InlineKeyboardButton(tr_for_user(uid, "back"), callback_data="back")]
        ])
        await q.edit_message_text(tr_for_user(uid, "language"), reply_markup=kb)
        return
    if q.data == "lang_ar":
        user_set_lang(uid, "ar")
        await q.edit_message_text(tr_for_user(uid, "lang_switched"), reply_markup=main_menu_kb(uid))
        return
    if q.data == "lang_en":
        user_set_lang(uid, "en")
        await q.edit_message_text(tr_for_user(uid, "lang_switched"), reply_markup=main_menu_kb(uid))
        return

    # التحقق من الاشتراك بالقناة
    if q.data == "verify":
        if await is_member(context, uid):
            await q.edit_message_text(tr_for_user(uid, "main_menu"), reply_markup=main_menu_kb(uid))
        else:
            await q.edit_message_text(tr_for_user(uid, "follow_gate"), reply_markup=gate_kb(uid))
        return

    # اشتراك 10$
    if q.data == "subscribe":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📣 " + tr_for_user(uid, "owner_channel"), url=OWNER_CHANNEL)],
            [InlineKeyboardButton(tr_for_user(uid, "back"), callback_data="back")]
        ])
        await q.edit_message_text(T[user_get(uid)["lang"]]["sub_desc"], reply_markup=kb)
        return

    if q.data == "back":
        await q.edit_message_text(tr_for_user(uid, "main_menu"), reply_markup=main_menu_kb(uid))
        return

    # التأكد من الاشتراك بالقناة قبل الأقسام
    if q.data.startswith("sec_") and not await is_member(context, uid):
        await q.edit_message_text(tr_for_user(uid, "follow_gate"), reply_markup=gate_kb(uid))
        return

    # التحقق من البريميوم قبل فتح الأقسام
    if q.data.startswith("sec_") and not user_is_premium(uid):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(tr_for_user(uid, "subscribe_10"), callback_data="subscribe")],
            [InlineKeyboardButton(tr_for_user(uid, "back"), callback_data="back")]
        ])
        await q.edit_message_text(tr_for_user(uid, "access_denied"), reply_markup=kb)
        return

    # فتح قسم
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = LINKS.get(key)
        if not sec:
            await q.edit_message_text("Soon…")
            return

        title = title_for(sec, uid)
        desc  = desc_for(sec, uid)

        # أزرار الروابط
        rows = []
        for text, url in sec.get("buttons", []):
            rows.append([InlineKeyboardButton(text, url=url)])
        rows.append([InlineKeyboardButton(tr_for_user(uid, "back"), callback_data="back")])

        # ملف محلي إن وجد
        local_file = sec.get("local_file")
        if local_file and Path(local_file).exists():
            await q.edit_message_text(f"{title}\n\n{desc}")
            with open(local_file, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=title, reply_markup=InlineKeyboardMarkup(rows))
        else:
            await q.edit_message_text(f"{title}\n\n{desc}", reply_markup=InlineKeyboardMarkup(rows))
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
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__ == "__main__":
    main()


