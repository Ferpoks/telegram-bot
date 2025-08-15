# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, tempfile, logging, base64, hashlib, socket
from pathlib import Path
from io import BytesIO

from dotenv import load_dotenv
from PIL import Image

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

import aiohttp

# ====== LOGGING ======
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ====== ENV ======
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp")); TMP_DIR.mkdir(parents=True, exist_ok=True)

# Providers
IMAGE_PROVIDER   = (os.getenv("IMAGE_PROVIDER","openai") or "openai").lower()   # openai | replicate
EMAIL_PROVIDER   = (os.getenv("EMAIL_PROVIDER","") or "").lower()               # kickbox | …
GEO_PROVIDER     = (os.getenv("GEO_PROVIDER","") or "").lower()                 # ipinfo | …
URLSCAN_KEY      = (os.getenv("URLSCAN_KEY","") or "").strip()
IPINFO_TOKEN     = (os.getenv("IPINFO_TOKEN","") or "").strip()
KICKBOX_KEY      = (os.getenv("KICKBOX_KEY","") or "").strip()

REPLICATE_API_TOKEN = (os.getenv("REPLICATE_API_TOKEN","") or "").strip()
REPLICATE_MODEL     = (os.getenv("REPLICATE_MODEL","black-forest-labs/flux-schnell") or "black-forest-labs/flux-schnell").strip()

# OpenAI (للدردشة/الترجمة/Whisper فقط)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY","") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL","gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION","0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

# Owner & UI
OWNER_ID = int(os.getenv("OWNER_ID","6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME","ferpo_ksa").strip().lstrip("@")
def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# Channels gate
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"
CHANNEL_ID = None

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "أدوات ذكاء اصطناعي، أمن سيبراني، تنزيل وسائط، تحويل ملفات، وأكثر — كلها داخل تيليجرام.\n"
    "الأساسيات متاحة للجميع، وميزات إضافية لعملاء VIP. ✨"
)

# Paylink (كما هي عندك)
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = (os.getenv("PAY_WEBHOOK_SECRET","") or "").strip()
PAYLINK_API_BASE   = (os.getenv("PAYLINK_API_BASE","https://restapi.paylink.sa/api")).rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID","") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET","") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL","") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR","10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API","1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE","") or "").strip()

# yt-dlp
try:
    import yt_dlp
except Exception:
    yt_dlp = None

# whois + dns (اختياري)
try:
    import whois as pywhois
except Exception:
    pywhois = None
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None

# ====== WEB SERVER (health/payhook) ======
SERVE_HEALTH = os.getenv("SERVE_HEALTH","0") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()}"
    return (base or "").rstrip("/") + path

def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj: return None
    if isinstance(obj,(str,bytes)):
        s = obj.decode() if isinstance(obj,bytes) else obj
        for pat in [r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)",
                    r"[?&]ref=([\w\-:]+)", r"(\d{6,}-\d{9,})"]:
            m = re.search(pat, s); 
            if m and _looks_like_ref(m.group(1)): return m.group(1)
        return None
    if isinstance(obj,dict):
        for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
            v = obj.get(k); 
            if isinstance(v,str) and _looks_like_ref(v.strip()): return v.strip()
        for v in obj.values():
            got = _find_ref_in_obj(v); 
            if got: return got
        return None
    if isinstance(obj,(list,tuple)):
        for v in obj:
            got = _find_ref_in_obj(v); 
            if got: return got
    return None

async def _payhook(request):
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok":False,"error":"bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    ref = _find_ref_in_obj(data)
    if not ref:
        log.warning("[payhook] no-ref; keys=%s", list(data.keys())[:6])
        return web.json_response({"ok":False,"error":"no-ref"}, status=200)
    activated = payments_mark_paid_by_ref(ref, raw=data)
    log.info("[payhook] ref=%s -> activated=%s", ref, activated)
    return web.json_response({"ok":True,"ref":ref,"activated":bool(activated)}, status=200)

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)): return
    async def _make_app():
        app = web.Application()
        if SERVE_HEALTH:
            async def _health(_): return web.json_response({"ok":True})
            app.router.add_get("/health", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            async def _hook_get(_): return web.json_response({"ok":True})
            app.router.add_get("/payhook", _hook_get)
        return app
    def _thread_main():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _start():
            app = await _make_app()
            runner = web.AppRunner(app); await runner.setup()
            port = int(os.getenv("PORT","10000"))
            site = web.TCPSite(runner,"0.0.0.0",port); await site.start()
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s health=%s)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF", "ON" if SERVE_HEALTH else "OFF")
        loop.run_until_complete(_start()); 
        try: loop.run_forever()
        finally: loop.stop(); loop.close()
    threading.Thread(target=_thread_main, daemon=True).start()
_run_http_server()

# ====== STARTUP ======
async def on_startup(app: Application):
    try: await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: log.warning("[startup] delete_webhook: %s", e)

    # resolve channel
    global CHANNEL_ID; CHANNEL_ID = None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id; log.info("[startup] resolved @%s -> %s", u, CHANNEL_ID); break
        except Exception as e:
            log.warning("[startup] get_chat @%s failed: %s", u, e)
    if CHANNEL_ID is None:
        log.error("[startup] could not resolve channel id; will use @username checks")

    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"),
                BotCommand("help","مساعدة"),
                BotCommand("geo","تحديد موقع IP"),
                BotCommand("osint","بحث ذكي"),
                BotCommand("write","كتابة محتوى"),
                BotCommand("stt","تحويل صوت لنص"),
                BotCommand("tr","ترجمة"),
                BotCommand("scan","فحص رابط"),
                BotCommand("email","فحص إيميل"),
                BotCommand("dl","تحميل وسائط"),
                BotCommand("img","صورة AI"),
                BotCommand("file","أداة ملفات")
            ],
            scope=BotCommandScopeDefault()
        )
        # owner-only full commands:
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
                BotCommand("restart","إعادة تشغيل")
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands: %s", e)

# ====== DB ======
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None: return conn
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row; _db._conn = conn
    log.info("[db] using %s", DB_PATH); return conn

def migrate_db(force_reset=False):
    with _conn_lock:
        c = _db().cursor()
        if force_reset:
            log.warning("[db] RESET requested; dropping known tables")
            for t in ("users","ai_state","payments"): 
                try: _db().execute(f"DROP TABLE IF EXISTS {t};")
                except Exception: pass

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
        );""")
        # ensure essential columns exist
        c.execute("PRAGMA table_info(users)"); ucols = {r["name"] for r in c.fetchall()}
        for col, ddl in [
            ("verified_ok", "ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;"),
            ("verified_at", "ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;"),
            ("vip_forever","ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0;"),
            ("vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;"),
            ("pref_lang","ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar';"),
        ]:
            if col not in ucols:
                _db().execute(ddl)

        # ai_state
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        c.execute("PRAGMA table_info(ai_state)"); acols = {r["name"] for r in c.fetchall()}
        if "extra" not in acols:
            _db().execute("ALTER TABLE ai_state ADD COLUMN extra TEXT DEFAULT NULL;")

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
        );""")
        _db().commit()

def init_db():
    force = os.getenv("DB_RESET","0") == "1"
    migrate_db(force_reset=force)

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id":uid,"premium":0,"verified_ok":0,"verified_at":0,"vip_forever":0,"vip_since":0,"pref_lang":"ar"}
        return dict(r)

def user_is_premium(uid: int|str) -> bool:
    u = user_get(uid); return bool(u.get("premium")) or bool(u.get("vip_forever"))

def ai_set_mode(uid: int|str, mode: str|None, extra: dict|None=None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id,mode,extra,updated_at) VALUES (?,?,?,strftime('%s','now')) "
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

# payments (مختصر)
def payments_new_ref(uid: int) -> str: return f"{uid}-{int(time.time())}"
def payments_create(uid: int, amount: float, provider="paylink", ref: str|None=None) -> str:
    ref = ref or payments_new_ref(uid)
    with _conn_lock:
        _db().execute("INSERT OR REPLACE INTO payments (ref,user_id,amount,provider,status,created_at) VALUES (?,?,?,?,?,?)",
                      (ref,str(uid),amount,provider,"pending",int(time.time())))
        _db().commit()
    return ref
def payments_status(ref: str) -> str|None:
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT status FROM payments WHERE ref=?", (ref,)); r=c.fetchone()
        return r["status"] if r else None
def payments_mark_paid_by_ref(ref: str, raw=None) -> bool:
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT user_id,status FROM payments WHERE ref=?", (ref,)); r=c.fetchone()
        if not r: return False
        if r["status"]=="paid": return True
        _db().execute("UPDATE payments SET status='paid',paid_at=?,raw=? WHERE ref=?",
                      (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref))
        _db().commit()
    try:
        _db().execute("UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0), strftime('%s','now')) WHERE id=?",(r["user_id"],)); _db().commit()
    except Exception as e: log.error("[payments] grant error: %s", e)
    return True

# ====== HELPERS ======
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except: pass

_member_cache = {}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now: return cached[0]
    targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
    for a in range(retries):
        for t in targets:
            try:
                cm = await context.bot.get_chat_member(t, user_id)
                ok = getattr(cm,"status",None) in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id]=(True, now+60); return True
            except Exception as e:
                log.warning("[is_member] t=%s err=%s", t, e)
        if a < retries-1: await asyncio.sleep(backoff*(a+1))
    _member_cache[user_id]=(False, now+60); return False

# ==== Security Utils ====
_URL_RE = re.compile(r"https?://[^\s]+")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
def resolve_ip(host: str) -> str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for fam,_,_,_,sockaddr in infos:
            ip = sockaddr[0]
            if ":" not in ip: return ip
        return infos[0][4][0] if infos else None
    except Exception:
        return None

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

def md5_hex(s: str) -> str: return hashlib.md5(s.strip().lower().encode()).hexdigest()

async def http_head(url: str) -> int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=15) as r:
                return r.status
    except Exception:
        return None

# ---- Geo via ipinfo (أولوية إذا مفعل) ----
async def fetch_geo(query: str) -> dict|None:
    # لو دومين: حوّله IP
    if _HOST_RE.match(query or ""):
        ip = resolve_ip(query)
        if ip: query = ip
    if GEO_PROVIDER == "ipinfo" and IPINFO_TOKEN:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://ipinfo.io/{query}/json?token={IPINFO_TOKEN}", timeout=15) as r:
                    data = await r.json(content_type=None)
                    if "bogon" in data: return {"error": "bogon / private IP"}
                    return {"query": query, **data}
        except Exception as e:
            log.warning("[ipinfo] %s", e)
            return {"error":"network error"}
    # fallback: ip-api
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse", timeout=15) as r:
                data = await r.json(content_type=None)
                return data
    except Exception:
        return {"error":"network error"}

def fmt_geo(data: dict) -> str:
    if not data: return "⚠️ تعذّر جلب البيانات."
    if data.get("error"): return f"⚠️ {data['error']}"
    if GEO_PROVIDER == "ipinfo" and ("ip" in data or "loc" in data):
        lat,lon = ("?", "?")
        if data.get("loc"):
            try: lat,lon = data["loc"].split(",")
            except: pass
        parts = [
            f"🔎 الاستعلام: <code>{data.get('query', data.get('ip',''))}</code>",
            f"🌍 الدولة/المنطقة: {data.get('country','?')} — {data.get('region','?')}",
            f"🏙️ المدينة: {data.get('city','?')}",
            f"⏰ التوقيت: {data.get('timezone','-')}",
            f"📡 ORG: {data.get('org','-')}",
            f"📍 الإحداثيات: {lat}, {lon}",
        ]
        return "\n".join(parts)
    # ip-api format
    parts = [
        f"🔎 الاستعلام: <code>{data.get('query','')}</code>",
        f"🌍 الدولة/المنطقة: {data.get('country','?')} — {data.get('regionName','?')}",
        f"🏙️ المدينة/الرمز: {data.get('city','?')} — {data.get('zip','-')}",
        f"⏰ التوقيت: {data.get('timezone','-')}",
        f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}",
        f"🛰️ AS: {data.get('as','-')}",
        f"📍 الإحداثيات: {data.get('lat','?')}, {data.get('lon','?')}",
    ]
    if data.get("reverse"): parts.append(f"🔁 Reverse: {data['reverse']}")
    return "\n".join(parts)

# ---- Kickbox email verify (إن توفّر) ----
async def email_check(e: str) -> str:
    if not is_valid_email(e): return "❌ الإيميل غير صالح."
    if EMAIL_PROVIDER == "kickbox" and KICKBOX_KEY:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.kickbox.com/v2/verify",
                                 params={"email": e, "apikey": KICKBOX_KEY}, timeout=20) as r:
                    data = await r.json(content_type=None)
                    result = data.get("result")  # deliverable / undeliverable / risky / unknown
                    reason = data.get("reason")
                    did_you_mean = data.get("did_you_mean")
                    out = [f"📧 {e}", f"نتيجة: {result or '-'}", f"سبب: {reason or '-'}"]
                    if did_you_mean: out.append(f"هل تقصد: {did_you_mean}")
                    return "\n".join(out)
        except Exception as ex:
            log.warning("[kickbox] %s", ex)
    # Fallback بسيط (MX + disposable)
    dom = e.split("@",1)[1].lower()
    disp = "✅ ليس ضمن المؤقت"
    DISPOSABLE = {"mailinator.com","tempmail.com","10minutemail.com","yopmail.com","guerrillamail.com","trashmail.com"}
    if dom in DISPOSABLE: disp = "❌ دومين مؤقت معروف"
    mx = "❓"; 
    if dnsresolver:
        try:
            ans = dnsresolver.resolve(dom,"MX"); mx = "✅ موجود" if len(ans) else "❌ غير موجود"
        except Exception: mx = "❌ غير موجود"
    return f"📧 {e}\nMX: {mx}\nDisposable: {disp}"

# ---- URL Scan (urlscan.io) + فحص سريع ----
async def link_scan(u: str) -> str:
    if not _URL_RE.search(u or ""):
        return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    # HEAD سريع
    st = await http_head(u)
    lines = [f"🔗 الرابط: <code>{u}</code>", f"🔎 حالة HTTP: {st if st is not None else 'N/A'}"]
    # تقديم طلب مسح إلى urlscan (بدون انتظار)
    if URLSCAN_KEY:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    "https://urlscan.io/api/v1/scan/",
                    headers={"API-Key": URLSCAN_KEY, "Content-Type":"application/json"},
                    json={"url": u, "visibility": "public"},
                    timeout=20
                ) as r:
                    data = await r.json(content_type=None)
                    res = data.get("result")
                    uuid = data.get("uuid")
                    if res:
                        lines.append(f"📄 تقرير جاهز خلال ثوانٍ: {res}")
                    elif uuid:
                        lines.append(f"📄 سيتوفر التقرير هنا: https://urlscan.io/result/{uuid}")
        except Exception as ex:
            log.warning("[urlscan] %s", ex)
    # Geo للمضيف
    try:
        host = re.sub(r"^https?://","",u).split("/",1)[0]
        ip = resolve_ip(host)
        if ip:
            data = await fetch_geo(ip)
            lines.append("\n"+fmt_geo(data))
    except Exception: pass
    return "\n".join(lines)

# ---- OSINT (اسم/إيميل) مبسّط ----
async def osint_email(email: str) -> str:
    # kickbox + geo على دومين + gravatar
    g_url = f"https://www.gravatar.com/avatar/{md5_hex(email)}?d=404"
    g_st = await http_head(g_url)
    grav = "✅ موجود" if g_st and 200 <= g_st < 300 else "❌ غير موجود"
    dom = email.split("@",1)[1]
    ip = resolve_ip(dom)
    geo_txt = fmt_geo(await fetch_geo(ip)) if ip else "⚠️ تعذّر حلّ IP للدومين."
    who = "WHOIS: غير متاح"
    if pywhois:
        try:
            w = pywhois.whois(dom); who = f"WHOIS:\n- Registrar: {getattr(w,'registrar',None)}\n- Created: {getattr(w,'creation_date',None)}\n- Expires: {getattr(w,'expiration_date',None)}"
        except Exception as e:
            who = f"WHOIS: {e}"
    res = await email_check(email)
    return f"{res}\n🖼️ Gravatar: {grav}\n{who}\n\n{geo_txt}"

async def osint_username(name: str) -> str:
    uname = re.sub(r"[^\w\-.]+","",name.strip())
    if len(uname) < 3: return "⚠️ أدخل اسم/يوزر صالح (٣ أحرف على الأقل)."
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/users/{uname}", timeout=15) as r:
                if r.status == 200:
                    d = await r.json()
                    return f"👤 GitHub: ✅ موجود — public_repos={d.get('public_repos')} منذ {d.get('created_at')}"
                elif r.status == 404:
                    return "👤 GitHub: ❌ غير موجود"
    except Exception: pass
    return "ℹ️ فحوص إضافية يمكن ربطها لاحقًا."

# ====== AI ======
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None: return None, "ai_disabled"
    models = [OPENAI_CHAT_MODEL, "gpt-4o-mini","gpt-4.1-mini","gpt-4o","gpt-4.1","gpt-3.5-turbo"]
    seen=set(); order=[m for m in models if m and not (m in seen or seen.add(m))]
    last=None
    for m in order:
        try:
            r = client.chat.completions.create(model=m, messages=messages, temperature=0.7, timeout=60)
            return r, None
        except Exception as e:
            s=str(e); last=s
            if "quota" in s or "insufficient_quota" in s: return None,"quota"
            if "api key" in s.lower(): return None,"apikey"
    return None,last or "unknown"

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED: return "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً."
    try:
        r, err = _chat_with_fallback([{"role":"system","content":"أجب بالعربية بإيجاز ووضوح."},{"role":"user","content":prompt}])
        if err: return "⚠️ تعذّر التنفيذ حالياً."
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return "⚠️ خطأ غير متوقع."

# ---- توليد صور: Replicate أولاً، وإلا OpenAI كـ fallback ----
def _replicate_run_sync(model: str, prompt: str):
    import replicate
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
    # بعض الموديلات تقبل حقل prompt فقط
    return replicate.run(model, input={"prompt": prompt})

async def ai_image_generate(prompt: str) -> bytes|None:
    if IMAGE_PROVIDER == "replicate" and REPLICATE_API_TOKEN:
        try:
            out = await asyncio.to_thread(_replicate_run_sync, REPLICATE_MODEL, prompt)
            if not out: return None
            # out قد يكون list[bytes-like/URL] أو str URL
            first = out[0] if isinstance(out, (list,tuple)) else out
            if hasattr(first, "read"):
                return first.read()
            if isinstance(first, (bytes,bytearray)):
                return bytes(first)
            if isinstance(first, str) and first.startswith("http"):
                async with aiohttp.ClientSession() as s:
                    async with s.get(first, timeout=60) as r:
                        return await r.read()
            return None
        except Exception as e:
            log.error("[image-gen/replicate] %s", e)
    # fallback إلى OpenAI (إن متاح)
    if AI_ENABLED and client is not None:
        try:
            resp = client.images.generate(model=os.getenv("OPENAI_IMAGE_MODEL","gpt-image-1"), prompt=prompt, size="1024x1024")
            b64 = resp.data[0].b64_json
            return base64.b64decode(b64)
        except Exception as e:
            log.error("[image-gen/openai] %s", e)
    return None

# ====== UI ======
def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 الانضمام للقناة", url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton("✅ تحقّق من القناة", callback_data="verify")]
    ])

def bottom_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = [
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton("📂 الأقسام", callback_data="back_sections")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())],
    ]
    rows.insert(1, [InlineKeyboardButton("⭐ حسابك VIP" if is_vip else "⚡ ترقية إلى VIP", callback_data="vip_badge" if is_vip else "upgrade")])
    return InlineKeyboardMarkup(rows)

SECTIONS = {
    "geolocation": {"title": "🛰️ IP Lookup", "desc": "أرسل IP/دومين"، "is_free": True},
    "osint": {"title": "🔎 البحث الذكي (OSINT)", "desc": "يوزر/إيميل", "is_free": False},
    "writer": {"title": "✍️ كاتب إعلانات", "desc": "", "is_free": True},
    "stt": {"title": "🎙️ تحويل صوت لنص", "desc": "", "is_free": True},
    "translate": {"title": "🌐 مترجم", "desc": "", "is_free": True},
    "link_scan": {"title": "🛡️ فحص الروابط", "desc": "", "is_free": False},
    "email_checker": {"title": "✉️ فحص بريد", "desc": "", "is_free": False},
    "media_dl": {"title": "⬇️ تنزيل وسائط", "desc": "", "is_free": True},
    "file_tools": {"title": "🗜️ أداة ملفات", "desc": "", "is_free": True},
    "image_ai": {"title": "🖼️ صور AI", "desc": "", "is_free": True},
}

def sections_list_kb():
    rows=[]
    for k, sec in SECTIONS.items():
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{k}")])
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def section_back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower(): log.warning("safe_edit: %s", e)

# ====== Commands ======
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الأوامر: /start /help /geo /osint /write /stt /tr /scan /email /dl /img /file")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    user_get(uid)
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO,"rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e:
        log.warning("[welcome] %s", e)
    if not (await is_member(context, uid, retries=3)):
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())

# Handlers shortcuts
async def geo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not (await is_member(context, uid)): await update.message.reply_text("🔐 انضم للقناة:", reply_markup=gate_kb()); return
    ai_set_mode(uid,"geo_ip"); await update.message.reply_text("📍 أرسل IP أو دومين.", parse_mode="HTML")
async def osint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_is_premium(uid) and uid!=OWNER_ID:
        await update.message.reply_text("🔒 VIP فقط.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid,"osint"); await update.message.reply_text("🔎 أرسل يوزر/إيميل.", parse_mode="HTML")
async def write_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_set_mode(update.effective_user.id,"writer"); await update.message.reply_text("✍️ صف ما تريد كتابته.")
async def stt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_set_mode(update.effective_user.id,"stt"); await update.message.reply_text("🎙️ أرسل Voice أو ملف صوت.")
async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; ai_set_mode(uid,"translate",{"to": user_get(uid).get("pref_lang","ar")})
    await update.message.reply_text("🌐 أرسل نص للترجمة.")
async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not user_is_premium(uid) and uid!=OWNER_ID:
        await update.message.reply_text("🔒 VIP فقط.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid,"link_scan"); await update.message.reply_text("🛡️ أرسل الرابط.")
async def email_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not user_is_premium(uid) and uid!=OWNER_ID:
        await update.message.reply_text("🔒 VIP فقط.", reply_markup=bottom_menu_kb(uid)); return
    ai_set_mode(uid,"email_check"); await update.message.reply_text("✉️ أرسل الإيميل.")
async def dl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_set_mode(update.effective_user.id,"media_dl"); await update.message.reply_text("⬇️ أرسل رابط فيديو/صوت.")
async def img_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_set_mode(update.effective_user.id,"image_ai"); await update.message.reply_text("🖼️ صف الصورة.")
async def file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_set_mode(update.effective_user.id,"file_tools_menu"); await update.message.reply_text("🗜️ اختر الأداة: صورة→PDF أو ضغط صورة.")

# Buttons
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q=update.callback_query; uid=q.from_user.id
    await q.answer()
    if q.data=="verify":
        ok = await is_member(context, uid, force=True)
        if ok:
            await safe_edit(q,"👌 تم التحقق. اختر من القائمة:", bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q,"❗️ ما زلت غير مشترك.\nانضم ثم اضغط تحقّق.", gate_kb()); 
        return
    if q.data=="myinfo":
        u=user_get(uid); await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}\n🌐 لغة الترجمة: {u.get('pref_lang','ar').upper()}", bottom_menu_kb(uid)); return
    if q.data=="back_home":
        await safe_edit(q, "👇 القائمة:", bottom_menu_kb(uid)); return
    if q.data=="back_sections":
        await safe_edit(q, "📂 الأقسام:", sections_list_kb()); return

    if q.data.startswith("sec_"):
        key=q.data.replace("sec_","")
        sec=SECTIONS.get(key)
        if not sec: await safe_edit(q,"قريبًا…", sections_list_kb()); return
        allowed = sec.get("is_free") or user_is_premium(uid) or uid==OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\nهذه الميزة VIP.", sections_list_kb()); return
        # modes
        mapping={
            "geolocation":("geo_ip","📍 أرسل IP/دومين"),
            "osint":("osint","🔎 أرسل يوزر/إيميل"),
            "writer":("writer","✍️ اكتب المطلوب"),
            "stt":("stt","🎙️ أرسل Voice/ملف صوت"),
            "translate":("translate","🌐 أرسل نص للترجمة"),
            "link_scan":("link_scan","🛡️ أرسل الرابط"),
            "email_checker":("email_check","✉️ أرسل الإيميل"),
            "media_dl":("media_dl","⬇️ أرسل الرابط"),
            "file_tools":("file_tools_menu","🗜️ اختر أداة الملفات"),
            "image_ai":("image_ai","🖼️ صف الصورة")
        }
        mode, text = mapping.get(key, (None, sec.get("desc","")))
        if mode: ai_set_mode(uid, mode)
        await safe_edit(q, text or sec.get("title",""), section_back_kb()); return

# Download media
MAX_UPLOAD_MB = 47; MAX_UPLOAD_BYTES = MAX_UPLOAD_MB*1024*1024
async def tg_download_to_path(bot, file_id: str, suffix: str="") -> Path:
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR)); os.close(fd)
    await f.download_to_drive(tmp_path); return Path(tmp_path)
async def download_media(url: str) -> Path|None:
    if yt_dlp is None: return None
    outtmpl = str(TMP_DIR / "%(title).50s.%(ext)s")
    ydl_opts = {"outtmpl": outtmpl, "format": "bestvideo[filesize<45M]+bestaudio/best[filesize<45M]/best",
                "merge_output_format":"mp4","quiet":True,"no_warnings":True,"retries":2,"noplaylist":True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True); fname = ydl.prepare_filename(info)
            base, _ = os.path.splitext(fname)
            for ext in (".mp4",".m4a",".webm",".mp3",".mkv"):
                p = Path(base+ext)
                if p.exists() and p.is_file():
                    if p.stat().st_size > MAX_UPLOAD_BYTES:
                        y2o = ydl_opts | {"format": "bestaudio[filesize<45M]/bestaudio", "merge_output_format":"m4a"}
                        with yt_dlp.YoutubeDL(y2o) as y2:
                            info2 = y2.extract_info(url, download=True)
                            fname2 = y2.prepare_filename(info2)
                            for ext2 in (".m4a",".mp3",".webm"):
                                p2 = Path(os.path.splitext(fname2)[0]+ext2)
                                if p2.exists() and p2.is_file() and p2.stat().st_size <= MAX_UPLOAD_BYTES: return p2
                        return None
                    return p
    except Exception as e:
        log.error("[ydl] %s", e)
    return None

# File tools
def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        imgs=[Image.open(p).convert("RGB") for p in image_paths]
        if not imgs: return None
        out = TMP_DIR / f"images_{int(time.time())}.pdf"
        first, rest = imgs[0], imgs[1:]; first.save(out, save_all=True, append_images=rest); return out
    except Exception as e:
        log.error("[img->pdf] %s", e); return None
def compress_image(image_path: Path, quality: int=70) -> Path|None:
    try:
        im=Image.open(image_path); out=TMP_DIR / f"compressed_{image_path.stem}.jpg"
        im.convert("RGB").save(out,"JPEG",optimize=True,quality=max(1,min(quality,95))); return out
    except Exception as e:
        log.error("[compress] %s", e); return None

# ====== Messages ======
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; user_get(uid)
    if not (await is_member(context, uid)): 
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    mode, extra = ai_get_mode(uid)
    if not mode:
        await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
        await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb()); return

    msg = update.message
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()
        if mode=="ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text)); return
        if mode=="geo_ip":
            data = await fetch_geo(text)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return
        if mode=="osint":
            out = await (osint_email(text) if ("@" in text and "." in text) else osint_username(text))
            await update.message.reply_text(out, parse_mode="HTML"); return
        if mode=="writer":
            await update.message.reply_text(ai_chat_reply(f"اكتب إعلانًا جذابًا:\n{text}"), parse_mode="HTML"); return
        if mode=="translate":
            if not AI_ENABLED: await update.message.reply_text("🧠 غير مفعّل."); return
            r,_ = _chat_with_fallback([
                {"role":"system","content":"You are a high-quality translator. Preserve meaning and style."},
                {"role":"user","content": f"Translate into {(extra or {}).get('to','ar')}: {text}"}
            ])
            await update.message.reply_text((r.choices[0].message.content or "").strip()); return
        if mode=="link_scan":
            await update.message.reply_text(await link_scan(text), parse_mode="HTML"); return
        if mode=="email_check":
            await update.message.reply_text(await email_check(text)); return
        if mode=="media_dl":
            if not _URL_RE.search(text):
                await update.message.reply_text("أرسل رابط صالح يبدأ بـ http/https."); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            path = await download_media(text)
            if path and path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
                try: await update.message.reply_document(document=InputFile(str(path)))
                except Exception: await update.message.reply_text("⚠️ تعذّر إرسال الملف.")
            else:
                await update.message.reply_text("⚠️ تعذّر التحميل أو الملف كبير."); return
            return
        if mode=="numbers":
            await update.message.reply_text("☎️ أرقام مؤقتة: أرسل الخدمة، وسأرجع لك روابط مزوّدين موثوقين."); return
        if mode=="image_ai":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            img = await ai_image_generate(text)
            if img:
                bio = BytesIO(img); bio.name="ai.png"
                await update.message.reply_photo(photo=InputFile(bio))
            else:
                await update.message.reply_text("⚠️ تعذّر توليد الصورة.")
            return
        if mode in ("file_tools_menu","file_img_to_pdf","file_img_compress"):
            await update.message.reply_text("📌 أرسل صورة (أو عدة صور لـ PDF)."); return

    if msg.voice or msg.audio:
        if ai_get_mode(uid)[0] == "stt":
            if not AI_ENABLED: await update.message.reply_text("🧠 غير مفعّل."); return
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            try:
                with open(str(p),"rb") as f:
                    resp = client.audio.transcriptions.create(model="whisper-1", file=f)
                await update.message.reply_text(getattr(resp,"text","").strip() or "⚠️ لم أستطع استخراج النص.")
            except Exception as e:
                log.error("[whisper] %s", e); await update.message.reply_text("⚠️ تعذّر التحويل.")
            return

    if msg.photo:
        photo = msg.photo[-1]; p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
        if ai_get_mode(uid)[0] == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists(): await update.message.reply_document(InputFile(str(outp)))
            else: await update.message.reply_text("⚠️ فشل الضغط.")
            return
        if ai_get_mode(uid)[0] == "file_img_to_pdf":
            mode, extra = ai_get_mode(uid); st = (extra or {}).get("paths", [])
            st.append(str(p)); ai_set_mode(uid,"file_img_to_pdf",{"paths": st})
            await update.message.reply_text(f"✅ أُضيفت صورة ({len(st)}). أرسل /makepdf للإخراج."); return

    if msg.document and ai_get_mode(uid)[0] in ("file_img_to_pdf","file_img_compress"):
        p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
        if ai_get_mode(uid)[0] == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists(): await update.message.reply_document(InputFile(str(outp)))
            else: await update.message.reply_text("⚠️ فشل الضغط.")
            return
        if ai_get_mode(uid)[0] == "file_img_to_pdf":
            mode, extra = ai_get_mode(uid); st = (extra or {}).get("paths", [])
            st.append(str(p)); ai_set_mode(uid,"file_img_to_pdf",{"paths": st})
            await update.message.reply_text(f"✅ أُضيفت صورة ({len(st)}). أرسل /makepdf للإخراج."); return

    await update.message.reply_text("🤖 جاهز. اختر ميزة من /help أو من الأزرار.", reply_markup=bottom_menu_kb(uid))

async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf": await update.message.reply_text("استخدم /file ثم (صورة → PDF)."); return
    paths = (extra or {}).get("paths", [])
    if not paths: await update.message.reply_text("لم يتم استلام صور بعد."); return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    ai_set_mode(uid, "file_tools_menu", {})

# Owner commands (مختصر):
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))
async def aidiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(p): 
            try: return version(p)
            except PackageNotFoundError: return "not-installed"
        key = (os.getenv("OPENAI_API_KEY") or "")
        msg = f"AI_ENABLED={'ON' if AI_ENABLED else 'OFF'}\nKey={'set' if key else 'missing'}\nModel={OPENAI_CHAT_MODEL}\nopenai={v('openai')}\nreplicate={v('replicate')}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"aidiag error: {e}")
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 إعادة تشغيل…"); os._exit(0)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context,'error','unknown'))

def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    # public commands (للمستخدمين العاديين يكفي /start و /help لكن نترك الباقي لمن يعرفها)
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
    # owner
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("restart", restart_cmd))
    # callbacks & messages
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()



