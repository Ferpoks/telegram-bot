# -*- coding: utf-8 -*-
"""
Bot: Ferpoks – Full-featured Telegram Bot (قسم 1/2)
- إصلاح عمود lang في قاعدة البيانات (hotfix)
- إصلاح مشكلة التبديل بين العربية/الإنجليزية
- Dark GPT -> رابط FlowGPT (حسب طلبك)
- قسم الأمن السيبراني -> روابط الملفات المرسلة
- فيزا وهمية -> فتح موقع مولد البطاقات
- زيادة المتابعين -> 3 مواقع
- ترجمة نص/صور، توليد صور (fallback إلى dall-e-3)، فحص روابط، IP Lookup، Email Checker، أرقام مؤقتة (placeholder)، تنزيل وسائط، ضغط/تحويل صور→PDF
- بدون روابط خارجية في الأقسام الأخرى إلا حيث طلبت أنت صراحةً (Dark GPT + فيزا وهمية + ملفات الأمن والبايثون + المتابعين)
"""
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, ssl, socket, tempfile, io
from pathlib import Path
from dotenv import load_dotenv

# ===== اللوج =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ===== OpenAI (اختياري) =====
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

# ===== البيئة =====
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")

def _ensure_parent(p: str):
    Path(p).parent.mkdir(parents=True, exist_ok=True)

# ===== OpenAI إعداد =====
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL  = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "dall-e-3")  # الافتراضي المتاح عادةً
OPENAI_STT_MODEL   = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(",")) if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"
CHANNEL_ID = None

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT  = "مرحباً بك في بوت فيربوكس 🔥 – جميع الخدمات تعمل من داخل الدردشة ✨"

# ===== خادِم ويب (Webhook + Health) =====
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

# ===== الإقلاع =====
async def on_startup(app: Application):
    init_db()
    _hotfix_add_lang_column()  # إصلاح عمود lang لو ناقص
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

# ===== قاعدة البيانات =====
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
          vip_since INTEGER DEFAULT 0
        );""")
        # جدول المدفوعات
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
        # حالة AI
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT,
          updated_at INTEGER
        );""")
        _db().commit()

def _hotfix_add_lang_column():
    """يضيف عمود lang إذا كان مفقوداً في قواعد بيانات قديمة."""
    try:
        c = _db().cursor()
        c.execute("PRAGMA table_info(users)")
        cols = {r["name"] for r in c.fetchall()}
        if "lang" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'ar'")
            _db().commit()
            log.info("[db] hotfix: added column 'lang'")
    except Exception as e:
        log.error("[db] hotfix lang failed: %s", e)

def init_db(): migrate_db()

def user_get(uid) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id, premium, verified_ok, verified_at, vip_forever, vip_since, lang) VALUES (?,?,?,?,?,?,?)",
                          (uid,0,0,0,0,0,'ar'))
            _db().commit()
            return {"id": uid, "premium":0, "verified_ok":0, "verified_at":0, "vip_forever":0, "vip_since":0, "lang":"ar"}
        d = dict(r)
        if "lang" not in d:
            # fallback في حال قاعدة قديمة جدًا
            d["lang"] = "ar"
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

# ===== المدفوعات =====
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

# ===== ترجمات، نصوص واجهة =====
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

# ===== الأقسام =====
SECTIONS = {
    "python_zero": {"title":"🐍 بايثون من الصفر (مجاني)","desc":"كتاب/دليل بايثون من الصفر.","is_free":True},
    "ai_tools":   {"title":"🤖 أدوات الذكاء","desc":"OSINT عميق / مولد نصوص / مترجم / صوت→نص / صور AI","is_free":True},
    "security_vip":{"title":"🛡️ الأمن السيبراني (VIP)","desc":"روابط الدورات + أدوات الفحص","is_free":False},
    "services_misc":{"title":"🧰 خدمات فورية","desc":"أرقام مؤقتة / صور→PDF / تنزيل وسائط","is_free":True},
    "dark_gpt":   {"title":"🕶️ Dark GPT","desc":"ينقلك إلى FlowGPT (حسب طلبك)","is_free":True},
    "virtual_visa":{"title":"💳 فيزا وهمية (اختبار)","desc":"فتح موقع توليد بطاقات تجريبية","is_free":True},
    "followers_boost":{"title":"🚀 زيادة المتابعين","desc":"3 مواقع موثوقة لخدمات SMM","is_free":True},
}

# روابط الأقسام (حسب طلبك)
LINKS = {
    # بايثون من الصفر (رابطك)
    "python_book": "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf",
    # الأمن السيبراني (روابطك)
    "cyber_file_1": "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf",
    "cyber_file_2": "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A",
    # Dark GPT (رابطك)
    "dark_gpt": "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X",
    # فيزا وهمية: مولد بطاقات (يمكن تغييره)
    "visa_gen": "https://namso-gen.com/",
    # زيادة المتابعين
    "smm_1": "https://smmcpan.com",
    "smm_2": "https://seoclevers.com",
    "smm_3": "https://saudifollowup.com",  # سعودي فولو با
}

def sections_list_kb():
    # رتب المفاتيح بالشكل المطلوب
    order = ("ai_tools","security_vip","followers_boost","services_misc","python_zero","dark_gpt","virtual_visa")
    rows=[]
    for key in order:
        sec = SECTIONS[key]
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{key}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def ai_tools_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 بحث ذكي (OSINT)", callback_data="ai_osint")],
        [InlineKeyboardButton("✍️ مولد نصوص", callback_data="ai_writer")],
        [InlineKeyboardButton("🎙️ صوت → نص", callback_data="ai_stt")],
        [InlineKeyboardButton("🌐 مترجم (نص/صورة)", callback_data="ai_translate")],
        [InlineKeyboardButton("🖼️ صور AI", callback_data="ai_images")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def security_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 روابط الأمن السيبراني", callback_data="sec_cyber_links")],
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

def followers_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("SMMCPAN", url=LINKS["smm_1"])],
        [InlineKeyboardButton("SEOclevers", url=LINKS["smm_2"])],
        [InlineKeyboardButton("سعودي فولو با", url=LINKS["smm_3"])],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

def section_back_kb(): 
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

def lang_kb(uid:int):
    cur = user_get(uid).get("lang","ar")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ العربية" if cur=="ar" else "العربية"), callback_data="lang_ar"),
         InlineKeyboardButton(("✅ English" if cur=="en" else "English"), callback_data="lang_en")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_home")]
    ])

# ===== أمان العضوية =====
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

# ===== ذكاء اصطناعي: شات عام =====
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

# ===== IP/Geo وأدوات =====
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

# ===== صور/وسائط =====
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
    import yt_dlp, os
    ydl_opts={
        "quiet": True, "noprogress": True,
        "outtmpl": "%(title).80s.%(ext)s",
        "format": "bestvideo+bestaudio/best" if is_vip else "best[filesize<15M]/worst",
    }
    loop=asyncio.get_running_loop()
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info=ydl.extract_info(url, download=True)
            filepath=None
            # احصل على الملف الذي نزّل
            req = info.get("requested_downloads") or []
            if req:
                filepath=req[0].get("filepath")
            if not filepath:
                # محاولة التقاط أول ملف بالامتدادات المعروفة
                title=info.get("title","media")
                for p in os.listdir("."):
                    if p.lower().startswith(title.lower()) and p.split(".")[-1].lower() in ("mp4","mkv","webm","mp3","m4a"):
                        filepath=p; break
            if not filepath: raise RuntimeError("file not found after download")
            with open(filepath,"rb") as f: data=f.read()
            return info.get("title","media"), data
    try:
        t,d = await loop.run_in_executor(None, _run)
        return t,d
    except Exception as e:
        log.warning("[media] %s", e); return None,None

# ===== توليد صور AI (fallback) =====
async def ai_generate_image(prompt:str)->bytes|None:
    if not AI_ENABLED or client is None: return None
    models = [OPENAI_IMAGE_MODEL, "dall-e-3", "gpt-image-1"]
    tried = set()
    for m in models:
        if m in tried: 
            continue
        tried.add(m)
        try:
            r=client.images.generate(model=m, prompt=prompt, size="1024x1024")
            b64=r.data[0].b64_json; return base64.b64decode(b64)
        except Exception as e:
            log.error("[image-gen] %s", e)
            continue
    return None

# ===== ترجمة صورة عبر Vision =====
async def ai_translate_image_bytes(b:bytes, target_lang:str="ar")->str:
    if not AI_ENABLED or client is None: return "⚠️ AI معطل."
    try:
        b64=base64.b64encode(b).decode()
        sysmsg = "ترجم النص الظاهر في الصورة إلى العربية فقط." if target_lang=="ar" else "Translate all text in the image to English only."
        r,err=_chat([
            {"role":"system","content":sysmsg},
            {"role":"user","content":[
                {"type":"text","text":"Extract the text and provide translation only."},
                {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
            ]}
        ], temperature=0.0)
        if err:
            return f"⚠️ فشل الاتصال بـ OpenAI ({err})."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        return f"⚠️ خطأ في الترجمة: {e}"

# ===== الأوامر =====
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

# ===== الأزرار =====
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("safe_edit: %s", e)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=q.from_user.id
    user_get(uid)
    await q.answer()

    # بوابة العضوية
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

    # اللغة
    if q.data=="lang_menu":
        await safe_edit(q, "اختر اللغة:", kb=lang_kb(uid)); return
    if q.data in ("lang_ar","lang_en"):
        user_set_lang(uid, "ar" if q.data=="lang_ar" else "en")
        await safe_edit(q, "✅ Language updated.", kb=bottom_menu_kb(uid)); return

    # تحقق الانضمام
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
        ])); return

    # معلومات و VIP
    if q.data=="vip_badge":
        u=user_get(uid); since=u.get("vip_since",0)
        since_txt=time.strftime("%Y-%m-%d", time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ VIP مدى الحياة\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data=="myinfo":
        u=user_get(uid)
        lang=u.get("lang","ar")
        await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}\n🌐 لغة العرض: {lang}", kb=bottom_menu_kb(uid)); return

    # ترقية VIP
    if q.data=="upgrade":
        if user_is_premium(uid) or uid==OWNER_ID:
            await safe_edit(q, "⭐ حسابك VIP بالفعل.", kb=bottom_menu_kb(uid)); return
        ref=payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(tr("back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API:
                # مصادقة
                token=None
                try:
                    s=await get_http_session()
                    async with s.post(f"{PAYLINK_API_BASE}/auth", json={"apiId":PAYLINK_API_ID,"secretKey":PAYLINK_API_SECRET,"persistToken":False}) as r:
                        data=await r.json(content_type=None)
                        token=data.get("token") or data.get("access_token") or data.get("jwt")
                except Exception as e:
                    log.error("[paylink auth] %s", e)
                pay_url=_build_pay_link(ref)
                if token:
                    body = {
                        "orderNumber": ref, "amount": VIP_PRICE_SAR, "clientName": q.from_user.full_name or "User",
                        "clientMobile":"0500000000","currency":"SAR","callBackUrl":_public_url("/payhook"),
                        "displayPending": False,"note": f"VIP #{ref}",
                        "products":[{"title":"VIP Lifetime","price":VIP_PRICE_SAR,"qty":1,"isDigital":True}]
                    }
                    try:
                        async with s.post(f"{PAYLINK_API_BASE}/addInvoice", json=body, headers={"Authorization": f"Bearer {token}"}) as r:
                            data=await r.json(content_type=None)
                            pay_url=data.get("url") or data.get("mobileUrl") or data.get("qrUrl") or pay_url
                    except Exception as e:
                        log.error("[paylink addInvoice] %s", e)
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

    # تنقل الأقسام
    if q.data == "back_home":
        ai_set_mode(uid, None)
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return

    if q.data == "back_sections" or q.data.startswith("sec_"):
        if q.data.startswith("sec_"):
            key=q.data.replace("sec_","")
            if key not in SECTIONS:
                await safe_edit(q, "قريباً…", kb=sections_list_kb()); return
            sec=SECTIONS[key]
            allowed = sec.get("is_free") or user_is_premium(uid) or uid==OWNER_ID
            if not allowed:
                await safe_edit(q, f"🔒 {sec['title']}\n{tr('access_denied')}", kb=sections_list_kb()); return

            # سلوك خاص لكل قسم
            if key=="ai_tools":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=ai_tools_kb()); return
            if key=="security_vip":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=security_kb()); return
            if key=="services_misc":
                await safe_edit(q, f"{sec['title']}\n{sec['desc']}\nاختر:", kb=services_kb()); return
            if key=="followers_boost":
                await safe_edit(q, f"{sec['title']}\nاختر موقع:", kb=followers_kb()); return
            if key=="dark_gpt":
                # يفتح رابط FlowGPT مباشرة
                await safe_edit(q, f"🕶️ Dark GPT\nاضغط لفتح: {LINKS['dark_gpt']}", kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("فتح Dark GPT", url=LINKS["dark_gpt"])],
                    [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
                ])); return
            if key=="virtual_visa":
                await safe_edit(q, f"💳 فيزا وهمية (اختبار)\nمولّد البطاقات:", kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("فتح مولّد البطاقات", url=LINKS["visa_gen"])],
                    [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
                ])); return
            if key=="python_zero":
                await safe_edit(q, "🐍 بايثون من الصفر — تحميل:", kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton("فتح/تحميل الكتاب", url=LINKS["python_book"])],
                    [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
                ])); return

            await safe_edit(q, f"{sec['title']}\n{sec.get('desc','')}", kb=section_back_kb()); return
        else:
            await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # أزرار الأمن السيبراني داخل قائمته
    if q.data=="sec_cyber_links":
        # إظهار روابط الأمن السيبراني (2 رابط من عندك)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📘 ملف 1", url=LINKS["cyber_file_1"])],
            [InlineKeyboardButton("📦 دورة الهاكر الأخلاقي", url=LINKS["cyber_file_2"])],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
        ])
        await safe_edit(q, "🛡️ روابط الأمن السيبراني:", kb=kb); return

    # خريطة الأوضاع للأزرار (لازم تكون صحيحة)
    map_modes={
        "ai_osint":"osint",
        "ai_writer":"writer",
        "ai_stt":"stt",
        "ai_translate":"translate",   # ← مهم: صار يضبط وضع الترجمة
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
            "vnum":"📱 هذه الخدمة تحتاج مفتاح API. سنفعّلها لاحقًا.",
            "convert":"🗜️ أرسل صورة (أو عدّة صور) لاختيار ضغط/تحويل PDF.",
            "media_dl":"⬇️ أرسل رابط فيديو/صوت (YouTube/Twitter/Instagram).",
        }
        await safe_edit(q, prompts[map_modes[q.data]], kb=section_back_kb()); return
# === تكملة الكود من الجزء الأول ===

# باقي وظائف وأوامر البوت

# قسم الأمن السيبراني
async def cybersecurity_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛡️ **قسم الأمن السيبراني**\n\n"
        "📂 [ملف PDF - أدوات الفحص](https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf)\n"
        "📂 [دورة الهاكر الأخلاقي](https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A)\n"
        "📂 [بايثون من الصفر](https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf)\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# قسم Dark GPT
async def dark_gpt_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X"
    await update.message.reply_text(f"🖤 للدخول إلى Dark GPT اضغط هنا:\n{url}")

# قسم الفيزا الوهمية
async def fake_visa_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = "https://namso-gen.com"
    await update.message.reply_text(f"💳 لتوليد بطاقة وهمية، تفضل هنا:\n{url}")

# قسم زيادة المتابعين
async def followers_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📈 **قسم زيادة المتابعين**\n\n"
        "🔹 [SMM Panel](https://smmcpan.com)\n"
        "🔹 [سعودي فولو](https://saudifollow.com)\n"
        "🔹 [Follow Add](https://followadd.com)\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# إصلاح اللغة - التحويل بين العربية والإنجليزية
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang_choice = query.data.split("_")[-1]
    if lang_choice == "ar":
        context.user_data["lang"] = "ar"
        await query.edit_message_text("✅ تم تعيين اللغة: العربية")
    elif lang_choice == "en":
        context.user_data["lang"] = "en"
        await query.edit_message_text("✅ Language set: English")
    else:
        await query.edit_message_text("❌ خيار لغة غير معروف")

# إضافة الأوامر للقائمة الرئيسية
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛡️ الأمن السيبراني", callback_data="section_cyber")],
        [InlineKeyboardButton("🖤 Dark GPT", callback_data="section_darkgpt")],
        [InlineKeyboardButton("💳 فيزا وهمية", callback_data="section_visa")],
        [InlineKeyboardButton("📈 زيادة المتابعين", callback_data="section_followers")],
        [InlineKeyboardButton("🌐 تغيير اللغة", callback_data="change_lang")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحبًا بك! اختر من القائمة:",
        reply_markup=main_menu_keyboard()
    )

# التعامل مع الضغط على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "section_cyber":
        await cybersecurity_section(update, context)
    elif query.data == "section_darkgpt":
        await dark_gpt_section(update, context)
    elif query.data == "section_visa":
        await fake_visa_section(update, context)
    elif query.data == "section_followers":
        await followers_section(update, context)
    elif query.data == "change_lang":
        lang_keyboard = [
            [InlineKeyboardButton("🇸🇦 العربية", callback_data="set_lang_ar")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en")],
        ]
        await query.edit_message_text("اختر اللغة:", reply_markup=InlineKeyboardMarkup(lang_keyboard))
    elif query.data.startswith("set_lang_"):
        await set_language(update, context)

# تسجيل الأوامر والمُعالجات
def register_handlers(application: Application):
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    # باقي الأقسام التي تحتاج API خارجي
    # تظهر رسالة "سيتم التفعيل لاحقًا"
async def placeholder_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚧 هذه الخدمة تحتاج مفتاح API وسيتم تفعيلها لاحقًا.")

# مثال: فحص الروابط
async def url_check_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 أرسل الرابط بعد الأمر.")
        return
    # حالياً placeholder
    await placeholder_section(update, context)

# مثال: IP Lookup
async def ip_lookup_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 أرسل IP أو دومين بعد الأمر.")
        return
    # حالياً placeholder
    await placeholder_section(update, context)

# مثال: Email Checker
async def email_checker_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 أرسل الإيميل بعد الأمر.")
        return
    # حالياً placeholder
    await placeholder_section(update, context)

# مثال: أرقام مؤقتة
async def temp_numbers_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await placeholder_section(update, context)

# مثال: تحويل الصور أو ضغطها أو PDF
async def convert_images_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await placeholder_section(update, context)

# مثال: تحميل فيديو/صوت من السوشيال ميديا
async def download_media_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await placeholder_section(update, context)

# إضافة هذه الأوامر للبوت
def register_additional_handlers(application: Application):
    application.add_handler(CommandHandler("urlcheck", url_check_section))
    application.add_handler(CommandHandler("iplookup", ip_lookup_section))
    application.add_handler(CommandHandler("emailcheck", email_checker_section))
    application.add_handler(CommandHandler("tempnumbers", temp_numbers_section))
    application.add_handler(CommandHandler("convert", convert_images_section))
    application.add_handler(CommandHandler("download", download_media_section))

# تشغيل البوت
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # تسجيل الأوامر
    register_handlers(application)
    register_additional_handlers(application)

    # بدء التشغيل
    application.run_polling()

if __name__ == "__main__":
    main()


