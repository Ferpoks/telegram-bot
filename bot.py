# -*- coding: utf-8 -*-
import os, re, io, sys, time, zipfile, tempfile, logging, socket, asyncio, base64, signal
import sqlite3
from pathlib import Path
from contextlib import closing, suppress
from typing import Optional, List

import requests

# ==== OpenAI (اختياري) ====
OPENAI_AVAILABLE = False
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# ==== DNS (MX) ====
DNS_AVAILABLE = False
try:
    import dns.resolver
    DNS_AVAILABLE = True
except Exception:
    DNS_AVAILABLE = False

# ==== PDF/Images ====
try:
    import fitz  # PyMuPDF
    from PIL import Image
except Exception as e:
    print("⚠️ يلزم PyMuPDF و Pillow.", file=sys.stderr)
    raise

# ==== yt-dlp ====
YTDLP_AVAILABLE = False
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except Exception:
    YTDLP_AVAILABLE = False

# ==== Telegram ====
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from telegram.error import BadRequest

# ==== AIOHTTP (خادم ويب بسيط لفتح المنفذ في Render) ====
from aiohttp import web

# ========= إعدادات عامة =========
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")
# لوج تفصيلي لمكتبة تيليجرام نفسها
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

ENV_PATH = Path(".env")
if ENV_PATH.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=True)
    except Exception:
        pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN مفقود")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
VT_API_KEY = os.getenv("VT_API_KEY", "").strip()
URLSCAN_API_KEY = os.getenv("URLSCAN_API_KEY", "").strip()

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# ========= إعدادات إضافية (VIP/Verify/صورة ترحيب) =========
def _env_id_list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except Exception:
            pass
    return ids

ADMIN_IDS = _env_id_list("ADMIN_IDS")
VERIFY_CHANNEL_IDS = _env_id_list("VERIFY_CHANNEL_IDS")
WELCOME_IMAGE_PATH = os.getenv("WELCOME_IMAGE_PATH", "").strip()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ========= قاعدة البيانات + ترحيل =========
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def db_init():
    """ترحيل تلقائي للجداول لتفادي 'no column named ...'، مع دعم إعادة تهيئة عبر DB_RESET=1."""
    reset = os.getenv("DB_RESET", "").strip() == "1"

    def has_table(con, name: str) -> bool:
        r = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return bool(r)

    def cols_of(con, name: str) -> list[str]:
        try:
            return [row["name"] for row in con.execute(f"PRAGMA table_info({name})").fetchall()]
        except Exception:
            return []

    with closing(db()) as con, con:
        con.execute("PRAGMA foreign_keys=ON;")
        con.execute("PRAGMA journal_mode=WAL;")

        if reset:
            con.execute("DROP TABLE IF EXISTS kv")
            con.execute("DROP TABLE IF EXISTS users")

        # users
        need_create_users = True
        if has_table(con, "users"):
            c = cols_of(con, "users")
            if set(("user_id","lang","created_at")).issubset(set(c)):
                need_create_users = False
            else:
                con.execute("ALTER TABLE users RENAME TO users_old")
                need_create_users = True

        if need_create_users:
            con.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    lang TEXT NOT NULL DEFAULT 'ar',
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)
            if has_table(con, "users_old"):
                old_cols = cols_of(con, "users_old")
                if "id" in old_cols:
                    con.execute("""
                        INSERT OR IGNORE INTO users(user_id, lang, created_at)
                        SELECT id, COALESCE(lang,'ar'), COALESCE(created_at, strftime('%s','now'))
                        FROM users_old
                    """)
                elif "user_id" in old_cols:
                    con.execute("""
                        INSERT OR IGNORE INTO users(user_id, lang, created_at)
                        SELECT user_id, COALESCE(lang,'ar'), COALESCE(created_at, strftime('%s','now'))
                        FROM users_old
                    """)
                con.execute("DROP TABLE users_old")

        # kv
        need_create_kv = True
        if has_table(con, "kv"):
            c = cols_of(con, "kv")
            if set(("user_id","k","v")).issubset(set(c)):
                need_create_kv = False
            else:
                con.execute("ALTER TABLE kv RENAME TO kv_old")
                need_create_kv = True

        if need_create_kv:
            con.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    user_id INTEGER NOT NULL,
                    k TEXT NOT NULL,
                    v TEXT,
                    PRIMARY KEY (user_id, k)
                )
            """)
            if has_table(con, "kv_old"):
                old_cols = cols_of(con, "kv_old")
                if set(("user","key","value")).issubset(set(old_cols)):
                    con.execute("INSERT OR IGNORE INTO kv(user_id, k, v) SELECT user, key, value FROM kv_old")
                elif set(("user_id","k","v")).issubset(set(old_cols)):
                    con.execute("INSERT OR IGNORE INTO kv(user_id, k, v) SELECT user_id, k, v FROM kv_old")
                con.execute("DROP TABLE kv_old")

        con.execute("CREATE INDEX IF NOT EXISTS idx_kv_user ON kv(user_id)")

def user_lang(uid: int) -> str:
    with closing(db()) as con, con:
        r = con.execute("SELECT lang FROM users WHERE user_id=?", (uid,)).fetchone()
        return r["lang"] if r else "ar"

def set_user_lang(uid: int, lang: str):
    with closing(db()) as con, con:
        con.execute("""INSERT INTO users(user_id,lang) VALUES(?,?)
                       ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang""",
                    (uid, lang))

def kv_set(uid: int, k: str, v: str):
    with closing(db()) as con, con:
        con.execute("""INSERT INTO kv(user_id,k,v) VALUES(?,?,?)
                       ON CONFLICT(user_id,k) DO UPDATE SET v=excluded.v""",
                    (uid, k, v))

def kv_get(uid: int, k: str, default: Optional[str]=None) -> Optional[str]:
    with closing(db()) as con, con:
        r = con.execute("SELECT v FROM kv WHERE user_id=? AND k=?", (uid, k)).fetchone()
        return (r["v"] if r else default)

def set_vip(uid: int, value: bool):
    kv_set(uid, "vip", "1" if value else "0")

def is_vip(uid: int) -> bool:
    return (kv_get(uid, "vip", "0") == "1")

def set_verified(uid: int, value: bool):
    kv_set(uid, "verified_ok", "1" if value else "0")

def is_verified(uid: int) -> bool:
    return (kv_get(uid, "verified_ok", "0") == "1")

# ========= التعريب =========
LOCALES = {
    "ar": {
        "app_title": "لوحة التحكّم","welcome": "مرحبًا! اختر من القائمة ↓",
        "menu_address": "📍 تحديد العناوين","menu_pdf": "📄 أدوات PDF","menu_media": "🎬 تنزيل الوسائط",
        "menu_security": "🛡️ الأمن السيبراني & فحوصات","menu_imggen": "🖼️ توليد الصور (AI)",
        "menu_translate": "🌐 الترجمة","menu_lang": "🌐 اللغة: عربي/English","back": "↩️ رجوع",
        "send_location": "أرسل موقعك (📎 → Location) لتحديد العنوان.",
        "address_result": "العنوان المحتمل:\n{addr}\n\nالإحداثيات: {lat}, {lon}",
        "pdf_title": "اختر أداة PDF:","pdf_to_jpg": "PDF → JPG (ZIP)","jpg_to_pdf": "JPG → PDF (متعدد)",
        "pdf_merge": "دمج PDF (ملفان)","pdf_split": "تقسيم PDF (مدى صفحات)","pdf_compress": "ضغط PDF","pdf_extract": "استخراج نص",
        "pdf_send_file": "أرسل ملف PDF الآن.","jpg_send_images": "أرسل صور JPG/PNG (أكثر من صورة)، ثم اضغط: ✅ إنهاء التحويل",
        "finish_jpg_to_pdf": "✅ إنهاء التحويل","merge_step1": "أرسل **الملف الأول (PDF)**.","merge_step2": "جيد! الآن أرسل **الملف الثاني (PDF)**.",
        "split_ask_range": "أرسل ملف PDF ثم اكتب مدى الصفحات مثل: 1-3 أو 2-2.","compress_hint": "أرسل PDF وسأعيد ضغطه (جودة 60-95).",
        "extract_hint": "أرسل ملف PDF لاستخراج النص منه.","enter_quality": "أدخل جودة الصور (60-95). الافتراضي: 80",
        "enter_pages_range": "اكتب مدى الصفحات الآن (مثال: 1-3).",
        "media_hint": "أرسل رابط الفيديو/الصوت (YouTube, Twitter, Instagram…)\nسأحمّله بأفضل جودة (حد تيليجرام ~2GB).",
        "downloading": "⏳ جاري التنزيل…","too_large": "⚠️ الملف أكبر من حد تيليجرام. رابط مباشر:\n{url}","media_done": "✅ تم تحميل الوسائط وإرسالها.",
        "security_title": "اختر أداة الفحص:","check_url": "🔗 فحص رابط","ip_lookup": "📡 IP Lookup","email_check": "✉️ Email Checker",
        "ask_url": "أرسل الرابط الآن.","ask_ip": "أرسل IP أو اسم النطاق.","ask_email": "أرسل البريد الإلكتروني للتحقق.",
        "url_report": "نتيجة فحص الرابط:\n- الحالة: {status}\n- الوجهة النهائية: {final}\n- الدومين: {host}\n- IP: {ip}\n{extra}",
        "ip_report": "IP Lookup:\n- IP: {ip}\n- الدولة: {country}\n- المدينة: {city}\n- الشركة: {org}\n- ASN: {asn}",
        "email_ok": "✅ البريد يبدو صالحًا وبسجلات MX.","email_bad": "❌ البريد غير صالح أو لا يوجد سجلات MX.","email_warn": "⚠️ تحقق من الصيغة/النطاق، تعذر فحص MX.",
        "imggen_hint": "اكتب وصف الصورة التي تريد توليدها.","imggen_no_key": "⚠️ يلزم OPENAI_API_KEY لتوليد الصور.","imggen_done": "✅ تم توليد الصورة.",
        "translate_choose": "اختر لغة المصدر والوجهة:","translate_from": "من (Source)","translate_to": "إلى (Target)",
        "translate_now": "أرسل النص المراد ترجمته.","translate_done": "✅ الترجمة:","need_text": "أرسل نصًا من فضلك.",
        "lang_switched": "تم تغيير اللغة.","error": "حدث خطأ غير متوقع. حاول مجددًا.",
    },
    "en": {
        "app_title": "Control Panel","welcome": "Welcome! Choose from the menu ↓",
        "menu_address": "📍 Address Finder","menu_pdf": "📄 PDF Tools","menu_media": "🎬 Media Downloader",
        "menu_security": "🛡️ Cybersecurity & Checks","menu_imggen": "🖼️ Image Generation (AI)",
        "menu_translate": "🌐 Translate","menu_lang": "🌐 Language: عربي/English","back": "↩️ Back",
        "send_location": "Send your location (📎 → Location) for reverse-geocoding.",
        "address_result": "Possible address:\n{addr}\n\nCoords: {lat}, {lon}",
        "pdf_title": "Pick a PDF tool:","pdf_to_jpg": "PDF → JPG (ZIP)","jpg_to_pdf": "JPG → PDF (multi)",
        "pdf_merge": "Merge PDFs (2 files)","pdf_split": "Split PDF (range)","pdf_compress": "Compress PDF","pdf_extract": "Extract Text",
        "pdf_send_file": "Send a PDF file now.","jpg_send_images": "Send JPG/PNG images, then press: ✅ Finish",
        "finish_jpg_to_pdf": "✅ Finish","merge_step1": "Send the **first PDF**.","merge_step2": "Now send the **second PDF**.",
        "split_ask_range": "Send a PDF then type a range like: 1-3 or 2-2.","compress_hint": "Send a PDF; I’ll recompress it (quality 60-95).",
        "extract_hint": "Send a PDF to extract its text.","enter_quality": "Enter image quality (60-95). Default: 80","enter_pages_range": "Type the pages range (e.g., 1-3).",
        "media_hint": "Send a video/audio URL (YouTube, Twitter, Instagram…)\nBest quality (Telegram limit ~2GB).",
        "downloading": "⏳ Downloading…","too_large": "⚠️ File exceeds Telegram limit. Direct link:\n{url}","media_done": "✅ Media downloaded & sent.",
        "security_title": "Pick a check:","check_url": "🔗 Check URL","ip_lookup": "📡 IP Lookup","email_check": "✉️ Email Checker",
        "ask_url": "Send the URL now.","ask_ip": "Send an IP or domain name.","ask_email": "Send the email to check.",
        "url_report": "URL Check:\n- Status: {status}\n- Final: {final}\n- Host: {host}\n- IP: {ip}\n{extra}",
        "ip_report": "IP Lookup:\n- IP: {ip}\n- Country: {country}\n- City: {city}\n- Org: {org}\n- ASN: {asn}",
        "email_ok": "✅ Email seems valid with active MX.","email_bad": "❌ Invalid email or no MX records.","email_warn": "⚠️ Check syntax/domain; MX check failed.",
        "imggen_hint": "Type a prompt to generate an image.","imggen_no_key": "⚠️ Image generation requires OPENAI_API_KEY.","imggen_done": "✅ Image generated.",
        "translate_choose": "Choose source and target languages:","translate_from": "From (Source)","translate_to": "To (Target)",
        "translate_now": "Send the text to translate.","translate_done": "✅ Translation:","need_text": "Please send text.",
        "lang_switched": "Language switched.","error": "Unexpected error. Please try again.",
    }
}

LANG_CHOICES = [("ar","العربية"),("en","English"),("fr","Français"),("tr","Türkçe")]

def t(uid_or_lang, key: str) -> str:
    lang = uid_or_lang if isinstance(uid_or_lang, str) else user_lang(int(uid_or_lang))
    return LOCALES.get(lang, LOCALES["ar"]).get(key, key)

# ========= القوائم =========
def main_menu(uid: int) -> InlineKeyboardMarkup:
    lang = user_lang(uid); txt = LOCALES[lang]
    kb = [
        [InlineKeyboardButton(txt["menu_address"], callback_data="menu:address")],
        [InlineKeyboardButton(txt["menu_pdf"], callback_data="menu:pdf")],
        [InlineKeyboardButton(txt["menu_media"], callback_data="menu:media")],
        [InlineKeyboardButton(txt["menu_security"], callback_data="menu:security")],
        [InlineKeyboardButton(txt["menu_imggen"], callback_data="menu:imggen")],
        [InlineKeyboardButton(txt["menu_translate"], callback_data="menu:translate")],
        [InlineKeyboardButton("🧰 إضافات / Extras", callback_data="menu:extras")],
        [InlineKeyboardButton("⭐ VIP & التحقق", callback_data="menu:vip")],
        [InlineKeyboardButton(txt["menu_lang"], callback_data="lang:toggle")],
    ]
    return InlineKeyboardMarkup(kb)

def pdf_menu(uid: int) -> InlineKeyboardMarkup:
    lang = user_lang(uid); txt = LOCALES[lang]
    kb = [
        [InlineKeyboardButton(txt["pdf_to_jpg"], callback_data="pdf:tojpg")],
        [InlineKeyboardButton(txt["jpg_to_pdf"], callback_data="pdf:jpg2pdf"),
         InlineKeyboardButton(txt["finish_jpg_to_pdf"], callback_data="pdf:jpg2pdf_finish")],
        [InlineKeyboardButton(txt["pdf_merge"], callback_data="pdf:merge")],
        [InlineKeyboardButton(txt["pdf_split"], callback_data="pdf:split")],
        [InlineKeyboardButton(txt["pdf_compress"], callback_data="pdf:compress")],
        [InlineKeyboardButton(txt["pdf_extract"], callback_data="pdf:extract")],
        [InlineKeyboardButton(LOCALES[lang]["back"], callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(kb)

def security_menu(uid: int) -> InlineKeyboardMarkup:
    lang = user_lang(uid); txt = LOCALES[lang]
    kb = [
        [InlineKeyboardButton(txt["check_url"], callback_data="sec:url")],
        [InlineKeyboardButton(txt["ip_lookup"], callback_data="sec:ip")],
        [InlineKeyboardButton(txt["email_check"], callback_data="sec:email")],
        [InlineKeyboardButton(LOCALES[lang]["back"], callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(kb)

def extras_menu(uid: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🚀 رشق متابعين (روابطي)", callback_data="ex:smm")],
        [InlineKeyboardButton("🎮 رسالة Epicgames", callback_data="ex:epic")],
        [InlineKeyboardButton(LOCALES[user_lang(uid)]["back"], callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(kb)

def vip_menu(uid: int) -> InlineKeyboardMarkup:
    vip = "✅" if is_vip(uid) else "❌"
    ver = "✅" if is_verified(uid) else "❌"
    kb = [
        [InlineKeyboardButton(f"التحقّق من القنوات {ver}", callback_data="vip:verify")],
        [InlineKeyboardButton("حالة VIP " + vip, callback_data="vip:status")],
        [InlineKeyboardButton(LOCALES[user_lang(uid)]["back"], callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(kb)

def translate_menu(uid: int, step: str="choose_from") -> InlineKeyboardMarkup:
    lang = user_lang(uid)
    if step == "choose_from":
        kb = [[InlineKeyboardButton(name, callback_data=f"tr_from:{code}")]
              for code, name in LANG_CHOICES]
    elif step == "choose_to":
        kb = [[InlineKeyboardButton(name, callback_data=f"tr_to:{code}")]
              for code, name in LANG_CHOICES]
    else:
        kb = []
    kb.append([InlineKeyboardButton(LOCALES[lang]["back"], callback_data="menu:back")])
    return InlineKeyboardMarkup(kb)

# ========= أدوات مساعدة =========
async def safe_answer_callback(query):
    try:
        await query.answer()
    except BadRequest as e:
        msg = str(e)
        if ("Query is too old" in msg) or ("query id is invalid" in msg):
            return
        raise

async def safe_edit(msg, text, **kwargs):
    """حاول تعديل الرسالة؛ لو فشل (قديم/غير معدل) ابعث رسالة جديدة كفال-باك."""
    try:
        await msg.edit_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        with suppress(Exception):
            await msg.reply_text(text, **kwargs)

def get_ctx_message(update: Update):
    if update and update.message:
        return update.message
    if update and update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None

async def send_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    m = get_ctx_message(update)
    if m:
        return await m.reply_text(text, **kwargs)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        return await context.bot.send_message(chat_id, text, **kwargs)

async def send_document(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str, caption: str="", filename: Optional[str]=None, **kwargs):
    m = get_ctx_message(update)
    doc = InputFile(file_path, filename=filename or Path(file_path).name)
    if m:
        return await m.reply_document(doc, caption=caption, **kwargs)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        return await context.bot.send_document(chat_id, doc, caption=caption, **kwargs)

async def send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, file_obj, caption: str="", filename: str="image.png", **kwargs):
    m = get_ctx_message(update)
    photo = InputFile(file_obj, filename=filename)
    if m:
        return await m.reply_photo(photo=photo, caption=caption, **kwargs)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        return await context.bot.send_photo(chat_id, photo=photo, caption=caption, **kwargs)

# ========= لوج لكل تحديث (تشخيص) =========
async def log_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    text = None
    m = update.effective_message
    if m:
        if getattr(m, "text", None):
            text = m.text
        elif getattr(m, "caption", None):
            text = m.caption
        elif getattr(m, "location", None):
            text = "[location]"
        elif getattr(m, "photo", None):
            text = "[photo]"
        elif getattr(m, "document", None):
            mt = m.document.mime_type if m.document else ""
            text = f"[document:{mt}]"
    log.info(f"📥 Update from {uid}: {text or '(no text)'}")

# ========= التحقق من العضوية =========
async def check_required_memberships(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> tuple[bool, list[int]]:
    missing = []
    for cid in VERIFY_CHANNEL_IDS:
        try:
            member = await context.bot.get_chat_member(cid, user_id)
            if getattr(member, "status", "") in ("left", "kicked"):
                missing.append(cid)
        except Exception:
            missing.append(cid)
    return (len(missing) == 0, missing)

# ========= Handlers =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with closing(db()) as con, con:
        con.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    # صورة ترحيب
    if WELCOME_IMAGE_PATH and Path(WELCOME_IMAGE_PATH).exists():
        try:
            with open(WELCOME_IMAGE_PATH, "rb") as f:
                await send_photo(update, context, f, caption=f"🛠️ {t(uid,'app_title')}\n{t(uid,'welcome')}")
        except Exception:
            await send_text(update, context, f"🛠️ {t(uid,'app_title')}\n{t(uid,'welcome')}")
    else:
        await send_text(update, context, f"🛠️ {t(uid,'app_title')}\n\n{t(uid,'welcome')}")
    await send_text(update, context, "اختر من القائمة:", reply_markup=main_menu(uid))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await send_text(update, context,
        "الأقسام: تحديد العناوين / أدوات PDF / تنزيل وسائط / الأمن السيبراني / توليد الصور / الترجمة.\n"
        "استخدم الأزرار للتنقّل. للأوامر:\n"
        "/start — القائمة الرئيسية\n/lang — تبديل اللغة\n/status — حالة VIP",
        reply_markup=main_menu(uid)
    )

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    new_lang = "en" if user_lang(uid) == "ar" else "ar"
    set_user_lang(uid, new_lang)
    await send_text(update, context, t(uid,"lang_switched"), reply_markup=main_menu(uid))

async def cb_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    uid = q.from_user.id
    data = q.data or ""
    await safe_answer_callback(q)

    # نظّف حالات
    for k in ["await","pdf_merge_first","jpg2pdf_list","split_range","compress_quality","tr_from","tr_to","split_pdf_path","compress_pdf_path"]:
        context.user_data.pop(k, None)

    if data == "menu:back":
        await safe_edit(q.message, f"🛠️ {t(uid,'app_title')}\n\n{t(uid,'welcome')}", reply_markup=main_menu(uid)); return
    if data == "menu:address":
        context.user_data["await"] = "address_location"
        await safe_edit(q.message, t(uid,"send_location"), reply_markup=main_menu(uid)); return
    if data == "menu:pdf":
        await safe_edit(q.message, t(uid,"pdf_title"), reply_markup=pdf_menu(uid)); return
    if data == "menu:media":
        context.user_data["await"] = "media_url"
        await safe_edit(q.message, t(uid,"media_hint"), reply_markup=main_menu(uid)); return
    if data == "menu:security":
        await safe_edit(q.message, t(uid,"security_title"), reply_markup=security_menu(uid)); return
    if data == "menu:imggen":
        context.user_data["await"] = "imggen_prompt"
        await safe_edit(q.message, t(uid,"imggen_hint"), reply_markup=main_menu(uid)); return
    if data == "menu:translate":
        await safe_edit(q.message, t(uid,"translate_choose"), reply_markup=translate_menu(uid,"choose_from")); return
    if data == "lang:toggle":
        new_lang = "en" if user_lang(uid) == "ar" else "ar"
        set_user_lang(uid, new_lang)
        await safe_edit(q.message, t(uid,"lang_switched"), reply_markup=main_menu(uid)); return

    # PDF
    if data.startswith("pdf:"):
        op = data.split(":")[1]
        if op == "tojpg":
            context.user_data["await"] = "pdf_to_jpg"
            await safe_edit(q.message, t(uid,"pdf_send_file"), reply_markup=pdf_menu(uid))
        elif op == "jpg2pdf":
            context.user_data["await"] = "jpg2pdf_collect"; context.user_data["jpg2pdf_list"]=[]
            await safe_edit(q.message, t(uid,"jpg_send_images"), reply_markup=pdf_menu(uid))
        elif op == "jpg2pdf_finish":
            imgs = context.user_data.get("jpg2pdf_list") or []
            if not imgs:
                await q.message.reply_text("لا توجد صور بعد.", reply_markup=pdf_menu(uid))
            else:
                await do_jpg_to_pdf_and_send(update, context, imgs)
                context.user_data["jpg2pdf_list"]=[]
        elif op == "merge":
            context.user_data["await"]="pdf_merge_first"
            await safe_edit(q.message, t(uid,"merge_step1"), reply_markup=pdf_menu(uid))
        elif op == "split":
            context.user_data["await"]="pdf_split_file"
            await safe_edit(q.message, t(uid,"split_ask_range"), reply_markup=pdf_menu(uid))
        elif op == "compress":
            context.user_data["await"]="pdf_compress_file"
            await safe_edit(q.message, t(uid,"compress_hint"), reply_markup=pdf_menu(uid))
        elif op == "extract":
            context.user_data["await"]="pdf_extract_file"
            await safe_edit(q.message, t(uid,"extract_hint"), reply_markup=pdf_menu(uid))
        return

    # Security
    if data == "sec:url":
        context.user_data["await"]="sec_url"
        await safe_edit(q.message, t(uid,"ask_url"), reply_markup=security_menu(uid)); return
    if data == "sec:ip":
        context.user_data["await"]="sec_ip"
        await safe_edit(q.message, t(uid,"ask_ip"), reply_markup=security_menu(uid)); return
    if data == "sec:email":
        context.user_data["await"]="sec_email"
        await safe_edit(q.message, t(uid,"ask_email"), reply_markup=security_menu(uid)); return

    # Translate flow
    if data.startswith("tr_from:"):
        code = data.split(":")[1]
        context.user_data["tr_from"]=code
        await safe_edit(q.message, t(uid,"translate_choose"), reply_markup=translate_menu(uid,"choose_to")); return
    if data.startswith("tr_to:"):
        code = data.split(":")[1]
        context.user_data["tr_to"]=code
        context.user_data["await"]="translate_text"
        await safe_edit(q.message, t(uid,"translate_now"), reply_markup=main_menu(uid)); return

    # Extras
    if data == "menu:extras":
        await safe_edit(q.message, "اختر من الإضافات:", reply_markup=extras_menu(uid)); return
    if data == "ex:smm":
        links = [
            ("zyadat.com", "https://zyadat.com/"),
            ("followadd.com", "https://followadd.com"),
            ("smmcpan.com", "https://smmcpan.com"),
            ("seoclevers.com", "https://seoclevers.com"),
            ("followergi.com", "https://followergi.com"),
            ("seorrs.com", "https://seorrs.com"),
            ("drd3m.com/ref/ixeuw", "https://drd3m.com/ref/ixeuw"),
        ]
        rows = [[InlineKeyboardButton(name, url=url)] for name, url in links]
        rows.append([InlineKeyboardButton(LOCALES[user_lang(uid)]["back"], callback_data="menu:back")])
        await safe_edit(q.message, "🚀 مواقع الرشق:", reply_markup=InlineKeyboardMarkup(rows)); return
    if data == "ex:epic":
        txt = ("Hello Epicgames, I am the dad of (اسمك), so my son was on an app called Discord and fell for a phishing site, "
               "logged in with his Epicgames information and someone got into his account. The hacker linked his PSN account so "
               "my son cannot link his own PSN account. I managed to change everything and got the phishing site deleted. "
               "Please unlink the hacker’s PSN from my son’s Epic account so he can play again. Thanks.")
        await safe_edit(q.message, "✉️ انسخ الرسالة وعدّل الاسم:\n\n" + txt, reply_markup=extras_menu(uid)); return

    # VIP
    if data == "menu:vip":
        await safe_edit(q.message, "⭐ VIP & التحقق", reply_markup=vip_menu(uid)); return
    if data == "vip:verify":
        ok, missing = await check_required_memberships(context, uid)
        set_verified(uid, ok)
        if ok:
            await safe_edit(q.message, "✅ تم التحقق: أنت عضو في كل القنوات.", reply_markup=vip_menu(uid))
        else:
            btns = []
            for cid in missing:
                btns.append([InlineKeyboardButton(f"فتح القناة {cid}", url=f"https://t.me/c/{str(cid).replace('-100','')}")])
            btns.append([InlineKeyboardButton(LOCALES[user_lang(uid)]["back"], callback_data="menu:back")])
            await safe_edit(q.message, "❌ لست عضوًا في كل القنوات المطلوبة. انضم ثم اضغط تحقق مجددًا.",
                            reply_markup=InlineKeyboardMarkup(btns))
        return
    if data == "vip:status":
        await safe_edit(q.message, f"⭐ VIP: {'✅' if is_vip(uid) else '❌'}\n🔒 Verified: {'✅' if is_verified(uid) else '❌'}",
                        reply_markup=vip_menu(uid)); return

# ========= الموقع =========
async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.user_data.get("await") != "address_location":
        return
    if not update.message or not update.message.location:
        return
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    addr = await reverse_geocode(lat, lon)
    text = t(uid,"address_result").format(addr=addr or "—", lat=lat, lon=lon)
    await send_text(update, context, text, reply_markup=main_menu(uid))
    context.user_data["await"] = None

async def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    try:
        r = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"format":"jsonv2","lat":lat,"lon":lon},
                         headers={"User-Agent":"TelegramBot/1.0"}, timeout=20)
        if r.ok:
            return r.json().get("display_name")
    except Exception as e:
        log.exception(e)
    return None

# ========= الرسائل =========
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = context.user_data.get("await")
    msg = update.message

    # IMG GEN
    if state == "imggen_prompt":
        prompt = (msg.text or "").strip() if msg and msg.text else ""
        if not prompt:
            await send_text(update, context, t(uid,"need_text")); return
        await do_image_generation(update, context, prompt)
        context.user_data["await"] = None; return

    # MEDIA
    if state == "media_url":
        url = (msg.text or "").strip() if msg and msg.text else ""
        if not url:
            await send_text(update, context, t(uid,"need_text")); return
        await send_text(update, context, t(uid,"downloading"))
        await do_media_download_and_send(update, context, url)
        context.user_data["await"]=None; return

    # SECURITY
    if state == "sec_url":
        url = (msg.text or "").strip()
        await do_check_url(update, context, url)
        context.user_data["await"]=None; return
    if state == "sec_ip":
        query = (msg.text or "").strip()
        await do_ip_lookup(update, context, query)
        context.user_data["await"]=None; return
    if state == "sec_email":
        email = (msg.text or "").strip()
        await do_email_check(update, context, email)
        context.user_data["await"]=None; return

    # PDF — split range input
    if state == "pdf_split_range":
        rng = (msg.text or "").strip()
        context.user_data["split_range"]=rng
        path = context.user_data.get("split_pdf_path")
        if path and Path(path).exists():
            await do_pdf_split_and_send(update, context, path, rng)
        else:
            await send_text(update, context, t(uid,"error"))
        context.user_data["await"]=None; return

    # PDF — compress quality
    if state == "pdf_compress_quality":
        qtxt = (msg.text or "").strip()
        q = 80
        if qtxt.isdigit():
            q = max(60, min(95, int(qtxt)))
        path = context.user_data.get("compress_pdf_path")
        if path and Path(path).exists():
            await do_pdf_compress_and_send(update, context, path, q)
        else:
            await send_text(update, context, t(uid,"error"))
        context.user_data["await"]=None; return

    # Translate text
    if state == "translate_text":
        text = (msg.text or "").strip()
        if not text:
            await send_text(update, context, t(uid,"need_text")); return
        src = context.user_data.get("tr_from","auto")
        dst = context.user_data.get("tr_to","ar")
        res = await do_translate(text, src, dst)
        await send_text(update, context, f"{t(uid,'translate_done')}\n\n{res}", reply_markup=main_menu(uid))
        context.user_data["await"]=None; return

    # JPG→PDF collect
    if state == "jpg2pdf_collect" and msg and (msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"))):
        path = await download_telegram_file(update, context)
        if path:
            imgs = context.user_data.get("jpg2pdf_list") or []
            imgs.append(path)
            context.user_data["jpg2pdf_list"] = imgs
            await send_text(update, context, f"✅ تم إضافة صورة ({len(imgs)})", reply_markup=pdf_menu(uid))
        return

    # PDF operations waiting for a PDF
    if msg and msg.document and msg.document.mime_type == "application/pdf":
        path = await download_telegram_file(update, context)
        if not path: return
        if state == "pdf_to_jpg":
            await do_pdf_to_jpg_and_send(update, context, path)
            context.user_data["await"]=None; return
        if state == "pdf_merge_first":
            context.user_data["pdf_merge_first"]=path
            context.user_data["await"]="pdf_merge_second"
            await send_text(update, context, t(uid,"merge_step2"), reply_markup=pdf_menu(uid)); return
        if state == "pdf_merge_second":
            first = context.user_data.get("pdf_merge_first"); second = path
            if first and second:
                await do_pdf_merge_and_send(update, context, first, second)
            else:
                await send_text(update, context, t(uid,"error"))
            context.user_data["await"]=None; return
        if state == "pdf_split_file":
            context.user_data["split_pdf_path"]=path
            context.user_data["await"]="pdf_split_range"
            await send_text(update, context, t(uid,"enter_pages_range"), reply_markup=pdf_menu(uid)); return
        if state == "pdf_compress_file":
            context.user_data["compress_pdf_path"]=path
            context.user_data["await"]="pdf_compress_quality"
            await send_text(update, context, t(uid,"enter_quality"), reply_markup=pdf_menu(uid)); return
        if state == "pdf_extract_file":
            await do_pdf_extract_text_and_send(update, context, path)
            context.user_data["await"]=None; return

# ========= تنزيل ملفات تيليجرام =========
async def download_telegram_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    try:
        if update.message and update.message.document:
            f = await context.bot.get_file(update.message.document.file_id)
            suffix = Path(update.message.document.file_name or "").suffix or ".bin"
        elif update.message and update.message.photo:
            photo = update.message.photo[-1]
            f = await context.bot.get_file(photo.file_id)
            suffix = ".jpg"
        else:
            return None
        fd, path = tempfile.mkstemp(prefix="tg_", suffix=suffix); os.close(fd)
        await f.download_to_drive(path)
        return path
    except Exception as e:
        log.exception(e); return None

# ========= PDF Tools =========
async def do_pdf_to_jpg_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, pdf_path: str):
    uid = update.effective_user.id
    try:
        tmpdir = tempfile.mkdtemp(prefix="pdf2jpg_")
        doc = fitz.open(pdf_path)
        paths = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=150)
            out = os.path.join(tmpdir, f"page_{i+1:03d}.jpg")
            pix.save(out); paths.append(out)
        zip_path = os.path.join(tmpdir, "pages.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths: z.write(p, arcname=Path(p).name)
        await send_document(update, context, zip_path, caption="✅ PDF → JPG", filename="pdf_pages.zip", reply_markup=pdf_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

async def do_jpg_to_pdf_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, images: List[str]):
    uid = update.effective_user.id
    try:
        pdf_path = os.path.join(tempfile.gettempdir(), f"images_{int(time.time())}.pdf")
        img_objs = [Image.open(p).convert("RGB") for p in images]
        first, rest = img_objs[0], img_objs[1:]
        first.save(pdf_path, save_all=True, append_images=rest)
        await send_document(update, context, pdf_path, caption="✅ JPG → PDF", filename="images.pdf", reply_markup=pdf_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

async def do_pdf_merge_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, p1: str, p2: str):
    uid = update.effective_user.id
    try:
        out = os.path.join(tempfile.gettempdir(), f"merge_{int(time.time())}.pdf")
        d1 = fitz.open(p1); d2 = fitz.open(p2)
        d1.insert_pdf(d2); d1.save(out); d1.close(); d2.close()
        await send_document(update, context, out, caption="✅ Merge Done", filename="merged.pdf", reply_markup=pdf_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

async def do_pdf_split_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, p: str, rng: str):
    uid = update.effective_user.id
    try:
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", rng)
        if not m: await send_text(update, context, "صيغة غير صحيحة. مثال: 1-3"); return
        a, b = int(m.group(1)), int(m.group(2))
        doc = fitz.open(p)
        a = max(1, min(a, len(doc))); b = max(1, min(b, len(doc)))
        if a > b: a, b = b, a
        out = os.path.join(tempfile.gettempdir(), f"split_{a}_{b}_{int(time.time())}.pdf")
        new = fitz.open()
        for i in range(a-1, b): new.insert_pdf(doc, from_page=i, to_page=i)
        new.save(out); new.close(); doc.close()
        await send_document(update, context, out, caption="✅ Split Done", filename=f"split_{a}-{b}.pdf", reply_markup=pdf_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

async def do_pdf_compress_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, p: str, quality: int=80):
    uid = update.effective_user.id
    try:
        doc = fitz.open(p)
        out = os.path.join(tempfile.gettempdir(), f"compress_{quality}_{int(time.time())}.pdf")
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                bio = io.BytesIO()
                pil_img.save(bio, format="JPEG", quality=quality)
                doc.update_stream(xref, bio.getvalue())
        doc.save(out); doc.close()
        await send_document(update, context, out, caption=f"✅ Compress Done (q={quality})", filename=f"compressed_q{quality}.pdf", reply_markup=pdf_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

async def do_pdf_extract_text_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, p: str):
    uid = update.effective_user.id
    try:
        doc = fitz.open(p)
        texts = [page.get_text() for page in doc]
        doc.close()
        text = "\n".join(texts).strip() or "(لا يوجد نص قابل للاستخراج)"
        if len(text) > 4000:
            fp = os.path.join(tempfile.gettempdir(), f"extracted_{int(time.time())}.txt")
            with open(fp, "w", encoding="utf-8") as f: f.write(text)
            await send_document(update, context, fp, caption="✅ Extract Done", filename="extracted.txt", reply_markup=pdf_menu(uid))
        else:
            await send_text(update, context, f"```\n{text}\n```", reply_markup=pdf_menu(uid), parse_mode="Markdown")
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"))

# ========= تنزيل الوسائط =========
async def do_media_download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    uid = update.effective_user.id
    if not YTDLP_AVAILABLE:
        await send_text(update, context, "يلزم تثبيت yt-dlp.", reply_markup=main_menu(uid)); return
    tempdir = tempfile.mkdtemp(prefix="media_")
    outtmpl = os.path.join(tempdir, "%(title).80s [%(id)s].%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "ignoreerrors": True,
        "retries": 3,
    }
    try:
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info: return None
                if "requested_downloads" in info:
                    return info["requested_downloads"][0]["filepath"]
                return ydl.prepare_filename(info)
        loop = asyncio.get_event_loop()
        file_path = await loop.run_in_executor(None, _download)
        if not file_path or not Path(file_path).exists():
            await send_text(update, context, "تعذر التنزيل.", reply_markup=main_menu(uid)); return
        size = Path(file_path).stat().st_size
        # لو mp4 وحجم مناسب حاول إرسال فيديو (مش مستند) لراحة المستخدم
        ext = Path(file_path).suffix.lower()
        try:
            if ext in (".mp4", ".mov", ".m4v") and size <= 49 * 1024 * 1024:
                with open(file_path, "rb") as f:
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=f, caption=t(uid,"media_done"))
            else:
                if size > 1_900_000_000:
                    await send_text(update, context, t(uid,"too_large").format(url=url), reply_markup=main_menu(uid)); return
                await send_document(update, context, file_path, caption=t(uid,"media_done"), reply_markup=main_menu(uid))
        except Exception:
            # فال-باك كمستند
            if size > 1_900_000_000:
                await send_text(update, context, t(uid,"too_large").format(url=url), reply_markup=main_menu(uid)); return
            await send_document(update, context, file_path, caption=t(uid,"media_done"), reply_markup=main_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"), reply_markup=main_menu(uid))

# ========= الأمن السيبراني =========
async def do_check_url(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    uid = update.effective_user.id
    try:
        if not re.match(r"^https?://", url, re.I): url = "http://" + url
        s = requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0 (TelegramBot)"})
        r = s.get(url, allow_redirects=True, timeout=20)
        final_url = r.url; status = f"{r.status_code}"
        host = ""
        with suppress(Exception):
            host = requests.utils.urlparse(final_url).hostname or ""
        ip = "—"
        if host:
            with suppress(Exception):
                ip = socket.gethostbyname(host)
        extra = ""
        if VT_API_KEY: extra += "\n(VirusTotal key متوفر)"
        if URLSCAN_API_KEY: extra += "\n(urlscan key متوفر)"
        text = t(uid,"url_report").format(status=status, final=final_url, host=host, ip=ip, extra=extra)
        await send_text(update, context, text, reply_markup=security_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"), reply_markup=security_menu(uid))

async def do_ip_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    uid = update.effective_user.id
    try:
        host = query.strip(); ip = host
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            with suppress(Exception): ip = socket.gethostbyname(host)
        r = requests.get(f"https://ipapi.co/{ip}/json/", timeout=15)
        data = r.json() if r.ok else {}
        text = t(uid,"ip_report").format(
            ip=ip, country=data.get("country_name","—"),
            city=data.get("city","—"), org=data.get("org","—"), asn=data.get("asn","—")
        )
        await send_text(update, context, text, reply_markup=security_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"), reply_markup=security_menu(uid))

EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
async def do_email_check(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    uid = update.effective_user.id
    try:
        if not EMAIL_RE.match(email):
            await send_text(update, context, t(uid,"email_bad"), reply_markup=security_menu(uid)); return
        domain = email.split("@",1)[1]
        if DNS_AVAILABLE:
            ok_mx = False
            try:
                answers = dns.resolver.resolve(domain, 'MX'); ok_mx = len(answers) > 0
            except Exception:
                ok_mx = False
            await send_text(update, context, t(uid,"email_ok") if ok_mx else t(uid,"email_bad"), reply_markup=security_menu(uid))
        else:
            await send_text(update, context, t(uid,"email_warn"), reply_markup=security_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"), reply_markup=security_menu(uid))

# ========= توليد الصور =========
async def do_image_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    uid = update.effective_user.id
    if not (OPENAI_AVAILABLE and OPENAI_API_KEY):
        await send_text(update, context, t(uid,"imggen_no_key"), reply_markup=main_menu(uid)); return
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        result = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024", n=1)
        b64 = result.data[0].b64_json
        img_bytes = io.BytesIO(base64.b64decode(b64)); img_bytes.seek(0)
        await send_photo(update, context, img_bytes, caption=t(uid,"imggen_done"), filename="image.png", reply_markup=main_menu(uid))
    except Exception as e:
        log.exception(e); await send_text(update, context, t(uid,"error"), reply_markup=main_menu(uid))

# ========= الترجمة =========
async def do_translate(text: str, src: str, dst: str) -> str:
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":"You are a helpful translator."},
                          {"role":"user","content":f"Translate the following text from {src} to {dst}. Keep meaning and tone:\n{text}"}],
                temperature=0.2
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            pass
    return f"[{src}→{dst}] {text}"

# ========= أوامر VIP =========
async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await send_text(update, context, "غير مصرح."); return
    target = None
    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target = int(context.args[0])
    if not target:
        await send_text(update, context, "استخدم: /grant <USER_ID> أو رد على رسالة المستخدم."); return
    set_vip(target, True)
    await send_text(update, context, f"✅ تم منح VIP للمستخدم {target}.")

async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await send_text(update, context, "غير مصرح."); return
    target = None
    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].isdigit():
        target = int(context.args[0])
    if not target:
        await send_text(update, context, "استخدم: /revoke <USER_ID> أو رد على رسالة المستخدم."); return
    set_vip(target, False)
    await send_text(update, context, f"✅ تم إلغاء VIP عن المستخدم {target}.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await send_text(update, context, f"⭐ VIP: {'✅' if is_vip(uid) else '❌'}\n🔒 Verified: {'✅' if is_verified(uid) else '❌'}")

# ========= أخطاء عامة =========
async def errors(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Exception in handler", exc_info=context.error)

# ========= خادم ويب صحي (لـ Render) =========
async def _health(request):
    return web.Response(text="OK", status=200)

async def _start_http_server():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info(f"🌐 Health server started on port {port}")

# ========= تشغيل (Web Service مع polling) =========
async def amain():
    db_init()

    tg = Application.builder().token(BOT_TOKEN).build()
    tg.add_handler(CommandHandler(["start","menu"], start))
    tg.add_handler(CommandHandler("help", help_cmd))
    tg.add_handler(CommandHandler("lang", lang_cmd))
    tg.add_handler(CallbackQueryHandler(cb_nav))
    tg.add_handler(MessageHandler(filters.LOCATION, on_location))
    tg.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    tg.add_handler(CommandHandler("grant", grant_cmd))
    tg.add_handler(CommandHandler("revoke", revoke_cmd))
    tg.add_handler(CommandHandler("status", status_cmd))
    tg.add_error_handler(errors)
    # لوج لكل تحديث (group=99 عشان ما يتداخل مع المعالجة)
    tg.add_handler(MessageHandler(filters.ALL, log_updates), group=99)

    # افتح المنفذ قبل فحص Render
    await _start_http_server()

    # اطبع معلومات البوت + احذف أي Webhook قبل polling
    me = await tg.bot.get_me()
    log.info(f"🤖 Logged in as @{me.username} (id={me.id}) with BOT_TOKEN starting: {BOT_TOKEN[:10]}...")
    with suppress(Exception):
        await tg.bot.delete_webhook(drop_pending_updates=True)
        log.info("🧹 deleteWebhook done (drop_pending_updates=True)")

    # شغّل تيليجرام يدويًا
    await tg.initialize()
    await tg.start()
    await tg.updater.start_polling(drop_pending_updates=True)
    log.info("✅ Bot started.")

    # انتظر إشارة إيقاف (بديل wait_until_closed غير المتوفرة)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    # إيقاف منظّم
    with suppress(Exception):
        await tg.updater.stop()
    with suppress(Exception):
        await tg.stop()
    with suppress(Exception):
        await tg.shutdown()

if __name__ == "__main__":
    asyncio.run(amain())
