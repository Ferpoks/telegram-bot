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
   

