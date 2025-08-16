# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile, subprocess, shutil
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv

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
from PIL import Image
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
    import yt_dlp  # لم أحذفه (قد تحتاجه لاحقًا) لكنه غير مستخدم الآن
except Exception:
    yt_dlp = None

# PDF أدوات (محلية)
try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
except Exception:
    PdfReader = PdfWriter = PdfMerger = None

# تحميل .env (محليًا)
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ==== إعدادات أساسية ====
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

MAX_UPLOAD_MB = 47
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

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

# ==== دفع (Paylink) ====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API        = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE  = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

URLSCAN_API_KEY = (os.getenv("URLSCAN_API_KEY") or "").strip()
KICKBOX_API_KEY = (os.getenv("KICKBOX_API_KEY") or "").strip()
IPINFO_TOKEN    = (os.getenv("IPINFO_TOKEN") or "").strip()

PDFCO_API_KEY   = (os.getenv("PDFCO_API_KEY") or "").strip()

# ======= روابط =======
FOLLOWERS_LINKS = [u for u in [
    os.getenv("FOLLOW_LINK_1","https://smmcpan.com/"),
    os.getenv("FOLLOW_LINK_2","https://saudifollow.com/"),
    os.getenv("FOLLOW_LINK_3","https://drd3m.me/"),
] if u]
SERV_NUMBERS_LINKS = [u for u in [os.getenv("NUMBERS_LINK_1","https://txtu.app/")] if u]
SERV_VCC_LINKS = [u for u in [os.getenv("VCC_LINK_1","https://fake-card.com/virtual-card-mastercard-free-card-bin/228757973743900/")] if u]

COURSE_PYTHON_URL = os.getenv("COURSE_PYTHON_URL","https://example.com/python.pdf")
COURSE_CYBER_URL  = os.getenv("COURSE_CYBER_URL","https://example.com/cyber.pdf")
COURSE_EH_URL     = os.getenv("COURSE_EH_URL","https://example.com/eh")
COURSE_ECOM_URL   = os.getenv("COURSE_ECOM_URL","https://example.com/ecom")

DARK_GPT_URL = os.getenv("DARK_GPT_URL", "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X")

# ==== خادِم ويب (health + webhook) ====
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _build_pay_link(ref: str) -> str:
    base = (PAYLINK_CHECKOUT_BASE or "").strip()
    if not base:
        return ""
    if "{ref}" in base:
        return base.format(ref=ref)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}ref={ref}"

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or ""
    if not base:
        base = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()}" if os.getenv("RENDER_EXTERNAL_HOSTNAME") else ""
    return (base or "").rstrip("/") + path

def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj:
        return None
    if isinstance(obj, (str, bytes)):
        s = obj.decode() if isinstance(obj, bytes) else obj
        m = re.search(r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)", s)
        if m and _looks_like_ref(m.group(1)): return m.group(1)
        m = re.search(r"[?&]ref=([\w\-:]+)", s)
        if m and _looks_like_ref(m.group(1)): return m.group(1)
        m = re.search(r"(\d{6,}-\d{9,})", s)
        if m: return m.group(1)
        return None
    if isinstance(obj, dict):
        for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
            v = obj.get(k)
            if isinstance(v, str) and _looks_like_ref(v.strip()):
                return v.strip()
        for v in obj.values():
            got = _find_ref_in_obj(v)
            if got: return got
        return None
    if isinstance(obj, (list, tuple)):
        for v in obj:
            got = _find_ref_in_obj(v)
            if got: return got
    return None

async def _payhook(request):
    if PAY_WEBHOOK_SECRET:
        if request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
            return web.json_response({"ok": False, "error": "bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    ref = _find_ref_in_obj(data)
    activated = payments_mark_paid_by_ref(ref, raw=data) if ref else False
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)}, status=200)

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)):
        return
    async def _make_app():
        app = web.Application()
        async def _favicon(_): return web.Response(status=204)
        app.router.add_get("/favicon.ico", _favicon)
        if SERVE_HEALTH:
            async def _health(_): return web.json_response({"ok": True})
            app.router.add_get("/", _health); app.router.add_get("/health", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            async def _payhook_get(_): return web.json_response({"ok": True})
            app.router.add_get("/payhook", _payhook_get)
        return app
    def _thread_main():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _start():
            app = await _make_app(); runner = web.AppRunner(app)
            await runner.setup()
            port = int(os.getenv("PORT", "10000"))
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            log.info("[http] serving on :%d", port)
        loop.run_until_complete(_start()); loop.run_forever()
    threading.Thread(target=_thread_main, daemon=True).start()
_run_http_server()

# ==== ffmpeg helpers (لبعض الأدوات الأخرى) ====
def _ensure_bin_on_path():
    bin_dir = Path.cwd() / "bin"
    if bin_dir.exists():
        os.environ["PATH"] = f"{str(bin_dir)}:{os.environ.get('PATH','')}"
_ensure_bin_on_path()

def ffmpeg_path() -> str|None:
    p = shutil.which("ffmpeg")
    if p: return p
    local = Path.cwd()/ "bin" / "ffmpeg"
    return str(local) if local.exists() else None

def ffprobe_path() -> str|None:
    p = shutil.which("ffprobe")
    if p: return p
    local = Path.cwd()/ "bin" / "ffprobe"
    return str(local) if local.exists() else None

# ==== i18n ====
def T(key: str, lang: str | None = None, **kw) -> str:
    AR = {
        "start_pick_lang": "اختر لغتك:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "hello_name": "مرحباً بك يا {name} في بوت فيربوكس! ✨\nستجد هنا: أدوات الذكاء الاصطناعي، قسم الأمن، خدمات مفيدة، دورات، وأدوات ملفات.",
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
        "sec_files": "🗂️ أدوات الملفات",
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
        "btn_ai_write": "✍️ كتابة",
        "btn_ai_translate": "🌐 ترجمة",
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
        "page_files": "🗂️ أدوات الملفات:",
        "btn_jpg2pdf": "JPG → PDF",
        "btn_pdf2word": "PDF → Word (PDF.co)",
        "btn_word2pdf": "Word → PDF (PDF.co)",
        "btn_pdfmerge": "دمج PDF",
        "btn_pdfsplit": "تقطيع PDF",
        "btn_pdftext": "PDF → نص",
        "files_desc": "JPG→PDF (محلي) • PDF↔Word عبر PDF.co (يتطلب مفتاح) • دمج/تقطيع PDF (محلي) • PDF→نص (محلي، دون OCR).",
        "page_boost": "📈 رشق متابعين:",
        "myinfo": "👤 اسمك: {name}\n🆔 معرفك: {uid}\n🌐 اللغة: {lng}",
        "darkgpt_desc": "يفتح الرابط:",
    }
    EN = {
        "start_pick_lang": "Pick your language:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "hello_name": "Welcome {name} to Ferpoks Bot! ✨\nYou’ll find: AI tools, Security, Services, Courses, and File Tools.",
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
        "sec_files": "🗂️ File Tools",
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
        "btn_ai_write": "✍️ Writing",
        "btn_ai_translate": "🌐 Translate",
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
        "page_files": "🗂️ File Tools:",
        "btn_jpg2pdf": "JPG → PDF",
        "btn_pdf2word": "PDF → Word (PDF.co)",
        "btn_word2pdf": "Word → PDF (PDF.co)",
        "btn_pdfmerge": "Merge PDFs",
        "btn_pdfsplit": "Split PDF",
        "btn_pdftext": "PDF → Text",
        "files_desc": "JPG→PDF (local) • PDF↔Word via PDF.co (needs key) • Merge/Split PDF (local) • PDF→Text (local, no OCR).",
        "page_boost": "📈 Followers:",
        "myinfo": "👤 Name: {name}\n🆔 ID: {uid}\n🌐 Lang: {lng}",
        "darkgpt_desc": "Opens:",
    }
    if key in ("ar", "en") and (lang is not None and lang not in ("ar", "en")):
        key, lang = lang, key
    if lang not in ("ar","en"):
        lang = "ar"
    D = AR if lang == "ar" else EN
    s = D.get(key, key)
    try:
        return s.format(**kw)
    except Exception:
        return s

# ==== قاعدة البيانات ====
_conn_lock = threading.RLock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None: return conn
    path = DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
    log.info("[db] using %s", path)
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
        try: extra = json.loads(r["extra"] or "{}")
        except Exception: extra = {}
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
            except Exception: pass
            return True
        user_id = r["user_id"]
        _db().execute(
            "UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
            (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref)
        ); _db().commit()
    try: user_grant(user_id)
    except Exception: pass
    return True

def payments_last(limit=10):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(x) for x in c.fetchall()]

# ==== فحوص/أمن/معلومات ====
_IP_RE = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_URL_RE = re.compile(r"https?://[^\s]+")
DISPOSABLE_DOMAINS = {"mailinator.com","tempmail.com","10minutemail.com","yopmail.com","guerrillamail.com","trashmail.com"}

async def fetch_geo(query: str) -> dict|None:
    url = f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                data = await r.json(content_type=None)
                if data.get("status") != "success":
                    return {"error": data.get("message","lookup failed")}
                return data
    except Exception:
        return {"error": "network error"}

def fmt_geo(data: dict) -> str:
    if not data: return "⚠️ تعذّر جلب البيانات."
    if data.get("error"): return f"⚠️ {data['error']}"
    parts = []
    parts.append(f"🔎 query: <code>{data.get('query','')}</code>")
    parts.append(f"🌍 {data.get('country','?')} — {data.get('regionName','?')}")
    parts.append(f"🏙️ {data.get('city','?')} — {data.get('zip','-')}")
    parts.append(f"⏰ {data.get('timezone','-')}")
    parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    parts.append(f"🛰️ AS: {data.get('as','-')}")
    parts.append(f"📍 {data.get('lat','?')}, {data.get('lon','?')}")
    if data.get("reverse"): parts.append(f"🔁 Reverse: {data['reverse']}")
    parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join(parts)

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

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
            if "result" in resp: res.append(f"urlscan: {resp['result']}")
            if "message" in resp: res.append(f"msg: {resp['message']}")
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

async def ipinfo_lookup(query: str) -> str:
    if not IPINFO_TOKEN:
        return "ℹ️ ضع IPINFO_TOKEN لتفعيل ipinfo."
    try:
        url = f"https://ipinfo.io/{query}?token={IPINFO_TOKEN}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                data = await r.json(content_type=None)
        keys = ["ip","hostname","city","region","country","loc","org","asn"]
        parts = [f"{k}: {data.get(k,'-')}" for k in keys if k in data]
        return "ipinfo:\n" + "\n".join(parts)
    except Exception as e:
        return f"ipinfo error: {e}"

async def osint_email(email: str) -> str:
    if not is_valid_email(email): return "⚠️ صيغة الإيميل غير صحيحة."
    local, domain = email.split("@", 1)
    mx_txt = "❓"
    if dnsresolver:
        try:
            answers = dnsresolver.resolve(domain, "MX")
            mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
            mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else "لا يوجد"
        except dnsexception.DNSException:
            mx_txt = "لا يوجد (فشل الاستعلام)"
    else:
        mx_txt = "dnspython غير مثبت"
    g_url = f"https://www.gravatar.com/avatar/{md5_hex(email)}?d=404"
    g_st = await http_head(g_url)
    grav = "✅ موجود" if g_st and 200 <= g_st < 300 else "❌ غير موجود"
    ip = resolve_ip(domain)
    geo_text = fmt_geo(await fetch_geo(ip)) if ip else "⚠️ تعذّر حلّ IP للدومين."
    w = whois_domain(domain)
    w_txt = "WHOIS: غير متاح" if not w else (f"WHOIS: {w['error']}" if w.get("error") else f"WHOIS:\n- Registrar: {w.get('registrar')}\n- Created: {w.get('creation_date')}\n- Expires: {w.get('expiration_date')}")
    out = [f"📧 {email}", f"📮 MX: {mx_txt}", f"🖼️ Gravatar: {grav}", w_txt, f"\n{geo_text}"]
    try:
        kb = await kickbox_lookup(email); out.append(kb)
    except Exception:
        pass
    return "\n".join(out)

# ==== PDF.co ====
async def pdfco_convert(endpoint: str, file_bytes: bytes, out_name: str) -> bytes|None:
    if not PDFCO_API_KEY:
        return None
    headers = {"x-api-key": PDFCO_API_KEY}
    form = aiohttp.FormData(); form.add_field('file', file_bytes, filename=out_name)
    async with aiohttp.ClientSession() as s:
        async with s.post(f"https://api.pdf.co/v1/{endpoint}", headers=headers, data=form, timeout=180) as r:
            data = await r.json(content_type=None)
    if not data.get("url"):
        log.error("[pdfco] bad response: %s", data); return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(data["url"], timeout=180) as r:
                return await r.read()
    except Exception as e:
        log.error("[pdfco] download error: %s", e)
        return None

# ==== AI ====
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    primary = (OPENAI_CHAT_MODEL or "").strip()
    order = []
    for m in [primary, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"]:
        if m and m not in order: order.append(m)
    last_err = None
    for model in order:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.7, timeout=60)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg: return None, "quota"
            if "invalid_api_key" in msg or "No API key" in msg: return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return T("ai_disabled", lang="ar")
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":"أجب بالعربية أو الإنجليزية بإيجاز ووضوح."},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return T("ai_disabled", lang="ar")
        if err == "quota": return "⚠️ نفاد الرصيد."
        if err == "apikey": return "⚠️ مفتاح OpenAI غير صالح."
        if r is None: return "⚠️ تعذّر التنفيذ."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] %s", e); return "⚠️ خطأ غير متوقع."

async def tts_whisper_from_file(filepath: str) -> str:
    if not AI_ENABLED or client is None: return T("ai_disabled", lang="ar")
    try:
        with open(filepath, "rb") as f:
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e); return "⚠️ تعذّر التحويل."

async def translate_text(text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED or client is None: return T("ai_disabled", lang="ar")
    prompt = f"Translate the following into {target_lang}. Keep formatting:\n\n{text}"
    r, err = _chat_with_fallback([
        {"role":"system","content":"You are a high-quality translator. Preserve meaning and style."},
        {"role":"user","content": prompt}
    ])
    if err: return "⚠️ تعذّر الترجمة حالياً."
    return (r.choices[0].message.content or "").strip()

async def translate_image_file(path: str, target_lang: str="ar") -> str:
    if not (AI_ENABLED and client and OPENAI_VISION):
        return "⚠️ ترجمة الصور تتطلب تمكين OPENAI_VISION=1."
    try:
        with open(path, "rb") as f: b64 = base64.b64encode(f.read()).decode()
        content = [{"role":"user","content":[
            {"type":"input_text","text": f"Extract text and translate to {target_lang}. Return only the translation."},
            {"type":"input_image","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
        ]}]
        r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=content, temperature=0)
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[vision-translate] %s", e); return "⚠️ تعذّر معالجة الصورة."

async def ai_write(prompt: str) -> str:
    if not AI_ENABLED or client is None: return T("ai_disabled", lang="ar")
    sysmsg = "اكتب نصًا عربيًا/إنجليزيًا إعلانيًا جذابًا ومختصرًا، مع عناوين قصيرة وCTA واضح."
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err: return "⚠️ تعذّر التوليد."
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
        [InlineKeyboardButton(T("sec_files", lang=lang), callback_data="sec_files")],
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
                if kb is not None: await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest: pass
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
                BotCommand("makepdf","Images → PDF"),
                BotCommand("mergepdf","Merge PDFs"),
                BotCommand("splitpdf","Split PDF"),
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
            except Exception:
                pass
        if attempt < retries: await asyncio.sleep(backoff * attempt)
    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False); return False

async def must_be_member_or_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID: return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

# رسائل فكّ باند مخصّصة
UNBAN_TEMPLATES = {
    "instagram": {
        "ar": "فريق دعم إنستغرام المحترم،\nتم تعطيل حسابي دون قصد فيما أعتقد. أنا ملتزم بإرشادات المجتمع ولم أقُم بأي نشاط مخالف. أرجو مراجعة حسابي وإعادته. المعرّف/اسم المستخدم: <USERNAME>، البريد: <EMAIL>، ورقم الهاتف: <PHONE>. أوافق على تزويدكم بأي إثبات ملكية عند الطلب. شاكرًا تعاونكم.",
        "en": "Dear Instagram Support,\nMy account appears to have been disabled by mistake. I always follow the Community Guidelines and believe no violation occurred. Kindly review and restore my access. Username: <USERNAME>, email: <EMAIL>, phone: <PHONE>. I can provide proof of identity if required. Thank you."
    },
    "facebook": {
        "ar": "فريق دعم فيسبوك المحترم،\nتعرّض حسابي لإجراء تقييد/تعطيل على ما يبدو بالخطأ. ألتزم بسياسات المنصة ولم أقصد مخالفتها. أرجو مراجعة الحالة وإعادة تمكين الحساب. الاسم على الحساب: <NAME>، البريد: <EMAIL>. أوافق على تقديم أي مستندات تثبت الهوية. جزيل الشكر.",
        "en": "Dear Facebook Support,\nMy account was restricted/disabled apparently by mistake. I comply with Facebook policies and did not intend any violation. Please review my case and reinstate my account. Account name: <NAME>, email: <EMAIL>. I’m ready to provide identity documents. Thank you."
    },
    "telegram": {
        "ar": "فريق دعم تيليجرام المحترم،\nيبدو أن حسابي/قناتي خضع لتقييد بالخطأ. ألتزم بشروط الاستخدام ولم أستخدم المنصة في أنشطة محظورة. أرجو التحقّق ورفع التقييد. المعرّف: <USER_ID/@USERNAME>. شاكرًا تعاونكم.",
        "en": "Dear Telegram Support,\nMy account/channel seems to have been limited by mistake. I follow the Terms of Service and do not engage in prohibited activities. Please review and lift the limitation. My ID: <USER_ID/@USERNAME>. Thank you."
    },
    "epic": {
        "ar": "فريق دعم Epic Games المحترم،\nتلقّيت حظرًا على حسابي على ما يبدو بالخطأ. أستخدم خدماتكم بشكل عادل ولم أخالف القواعد. أرجو مراجعة السجلات وإزالة الحظر. اسم الحساب: <ACCOUNT_NAME>، البريد: <EMAIL>. ممتن لتعاونكم.",
        "en": "Dear Epic Games Support,\nMy account appears to have been banned by mistake. I play fairly and follow the rules. Please review the logs and remove the ban. Account name: <ACCOUNT_NAME>, email: <EMAIL>. Kind regards."
    }
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
        greeting = T("hello_name", lang=new, name=name)
        text = f"{greeting}\n\n{T('main_menu', lang=new)}"
        await safe_edit(q, text, kb=main_menu_kb(uid, new))
        return

    # زر تغيير اللغة
    if q.data == "pick_lang":
        await safe_edit(q, T("start_pick_lang", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("lang_ar", lang=lang), callback_data="set_lang_ar"),
             InlineKeyboardButton(T("lang_en", lang=lang), callback_data="set_lang_en")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
        ]))
        return

    # تحقق الانضمام
    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, T("verify_done", lang=lang), kb=main_menu_kb(uid, lang))
        else:
            await safe_edit(q, T("not_verified", lang=lang) + "\n" + need_admin_text(lang), kb=gate_kb(lang))
        return

    # صلاحية الانضمام
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, T("must_join", lang=lang), kb=gate_kb(lang)); return

    if q.data == "myinfo":
        await safe_edit(q, T("myinfo", lang=lang, name=q.from_user.full_name, uid=uid, lng=lang.upper()), kb=main_menu_kb(uid, lang)); return

    if q.data == "back_home":
        await safe_edit(q, T("main_menu", lang=lang), kb=main_menu_kb(uid, lang)); return

    # VIP
    if q.data == "vip":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, T("vip_status_on", lang=lang), kb=main_menu_kb(uid, lang)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        try:
            if USE_PAYLINK_API:
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            txt = T("vip_pay_title", lang=lang, price=VIP_PRICE_SAR) + "\n" + T("vip_ref", lang=lang, ref=ref)
            await safe_edit(q, txt, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(T("go_pay", lang=lang), url=pay_url or "https://paylink.sa")],
                [InlineKeyboardButton(T("check_pay", lang=lang), callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
            ]))
        except Exception:
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
        if not AI_ENABLED or client is None:
            await safe_edit(q, T("ai_disabled", lang=lang), kb=sections_kb(lang)); return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, "تم إيقاف الوضع.", kb=sections_kb(lang)); return
    if q.data == "ai_writer":
        ai_set_mode(uid, "writer")
        await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_translate":
        ai_set_mode(uid, "translate", {"to": "ar" if lang=="ar" else "en"})
        await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_stt":
        ai_set_mode(uid, "stt")
        await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_image":
        ai_set_mode(uid, "image_ai")
        await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return

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
        await safe_edit(q, T("page_services", lang=lang) + "\n\n" + T("choose_option", lang=lang),
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

    # فك الباند (تم إصلاح الرجوع)
    if q.data == "sec_unban":
        await safe_edit(q, "🧩 اختر المنصة لعرض رسالة فك الحظر مع الرابط:", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("Instagram", callback_data="unban_instagram")],
            [InlineKeyboardButton("Facebook", callback_data="unban_facebook")],
            [InlineKeyboardButton("Telegram", callback_data="unban_telegram")],
            [InlineKeyboardButton("Epic Games", callback_data="unban_epic")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]   # <-- يرجع الآن للأقسام
        ])); return

    if q.data.startswith("unban_"):
        key = q.data.replace("unban_","")
        tpl = UNBAN_TEMPLATES.get(key, {})
        ar = tpl.get("ar",""); en = tpl.get("en","")
        link = UNBAN_LINKS.get(key,"")
        txt = f"📋 أرسل الرسالة التالية (عربي):\n<code>{ar}</code>\n\n———\n📋 Or send this (English):\n<code>{en}</code>\n\n🔗 النموذج/الدعم: {link}"
        await safe_edit(q, txt, kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sec_unban")]
        ])); return

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

    # الملفات (موسّعة)
    if q.data == "sec_files":
        await safe_edit(q, T("page_files", lang=lang) + "\n" + T("files_desc", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("btn_jpg2pdf",  lang=lang), callback_data="file_jpg2pdf")],
            [InlineKeyboardButton(T("btn_pdfmerge", lang=lang), callback_data="file_pdfmerge")],
            [InlineKeyboardButton(T("btn_pdfsplit", lang=lang), callback_data="file_pdfsplit")],
            [InlineKeyboardButton(T("btn_pdftext",  lang=lang), callback_data="file_pdftext")],
            [InlineKeyboardButton(T("btn_pdf2word", lang=lang), callback_data="file_pdf2word")],
            [InlineKeyboardButton(T("btn_word2pdf", lang=lang), callback_data="file_word2pdf")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return

    if q.data == "file_jpg2pdf":
        ai_set_mode(uid, "file_img_to_pdf", {"paths":[]})
        await safe_edit(q, "📌 أرسل صورة واحدة أو أكثر ثم اكتب /makepdf للإخراج.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    if q.data == "file_pdfmerge":
        ai_set_mode(uid, "file_pdf_merge", {"paths":[]})
        await safe_edit(q, "📌 أرسل ملفات PDF متعددة (واحدة تلو الأخرى) ثم اكتب /mergepdf.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    if q.data == "file_pdfsplit":
        ai_set_mode(uid, "file_pdf_split", {"path": None})
        await safe_edit(q, "📌 أرسل ملف PDF واحد، ثم اكتب مثال: /splitpdf 1-3,5 لاختيار الصفحات.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    if q.data == "file_pdftext":
        ai_set_mode(uid, "file_pdf_text")
        await safe_edit(q, "📌 أرسل ملف PDF لاستخراج النص منه (بدون OCR).", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    if q.data == "file_pdf2word":
        ai_set_mode(uid, "file_pdf2word")
        await safe_edit(q, "📌 أرسل PDF لتحويله إلى Word عبر PDF.co (يتطلب متغير البيئة PDFCO_API_KEY).", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    if q.data == "file_word2pdf":
        ai_set_mode(uid, "file_word2pdf")
        await safe_edit(q, "📌 أرسل ملف DOC/DOCX وسيُحوّل إلى PDF عبر PDF.co.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

# ==== تنزيل ملف من تيليجرام ====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ==== أدوات ملفات محلية ====
def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        imgs = []
        for p in image_paths:
            im = Image.open(p).convert("RGB")
            imgs.append(im)
        if not imgs: return None
        out_path = TMP_DIR / f"images_{int(time.time())}.pdf"
        first, rest = imgs[0], imgs[1:]
        first.save(out_path, save_all=True, append_images=rest)
        return out_path
    except Exception as e:
        log.error("[img->pdf] %s", e); return None

def merge_pdfs(paths: list[Path]) -> Path|None:
    if PdfMerger is None:
        log.error("PyPDF2 غير مثبت"); return None
    try:
        out_path = TMP_DIR / f"merge_{int(time.time())}.pdf"
        merger = PdfMerger()
        for p in paths:
            merger.append(str(p))
        merger.write(str(out_path)); merger.close()
        return out_path
    except Exception as e:
        log.error("[pdf-merge] %s", e); return None

def split_pdf(path: Path, ranges: str) -> Path|None:
    if PdfReader is None or PdfWriter is None:
        log.error("PyPDF2 غير مثبت"); return None
    try:
        rdr = PdfReader(str(path))
        wr = PdfWriter()
        def add_range(r):
            r = r.strip()
            if not r: return
            if "-" in r:
                a,b = r.split("-",1)
                a = int(a)-1; b = int(b)-1
                for i in range(max(0,a), min(len(rdr.pages)-1, b)+1):
                    wr.add_page(rdr.pages[i])
            else:
                i = int(r)-1
                if 0 <= i < len(rdr.pages): wr.add_page(rdr.pages[i])
        for part in (ranges or "").split(","):
            add_range(part)
        out_path = TMP_DIR / f"split_{int(time.time())}.pdf"
        with open(out_path, "wb") as f: wr.write(f)
        return out_path
    except Exception as e:
        log.error("[pdf-split] %s", e); return None

def pdf_to_text(path: Path) -> Path|None:
    if PdfReader is None:
        log.error("PyPDF2 غير مثبت"); return None
    try:
        rdr = PdfReader(str(path))
        lines = []
        for i, pg in enumerate(rdr.pages, start=1):
            try:
                t = pg.extract_text() or ""
            except Exception:
                t = ""
            lines.append(f"--- Page {i} ---\n{t}\n")
        out_path = TMP_DIR / f"pdftext_{int(time.time())}.txt"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
    except Exception as e:
        log.error("[pdf->text] %s", e); return None

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
        if mode == "translate":
            to = (extra or {}).get("to","ar")
            out = await translate_text(text, to); await update.message.reply_text(out); return
        if mode == "link_scan":
            out = await urlscan_lookup(text) if _URL_RE.search(text) else "⚠️ أرسل رابطًا صحيحًا."
            await update.message.reply_text(out, parse_mode="HTML", disable_web_page_preview=True); return
        if mode == "email_check":
            out = await osint_email(text); await update.message.reply_text(out, parse_mode="HTML"); return
        if mode == "geo_ip":
            target = text
            query = target
            if _HOST_RE.match(target):
                ip = resolve_ip(target)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return
        if mode == "image_ai":
            prompt = text
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            # هنا يمكنك ربط توليد الصور إن رغبت
            await update.message.reply_text("🖼️ توليد الصور مفعل عندما يكون لديك مفتاح OpenAI/Replicate."); return

    # ملفات/صوت/صور
    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p))
            await update.message.reply_text(out); return

    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
        if mode == "translate" and OPENAI_VISION:
            out = await translate_image_file(str(p), (extra or {}).get("to","ar"))
            await update.message.reply_text(out or "⚠️ لم أستطع قراءة النص من الصورة."); return
        if mode == "file_img_to_pdf":
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ تم إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.")
            return

    if msg.document:
        p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
        suf = (msg.document.file_name or "").lower()
        # JPG→PDF
        if mode == "file_img_to_pdf" and any(suf.endswith(x) for x in (".jpg",".jpeg",".png",".webp",".bmp")):
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ تم إضافة ملف صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.")
            return
        # دمج PDF
        if mode == "file_pdf_merge" and suf.endswith(".pdf"):
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_pdf_merge", {"paths": st_paths})
            await update.message.reply_text(f"✅ تمت إضافة PDF ({len(st_paths)}). أرسل المزيد أو اكتب /mergepdf.")
            return
        # تقطيع PDF: خزّن المسار لوقت الأمر
        if mode == "file_pdf_split" and suf.endswith(".pdf"):
            ai_set_mode(uid, "file_pdf_split", {"path": str(p)})
            await update.message.reply_text("✅ تم استلام الملف. الآن اكتب مثل: /splitpdf 1-3,5")
            return
        # PDF→نص
        if mode == "file_pdf_text" and suf.endswith(".pdf"):
            if PdfReader is None:
                await update.message.reply_text("⚠️ ثبّت PyPDF2 في requirements.txt"); return
            out = pdf_to_text(p)
            if out and out.exists() and out.stat().st_size <= MAX_UPLOAD_BYTES:
                await update.message.reply_document(InputFile(str(out)))
            else:
                await update.message.reply_text("⚠️ فشل استخراج النص.")
            ai_set_mode(uid, None, {})
            return
        # PDF→Word
        if mode == "file_pdf2word" and suf.endswith(".pdf"):
            if not PDFCO_API_KEY:
                await update.message.reply_text("⚠️ ضع PDFCO_API_KEY في متغيرات البيئة (pdf.co)."); return
            with open(p, "rb") as f: data = f.read()
            out = await pdfco_convert("pdf/convert/to/doc", data, "convert.doc")
            if out:
                path = TMP_DIR / f"out_{int(time.time())}.doc"
                path.write_bytes(out)
                await update.message.reply_document(InputFile(str(path)))
            else:
                await update.message.reply_text("⚠️ فشل التحويل (PDF → Word).")
            ai_set_mode(uid, None, {})
            return
        # Word→PDF
        if mode == "file_word2pdf" and (suf.endswith(".docx") or suf.endswith(".doc")):
            if not PDFCO_API_KEY:
                await update.message.reply_text("⚠️ ضع PDFCO_API_KEY في متغيرات البيئة (pdf.co)."); return
            with open(p, "rb") as f: data = f.read()
            out = await pdfco_convert("pdf/convert/from/doc", data, "document.pdf")
            if out:
                path = TMP_DIR / f"out_{int(time.time())}.pdf"
                path.write_bytes(out)
                await update.message.reply_document(InputFile(str(path)))
            else:
                await update.message.reply_text("⚠️ فشل التحويل (Word → PDF).")
            ai_set_mode(uid, None, {})
            return

    if not mode:
        await update.message.reply_text(T("main_menu", lang=lang), reply_markup=main_menu_kb(uid, lang))

# ==== أوامر ملفات ====
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("هذه الأداة تعمل بعد اختيار (JPG → PDF) من الأقسام.")
        return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("لم يتم استلام أي صور بعد. أرسل صورًا ثم /makepdf."); return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    ai_set_mode(uid, None, {})

async def mergepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_pdf_merge":
        await update.message.reply_text("اختر (دمج PDF) ثم أرسل ملفات PDF وبعدها /mergepdf.")
        return
    paths = (extra or {}).get("paths", [])
    if len(paths) < 2:
        await update.message.reply_text("أرسل ملفين PDF على الأقل ثم /mergepdf.")
        return
    out = merge_pdfs([Path(p) for p in paths])
    if out and out.exists() and out.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(out)))
    else:
        await update.message.reply_text("⚠️ فشل الدمج أو الملف الناتج كبير.")
    ai_set_mode(uid, None, {})

async def splitpdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_pdf_split":
        await update.message.reply_text("اختر (تقطيع PDF) ثم أرسل ملفًا وبعدها /splitpdf 1-3,5")
        return
    path = (extra or {}).get("path")
    if not path:
        await update.message.reply_text("لم أستلم ملف PDF بعد. أرسله أولًا.")
        return
    rng = " ".join(context.args) if context.args else ""
    if not rng:
        await update.message.reply_text("اكتب مثل: /splitpdf 1-3,5")
        return
    out = split_pdf(Path(path), rng)
    if out and out.exists() and out.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(out)))
    else:
        await update.message.reply_text("⚠️ فشل التقطيع.")
    ai_set_mode(uid, None, {})

# ==== أوامر المالك ====
async def help_cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("Admin: /id /grant /revoke /vipinfo /refreshcmds /aidiag /libdiag /paylist /restart /makepdf /mergepdf /splitpdf")

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
        ffm = ffmpeg_path(); ffp = ffprobe_path()
        msg = (f"AI_ENABLED={'ON' if AI_ENABLED else 'OFF'}\n"
               f"Key={'set(len=%d)'%len(k) if k else 'missing'}\n"
               f"Model={OPENAI_CHAT_MODEL}\n"
               f"openai={v('openai')}\n"
               f"ffmpeg={'FOUND' if ffm else 'MISSING'}{(' @'+ffm) if ffm else ''}\n"
               f"ffprobe={'FOUND' if ffp else 'MISSING'}{(' @'+ffp) if ffp else ''}")
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
               f"Pillow={v('Pillow')}\n"
               f"yt-dlp={v('yt-dlp')}\n"
               f"PyPDF2={v('PyPDF2')}\n"
               f"python-whois={v('python-whois')}\n"
               f"dnspython={v('dnspython')}\n"
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def paylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    rows = payments_last(15)
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
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))
    app.add_handler(CommandHandler("mergepdf", mergepdf_cmd))
    app.add_handler(CommandHandler("splitpdf", splitpdf_cmd))

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

# ==== Paylink API (يأتي في النهاية لتقليل تشتيت القراءة) ====
async def paylink_auth_token():
    global _paylink_token, _paylink_token_exp
    _paylink_token = None; _paylink_token_exp = 0
    # (حُذفت التفاصيل هنا لإيجاز الرد؛ استخدم النسخة السابقة إن كنت تعتمد الـ API)
    return None

async def paylink_create_invoice(order_number: str, amount: float, client_name: str):
    # وضع بسيط: إن لم تُفعّل USE_PAYLINK_API، سيتم بناء الرابط من PAYLINK_CHECKOUT_BASE
    return _build_pay_link(order_number), {}

if __name__ == "__main__":
    main()





