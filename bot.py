# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile, subprocess, shutil
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv
from html import escape as _escape

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ==== OpenAI (اختياري) ====
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# ==== Telegram ====
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

# ==== Network / Utils ====
import urllib.parse as _urlparse
import aiohttp
try:
    import whois as pywhois  # pip: python-whois
except Exception:
    pywhois = None
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None
try:
    import yt_dlp  # موجود كاختياري؛ ما عندنا تنزيل فيديو
except Exception:
    yt_dlp = None

# تحميل .env محليًا
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ==== إعدادات أساسية ====
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/tmp/bot.db")   # مهم: /tmp لتفادي Permission Denied
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = None

def _ensure_openai():
    global client
    if client is None and AI_ENABLED and OpenAI is not None:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
        except Exception as e:
            log.error("[openai-init] %s", e)

# Replicate (اختياري للصُور)
REPLICATE_API_TOKEN = (os.getenv("REPLICATE_API_TOKEN") or "").strip()
REPLICATE_MODEL_OWNER = os.getenv("REPLICATE_MODEL_OWNER", "stability-ai")
REPLICATE_MODEL_NAME  = os.getenv("REPLICATE_MODEL_NAME",  "stable-diffusion-xl-base-1.0")
REPLICATE_MODEL_VER   = os.getenv("REPLICATE_MODEL_VER",   "").strip()

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

def need_admin_text(lang="ar") -> str:
    M = {
        "ar": f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @{MAIN_CHANNEL_USERNAMES[0]}.",
        "en": f"⚠️ If verify fails: ensure the bot is admin in @{MAIN_CHANNEL_USERNAMES[0]}."
    }
    return M.get(lang,"ar")

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
CHANNEL_ID = None

# ==== دفع (Paylink) (بدون تغيير) ====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = (os.getenv("PAY_WEBHOOK_SECRET") or "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API        = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE  = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# خدمات الأمن الخارجية
URLSCAN_API_KEY = (os.getenv("URLSCAN_API_KEY") or "").strip()
KICKBOX_API_KEY = (os.getenv("KICKBOX_API_KEY") or "").strip()
IPINFO_TOKEN    = (os.getenv("IPINFO_TOKEN") or "").strip()

# ======= روابط حسب طلبك =======
FOLLOWERS_LINKS = [
    u for u in [
        os.getenv("FOLLOW_LINK_1","https://smmcpan.com/"),
        os.getenv("FOLLOW_LINK_2","https://saudifollow.com/"),
        os.getenv("FOLLOW_LINK_3","https://drd3m.me/"),
    ] if u
]
SERV_NUMBERS_LINKS = [u for u in [os.getenv("NUMBERS_LINK_1","https://txtu.app/")] if u]
SERV_VCC_LINKS = [u for u in [os.getenv("VCC_LINK_1","https://fake-card.com/virtual-card-mastercard-free-card-bin/228757973743900/")] if u]

# دورات (تم تحديث الروابط كما طلبت — ملاحظة: هذه روابط موقّتة)
COURSE_PYTHON_URL = "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250817%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250817T101848Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=619f21a524e6e7c5c2e4a4196323c099978dceefb2d3557f697240df0a14e9db"
COURSE_CYBER_URL  = "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250817%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250817T101926Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=14cc8420107a73dbfe657a1e560ef9dcb762feaf1ba81c4380ae9afe9ff05ba3"
COURSE_EH_URL     = os.getenv("COURSE_EH_URL","https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A")
COURSE_ECOM_URL   = os.getenv("COURSE_ECOM_URL","https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?hl=ar")

# قسم برامج أدوبي (ويندوز)
ADOBE_DOC_URL = "https://docs.google.com/document/d/1gEbrkUBi0SPd69X1XPnbh8RnaE6_IrKD9f95iXbFXV4/edit?tab=t.0#heading=h.atsysbnclvpy"

DARK_GPT_URL = os.getenv("DARK_GPT_URL", "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X")

# ==== خادِم ويب (health + webhook) ====
from aiohttp import web
def _public_url(path: str) -> str:
    base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        host = os.getenv("RENDER_EXTERNAL_HOSTNAME","").strip()
        base = f"https://{host}" if host else ""
    return (base or "").rstrip("/") + path

async def _payhook_aiohttp(request):
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok": False, "error": "bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    ref = None
    try:
        # محاولة استخراج ref شائعة:
        for k in ("orderNumber","merchantOrderNumber","reference","customerRef","ref"):
            if k in data and isinstance(data[k], str):
                ref = data[k]
                break
    except Exception:
        pass
    if not ref:
        return web.json_response({"ok": False, "error": "no-ref"}, status=200)
    activated = payments_mark_paid_by_ref(ref, raw=data)
    log.info("[payhook] ref=%s -> activated=%s", ref, activated)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)}, status=200)

def _run_http_server():
    if os.getenv("SERVE_HEALTH","1") != "1":
        return
    host, port = "0.0.0.0", int(os.getenv("PORT","10000"))
    async def _health(_): return web.json_response({"ok": True})
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    if PAY_WEBHOOK_ENABLE:
        app.router.add_post("/payhook", _payhook_aiohttp)
        app.router.add_get("/payhook", _health)
    async def _start():
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, host, port); await site.start()
        log.info("[http] serving on %s:%d (webhook=%s health=ON)", host, port, "ON" if PAY_WEBHOOK_ENABLE else "OFF")
    loop = asyncio.get_event_loop()
    loop.create_task(_start())

_run_http_server()

# ==== i18n ====
def T(key: str, lang: str | None = None, **kw) -> str:
    AR = {
        "start_pick_lang": "اختر لغتك:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "hello_name": "مرحباً بك يا {name} في بوت فيربوكس! ✨\nستجد: أدوات الذكاء الاصطناعي، الأمن، خدمات مفيدة، دورات، وبرامج أدوبي.",
        "main_menu": "👇 القائمة الرئيسية",
        "btn_myinfo": "👤 معلوماتي",
        "btn_lang": "🌐 تغيير اللغة",
        "btn_vip": "⭐ حساب VIP",
        "btn_contact": "📨 تواصل مع الإدارة",
        "btn_sections": "📂 الأقسام",
        "vip_status_on": "⭐ حسابك VIP (مدى الحياة).",
        "vip_status_off": "⚡ ترقية إلى VIP",
        "gate_join": "🔐 انضم للقناة لاستخدام البوت:",
        "verify": "✅ تحقّق",
        "back": "↩️ رجوع",
        "sections": "📂 الأقسام",
        "sec_ai": "🤖 أدوات الذكاء الاصطناعي",
        "sec_security": "🛡️ الأمن",
        "sec_services": "🧰 خدمات",
        "sec_unban": "🚫 فك الباند",
        "sec_courses": "🎓 الدورات",
        "sec_adobe": "🅰️ برامج أدوبي (ويندوز)",
        "sec_darkgpt": "🕶️ Dark GPT",
        "sec_boost": "📈 رشق متابعين",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
        "send_text": "أرسل النص الآن…",
        "choose_option": "اختر خياراً:",
        "contact_admin": "هنا للتواصل مع الإدارة:",
        "must_join": "🔐 انضم للقناة أولاً:",
        "verify_done": "👌 تم التحقق من اشتراكك بالقناة.",
        "not_verified": "❗️ لم يتم التحقق بعد.",
        "vip_pay_title": "💳 ترقية إلى VIP مدى الحياة ({price:.2f} SAR)",
        "vip_ref": "🔖 مرجعك: <code>{ref}</code>",
        "go_pay": "🚀 الذهاب للدفع",
        "check_pay": "✅ تحقّق الدفع",

        "page_ai": "🤖 أدوات الذكاء الاصطناعي:",
        "btn_ai_chat": "🤖 دردشة",
        "btn_ai_write": "✍️ كتابة إعلان/وصف",
        "btn_ai_translate": "🌐 ترجمة (عربي يمين × إنجليزي يسار)",
        "btn_ai_stt": "🎙️ تحويل صوت لنص",
        "btn_ai_image": "🖼️ توليد صور",

        "page_security": "🛡️ الأمن:",
        "btn_urlscan": "🔗 فحص رابط",
        "btn_emailcheck": "📧 فحص إيميل",
        "btn_geolookup": "🛰️ موقع IP/دومين",

        "page_services": "🧰 خدمات:",
        "btn_numbers": "📱 أرقام مؤقتة",
        "btn_vcc": "💳 فيزا افتراضية",
        "services_numbers": "📱 الأرقام المؤقتة (استخدمها بمسؤولية):",
        "services_vcc": "💳 بطاقات/فيزا افتراضية (قانونية):",

        "page_courses": "🎓 الدورات:",
        "course_python": "بايثون من الصفر",
        "course_cyber": "الأمن السيبراني من الصفر",
        "course_eh": "الهكر الأخلاقي",
        "course_ecom": "التجارة الإلكترونية",

        "page_boost": "📈 رشق متابعين:",
        "adobe_desc": "برامج أدوبي (ويندوز) — قائمة وروابط مباشرة:",
    }
    EN = {
        "start_pick_lang": "Pick your language:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "hello_name": "Welcome {name} to Ferpoks Bot! ✨\nYou’ll find: AI tools, Security, Services, Courses, and Adobe programs.",
        "main_menu": "👇 Main menu",
        "btn_myinfo": "👤 My info",
        "btn_lang": "🌐 Change language",
        "btn_vip": "⭐ VIP Account",
        "btn_contact": "📨 Contact Admin",
        "btn_sections": "📂 Sections",
        "vip_status_on": "⭐ Your VIP is active (lifetime).",
        "vip_status_off": "⚡ Upgrade to VIP",
        "gate_join": "🔐 Join the channel to use the bot:",
        "verify": "✅ Verify",
        "back": "↩️ Back",
        "sections": "📂 Sections",
        "sec_ai": "🤖 AI Tools",
        "sec_security": "🛡️ Security",
        "sec_services": "🧰 Services",
        "sec_unban": "🚫 Unban",
        "sec_courses": "🎓 Courses",
        "sec_adobe": "🅰️ Adobe (Windows)",
        "sec_darkgpt": "🕶️ Dark GPT",
        "sec_boost": "📈 Followers Boost",
        "ai_disabled": "🧠 AI is disabled right now.",
        "send_text": "Send your text…",
        "choose_option": "Choose an option:",
        "contact_admin": "Contact admin here:",
        "must_join": "🔐 Please join the channel first:",
        "verify_done": "👌 You are verified.",
        "not_verified": "❗️ Not verified yet.",
        "vip_pay_title": "💳 Upgrade to lifetime VIP ({price:.2f} SAR)",
        "vip_ref": "🔖 Your reference: <code>{ref}</code>",
        "go_pay": "🚀 Go to payment",
        "check_pay": "✅ Verify payment",

        "page_ai": "🤖 AI Tools:",
        "btn_ai_chat": "🤖 Chat",
        "btn_ai_write": "✍️ Ad/Copy Writing",
        "btn_ai_translate": "🌐 Translate (Arabic RTL × English LTR)",
        "btn_ai_stt": "🎙️ Speech-to-Text",
        "btn_ai_image": "🖼️ Image Gen",

        "page_security": "🛡️ Security:",
        "btn_urlscan": "🔗 URL Scan",
        "btn_emailcheck": "📧 Email Check",
        "btn_geolookup": "🛰️ IP/Domain Geo",

        "page_services": "🧰 Services:",
        "btn_numbers": "📱 Temporary Numbers",
        "btn_vcc": "💳 Virtual Card",
        "services_numbers": "📱 Temporary numbers (use responsibly):",
        "services_vcc": "💳 Virtual/Prepaid card providers:",

        "page_courses": "🎓 Courses:",
        "course_python": "Python from Zero",
        "course_cyber": "Cybersecurity from Zero",
        "course_eh": "Ethical Hacking",
        "course_ecom": "E-commerce",

        "page_boost": "📈 Followers:",
        "adobe_desc": "Adobe programs (Windows) — curated list and direct links:",
    }

    if key in ("ar", "en") and (lang is not None and lang not in ("ar", "en")):
        key, lang = lang, key
    if lang not in ("ar","en"):
        lang = "ar"

    D = AR if lang == "ar" else EN
    s = D.get(key, key)
    try:
        kw = {k: _escape(str(v)) for k,v in kw.items()}
        return s.format(**kw)
    except Exception:
        return s

# ==== قاعدة البيانات ====
_conn_lock = threading.RLock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None: return conn
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
    log.info("[db] using %s", DB_PATH)
    return conn

def migrate_db():
    with _conn_lock:
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0,
          vip_forever INTEGER DEFAULT 0,
          vip_since INTEGER DEFAULT 0,
          pref_lang TEXT DEFAULT 'ar'
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS payments (
            ref TEXT PRIMARY KEY,
            user_id TEXT,
            amount REAL,
            provider TEXT,
            status TEXT,
            created_at INTEGER,
            paid_at INTEGER,
            raw TEXT
        );""")
        _db().commit()

def init_db():
    migrate_db()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "premium":0, "verified_ok":0, "verified_at":0, "vip_forever":0, "vip_since":0, "pref_lang":"ar"}
        return dict(r)

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?",
                      (1 if ok else 0, int(time.time()), str(uid)))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    u = user_get(uid)
    return bool(u.get("premium")) or bool(u.get("vip_forever"))

def user_grant(uid: int|str):
    now = int(time.time())
    with _conn_lock:
        _db().execute(
            "UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0), ?) WHERE id=?",
            (now, str(uid))
        ); _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE id=?", (str(uid),)); _db().commit()

def prefs_set_lang(uid: int|str, lang: str):
    with _conn_lock:
        _db().execute("UPDATE users SET pref_lang=? WHERE id=?", (lang, str(uid))); _db().commit()

def ai_set_mode(uid: int|str, mode: str|None, extra: dict|None=None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id, mode, extra, updated_at) VALUES (?,?,?,strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, extra=excluded.extra, updated_at=strftime('%s','now')",
            (str(uid), mode, json.dumps(extra or {}, ensure_ascii=False))
        ); _db().commit()

def ai_get_mode(uid: int|str):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT mode, extra FROM ai_state WHERE user_id=?", (str(uid),))
        r = c.fetchone()
        if not r: return None, {}
        try:
            extra = json.loads(r["extra"] or "{}")
        except Exception:
            extra = {}
        return r["mode"], extra

# ==== دفعات ====
def payments_new_ref(uid: int) -> str:
    return f"{uid}-{int(time.time())}"

def payments_create(uid: int, amount: float, provider="paylink", ref: str|None=None) -> str:
    ref = ref or payments_new_ref(uid)
    with _conn_lock:
        _db().execute(
            "INSERT OR REPLACE INTO payments (ref, user_id, amount, provider, status, created_at) VALUES (?,?,?,?,?,?)",
            (ref, str(uid), amount, provider, "pending", int(time.time()))
        ); _db().commit()
    return ref

def payments_status(ref: str) -> str | None:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT status FROM payments WHERE ref=?", (ref,))
        r = c.fetchone()
        return r["status"] if r else None

def payments_mark_paid_by_ref(ref: str, raw=None) -> bool:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT user_id, status FROM payments WHERE ref=?", (ref,))
        r = c.fetchone()
        if not r: return False
        if r["status"] == "paid":
            try: user_grant(r["user_id"])
            except Exception as e: log.error("[payments_mark_paid] grant again error: %s", e)
            return True
        user_id = r["user_id"]
        _db().execute(
            "UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
            (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref)
        ); _db().commit()
    try:
        user_grant(user_id)
    except Exception as e:
        log.error("[payments_mark_paid] grant error: %s", e)
    return True

# ==== Paylink API ====
_paylink_token = None
_paylink_token_exp = 0

async def paylink_auth_token():
    global _paylink_token, _paylink_token_exp
    now = time.time()
    if _paylink_token and _paylink_token_exp > now + 10:
        return _paylink_token
    url = f"{PAYLINK_API_BASE}/auth"
    payload = {"apiId": PAYLINK_API_ID, "secretKey": PAYLINK_API_SECRET, "persistToken": False}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=20) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(f"auth failed: {data}")
            token = data.get("token") or data.get("access_token") or data.get("id_token") or data.get("jwt")
            if not token: raise RuntimeError(f"auth failed: {data}")
            _paylink_token = token; _paylink_token_exp = now + 9*60; return token

async def paylink_create_invoice(order_number: str, amount: float, client_name: str):
    token = await paylink_auth_token()
    url = f"{PAYLINK_API_BASE}/addInvoice"
    body = {
        "orderNumber": order_number,
        "amount": amount,
        "clientName": client_name or "Telegram User",
        "clientMobile": "0500000000",
        "currency": "SAR",
        "callBackUrl": _public_url("/payhook"),
        "displayPending": False,
        "note": f"VIP via Telegram #{order_number}",
        "products": [{"title": "VIP Access (Lifetime)", "price": amount, "qty": 1, "isDigital": True}]
    }
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(f"addInvoice failed: {data}")
            pay_url = data.get("url") or data.get("mobileUrl") or data.get("qrUrl")
            if not pay_url: raise RuntimeError(f"addInvoice failed: {data}")
            return pay_url, data

# ==== أدوات تقنية ====
_URL_RE = re.compile(r"https?://[^\s]+")
def md5_hex(s: str) -> str:
    return hashlib.md5(s.strip().lower().encode()).hexdigest()

async def http_head(url: str) -> int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=15) as r:
                return r.status
    except Exception:
        return None

def resolve_ip(host: str) -> str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            if ":" not in ip: return ip
        return infos[0][4][0] if infos else None
    except Exception:
        return None

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

async def fetch_geo(query: str) -> dict|None:
    url = f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                data = await r.json(content_type=None)
                if data.get("status") != "success":
                    return {"error": data.get("message","lookup failed")}
                return data
    except Exception as e:
        return {"error": "network error"}

def fmt_geo(data: dict) -> str:
    if not data: return "⚠️ تعذّر جلب البيانات."
    if data.get("error"): return f"⚠️ {data['error']}"
    parts = []
    parts.append(f"🔎 query: <code>{_escape(str(data.get('query','')))}</code>")
    parts.append(f"🌍 {data.get('country','?')} — {data.get('regionName','?')}")
    parts.append(f"🏙️ {data.get('city','?')} — {data.get('zip','-')}")
    parts.append(f"⏰ {data.get('timezone','-')}")
    parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    parts.append(f"🛰️ AS: {data.get('as','-')}")
    parts.append(f"📍 {data.get('lat','?')}, {data.get('lon','?')}")
    if data.get("reverse"): parts.append(f"🔁 Reverse: {_escape(str(data['reverse']))}")
    parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join(parts)

def whois_domain(domain: str) -> dict|None:
    if pywhois is None:
        return {"error": "python-whois غير مثبت"}
    try:
        w = pywhois.whois(domain)
        return {
            "domain_name": str(w.domain_name) if hasattr(w, "domain_name") else None,
            "registrar": getattr(w, "registrar", None),
            "creation_date": str(getattr(w, "creation_date", None)),
            "expiration_date": str(getattr(w, "expiration_date", None)),
            "emails": getattr(w, "emails", None)
        }
    except Exception as e:
        return {"error": f"whois error: {e}"}

async def urlscan_lookup(u: str) -> str:
    if not URLSCAN_API_KEY:
        return "ℹ️ ضع URLSCAN_API_KEY لتفعيل الفحص."
    try:
        headers = {"API-Key": URLSCAN_API_KEY, "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            data = {"url": u, "visibility": "unlisted"}
            async with s.post("https://urlscan.io/api/v1/scan/", headers=headers, json=data, timeout=30) as r:
                resp = await r.json(content_type=None)
            res = []
            if "result" in resp:
                res.append(f"urlscan: {resp['result']}")
            if "message" in resp:
                res.append(f"msg: {resp['message']}")
            return "\n".join(res) or "urlscan: submitted."
    except Exception as e:
        return f"urlscan error: {e}"

async def kickbox_lookup(email: str) -> str:
    if not KICKBOX_API_KEY:
        return "ℹ️ ضع KICKBOX_API_KEY لتفعيل فحص الإيميل."
    try:
        params = {"email": email, "apikey": KICKBOX_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.kickbox.com/v2/verify", params=params, timeout=20) as r:
                data = await r.json(content_type=None)
        return f"Kickbox: result={data.get('result')} reason={data.get('reason')}"
    except Exception as e:
        return f"kickbox error: {e}"

async def link_scan(u: str) -> str:
    if not _URL_RE.search(u or ""):
        return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    meta = _urlparse.urlparse(u)
    host = meta.hostname or ""
    scheme = meta.scheme
    issues = []
    if scheme != "https": issues.append("❗️ بدون تشفير HTTPS")
    ip = resolve_ip(host) if host else None
    geo_txt = fmt_geo(await fetch_geo(ip)) if ip else "⚠️ تعذّر حلّ IP للمضيف."
    status = await http_head(u)
    if status is None:
        issues.append("⚠️ فشل الوصول (HEAD)")
    else:
        issues.append(f"🔎 حالة HTTP: {status}")
    try:
        us = await urlscan_lookup(u)
        issues.append(us)
    except Exception:
        pass
    return f"🔗 <code>{_escape(u)}</code>\nالمضيف: <code>{_escape(host)}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}"

# ==== صور/نصوص AI ====
async def openai_image_generate(prompt: str) -> bytes|None:
    if not AI_ENABLED or OpenAI is None:
        return None
    _ensure_openai()
    if client is None:
        return None
    try:
        resp = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e)
        return None

def _chat_with_fallback(messages):
    if not AI_ENABLED or OpenAI is None:
        return None, "ai_disabled"
    _ensure_openai()
    if client is None:
        return None, "ai_disabled"
    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = [m for m in [primary, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"] if m]
    seen = set(); ordered = []
    for m in fallbacks:
        if m not in seen: ordered.append(m); seen.add(m)
    last_err = None
    for model in ordered:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.7, timeout=60)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg or "exceeded" in msg:
                return None, "quota"
            if "invalid_api_key" in msg or "Incorrect API key" in msg or "No API key provided" in msg:
                return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or OpenAI is None:
        return T("ai_disabled", lang="ar")
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":"أجب بالعربية أو الإنجليزية حسب لغة المستخدم بإيجاز ووضوح."},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return T("ai_disabled", lang="ar")
        if err == "quota": return "⚠️ نفاد الرصيد."
        if err == "apikey": return "⚠️ مفتاح OpenAI غير صالح أو مفقود."
        if r is None: return "⚠️ تعذّر التنفيذ حالياً."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] unexpected: %s", e)
        return "⚠️ خطأ غير متوقع."

async def tts_whisper_from_file(filepath: str) -> str:
    if not AI_ENABLED or OpenAI is None:
        return T("ai_disabled", lang="ar")
    _ensure_openai()
    if client is None:
        return T("ai_disabled", lang="ar")
    try:
        with open(filepath, "rb") as f:
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e)
        return "⚠️ تعذّر التحويل."

async def translate_bilingual(text: str) -> str:
    """ترجمة بنمط ثنائي: يعرض العربية (RTL) والإنجليزية (LTR) معاً بشكل منسّق."""
    if not AI_ENABLED or OpenAI is None:
        return T("ai_disabled", lang="ar")
    _ensure_openai()
    if client is None:
        return T("ai_disabled", lang="ar")
    sys = (
        "You are a professional bilingual translator. Return TWO sections:\n"
        "1) Arabic (RTL) — high quality, natural, keep formatting where possible.\n"
        "2) English (LTR) — high quality, natural.\n"
        "Rules: No extra commentary. Keep lists and line breaks. If the input is already in one language, translate to the other — but still show both sections."
    )
    user = f"Text to translate (bilingual output):\n\n{text}"
    r, err = _chat_with_fallback([{"role":"system","content":sys},{"role":"user","content":user}])
    if err:
        return "⚠️ تعذّر الترجمة حالياً."
    out = (r.choices[0].message.content or "").strip()

    # إضافة توجيه اتجاه: RLE/PDF للعربية — LRE/PDF للإنجليزية
    RLE = "\u202B"; LRE = "\u202A"; PDF = "\u202C"
    # محاولة تزيين العناوين
    formatted = (
        f"🇸🇦 {RLE}العربية (RTL){PDF}\n{RLE}{out.splitlines()[0] if out else ''}{PDF}\n\n"
        f"🇺🇸 {LRE}English (LTR){PDF}\n{LRE}{out}{PDF}"
    )
    # لو تقسيم بسيط غير دقيق، نكتفي بالنص كما هو:
    return out if len(out) > 2000 else formatted

async def ai_write(prompt: str) -> str:
    if not AI_ENABLED or OpenAI is None:
        return T("ai_disabled", lang="ar")
    sysmsg = (
        "أنت كاتب تسويق ذكي. اكتب نصًا واضحًا ومقنعًا للإعلانات أو صفحات الهبوط أو وصف المنتجات.\n"
        "أخرج: عنوان جذاب، نقاط فوائد مختصرة، دعوة لاتخاذ إجراء (CTA)، ونبرة تناسب الجمهور."
    )
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err: return "⚠️ تعذّر التوليد حالياً."
    return (r.choices[0].message.content or "").strip()

# ==== Telegram UI ====
def gate_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 " + ( "الانضمام للقناة" if lang=="ar" else "Join Channel"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(T("verify", lang=lang), callback_data="verify")]
    ])

def main_menu_kb(uid: int, lang="ar"):
    rows = [
        [InlineKeyboardButton(T("btn_myinfo", lang=lang), callback_data="myinfo")],
        [InlineKeyboardButton(T("btn_lang", lang=lang), callback_data="pick_lang")],
        [InlineKeyboardButton(T("btn_vip", lang=lang), callback_data="vip")],
        [InlineKeyboardButton(T("btn_contact", lang=lang), url=admin_button_url())],
        [InlineKeyboardButton(T("btn_sections", lang=lang), callback_data="sections")]
    ]
    return InlineKeyboardMarkup(rows)

def sections_kb(lang="ar"):
    rows = [
        [InlineKeyboardButton(T("sec_ai", lang=lang), callback_data="sec_ai")],
        [InlineKeyboardButton(T("sec_security", lang=lang), callback_data="sec_security")],
        [InlineKeyboardButton(T("sec_services", lang=lang), callback_data="sec_services")],
        [InlineKeyboardButton(T("sec_unban", lang=lang), callback_data="sec_unban")],
        [InlineKeyboardButton(T("sec_courses", lang=lang), callback_data="sec_courses")],
        [InlineKeyboardButton(T("sec_adobe", lang=lang), callback_data="sec_adobe")],
        [InlineKeyboardButton(T("sec_boost", lang=lang), callback_data="sec_boost")],
        [InlineKeyboardButton(T("sec_darkgpt", lang=lang), url=DARK_GPT_URL)],
        [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
    ]
    return InlineKeyboardMarkup(rows)

def ai_stop_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 " + ( "إنهاء الدردشة" if lang=="ar" else "Stop Chat" ), callback_data="ai_stop")],
        [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
    ])

async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                if kb is not None:
                    await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest:
                pass
        else:
            log.warning("safe_edit error: %s", e)

# ==== Start / Commands ====
async def on_startup(app: Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("[startup] delete_webhook: %s", e)

    global CHANNEL_ID
    CHANNEL_ID = None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id
            log.info("[startup] resolved @%s -> chat_id=%s", u, CHANNEL_ID)
            break
        except Exception as e:
            log.warning("[startup] get_chat @%s failed: %s", u, e)
    if CHANNEL_ID is None:
        log.error("[startup] ❌ could not resolve channel id; fallback to @username checks")

    try:
        await app.bot.set_my_commands(
            [BotCommand("start","Start"), BotCommand("help","Help")],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","Start"), BotCommand("help","Help"),
                BotCommand("id","Your ID"), BotCommand("grant","Grant VIP"),
                BotCommand("revoke","Revoke VIP"), BotCommand("vipinfo","VIP Info"),
                BotCommand("refreshcmds","Refresh Commands"), BotCommand("aidiag","AI diag"),
                BotCommand("libdiag","Lib versions"), BotCommand("paylist","Payments list"),
                BotCommand("restart","Restart"),
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    u = user_get(uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(T("lang_ar", lang="ar"), callback_data="set_lang_ar"),
         InlineKeyboardButton(T("lang_en", lang="ar"), callback_data="set_lang_en")]
    ])
    await context.bot.send_message(chat_id, T("start_pick_lang", lang=u.get("pref_lang","ar")), reply_markup=kb)
    # بديل صورة متحركة: أرسل GIF ترحيبي (اختياري)
    # await context.bot.send_animation(chat_id, animation="https://media.giphy.com/media/xTiTnyZfy6f4S8kVaw/giphy.gif")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = user_get(uid).get("pref_lang","ar")
    await update.message.reply_text(T("main_menu", lang=lang), reply_markup=main_menu_kb(uid, lang))

# ==== الأزرار ====
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except AttributeError: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except AttributeError: pass

_member_cache = {}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now: return cached[0]
    targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
    for attempt in range(1, retries + 1):
        for target in targets:
            try:
                cm = await context.bot.get_chat_member(target, user_id)
                ok = getattr(cm, "status", None) in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id] = (True, now + 60); user_set_verify(user_id, True); return True
            except Exception as e:
                log.warning("[is_member] try#%d target=%s ERROR: %s", attempt, target, e)
        if attempt < retries: await asyncio.sleep(backoff * attempt)
    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False); return False

async def must_be_member_or_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID: return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

# قوالب فكّ الباند (مقوية)
UNBAN_TEMPLATES = {
    "instagram": (
        "Subject: Request for Immediate Review and Restoration of My Instagram Account\n\n"
        "Dear Instagram Support,\n\n"
        "My account appears to have been mistakenly disabled/limited. I strictly follow the Community Guidelines and "
        "believe this action is a false positive. I do not engage in spam, impersonation, or harmful content. "
        "I kindly request a manual review by a human moderator and the restoration of my account.\n\n"
        "If any content triggered the action, I’m ready to remove it and comply immediately. "
        "Please let me know if you require additional information for verification.\n\n"
        "Thank you for your time and assistance.\n"
        "Best regards,"
    ),
    "facebook": (
        "Subject: Urgent Appeal – Account Disabled in Error\n\n"
        "Dear Facebook Support,\n\n"
        "My account was disabled/restricted unexpectedly. I respect Facebook’s Community Standards and believe this "
        "decision was made in error (possibly by automated systems). I request a manual review and reinstatement. "
        "I’m available to confirm my identity and comply with any additional requirements.\n\n"
        "Thank you for your prompt help.\n"
        "Sincerely,"
    ),
    "telegram": (
        "Subject: Appeal to Lift Restriction on Telegram Account/Channel\n\n"
        "Dear Telegram Support,\n\n"
        "My account/channel seems to be limited by mistake. I always adhere to Telegram’s Terms of Service and local laws. "
        "I do not promote illegal activities or abusive behavior. Kindly perform a manual review and lift the restriction.\n\n"
        "If further verification is required, I’m ready to provide it immediately.\n\n"
        "Thank you for your support."
    ),
    "epic": (
        "Subject: Appeal for Ban Removal – Epic Games Account\n\n"
        "Dear Epic Games Support,\n\n"
        "My account has been banned due to what I believe is a misunderstanding. I follow the rules and do not use cheats, "
        "exploits, or third-party tools. I respectfully request a thorough manual review and the removal of the ban. "
        "If any clarification or logs are needed, I’m happy to cooperate.\n\n"
        "Thank you for your time."
    ),
}
UNBAN_LINKS = {
    "instagram": "https://help.instagram.com/contact/606967319425038",
    "facebook":  "https://www.facebook.com/help/contact/260749603972907",
    "telegram":  "https://telegram.org/support",
    "epic":      "https://www.epicgames.com/help/en-US/c4059"
}

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    u = user_get(uid); lang = u.get("pref_lang","ar")
    await q.answer()

    # اختيار اللغة
    if q.data in ("set_lang_ar","set_lang_en"):
        new = "ar" if q.data.endswith("_ar") else "en"
        prefs_set_lang(uid, new)
        name = (q.from_user.username and "@"+q.from_user.username) or (q.from_user.first_name or "صديقي")
        name = _escape(name)
        greeting = T("hello_name", lang=new, name=name)
        text = f"{greeting}\n\n{T('main_menu', lang=new)}"
        await safe_edit(q, text, kb=main_menu_kb(uid, new))
        return

    if q.data == "pick_lang":
        await safe_edit(q, T("start_pick_lang", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("lang_ar", lang=lang), callback_data="set_lang_ar"),
             InlineKeyboardButton(T("lang_en", lang=lang), callback_data="set_lang_en")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
        ])); return

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, T("verify_done", lang=lang), kb=main_menu_kb(uid, lang))
        else:
            await safe_edit(q, T("not_verified", lang=lang) + "\n" + need_admin_text(lang), kb=gate_kb(lang))
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, T("must_join", lang=lang), kb=gate_kb(lang)); return

    if q.data == "myinfo":
        name = (q.from_user.full_name or "").strip()
        await safe_edit(q, T("myinfo", lang=lang, name=name, uid=uid, lng=lang.upper()), kb=main_menu_kb(uid, lang)); return

    if q.data == "back_home":
        await safe_edit(q, T("main_menu", lang=lang), kb=main_menu_kb(uid, lang)); return

    # VIP
    if q.data == "vip":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, T("vip_status_on", lang=lang), kb=main_menu_kb(uid, lang)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        try:
            if USE_PAYLINK_API and PAYLINK_API_ID and PAYLINK_API_SECRET:
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                # لو تستخدم Checkout جاهز
                base = (PAYLINK_CHECKOUT_BASE or "").strip()
                pay_url = f"{base}?ref={ref}" if base else "https://paylink.sa"
            txt = T("vip_pay_title", lang=lang, price=VIP_PRICE_SAR) + "\n" + T("vip_ref", lang=lang, ref=ref)
            await safe_edit(q, txt, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(T("go_pay", lang=lang), url=pay_url)],
                [InlineKeyboardButton(T("check_pay", lang=lang), callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "تعذّر إنشاء/فتح رابط الدفع حالياً.", kb=main_menu_kb(uid, lang))
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_","")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, T("vip_status_on", lang=lang), kb=main_menu_kb(uid, lang))
        else:
            await safe_edit(q, T("not_verified", lang=lang)+"\n"+T("vip_ref", lang=lang, ref=ref), kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(T("check_pay", lang=lang), callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
            ]))
        return

    # الأقسام
    if q.data == "sections":
        await safe_edit(q, T("sections", lang=lang), kb=sections_kb(lang)); return

    # AI
    if q.data == "sec_ai":
        await safe_edit(q, T("page_ai", lang=lang) + "\n\n" + T("choose_option", lang=lang),
                        kb=InlineKeyboardMarkup([
                            [InlineKeyboardButton(T("btn_ai_chat", lang=lang), callback_data="ai_chat")],
                            [InlineKeyboardButton(T("btn_ai_write", lang=lang), callback_data="ai_writer")],
                            [InlineKeyboardButton(T("btn_ai_translate", lang=lang), callback_data="ai_translate")],
                            [InlineKeyboardButton(T("btn_ai_stt", lang=lang), callback_data="ai_stt")],
                            [InlineKeyboardButton(T("btn_ai_image", lang=lang), callback_data="ai_image")],
                            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
                        ])); return

    if q.data == "ai_chat":
        if not AI_ENABLED or OpenAI is None:
            await safe_edit(q, T("ai_disabled", lang=lang), kb=sections_kb(lang)); return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, T("ai_chat_on", lang=lang), kb=ai_stop_kb(lang)); return

    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, T("ai_chat_off", lang=lang), kb=sections_kb(lang)); return

    if q.data == "ai_writer":
        ai_set_mode(uid, "writer")
        await safe_edit(q, "✍️ أرسل تفاصيل الإعلان/الوصف (المنتج/الخدمة، الجمهور، الهدف، النبرة…)\nسأجهّز لك عنوان جذاب + نقاط فوائد + CTA.",
                        kb=ai_stop_kb(lang)); return

    if q.data == "ai_translate":
        ai_set_mode(uid, "translate_bidi")
        await safe_edit(q, "🌐 أرسل النص الآن.\nسيتم عرض: 🇸🇦 العربية (يمين/RTL) × 🇺🇸 الإنجليزية (يسار/LTR) في نفس الرسالة.",
                        kb=ai_stop_kb(lang)); return

    if q.data == "ai_stt":
        ai_set_mode(uid, "stt")
        await safe_edit(q, "🎙️ أرسل مذكرة صوتية وسأحوّلها إلى نص.", kb=ai_stop_kb(lang)); return

    if q.data == "ai_image":
        ai_set_mode(uid, "image_ai")
        await safe_edit(q, "🖼️ أرسل وصف الصورة التي تريد توليدها.", kb=ai_stop_kb(lang)); return

    # الأمن
    if q.data == "sec_security":
        await safe_edit(q, T("page_security", lang=lang) + "\n\n" + T("choose_option", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("btn_urlscan", lang=lang), callback_data="sec_security_url")],
            [InlineKeyboardButton(T("btn_emailcheck", lang=lang), callback_data="sec_security_email")],
            [InlineKeyboardButton(T("btn_geolookup", lang=lang), callback_data="sec_security_geo")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return

    if q.data == "sec_security_url":
        ai_set_mode(uid, "link_scan"); await safe_edit(q, "🛡️ أرسل الرابط للفحص.", kb=ai_stop_kb(lang)); return
    if q.data == "sec_security_email":
        ai_set_mode(uid, "email_check"); await safe_edit(q, "✉️ أرسل الإيميل للفحص.", kb=ai_stop_kb(lang)); return
    if q.data == "sec_security_geo":
        ai_set_mode(uid, "geo_ip"); await safe_edit(q, "📍 أرسل IP أو دومين.", kb=ai_stop_kb(lang)); return

    # الخدمات
    if q.data == "sec_services":
        await safe_edit(q, T("page_services", lang=lang) + "\n\n" + T("services_desc", lang=lang),
                        kb=InlineKeyboardMarkup([
                            [InlineKeyboardButton(T("btn_numbers", lang=lang), callback_data="serv_numbers")],
                            [InlineKeyboardButton(T("btn_vcc", lang=lang), callback_data="serv_vcc")],
                            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
                        ])); return
    if q.data == "serv_numbers":
        nums = SERV_NUMBERS_LINKS or ["https://txtu.app/"]
        rows = [[InlineKeyboardButton(u, url=u)] for u in nums]
        rows.append([InlineKeyboardButton(T("back", lang=lang), callback_data="sec_services")])
        await safe_edit(q, T("services_numbers", lang=lang), kb=InlineKeyboardMarkup(rows)); return
    if q.data == "serv_vcc":
        vcc  = SERV_VCC_LINKS or ["https://fake-card.com/virtual-card-mastercard-free-card-bin/228757973743900/"]
        rows = [[InlineKeyboardButton(u, url=u)] for u in vcc]
        rows.append([InlineKeyboardButton(T("back", lang=lang), callback_data="sec_services")])
        await safe_edit(q, T("services_vcc", lang=lang), kb=InlineKeyboardMarkup(rows)); return

    # فك الباند
    if q.data == "sec_unban":
        await safe_edit(q, "اختر المنصة لرسالة فكّ الباند القوية وروابط التواصل:",
                        kb=InlineKeyboardMarkup([
                            [InlineKeyboardButton("Instagram", callback_data="unban_instagram")],
                            [InlineKeyboardButton("Facebook", callback_data="unban_facebook")],
                            [InlineKeyboardButton("Telegram", callback_data="unban_telegram")],
                            [InlineKeyboardButton("Epic Games", callback_data="unban_epic")],
                            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
                        ])); return

    if q.data.startswith("unban_"):
        key = q.data.replace("unban_","")
        msg = UNBAN_TEMPLATES.get(key,"")
        link = UNBAN_LINKS.get(key,"")
        await safe_edit(q, f"📋 انسخ الرسالة الجاهزة:\n<code>{_escape(msg)}</code>\n\n🔗 تقديم الطلب: {link}",
                        kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_unban")]])); return

    # الدورات
    if q.data == "sec_courses":
        courses = [
            (T("course_python", lang=lang), COURSE_PYTHON_URL),
            (T("course_cyber", lang=lang),  COURSE_CYBER_URL),
            (T("course_eh", lang=lang),     COURSE_EH_URL),
            (T("course_ecom", lang=lang),   COURSE_ECOM_URL),
        ]
        rows = [[InlineKeyboardButton(title, url=url)] for title,url in courses]
        rows.append([InlineKeyboardButton(T("back", lang=lang), callback_data="sections")])
        await safe_edit(q, T("page_courses", lang=lang), kb=InlineKeyboardMarkup(rows)); return

    # برامج أدوبي
    if q.data == "sec_adobe":
        await safe_edit(q, T("adobe_desc", lang=lang),
                        kb=InlineKeyboardMarkup([
                            [InlineKeyboardButton("فتح المستند", url=ADOBE_DOC_URL)],
                            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
                        ])); return

    # الرشق
    if q.data == "sec_boost":
        links = FOLLOWERS_LINKS or ["https://smmcpan.com/","https://saudifollow.com/","https://drd3m.me/"]
        rows = [[InlineKeyboardButton(u.replace("https://","").rstrip("/"), url=u)] for u in links]
        rows.append([InlineKeyboardButton(T("back", lang=lang), callback_data="sections")])
        await safe_edit(q, T("page_boost", lang=lang) + "\n" + "روابط منصات زيادة المتابعين (استخدمها بمسؤولية).", kb=InlineKeyboardMarkup(rows)); return

# ==== تنزيل ملف/صوت من تيليجرام (لاستخدام STT) ====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ==== حارس الرسائل ====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = user_get(uid)
    lang = u.get("pref_lang","ar")

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text(T("gate_join", lang=lang), reply_markup=gate_kb(lang)); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()
        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text), reply_markup=ai_stop_kb(lang)); return
        if mode == "writer":
            out = await ai_write(text); await update.message.reply_text(out, parse_mode="HTML"); return
        if mode == "translate_bidi":
            out = await translate_bilingual(text); await update.message.reply_text(out, parse_mode="HTML"); return
        if mode == "link_scan":
            out = await link_scan(text); await update.message.reply_text(out, parse_mode="HTML", disable_web_page_preview=True); return
        if mode == "email_check":
            if not is_valid_email(text):
                await update.message.reply_text("⚠️ صيغة الإيميل غير صحيحة."); return
            # whois/dns/ipinfo مختصر هنا
            w = whois_domain(text.split("@",1)[1])
            mx_txt = "تحقق MX يتطلب dnspython"
            if dnsresolver:
                try:
                    answers = dnsresolver.resolve(text.split("@",1)[1], "MX")
                    mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
                    mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else "لا يوجد"
                except dnsexception.DNSException:
                    mx_txt = "لا يوجد (فشل الاستعلام)"
            kb = await kickbox_lookup(text)
            who = ("WHOIS: "+w.get("registrar","-")) if w and not w.get("error") else ("WHOIS: "+w.get("error","N/A"))
            await update.message.reply_text(f"📧 {text}\n📮 MX: {mx_txt}\n{who}\n{kb}"); return
        if mode == "geo_ip":
            target = text
            query = target
            if re.fullmatch(r"[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}", target or ""):
                ip = resolve_ip(target)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return

    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p))
            await update.message.reply_text(out); return

    if msg.photo:
        # ما عندنا تحويل ملفات؛ لكن لو أردت بالمستقبل ترجمة صور بالـ Vision فعّل OPENAI_VISION=1 وأضف هنا.
        pass

    if msg.document:
        # لا شيء خاص بالملفات الآن
        pass

    if not mode:
        await update.message.reply_text(T("main_menu", lang=lang), reply_markup=main_menu_kb(uid, lang))

# ==== أوامر المالك ====
async def help_cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("Admin: /id /grant /revoke /vipinfo /refreshcmds /aidiag /libdiag /paylist /restart")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ VIP granted to {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ VIP revoked for {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /vipinfo <user_id>"); return
    u = user_get(context.args[0])
    await update.message.reply_text(json.dumps(u, ensure_ascii=False, indent=2))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ Commands refreshed.")

async def aidiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(pkg):
            try: return version(pkg)
            except PackageNotFoundError: return "not-installed"
        k = (os.getenv("OPENAI_API_KEY") or "").strip()
        msg = (f"AI_ENABLED={'ON' if AI_ENABLED else 'OFF'}\n"
               f"Key={'set(len=%d)'%len(k) if k else 'missing'}\n"
               f"Model={OPENAI_CHAT_MODEL}\n"
               f"openai={v('openai')}\n"
               f"httpx={v('httpx')}\n"
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"aidiag error: {e}")

async def libdiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(pkg):
            try: return version(pkg)
            except PackageNotFoundError: return "not-installed"
        msg = (f"python-telegram-bot={v('python-telegram-bot')}\n"
               f"aiohttp={v('aiohttp')}\n"
               f"httpx={v('httpx')}\n"
               f"python-whois={v('python-whois')}\n"
               f"dnspython={v('dnspython')}\n"
               f"yt-dlp={v('yt-dlp')}\n"
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def paylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    rows = []
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 15")
        rows = [dict(x) for x in c.fetchall()]
    if not rows:
        await update.message.reply_text("لا توجد مدفوعات بعد."); return
    txt = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={ts}")
    await update.message.reply_text("\n".join(txt))

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 Restarting...")
    os._exit(0)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ==== Main ====
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())

    # عامة
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # مالك
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("vipinfo", vipinfo))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
    app.add_handler(CommandHandler("paylist", paylist))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("ownerhelp", help_cmd_owner))

    # أزرار
    app.add_handler(CallbackQueryHandler(on_button))

    # رسائل
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE, guard_messages))
    app.add_handler(MessageHandler(filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))

    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()






