# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging, base64, hashlib, socket, tempfile, textwrap, io
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

# ==== Replicate (للصور والفيديو - اختياري) ====
try:
    import replicate
except Exception:
    replicate = None

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
    import whois as pywhois  # python-whois
except Exception:
    pywhois = None
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None
try:
    import yt_dlp
except Exception:
    yt_dlp = None

# ملفات
try:
    from pdf2docx import parse as pdf2docx_parse
except Exception:
    pdf2docx_parse = None
try:
    from docx import Document as DocxDocument
except Exception:
    DocxDocument = None
try:
    from reportlab.pdfgen import canvas as reportlab_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
except Exception:
    reportlab_canvas = None

# ==== تحميل البيئة ====
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

# Replicate
REPLICATE_API_TOKEN = (os.getenv("REPLICATE_API_TOKEN") or "").strip()
REPLICATE_MODEL = os.getenv("REPLICATE_MODEL", "black-forest-labs/flux-schnell")
REPLICATE_TIMEOUT = int(os.getenv("REPLICATE_TIMEOUT", "120"))
if REPLICATE_API_TOKEN and replicate:
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

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

def need_admin_text() -> str:
    return f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @{MAIN_CHANNEL_USERNAMES[0]}."

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")

CHANNEL_ID = None

# ==== إعدادات الدفع (Paylink) ====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API        = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE  = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# ==== خدمية: روابط خارجية كمتغيرات بيئة ====
# خدمات/رشق/أرقام/بطاقات/دورات/فك الباند:
TEMP_NUMBERS_URL = os.getenv("TEMP_NUMBERS_URL", "")
VCC_URL = os.getenv("VCC_URL", "")
BOOST_SITE1 = os.getenv("BOOST_SITE1", "")
BOOST_SITE2 = os.getenv("BOOST_SITE2", "")
BOOST_SITE3 = os.getenv("BOOST_SITE3", "")
COURSE_PYTHON_URL = os.getenv("COURSE_PYTHON_URL", "")
COURSE_CYBER_URL = os.getenv("COURSE_CYBER_URL", "")
COURSE_ETHICAL_URL = os.getenv("COURSE_ETHICAL_URL", "")

UNBAN_IG_URL = os.getenv("UNBAN_IG_URL", "")
UNBAN_FB_URL = os.getenv("UNBAN_FB_URL", "")
UNBAN_TG_URL = os.getenv("UNBAN_TG_URL", "")
UNBAN_EPIC_URL = os.getenv("UNBAN_EPIC_URL", "")

DARK_GPT_URL = os.getenv("DARK_GPT_URL", "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X")

# أمن: API Keys
URLSCAN_API_KEY = os.getenv("URLSCAN_API_KEY", "").strip()
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "").strip()
KICKBOX_API_KEY = os.getenv("KICKBOX_API_KEY", "").strip()
VT_API_KEY = os.getenv("VT_API_KEY", "").strip()  # VirusTotal اختياري

# ==== خادِم ويب (Webhook + Health) ====
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web, ClientSession
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _clean_base(url: str) -> str:
    u = (url or "").strip().strip('"').strip("'")
    if u.startswith("="):
        u = u.lstrip("=")
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE)
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

# ==== WEBHOOK ====
async def _payhook(request):
    if PAY_WEBHOOK_SECRET:
        if request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
            return web.json_response({"ok": False, "error": "bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}

    ref = _find_ref_in_obj(data)
    if not ref:
        log.warning("[payhook] no-ref; sample keys: %s", list(data.keys())[:8])
        return web.json_response({"ok": False, "error": "no-ref"}, status=200)

    activated = payments_mark_paid_by_ref(ref, raw=data)
    log.info("[payhook] ref=%s -> activated=%s", ref, activated)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)}, status=200)

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)):
        log.info("[http] aiohttp غير متوفر أو الإعدادات لا تتطلب خادم ويب")
        return

    async def _make_app():
        app = web.Application()
        async def _favicon(_): return web.Response(status=204)
        app.router.add_get("/favicon.ico", _favicon)
        async def _health(_): return web.json_response({"ok": True})
        if SERVE_HEALTH:
            app.router.add_get("/", _health)
            app.router.add_get("/health", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            async def _payhook_get(_): return web.json_response({"ok": True})
            app.router.add_get("/payhook", _payhook_get)
        return app

    def _thread_main():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _start():
            app = await _make_app()
            runner = web.AppRunner(app)
            await runner.setup()
            port = int(os.getenv("PORT", "10000"))
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s health=%s)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF", "ON" if SERVE_HEALTH else "OFF")

        loop.run_until_complete(_start())
        try:
            loop.run_forever()
        finally:
            loop.stop(); loop.close()

    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ==== DB ====
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None: return conn
    path = DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
        log.info("[db] using %s", path)
        return conn
    except sqlite3.OperationalError as e:
        alt = "/tmp/bot.db"
        Path(alt).parent.mkdir(parents=True, exist_ok=True)
        log.warning("[db] fallback to %s because: %s", alt, e)
        conn = sqlite3.connect(alt, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
        return conn

def _colset(cursor, table: str):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}

def migrate_db():
    with _conn_lock:
        c = _db().cursor()
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          user_id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0,
          vip_forever INTEGER DEFAULT 0,
          vip_since INTEGER DEFAULT 0,
          pref_lang TEXT DEFAULT 'ar'
        );""")
        cols_u = _colset(c, "users")
        if "user_id" not in cols_u and "id" in cols_u:
            log.warning("[db-migrate] users table missing 'user_id'; rebuilding")
            _db().execute("ALTER TABLE users RENAME TO users_old;")
            _db().execute("""
            CREATE TABLE users (
              user_id TEXT PRIMARY KEY,
              premium INTEGER DEFAULT 0,
              verified_ok INTEGER DEFAULT 0,
              verified_at INTEGER DEFAULT 0,
              vip_forever INTEGER DEFAULT 0,
              vip_since INTEGER DEFAULT 0,
              pref_lang TEXT DEFAULT 'ar'
            );""")
            try:
                _db().execute("INSERT OR IGNORE INTO users (user_id,premium,verified_ok,verified_at,vip_forever,vip_since,pref_lang) SELECT id,premium,verified_ok,verified_at,vip_forever,vip_since,COALESCE(pref_lang,'ar') FROM users_old;")
            except Exception as e:
                log.warning("[db-migrate] copy users_old failed: %s", e)
            _db().execute("DROP TABLE IF EXISTS users_old;")
        else:
            # Ensure columns exist
            need_cols = {"premium","verified_ok","verified_at","vip_forever","vip_since","pref_lang"}
            for cc in need_cols:
                if cc not in cols_u:
                    _db().execute(f"ALTER TABLE users ADD COLUMN {cc} { 'TEXT' if cc=='pref_lang' else 'INTEGER DEFAULT 0' };")

        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT '{}',
          updated_at INTEGER
        );""")
        cols_a = _colset(c, "ai_state")
        if "extra" not in cols_a:
            _db().execute("ALTER TABLE ai_state ADD COLUMN extra TEXT DEFAULT '{}';")

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
        c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (user_id) VALUES (?);", (uid,))
            _db().commit()
            return {"user_id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0, "vip_forever": 0, "vip_since": 0, "pref_lang":"ar"}
        return dict(r)

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE user_id=?",
                      (1 if ok else 0, int(time.time()), str(uid)))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    u = user_get(uid)
    return bool(u.get("premium")) or bool(u.get("vip_forever"))

def user_grant(uid: int|str):
    now = int(time.time())
    with _conn_lock:
        _db().execute(
            "UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0), ?) WHERE user_id=?",
            (now, str(uid))
        )
        _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE user_id=?", (str(uid),))
        _db().commit()

def prefs_set_lang(uid: int|str, lang: str):
    with _conn_lock:
        _db().execute("UPDATE users SET pref_lang=? WHERE user_id=?", (lang, str(uid))); _db().commit()

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

def payments_last(limit=10):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(x) for x in c.fetchall()]

# ==== نصوص ====
I18N = {
    "ar": {
        "welcome_choose_lang": "اختر لغتك:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "greet": "مرحباً بك يا {name} في بوت فيربوكس! هنا ستجد أقسام الذكاء الاصطناعي، الأمن، الخدمات، فك الباند، الدورات، الملفات، تنزيل الفيديو، والرشق.",
        "menu_title": "القائمة الرئيسية:",
        "btn_myinfo": "👤 معلوماتي",
        "btn_lang": "🌐 تغيير اللغة",
        "btn_vip": "⭐ حساب VIP",
        "btn_contact": "📨 تواصل مع الإدارة",
        "btn_sections": "📂 الأقسام",
        "vip_badge": "⭐ حسابك VIP (مدى الحياة)\nمنذ: {since}",
        "need_join": "🔐 انضم للقناة لاستخدام البوت:",
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق من القناة",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "sections_title": "اختر قسماً:",
        "sec_ai": "🤖 الذكاء الاصطناعي",
        "sec_security": "🛡️ الأمن",
        "sec_services": "⚙️ الخدمات",
        "sec_unban": "🚫 فك الباند",
        "sec_courses": "🎓 الدورات",
        "sec_files": "🗂️ الملفات",
        "sec_downloader": "⬇️ تنزيل الفيديو",
        "sec_boost": "⚡ رشق متابعين",
        "sec_darkgpt": "🕶️ Dark GPT",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
        "saved_lang": "✅ تم تغيير اللغة إلى: {lang}",
        "myinfo": "👤 اسمك: {name}\n🆔 معرفك: {uid}\n🌐 اللغة: {lang}\n⭐ VIP: {vip}",
        "upgrade_title": "💳 ترقية إلى VIP مدى الحياة ({price:.2f} SAR)\nسيتم التفعيل تلقائيًا بعد الدفع.\n🔖 مرجعك: {ref}",
        "pay_go": "🚀 الذهاب للدفع",
        "pay_check": "✅ تحقّق الدفع",
        "vip_enabled": "🎉 تم تفعيل VIP (مدى الحياة) على حسابك. استمتع!",
        "vip_wait": "⌛ لم يصل إشعار الدفع بعد. إذا دفعت الآن، اضغط تحقّق بعد لحظات.",
        "ai_chat_on": "🤖 وضع الدردشة مفعّل. أرسل سؤالك الآن.",
        "ai_stop": "🔚 تم إنهاء وضع الذكاء الاصطناعي.",
        "file_tools_title": "🗂️ أدوات الملفات:",
        "ft_img2pdf": "🖼️ صورة → PDF",
        "ft_pdf2docx": "📄 PDF → Word",
        "ft_docx2pdf": "📝 Word → PDF",
        "ft_img_compress": "🗜️ تصغير صورة",
        "send_images_pdf": "🖼️ أرسل صورة واحدة أو أكثر وسأحوّلها إلى PDF. ثم أرسل /makepdf للإخراج.",
        "send_image_compress": "🗜️ أرسل صورة وسأرجّع نسخة مضغوطة.",
        "send_pdf_for_docx": "📄 أرسل ملف PDF وسأحوّله إلى DOCX.",
        "send_docx_for_pdf": "📝 أرسل ملف DOCX وسأحوّله إلى PDF.",
        "unban_title": "🚫 فك الباند: اختر منصة:",
        "unban_ig": "انستقرام",
        "unban_fb": "فيسبوك",
        "unban_tg": "تيليجرام",
        "unban_epic": "Epic Games",
        "unban_text_prefix": "انسخ الرسالة التالية وقدّمها عبر رابط الدعم:",
        "courses_title": "🎓 دورات متاحة:",
        "course_python": "بايثون من الصفر",
        "course_cyber": "الأمن السيبراني من الصفر",
        "course_ethical": "الهكر الأخلاقي",
        "services_title": "⚙️ خدمات:",
        "svc_temp_numbers": "أرقام وهمية",
        "svc_vcc": "بطاقات/فيزا مؤقتة",
        "downloader_title": "⬇️ أرسل رابط الفيديو أو الصوت (يوتيوب/تويتر/انستا..).",
        "boost_title": "⚡ مواقع الرشق:",
        "security_title": "🛡️ أدوات الأمن:",
        "sec_urlscan": "فحص رابط (urlscan)",
        "sec_ipinfo": "IP/دومين Lookup",
        "sec_kickbox": "تحقق Email (Kickbox)",
        "sec_virustotal": "VirusTotal (اختياري)",
        "translate_tip": "🌐 أرسل نصًّا{' أو صورة' if True else ''} للترجمة.",
    },
    "en": {
        "welcome_choose_lang": "Choose your language:",
        "lang_ar": "العربية",
        "lang_en": "English",
        "greet": "Welcome {name} to Ferpoks bot! Browse AI tools, Security, Services, Unban, Courses, Files, Video Downloader, and Boost.",
        "menu_title": "Main Menu:",
        "btn_myinfo": "👤 My Info",
        "btn_lang": "🌐 Change Language",
        "btn_vip": "⭐ VIP Account",
        "btn_contact": "📨 Contact Admin",
        "btn_sections": "📂 Sections",
        "vip_badge": "⭐ Your VIP (lifetime)\nSince: {since}",
        "need_join": "🔐 Join the channel to use the bot:",
        "follow_btn": "📣 Join Channel",
        "check_btn": "✅ Verify Channel",
        "access_denied": "⚠️ VIP only.",
        "back": "↩️ Back",
        "sections_title": "Pick a section:",
        "sec_ai": "🤖 AI Tools",
        "sec_security": "🛡️ Security",
        "sec_services": "⚙️ Services",
        "sec_unban": "🚫 Unban",
        "sec_courses": "🎓 Courses",
        "sec_files": "🗂️ Files",
        "sec_downloader": "⬇️ Video Downloader",
        "sec_boost": "⚡ Followers Boost",
        "sec_darkgpt": "🕶️ Dark GPT",
        "ai_disabled": "🧠 AI is disabled.",
        "saved_lang": "✅ Language changed to: {lang}",
        "myinfo": "👤 Name: {name}\n🆔 ID: {uid}\n🌐 Lang: {lang}\n⭐ VIP: {vip}",
        "upgrade_title": "💳 Upgrade to VIP lifetime ({price:.2f} SAR)\nAuto-activation after payment.\n🔖 Ref: {ref}",
        "pay_go": "🚀 Pay",
        "pay_check": "✅ Check Payment",
        "vip_enabled": "🎉 VIP activated!",
        "vip_wait": "⌛ Payment not received yet.",
        "ai_chat_on": "🤖 Chat mode ON. Send your question.",
        "ai_stop": "🔚 AI mode stopped.",
        "file_tools_title": "🗂️ File Tools:",
        "ft_img2pdf": "🖼️ Image → PDF",
        "ft_pdf2docx": "📄 PDF → Word",
        "ft_docx2pdf": "📝 Word → PDF",
        "ft_img_compress": "🗜️ Compress Image",
        "send_images_pdf": "🖼️ Send one or more images then /makepdf.",
        "send_image_compress": "🗜️ Send an image to compress.",
        "send_pdf_for_docx": "📄 Send a PDF; I’ll convert to DOCX.",
        "send_docx_for_pdf": "📝 Send a DOCX; I’ll convert to PDF.",
        "unban_title": "🚫 Unban: pick platform:",
        "unban_ig": "Instagram",
        "unban_fb": "Facebook",
        "unban_tg": "Telegram",
        "unban_epic": "Epic Games",
        "unban_text_prefix": "Copy the following message and submit via support link:",
        "courses_title": "🎓 Available Courses:",
        "course_python": "Python from Zero",
        "course_cyber": "Cybersecurity from Zero",
        "course_ethical": "Ethical Hacking",
        "services_title": "⚙️ Services:",
        "svc_temp_numbers": "Temporary Numbers",
        "svc_vcc": "Virtual/Temp Cards",
        "downloader_title": "⬇️ Send a video/audio link (YouTube/Twitter/Instagram..).",
        "boost_title": "⚡ Boost sites:",
        "security_title": "🛡️ Security Tools:",
        "sec_urlscan": "Scan URL (urlscan)",
        "sec_ipinfo": "IP/Domain Lookup",
        "sec_kickbox": "Email Verify (Kickbox)",
        "sec_virustotal": "VirusTotal (optional)",
        "translate_tip": "🌐 Send text (or image if enabled) to translate.",
    }
}

def T(uid: int, key: str) -> str:
    lang = user_get(uid).get("pref_lang","ar")
    return I18N.get(lang, I18N["ar"]).get(key, key)

# ==== لوحات ====
def main_menu_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"btn_myinfo"), callback_data="myinfo")],
        [InlineKeyboardButton(T(uid,"btn_lang"), callback_data="lang_menu")],
        [InlineKeyboardButton(T(uid,"btn_vip"), callback_data="vip_menu")],
        [InlineKeyboardButton(T(uid,"btn_contact"), url=admin_button_url())],
        [InlineKeyboardButton(T(uid,"btn_sections"), callback_data="sections")]
    ])

def gate_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(T(uid,"check_btn"), callback_data="verify")]
    ])

def sections_list_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"sec_ai"), callback_data="sec_ai")],
        [InlineKeyboardButton(T(uid,"sec_security"), callback_data="sec_security")],
        [InlineKeyboardButton(T(uid,"sec_services"), callback_data="sec_services")],
        [InlineKeyboardButton(T(uid,"sec_unban"), callback_data="sec_unban")],
        [InlineKeyboardButton(T(uid,"sec_courses"), callback_data="sec_courses")],
        [InlineKeyboardButton(T(uid,"sec_files"), callback_data="sec_files")],
        [InlineKeyboardButton(T(uid,"sec_downloader"), callback_data="sec_downloader")],
        [InlineKeyboardButton(T(uid,"sec_boost"), callback_data="sec_boost")],
        [InlineKeyboardButton(T(uid,"sec_darkgpt"), url=DARK_GPT_URL)],
        [InlineKeyboardButton(T(uid,"back"), callback_data="back_home")]
    ])

def lang_menu_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(I18N["ar"]["lang_ar"], callback_data="set_lang_ar"),
         InlineKeyboardButton(I18N["en"]["lang_en"], callback_data="set_lang_en")],
        [InlineKeyboardButton(T(uid,"back"), callback_data="back_home")]
    ])

def vip_menu_kb(uid: int, ref: str|None=None, pay_url: str|None=None):
    rows = []
    if ref and pay_url:
        rows.append([InlineKeyboardButton(T(uid,"pay_go"), url=pay_url)])
        rows.append([InlineKeyboardButton(T(uid,"pay_check"), callback_data=f"verify_pay_{ref}")])
    rows.append([InlineKeyboardButton(T(uid,"back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def file_tools_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"ft_img2pdf"), callback_data="file_img2pdf")],
        [InlineKeyboardButton(T(uid,"ft_pdf2docx"), callback_data="file_pdf2docx")],
        [InlineKeyboardButton(T(uid,"ft_docx2pdf"), callback_data="file_docx2pdf")],
        [InlineKeyboardButton(T(uid,"ft_img_compress"), callback_data="file_img_compress")],
        [InlineKeyboardButton(T(uid,"back"), callback_data="sections")]
    ])

def security_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"sec_urlscan"), callback_data="sec_urlscan")],
        [InlineKeyboardButton(T(uid,"sec_ipinfo"), callback_data="sec_ipinfo")],
        [InlineKeyboardButton(T(uid,"sec_kickbox"), callback_data="sec_kickbox")],
        [InlineKeyboardButton(T(uid,"sec_virustotal"), callback_data="sec_virustotal")],
        [InlineKeyboardButton(T(uid,"back"), callback_data="sections")]
    ])

def services_kb(uid: int):
    rows = []
    rows.append([InlineKeyboardButton(T(uid,"svc_temp_numbers"), url=TEMP_NUMBERS_URL or "https://example.com")])
    rows.append([InlineKeyboardButton(T(uid,"svc_vcc"), url=VCC_URL or "https://example.com")])
    rows.append([InlineKeyboardButton(T(uid,"back"), callback_data="sections")])
    return InlineKeyboardMarkup(rows)

def unban_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"unban_ig"), callback_data="unban_ig")],
        [InlineKeyboardButton(T(uid,"unban_fb"), callback_data="unban_fb")],
        [InlineKeyboardButton(T(uid,"unban_tg"), callback_data="unban_tg")],
        [InlineKeyboardButton(T(uid,"unban_epic"), callback_data="unban_epic")],
        [InlineKeyboardButton(T(uid,"back"), callback_data="sections")]
    ])

def courses_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"course_python"), url=COURSE_PYTHON_URL or "https://example.com")],
        [InlineKeyboardButton(T(uid,"course_cyber"), url=COURSE_CYBER_URL or "https://example.com")],
        [InlineKeyboardButton(T(uid,"course_ethical"), url=COURSE_ETHICAL_URL or "https://example.com")],
        [InlineKeyboardButton(T(uid,"back"), callback_data="sections")]
    ])

def boost_kb(uid: int):
    rows=[]
    if BOOST_SITE1: rows.append([InlineKeyboardButton("Site 1", url=BOOST_SITE1)])
    if BOOST_SITE2: rows.append([InlineKeyboardButton("Site 2", url=BOOST_SITE2)])
    if BOOST_SITE3: rows.append([InlineKeyboardButton("Site 3", url=BOOST_SITE3)])
    if not rows:
        rows.append([InlineKeyboardButton("Example", url="https://example.com")])
    rows.append([InlineKeyboardButton(T(uid,"back"), callback_data="sections")])
    return InlineKeyboardMarkup(rows)

# ==== تعديل آمن ====
async def safe_edit(q, text=None, kb=None, parse_mode="HTML"):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode=parse_mode)
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

# ==== العضوية ====
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except AttributeError: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except AttributeError: pass

_member_cache = {}
async def must_be_member_or_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID: return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

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

# ==== AI دردشة/ترجمة ====
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = [m for m in [primary, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1", "gpt-3.5-turbo"] if m]
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
            if "insufficient_quota" in msg or "You exceeded your current quota" in msg:
                return None, "quota"
            if "invalid_api_key" in msg or "Incorrect API key" in msg or "No API key provided" in msg:
                return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str, lang="ar") -> str:
    if not AI_ENABLED or client is None:
        return I18N.get(lang, I18N["ar"])["ai_disabled"]
    sys_ar = "أجب بالعربية بإيجاز ووضوح. إن احتجت خطوات، اذكرها بنقاط."
    sys_en = "Answer briefly and clearly in English. Use bullet steps when needed."
    sysmsg = sys_ar if lang=="ar" else sys_en
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":sysmsg},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return I18N.get(lang, I18N["ar"])["ai_disabled"]
        if err == "quota": return "⚠️ نفاد الرصيد في حساب OpenAI."
        if err == "apikey": return "⚠️ مفتاح OpenAI غير صالح أو مفقود."
        if r is None: return "⚠️ تعذّر التنفيذ حالياً."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] unexpected: %s", e)
        return "⚠️ حدث خطأ غير متوقع أثناء الرد من AI."

# ==== أدوات تقنية ====
_IP_RE = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_URL_RE = re.compile(r"https?://[^\s]+")

DISPOSABLE_DOMAINS = {"mailinator.com","tempmail.com","10minutemail.com","yopmail.com","guerrillamail.com","trashmail.com"}

async def fetch_geo_ipapi(query: str) -> dict|None:
    url = f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                data = await r.json(content_type=None)
                if data.get("status") != "success":
                    return {"error": data.get("message","lookup failed")}
                return data
    except Exception as e:
        log.warning("[geo] fetch error: %s", e)
        return {"error": "network error"}

async def fetch_ipinfo(target: str) -> dict:
    url = f"https://ipinfo.io/{target}/json"
    params = {}
    if IPINFO_TOKEN: params["token"] = IPINFO_TOKEN
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=15) as r:
                data = await r.json(content_type=None)
                return data
    except Exception as e:
        return {"error": f"ipinfo error: {e}"}

def fmt_geo_ar(data: dict) -> str:
    if not data: return "⚠️ تعذّر جلب البيانات."
    parts = []
    if "error" in data: parts.append(f"⚠️ {data['error']}")
    if "query" in data: parts.append(f"🔎 الاستعلام: <code>{data.get('query')}</code>")
    if "country" in data or "region" in data or "regionName" in data:
        parts.append(f"🌍 {data.get('country','?')} — {data.get('region','') or data.get('regionName','')}")
    if "city" in data or "postal" in data or "zip" in data:
        parts.append(f"🏙️ {data.get('city','?')} — {data.get('postal') or data.get('zip','-')}")
    if "timezone" in data: parts.append(f"⏰ {data.get('timezone')}")
    if "org" in data or "isp" in data:
        parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    if "asn" in data: parts.append(f"🛰️ AS: {data.get('asn')}")
    if "as" in data: parts.append(f"🛰️ AS: {data.get('as')}")
    if "loc" in data:
        parts.append(f"📍 {data.get('loc')}")
    elif "lat" in data and "lon" in data:
        parts.append(f"📍 {data.get('lat')}, {data.get('lon')}")
    if "reverse" in data:
        parts.append(f"🔁 Reverse: {data.get('reverse')}")
    parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join([p for p in parts if p])

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
        for fam, _, _, _, sockaddr in infos:
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

# OSINT/SECURITY calls
async def urlscan_submit(u: str) -> str:
    headers = {"Content-Type": "application/json"}
    if URLSCAN_API_KEY:
        headers["API-Key"] = URLSCAN_API_KEY
    body = {"url": u, "visibility": "unlisted"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://urlscan.io/api/v1/scan/", headers=headers, json=body, timeout=25) as r:
                data = await r.json(content_type=None)
                # return result link if provided
                rid = data.get("uuid") or data.get("result") or ""
                if isinstance(rid, str) and rid.startswith("http"):
                    return rid
                if "uuid" in data:
                    return f"https://urlscan.io/result/{data['uuid']}/"
                return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return f"⚠️ urlscan error: {e}"

async def kickbox_verify(email: str) -> str:
    if not KICKBOX_API_KEY:
        return "Kickbox: لم يتم ضبط المفتاح."
    url = "https://api.kickbox.com/v2/verify"
    params = {"email": email, "apikey": KICKBOX_API_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=20) as r:
                data = await r.json(content_type=None)
                return f"Kickbox: result={data.get('result')} reason={data.get('reason')}"
    except Exception as e:
        return f"Kickbox error: {e}"

async def virustotal_url(u: str) -> str:
    if not VT_API_KEY:
        return "VirusTotal: لم يتم ضبط المفتاح."
    try:
        async with aiohttp.ClientSession() as s:
            # Submit URL
            headers = {"x-apikey": VT_API_KEY}
            form = aiohttp.FormData()
            form.add_field("url", u)
            async with s.post("https://www.virustotal.com/api/v3/urls", headers=headers, data=form, timeout=20) as r:
                data = await r.json()
                id_ = data.get("data", {}).get("id")
                if not id_:
                    return f"VT submit resp: {json.dumps(data)[:400]}"
            # Get analysis
            async with s.get(f"https://www.virustotal.com/api/v3/analyses/{id_}", headers=headers, timeout=20) as r2:
                res = await r2.json()
                stats = res.get("data", {}).get("attributes", {}).get("stats", {})
                return f"VirusTotal: {stats}"
    except Exception as e:
        return f"VirusTotal error: {e}"

async def osint_email(email: str) -> str:
    if not is_valid_email(email): return "⚠️ صيغة الإيميل غير صحيحة."
    local, domain = email.split("@", 1)
    # MX
    mx_txt = "❓ غير متاح"
    if dnsresolver:
        try:
            answers = dnsresolver.resolve(domain, "MX")
            mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
            mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else "لا يوجد"
        except dnsexception.DNSException:
            mx_txt = "لا يوجد (فشل الاستعلام)"
    else:
        mx_txt = "لم يتم تثبيت dnspython"

    # Gravatar
    g_url = f"https://www.gravatar.com/avatar/{md5_hex(email)}?d=404"
    g_st = await http_head(g_url)
    grav = "✅ موجود" if g_st and 200 <= g_st < 300 else "❌ غير موجود"

    # WHOIS
    w = whois_domain(domain)
    w_txt = "WHOIS: غير متاح"
    if w:
        if w.get("error"):
            w_txt = f"WHOIS: {w['error']}"
        else:
            w_txt = f"WHOIS:\n- Registrar: {w.get('registrar')}\n- Created: {w.get('creation_date')}\n- Expires: {w.get('expiration_date')}"

    out = [
        f"📧 الإيميل: <code>{email}</code>",
        f"📮 MX: {mx_txt}",
        f"🖼️ Gravatar: {grav}",
        w_txt
    ]
    if KICKBOX_API_KEY:
        out.append(await kickbox_verify(email))
    return "\n".join(out)

async def link_scan(u: str) -> str:
    if not _URL_RE.search(u or ""):
        return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    meta = classify_url(u)
    if not meta.get("ok"):
        return f"⚠️ رابط غير صالح: {meta.get('error')}"
    host = meta.get("host") or ""
    scheme = meta.get("scheme")
    issues = []
    if scheme != "https": issues.append("❗️ بدون تشفير HTTPS")
    ip = resolve_ip(host) if host else None
    geo_txt = ""
    if ip:
        data = await fetch_geo_ipapi(ip)
        geo_txt = fmt_geo_ar(data)
    else:
        geo_txt = "⚠️ تعذّر حلّ IP للمضيف."
    status = await http_head(u)
    if status is None: issues.append("⚠️ فشل الوصول (HEAD)")
    else: issues.append(f"🔎 حالة HTTP: {status}")
    vt_line = ""
    if VT_API_KEY:
        vt_line = await virustotal_url(u)
    us_line = await urlscan_submit(u) if URLSCAN_API_KEY else "urlscan: لم يتم ضبط المفتاح."
    return f"🔗 الرابط: <code>{u}</code>\nالمضيف: <code>{host}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}\n\n{us_line}\n{vt_line}"

def classify_url(u: str) -> dict:
    try:
        p = _urlparse.urlparse(u)
        return {"ok": True, "scheme": p.scheme, "host": p.hostname, "path": p.path, "q": p.query}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Whisper
async def tts_whisper_from_file(filepath: str) -> str:
    if not AI_ENABLED or client is None:
        return I18N["ar"]["ai_disabled"]
    try:
        with open(filepath, "rb") as f:
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e)
        return "⚠️ تعذّر التحويل."

# ترجمة
async def translate_text(text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED or client is None:
        return I18N["ar"]["ai_disabled"]
    prompt = f"Translate the following into {target_lang}. Keep formatting when possible:\n\n{text}"
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
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content = [
            {"role":"user","content":[
                {"type":"input_text","text": f"Extract the text from the image and translate it into {target_lang}. Return only the translation."},
                {"type":"input_image","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
            ]}
        ]
        r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=content, temperature=0)
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[vision-translate] %s", e)
        return "⚠️ تعذّر معالجة الصورة."

# توليد صور: Replicate أولاً ثم OpenAI
async def ai_image_generate(prompt: str) -> bytes|None:
    # Replicate
    if REPLICATE_API_TOKEN and replicate is not None:
        try:
            out = await _replicate_image(prompt)
            if out: return out
        except Exception as e:
            log.error("[replicate] %s", e)
    # OpenAI fallback
    if not AI_ENABLED or client is None:
        return None
    try:
        resp = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e)
        return None

async def _replicate_image(prompt: str) -> bytes|None:
    # معظم نماذج الصور على Replicate ترجع URL للصورة
    loop = asyncio.get_event_loop()
    def run_model():
        return replicate.run(REPLICATE_MODEL, input={"prompt": prompt})
    result = await loop.run_in_executor(None, run_model)
    # النتيجة قد تكون str أو list[str]
    url = None
    if isinstance(result, list) and result:
        url = result[0]
    elif isinstance(result, str):
        url = result
    if not url:
        return None
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=REPLICATE_TIMEOUT) as r:
            if r.status == 200:
                return await r.read()
    return None

# تنزيل وسائط
async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp غير مثبت")
        return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).80s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fname = ydl.prepare_filename(info)
            base, _ = os.path.splitext(fname)
            for ext in (".mp4",".m4a",".webm",".mp3",".mkv"):
                p = Path(base + ext)
                if p.exists() and p.is_file():
                    if p.stat().st_size > MAX_UPLOAD_BYTES:
                        # محاولة تنزيل صوت فقط
                        ydl_opts_audio = ydl_opts | {"format": "bestaudio/best", "merge_output_format": "m4a"}
                        with yt_dlp.YoutubeDL(ydl_opts_audio) as y2:
                            info2 = y2.extract_info(url, download=True)
                            fname2 = y2.prepare_filename(info2)
                            for ext2 in (".m4a",".mp3",".webm"):
                                p2 = Path(os.path.splitext(fname2)[0] + ext2)
                                if p2.exists() and p2.is_file() and p2.stat().st_size <= MAX_UPLOAD_BYTES:
                                    return p2
                        return None
                    return p
    except Exception as e:
        log.error("[ydl] %s", e)
        return None
    return None

# ==== تحويلات ملفات ====
def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        imgs = []
        for p in image_paths:
            im = Image.open(p).convert("RGB")
            imgs.append(im)
        if not imgs:
            return None
        out_path = TMP_DIR / f"images_{int(time.time())}.pdf"
        first, rest = imgs[0], imgs[1:]
        first.save(out_path, save_all=True, append_images=rest)
        return out_path
    except Exception as e:
        log.error("[img->pdf] %s", e)
        return None

def compress_image(image_path: Path, quality: int = 70) -> Path|None:
    try:
        im = Image.open(image_path)
        out_path = TMP_DIR / f"compressed_{image_path.stem}.jpg"
        im.convert("RGB").save(out_path, "JPEG", optimize=True, quality=max(1, min(quality, 95)))
        return out_path
    except Exception as e:
        log.error("[compress] %s", e)
        return None

def pdf_to_docx(pdf_path: Path) -> Path|None:
    if pdf2docx_parse is None: return None
    outp = TMP_DIR / (pdf_path.stem + ".docx")
    try:
        pdf2docx_parse(str(pdf_path), str(outp))
        return outp if outp.exists() else None
    except Exception as e:
        log.error("[pdf2docx] %s", e)
        return None

def docx_to_pdf(docx_path: Path) -> Path|None:
    # تحويل نصي مبسّط عبر reportlab (قد يفقد التنسيق المعقد)
    if DocxDocument is None or reportlab_canvas is None:
        return None
    try:
        doc = DocxDocument(str(docx_path))
        pdf_path = TMP_DIR / (docx_path.stem + ".pdf")
        c = reportlab_canvas.Canvas(str(pdf_path), pagesize=A4)
        width, height = A4
        left = 2*cm
        top = height - 2*cm
        y = top
        for para in doc.paragraphs:
            lines = textwrap.wrap(para.text, width=95)
            for line in lines:
                if y < 2*cm:
                    c.showPage()
                    y = top
                c.drawString(left, y, line)
                y -= 14
            y -= 6
        c.save()
        return pdf_path if pdf_path.exists() else None
    except Exception as e:
        log.error("[docx2pdf] %s", e)
        return None

# ==== أوامر / رسائل ====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start – Start\n/help – Help")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    u = user_get(uid)

    # لغات: اعرض اختيار اللغة أولاً (دائمًا عند /start لتأكيد التنظيم)
    try:
        name = update.effective_user.first_name or update.effective_user.username or "صديقي"
        txt = f"{T(uid,'welcome_choose_lang')}\n\n" + (f"{I18N['ar']['greet'].format(name=name)}" if u.get('pref_lang','ar')=='ar' else I18N['en']['greet'].format(name=name))
        await context.bot.send_message(chat_id, txt, reply_markup=lang_menu_kb(uid))
    except Exception as e:
        log.warning("[welcome] %s", e)

    # تحقّق عضوية القناة
    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        await context.bot.send_message(chat_id, T(uid,"need_join"), reply_markup=gate_kb(uid))
        await context.bot.send_message(chat_id, need_admin_text())
        return

    # قائمة واحدة فقط
    await context.bot.send_message(chat_id, T(uid,"menu_title"), reply_markup=main_menu_kb(uid))

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    # عضوية القناة
    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, T(uid,"menu_title"), kb=main_menu_kb(uid))
        else:
            await safe_edit(q, T(uid,"need_join") + "\n\n" + need_admin_text(), kb=gate_kb(uid))
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, T(uid,"need_join"), kb=gate_kb(uid)); return

    # لغة
    if q.data == "lang_menu":
        await safe_edit(q, T(uid,"welcome_choose_lang"), kb=lang_menu_kb(uid)); return
    if q.data == "set_lang_ar":
        prefs_set_lang(uid, "ar")
        await safe_edit(q, T(uid,"saved_lang").format(lang="AR"), kb=main_menu_kb(uid)); return
    if q.data == "set_lang_en":
        prefs_set_lang(uid, "en")
        await safe_edit(q, T(uid,"saved_lang").format(lang="EN"), kb=main_menu_kb(uid)); return

    # القائمة الرئيسية
    if q.data == "back_home":
        await safe_edit(q, T(uid,"menu_title"), kb=main_menu_kb(uid)); return
    if q.data == "sections":
        await safe_edit(q, T(uid,"sections_title"), kb=sections_list_kb(uid)); return

    if q.data == "myinfo":
        u = user_get(uid)
        since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        vip = "YES" if user_is_premium(uid) or uid==OWNER_ID else "NO"
        await safe_edit(q, T(uid,"myinfo").format(name=q.from_user.full_name, uid=uid, lang=u.get('pref_lang','ar').upper(), vip=vip), kb=main_menu_kb(uid)); return

    if q.data == "vip_menu" or q.data == "btn_vip":
        if user_is_premium(uid) or uid == OWNER_ID:
            u = user_get(uid); since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
            await safe_edit(q, T(uid,"vip_badge").format(since=since_txt), kb=main_menu_kb(uid)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        # إنشاء رابط الدفع
        pay_url = None
        try:
            if USE_PAYLINK_API and PAYLINK_API_ID and PAYLINK_API_SECRET:
                token = await paylink_auth_token()
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
        except Exception as e:
            log.error("[upgrade] %s", e)
        await safe_edit(q, T(uid,"upgrade_title").format(price=VIP_PRICE_SAR, ref=ref), kb=vip_menu_kb(uid, ref, pay_url or None)); return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_", "")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, T(uid,"vip_enabled"), kb=main_menu_kb(uid))
        else:
            await safe_edit(q, T(uid,"vip_wait"), kb=vip_menu_kb(uid, ref, None))
        return

    # الأقسام
    if q.data == "sec_ai":
        # نفس أوضاعك القديمة كما هي (دردشة/ترجمة/صورة/صوت.. تُفعَّل من أوامر منفصلة إذا أردت)
        await safe_edit(q, T(uid,"sec_ai"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sections")]])); return

    if q.data == "sec_security":
        await safe_edit(q, T(uid,"security_title"), kb=security_kb(uid)); return
    if q.data == "sec_services":
        await safe_edit(q, T(uid,"services_title"), kb=services_kb(uid)); return
    if q.data == "sec_unban":
        await safe_edit(q, T(uid,"unban_title"), kb=unban_kb(uid)); return
    if q.data == "sec_courses":
        await safe_edit(q, T(uid,"courses_title"), kb=courses_kb(uid)); return
    if q.data == "sec_files":
        await safe_edit(q, T(uid,"file_tools_title"), kb=file_tools_kb(uid)); return
    if q.data == "sec_downloader":
        ai_set_mode(uid, "media_dl")
        await safe_edit(q, T(uid,"downloader_title"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sections")]])); return
    if q.data == "sec_boost":
        await safe_edit(q, T(uid,"boost_title"), kb=boost_kb(uid)); return

    # Security sub
    if q.data == "sec_urlscan":
        ai_set_mode(uid, "urlscan")
        await safe_edit(q, "🛡️ أرسل الرابط لفحصه عبر urlscan.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_security")]])); return
    if q.data == "sec_ipinfo":
        ai_set_mode(uid, "ipinfo")
        await safe_edit(q, "📍 أرسل IP أو Domain وسأعرض معلومات ipinfo + ip-api.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_security")]])); return
    if q.data == "sec_kickbox":
        ai_set_mode(uid, "kickbox")
        await safe_edit(q, "✉️ أرسل الإيميل للتحقق (Kickbox + DNS).", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_security")]])); return
    if q.data == "sec_virustotal":
        ai_set_mode(uid, "virustotal")
        await safe_edit(q, "🧪 أرسل رابط لفحصه على VirusTotal.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_security")]])); return

    # Files sub
    if q.data == "file_img2pdf":
        ai_set_mode(uid, "file_img_to_pdf", {"paths": []})
        await safe_edit(q, T(uid,"send_images_pdf"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_files")]])); return
    if q.data == "file_pdf2docx":
        ai_set_mode(uid, "file_pdf_to_docx")
        await safe_edit(q, T(uid,"send_pdf_for_docx"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_files")]])); return
    if q.data == "file_docx2pdf":
        ai_set_mode(uid, "file_docx_to_pdf")
        await safe_edit(q, T(uid,"send_docx_for_pdf"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_files")]])); return
    if q.data == "file_img_compress":
        ai_set_mode(uid, "file_img_compress")
        await safe_edit(q, T(uid,"send_image_compress"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"back"), callback_data="sec_files")]])); return

    # Unban sub (يرسل نص جاهز + زر رابط دعم)
    async def _send_unban(template_text: str, link: str):
        msg = f"{T(uid,'unban_text_prefix')}\n\n<code>{template_text}</code>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Support Link", url=link or "https://example.com")],[InlineKeyboardButton(T(uid,"back"), callback_data="sec_unban")]])
        await safe_edit(q, msg, kb=kb)

    if q.data == "unban_ig":
        txt = "مرحباً فريق انستقرام، تم تعطيل حسابي بالخطأ. أتعهد بالالتزام بالإرشادات. أرجو مراجعته وإعادته. شكراً."
        await _send_unban(txt, UNBAN_IG_URL); return
    if q.data == "unban_fb":
        txt = "مرحباً فيسبوك، تم تقييد حسابي دون قصد. أرجو التحقق وإلغاء الحظر. سألتزم بالسياسات. شكراً."
        await _send_unban(txt, UNBAN_FB_URL); return
    if q.data == "unban_tg":
        txt = "مرحباً تيليجرام، يبدو أن حسابي تعرض لحظر خاطئ. أطلب مراجعة الحالة وإعادته. مع الشكر."
        await _send_unban(txt, UNBAN_TG_URL); return
    if q.data == "unban_epic":
        txt = "Hi Epic Games Support, my account seems mistakenly banned. Please review and restore access. I will follow the rules. Thanks."
        await _send_unban(txt, UNBAN_EPIC_URL); return

async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ==== Handlers عامة ====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text(T(uid,"need_join"), reply_markup=gate_kb(uid)); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # نصوص
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()
        # Security modes
        if mode == "urlscan":
            if not _URL_RE.search(text):
                await update.message.reply_text("أرسل رابط صالح."); return
            res = await urlscan_submit(text)
            await update.message.reply_text(str(res), disable_web_page_preview=False); return

        if mode == "ipinfo":
            target = text
            if _HOST_RE.match(text):
                ip = resolve_ip(text)
                if ip: target = ip
            data_ipinfo = await fetch_ipinfo(target)
            data_ipapi = await fetch_geo_ipapi(target)
            out = "IPINFO:\n" + json.dumps(data_ipinfo, ensure_ascii=False, indent=2) + "\n\n" + fmt_geo_ar(data_ipapi)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "kickbox":
            if not is_valid_email(text):
                await update.message.reply_text("صيغة الإيميل غير صحيحة."); return
            base = await osint_email(text)
            await update.message.reply_text(base, parse_mode="HTML"); return

        if mode == "virustotal":
            if not _URL_RE.search(text):
                await update.message.reply_text("أرسل رابط صالح."); return
            vt = await virustotal_url(text)
            await update.message.reply_text(vt); return

        # Downloader
        if mode == "media_dl":
            if not _URL_RE.search(text):
                await update.message.reply_text("أرسل رابط صالح للتحميل."); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            path = await download_media(text)
            if path and path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
                try:
                    await update.message.reply_document(document=InputFile(str(path)))
                except Exception:
                    await update.message.reply_text("⚠️ تعذّر إرسال الملف.")
            else:
                await update.message.reply_text("⚠️ تعذّر التحميل أو أن الملف كبير جداً.")
            return

    # صور/ملفات
    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
        if mode == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists():
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text("⚠️ فشل الضغط.")
            return
        if mode == "file_img_to_pdf":
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ تم إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.")
            return

    if msg.document:
        fn = (msg.document.file_name or "").lower()
        p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
        if mode == "file_img_to_pdf":
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ تم إضافة ملف ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل المزيد.")
            return
        if mode == "file_pdf_to_docx":
            if not fn.endswith(".pdf"):
                await update.message.reply_text("أرسل PDF فقط.")
                return
            outp = pdf_to_docx(p)
            if outp and outp.exists() and outp.stat().st_size <= MAX_UPLOAD_BYTES:
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text("⚠️ فشل التحويل PDF→DOCX.")
            return
        if mode == "file_docx_to_pdf":
            if not fn.endswith(".docx"):
                await update.message.reply_text("أرسل DOCX فقط.")
                return
            outp = docx_to_pdf(p)
            if outp and outp.exists() and outp.stat().st_size <= MAX_UPLOAD_BYTES:
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text("⚠️ فشل التحويل DOCX→PDF.")
            return

    # افتراضي: أظهر القائمة
    await update.message.reply_text(T(uid,"menu_title"), reply_markup=main_menu_kb(uid))

# ==== makepdf ====
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("استخدم أداة صورة → PDF أولاً من الأقسام.")
        return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("لم يتم استلام أي صور بعد.")
        return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    ai_set_mode(uid, None, {})

# ==== Paylink API (async) ====
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

# ==== أوامر المالك ====
async def help_cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("أوامر المالك: /id /grant /revoke /vipinfo /refreshcmds /aidiag /libdiag /paylist /debugverify (/dv) /restart /makepdf")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ تم تفعيل VIP مدى الحياة للمستخدم {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم إلغاء VIP للمستخدم {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /vipinfo <user_id>"); return
    u = user_get(context.args[0])
    await update.message.reply_text(json.dumps(u, ensure_ascii=False, indent=2))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

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
               f"replicate={'ON' if (replicate and REPLICATE_API_TOKEN) else 'OFF'}")
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
               f"whois={v('whois')}\n"
               f"dnspython={v('dnspython')}\n"
               f"pdf2docx={v('pdf2docx')}\n"
               f"python-docx={v('python-docx')}\n"
               f"reportlab={v('reportlab')}\n"
               f"replicate={v('replicate')}\n"
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def paylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    rows = payments_last(15)
    if not rows:
        await update.message.reply_text("لا توجد مدفوعات بعد.")
        return
    txt = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={ts}")
    await update.message.reply_text("\n".join(txt))

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok} (check logs for details)")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 جار إعادة تشغيل الخدمة الآن...")
    os._exit(0)

# ==== أخطاء ====
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ==== Startup: أوامر ====
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
        log.error("[startup] could not resolve channel id; fallback to @username checks")

    # أوامر عامة (للمستخدمين العاديين): فقط start/help
    try:
        await app.bot.set_my_commands(
            [BotCommand("start","Start"), BotCommand("help","Help")],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # أوامر المالك
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"),
                BotCommand("help","مساعدة"),
                BotCommand("id","معرّفك"),
                BotCommand("grant","منح VIP"),
                BotCommand("revoke","سحب VIP"),
                BotCommand("vipinfo","معلومات VIP"),
                BotCommand("refreshcmds","تحديث الأوامر"),
                BotCommand("aidiag","تشخيص AI"),
                BotCommand("libdiag","إصدارات المكتبات"),
                BotCommand("paylist","آخر المدفوعات"),
                BotCommand("debugverify","تشخيص التحقق"),
                BotCommand("dv","تشخيص سريع"),
                BotCommand("restart","إعادة تشغيل"),
                BotCommand("makepdf","إخراج PDF للصور")
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

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

    # المالك
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("vipinfo", vipinfo))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
    app.add_handler(CommandHandler("paylist", paylist))
    app.add_handler(CommandHandler("debugverify", debug_verify))
    app.add_handler(CommandHandler("dv", debug_verify))
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



