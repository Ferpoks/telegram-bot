# -*- coding: utf-8 -*-
"""
Bot: Ferpoks – Full-featured Telegram Bot (fixed)
Fixes:
- Restored sections
- Deep OSINT (name/email/domain/phone/username) – safe, text-only
- Translator works for text + images (OpenAI Vision)
- Image generator fixed
- Link scan fixed (DNS/SSL/IP + country/ASN)
- IP lookup fixed
- Email checker fixed
- Virtual numbers flow (requires VNUM_API_* env) – graceful fallback
- Image convert (compress/PDF) wired
- Social media download wired (yt-dlp present)
- Language switcher (Arabic/English) with /lang + button
- Routing bugs fixed (translate no longer opens geo)
"""
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, ssl, socket, tempfile, io
from pathlib import Path
from dotenv import load_dotenv

# ===== Logging =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ===== Optional OpenAI =====
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatMemberStatus, ChatAction
from telegram.error import BadRequest

# ===== Env =====
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")

def _ensure_parent(p: str):
    Path(p).parent.mkdir(parents=True, exist_ok=True)

# ===== OpenAI setup =====
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL  = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_STT_MODEL   = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")  # whisper-1 alternative
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(",")) if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"
CHANNEL_ID = None

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT  = "مرحباً بك في بوت فيربوكس 🔥 – كل الميزات تعمل داخل الدردشة مباشرة ✨"

# ===== Payments (Paylink) =====
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API","1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR","10"))

def _clean_base(u: str) -> str:
    u = (u or "").strip().strip('"').strip("'")
    if u.startswith("="): u = u.lstrip("=")
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE)
    if "{ref}" in base: return base.format(ref=ref)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}ref={ref}"

# ===== aiohttp server (health/webhook) =====
SERVE_HEALTH = os.getenv("SERVE_HEALTH","0") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web, ClientSession, ClientTimeout
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

AIOHTTP_SESSION = None
async def get_http_session():
    global AIOHTTP_SESSION
    if not AIOHTTP_AVAILABLE:
        return None
    if AIOHTTP_SESSION and not AIOHTTP_SESSION.closed:
        return AIOHTTP_SESSION
    AIOHTTP_SESSION = ClientSession(timeout=ClientTimeout(total=30))
    return AIOHTTP_SESSION

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or (f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()}" if os.getenv("RENDER_EXTERNAL_HOSTNAME") else "")
    return (base or "").rstrip("/") + path

def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj: return None
    if isinstance(obj,(str,bytes)):
        s = obj.decode() if isinstance(obj,bytes) else obj
        m = re.search(r"(?:orderNumber|merchantOrderNumber|merchantOrderNo|reference|customerRef|customerReference)\s*[:=]\s*['\"]?([\w\-:]+)", s)
        if m and _looks_like_ref(m.group(1)): return m.group(1)
        m = re.search(r"[?&]ref=([\w\-:]+)", s)
        if m and _looks_like_ref(m.group(1)): return m.group(1)
        m = re.search(r"(\d{6,}-\d{9,})", s)
        if m: return m.group(1)
        return None
    if isinstance(obj,dict):
        for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
            v = obj.get(k)
            if isinstance(v,str) and _looks_like_ref(v.strip()): return v.strip()
        for v in obj.values():
            got = _find_ref_in_obj(v)
            if got: return got
        return None
    if isinstance(obj,(list,tuple)):
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
        log.warning("[payhook] no-ref keys=%s", list(data.keys())[:6])
        return web.json_response({"ok": False, "error": "no-ref"}, status=200)
    activated = payments_mark_paid_by_ref(ref, raw=data)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)}, status=200)

def _run_http_server():
    if not (AIOHTTP_AVAILABLE and (SERVE_HEALTH or PAY_WEBHOOK_ENABLE)):
        return
    async def _make_app():
        app = web.Application()
        app.router.add_get("/favicon.ico", lambda _: web.Response(status=204))
        if SERVE_HEALTH:
            app.router.add_get("/", lambda _: web.json_response({"ok": True}))
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            app.router.add_get("/payhook", lambda _: web.json_response({"ok": True}))
        return app
    def _thread_main():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _start():
            app = await _make_app()
            runner = web.AppRunner(app); await runner.setup()
            port = int(os.getenv("PORT","10000"))
            site = web.TCPSite(runner, "0.0.0.0", port); await site.start()
            log.info("[http] up on 0.0.0.0:%d", port)
        loop.run_until_complete(_start())
        try: loop.run_forever()
        finally:
            loop.stop(); loop.close()
    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ===== Startup =====
async def on_startup(app: Application):
    init_db()
    if AIOHTTP_AVAILABLE: _ = await get_http_session()
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("[startup] delete_webhook: %s", e)

    global CHANNEL_ID
    CHANNEL_ID = None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id; break
        except Exception as e:
            log.warning("[startup] get_chat @%s -> %s", u, e)

    try:
        await app.bot.set_my_commands(
            [BotCommand("start","بدء"),
             BotCommand("help","مساعدة"),
             BotCommand("geo","تحديد موقع IP"),
             BotCommand("translate","مترجم فوري"),
             BotCommand("lang","تغيير اللغة")],
            scope=BotCommandScopeDefault())
    except Exception as e:
        log.warning("[startup] default cmds: %s", e)

    try:
        await app.bot.set_my_commands(
            [BotCommand("start","بدء"), BotCommand("help","مساعدة"),
             BotCommand("id","معرّفك"), BotCommand("grant","منح VIP"),
             BotCommand("revoke","سحب VIP"), BotCommand("vipinfo","معلومات VIP"),
             BotCommand("refreshcmds","تحديث الأوامر"),
             BotCommand("debugverify","تشخيص التحقق"),
             BotCommand("restart","إعادة تشغيل")],
            scope=BotCommandScopeChat(chat_id=OWNER_ID))
    except Exception as e:
        log.warning("[startup] owner cmds: %s", e)

# ===== Database =====
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn: return conn
    _ensure_parent(DB_PATH)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
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
          lang TEXT DEFAULT 'ar'
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS payments (
          ref TEXT PRIMARY KEY, user_id TEXT, amount REAL, provider TEXT,
          status TEXT, created_at INTEGER, paid_at INTEGER, raw TEXT
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY, mode TEXT, updated_at INTEGER
        );""")
        _db().commit()

def init_db(): migrate_db()

def user_get(uid) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?)", (uid,)); _db().commit()
            return {"id": uid, "premium":0, "verified_ok":0, "verified_at":0, "vip_forever":0, "vip_since":0, "lang":"ar"}
        d = dict(r)
        d.setdefault("lang","ar")
        return d

def user_set_lang(uid, lang):
    with _conn_lock:
        _db().execute("UPDATE users SET lang=? WHERE id=?", (lang, str(uid))); _db().commit()

def user_set_verify(uid, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?", (1 if ok else 0, int(time.time()), str(uid))); _db().commit()

def user_is_premium(uid) -> bool:
    u = user_get(uid); return bool(u.get("premium")) or bool(u.get("vip_forever"))

def user_grant(uid):
    now=int(time.time())
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0),?) WHERE id=?", (now,str(uid))); _db().commit()

def user_revoke(uid):
    with _conn_lock: _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE id=?", (str(uid),)); _db().commit()

def ai_set_mode(uid, mode):
    with _conn_lock:
        _db().execute("INSERT INTO ai_state (user_id,mode,updated_at) VALUES (?,?,strftime('%s','now')) "
                      "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=strftime('%s','now')",
                      (str(uid), mode))
        _db().commit()

def ai_get_mode(uid):
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
        r=c.fetchone(); return r["mode"] if r else None

# ===== Payments =====
def payments_new_ref(uid:int)->str: return f"{uid}-{int(time.time())}"

def payments_create(uid:int, amount:float, provider="paylink", ref=None)->str:
    ref = ref or payments_new_ref(uid)
    with _conn_lock:
        _db().execute("INSERT OR REPLACE INTO payments (ref,user_id,amount,provider,status,created_at) VALUES (?,?,?,?,?,?)",
                      (ref,str(uid),amount,provider,"pending",int(time.time())))
        _db().commit(); return ref

def payments_status(ref:str)->str|None:
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT status FROM payments WHERE ref=?", (ref,))
        r=c.fetchone(); return r["status"] if r else None

def payments_mark_paid_by_ref(ref:str, raw=None)->bool:
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT user_id,status FROM payments WHERE ref=?", (ref,))
        r=c.fetchone()
        if not r: return False
        if r["status"]=="paid":
            try: user_grant(r["user_id"])
            except: pass
            return True
        _db().execute("UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
                      (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref))
        _db().commit()
    try: user_grant(r["user_id"])
    except Exception as e: log.error("[payments_mark_paid] grant: %s", e)
    return True

# ===== UI Helpers =====
def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

def tr(k): return {
    "follow_btn":"📣 الانضمام للقناة",
    "check_btn":"✅ تحقّق من القناة",
    "access_denied":"⚠️ هذا القسم خاص بمشتركي VIP.",
    "back":"↩️ رجوع",
    "ai_disabled":"🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
}.get(k,k)

def bottom_menu_kb(uid:int):
    is_vip = (user_is_premium(uid) or uid==OWNER_ID)
    rows=[
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton(("⭐ VIP حسابك" if is_vip else "⚡ ترقية إلى VIP"), callback_data=("vip_badge" if is_vip else "upgrade"))],
        [InlineKeyboardButton("📂 الأقسام", callback_data="back_sections")],
        [InlineKeyboardButton("🌐 اللغة", callback_data="lang_menu")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())],
    ]
    return InlineKeyboardMarkup(rows)

def sections_list_kb():
    rows=[]
    for key in ("ai_tools","security_vip","services_misc","python_zero","classic_sections"):
        title = SECTIONS[key]["title"]; free = SECTIONS[key].get("is_free", False)
        lock = "🟢" if free else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {title}", callback_data=f"sec_{key}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def ai_tools_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 بحث ذكي (عميق)", callback_data="ai_osint")],
        [InlineKeyboardButton("✍️ مولد نصوص", callback_data="ai_writer")],
        [InlineKeyboardButton("🎙️ صوت → نص", callback_data="ai_stt")],
        [InlineKeyboardButton("🌐 مترجم (نص/صورة)", callback_data="ai_translate")],
        [InlineKeyboardButton("🖼️ صور AI", callback_data="ai_images")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def security_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 فحص رابط (VIP)", callback_data="sec_linkscan")],
        [InlineKeyboardButton("🛰️ IP Lookup", callback_data="sec_ip")],
        [InlineKeyboardButton("✉️ Email Checker (VIP)", callback_data="sec_email")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def services_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 أرقام مؤقتة (VIP)", callback_data="svc_vnum")],
        [InlineKeyboardButton("🗜️ صور → PDF/ضغط", callback_data="svc_convert")],
        [InlineKeyboardButton("⬇️ تنزيل وسائط", callback_data="svc_media")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def lang_kb(uid:int):
    cur = user_get(uid).get("lang","ar")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ العربية" if cur=="ar" else "العربية"), callback_data="lang_ar"),
         InlineKeyboardButton(("✅ English" if cur=="en" else "English"), callback_data="lang_en")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_home")]
    ])

def section_back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

# ===== Sections =====
SECTIONS = {
    "python_zero": {"title":"🐍 بايثون من الصفر (مجاني)","desc":"مقدمة وأساسيات بايثون.","is_free":True},
    "ai_tools":   {"title":"🤖 أقسام تقنية ذكية","desc":"OSINT عميق / مولد نصوص / مترجم / صوت→نص / صور AI","is_free":True},
    "security_vip":{"title":"🛡️ أمن وحماية (VIP)","desc":"Link Scan / IP Lookup / Email Checker","is_free":False},
    "services_misc":{"title":"🧰 خدمات فورية","desc":"أرقام مؤقتة / صور→PDF / تنزيل وسائط","is_free":True},
    # Restored legacy/Classic section (placeholder text only, no links to user)
    "classic_sections":{"title":"📚 أقسام كلاسيكية","desc":"الأقسام القديمة أُعيدت كنصوص داخلية بدون روابط مباشرة.","is_free":True},
}

# ===== Safe edit =====
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("safe_edit: %s", e)

# ===== Membership =====
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except: pass

_member_cache={}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id:int, force=False, retries=1, backoff=0.4)->bool:
    now=time.time()
    if not force:
        cached=_member_cache.get(user_id)
        if cached and cached[1]>now: return cached[0]
    targets=[CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
    async def _check(t):
        try: cm=await context.bot.get_chat_member(t,user_id); return getattr(cm,"status",None)
        except Exception as e: log.warning("[is_member] %s", e); return None
    ok=False
    for a in range(retries):
        statuses=await asyncio.gather(*[_check(t) for t in targets])
        if any(s in ALLOWED_STATUSES for s in statuses if s): ok=True; break
        if a<retries-1: await asyncio.sleep(backoff*(a+1))
    _member_cache[user_id]=(ok, now+(600 if ok else 120))
    user_set_verify(user_id, ok); return ok

async def must_be_member_or_vip(context, uid:int)->bool:
    return user_is_premium(uid) or uid==OWNER_ID or await is_member(context, uid)

# ===== AI helpers =====
def _chat(messages, temperature=0.4, max_tokens=None):
    if not AI_ENABLED or client is None: return None, "ai_disabled"
    models=[OPENAI_CHAT_MODEL, "gpt-4o-mini", "gpt-4o"]
    last=None
    for m in models:
        try:
            r=client.chat.completions.create(model=m, messages=messages, temperature=temperature, max_tokens=max_tokens)
            return r,None
        except Exception as e:
            last=str(e)
            if "api key" in last.lower(): return None,"apikey"
            if "quota" in last.lower(): return None,"quota"
    return None,last or "unknown"

def ai_reply(prompt, lang="ar")->str:
    if not AI_ENABLED or client is None: return tr("ai_disabled")
    sysmsg = "أجب بالعربية بإيجاز ووضوح." if lang=="ar" else "Answer in concise, clear English."
    r,err=_chat([{"role":"system","content":sysmsg},{"role":"user","content":prompt}], temperature=0.5)
    if err=="quota": return "⚠️ نفاد الرصيد." if lang=="ar" else "⚠️ Out of quota."
    if err=="apikey": return "⚠️ مفتاح API غير صالح." if lang=="ar" else "⚠️ Invalid API key."
    if not r: return "⚠️ تعذّر الرد." if lang=="ar" else "⚠️ Failed to respond."
    return (r.choices[0].message.content or "").strip()

# ===== Geo/IP & utilities =====
_IP_RE = re.compile(r"\b(?:(?:\d{1,3}\.){3}\d{1,3})\b")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")

async def fetch_geo(query: str) -> dict|None:
    if not AIOHTTP_AVAILABLE: return {"error":"aiohttp missing"}
    url=f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        s=await get_http_session()
        async with s.get(url) as r:
            data=await r.json(content_type=None)
            if data.get("status")!="success": return {"error": data.get("message","lookup failed")}
            return data
    except Exception as e:
        return {"error": f"network {e}"}

def fmt_geo(d:dict)->str:
    if not d: return "⚠️ لا بيانات."
    if d.get("error"): return f"⚠️ {d['error']}"
    parts=[
        f"🔎 الاستعلام: <code>{d.get('query','')}</code>",
        f"🌍 {d.get('country','?')} — {d.get('regionName','?')}",
        f"🏙️ {d.get('city','?')} — {d.get('zip','-')}",
        f"⏰ {d.get('timezone','-')}",
        f"📡 ISP/ORG: {d.get('isp','-')} / {d.get('org','-')}",
        f"🛰️ AS: {d.get('as','-')}",
        f"📍 {d.get('lat','?')}, {d.get('lon','?')}",
    ]
    if d.get("reverse"): parts.append(f"🔁 Reverse: {d['reverse']}")
    return "\n".join(parts)

async def basic_link_scan(url: str) -> str:
    try:
        m=re.match(r"^https?://([^/]+)/?", url.strip(), re.I)
        if not m: return "⚠️ رابط غير صحيح."
        host=m.group(1)
        ip=socket.gethostbyname(host)
        ssl_info="-"
        if url.lower().startswith("https://"):
            try:
                ctx=ssl.create_default_context()
                with socket.create_connection((host,443), timeout=5) as sock:
                    with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                        cert=ssock.getpeercert()
                        issuer=dict(x[0] for x in cert.get('issuer',())).get('organizationName','-')
                        subject=dict(x[0] for x in cert.get('subject',())).get('commonName','-')
                        ssl_info=f"CN={subject} / ISSUER={issuer}"
            except Exception as e:
                ssl_info=f"ssl-error: {e}"
        geo=await fetch_geo(ip)
        country=geo.get("country","-") if isinstance(geo,dict) else "-"
        isp=geo.get("isp","-") if isinstance(geo,dict) else "-"
        asn=geo.get("as","-") if isinstance(geo,dict) else "-"
        return (f"🔗 <code>{url}</code>\n🌐 {host}\n🧭 IP: {ip}\n🛡️ SSL: {ssl_info}\n"
                f"📍 الدولة: {country}\n📡 ISP: {isp}\n🛰️ ASN: {asn}\n"
                "ℹ️ فحص تقني أساسي.")
    except Exception as e:
        return f"⚠️ فشل الفحص: {e}"

def email_basic_check(email: str)->str:
    email=(email or "").strip()
    if not re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$", email):
        return "❌ صيغة غير صحيحة."
    domain=email.split("@",1)[1].lower()
    provider="-"
    for k,v in {"gmail.com":"Google","outlook.com":"Microsoft","hotmail.com":"Microsoft","live.com":"Microsoft",
                "yahoo.com":"Yahoo","icloud.com":"Apple","proton.me":"Proton","protonmail.com":"Proton"}.items():
        if domain.endswith(k): provider=v; break
    return f"✅ صالح بنيويًا.\n📮 مزود محتمل: {provider}\n🌐 الدومين: {domain}"

# ===== Media/Image utils =====
def pillow_ok():
    try:
        from PIL import Image  # noqa
        return True
    except: return False

async def compress_image(b: bytes, quality=70)->bytes|None:
    if not pillow_ok(): return None
    from PIL import Image
    im=Image.open(io.BytesIO(b))
    out=io.BytesIO(); im.convert("RGB").save(out, format="JPEG", optimize=True, quality=quality)
    return out.getvalue()

async def images_to_pdf(images:list[bytes])->bytes|None:
    try:
        import img2pdf
        return img2pdf.convert(images)
    except Exception as e:
        log.warning("[pdf] %s", e); return None

def yt_dlp_ok():
    try:
        import yt_dlp  # noqa
        return True
    except: return False

async def download_media(url:str, is_vip:bool)->tuple[str,bytes]|tuple[None,None]:
    if not yt_dlp_ok(): return None,None
    import yt_dlp, tempfile, os
    ydl_opts={
        "quiet": True, "noprogress": True,
        "outtmpl": "%(title).80s.%(ext)s",
        "format": "bestvideo+bestaudio/best" if is_vip else "best[filesize<15M]/worst",
    }
    loop=asyncio.get_running_loop()
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info=ydl.extract_info(url, download=True)
            if "requested_downloads" in info and info["requested_downloads"]:
                fn=info["requested_downloads"][0]["filepath"]
            else:
                title=info.get("title","media")
                exts=("mp4","mkv","webm","mp3","m4a")
                fn=None
                for p in os.listdir("."):
                    if p.lower().startswith(title.lower()) and p.split(".")[-1].lower() in exts:
                        fn=p; break
            if not fn: raise RuntimeError("file not found after download")
            with open(fn,"rb") as f: data=f.read()
            return info.get("title","media"), data
    try:
        t,d = await loop.run_in_executor(None, _run)
        return t,d
    except Exception as e:
        log.warning("[media] %s", e); return None,None

# ===== AI Vision (image translate) & Image Gen =====
async def ai_generate_image(prompt:str)->bytes|None:
    if not AI_ENABLED or client is None: return None
    try:
        r=client.images.generate(model=OPENAI_IMAGE_MODEL, prompt=prompt, size="1024x1024")
        b64=r.data[0].b64_json; return base64.b64decode(b64)
    except Exception as e:
        log.error("[image-gen] %s", e); return None

async def ai_translate_image_bytes(b:bytes, target_lang:str="ar")->str:
    if not AI_ENABLED or client is None: return "⚠️ AI معطل."
    try:
        b64=base64.b64encode(b).decode()
        sysmsg = "ترجم النص الظاهر في الصورة إلى العربية فقط." if target_lang=="ar" else "Translate all text in the image to English only."
        r,err=_chat([
            {"role":"system","content":sysmsg},
            {"role":"user","content":[
                {"type":"text","text":"Extract all text and provide translation only."},
                {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
            ]}
        ], temperature=0.0)
        if err: return f"⚠️ {err}"
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        return f"⚠️ خطأ في الترجمة: {e}"

# ===== Commands =====
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 /start — البدء\n/geo — تحديد موقع IP\n/translate — مترجم\n/lang — تغيير اللغة")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; chat_id=update.effective_chat.id
    user_get(uid)
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO,"rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT)
    except Exception as e: log.warning("[welcome] %s", e)

    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
        ]))
        return

    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())

async def geo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
        ])); return
    ai_set_mode(uid, "geo_ip")
    await update.message.reply_text("📍 أرسل IP أو دومين (مثال: 8.8.8.8 أو example.com).", parse_mode="HTML")

async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:"); return
    ai_set_mode(uid, "translate")
    await update.message.reply_text("🌐 أرسل نصًا (أو صورة نصية) للترجمة. استخدم /lang لتغيير لغة الإخراج.")

async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    await update.message.reply_text("اختر لغتك:", reply_markup=lang_kb(uid))

# ===== Buttons =====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=q.from_user.id
    user_get(uid)
    await q.answer()

    # gating
    if q.data=="verify":
        ok=await is_member(context, uid, force=True)
        if ok:
            await safe_edit(q, "👌 تم التحقق. اختر:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ غير مشترك بعد. انضم ثم اضغط تحقّق.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
            ]))
        return

    # language
    if q.data=="lang_menu":
        await safe_edit(q, "اختر اللغة:", kb=lang_kb(uid)); return
    if q.data in ("lang_ar","lang_en"):
        user_set_lang(uid, "ar" if q.data=="lang_ar" else "en")
        await safe_edit(q, "✅ تم ضبط اللغة.", kb=bottom_menu_kb(uid)); return

    # membership check
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
        ])); return

    if q.data=="vip_badge":
        u=user_get(uid); since=u.get("vip_since",0)
        since_txt=time.strftime("%Y-%m-%d", time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ VIP مدى الحياة\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data=="myinfo":
        u=user_get(uid)
        await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}\n🌐 لغة العرض: {u.get('lang','ar')}", kb=bottom_menu_kb(uid)); return

    if q.data=="upgrade":
        if user_is_premium(uid) or uid==OWNER_ID:
            await safe_edit(q, "⭐ حسابك VIP بالفعل.", kb=bottom_menu_kb(uid)); return
        ref=payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(tr("back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API:
                token = await paylink_auth_token()
                url = f"{PAYLINK_API_BASE}/addInvoice"
                body = {
                    "orderNumber": ref, "amount": VIP_PRICE_SAR, "clientName": q.from_user.full_name or "User",
                    "clientMobile":"0500000000","currency":"SAR","callBackUrl":_public_url("/payhook"),
                    "displayPending": False,"note": f"VIP #{ref}",
                    "products":[{"title":"VIP Lifetime","price":VIP_PRICE_SAR,"qty":1,"isDigital":True}]
                }
                s=await get_http_session()
                async with s.post(url, json=body, headers={"Authorization": f"Bearer {token}"}) as r:
                    data=await r.json(content_type=None)
                    pay_url=data.get("url") or data.get("mobileUrl") or data.get("qrUrl") or _build_pay_link(ref)
            else:
                pay_url=_build_pay_link(ref)
            await safe_edit(q, f"💳 VIP مدى الحياة ({VIP_PRICE_SAR:.2f} SAR)\n🔖 مرجع: <code>{ref}</code>", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 الذهاب للدفع", url=pay_url)],
                [InlineKeyboardButton("✅ تحقّق الدفع", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "⚠️ تعذّر إنشاء الدفع حالياً.", kb=sections_list_kb())
        return

    if q.data.startswith("verify_pay_"):
        ref=q.data.split("_",2)[-1]
        st=payments_status(ref)
        if st=="paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP.", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ الدفع لم يُسجّل بعد. جرّب لاحقًا.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تحقّق مرة أخرى", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        return

    # navigation
    if q.data == "back_home":
        ai_set_mode(uid, None)
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections" or q.data.startswith("sec_"):
        if q.data.startswith("sec_"):
            key=q.data.replace("sec_",""); sec=SECTIONS.get(key)
            if not sec:
                await safe_edit(q, "قريباً…", kb=sections_list_kb()); return
            allowed = sec.get("is_free") or user_is_premium(uid) or uid==OWNER_ID
            if not allowed:
                await safe_edit(q, f"🔒 {sec['title']}\n{tr('access_denied')}", kb=sections_list_kb()); return
            if key=="ai_tools":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=ai_tools_kb()); return
            if key=="security_vip":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=security_kb()); return
            if key=="services_misc":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=services_kb()); return
            await safe_edit(q, f"{sec['title']}\n{sec.get('desc','')}", kb=section_back_kb()); return
        else:
            await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # AI tool modes
    map_modes={
        "ai_osint":"osint",
        "ai_writer":"writer",
        "ai_stt":"stt",
        "ai_translate":"translate",
        "ai_images":"image_ai",
        "sec_linkscan":"linkscan",
        "sec_ip":"geo_ip",
        "sec_email":"email_check",
        "svc_vnum":"vnum",
        "svc_convert":"convert",
        "svc_media":"media_dl",
    }
    if q.data in map_modes:
        ai_set_mode(uid, map_modes[q.data])
        prompts={
            "osint":"🔎 أدخل: اسم/بريد/اسم مستخدم/دومين/هاتف (OSINT مبسّط وآمن).",
            "writer":"✍️ أرسل طلب الكتابة (مثال: إعلان لعطر).",
            "stt":"🎙️ أرسل ملاحظة صوتية الآن.",
            "translate":"🌐 أرسل نصًا أو صورة نصية للترجمة. استخدم /lang لتغيير اللغة.",
            "image_ai":"🖼️ اكتب وصف الصورة المطلوبة.",
            "linkscan":"🧪 أرسل الرابط لفحصه.",
            "geo_ip":"🛰️ أرسل IP أو دومين.",
            "email_check":"✉️ أرسل البريد الإلكتروني لفحصه.",
            "vnum":"📱 أرسل رمز الدولة (SA/US/EG...).",
            "convert":"🗜️ أرسل صورة (أو عدّة صور) لاختيار ضغط/تحويل PDF.",
            "media_dl":"⬇️ أرسل رابط فيديو/صوت (YouTube/Twitter/Instagram).",
        }
        await safe_edit(q, prompts[map_modes[q.data]], kb=section_back_kb()); return

# ===== Messages: text/photo/voice =====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:"); return
    u=user_get(uid); lang=u.get("lang","ar")
    mode=ai_get_mode(uid)
    txt=(update.message.text or "").strip()

    # GEO
    if mode=="geo_ip":
        if not txt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        query=None
        m=_IP_RE.search(txt)
        if m: query=m.group(0)
        elif _HOST_RE.match(txt.lower()): query=txt.lower()
        else:
            await update.message.reply_text("⚠️ أدخل IP أو دومين صحيح."); return
        sent=await update.message.reply_text("⏳ جاري الاستعلام …")
        data=await fetch_geo(query); out=fmt_geo(data)
        try: await sent.edit_text(out, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True)
        except: await update.message.reply_text(out, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True)
        return

    # OSINT deep (safe)
    if mode=="osint":
        if not txt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        kind="name"
        if "@" in txt and "." in txt: kind="email"
        elif re.match(r"^\+?\d{7,15}$", txt): kind="phone"
        elif re.match(r"^[a-z0-9_.-]{2,32}$", txt, re.I): kind="username"
        elif "." in txt and _HOST_RE.match(txt): kind="domain"
        prompt = {
            "email": f"حلل بريدًا إلكترونيًا بشكل آمن (هيكل/مزود/نصائح حماية) بدون معلومات حساسة: {txt}",
            "phone": f"قدم ملاحظات عامة وآمنة عن رقم هاتف (تنسيقات دولية ونصائح خصوصية) للرقم: {txt}",
            "username": f"قدم أفكار بحث علني (منصات محتملة/تحذيرات الخصوصية) لاسم المستخدم: {txt}",
            "domain": f"قدم نقاط OSINT عامة وآمنة عن الدومين (تقنية/أمن/نصائح): {txt}",
            "name": f"قدم إطار بحث علني عام وآمن عن الاسم: {txt}",
        }[kind]
        if kind=="email":
            structural = email_basic_check(txt)
            extra = ai_reply(prompt, lang=lang)
            await update.message.reply_text(f"🔎 فحص بنيوي:\n{structural}\n\n📌 تحليلات عامة:\n{extra}", reply_markup=section_back_kb())
        else:
            out = ai_reply(prompt, lang=lang)
            await update.message.reply_text(out, reply_markup=section_back_kb())
        return

    # Writer
    if mode=="writer":
        if not txt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        sys = "اكتب بالعربية بأسلوب تسويقي عملي ومختصر." if lang=="ar" else "Write in concise, compelling English."
        out = ai_reply(f"{sys}\n\n{txt}", lang=lang)
        await update.message.reply_text(out, reply_markup=section_back_kb()); return

    # Translate (text)
    if mode=="translate" and txt:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        target = "العربية" if lang=="ar" else "English"
        out = ai_reply(f"ترجم بدقة إلى {target}:\n{txt}", lang=lang)
        await update.message.reply_text(out, reply_markup=section_back_kb()); return

    # Link Scan (VIP)
    if mode=="linkscan":
        if not (user_is_premium(uid) or uid==OWNER_ID):
            await update.message.reply_text(tr("access_denied"), reply_markup=sections_list_kb()); return
        if not txt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        report = await basic_link_scan(txt)
        await update.message.reply_text(report, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True); return

    # Email Checker (VIP)
    if mode=="email_check":
        if not (user_is_premium(uid) or uid==OWNER_ID):
            await update.message.reply_text(tr("access_denied"), reply_markup=sections_list_kb()); return
        if not txt: return
        out=email_basic_check(txt)
        await update.message.reply_text(f"نتيجة الفحص:\n{out}", reply_markup=section_back_kb()); return

    # Media download
    if mode=="media_dl":
        if not txt: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VIDEO)
        title,data=await download_media(txt, is_vip=(user_is_premium(uid) or uid==OWNER_ID))
        if not data:
            await update.message.reply_text("⚠️ تعذّر التنزيل أو yt-dlp غير متوفر.", reply_markup=section_back_kb()); return
        try: await update.message.reply_video(video=data, caption=f"{title[:60]}", reply_markup=section_back_kb())
        except: await update.message.reply_document(document=data, caption=f"{title[:60]}", reply_markup=section_back_kb())
        return

    # Image convert prompt
    if mode=="convert":
        await update.message.reply_text("📎 أرسل صورة (أو عدة صور) لأختيارات الضغط/الـPDF.", reply_markup=section_back_kb()); return

    # Default: show menus
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# Voice/Audio -> STT
async def on_voice_or_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:"); return
    if ai_get_mode(uid)!="stt": return
    file = update.message.voice or update.message.audio
    if not file: return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    f=await context.bot.get_file(file.file_id)
    b=await f.download_as_bytearray()
    name=(getattr(file,"file_name",None) or "voice.ogg")
    if not AI_ENABLED:
        await update.message.reply_text(tr("ai_disabled")); return
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix="."+name.split(".")[-1]) as tmp:
            tmp.write(b); path=tmp.name
        with open(path,"rb") as fp:
            r = client.audio.transcriptions.create(model=OPENAI_STT_MODEL, file=fp, response_format="text")
        out=(r or "").strip()
        await update.message.reply_text(f"📝 النص:\n{out}", reply_markup=section_back_kb())
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطأ في التحويل: {e}", reply_markup=section_back_kb())

# Photos: translate image OR convert
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:"); return
    mode=ai_get_mode(uid); lang=user_get(uid).get("lang","ar")
    photo=update.message.photo[-1]
    f=await context.bot.get_file(photo.file_id)
    b=await f.download_as_bytearray()

    if mode=="translate":
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        out=await ai_translate_image_bytes(bytes(b), target_lang=("ar" if lang=="ar" else "en"))
        await update.message.reply_text(out, reply_markup=section_back_kb())
        return

    if mode=="convert":
        context.user_data.setdefault("convert_images", []).append(bytes(b))
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗜️ ضغط آخر صورة", callback_data="conv_compress")],
            [InlineKeyboardButton("📄 إضافة للمجموعة ثم إنهاء PDF", callback_data="conv_pdf_add")],
            [InlineKeyboardButton("✅ إنهاء PDF", callback_data="conv_pdf_done")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
        ])
        await update.message.reply_text("اختر العملية:", reply_markup=kb)
        return

# Convert buttons
async def on_convert_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=q.from_user.id
    if ai_get_mode(uid)!="convert": await q.answer(); return
    imgs:list[bytes]=context.user_data.get("convert_images", [])
    if q.data=="conv_compress":
        if not imgs: await safe_edit(q, "أرسل صورة أولًا.", kb=section_back_kb()); return
        out=await compress_image(imgs[-1], quality=70)
        if not out: await safe_edit(q, "⚠️ Pillow غير متوفرة.", kb=section_back_kb()); return
        await q.message.reply_document(InputFile(out, filename="compressed.jpg"), caption="تم الضغط ✅", reply_markup=section_back_kb()); await q.answer(); return
    if q.data=="conv_pdf_add":
        await safe_edit(q, "📥 أرسل مزيدًا من الصور ثم اضغط «إنهاء PDF».", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ إنهاء PDF", callback_data="conv_pdf_done")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
        ])); await q.answer(); return
    if q.data=="conv_pdf_done":
        if not imgs: await safe_edit(q, "أرسل صورًا أولًا.", kb=section_back_kb()); return
        pdf=await images_to_pdf(imgs)
        if not pdf: await safe_edit(q, "⚠️ img2pdf غير متوفرة.", kb=section_back_kb()); return
        await q.message.reply_document(InputFile(pdf, filename="images.pdf"), caption="تم إنشاء PDF ✅", reply_markup=section_back_kb())
        context.user_data["convert_images"]=[]; await q.answer(); return

# Image AI via text
async def on_text_for_image_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if ai_get_mode(uid)!="image_ai": return
    txt=(update.message.text or "").strip()
    if not txt: return
    if not AI_ENABLED:
        await update.message.reply_text(tr("ai_disabled"), reply_markup=section_back_kb()); return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    img=await ai_generate_image(txt)
    if not img:
        await update.message.reply_text("⚠️ تعذّر توليد الصورة.", reply_markup=section_back_kb()); return
    try:
        await update.message.reply_photo(photo=img, caption="🖼️ تم الإنشاء.", reply_markup=section_back_kb())
    except Exception as e:
        await update.message.reply_text(f"⚠️ لم أستطع إرسال الصورة: {e}", reply_markup=section_back_kb())

# Virtual numbers (requires env)
async def vnum_request(country_code:str)->str:
    base=(os.getenv("VNUM_API_BASE") or "").strip()
    key =(os.getenv("VNUM_API_KEY") or "").strip()
    if not (base and key and AIOHTTP_AVAILABLE):
        return "⚠️ خدمة الأرقام المؤقتة غير مفعّلة. أضف VNUM_API_BASE و VNUM_API_KEY في .env"
    try:
        s=await get_http_session()
        payload={"country": country_code.upper()}
        headers={"Authorization": f"Bearer {key}"}
        async with s.post(base.rstrip("/")+"/getNumber", json=payload, headers=headers) as r:
            data=await r.json(content_type=None)
            if r.status>=400: return f"⚠️ فشل الطلب: {data}"
            num=data.get("number") or "غير متاح"
            return f"📱 رقم تجريبي: {num}\nℹ️ استخدم ضمن القوانين فقط."
    except Exception as e:
        return f"⚠️ خطأ خدمة الأرقام: {e}"

# Text catcher for vnum & image_ai & others
async def extra_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    mode=ai_get_mode(uid)
    txt=(update.message.text or "").strip()
    if not txt: return
    if mode=="vnum":
        if not (user_is_premium(uid) or uid==OWNER_ID):
            await update.message.reply_text(tr("access_denied"), reply_markup=sections_list_kb()); return
        out=await vnum_request(txt)
        await update.message.reply_text(out, reply_markup=section_back_kb()); return
    if mode=="image_ai":
        await on_text_for_image_ai(update, context); return

# ===== Error =====
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context,'error','unknown'))

# ===== Main =====
def main():
    init_db()
    app=(Application.builder().token(BOT_TOKEN).post_init(on_startup).concurrent_updates(True).build())
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("geo", geo_cmd))
    app.add_handler(CommandHandler("translate", translate_cmd))
    app.add_handler(CommandHandler("lang", lang_cmd))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(CallbackQueryHandler(on_convert_buttons, pattern="^conv_"))

    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO), on_voice_or_audio))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    # Specific text routes first (image_ai/vnum), then general guard
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, extra_text_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))

    app.add_error_handler(on_error)
    app.run_polling()

if __name__=="__main__":
    main()


