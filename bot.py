# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv

# ========= LOGGING =========
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ========= Third-Party Clients =========
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from PIL import Image
import aiohttp

try:
    import yt_dlp
except Exception:
    yt_dlp = None

try:
    import whois as pywhois
except Exception:
    pywhois = None

try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None
    dnsexception = None

# ========= Telegram =========
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

# ========= ENV =========
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

MAX_UPLOAD_MB = 47
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# القنوات
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS", "ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"
CHANNEL_ID = None

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO", "assets/ferpoks.jpg")

# ========= Payments (VIP مدى الحياة) =========
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = (os.getenv("PAY_WEBHOOK_SECRET") or "").strip()
PAYLINK_API_BASE   = (os.getenv("PAYLINK_API_BASE") or "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# ========= External Services (اختياري) =========
FIVESIM_API_KEY = (os.getenv("FIVESIM_API_KEY") or "").strip()

# ========= HTTP tiny server (health + payhook) =========
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1" or PAY_WEBHOOK_ENABLE

def _clean_base(url: str) -> str:
    u = (url or "").strip().strip('"').strip("'")
    if u.startswith("="):
        u = u.lstrip("=")
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE or "")
    if "{ref}" in base:
        return base.format(ref=ref)
    if base:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}ref={ref}"
    # بدون Base: نعرض ref فقط
    return f"https://example.com/checkout?ref={ref}"

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or ""
    if not base:
        hn = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
        if hn:
            base = f"https://{hn}"
    return (base or "").rstrip("/") + path

def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj:
        return None
    if isinstance(obj, (str, bytes)):
        s = obj.decode() if isinstance(obj, bytes) else obj
        for pat in (
            r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)",
            r"[?&]ref=([\w\-:]+)",
            r"(\d{6,}-\d{9,})",
        ):
            m = re.search(pat, s)
            if m and _looks_like_ref(m.group(1)):
                return m.group(1)
        return None
    if isinstance(obj, dict):
        for k in ("orderNumber", "merchantOrderNumber", "merchantOrderNo", "ref", "reference", "customerRef", "customerReference"):
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
    if not ref:
        log.warning("[payhook] no-ref; keys=%s", list(data.keys())[:8])
        return web.json_response({"ok": False, "error": "no-ref"})
    activated = payments_mark_paid_by_ref(ref, raw=data)
    log.info("[payhook] ref=%s -> activated=%s", ref, activated)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)})

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)):
        return
    async def _make_app():
        app = web.Application()
        async def _ok(_): return web.json_response({"ok": True})
        async def _health(_): return web.json_response({"ok": True, "service": "bot", "ts": int(time.time())})
        app.router.add_get("/", _ok)
        app.router.add_get("/health", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            app.router.add_get("/payhook", _ok)
        return app
    def _thread():
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
    threading.Thread(target=_thread, daemon=True).start()

_run_http_server()

# ========= DB =========
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None:
        return conn
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
    log.info("[db] using %s", DB_PATH)
    return conn

def _has_table(name: str) -> bool:
    c = _db().cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return c.fetchone() is not None

def _table_cols(name: str) -> set:
    c = _db().cursor()
    try:
        c.execute(f"PRAGMA table_info({name});")
        return {r["name"] for r in c.fetchall()}
    except Exception:
        return set()

def migrate_db():
    with _conn_lock:
        # users
        if not _has_table("users"):
            _db().execute("""
            CREATE TABLE users (
              id TEXT PRIMARY KEY,
              premium INTEGER DEFAULT 0,
              verified_ok INTEGER DEFAULT 0,
              verified_at INTEGER DEFAULT 0,
              vip_forever INTEGER DEFAULT 0,
              vip_since INTEGER DEFAULT 0,
              pref_lang TEXT DEFAULT 'ar'
            );""")
        else:
            cols = _table_cols("users")
            changes = []
            if "id" not in cols:
                log.warning("[db-migrate] users table missing 'id'; rebuilding table with correct schema")
                _db().execute("ALTER TABLE users RENAME TO users_old;")
                _db().execute("""
                CREATE TABLE users (
                  id TEXT PRIMARY KEY,
                  premium INTEGER DEFAULT 0,
                  verified_ok INTEGER DEFAULT 0,
                  verified_at INTEGER DEFAULT 0,
                  vip_forever INTEGER DEFAULT 0,
                  vip_since INTEGER DEFAULT 0,
                  pref_lang TEXT DEFAULT 'ar'
                );""")
                try:
                    _db().execute("""
                    INSERT INTO users (id,premium,verified_ok,verified_at,vip_forever,vip_since,pref_lang)
                    SELECT id,premium,COALESCE(verified_ok,0),COALESCE(verified_at,0),COALESCE(vip_forever,0),COALESCE(vip_since,0),COALESCE(pref_lang,'ar')
                    FROM users_old;
                    """)
                except Exception:
                    pass
                _db().execute("DROP TABLE IF EXISTS users_old;")
            else:
                if "verified_ok" not in cols: changes.append("ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;")
                if "verified_at" not in cols: changes.append("ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;")
                if "vip_forever" not in cols: changes.append("ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0;")
                if "vip_since" not in cols: changes.append("ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;")
                if "pref_lang" not in cols: changes.append("ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar';")
                for q in changes:
                    _db().execute(q)

        # ai_state
        if not _has_table("ai_state"):
            _db().execute("""
            CREATE TABLE ai_state (
              user_id TEXT PRIMARY KEY,
              mode TEXT,
              extra TEXT,
              updated_at INTEGER
            );""")
        else:
            cols = _table_cols("ai_state")
            if "extra" not in cols:
                _db().execute("ALTER TABLE ai_state ADD COLUMN extra TEXT;")
            if "updated_at" not in cols:
                _db().execute("ALTER TABLE ai_state ADD COLUMN updated_at INTEGER;")

        # payments
        if not _has_table("payments"):
            _db().execute("""
            CREATE TABLE payments (
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

# ========= Utils / Net =========
def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except Exception: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except Exception: pass

_member_cache = {}

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now:
            return cached[0]
    targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
    for attempt in range(1, retries + 1):
        for target in targets:
            try:
                cm = await context.bot.get_chat_member(target, user_id)
                ok = getattr(cm, "status", None) in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id] = (True, now + 60); user_set_verify(user_id, True); return True
            except Exception as e:
                log.warning("[is_member] try#%d %s -> %s", attempt, target, e)
        if attempt < retries: await asyncio.sleep(backoff * attempt)
    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False); return False

async def must_be_member_or_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID:
        return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

_URL_RE = re.compile(r"https?://[^\s]+")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_IP_RE   = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
DISPOSABLE_DOMAINS = {"mailinator.com","tempmail.com","10minutemail.com","yopmail.com","guerrillamail.com","trashmail.com"}

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

def fmt_geo(data: dict, lang="ar") -> str:
    if not data: return "⚠️ تعذّر جلب البيانات." if lang=="ar" else "⚠️ Failed to fetch."
    if data.get("error"): return f"⚠️ {data['error']}"
    if lang=="ar":
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
        parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
        return "\n".join(parts)
    else:
        parts = [
            f"🔎 Query: <code>{data.get('query','')}</code>",
            f"🌍 Country/Region: {data.get('country','?')} — {data.get('regionName','?')}",
            f"🏙️ City/ZIP: {data.get('city','?')} — {data.get('zip','-')}",
            f"⏰ Timezone: {data.get('timezone','-')}",
            f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}",
            f"🛰️ AS: {data.get('as','-')}",
            f"📍 Coords: {data.get('lat','?')}, {data.get('lon','?')}",
        ]
        if data.get("reverse"): parts.append(f"🔁 Reverse: {data['reverse']}")
        parts.append("\nℹ️ Use this info for lawful purposes only.")
        return "\n".join(parts)

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

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

async def osint_email(email: str, lang="ar") -> str:
    if not is_valid_email(email): 
        return "⚠️ صيغة الإيميل غير صحيحة." if lang=="ar" else "⚠️ Invalid email."
    local, domain = email.split("@", 1)
    # MX
    mx_txt = "❓"
    if dnsresolver:
        try:
            answers = dnsresolver.resolve(domain, "MX")
            mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
            mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else ("لا يوجد" if lang=="ar" else "None")
        except Exception:
            mx_txt = "لا يوجد" if lang=="ar" else "None"
    else:
        mx_txt = "dnspython غير مثبت" if lang=="ar" else "dnspython not installed"

    # Gravatar
    g_url = f"https://www.gravatar.com/avatar/{md5_hex(email)}?d=404"
    g_st = await http_head(g_url)
    grav = "✅ موجود" if (g_st and 200 <= g_st < 300) else "❌ غير موجود"
    if lang=="en":
        grav = "✅ Exists" if (g_st and 200 <= g_st < 300) else "❌ Not found"

    # Resolve domain & geo
    ip = resolve_ip(domain)
    geo_text = ""
    if ip:
        data = await fetch_geo(ip)
        geo_text = fmt_geo(data, lang)
    else:
        geo_text = "⚠️ تعذّر حلّ IP للدومين." if lang=="ar" else "⚠️ Could not resolve domain IP."

    # WHOIS
    w = whois_domain(domain)
    if w and not w.get("error"):
        w_txt = f"WHOIS:\n- Registrar: {w.get('registrar')}\n- Created: {w.get('creation_date')}\n- Expires: {w.get('expiration_date')}"
    else:
        w_txt = f"WHOIS: {w.get('error')}" if w and w.get("error") else "WHOIS: N/A"

    if lang=="ar":
        out = [
            f"📧 الإيميل: <code>{email}</code>",
            f"📮 MX: {mx_txt}",
            f"🖼️ Gravatar: {grav}",
            w_txt,
            f"\n{geo_text}"
        ]
    else:
        out = [
            f"📧 Email: <code>{email}</code>",
            f"📮 MX: {mx_txt}",
            f"🖼️ Gravatar: {grav}",
            w_txt,
            f"\n{geo_text}"
        ]
    return "\n".join(out)

async def osint_username(name: str, lang="ar") -> str:
    uname = re.sub(r"[^\w\-.]+", "", name.strip())
    if not uname or len(uname) < 3:
        return "⚠️ أدخل اسم/يوزر صالح (٣ أحرف على الأقل)." if lang=="ar" else "⚠️ Enter a valid username (≥3)."
    gh_line = "GitHub: لم يتم الفحص" if lang=="ar" else "GitHub: not checked"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/users/{uname}", timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    if lang=="ar":
                        gh_line = f"GitHub: ✅ موجود — public_repos={data.get('public_repos')}, منذ {data.get('created_at')}"
                    else:
                        gh_line = f"GitHub: ✅ exists — public_repos={data.get('public_repos')}, since {data.get('created_at')}"
                elif r.status == 404:
                    gh_line = "GitHub: ❌ غير موجود" if lang=="ar" else "GitHub: ❌ not found"
                else:
                    gh_line = f"GitHub: status {r.status}"
    except Exception as e:
        gh_line = f"GitHub: network error ({e})"
    if lang=="ar":
        return f"👤 البحث عن: <code>{uname}</code>\n{gh_line}\n\nℹ️ فحوص إضافية ممكن لاحقًا."
    else:
        return f"👤 Lookup: <code>{uname}</code>\n{gh_line}\n\nℹ️ More probes can be added later."

def classify_url(u: str) -> dict:
    try:
        p = re.match(r"^(https?)://([^/]+)(/[^?]*)?(\?.*)?$", u)
        if not p:
            from urllib.parse import urlparse
            qp = urlparse(u)
            return {"ok": True, "scheme": qp.scheme, "host": qp.hostname, "path": qp.path, "q": qp.query}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def link_scan(u: str, lang="ar") -> str:
    if not _URL_RE.search(u or ""):
        return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    meta = classify_url(u)
    if not meta.get("ok"):
        return f"⚠️ رابط غير صالح: {meta.get('error')}"
    from urllib.parse import urlparse
    p = urlparse(u); host = p.hostname or ""; scheme = p.scheme
    issues = []
    if scheme != "https":
        issues.append("❗️ بدون تشفير HTTPS" if lang=="ar" else "❗️ Not HTTPS")
    ip = resolve_ip(host) if host else None
    geo_txt = ""
    if ip:
        data = await fetch_geo(ip)
        geo_txt = fmt_geo(data, lang)
    else:
        geo_txt = "⚠️ تعذّر حلّ IP للمضيف." if lang=="ar" else "⚠️ Could not resolve host IP."
    status = await http_head(u)
    if status is None:
        issues.append("⚠️ فشل الوصول (HEAD)" if lang=="ar" else "⚠️ HEAD request failed")
    else:
        issues.append(f"🔎 HTTP: {status}")
    prefix = "🔗 الرابط" if lang=="ar" else "🔗 URL"
    hosttxt = "المضيف" if lang=="ar" else "Host"
    return f"{prefix}: <code>{u}</code>\n{hosttxt}: <code>{host}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}"

async def email_check(e: str, lang="ar") -> str:
    ok = is_valid_email(e)
    if not ok: return "❌ الإيميل غير صالح." if lang=="ar" else "❌ Invalid email."
    dom = e.split("@",1)[1].lower()
    disp = "✅ ليس مؤقت" if dom not in DISPOSABLE_DOMAINS else "❌ مؤقت"
    if lang=="en": disp = "✅ Not disposable" if dom not in DISPOSABLE_DOMAINS else "❌ Disposable"
    mx = "❓"
    if dnsresolver:
        try:
            ans = dnsresolver.resolve(dom, "MX")
            mx = "✅ موجود" if len(ans) else "❌ غير موجود"
            if lang=="en": mx = "✅ present" if len(ans) else "❌ missing"
        except Exception:
            mx = "❌ غير موجود" if lang=="ar" else "❌ missing"
    else:
        mx = "ℹ️ تحتاج dnspython" if lang=="ar" else "ℹ️ dnspython needed"
    head = "📧" if lang=="ar" else "📧"
    return f"{head} {e}\nMX: {mx}\nDisposable: {disp}"

# ========= AI =========
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = [m for m in [primary, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"] if m]
    last_err = None
    for model in fallbacks:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.7, timeout=60)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg: return None, "quota"
            if "api key" in msg.lower(): return None, "apikey"
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str, lang="ar") -> str:
    if not AI_ENABLED or client is None:
        return "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً." if lang=="ar" else "🧠 AI is disabled."
    sys_ar = "أجب بالعربية بإيجاز ووضوح."
    sys_en = "Reply in concise English."
    sysmsg = sys_ar if lang=="ar" else sys_en
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err == "quota": return "⚠️ نفاد الرصيد." if lang=="ar" else "⚠️ Out of quota."
    if err: return "⚠️ تعذّر التنفيذ حالياً." if lang=="ar" else "⚠️ Failed right now."
    return (r.choices[0].message.content or "").strip()

async def tts_whisper_from_file(filepath: str, lang="ar") -> str:
    if not AI_ENABLED or client is None:
        return "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً." if lang=="ar" else "🧠 AI is disabled."
    try:
        with open(filepath, "rb") as f:
            resp = client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or ("⚠️ لم أستطع استخراج النص." if lang=="ar" else "⚠️ Could not transcribe.")
    except Exception:
        return "⚠️ جرّب إرسال الملف بصيغة mp3/m4a/wav." if lang=="ar" else "⚠️ Try mp3/m4a/wav."

async def translate_text(text: str, src_to: str="ar_en"):
    # src_to: "ar_en" أو "en_ar"
    if not AI_ENABLED or client is None:
        return "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً."
    if src_to == "ar_en":
        prompt = f"Translate the following Arabic text into clear English:\n\n{text}"
        sysmsg = "High-quality translator."
    else:
        prompt = f"ترجم النص التالي إلى العربية بأسلوب واضح ومفهوم:\n\n{text}"
        sysmsg = "مترجم عالي الجودة."
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content": prompt}])
    if err: return "⚠️ تعذّر الترجمة حالياً."
    return (r.choices[0].message.content or "").strip()

async def translate_image_file(path: str, target_lang: str="ar"):
    if not (AI_ENABLED and client and OPENAI_VISION):
        return "⚠️ ترجمة الصور تتطلب تمكين OPENAI_VISION=1."
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    content = [{"role":"user","content":[
        {"type":"input_text","text": f"Extract text from the image and translate it into {target_lang}. Return only the translation."},
        {"type":"input_image","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
    ]}]
    r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=content, temperature=0)
    return (r.choices[0].message.content or "").strip()

async def ai_write(prompt: str, lang="ar") -> str:
    sysmsg = "اكتب نصًا عربيًا إعلانيًا جذابًا ومختصرًا، بعناوين قصيرة وCTA واضح." if lang=="ar" else "Write a concise, catchy ad copy in English with clear CTA."
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err: return "⚠️ تعذّر التوليد حالياً." if lang=="ar" else "⚠️ Generation failed."
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

# ========= Media DL =========
async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp غير مثبت")
        return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).70s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo[filesize<46M]+bestaudio/best[filesize<46M]/best",
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
                    if p.stat().st_size <= MAX_UPLOAD_BYTES:
                        return p
                    # جرّب صوت فقط
                    y2_opts = {**ydl_opts, "format": "bestaudio[filesize<46M]/bestaudio", "merge_output_format":"m4a"}
                    with yt_dlp.YoutubeDL(y2_opts) as y2:
                        info2 = y2.extract_info(url, download=True)
                        fname2 = y2.prepare_filename(info2)
                        for ext2 in (".m4a",".mp3",".webm"):
                            p2 = Path(os.path.splitext(fname2)[0] + ext2)
                            if p2.exists() and p2.is_file() and p2.stat().st_size <= MAX_UPLOAD_BYTES:
                                return p2
                    return None
    except Exception as e:
        log.error("[yt-dlp] %s", e)
        return None
    return None

# ========= i18n =========
I18N = {
    "ar": {
        "welcome": "مرحباً بك في بوت فيربوكس 🔥\nكل الأدوات هنا تتم داخل تيليجرام:\n• أدوات الذكاء الاصطناعي\n• أمن وحماية وفحص\n• تحميل وسائط بدقة عالية\n• تحويل ملفات وصور\n• أقسام دورات وروابط رسمية لفك الباند\n\nالمحتوى المجاني متاح، وميزات VIP أقوى (مدى الحياة).",
        "btn_sections": "📂 الأقسام",
        "btn_contact": "📨 تواصل مع الإدارة",
        "btn_lang": "🌐 تغيير اللغة",
        "btn_info": "👤 معلوماتي",
        "btn_upgrade": "⚡ ترقية إلى VIP",
        "btn_vip": "⭐ حسابك VIP",
        "btn_back": "↩️ رجوع",
        "gate_join": "📣 الانضمام للقناة",
        "gate_check": "✅ تحقّق من القناة",
        "gate_need": "🔐 انضم للقناة لاستخدام البوت:",
        "need_admin_text": "⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في القناة.",
        "menu_title": "👇 القائمة الرئيسية:",
        "sections_title": "📂 الأقسام الرئيسية:",
        "only_start_help": "استخدم /start أو /help.",
        "vip_lifetime_since": "⭐ حسابك VIP (مدى الحياة)\nمنذ: {since}",
        "lang_set": "✅ تم تغيير اللغة إلى: {lang}",
        "paid_done": "🎉 تم تفعيل VIP (مدى الحياة) على حسابك.",
        "pay_wait": "⌛ لم يصل إشعار الدفع بعد.",
        "create_invoice": "⏳ جاري إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>",
        "upgrade_text": "💳 ترقية إلى VIP مدى الحياة ({price:.2f} SAR)\nسيتم التفعيل تلقائيًا بعد الدفع.\n🔖 مرجعك: <code>{ref}</code>",
        "open_pay": "🚀 الذهاب للدفع",
        "verify_pay": "✅ تحقّق الدفع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
        "send_valid_url": "أرسل رابط صالح يبدأ بـ http/https.",
        "file_too_big_or_failed": "⚠️ تعذّر التحميل أو الملف كبير جداً.",
        "added_image": "✅ تم إضافة صورة ({n}). أرسل /makepdf للإخراج أو أرسل صورًا إضافية.",
        "pdf_fail": "⚠️ فشل إنشاء PDF أو الحجم كبير.",
        "img_compress_fail": "⚠️ فشل ضغط الصورة.",
        "choose_file_tool": "🗜️ اختر أداة الملفات:",
        "ai_chat_on": "🤖 وضع الدردشة مفعّل. أرسل سؤالك الآن.",
        "ai_chat_off": "🔚 تم إنهاء وضع الذكاء الاصطناعي.",
        "main_cats": {
            "cat_ai": "🤖 أدوات الذكاء الاصطناعي",
            "cat_security": "🛡️ أمن وحماية",
            "cat_media": "⬇️ تحميل وسائط",
            "cat_files": "🗂️ تحويل ملفات",
            "cat_services": "🧰 خدمات",
            "cat_courses": "📚 دورات",
            "cat_unban": "🔓 فك الحظر (روابط رسمية)"
        },
        "ai_tools": {
            "ai_chat": "🧠 دردشة AI",
            "ai_writer": "✍️ كاتب إعلاني",
            "ai_stt": "🎙️ تحويل الصوت إلى نص",
            "ai_tti": "🖼️ نص → صورة",
            "ai_translate": "🌐 مترجم فوري (AR ↔ EN)",
            "tr_dir": "اتجاه: {dir}",
            "dir_ar_en": "AR → EN",
            "dir_en_ar": "EN → AR",
            "dir_switch": "🔁 تبديل الاتجاه"
        },
        "security_tools": {
            "ip_lookup": "🛰️ IP Lookup",
            "link_scan": "🛡️ فحص الروابط (VIP)",
            "email_check": "✉️ Email Checker (VIP)",
            "osint": "🔎 OSINT (اسم/إيميل) (VIP)"
        },
        "media_tools": {
            "dl": "🎬 تنزيل فيديو/صوت"
        },
        "file_tools": {
            "img2pdf": "🖼️ صورة → PDF",
            "compress": "🗜️ تصغير صورة"
        },
        "services_tools": {
            "temp_numbers": "☎️ أرقام مؤقتة (VIP)",
            "dev_test_cards": "💳 بطاقات اختبار للمطورين (تعليمي)"
        },
        "courses_tools": {
            "py_zero": "🐍 بايثون من الصفر",
            "more": "روابط دورات (قابلة للتعديل)"
        },
        "unban_tools": {
            "unban_ig": "📷 Instagram Support",
            "unban_fb": "📘 Facebook Support",
            "unban_tg": "✈️ Telegram Support",
            "unban_epic": "🎮 Epic Games Support"
        },
        "vip_only": "🔒 هذه الأداة خاصة بمشتركي VIP.",
        "send_ip_or_domain": "📍 أرسل IP أو دومين الآن…",
        "send_username_or_email": "🔎 أرسل اسم/يوزر أو إيميل للفحص.",
        "send_text_for_writer": "✍️ اكتب وصفًا قصيرًا للنص المطلوب.",
        "send_voice_or_audio": "🎙️ أرسل Voice أو ملف صوت.",
        "send_text_or_image_for_tr": "🌐 أرسل نصًا أو صورة للترجمة.",
        "send_url_to_dl": "⬇️ أرسل رابط فيديو/صوت.",
        "send_email_to_check": "✉️ أرسل الإيميل لفحصه.",
        "numbers_hint": "☎️ أرسل اسم الخدمة (Telegram/WhatsApp..).",
        "img_desc": "🖼️ اكتب وصف الصورة المراد توليدها.",
        "back_to_sections": "📂 رجوع للأقسام"
    },
    "en": {
        "welcome": "Welcome to Ferpoks Bot 🔥\nEverything happens inside Telegram:\n• AI tools\n• Security & checks\n• High-quality media downloads\n• File & image tools\n• Courses and official unban links\n\nFree features available; VIP is lifetime.",
        "btn_sections": "📂 Sections",
        "btn_contact": "📨 Contact Admin",
        "btn_lang": "🌐 Change language",
        "btn_info": "👤 My info",
        "btn_upgrade": "⚡ Upgrade to VIP",
        "btn_vip": "⭐ Your VIP",
        "btn_back": "↩️ Back",
        "gate_join": "📣 Join the channel",
        "gate_check": "✅ Verify subscription",
        "gate_need": "🔐 Join the channel to use the bot:",
        "need_admin_text": "⚠️ If verify fails, make sure the bot is Admin in the channel.",
        "menu_title": "👇 Main menu:",
        "sections_title": "📂 Main sections:",
        "only_start_help": "Use /start or /help.",
        "vip_lifetime_since": "⭐ Your VIP (lifetime)\nSince: {since}",
        "lang_set": "✅ Language set to: {lang}",
        "paid_done": "🎉 VIP (lifetime) activated.",
        "pay_wait": "⌛ Payment not confirmed yet.",
        "create_invoice": "⏳ Creating payment link…\n🔖 Ref: <code>{ref}</code>",
        "upgrade_text": "💳 Upgrade to VIP lifetime ({price:.2f} SAR)\nActivation will be automatic.\n🔖 Ref: <code>{ref}</code>",
        "open_pay": "🚀 Open payment",
        "verify_pay": "✅ Verify payment",
        "ai_disabled": "🧠 AI feature is disabled.",
        "send_valid_url": "Send a valid http/https URL.",
        "file_too_big_or_failed": "⚠️ Failed or file too big.",
        "added_image": "✅ Image added ({n}). Send /makepdf or add more.",
        "pdf_fail": "⚠️ PDF failed or too big.",
        "img_compress_fail": "⚠️ Image compression failed.",
        "choose_file_tool": "🗜️ Choose a file tool:",
        "ai_chat_on": "🤖 Chat mode ON. Send your question.",
        "ai_chat_off": "🔚 AI chat stopped.",
        "main_cats": {
            "cat_ai": "🤖 AI Tools",
            "cat_security": "🛡️ Security",
            "cat_media": "⬇️ Media Download",
            "cat_files": "🗂️ File Tools",
            "cat_services": "🧰 Services",
            "cat_courses": "📚 Courses",
            "cat_unban": "🔓 Unban (Official)"
        },
        "ai_tools": {
            "ai_chat": "🧠 AI Chat",
            "ai_writer": "✍️ Ad Writer",
            "ai_stt": "🎙️ Speech → Text",
            "ai_tti": "🖼️ Text → Image",
            "ai_translate": "🌐 Translator (AR ↔ EN)",
            "tr_dir": "Direction: {dir}",
            "dir_ar_en": "AR → EN",
            "dir_en_ar": "EN → AR",
            "dir_switch": "🔁 Switch"
        },
        "security_tools": {
            "ip_lookup": "🛰️ IP Lookup",
            "link_scan": "🛡️ Link Scan (VIP)",
            "email_check": "✉️ Email Checker (VIP)",
            "osint": "🔎 OSINT (name/email) (VIP)"
        },
        "media_tools": {
            "dl": "🎬 Download Video/Audio"
        },
        "file_tools": {
            "img2pdf": "🖼️ Image → PDF",
            "compress": "🗜️ Compress Image"
        },
        "services_tools": {
            "temp_numbers": "☎️ Temporary Numbers (VIP)",
            "dev_test_cards": "💳 Developer Test Cards (educational)"
        },
        "courses_tools": {
            "py_zero": "🐍 Python from Zero",
            "more": "More courses (editable)"
        },
        "unban_tools": {
            "unban_ig": "📷 Instagram Support",
            "unban_fb": "📘 Facebook Support",
            "unban_tg": "✈️ Telegram Support",
            "unban_epic": "🎮 Epic Games Support"
        },
        "vip_only": "🔒 VIP only.",
        "send_ip_or_domain": "📍 Send IP or domain…",
        "send_username_or_email": "🔎 Send username/email for OSINT.",
        "send_text_for_writer": "✍️ Send a short brief.",
        "send_voice_or_audio": "🎙️ Send Voice or audio file.",
        "send_text_or_image_for_tr": "🌐 Send text or image to translate.",
        "send_url_to_dl": "⬇️ Send a video/audio URL.",
        "send_email_to_check": "✉️ Send the email to check.",
        "numbers_hint": "☎️ Send the service name (Telegram/WhatsApp..).",
        "img_desc": "🖼️ Send a text description to generate an image.",
        "back_to_sections": "📂 Back to sections"
    }
}

def T(uid: int, key: str) -> str:
    lang = user_get(uid).get("pref_lang","ar")
    return I18N.get(lang, I18N["ar"]).get(key, key)

def L(uid: int) -> dict:
    lang = user_get(uid).get("pref_lang","ar")
    return I18N.get(lang, I18N["ar"])

# ========= Keyboards =========
def gate_kb(uid:int):
    l = L(uid)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l["gate_join"], url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(l["gate_check"], callback_data="verify")]
    ])

def main_menu_kb(uid: int):
    l = L(uid)
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = []
    rows.append([InlineKeyboardButton(l["btn_sections"], callback_data="sections")])
    rows.append([InlineKeyboardButton(l["btn_info"], callback_data="myinfo"),
                 InlineKeyboardButton(l["btn_lang"], callback_data="lang")])
    rows.append([InlineKeyboardButton(l["btn_contact"], url=admin_button_url())])
    if is_vip:
        rows.append([InlineKeyboardButton(l["btn_vip"], callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton(l["btn_upgrade"], callback_data="upgrade")])
    return InlineKeyboardMarkup(rows)

def sections_kb(uid:int):
    m = L(uid)["main_cats"]
    rows = [
        [InlineKeyboardButton(m["cat_ai"], callback_data="cat_ai")],
        [InlineKeyboardButton(m["cat_security"], callback_data="cat_security")],
        [InlineKeyboardButton(m["cat_media"], callback_data="cat_media")],
        [InlineKeyboardButton(m["cat_files"], callback_data="cat_files")],
        [InlineKeyboardButton(m["cat_services"], callback_data="cat_services")],
        [InlineKeyboardButton(m["cat_courses"], callback_data="cat_courses")],
        [InlineKeyboardButton(m["cat_unban"], callback_data="cat_unban")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="back_home")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_ai_kb(uid:int):
    a = L(uid)["ai_tools"]
    rows = [
        [InlineKeyboardButton(a["ai_chat"], callback_data="tool_ai_chat")],
        [InlineKeyboardButton(a["ai_writer"], callback_data="tool_ai_writer")],
        [InlineKeyboardButton(a["ai_stt"], callback_data="tool_ai_stt")],
        [InlineKeyboardButton(a["ai_tti"], callback_data="tool_ai_tti")],
        [InlineKeyboardButton(a["ai_translate"], callback_data="tool_ai_translate")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def tr_dir_kb(uid:int, dir_code:str):
    a = L(uid)["ai_tools"]
    dir_text = a["dir_ar_en"] if dir_code=="ar_en" else a["dir_en_ar"]
    rows = [
        [InlineKeyboardButton(a["dir_switch"], callback_data=f"tr_switch_{dir_code}")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")]
    ]
    return InlineKeyboardMarkup(rows), a["tr_dir"].format(dir=dir_text)

def cat_security_kb(uid:int):
    s = L(uid)["security_tools"]
    rows = [
        [InlineKeyboardButton(s["ip_lookup"], callback_data="tool_ip_lookup")],
        [InlineKeyboardButton(s["link_scan"], callback_data="tool_link_scan")],
        [InlineKeyboardButton(s["email_check"], callback_data="tool_email_check")],
        [InlineKeyboardButton(s["osint"], callback_data="tool_osint")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_media_kb(uid:int):
    m = L(uid)["media_tools"]
    rows = [
        [InlineKeyboardButton(m["dl"], callback_data="tool_media_dl")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_files_kb(uid:int):
    f = L(uid)["file_tools"]
    rows = [
        [InlineKeyboardButton(f["img2pdf"], callback_data="tool_img2pdf")],
        [InlineKeyboardButton(f["compress"], callback_data="tool_compress")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_services_kb(uid:int):
    s = L(uid)["services_tools"]
    rows = [
        [InlineKeyboardButton(s["temp_numbers"], callback_data="tool_numbers")],
        [InlineKeyboardButton(s["dev_test_cards"], callback_data="tool_dev_cards")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_courses_kb(uid:int):
    c = L(uid)["courses_tools"]
    rows = [
        [InlineKeyboardButton(c["py_zero"], callback_data="course_py_zero")],
        [InlineKeyboardButton(c["more"], callback_data="course_more")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def cat_unban_kb(uid:int):
    u = L(uid)["unban_tools"]
    # روابط رسمية (يمكنك تعديلها لاحقًا)
    rows = [
        [InlineKeyboardButton(u["unban_ig"], url="https://help.instagram.com/")],
        [InlineKeyboardButton(u["unban_fb"], url="https://www.facebook.com/help/")],
        [InlineKeyboardButton(u["unban_tg"], url="https://telegram.org/support")],
        [InlineKeyboardButton(u["unban_epic"], url="https://www.epicgames.com/help")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="sections")],
    ]
    return InlineKeyboardMarkup(rows)

def lang_kb(uid:int):
    u = user_get(uid)
    current = u.get("pref_lang","ar")
    rows = [
        [InlineKeyboardButton("العربية ✅" if current=="ar" else "العربية", callback_data="lang_ar"),
         InlineKeyboardButton("English ✅" if current=="en" else "English", callback_data="lang_en")],
        [InlineKeyboardButton(L(uid)["btn_back"], callback_data="back_home")]
    ]
    return InlineKeyboardMarkup(rows)

# ========= Safe edit =========
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("safe_edit error: %s", e)

# ========= Commands =========
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

    # فقط /start /help للكل
    try:
        await app.bot.set_my_commands(
            [BotCommand("start","Start"), BotCommand("help","Help")],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # أوامر المالك فقط
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("id","Your id"),
                BotCommand("grant","Grant VIP"),
                BotCommand("revoke","Revoke VIP"),
                BotCommand("vipinfo","VIP info"),
                BotCommand("refreshcmds","Refresh commands"),
                BotCommand("aidiag","AI diag"),
                BotCommand("libdiag","Lib diag"),
                BotCommand("paylist","Payments"),
                BotCommand("debugverify","Verify check"),
                BotCommand("restart","Restart")
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    u = user_get(uid)

    # رسالة ترحيب مختصرة حسب اللغة
    l = L(uid)
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=l["welcome"])
        else:
            await context.bot.send_message(chat_id, l["welcome"])
    except Exception:
        await context.bot.send_message(chat_id, l["welcome"])

    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        await context.bot.send_message(chat_id, l["gate_need"], reply_markup=gate_kb(uid))
        await context.bot.send_message(chat_id, l["need_admin_text"])
        return

    await context.bot.send_message(chat_id, l["menu_title"], reply_markup=main_menu_kb(uid))
    await context.bot.send_message(chat_id, l["sections_title"], reply_markup=sections_kb(uid))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(L(uid)["only_start_help"], reply_markup=main_menu_kb(uid))

# ========= Callback buttons =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    # verify subscription
    if q.data == "verify":
        ok = await is_member(context, uid, force=True)
        if ok:
            await safe_edit(q, L(uid)["menu_title"], main_menu_kb(uid))
            await q.message.reply_text(L(uid)["sections_title"], reply_markup=sections_kb(uid))
        else:
            await safe_edit(q, L(uid)["gate_need"], gate_kb(uid))
        return

    # gate
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, L(uid)["gate_need"], gate_kb(uid)); return

    data = q.data

    # Back/Home/Sections
    if data == "back_home":
        await safe_edit(q, L(uid)["menu_title"], main_menu_kb(uid)); return
    if data == "sections":
        await safe_edit(q, L(uid)["sections_title"], sections_kb(uid)); return

    # Language
    if data == "lang":
        await safe_edit(q, L(uid)["btn_lang"], lang_kb(uid)); return
    if data in ("lang_ar","lang_en"):
        prefs_set_lang(uid, "ar" if data=="lang_ar" else "en")
        await safe_edit(q, L(uid)["lang_set"].format(lang="العربية" if data=="lang_ar" else "English"), main_menu_kb(uid)); return

    # Info / VIP badge
    if data == "myinfo":
        u = user_get(uid)
        since = u.get("vip_since", 0)
        since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        is_vip = "✅" if user_is_premium(uid) else "❌"
        txt = f"👤 {q.from_user.full_name}\n🆔 {uid}\nVIP: {is_vip}\nLang: {u.get('pref_lang','ar').upper()}\nVIP since: {since_txt}"
        await safe_edit(q, txt, main_menu_kb(uid)); return

    if data == "vip_badge":
        u = user_get(uid)
        since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, L(uid)["vip_lifetime_since"].format(since=since_txt), main_menu_kb(uid)); return

    # Upgrade
    if data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, L(uid)["vip_lifetime_since"].format(since=time.strftime('%Y-%m-%d', time.gmtime(user_get(uid).get("vip_since",0)))), main_menu_kb(uid))
            return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, L(uid)["create_invoice"].format(ref=ref), InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="back_home")]]))
        try:
            if USE_PAYLINK_API and PAYLINK_API_ID and PAYLINK_API_SECRET:
                token = await paylink_auth_token()
                url = f"{PAYLINK_API_BASE}/addInvoice"
                body = {
                    "orderNumber": ref, "amount": VIP_PRICE_SAR, "clientName": q.from_user.full_name or "Telegram User",
                    "clientMobile": "0500000000", "currency": "SAR", "callBackUrl": _public_url("/payhook"),
                    "displayPending": False, "note": f"VIP Lifetime #{ref}",
                    "products":[{"title":"VIP Lifetime","price":VIP_PRICE_SAR,"qty":1,"isDigital":True}]
                }
                headers = {"Authorization": f"Bearer {token}"}
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=body, headers=headers, timeout=30) as r:
                        dataj = await r.json(content_type=None)
                        pay_url = dataj.get("url") or dataj.get("mobileUrl") or dataj.get("qrUrl") or _build_pay_link(ref)
            else:
                pay_url = _build_pay_link(ref)
            txt = L(uid)["upgrade_text"].format(price=VIP_PRICE_SAR, ref=ref)
            await safe_edit(q, txt, InlineKeyboardMarkup([
                [InlineKeyboardButton(L(uid)["open_pay"], url=pay_url)],
                [InlineKeyboardButton(L(uid)["verify_pay"], callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(L(uid)["btn_back"], callback_data="back_home")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "Failed to create payment link.", main_menu_kb(uid))
        return

    if data.startswith("verify_pay_"):
        ref = data.replace("verify_pay_","")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, L(uid)["paid_done"], main_menu_kb(uid))
        else:
            await safe_edit(q, L(uid)["pay_wait"], InlineKeyboardMarkup([
                [InlineKeyboardButton(L(uid)["verify_pay"], callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(L(uid)["btn_back"], callback_data="back_home")]
            ]))
        return

    # Categories
    if data == "cat_ai":
        await safe_edit(q, L(uid)["main_cats"]["cat_ai"], cat_ai_kb(uid)); return
    if data == "cat_security":
        await safe_edit(q, L(uid)["main_cats"]["cat_security"], cat_security_kb(uid)); return
    if data == "cat_media":
        await safe_edit(q, L(uid)["main_cats"]["cat_media"], cat_media_kb(uid)); return
    if data == "cat_files":
        await safe_edit(q, L(uid)["main_cats"]["cat_files"], cat_files_kb(uid)); return
    if data == "cat_services":
        await safe_edit(q, L(uid)["main_cats"]["cat_services"], cat_services_kb(uid)); return
    if data == "cat_courses":
        await safe_edit(q, L(uid)["main_cats"]["cat_courses"], cat_courses_kb(uid)); return
    if data == "cat_unban":
        await safe_edit(q, L(uid)["main_cats"]["cat_unban"], cat_unban_kb(uid)); return

    # AI tools
    if data == "tool_ai_chat":
        ai_set_mode(uid, "ai_chat", {})
        await safe_edit(q, L(uid)["ai_chat_on"], InlineKeyboardMarkup([
            [InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")],
            [InlineKeyboardButton("🔚", callback_data="ai_stop")]
        ])); return

    if data == "ai_stop":
        ai_set_mode(uid, None, {})
        await safe_edit(q, L(uid)["ai_chat_off"], cat_ai_kb(uid)); return

    if data == "tool_ai_writer":
        ai_set_mode(uid, "writer", {})
        await safe_edit(q, L(uid)["send_text_for_writer"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")]])); return

    if data == "tool_ai_stt":
        ai_set_mode(uid, "stt", {})
        await safe_edit(q, L(uid)["send_voice_or_audio"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")]])); return

    if data == "tool_ai_tti":
        ai_set_mode(uid, "image_ai", {})
        await safe_edit(q, L(uid)["img_desc"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")]])); return

    if data == "tool_ai_translate":
        # الافتراضي اتجاه AR->EN
        ai_set_mode(uid, "translate", {"dir":"ar_en"})
        kb, tr_line = tr_dir_kb(uid, "ar_en")
        await safe_edit(q, f"{L(uid)['send_text_or_image_for_tr']}\n{tr_line}", kb); return

    if data.startswith("tr_switch_"):
        cur = data.replace("tr_switch_","")
        new = "en_ar" if cur=="ar_en" else "ar_en"
        ai_set_mode(uid, "translate", {"dir":new})
        kb, tr_line = tr_dir_kb(uid, new)
        await safe_edit(q, f"{L(uid)['send_text_or_image_for_tr']}\n{tr_line}", kb); return

    # Security tools (VIP gates where needed)
    if data == "tool_ip_lookup":
        ai_set_mode(uid, "geo_ip", {})
        await safe_edit(q, L(uid)["send_ip_or_domain"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_security")]])); return

    if data == "tool_link_scan":
        if not user_is_premium(uid) and uid != OWNER_ID:
            await safe_edit(q, L(uid)["vip_only"], cat_security_kb(uid)); return
        ai_set_mode(uid, "link_scan", {})
        await safe_edit(q, L(uid)["send_url_to_dl"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_security")]])); return

    if data == "tool_email_check":
        if not user_is_premium(uid) and uid != OWNER_ID:
            await safe_edit(q, L(uid)["vip_only"], cat_security_kb(uid)); return
        ai_set_mode(uid, "email_check", {})
        await safe_edit(q, L(uid)["send_email_to_check"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_security")]])); return

    if data == "tool_osint":
        if not user_is_premium(uid) and uid != OWNER_ID:
            await safe_edit(q, L(uid)["vip_only"], cat_security_kb(uid)); return
        ai_set_mode(uid, "osint", {})
        await safe_edit(q, L(uid)["send_username_or_email"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_security")]])); return

    # Media
    if data == "tool_media_dl":
        ai_set_mode(uid, "media_dl", {})
        await safe_edit(q, L(uid)["send_url_to_dl"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_media")]])); return

    # Files
    if data == "tool_img2pdf":
        ai_set_mode(uid, "file_img_to_pdf", {"paths":[]})
        await safe_edit(q, "🖼️", InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_files")]]))
        await q.message.reply_text("🖼️ أرسل صورة واحدة أو أكثر وسأحوّلها إلى PDF.\nSend images, then /makepdf.")
        return

    if data == "tool_compress":
        ai_set_mode(uid, "file_img_compress", {})
        await safe_edit(q, L(uid)["choose_file_tool"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_files")]]))
        await q.message.reply_text("📷 أرسل صورة وسأرجّع نسخة مضغوطة.")
        return

    # Services
    if data == "tool_numbers":
        if not user_is_premium(uid) and uid != OWNER_ID:
            await safe_edit(q, L(uid)["vip_only"], cat_services_kb(uid)); return
        ai_set_mode(uid, "numbers", {})
        await safe_edit(q, L(uid)["numbers_hint"], InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_services")]])); return

    if data == "tool_dev_cards":
        # معلومات تعليمية (بطاقات اختبار Stripe مثلاً)
        txt = ("🔹 Educational test cards (Stripe):\n"
               "- Visa: 4242 4242 4242 4242 | Any future expiry | Any CVC | Any ZIP\n"
               "- 3D Secure (test): 4000 0027 6000 3184\n"
               "These are for development/testing only. Not valid for real purchases.")
        await safe_edit(q, txt, cat_services_kb(uid)); return

    # Courses (روابط قابلة للتعديل لاحقًا)
    if data == "course_py_zero":
        await safe_edit(q, "🐍 Python from Zero (edit the link in code later)", cat_courses_kb(uid)); return
    if data == "course_more":
        await safe_edit(q, "📚 Add more course links later from code.", cat_courses_kb(uid)); return

# ========= Media/Files helpers =========
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

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

# ========= Messages guard =========
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text(L(uid)["gate_need"], reply_markup=gate_kb(uid)); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # نص عام إذا لا يوجد وضع
    if not mode and msg and msg.text and msg.text.startswith("/"):
        return
    if not mode:
        await update.message.reply_text(L(uid)["menu_title"], reply_markup=main_menu_kb(uid))
        await update.message.reply_text(L(uid)["sections_title"], reply_markup=sections_kb(uid))
        return

    # TEXT
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()
        lang = user_get(uid).get("pref_lang","ar")

        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text, lang), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(L(uid)["btn_back"], callback_data="cat_ai")],[InlineKeyboardButton("🔚", callback_data="ai_stop")]]))
            return

        if mode == "writer":
            out = await ai_write(text, lang)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "translate":
            dir_code = (extra or {}).get("dir","ar_en")
            out = await translate_text(text, dir_code)
            await update.message.reply_text(out); return

        if mode == "geo_ip":
            target = text
            query = target
            if _HOST_RE.match(target):
                ip = resolve_ip(target)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data, lang), parse_mode="HTML"); return

        if mode == "link_scan":
            out = await link_scan(text, lang)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "email_check":
            out = await email_check(text, lang)
            await update.message.reply_text(out); return

        if mode == "osint":
            if "@" in text and "." in text:
                out = await osint_email(text, lang)
            else:
                out = await osint_username(text, lang)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "media_dl":
            if not _URL_RE.search(text):
                await update.message.reply_text(L(uid)["send_valid_url"]); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            path = await download_media(text)
            if path and path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
                try:
                    await update.message.reply_document(document=InputFile(str(path)))
                except Exception:
                    await update.message.reply_text(L(uid)["file_too_big_or_failed"])
            else:
                await update.message.reply_text(L(uid)["file_too_big_or_failed"])
            return

        if mode == "numbers":
            service = text[:50]
            if not FIVESIM_API_KEY:
                await update.message.reply_text("ℹ️ لم يتم ضبط FIVESIM_API_KEY. أضفه في .env لتفعيل الأرقام.", parse_mode="HTML"); return
            await update.message.reply_text(f"✅ طلبك قيد المعالجة لخدمة: {service}\n(اربط API فعلي لإتمام الاستلام)") 
            return

        if mode == "image_ai":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            img_bytes = await ai_image_generate(text)
            if img_bytes:
                bio = BytesIO(img_bytes); bio.name = "ai.png"
                await update.message.reply_photo(photo=InputFile(bio))
            else:
                await update.message.reply_text(L(uid)["ai_disabled"])
            return

        if mode in ("file_img_to_pdf","file_img_compress"):
            await update.message.reply_text(L(uid)["choose_file_tool"]); return

    # VOICE/AUDIO
    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p), user_get(uid).get("pref_lang","ar"))
            await update.message.reply_text(out)
            return

    # PHOTO
    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
        if mode == "translate" and OPENAI_VISION:
            dir_code = (extra or {}).get("dir","ar_en")
            target_lang = "en" if dir_code=="ar_en" else "ar"
            out = await translate_image_file(str(p), target_lang)
            await update.message.reply_text(out or "⚠️"); return
        if mode == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists():
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text(L(uid)["img_compress_fail"])
            return
        if mode == "file_img_to_pdf":
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(L(uid)["added_image"].format(n=len(st_paths)))
            return

    # DOCUMENT (صورة كمستند)
    if msg.document:
        if mode in ("file_img_to_pdf","file_img_compress"):
            p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
            if mode == "file_img_compress":
                outp = compress_image(p)
                if outp and outp.exists():
                    await update.message.reply_document(InputFile(str(outp)))
                else:
                    await update.message.reply_text(L(uid)["img_compress_fail"])
                return
            if mode == "file_img_to_pdf":
                st_paths = (extra or {}).get("paths", [])
                st_paths.append(str(p))
                ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
                await update.message.reply_text(L(uid)["added_image"].format(n=len(st_paths)))
                return

    await update.message.reply_text(L(uid)["menu_title"], reply_markup=main_menu_kb(uid))

# ========= Commands (owner/admin) =========
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ granted VIP to {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ revoked VIP from {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /vipinfo <user_id>"); return
    u = user_get(context.args[0]); await update.message.reply_text(json.dumps(u, ensure_ascii=False, indent=2))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("ok")

async def aidiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(p): 
            try: return version(p)
            except PackageNotFoundError: return "not-installed"
        k = (os.getenv("OPENAI_API_KEY") or "")
        msg = (f"AI_ENABLED={'ON' if AI_ENABLED else 'OFF'}\n"
               f"Key={'set' if k else 'missing'}\n"
               f"Model={OPENAI_CHAT_MODEL}\n"
               f"openai={v('openai')}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"aidiag error: {e}")

async def libdiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(p): 
            try: return version(p)
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
        await update.message.reply_text("no payments"); return
    lines = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        lines.append(f"ref={r['ref']} user={r['user_id']} {r['status']} at={ts}")
    await update.message.reply_text("\n".join(lines))

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True)
    await update.message.reply_text(f"member={ok}")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("restarting…")
    os._exit(0)

# ========= Files extra command =========
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("Use from: Sections → File Tools → Image → PDF"); return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("No images yet. Send photos first."); return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text(L(uid)["pdf_fail"])
    ai_set_mode(uid, "file_img_to_pdf", {"paths":[]})

# ========= Errors =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ========= Main =========
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())

    # الأوامر العامة فقط
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
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

# ========= Paylink helpers =========
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

if __name__ == "__main__":
    main()





