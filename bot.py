# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile, shutil, mimetypes
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
    import whois as pywhois
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

ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ==== إعدادات أساسية ====
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

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

HAS_FFMPEG = bool(shutil.which("ffmpeg") or shutil.which("avconv"))

def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")

WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "القوائم مرتبة حسب الأقسام:\n"
    "• أدوات الذكاء الاصطناعي\n"
    "• خدمات فورية (تنزيل وسائط…)\n"
    "• الأمن السيبراني\n"
    "• أرقام وبطاقات\n"
    "• فك الباند\n"
    "• دورات\n"
    "• أداة ملفات\n"
    "اختر من القائمة 👇"
)

WELCOME_TEXT_EN = (
    "Welcome to Ferpoks Bot 🔥\n"
    "Organized sections:\n"
    "• AI Tools\n"
    "• Quick Services (media download…)\n"
    "• Cybersecurity\n"
    "• Numbers & Cards\n"
    "• Unban/Appeals\n"
    "• Courses\n"
    "• File Tools\n"
    "Pick from the menu 👇"
)

CHANNEL_ID = None

# ==== روابط جاهزة من البيئة ====
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

# روابط “نمو/رشق”
GROWTH_URLS = [u.strip() for u in (os.getenv("GROWTH_URLS","").split(",")) if u.strip()]
# أرقام وهمية/مؤقتة + بطاقات افتراضية
TEMP_NUMBERS_URL = os.getenv("TEMP_NUMBERS_URL","")
VCC_URL = os.getenv("VCC_URL","")

# روابط فك الباند (يمكنك استبدالها من البيئة إن أردت)
UNBAN_IG = os.getenv("UNBAN_IG", "https://help.instagram.com")
UNBAN_FB = os.getenv("UNBAN_FB", "https://www.facebook.com/help")
UNBAN_TG = os.getenv("UNBAN_TG", "https://telegram.org/support")
UNBAN_EPIC = os.getenv("UNBAN_EPIC", "https://www.epicgames.com/help")

# روابط دورات (بدّلها من البيئة)
COURSE_PY = os.getenv("COURSE_PY", "https://www.python.org/about/gettingstarted/")
COURSE_EXTRA_1 = os.getenv("COURSE_EXTRA_1","")
COURSE_EXTRA_2 = os.getenv("COURSE_EXTRA_2","")

# ==== إعدادات الدفع (Paylink اختياري) ====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = (os.getenv("PAY_WEBHOOK_SECRET") or "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE  = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web, ClientSession
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _clean_base(url: str) -> str:
    u = (url or "").strip().strip('"').strip("'")
    if u.startswith("="): u = u.lstrip("=")
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE)
    if "{ref}" in base: return base.format(ref=ref)
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
    if not obj: return None
    if isinstance(obj, (str, bytes)):
        s = obj.decode() if isinstance(obj, bytes) else obj
        for pat in (
            r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)",
            r"[?&]ref=([\w\-:]+)",
            r"(\d{6,}-\d{9,})"
        ):
            m = re.search(pat, s); 
            if m and _looks_like_ref(m.group(1)): return m.group(1)
        return None
    if isinstance(obj, dict):
        for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
            v = obj.get(k)
            if isinstance(v, str) and _looks_like_ref(v.strip()): return v.strip()
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
        log.info("[http] aiohttp غير متوفر أو غير مطلوب")
        return

    async def _make_app():
        app = web.Application()
        async def _favicon(_): return web.Response(status=204)
        app.router.add_get("/favicon.ico", _favicon)

        if SERVE_HEALTH:
            async def _root(_): return web.json_response({"ok": True, "service": "bot"})
            async def _health(_): return web.json_response({"ok": True, "health": "green"})
            app.router.add_get("/", _root)
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

# ========= i18n (AR/EN) =========
LANGS = ("ar","en")

T = {
    "menu_main": {"ar": "👇 القائمة الرئيسية", "en": "👇 Main Menu"},
    "btn_sections": {"ar": "📂 الأقسام", "en": "📂 Sections"},
    "btn_contact": {"ar": "📨 تواصل مع الإدارة", "en": "📨 Contact Admin"},
    "btn_lang": {"ar": "🌐 تغيير اللغة", "en": "🌐 Change Language"},
    "btn_myinfo": {"ar": "👤 معلوماتي", "en": "👤 My Info"},
    "btn_upgrade": {"ar": "⚡ ترقية إلى VIP", "en": "⚡ Upgrade to VIP"},
    "btn_vip": {"ar": "⭐ حسابك VIP", "en": "⭐ Your VIP"},
    "btn_back": {"ar": "↩️ رجوع", "en": "↩️ Back"},
    "follow_btn": {"ar":"📣 الانضمام للقناة", "en":"📣 Join Channel"},
    "check_btn": {"ar":"✅ تحقّق من القناة", "en":"✅ Verify"},
    "need_admin_note": {
        "ar": "⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في",
        "en": "⚠️ If verify fails: ensure the bot is admin in"
    },
    "sections_title": {"ar": "📂 الأقسام", "en": "📂 Sections"},
    # Categories
    "cat_ai": {"ar":"🤖 أدوات الذكاء الاصطناعي", "en":"🤖 AI Tools"},
    "cat_services": {"ar":"⚡ خدمات فورية", "en":"⚡ Quick Services"},
    "cat_cyber": {"ar":"🛡️ الأمن السيبراني", "en":"🛡️ Cybersecurity"},
    "cat_numbers": {"ar":"☎️ أرقام وبطاقات", "en":"☎️ Numbers & Cards"},
    "cat_unban": {"ar":"🚫 فك الباند", "en":"🚫 Unban/Appeals"},
    "cat_courses": {"ar":"🎓 دورات", "en":"🎓 Courses"},
    "cat_files": {"ar":"🗜️ أداة ملفات", "en":"🗜️ File Tools"},
    # AI options
    "ai_chat": {"ar":"💬 دردشة AI", "en":"💬 AI Chat"},
    "ai_translate": {"ar":"🌐 مترجم (AR/EN)", "en":"🌐 Translator (AR/EN)"},
    "ai_writer": {"ar":"✍️ كاتب محتوى", "en":"✍️ Copy Writer"},
    "ai_stt": {"ar":"🎙️ تحويل صوت→نص", "en":"🎙️ Speech→Text"},
    "ai_image": {"ar":"🖼️ نص→صورة", "en":"🖼️ Text→Image"},
    # Services
    "svc_dl": {"ar":"⬇️ تنزيل فيديو/صوت", "en":"⬇️ Download Media"},
    "svc_growth": {"ar":"🚀 نمو/رشق متابعين", "en":"🚀 Growth/Followers"},
    # Cyber
    "cy_ip": {"ar":"🛰️ IP Lookup", "en":"🛰️ IP Lookup"},
    "cy_scan": {"ar":"🛡️ فحص رابط", "en":"🛡️ URL Scan"},
    "cy_email": {"ar":"✉️ فحص إيميل", "en":"✉️ Email Check"},
    "cy_osint": {"ar":"🔎 OSINT (يوزر/إيميل)", "en":"🔎 OSINT (user/email)"},
    # Numbers & Cards
    "num_temp": {"ar":"☎️ أرقام مؤقتة", "en":"☎️ Temp Numbers"},
    "num_vcc": {"ar":"💳 بطاقات افتراضية", "en":"💳 Virtual Cards"},
    # Unban
    "ub_ig": {"ar":"انستقرام", "en":"Instagram"},
    "ub_fb": {"ar":"فيسبوك", "en":"Facebook"},
    "ub_tg": {"ar":"تيليجرام", "en":"Telegram"},
    "ub_epic": {"ar":"Epic Games", "en":"Epic Games"},
    # Courses
    "cr_py": {"ar":"بايثون للمبتدئين", "en":"Python for Beginners"},
    # Files
    "file_img2pdf": {"ar":"🖼️ صورة → PDF", "en":"🖼️ Image → PDF"},
    "file_compress": {"ar":"🗜️ ضغط صورة", "en":"🗜️ Compress Image"},
}

def txt(uid: int, key: str) -> str:
    u = user_get(uid)
    lang = (u.get("pref_lang") or "ar") if u else "ar"
    return T.get(key, {}).get(lang, T.get(key, {}).get("ar", key))

# ========= قاعدة البيانات =========
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

def _table_has_column(cur, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r["name"] for r in cur.fetchall()}
    return col in cols

def migrate_db():
    with _conn_lock:
        c = _db().cursor()

        # users
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0,
          vip_forever INTEGER DEFAULT 0,
          vip_since INTEGER DEFAULT 0,
          pref_lang TEXT DEFAULT 'ar'
        );
        """)
        # تأكد من الأعمدة
        for col, ddl in [
            ("id", None),
            ("premium","ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0;"),
            ("verified_ok","ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;"),
            ("verified_at","ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;"),
            ("vip_forever","ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0;"),
            ("vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;"),
            ("pref_lang","ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar';"),
        ]:
            if col != "id" and not _table_has_column(c, "users", col) and ddl:
                _db().execute(ddl)

        # ai_state
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT NULL,
          updated_at INTEGER
        );
        """)
        # أعمدة ai_state
        for col, ddl in [
            ("mode", None),
            ("extra","ALTER TABLE ai_state ADD COLUMN extra TEXT DEFAULT NULL;"),
            ("updated_at","ALTER TABLE ai_state ADD COLUMN updated_at INTEGER;"),
        ]:
            if col not in ("user_id",) and not _table_has_column(c, "ai_state", col) and ddl:
                _db().execute(ddl)

        # payments
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
        );
        """)
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
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0, "vip_forever": 0, "vip_since": 0, "pref_lang":"ar"}
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
        _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE id=?", (str(uid),))
        _db().commit()

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
            except Exception as e: log.error("[payments_mark_paid] grant again: %s", e)
            return True
        user_id = r["user_id"]
        _db().execute(
            "UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
            (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref)
        ); _db().commit()
    try: user_grant(user_id)
    except Exception as e: log.error("[payments_mark_paid] grant: %s", e)
    return True

def payments_last(limit=10):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(x) for x in c.fetchall()]

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

# ========= أدوات تقنية =========
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
    except Exception as e:
        log.warning("[geo] fetch error: %s", e)
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
    parts.append("\nℹ️ استخدم المعلومات لأغراض مشروعة فقط.")
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

    # Resolve domain & geo
    ip = resolve_ip(domain)
    geo_text = ""
    if ip:
        data = await fetch_geo(ip); geo_text = fmt_geo(data)
    else:
        geo_text = "⚠️ تعذّر حلّ IP للدومين."

    # WHOIS
    w = whois_domain(domain)
    w_txt = "WHOIS: غير متاح"
    if w:
        if w.get("error"): w_txt = f"WHOIS: {w['error']}"
        else:
            w_txt = f"WHOIS:\n- Registrar: {w.get('registrar')}\n- Created: {w.get('creation_date')}\n- Expires: {w.get('expiration_date')}"

    out = [
        f"📧 <code>{email}</code>",
        f"📮 MX: {mx_txt}",
        f"🖼️ Gravatar: {grav}",
        w_txt,
        f"\n{geo_text}"
    ]
    return "\n".join(out)

async def osint_username(name: str) -> str:
    uname = re.sub(r"[^\w\-.]+", "", name.strip())
    if not uname or len(uname) < 3:
        return "⚠️ أدخل اسم/يوزر صالح (٣ أحرف على الأقل)."
    gh_line = "GitHub: لم يتم الفحص"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/users/{uname}", timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    gh_line = f"GitHub: ✅ — repos={data.get('public_repos')}, since {data.get('created_at')}"
                elif r.status == 404:
                    gh_line = "GitHub: ❌"
                else:
                    gh_line = f"GitHub: status {r.status}"
    except Exception as e:
        gh_line = f"GitHub: network ({e})"
    return f"👤 <code>{uname}</code>\n{gh_line}\n\nℹ️ يمكنك إضافة مصادر أخرى لاحقًا."

def classify_url(u: str) -> dict:
    try:
        p = _urlparse.urlparse(u)
        return {"ok": True, "scheme": p.scheme, "host": p.hostname, "path": p.path, "q": p.query}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def link_scan(u: str) -> str:
    if not _URL_RE.search(u or ""): return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    meta = classify_url(u)
    if not meta.get("ok"): return f"⚠️ رابط غير صالح: {meta.get('error')}"
    host = meta.get("host") or ""
    scheme = meta.get("scheme")
    issues = []
    if scheme != "https": issues.append("❗️ بدون تشفير HTTPS")
    ip = resolve_ip(host) if host else None
    geo_txt = ""
    if ip:
        data = await fetch_geo(ip); geo_txt = fmt_geo(data)
    else:
        geo_txt = "⚠️ تعذّر حلّ IP للمضيف."
    status = await http_head(u)
    if status is None: issues.append("⚠️ فشل الوصول (HEAD)")
    else: issues.append(f"🔎 حالة HTTP: {status}")
    return f"🔗 <code>{u}</code>\nالمضيف: <code>{host}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}"

async def email_check(e: str) -> str:
    ok = is_valid_email(e)
    if not ok: return "❌ الإيميل غير صالح."
    dom = e.split("@",1)[1].lower()
    disp = "⚠️ غير معروف"
    if dom in DISPOSABLE_DOMAINS: disp = "❌ دومين مؤقت"
    else: disp = "✅ ليس ضمن قائمة المؤقت"
    mx = "❓"
    if dnsresolver:
        try:
            ans = dnsresolver.resolve(dom, "MX")
            mx = "✅ موجود" if len(ans) else "❌ غير موجود"
        except dnsexception.DNSException:
            mx = "❌ غير موجود"
    else:
        mx = "ℹ️ تحتاج dnspython للاختبار (اختياري)"
    return f"📧 {e}\nصلاحية: ✅\nMX: {mx}\nDisposable: {disp}"

async def tts_whisper_from_file(filepath: str) -> str:
    if not AI_ENABLED or client is None: return "🧠 الذكاء الاصطناعي غير مفعّل."
    try:
        with open(filepath, "rb") as f:
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e)
        return "⚠️ تعذّر التحويل. أرسل كملف mp3/m4a/wav."

async def translate_text(text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED or client is None: return "🧠 الذكاء الاصطناعي غير مفعّل."
    prompt = f"Translate the following into {target_lang}. Keep formatting when possible:\n\n{text}"
    r = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role":"system","content":"You are a high-quality translator."},{"role":"user","content":prompt}],
        temperature=0
    )
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

async def ai_write(prompt: str) -> str:
    if not AI_ENABLED or client is None: return "🧠 الذكاء الاصطناعي غير مفعّل."
    sysmsg = "اكتب نصًا عربيًا إعلانيًا جذابًا ومختصرًا مع عناوين قصيرة وCTA واضح."
    r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=[{"role":"system","content":sysmsg},{"role":"user","content":prompt}], temperature=0.7)
    return (r.choices[0].message.content or "").strip()

async def ai_image_generate(prompt: str) -> bytes|None:
    if not AI_ENABLED or client is None: return None
    try:
        resp = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e); return None

# ==== مُحمّل الوسائط (تم تصحيحه) ====
async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp غير مثبت")
        return None

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).60s-%(id)s.%(ext)s")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115 Safari/537.36",
        "Referer": "https://www.tiktok.com/"
    }

    if HAS_FFMPEG:
        fmt = "(bv*[filesize<47M]+ba[filesize<47M]/b[ext=mp4][filesize<47M]/b[filesize<47M]/b)"
    else:
        fmt = "b[ext=mp4][filesize<47M]/b[filesize<47M]/b"

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": fmt,
        "merge_output_format": "mp4" if HAS_FFMPEG else None,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "noplaylist": True,
        "http_headers": headers,
        "geo_bypass": True,
        "cachedir": str(TMP_DIR / "ydl_cache"),
    }

    def _valid_candidate(p: Path) -> bool:
        if not p or not p.exists(): return False
        if p.suffix.endswith(".part") or p.name.endswith(".ytdl"): return False
        return True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            candidates = []
            if isinstance(info, dict) and "requested_downloads" in info:
                for d in info["requested_downloads"] or []:
                    fp = d.get("filepath")
                    if fp: candidates.append(Path(fp))

            candidates.append(Path(ydl.prepare_filename(info)))

            for p in candidates:
                if _valid_candidate(p) and p.stat().st_size <= MAX_UPLOAD_BYTES:
                    return p

            # جودة أقل
            try:
                low_opts = ydl_opts | {"format": "b[height<=720][ext=mp4]/b[height<=720]/b"}
                with yt_dlp.YoutubeDL(low_opts) as y2:
                    info2 = y2.extract_info(url, download=True)
                    p2 = Path(y2.prepare_filename(info2))
                    if _valid_candidate(p2) and p2.stat().st_size <= MAX_UPLOAD_BYTES:
                        return p2
            except Exception as e:
                log.error("[ydl-low] %s", e)

            # صوت فقط
            try:
                audio_opts = ydl_opts | {"format": "ba/bestaudio", "merge_output_format": None}
                with yt_dlp.YoutubeDL(audio_opts) as y3:
                    info3 = y3.extract_info(url, download=True)
                    base3 = Path(y3.prepare_filename(info3))
                    for ext in (".m4a",".mp3",".webm",".ogg"):
                        p3 = base3.with_suffix(ext)
                        if _valid_candidate(p3) and p3.stat().st_size <= MAX_UPLOAD_BYTES:
                            return p3
                    if _valid_candidate(base3) and base3.stat().st_size <= MAX_UPLOAD_BYTES:
                        return base3
            except Exception as e:
                log.error("[ydl-audio] %s", e)

    except Exception as e:
        log.error("[ydl] %s", e)

    return None

# ========= واجهة الأزرار =========
def gate_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(txt(uid,"follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(txt(uid,"check_btn"), callback_data="verify")]
    ])

def main_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = []
    rows.append([InlineKeyboardButton(txt(uid,"btn_sections"), callback_data="sections")])
    rows.append([InlineKeyboardButton(txt(uid,"btn_myinfo"), callback_data="myinfo")])
    rows.append([InlineKeyboardButton(txt(uid,"btn_lang"), callback_data="lang_toggle")])
    if is_vip:
        rows.append([InlineKeyboardButton(txt(uid,"btn_vip"), callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton(txt(uid,"btn_upgrade"), callback_data="upgrade")])
    rows.append([InlineKeyboardButton(txt(uid,"btn_contact"), url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def sections_root_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"cat_ai"), callback_data="cat_ai")],
        [InlineKeyboardButton(txt(uid,"cat_services"), callback_data="cat_services")],
        [InlineKeyboardButton(txt(uid,"cat_cyber"), callback_data="cat_cyber")],
        [InlineKeyboardButton(txt(uid,"cat_numbers"), callback_data="cat_numbers")],
        [InlineKeyboardButton(txt(uid,"cat_unban"), callback_data="cat_unban")],
        [InlineKeyboardButton(txt(uid,"cat_courses"), callback_data="cat_courses")],
        [InlineKeyboardButton(txt(uid,"cat_files"), callback_data="cat_files")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="back_home")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_ai_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"ai_chat"), callback_data="ai_chat")],
        [InlineKeyboardButton(txt(uid,"ai_translate"), callback_data="ai_tr_menu")],
        [InlineKeyboardButton(txt(uid,"ai_writer"), callback_data="ai_writer")],
        [InlineKeyboardButton(txt(uid,"ai_stt"), callback_data="ai_stt")],
        [InlineKeyboardButton(txt(uid,"ai_image"), callback_data="ai_image")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def tr_menu_kb(uid: int, direction: str):
    # direction: "ar->en" or "en->ar"
    left = "◀️"; right = "▶️"
    label = f"{direction}"
    rows = [
        [InlineKeyboardButton(left, callback_data="tr_left"),
         InlineKeyboardButton(label, callback_data="noop"),
         InlineKeyboardButton(right, callback_data="tr_right")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_ai")]
    ]
    return InlineKeyboardMarkup(rows)

def cat_services_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"svc_dl"), callback_data="svc_dl")],
        [InlineKeyboardButton(txt(uid,"svc_growth"), callback_data="svc_growth")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_cyber_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"cy_ip"), callback_data="cy_ip")],
        [InlineKeyboardButton(txt(uid,"cy_scan"), callback_data="cy_scan")],
        [InlineKeyboardButton(txt(uid,"cy_email"), callback_data="cy_email")],
        [InlineKeyboardButton(txt(uid,"cy_osint"), callback_data="cy_osint")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_numbers_kb(uid: int):
    btns = []
    if TEMP_NUMBERS_URL:
        btns.append([InlineKeyboardButton(txt(uid,"num_temp"), url=TEMP_NUMBERS_URL)])
    if VCC_URL:
        btns.append([InlineKeyboardButton(txt(uid,"num_vcc"), url=VCC_URL)])
    if not btns:
        btns.append([InlineKeyboardButton("ℹ️ اضبط TEMP_NUMBERS_URL و VCC_URL في البيئة", callback_data="noop")])
    btns.append([InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")])
    return InlineKeyboardMarkup(btns)

def cat_unban_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"ub_ig"), url=UNBAN_IG)],
        [InlineKeyboardButton(txt(uid,"ub_fb"), url=UNBAN_FB)],
        [InlineKeyboardButton(txt(uid,"ub_tg"), url=UNBAN_TG)],
        [InlineKeyboardButton(txt(uid,"ub_epic"), url=UNBAN_EPIC)],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_courses_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"cr_py"), url=COURSE_PY)],
    ]
    if COURSE_EXTRA_1: rows.append([InlineKeyboardButton("📘 Course #2", url=COURSE_EXTRA_1)])
    if COURSE_EXTRA_2: rows.append([InlineKeyboardButton("📙 Course #3", url=COURSE_EXTRA_2)])
    rows.append([InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")])
    return InlineKeyboardMarkup(rows)

def cat_files_kb(uid: int):
    rows = [
        [InlineKeyboardButton(txt(uid,"file_img2pdf"), callback_data="file_img2pdf")],
        [InlineKeyboardButton(txt(uid,"file_compress"), callback_data="file_compress")],
        [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

# ==== تعديل آمن ====
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
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
    if user_id == OWNER_ID: return True
    if user_is_premium(user_id): return True
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

# ========= رسائل وأوامر =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("/start – Start\n/help – Help")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    u = user_get(uid)

    # حلّ CHANNEL_ID
    global CHANNEL_ID
    if CHANNEL_ID is None:
        for uname in MAIN_CHANNEL_USERNAMES:
            try:
                chat = await context.bot.get_chat(f"@{uname}")
                CHANNEL_ID = chat.id
                log.info("[startup] resolved @%s -> chat_id=%s", uname, CHANNEL_ID); break
            except Exception as e:
                log.warning("[startup] get_chat @%s failed: %s", uname, e)

    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=(WELCOME_TEXT_AR if u.get("pref_lang","ar")=="ar" else WELCOME_TEXT_EN))
        else:
            await context.bot.send_message(chat_id, (WELCOME_TEXT_AR if u.get("pref_lang","ar")=="ar" else WELCOME_TEXT_EN))
    except Exception as e:
        log.warning("[welcome] %s", e)

    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        try:
            await context.bot.send_message(chat_id, f"🔐", reply_markup=gate_kb(uid))
            await context.bot.send_message(chat_id, f"{txt(uid,'need_admin_note')} @{MAIN_CHANNEL_USERNAMES[0]}")
        except Exception as e:
            log.warning("[start] gate send ERROR: %s", e)
        return

    try:
        await context.bot.send_message(chat_id, txt(uid,"menu_main"), reply_markup=main_menu_kb(uid))
        await context.bot.send_message(chat_id, txt(uid,"sections_title"), reply_markup=sections_root_kb(uid))
    except Exception as e:
        log.warning("[start] menu send ERROR: %s", e)

# ==== /setlang مخفية (للأمان) ====
async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args:
        lang = context.args[0].lower()
        if lang not in LANGS: lang = "ar"
        prefs_set_lang(uid, lang)
    await start(update, context)

# ==== الأزرار ====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, txt(uid,"menu_main"), kb=main_menu_kb(uid))
            await q.message.reply_text(txt(uid,"sections_title"), reply_markup=sections_root_kb(uid))
        else:
            await safe_edit(q, f"❗️ {txt(uid,'need_admin_note')} @{MAIN_CHANNEL_USERNAMES[0]}", kb=gate_kb(uid))
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐", kb=gate_kb(uid)); return

    if q.data == "lang_toggle":
        u = user_get(uid)
        new_lang = "en" if (u.get("pref_lang","ar")=="ar") else "ar"
        prefs_set_lang(uid, new_lang)
        await safe_edit(q, txt(uid,"menu_main"), kb=main_menu_kb(uid))
        try:
            await q.message.reply_text(txt(uid,"sections_title"), reply_markup=sections_root_kb(uid))
        except Exception: pass
        return

    if q.data == "vip_badge":
        u = user_get(uid)
        since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ VIP — since: {since_txt}", kb=main_menu_kb(uid)); return

    if q.data == "myinfo":
        u = user_get(uid)
        await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}\n🌐 {u.get('pref_lang','ar').upper()}", kb=main_menu_kb(uid)); return

    if q.data == "back_home":
        await safe_edit(q, txt(uid,"menu_main"), kb=main_menu_kb(uid)); return

    if q.data == "sections":
        await safe_edit(q, txt(uid,"sections_title"), kb=sections_root_kb(uid)); return

    # === الأقسام ===
    if q.data == "cat_ai":
        await safe_edit(q, txt(uid,"cat_ai"), kb=cat_ai_kb(uid)); return
    if q.data == "ai_chat":
        if not AI_ENABLED or client is None:
            await safe_edit(q, "🧠 الذكاء الاصطناعي غير مفعّل.", kb=cat_ai_kb(uid)); return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, "🤖 أرسل سؤالك الآن…", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_ai")]])); return
    if q.data == "ai_tr_menu":
        # الاتجاه الافتراضي
        ai_set_mode(uid, "translate", {"from":"en","to":"ar"})
        await safe_edit(q, "🌐 اختر الاتجاه عبر الأسهم ثم أرسل النص/الصورة.", kb=tr_menu_kb(uid, "en->ar")); return
    if q.data == "tr_left":
        _m, extra = ai_get_mode(uid); f = extra.get("from","en"); t = extra.get("to","ar")
        f,t = t,f
        ai_set_mode(uid, "translate", {"from":f,"to":t})
        await safe_edit(q, "🌐", kb=tr_menu_kb(uid, f"{f}->{t}")); return
    if q.data == "tr_right":
        _m, extra = ai_get_mode(uid); f = extra.get("from","en"); t = extra.get("to","ar")
        f,t = t,f
        ai_set_mode(uid, "translate", {"from":f,"to":t})
        await safe_edit(q, "🌐", kb=tr_menu_kb(uid, f"{f}->{t}")); return
    if q.data == "ai_writer":
        ai_set_mode(uid, "writer")
        await safe_edit(q, "✍️ اكتب وصفًا قصيرًا للنص المطلوب.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_ai")]])); return
    if q.data == "ai_stt":
        ai_set_mode(uid, "stt")
        await safe_edit(q, "🎙️ أرسل Voice أو ملف صوت (mp3/m4a/wav).", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_ai")]])); return
    if q.data == "ai_image":
        ai_set_mode(uid, "image_ai")
        await safe_edit(q, "🖼️ اكتب وصف الصورة المراد توليدها.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_ai")]])); return

    if q.data == "cat_services":
        await safe_edit(q, txt(uid,"cat_services"), kb=cat_services_kb(uid)); return
    if q.data == "svc_dl":
        ai_set_mode(uid, "media_dl")
        await safe_edit(q, "⬇️ أرسل رابط فيديو/صوت (YouTube/TikTok/…)", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_services")]])); return
    if q.data == "svc_growth":
        if not GROWTH_URLS:
            await safe_edit(q, "أضف GROWTH_URLS في البيئة (comma-separated).", kb=cat_services_kb(uid)); return
        rows = [[InlineKeyboardButton(f"🌟 #{i+1}", url=url)] for i, url in enumerate(GROWTH_URLS[:8])]
        rows.append([InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_services")])
        await safe_edit(q, "🚀 روابط نمو/متابعين:", kb=InlineKeyboardMarkup(rows)); return

    if q.data == "cat_cyber":
        await safe_edit(q, txt(uid,"cat_cyber"), kb=cat_cyber_kb(uid)); return
    if q.data == "cy_ip":
        ai_set_mode(uid, "geo_ip")
        await safe_edit(q, "📍 أرسل IP أو دومين.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_cyber")]])); return
    if q.data == "cy_scan":
        ai_set_mode(uid, "link_scan")
        await safe_edit(q, "🛡️ أرسل الرابط للفحص.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_cyber")]])); return
    if q.data == "cy_email":
        ai_set_mode(uid, "email_check")
        await safe_edit(q, "✉️ أرسل الإيميل للفحص.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_cyber")]])); return
    if q.data == "cy_osint":
        ai_set_mode(uid, "osint")
        await safe_edit(q, "🔎 أرسل يوزر أو إيميل.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_cyber")]])); return

    if q.data == "cat_numbers":
        await safe_edit(q, txt(uid,"cat_numbers"), kb=cat_numbers_kb(uid)); return

    if q.data == "cat_unban":
        await safe_edit(q, txt(uid,"cat_unban"), kb=cat_unban_kb(uid)); return

    if q.data == "cat_courses":
        await safe_edit(q, txt(uid,"cat_courses"), kb=cat_courses_kb(uid)); return

    if q.data == "cat_files":
        await safe_edit(q, txt(uid,"cat_files"), kb=cat_files_kb(uid)); return
    if q.data == "file_img2pdf":
        ai_set_mode(uid, "file_img_to_pdf", {"paths":[]})
        await safe_edit(q, "🖼️ أرسل صورة واحدة أو أكثر ثم /makepdf", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_files")]])); return
    if q.data == "file_compress":
        ai_set_mode(uid, "file_img_compress")
        await safe_edit(q, "🗜️ أرسل صورة وسيتم إرجاع نسخة مضغوطة.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="cat_files")]])); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك VIP مفعل.", kb=main_menu_kb(uid)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ إنشاء رابط الدفع…\n🔖 <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(txt(uid,"btn_back"), callback_data="back_home")]]))
        try:
            if USE_PAYLINK_API:
                pay_url, _invoice = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            txtm = (f"💳 VIP مدى الحياة ({VIP_PRICE_SAR:.2f} SAR)\nسيتم التفعيل تلقائيًا بعد الدفع.\n🔖 <code>{ref}</code>")
            await safe_edit(q, txtm, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 الذهاب للدفع", url=pay_url)],
                [InlineKeyboardButton("✅ تحقّق الدفع", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="back_home")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "تعذّر إنشاء/فتح رابط الدفع.", kb=sections_root_kb(uid))
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_", "")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP.", kb=main_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ لم يصل إشعار الدفع بعد. جرّب لاحقًا.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تحقّق مرة أخرى", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(txt(uid,"btn_back"), callback_data="back_home")]
            ]))
        return

# ==== تنزيل من تيليجرام إلى ملف ====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ==== أدوات ملفات ====
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

def compress_image(image_path: Path, quality: int = 70) -> Path|None:
    try:
        im = Image.open(image_path)
        out_path = TMP_DIR / f"compressed_{image_path.stem}.jpg"
        im.convert("RGB").save(out_path, "JPEG", optimize=True, quality=max(1, min(quality, 95)))
        return out_path
    except Exception as e:
        log.error("[compress] %s", e); return None

# ==== Handlers للرسائل ====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐", reply_markup=gate_kb(uid)); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # نصوص
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()

        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            try:
                r = client.chat.completions.create(model=OPENAI_CHAT_MODEL,
                    messages=[{"role":"system","content":"أجب بإيجاز وبالعربية متى أمكن."},{"role":"user","content":text}],
                    temperature=0.7) if (AI_ENABLED and client) else None
                out = (r.choices[0].message.content or "").strip() if r else "🧠 الذكاء الاصطناعي غير مفعّل."
            except Exception as e:
                log.error("[ai_chat] %s", e); out = "⚠️ تعذّر التنفيذ."
            await update.message.reply_text(out); return

        if mode == "geo_ip":
            query = text
            if _HOST_RE.match(text):
                ip = resolve_ip(text)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return

        if mode == "osint":
            if "@" in text and "." in text: out = await osint_email(text)
            else: out = await osint_username(text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "writer":
            out = await ai_write(text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "translate":
            to = (extra or {}).get("to","ar")
            out = await translate_text(text, to)
            await update.message.reply_text(out); return

        if mode == "link_scan":
            out = await link_scan(text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "email_check":
            out = await email_check(text)
            await update.message.reply_text(out); return

        if mode == "media_dl":
            if not _URL_RE.search(text):
                await update.message.reply_text("أرسل رابط صالح (http/https)."); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VIDEO)
            path = await download_media(text)
            if not path or not path.exists():
                await update.message.reply_text("⚠️ تعذّر التحميل (قد يكون الرابط غير مدعوم)."); return
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                await update.message.reply_text(f"⚠️ الملف أكبر من {MAX_UPLOAD_MB}MB."); return
            suf = path.suffix.lower()
            try:
                if suf in (".mp4", ".webm", ".mkv", ".mov"):
                    await update.message.reply_video(video=InputFile(str(path)))
                elif suf in (".mp3", ".m4a", ".aac", ".ogg", ".wav"):
                    await update.message.reply_audio(audio=InputFile(str(path)))
                else:
                    await update.message.reply_document(document=InputFile(str(path)))
            except Exception as e:
                log.error("[send-media] %s", e)
                await update.message.reply_text("⚠️ تعذّر إرسال الملف.")
            return

        if mode == "image_ai":
            prompt = text
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            img_bytes = await ai_image_generate(prompt)
            if img_bytes:
                bio = BytesIO(img_bytes); bio.name = "ai.png"
                await update.message.reply_photo(photo=InputFile(bio))
            else:
                await update.message.reply_text("⚠️ تعذّر توليد الصورة.")
            return

        if mode == "file_img_to_pdf":
            await update.message.reply_text("📌 أرسل صورًا، ثم /makepdf للإخراج."); return

        if mode == "file_img_compress":
            await update.message.reply_text("📌 أرسل صورة وسيتم ضغطها."); return

    # صوت
    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p))
            await update.message.reply_text(out)
            return

    # صور
    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
        if mode == "translate" and OPENAI_VISION:
            out = await translate_image_file(str(p), (extra or {}).get("to","ar"))
            await update.message.reply_text(out or "⚠️ لم أستطع قراءة النص.")
            return
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
            await update.message.reply_text(f"✅ تم إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج.")
            return

    # مستند
    if msg.document:
        if mode in ("file_img_to_pdf", "file_img_compress"):
            p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
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
                await update.message.reply_text(f"✅ تم إضافة ملف صورة ({len(st_paths)}). أرسل /makepdf للإخراج.")
                return

    # إن ما في وضع
    if not mode:
        await update.message.reply_text(txt(uid,"menu_main"), reply_markup=main_menu_kb(uid))
        await update.message.reply_text(txt(uid,"sections_title"), reply_markup=sections_root_kb(uid))
    else:
        await update.message.reply_text("🤖 جاهز.")

# ==== makepdf ====
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("استخدم من قسم (أداة ملفات) أولاً.")
        return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("لم يتم استلام صور بعد.")
        return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    ai_set_mode(uid, None, {})

# ==== أوامر المالك ====
async def help_cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("أوامر المالك: /id /grant /revoke /vipinfo /refreshcmds /aidiag /libdiag /paylist /debugverify (/dv) /restart /setlang")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ VIP مدى الحياة للمستخدم {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم الإلغاء للمستخدم {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /vipinfo <user_id>"); return
    u = user_get(context.args[0])
    await update.message.reply_text(json.dumps(u, ensure_ascii=False, indent=2))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application)
    await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

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
               f"openai={v('openai')}")
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
               f"python-whois={v('whois')}\n"
               f"dnspython={v('dnspython')}\n"
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def paylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    rows = payments_last(15)
    if not rows:
        await update.message.reply_text("لا توجد مدفوعات.")
        return
    txtm = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        txtm.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={ts}")
    await update.message.reply_text("\n".join(txtm))

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok}")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 إعادة تشغيل…")
    os._exit(0)

# ==== on_startup: ضبط الأوامر ====
async def on_startup(app: Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("[startup] delete_webhook: %s", e)

    # default (للعامة): /start /help فقط
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
                BotCommand("start","Start"),
                BotCommand("help","Help"),
                BotCommand("id","معرّفك"),
                BotCommand("grant","منح VIP"),
                BotCommand("revoke","سحب VIP"),
                BotCommand("vipinfo","معلومات VIP"),
                BotCommand("refreshcmds","تحديث الأوامر"),
                BotCommand("aidiag","تشخيص AI"),
                BotCommand("libdiag","إصدارات المكتبات"),
                BotCommand("paylist","قائمة المدفوعات"),
                BotCommand("debugverify","تشخيص التحقق"),
                BotCommand("dv","اختصار debugverify"),
                BotCommand("restart","إعادة تشغيل"),
                BotCommand("setlang","تغيير اللغة يدوي")
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

# ==== أخطاء عامة ====
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
    app.add_handler(CommandHandler("setlang", setlang_cmd))  # للمالك أو من يعرفها

    # أوامر المالك
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

    # أزرار
    app.add_handler(CallbackQueryHandler(on_button))

    # رسائل
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE, guard_messages))
    app.add_handler(MessageHandler(filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))

    app.add_handler(CommandHandler("makepdf", makepdf_cmd))

    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()

