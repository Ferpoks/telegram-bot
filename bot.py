# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging, base64, hashlib, socket, tempfile
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv

# ==== LOGGING ====
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

# ==== تحميل البيئة ====
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ==== إعدادات أساسية ====
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")  # تأكد أنه على قرص دائم
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

MAX_UPLOAD_MB = 47  # حد الحجم لإرسال الملفات عبر البوت
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

def need_admin_text() -> str:
    return f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت **مشرف** في @{MAIN_CHANNEL_USERNAMES[0]}."

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "كل الأدوات هنا تتم داخل تيليجرام: ذكاء اصطناعي، فحص روابط، تحميل وسائط، تحويل صوت لنص، توليد صور، وأكثر.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

CHANNEL_ID = None  # سيُحل عند الإقلاع

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

# ==== أرقام مؤقتة (VIP/اختياري) ====
FIVESIM_API_KEY = os.getenv("FIVESIM_API_KEY", "").strip()  # لو تركته فاضي: الميزة تظهر رسالة إعداد

# ==== خادِم ويب (Webhook + Health) ====
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "0") == "1" or PAY_WEBHOOK_ENABLE
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
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))  # userId-timestamp

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
        if SERVE_HEALTH:
            async def _health(_): return web.json_response({"ok": True})
            app.router.add_get("/", _health)
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
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF")

        loop.run_until_complete(_start())
        try:
            loop.run_forever()
        finally:
            loop.stop(); loop.close()

    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ==== عند الإقلاع ====
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

    # أوامر عامة للمستخدمين
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"),
                BotCommand("help","مساعدة"),
                BotCommand("geo","تحديد موقع IP"),
                BotCommand("osint","بحث ذكي (اسم/إيميل)"),
                BotCommand("write","كتابة محتوى"),
                BotCommand("stt","تحويل صوت لنص"),
                BotCommand("tr","ترجمة فورية"),
                BotCommand("scan","فحص رابط"),
                BotCommand("email","Email Checker"),
                BotCommand("dl","تحميل وسائط"),
                BotCommand("img","توليد صورة AI"),
                BotCommand("file","أداة ملفات")
            ],
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
                BotCommand("debugverify","تشخيص التحقق"),
                BotCommand("dv","تشخيص سريع"),
                BotCommand("aidiag","تشخيص AI"),
                BotCommand("libdiag","إصدارات المكتبات"),
                BotCommand("paylist","آخر المدفوعات"),
                BotCommand("restart","إعادة تشغيل")
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

# ==== قاعدة البيانات ====
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

def migrate_db():
    with _conn_lock:
        c = _db().cursor()
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
        c.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in c.fetchall()}
        if "verified_ok" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;")
        if "verified_at" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;")
        if "vip_forever" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0;")
        if "vip_since" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;")
        if "pref_lang" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar';")

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
        )
        _db().commit()

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
    with __conn_lock:
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

# ==== نصوص سريعة ====
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق من القناة",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
    }
    return M.get(k, k)

# ==== الأقسام ====
SECTIONS = {
    # تقنية ذكية
    "osint": {"title": "🔎 البحث الذكي (OSINT)", "desc": "أرسل اسم/يوزر أو إيميل ونرجّع لك معلومات متاحة.", "is_free": False},
    "writer": {"title": "✍️ مولّد نصوص إعلانية", "desc": "اكتب وصفًا قصيرًا لمنتجك.", "is_free": True},
    "stt": {"title": "🎙️ تحويل الصوت إلى نص", "desc": "أرسل مذكرة صوتية (Voice) أو ملف صوت.", "is_free": True},
    "translate": {"title": "🌐 مترجم فوري", "desc": "أرسل نصًا (وصورة إذا فعّلت رؤية OpenAI).", "is_free": True},

    # أمن وحماية (VIP)
    "link_scan": {"title": "🛡️ فحص الروابط", "desc": "أرسل رابط وسنحلله + الدولة/المستضيف.", "is_free": False},
    "geolocation": {"title": "🛰️ IP Lookup", "desc": "أرسل IP أو دومين ونرجّع البلد/المدينة/ASN.", "is_free": True},
    "email_checker": {"title": "✉️ Email Checker", "desc": "تحقق تنسيق، MX، دومينات مؤقتة.", "is_free": False},

    # تحميل وسائط
    "media_dl": {"title": "⬇️ تحميل وسائط", "desc": "أرسل رابط يوتيوب/تويتر/انستغرام.", "is_free": True},

    # خدمية
    "numbers": {"title": "☎️ أرقام مؤقتة (VIP)", "desc": "اطلب رقم لخدمة محددة (يتطلب API).", "is_free": False},
    "file_tools": {"title": "🗜️ أداة ملفات", "desc": "حوّل صورة إلى PDF أو صغّرها.", "is_free": True},

    # صور AI
    "image_ai": {"title": "🖼️ صور بالذكاء الاصطناعي", "desc": "اكتب وصف الصورة المطلوبة.", "is_free": True},

    # محتويات VIP قديمة
    "cyber_sec": {"title": "🛡️ الأمن السيبراني (VIP)", "desc": "دروس ومواد مختارة.", "is_free": False},
    "canva_500": {"title": "🖼️ 500 دعوة Canva Pro (VIP)", "desc": "دعوات كانفا برو.", "is_free": False},
}

# ==== لوحات الأزرار ====
def bottom_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = []
    rows.append([InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")])
    if is_vip:
        rows.append([InlineKeyboardButton("⭐ حسابك VIP", callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")])
    rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())])
    rows.append([InlineKeyboardButton("📂 الأقسام", callback_data="back_sections")])
    return InlineKeyboardMarkup(rows)

def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def sections_list_kb():
    rows = []
    for k, sec in SECTIONS.items():
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{k}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def section_back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

def ai_hub_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])

def ai_stop_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 إنهاء وضع الذكاء الاصطناعي", callback_data="ai_stop")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]
    ])

def file_tools_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ صورة → PDF", callback_data="file_pdf")],
        [InlineKeyboardButton("🗜️ تصغير صورة", callback_data="file_compress")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

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

# ==== AI عام ====
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

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":"أجب بالعربية بإيجاز ووضوح. إن احتجت خطوات، اذكرها بنقاط."},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return tr("ai_disabled")
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
    parts.append(f"🔎 الاستعلام: <code>{data.get('query','')}</code>")
    parts.append(f"🌍 الدولة/المنطقة: {data.get('country','?')} — {data.get('regionName','?')}")
    parts.append(f"🏙️ المدينة/الرمز: {data.get('city','?')} — {data.get('zip','-')}")
    parts.append(f"⏰ التوقيت: {data.get('timezone','-')}")
    parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    parts.append(f"🛰️ AS: {data.get('as','-')}")
    parts.append(f"📍 الإحداثيات: {data.get('lat','?')}, {data.get('lon','?')}")
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
        data = await fetch_geo(ip)
        geo_text = fmt_geo(data)
    else:
        geo_text = "⚠️ تعذّر حلّ IP للدومين."

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
        w_txt,
        f"\n{geo_text}"
    ]
    return "\n".join(out)

async def osint_username(name: str) -> str:
    uname = re.sub(r"[^\w\-.]+", "", name.strip())
    if not uname or len(uname) < 3:
        return "⚠️ أدخل اسم/يوزر صالح (٣ أحرف على الأقل)."
    # GitHub probe (بلا مفتاح)
    gh_line = "GitHub: لم يتم الفحص"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/users/{uname}", timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    gh_line = f"GitHub: ✅ موجود — public_repos={data.get('public_repos')}, منذ {data.get('created_at')}"
                elif r.status == 404:
                    gh_line = "GitHub: ❌ غير موجود"
                else:
                    gh_line = f"GitHub: حالة غير متوقعة {r.status}"
    except Exception as e:
        gh_line = f"GitHub: خطأ شبكة ({e})"
    # يمكنك إضافة فحوص إضافية حسب الحاجة
    return f"👤 البحث عن: <code>{uname}</code>\n{gh_line}\n\nℹ️ فحوص أخرى ممكن إضافتها لاحقًا (تويتر/انستغرام عبر APIs)."

def classify_url(u: str) -> dict:
    try:
        p = _urlparse.urlparse(u)
        return {"ok": True, "scheme": p.scheme, "host": p.hostname, "path": p.path, "q": p.query}
    except Exception as e:
        return {"ok": False, "error": str(e)}

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
        data = await fetch_geo(ip)
        geo_txt = fmt_geo(data)
    else:
        geo_txt = "⚠️ تعذّر حلّ IP للمضيف."
    # HEAD
    status = await http_head(u)
    if status is None:
        issues.append("⚠️ فشل الوصول (HEAD)")
    else:
        issues.append(f"🔎 حالة HTTP: {status}")
    return f"🔗 الرابط: <code>{u}</code>\nالمضيف: <code>{host}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}"

async def email_check(e: str) -> str:
    ok = is_valid_email(e)
    if not ok: return "❌ الإيميل غير صالح."
    dom = e.split("@",1)[1].lower()
    disp = "⚠️ غير معروف"
    if dom in DISPOSABLE_DOMAINS: disp = "❌ دومين مؤقت معروف"
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
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        with open(filepath, "rb") as f:
            # whisper-1 يقبل ogg غالبًا. لو فشل، اطلب من المستخدم يرسل ملف audio بدل voice.
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e)
        return "⚠️ تعذّر التحويل. أعد الإرسال كـ (ملف صوت) بدل Voice، أو جرّب صيغة mp3/m4a/wav."

async def translate_text(text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
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

async def ai_write(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    sysmsg = "اكتب نصًا عربيًا إعلانيًا جذابًا ومختصرًا، مع عناوين قصيرة وCTA واضح."
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err: return "⚠️ تعذّر التوليد حالياً."
    return (r.choices[0].message.content or "").strip()

async def ai_image_generate(prompt: str) -> bytes|None:
    if not AI_ENABLED or client is None:
        return None
    try:
        resp = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e)
        return None

async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp غير مثبت")
        return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).50s.%(ext)s")
    # نحاول جودة متوازنة تبقي الحجم أقل من ~48MB قدر الإمكان
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best",
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
            # بعد الدمج قد تكون صارت m4a/mp4
            base, _ = os.path.splitext(fname)
            # ابحث عن ملف الناتج
            for ext in (".mp4",".m4a",".webm",".mp3",".mkv",".mp4.part",".m4a.part"):
                p = Path(base + ext)
                if p.exists() and p.is_file():
                    if p.stat().st_size > MAX_UPLOAD_BYTES:
                        # محاولة تنزيل صوت فقط لتقليل الحجم
                        ydl_opts_audio = ydl_opts | {"format": "bestaudio[filesize<45M]/bestaudio", "merge_output_format": "m4a"}
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

# ==== رسائل أساسية ====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة\n/geo – IP Lookup\n/osint – بحث ذكي\n/write – كتابة محتوى\n/stt – تحويل صوت لنص\n/tr – ترجمة\n/scan – فحص رابط\n/email – Email Checker\n/dl – تحميل وسائط\n/img – صورة AI\n/file – أداة ملفات")

async def geo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid, "geo_ip")
    await update.message.reply_text("📍 أرسل الآن **IP** أو **دومين** (مثال: 8.8.8.8 أو example.com).", parse_mode="HTML")

async def osint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_is_premium(uid) and uid != OWNER_ID:
        await update.message.reply_text("🔒 هذه الميزة VIP. فعّلها من زر الترقية.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid, "osint")
    await update.message.reply_text("🔎 أرسل **اسم/يوزر** أو **إيميل** للفحص (OSINT).", parse_mode="HTML")

async def write_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai_set_mode(uid, "writer"); await update.message.reply_text("✍️ اكتب وصفًا قصيرًا لما تريد كتابته (مثال: إعلان لعطور).")

async def stt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai_set_mode(uid, "stt"); await update.message.reply_text("🎙️ أرسل مذكرة **Voice** أو **ملف صوت** (mp3/m4a/wav...).", parse_mode="HTML")

async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = user_get(uid)
    ai_set_mode(uid, "translate", {"to": u.get("pref_lang","ar")})
    await update.message.reply_text(f"🌐 أرسل نصًّا{' أو صورة' if OPENAI_VISION else ''} للترجمة → {u.get('pref_lang','ar').upper()}.\nلتغيير اللغة: /setlang <رمز> (مثال: ar, en, fr)")

async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("استخدم: /setlang <رمز لغة> (مثال: ar, en)")
        return
    lang = context.args[0].lower()[:8]
    prefs_set_lang(uid, lang)
    await update.message.reply_text(f"✅ تم ضبط لغة الترجمة الافتراضية إلى: {lang.upper()}")

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_is_premium(uid) and uid != OWNER_ID:
        await update.message.reply_text("🔒 هذه الميزة VIP. فعّلها من زر الترقية.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid, "link_scan"); await update.message.reply_text("🛡️ أرسل الرابط المطلوب فحصه.")

async def email_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_is_premium(uid) and uid != OWNER_ID:
        await update.message.reply_text("🔒 هذه الميزة VIP. فعّلها من زر الترقية.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid, "email_check"); await update.message.reply_text("✉️ أرسل الإيميل لفحصه.")

async def dl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai_set_mode(uid, "media_dl"); await update.message.reply_text("⬇️ أرسل رابط فيديو/صوت (يوتيوب/تويتر/انستغرام).")

async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai_set_mode(uid, "image_ai"); await update.message.reply_text("🖼️ اكتب وصف الصورة المراد توليدها.")

async def file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai_set_mode(uid, "file_tools_menu"); await update.message.reply_text("🗜️ اختر الأداة:", reply_markup=file_tools_kb())

# ==== /start ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    user_get(uid)

    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e:
        log.warning("[welcome] ERROR: %s", e)

    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        try:
            await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
            await context.bot.send_message(chat_id, need_admin_text())
        except Exception as e:
            log.warning("[start] gate send ERROR: %s", e)
        return

    try:
        await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
        await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())
    except Exception as e:
        log.warning("[start] menu send ERROR: %s", e)

# ==== الأزرار ====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك بالقناة.\nانضم ثم اضغط تحقّق.\n\n" + need_admin_text(), kb=gate_kb())
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "vip_badge":
        u = user_get(uid)
        since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ حسابك VIP (مدى الحياة)\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data == "myinfo":
        u = user_get(uid)
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n🌐 لغة الترجمة: {u.get('pref_lang','ar').upper()}", kb=bottom_menu_kb(uid)); return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك مفعل VIP بالفعل (مدى الحياة).", kb=bottom_menu_kb(uid))
            return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ جاري إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(tr("back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API:
                pay_url, _invoice = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            txt = (f"💳 ترقية إلى VIP مدى الحياة ({VIP_PRICE_SAR:.2f} SAR)\n"
                   f"سيتم التفعيل تلقائيًا بعد الدفع.\n"
                   f"🔖 مرجعك: <code>{ref}</code>")
            await safe_edit(q, txt, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 الذهاب للدفع", url=pay_url)],
                [InlineKeyboardButton("✅ تحقّق الدفع", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        except Exception as e:
            log.error("[upgrade] create invoice ERROR: %s", e)
            await safe_edit(q, "تعذّر إنشاء/فتح رابط الدفع حالياً.", kb=sections_list_kb())
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_", "")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP (مدى الحياة) على حسابك. استمتع!", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ لم يصل إشعار الدفع بعد.\nإذا دفعت للتو فانتظر قليلاً ثم اضغط تحقّق مرة أخرى.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تحقّق مرة أخرى", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        return

    # أقسام بالنقر
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", kb=sections_list_kb()); return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')} — فعّل VIP من زر الترقية.", kb=sections_list_kb()); return

        if key == "geolocation":
            ai_set_mode(uid, "geo_ip")
            await safe_edit(q, "📍 أرسل IP أو دومين الآن…", kb=section_back_kb()); return
        if key == "osint":
            ai_set_mode(uid, "osint")
            await safe_edit(q, "🔎 أرسل **اسم/يوزر** أو **إيميل** للفحص.", kb=section_back_kb()); return
        if key == "writer":
            ai_set_mode(uid, "writer")
            await safe_edit(q, "✍️ اكتب وصفًا قصيرًا لما تريد كتابته.", kb=section_back_kb()); return
        if key == "stt":
            ai_set_mode(uid, "stt")
            await safe_edit(q, "🎙️ أرسل Voice أو ملف صوت.", kb=section_back_kb()); return
        if key == "translate":
            u = user_get(uid)
            ai_set_mode(uid, "translate", {"to": u.get("pref_lang","ar")})
            await safe_edit(q, f"🌐 أرسل نصًّا{' أو صورة' if OPENAI_VISION else ''} للترجمة → {u.get('pref_lang','ar').upper()}.", kb=section_back_kb()); return
        if key == "link_scan":
            ai_set_mode(uid, "link_scan")
            await safe_edit(q, "🛡️ أرسل الرابط للفحص.", kb=section_back_kb()); return
        if key == "email_checker":
            ai_set_mode(uid, "email_check")
            await safe_edit(q, "✉️ أرسل الإيميل للفحص.", kb=section_back_kb()); return
           if key == "media_dl":
        ai_set_mode(uid, "media_dl")
        await safe_edit(q, "🎬 أرسل رابط الفيديو أو الصوت للتحميل.", kb=section_back_kb()); return

    if key == "numbers":
        ai_set_mode(uid, "numbers")
        await safe_edit(q, "☎️ خدمة الأرقام المؤقتة تتطلب ربط API.\nأرسل اسم الخدمة (مثال: Telegram / WhatsApp) وسأحاول تجهيز رقم.\n(لو ما ربطت API راح يوصلك تنبيه بالإعداد)", kb=section_back_kb()); return

        if key == "file_tools":
            ai_set_mode(uid, "file_tools_menu")
            await safe_edit(q, "🗜️ اختر أداة الملفات:", kb=file_tools_kb()); return
        if key == "image_ai":
            ai_set_mode(uid, "image_ai")
            await safe_edit(q, "🖼️ اكتب وصف الصورة المراد توليدها.", kb=section_back_kb()); return

        # الافتراضي
        await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}", kb=section_back_kb()); return

    # أزرار أدوات الملفات
    if q.data == "file_pdf":
        ai_set_mode(uid, "file_img_to_pdf")
        await safe_edit(q, "🖼️ أرسل صورة واحدة أو أكثر وسأحوّلها إلى PDF.", kb=section_back_kb()); return
    if q.data == "file_compress":
        ai_set_mode(uid, "file_img_compress")
        await safe_edit(q, "🗜️ أرسل صورة وسأرجّع لك نسخة مضغوطة (جودة متوازنة).", kb=section_back_kb()); return

    # AI Chat Toggle
    if q.data == "ai_chat":
        if not AI_ENABLED or client is None:
            await safe_edit(q, tr("ai_disabled"), kb=sections_list_kb())
            await q.message.reply_text(tr("ai_disabled"), reply_markup=sections_list_kb())
            return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, "🤖 وضع الدردشة مفعّل. أرسل سؤالك الآن.", kb=ai_stop_kb()); return
    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb())
        try:
            await q.message.reply_text("تم إيقاف وضع الذكاء الاصطناعي.", reply_markup=sections_list_kb())
        except Exception:
            pass
        return


# ==== تنزيل ملف من تيليجرام إلى مسار محلي ====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ==== أدوات ملفات: تحويل صور إلى PDF / ضغط صورة ====
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

# ==== VIP: أرقام مؤقتة (Placeholder بسيط إن ما وُجد API) ====
async def get_temp_number(service: str) -> str:
    if not FIVESIM_API_KEY:
        return "ℹ️ لم يتم ضبط مفتاح API لخدمة الأرقام المؤقتة.\nأضف FIVESIM_API_KEY في البيئة لتفعيل الميزة."
    # مبدئيًا Placeholder حتى تربط فعليًا API
    # يمكنك لاحقًا استخدام aiohttp للاتصال بـ 5sim/getsmscode وغيرها وإرجاع رقم حقيقي
    return f"✅ (تجريبي) رقم جاهز لخدمة: {service}\n(اربط API الحقيقي لاستلام الرسائل وإدارة الجلسة)"

# ==== Handlers عامة للرسائل ====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    # تحقّق الانضمام (VIP/مالك bypass)
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode, extra = ai_get_mode(uid)

    # إذا ما في وضع خاص: أعرض القائمة
    if not mode:
        await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
        await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        return

    msg = update.message

    # أوضاع نصية
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()

        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text), reply_markup=ai_stop_kb()); return

        if mode == "geo_ip":
            target = text
            # هل هو IP أم دومين؟
            query = target
            if _HOST_RE.match(target):
                ip = resolve_ip(target)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return

        if mode == "osint":
            if "@" in text and "." in text:
                out = await osint_email(text)
            else:
                out = await osint_username(text)
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
                await update.message.reply_text("أرسل رابط صالح للتحميل (يبدأ بـ http أو https)."); return
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

        if mode == "numbers":
            service = text[:50]
            out = await get_temp_number(service)
            await update.message.reply_text(out); return

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

        if mode == "file_tools_menu":
            await update.message.reply_text("اختر من الأزرار:", reply_markup=file_tools_kb()); return

        if mode in ("file_img_to_pdf", "file_img_compress"):
            await update.message.reply_text("📌 أرسل صورة (أو أكثر لـ PDF)."); return

    # أوضاع استقبال ملفات/صور/صوت
    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p))
            await update.message.reply_text(out)
            return

    if msg.photo:
        # أفضل دقة
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")

        if mode == "translate" and OPENAI_VISION:
            out = await translate_image_file(str(p), (extra or {}).get("to","ar"))
            await update.message.reply_text(out or "⚠️ لم أستطع قراءة النص من الصورة.")
            return
        if mode == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists():
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text("⚠️ فشل الضغط.")
            return
        if mode == "file_img_to_pdf":
            # دعم إرسال عدة صور: نخزن مؤقتًا لكل مستخدم قائمة مسارات
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ تم إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.")
            return

    if msg.document:
        # ممكن تكون صورة مرسلة كمستند
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
                await update.message.reply_text(f"✅ تم إضافة ملف صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.")
                return

    # لو ما تطابقت أي حالة:
    await update.message.reply_text("🤖 جاهز. اختر ميزة من /help أو من الأزرار.", reply_markup=bottom_menu_kb(uid))


# ==== أوامر إضافية مرتبطة بأوضاع الملفات ====
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("هذه الأداة تعمل بعد اختيار (صورة → PDF). استخدم /file ثم اختر الأداة.")
        return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("لم يتم استلام أي صور بعد. أرسل صورًا ثم /makepdf.")
        return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    # إعادة الضبط
    ai_set_mode(uid, "file_tools_menu", {})


# ==== أوامر المالك والصيانة ====
async def help_cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text("أوامر المالك: /id /grant /revoke /vipinfo /refreshcmds /aidiag /libdiag /paylist /debugverify (/dv) /restart")

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

# إعادة تعريف صحيحة لـ payments_last (تصحيح __conn_lock -> _conn_lock)
def payments_last(limit=10):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(x) for x in c.fetchall()]

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

# ==== أوامر أساسية موجودة مسبقًا: نربطها هنا أيضًا ====
# (help_cmd موجود فوق، لا حاجة لتكراره)

# ==== Message handlers للأوامر المباشرة ====
async def tr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await translate_cmd(update, context)

async def geo_cmd_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await geo_cmd(update, context)

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

    # أوامر عامة
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("geo", geo_cmd))
    app.add_handler(CommandHandler("osint", osint_cmd))
    app.add_handler(CommandHandler("write", write_cmd))
    app.add_handler(CommandHandler("stt", stt_cmd))
    app.add_handler(CommandHandler("tr", translate_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("email", email_cmd))
    app.add_handler(CommandHandler("dl", dl_cmd))
    app.add_handler(CommandHandler("img", img_cmd))
    app.add_handler(CommandHandler("file", file_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))

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
    app.add_handler(CommandHandler("ownerhelp", help_cmd_owner))

    # أزرار
    app.add_handler(CallbackQueryHandler(on_button))

    # رسائل (نص/صوت/صور/مستندات) — بعد الأوامر
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE, guard_messages))
    app.add_handler(MessageHandler(filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))

    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()







