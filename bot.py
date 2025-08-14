# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging, base64, hashlib, socket, tempfile
from pathlib import Path
from io import BytesIO

# ===== Logging =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ===== .env =====
try:
    from dotenv import load_dotenv
    if Path(".env").exists() and not os.getenv("RENDER"):
        load_dotenv(".env", override=True)
except Exception:
    pass

# ===== Telegram =====
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

# ===== Optional libs =====
import urllib.parse as _urlparse
from PIL import Image
import aiohttp

# OpenAI
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# whois (python-whois)
try:
    import whois as pywhois
except Exception:
    pywhois = None

# DNS
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None

# yt-dlp
try:
    import yt_dlp
except Exception:
    yt_dlp = None

# Replicate
try:
    import replicate as _replicate_sync
except Exception:
    _replicate_sync = None

# ===== Config =====
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS", "").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}" if MAIN_CHANNEL_USERNAMES else "https://t.me/"

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO", "")
WELCOME_TEXT_AR = os.getenv("WELCOME_TEXT",
    "مرحباً بك 👋\n"
    "هذا البوت يوفّر لك أدوات مرتّبة:\n"
    "— ذكاء اصطناعي (دردشة/ترجمة/تحويل صوت/صور AI)\n"
    "— أمن سيبراني (فحص رابط، Geo IP، فحص إيميل)\n"
    "— تحميل وسائط بجودة مناسبة\n"
    "— أدوات ملفات (صورة→PDF والضغط)\n"
    "— روابط سريعة: أرقام مؤقتة، بطاقات افتراضية، نمو متابعين\n"
    "المزايا المتقدمة قد تتطلب اشتراك VIP."
)

CHANNEL_ID = None

MAX_UPLOAD_MB = 47
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

def admin_button_url() -> str:
    if OWNER_USERNAME:
        return f"tg://resolve?domain={OWNER_USERNAME}"
    if OWNER_ID:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me/"

# ===== Payments (Paylink) =====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "0") == "1"
PAY_WEBHOOK_SECRET = (os.getenv("PAY_WEBHOOK_SECRET") or "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# ===== Health server =====
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1"

# ===== Providers / API keys =====
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

IMAGE_PROVIDER = (os.getenv("IMAGE_PROVIDER", "openai") or "openai").lower()
REPLICATE_API_TOKEN = (os.getenv("REPLICATE_API_TOKEN") or "").strip()
REPLICATE_MODEL = (os.getenv("REPLICATE_MODEL") or "black-forest-labs/flux-schnell").strip()

IPINFO_TOKEN = (os.getenv("IPINFO_TOKEN") or "").strip()
KICKBOX_KEY = (os.getenv("KICKBOX_KEY") or "").strip()
URLSCAN_KEY = (os.getenv("URLSCAN_KEY") or "").strip()

# روابط للخدمات (أزرار)
GROWTH_URLS = [u.strip() for u in (os.getenv("GROWTH_URLS", "").split(",")) if u.strip()]
TEMP_NUMBERS_URLS = [u.strip() for u in (os.getenv("TEMP_NUMBERS_URLS", "").split(",")) if u.strip()]
VCC_URLS = [u.strip() for u in (os.getenv("VCC_URLS", "").split(",")) if u.strip()]

# ===== Small i18n =====
LANGS = {"ar":"العربية","en":"English"}
def tr(k: str, lang="ar") -> str:
    M = {
        "follow_btn": {"ar":"📣 الانضمام للقناة","en":"📣 Join the channel"},
        "check_btn": {"ar":"✅ تحقّق من القناة","en":"✅ Verify channel"},
        "access_denied": {"ar":"⚠️ هذا القسم خاص بمشتركي VIP.","en":"⚠️ VIP-only section."},
        "back": {"ar":"↩️ رجوع","en":"↩️ Back"},
        "ai_disabled": {"ar":"🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.","en":"🧠 AI is currently disabled."},
    }
    try:
        return M[k][lang]
    except Exception:
        return k

# ===== HTTP tiny server (health + payhook) =====
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or ""
    if not base:
        base = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()}" if os.getenv("RENDER_EXTERNAL_HOSTNAME") else ""
    return (base or "").rstrip("/") + path

def _clean_base(url: str) -> str:
    u = (url or "").strip().strip('"').strip("'")
    if u.startswith("="): u = u.lstrip("=")
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE)
    if not base:
        return ""
    if "{ref}" in base:
        return base.format(ref=ref)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}ref={ref}"

def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj: return None
    if isinstance(obj, (str, bytes)):
        s = obj.decode() if isinstance(obj, bytes) else obj
        for pat in [
            r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)",
            r"[?&]ref=([\w\-:]+)",
            r"(\d{6,}-\d{9,})",
        ]:
            m = re.search(pat, s)
            if m and _looks_like_ref(m.group(1)): return m.group(1)
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
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok": False, "error": "bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    ref = _find_ref_in_obj(data)
    if not ref:
        log.warning("[payhook] no-ref; keys=%s", list(data.keys())[:6])
        return web.json_response({"ok": False, "error": "no-ref"}, status=200)
    activated = payments_mark_paid_by_ref(ref, raw=data)
    log.info("[payhook] %s -> activated=%s", ref, activated)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)})

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)):
        return
    async def _make_app():
        app = web.Application()
        async def _ok(_): return web.json_response({"ok": True})
        app.router.add_get("/health", _ok)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            app.router.add_get("/payhook", _ok)
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
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s health=ON)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF")
        loop.run_until_complete(_start())
        try:
            loop.run_forever()
        finally:
            loop.stop(); loop.close()

    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ===== DB =====
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

def _have_column(table: str, col: str) -> bool:
    c = _db().cursor(); c.execute(f"PRAGMA table_info({table})")
    cols = {r["name"] for r in c.fetchall()}
    return col in cols

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
          mode TEXT,
          extra TEXT,
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
        # Ensure columns exist
        for t, col, ddl in [
            ("users","verified_ok","ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0"),
            ("users","verified_at","ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0"),
            ("users","vip_forever","ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0"),
            ("users","vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0"),
            ("users","pref_lang","ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar'"),
            ("ai_state","extra","ALTER TABLE ai_state ADD COLUMN extra TEXT"),
            ("ai_state","updated_at","ALTER TABLE ai_state ADD COLUMN updated_at INTEGER"),
        ]:
            try:
                if not _have_column(t, col):
                    log.warning("[db-migrate] %s table missing '%s'; applying...", t, col)
                    _db().execute(ddl)
            except Exception as e:
                log.warning("[db-migrate] %s", e)
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

# ===== Payments helpers =====
def payments_new_ref(uid: int) -> str: return f"{uid}-{int(time.time())}"

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
        _db().execute("UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
                      (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref))
        _db().commit()
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

# ===== Paylink API =====
async def paylink_auth_token():
    url = f"{PAYLINK_API_BASE}/auth"
    payload = {"apiId": PAYLINK_API_ID, "secretKey": PAYLINK_API_SECRET, "persistToken": False}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=20) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(f"auth failed: {data}")
            token = data.get("token") or data.get("access_token") or data.get("jwt")
            if not token: raise RuntimeError(f"auth failed: {data}")
            return token

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

# ===== Helpers =====
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
    if user_is_premium(user_id) or (OWNER_ID and user_id == OWNER_ID): return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

# Regexes
_IP_RE   = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_URL_RE  = re.compile(r"https?://[^\s]+")

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

def md5_hex(s: str) -> str:
    return hashlib.md5(s.strip().lower().encode()).hexdigest()

def resolve_ip(host: str) -> str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for fam, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            if ":" not in ip: return ip
        return infos[0][4][0] if infos else None
    except Exception:
        return None

# ===== Providers: OpenAI Chat =====
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    fallbacks = [m for m in [OPENAI_CHAT_MODEL, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"] if m]
    seen = set(); ordered = [m for m in fallbacks if not (m in seen or seen.add(m))]
    last_err = None
    for model in ordered:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.7, timeout=60)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg: return None, "quota"
            if "invalid_api_key" in msg or "No API key provided" in msg: return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":"أجب بالعربية بإيجاز ووضوح."},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return tr("ai_disabled")
        if err == "quota": return "⚠️ نفاد رصيد OpenAI."
        if err == "apikey": return "⚠️ مفتاح OpenAI غير صالح أو مفقود."
        if r is None: return "⚠️ تعذّر التنفيذ حالياً."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] unexpected: %s", e)
        return "⚠️ خطأ غير متوقع."

# ===== Cyber: IPinfo / Kickbox / urlscan =====
def fmt_geo_ipinfo(data: dict, query: str) -> str:
    if not data: return "⚠️ لا توجد بيانات."
    if data.get("error"): return f"⚠️ {data['error']}"
    city = data.get("city","?")
    region = data.get("region","?")
    country = data.get("country","?")
    org = data.get("org","-")
    loc = data.get("loc","?,?")
    postal = data.get("postal","-")
    tz = data.get("timezone","-")
    parts = [
        f"🔎 الاستعلام: <code>{query}</code>",
        f"🌍 الدولة/المنطقة: {country} — {region}",
        f"🏙️ المدينة/الرمز: {city} — {postal}",
        f"⏰ التوقيت: {tz}",
        f"📡 ORG: {org}",
        f"📍 الإحداثيات: {loc}",
        "\nℹ️ هذه المعلومات من ipinfo.io."
    ]
    return "\n".join(parts)

async def fetch_geo(query: str) -> dict|None:
    # query قد يكون IP أو دومين
    target_ip = query
    if _HOST_RE.match(query):
        ip = resolve_ip(query)
        target_ip = ip or query
    if not IPINFO_TOKEN:
        # fallback خفيف إن لم يوجد مفتاح
        url = f"http://ip-api.com/json/{target_ip}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=15) as r:
                    data = await r.json(content_type=None)
                    if data.get("status") != "success":
                        return {"error": data.get("message","lookup failed")}
                    # تطبيع إلى صيغة ipinfo تقريبية
                    d = {
                        "city": data.get("city"),
                        "region": data.get("regionName"),
                        "country": data.get("country"),
                        "org": data.get("org") or data.get("isp"),
                        "loc": f"{data.get('lat')},{data.get('lon')}",
                        "postal": data.get("zip"),
                        "timezone": data.get("timezone")
                    }
                    return d
        except Exception as e:
            log.warning("[geo] fallback error: %s", e); return {"error":"network error"}
    else:
        url = f"https://ipinfo.io/{target_ip}?token={IPINFO_TOKEN}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=15) as r:
                    if r.status >= 400:
                        return {"error": f"ipinfo: HTTP {r.status}"}
                    return await r.json(content_type=None)
        except Exception as e:
            log.warning("[geo] ipinfo error: %s", e); return {"error":"network error"}

async def kickbox_verify(email: str) -> dict:
    if not KICKBOX_KEY:
        return {"error":"لم يتم ضبط KICKBOX_KEY"}
    url = f"https://api.kickbox.com/v2/verify?email={_urlparse.quote(email)}&apikey={KICKBOX_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=20) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {"error": f"HTTP {r.status}"}
            return data

async def urlscan_scan(u: str) -> dict:
    if not URLSCAN_KEY:
        return {"error":"لم يتم ضبط URLSCAN_KEY"}
    headers = {"API-Key": URLSCAN_KEY, "Content-Type":"application/json"}
    body = {"url": u, "visibility":"public"}
    async with aiohttp.ClientSession() as s:
        async with s.post("https://urlscan.io/api/v1/scan", headers=headers, json=body, timeout=30) as r:
            data = await r.json(content_type=None)
            # data يحتوي result و uuid
            return {"status": r.status, **data}

# ===== Link/Email helpers =====
async def http_head(url: str) -> int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=15) as r:
                return r.status
    except Exception:
        # بعض المواقع (مثل amazon) لا تدعم HEAD؛ جرّب GET خفيف
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, allow_redirects=True, timeout=15) as r:
                    return r.status
        except Exception:
            return None

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
    status = await http_head(u)
    issues = []
    if status is None:
        issues.append("⚠️ فشل الوصول (GET/HEAD)")
    else:
        issues.append(f"🔎 حالة HTTP: {status}")

    # urlscan (إن توفّر مفتاح)
    scan_line = "urlscan: غير مفعّل"
    if URLSCAN_KEY:
        try:
            res = await urlscan_scan(u)
            if res.get("error"):
                scan_line = f"urlscan: {res['error']}"
            else:
                result_url = res.get("result")
                uuid = res.get("uuid")
                if result_url:
                    scan_line = f"urlscan: ✅ <a href=\"{result_url}\">النتيجة</a> (uuid={uuid})"
                else:
                    scan_line = f"urlscan: ⏳ تم إرسال الفحص (uuid={uuid})"
        except Exception as e:
            scan_line = f"urlscan: خطأ {e}"

    # Geo عبر IPinfo
    ip = resolve_ip(host) if host else None
    geo_text = "⚠️ تعذّر حلّ IP للمضيف."
    if ip:
        data = await fetch_geo(ip)
        geo_text = fmt_geo_ipinfo(data, ip)

    return f"🔗 الرابط: <code>{u}</code>\nالمضيف: <code>{host}</code>\n" + "\n".join(issues) + f"\n{scan_line}\n\n{geo_text}"

async def email_check_kickbox(e: str) -> str:
    if not is_valid_email(e):
        return "❌ الإيميل غير صالح."
    res = await kickbox_verify(e) if KICKBOX_KEY else {"error":"KICKBOX_KEY غير مضبوط"}
    if res.get("error"):
        return f"⚠️ Kickbox: {res['error']}"
    # صياغة
    parts = [
        f"📧 {e}",
        f"نتيجة: {res.get('result','?')} ({res.get('reason','-')})",
        f"دومين: {res.get('domain','-')} | MX: {res.get('mx','-')}",
        f"Disposable: {'✅' if res.get('disposable') else '❌'}",
    ]
    if res.get("did_you_mean"):
        parts.append(f"هل تقصد: {res['did_you_mean']}")
    return "\n".join(parts)

# ===== AI Image: OpenAI or Replicate =====
async def ai_image_generate(prompt: str) -> bytes|None:
    provider = IMAGE_PROVIDER
    if provider == "replicate" and _replicate_sync is not None and REPLICATE_API_TOKEN:
        try:
            # تشغيل استدعاء Replicate في ثريد لأن مكتبتهم متزامنة
            def _run():
                os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
                out = _replicate_sync.run(REPLICATE_MODEL, input={"prompt": prompt})
                # out قد يكون قائمة روابط/رابط
                if isinstance(out, list) and out:
                    return out[0]
                if isinstance(out, str):
                    return out
                return None
            url = await asyncio.to_thread(_run)
            if not url: return None
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=120) as r:
                    if r.status == 200:
                        return await r.read()
                    return None
        except Exception as e:
            log.error("[image-gen] replicate %s", e)
            return None

    # افتراضي OpenAI
    if not AI_ENABLED or client is None:
        return None
    try:
        resp = client.images.generate(model=os.getenv("OPENAI_IMAGE_MODEL","gpt-image-1"), prompt=prompt, size="1024x1024")
        b64 = resp.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e)
        return None

# ===== File Tools =====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        imgs = [Image.open(p).convert("RGB") for p in image_paths]
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

# ===== yt-dlp =====
async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp غير مثبت"); return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).50s.%(ext)s")
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
            base, _ = os.path.splitext(fname)
            for ext in (".mp4",".m4a",".webm",".mp3",".mkv",".mp4.part",".m4a.part"):
                p = Path(base + ext)
                if p.exists() and p.is_file():
                    if p.stat().st_size > MAX_UPLOAD_BYTES:
                        y2_opts = ydl_opts | {"format":"bestaudio[filesize<45M]/bestaudio", "merge_output_format":"m4a"}
                        with yt_dlp.YoutubeDL(y2_opts) as y2:
                            info2 = y2.extract_info(url, download=True)
                            fname2 = y2.prepare_filename(info2)
                            for ext2 in (".m4a",".mp3",".webm"):
                                p2 = Path(os.path.splitext(fname2)[0] + ext2)
                                if p2.exists() and p2.is_file() and p2.stat().st_size <= MAX_UPLOAD_BYTES:
                                    return p2
                        return None
                    return p
    except Exception as e:
        log.error("[ydl] %s", e); return None
    return None

# ===== Keyboards =====
def gate_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn", lang), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn", lang), callback_data="verify")]
    ])

def section_back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

def bottom_menu_kb(uid: int):
    rows = []
    rows.append([InlineKeyboardButton("📂 الأقسام", callback_data="back_sections")])
    rows.append([InlineKeyboardButton("🌐 تغيير اللغة", callback_data="lang_menu")])
    rows.append([InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")])
    if user_is_premium(uid) or uid == OWNER_ID:
        rows.append([InlineKeyboardButton("⭐ حسابك VIP", callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")])
    rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def lang_menu_kb():
    rows = [[InlineKeyboardButton(f"{LANGS[code]} ({code})", callback_data=f"setlang_{code}")]
            for code in LANGS.keys()]
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

SECTIONS = {
    "ai_tools": {"title":"🤖 أدوات الذكاء الاصطناعي","desc":"دردشة/ترجمة/صوت→نص/توليد صور","is_free":True},
    "security": {"title":"🛡️ الأمن السيبراني","desc":"Geo IP, فحص رابط, فحص إيميل","is_free":True},
    "media": {"title":"⬇️ تحميل وسائط","desc":"YouTube / TikTok / Twitter / Instagram","is_free":True},
    "files": {"title":"🗜️ أداة ملفات","desc":"صورة→PDF وضغط الصور","is_free":True},
    "links": {"title":"🔗 روابط سريعة","desc":"أرقام مؤقتة / بطاقات افتراضية / نمو متابعين","is_free":True},
}

def sections_list_kb():
    rows = [[InlineKeyboardButton(("🟢 " if sec["is_free"] else "🔒 ") + sec["title"], callback_data=f"sec_{k}")]
            for k, sec in SECTIONS.items()]
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def ai_tools_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],
        [InlineKeyboardButton("🌐 ترجمة", callback_data="ai_translate")],
        [InlineKeyboardButton("🎙️ صوت→نص", callback_data="ai_stt")],
        [InlineKeyboardButton("🖼️ صور AI", callback_data="ai_image")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def security_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📍 Geo IP", callback_data="sec_geo")],
        [InlineKeyboardButton("🛡️ فحص رابط", callback_data="sec_linkscan")],
        [InlineKeyboardButton("✉️ فحص إيميل (Kickbox)", callback_data="sec_emailcheck")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def media_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 تنزيل فيديو/صوت", callback_data="sec_dl")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def files_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ صورة → PDF", callback_data="file_pdf")],
        [InlineKeyboardButton("🗜️ تصغير صورة", callback_data="file_compress")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def links_kb():
    rows = []
    if GROWTH_URLS:
        rows.append([InlineKeyboardButton("📈 رشق/نمو متابعين", url=GROWTH_URLS[0])])
    if TEMP_NUMBERS_URLS:
        rows.append([InlineKeyboardButton("☎️ أرقام مؤقتة", url=TEMP_NUMBERS_URLS[0])])
    if VCC_URLS:
        rows.append([InlineKeyboardButton("💳 بطاقات افتراضية", url=VCC_URLS[0])])
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")])
    return InlineKeyboardMarkup(rows)

# ===== Commands =====
async def on_startup(app: Application):
    # remove webhook (polling)
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("[startup] delete_webhook: %s", e)

    # resolve channel id
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

    # commands: public (start/help فقط)
    try:
        await app.bot.set_my_commands(
            [BotCommand("start","بدء"), BotCommand("help","مساعدة")],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # commands: owner
    if OWNER_ID:
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
                    BotCommand("restart","إعادة تشغيل"),
                ],
                scope=BotCommandScopeChat(chat_id=OWNER_ID)
            )
        except Exception as e:
            log.warning("[startup] set_my_commands owner: %s", e)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الأوامر المتاحة:\n/start — بدء\n/help — مساعدة")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    u = user_get(uid)
    # ترحيب
    try:
        if WELCOME_PHOTO and Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e:
        log.warning("[welcome] %s", e)

    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb(u.get("pref_lang","ar")))
        return

    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())

# ===== Buttons =====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()
    u = user_get(uid)

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك.\nاختر من القائمة:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك.\nانضم ثم اضغط تحقّق.", kb=gate_kb(u.get("pref_lang","ar")))
        return

    if q.data == "lang_menu":
        await safe_edit(q, "اختر اللغة:", kb=lang_menu_kb()); return

    if q.data.startswith("setlang_"):
        code = q.data.replace("setlang_", "")[:8]
        if code in LANGS:
            prefs_set_lang(uid, code)
            await safe_edit(q, f"✅ اللغة الافتراضية: {LANGS[code]} ({code})", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⚠️ رمز لغة غير معروف.", kb=bottom_menu_kb(uid))
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb(u.get("pref_lang","ar"))); return

    if q.data == "vip_badge":
        since = u.get("vip_since", 0)
        since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ حسابك VIP (مدى الحياة)\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}\n🌐 اللغة: {u.get('pref_lang','ar').upper()}", kb=bottom_menu_kb(uid)); return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return

    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك VIP بالفعل.", kb=bottom_menu_kb(uid)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(tr("back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API:
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            txt = (f"💳 ترقية إلى VIP ({VIP_PRICE_SAR:.2f} SAR)\n"
                   f"🔖 مرجع: <code>{ref}</code>")
            await safe_edit(q, txt, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 الدفع الآن", url=pay_url)],
                [InlineKeyboardButton("✅ تحقّق الدفع", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e); await safe_edit(q, "تعذّر إنشاء رابط الدفع.", kb=sections_list_kb())
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_", "")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP (مدى الحياة).", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ لم يصل إشعار الدفع بعد. جرّب لاحقًا.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تحقّق مرة أخرى", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        if key == "ai_tools":
            ai_set_mode(uid, None); await safe_edit(q, "🤖 أدوات الذكاء الاصطناعي:", kb=ai_tools_kb()); return
        if key == "security":
            ai_set_mode(uid, None); await safe_edit(q, "🛡️ الأمن السيبراني:", kb=security_kb()); return
        if key == "media":
            ai_set_mode(uid, None); await safe_edit(q, "⬇️ التحميل:", kb=media_kb()); return
        if key == "files":
            ai_set_mode(uid, None); await safe_edit(q, "🗜️ أدوات الملفات:", kb=files_kb()); return
        if key == "links":
            ai_set_mode(uid, None); await safe_edit(q, "🔗 روابط سريعة:", kb=links_kb()); return
        # فهرس الأقسام
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # أدوات الذكاء الاصطناعي (أزرار)
    if q.data == "ai_chat":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=sections_list_kb()); return
        ai_set_mode(uid, "ai_chat"); await safe_edit(q, "🤖 أرسل سؤالك الآن…", kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔚 إنهاء", callback_data="ai_stop")],[InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]])); return
    if q.data == "ai_translate":
        ai_set_mode(uid, "translate", {"to": u.get("pref_lang","ar")})
        await safe_edit(q, f"🌐 أرسل نصًا للترجمة → {u.get('pref_lang','ar').upper()}.", kb=section_back_kb()); return
    if q.data == "ai_stt":
        ai_set_mode(uid, "stt"); await safe_edit(q, "🎙️ أرسل Voice أو ملف صوت.", kb=section_back_kb()); return
    if q.data == "ai_image":
        ai_set_mode(uid, "image_ai"); await safe_edit(q, f"🖼️ اكتب وصف الصورة (المزوّد: {IMAGE_PROVIDER}).", kb=section_back_kb()); return
    if q.data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, "🔚 تم الإنهاء.", kb=sections_list_kb()); return

    # الأمن السيبراني
    if q.data == "sec_geo":
        ai_set_mode(uid, "geo_ip"); await safe_edit(q, "📍 أرسل IP أو دومين.", kb=section_back_kb()); return
    if q.data == "sec_linkscan":
        ai_set_mode(uid, "link_scan"); await safe_edit(q, "🛡️ أرسل الرابط لفحصه عبر urlscan + Geo.", kb=section_back_kb()); return
    if q.data == "sec_emailcheck":
        ai_set_mode(uid, "email_check"); await safe_edit(q, "✉️ أرسل الإيميل لفحصه عبر Kickbox.", kb=section_back_kb()); return

    # التحميل
    if q.data == "sec_dl":
        ai_set_mode(uid, "media_dl"); await safe_edit(q, "⬇️ أرسل رابط فيديو/صوت.", kb=section_back_kb()); return

    # الملفات
    if q.data == "file_pdf":
        ai_set_mode(uid, "file_img_to_pdf"); await safe_edit(q, "🖼️ أرسل صورة (أو أكثر) ثم /makepdf للإخراج.", kb=section_back_kb()); return
    if q.data == "file_compress":
        ai_set_mode(uid, "file_img_compress"); await safe_edit(q, "🗜️ أرسل صورة لضغطها.", kb=section_back_kb()); return

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

# ===== Message handling =====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # نص
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()

        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text)); return

        if mode == "geo_ip":
            target = text
            if _HOST_RE.match(target):
                ip = resolve_ip(target) or target
                target = ip
            data = await fetch_geo(target)
            await update.message.reply_text(fmt_geo_ipinfo(data, target), parse_mode="HTML"); return

        if mode == "link_scan":
            out = await link_scan(text)
            await update.message.reply_text(out, parse_mode="HTML", disable_web_page_preview=True); return

        if mode == "email_check":
            out = await email_check_kickbox(text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "translate":
            to = (extra or {}).get("to","ar")
            if not AI_ENABLED:
                await update.message.reply_text(tr("ai_disabled")); return
            prompt = f"Translate the following into {to}. Keep formatting where possible:\n\n{text}"
            r, err = _chat_with_fallback([
                {"role":"system","content":"You are a high-quality translator. Preserve meaning and style."},
                {"role":"user","content": prompt}
            ])
            if err: await update.message.reply_text("⚠️ تعذّر الترجمة حالياً."); return
            await update.message.reply_text((r.choices[0].message.content or "").strip()); return

        if mode == "media_dl":
            if not _URL_RE.search(text): await update.message.reply_text("أرسل رابط صالح."); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            path = await download_media(text)
            if path and path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
                try:
                    await update.message.reply_document(document=InputFile(str(path)))
                except Exception:
                    await update.message.reply_text("⚠️ تعذّر إرسال الملف.")
            else:
                await update.message.reply_text("⚠️ تعذّر التحميل أو أن الملف كبير.")
            return

        if mode == "image_ai":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            img_bytes = await ai_image_generate(text)
            if img_bytes:
                bio = BytesIO(img_bytes); bio.name = "image.png"
                await update.message.reply_photo(photo=InputFile(bio), caption=f"(provider: {IMAGE_PROVIDER})")
            else:
                await update.message.reply_text("⚠️ تعذّر توليد الصورة.")
            return

        if mode == "file_img_to_pdf":
            await update.message.reply_text("📌 أرسل صورًا (ثم /makepdf)."); return
        if mode == "file_img_compress":
            await update.message.reply_text("📌 أرسل صورة لضغطها."); return

        # لا يوجد وضع محدد
        await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb()); return

    # صوت
    if msg.voice or msg.audio:
        if mode == "ai_stt":
            if not AI_ENABLED:
                await update.message.reply_text(tr("ai_disabled")); return
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            try:
                with open(p, "rb") as f:
                    resp = client.audio.transcriptions.create(model="whisper-1", file=f)
                text = getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
            except Exception as e:
                log.error("[whisper] %s", e); text = "⚠️ تعذّر التحويل."
            await update.message.reply_text(text); return

    # صور (PDF/Compress أو ترجمة صورة إن فعّلت الرؤية)
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
            await update.message.reply_text(f"✅ تمت إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج.")
            return

    if msg.document:
        if mode in ("file_img_to_pdf","file_img_compress"):
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
                await update.message.reply_text(f"✅ تمت إضافة ملف صورة ({len(st_paths)}). أرسل /makepdf للإخراج.")
                return

    # افتراضي
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# makepdf
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("هذه الأداة تعمل بعد اختيار (صورة → PDF) من الأقسام.")
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

# ===== Owner commands =====
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ تم تفعيل VIP للمستخدم {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم إلغاء VIP للمستخدم {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /vipinfo <user_id>"); return
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
               f"OpenAI={v('openai')}  Replicate={v('replicate')}\n"
               f"Provider(image)={IMAGE_PROVIDER}")
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
    lines = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        lines.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={ts}")
    await update.message.reply_text("\n".join(lines))

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    ok = await is_member(context, update.effective_user.id, force=True)
    await update.message.reply_text(f"member={ok}")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 إعادة تشغيل...")
    os._exit(0)

# ===== Errors =====
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ===== Main =====
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())

    # public cmds
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))

    # owner cmds
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

    # buttons
    app.add_handler(CallbackQueryHandler(on_button))

    # messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE, guard_messages))
    app.add_handler(MessageHandler(filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))

    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()


