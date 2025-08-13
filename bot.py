# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging, base64, ssl, socket
from pathlib import Path
from dotenv import load_dotenv

# ====== ضبط اللوج ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("bot")

# ====== OpenAI (اختياري) ======
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

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

# ====== تحميل البيئة ======
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ====== إعدادات أساسية ======
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

# مهم: تأكد أن هذا المسار على قرص دائم
DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")

def _ensure_parent(pth: str) -> bool:
    try:
        Path(pth).parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print("[db] cannot create parent dir for", pth, "->", e)
        return False

# === OpenAI ===
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")  # whisper-1 بديل
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

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
    "كل الميزات هنا داخل الدردشة مباشرة، بدون روابط خارجية للمستخدم. ✨"
)

CHANNEL_ID = None  # سيُحل عند الإقلاع

# ====== إعدادات الدفع ======
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()

# API
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))

# fallback اختياري (رابط منتج ثابت)
USE_PAYLINK_API        = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE  = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

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

# ====== خادِم ويب (Webhook + Health) ======
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "0") == "1" or PAY_WEBHOOK_ENABLE
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
            got = _find_ref_in_obj(v); 
            if got: return got
        return None
    if isinstance(obj, (list, tuple)):
        for v in obj:
            got = _find_ref_in_obj(v)
            if got: return got
    return None

# ====== WEBHOOK ======
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
            loop.stop()
            loop.close()

    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ====== عند الإقلاع ======
async def on_startup(app: Application):
    init_db()
    if AIOHTTP_AVAILABLE:
        _ = await get_http_session()
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
                BotCommand("start", "بدء"), 
                BotCommand("help", "مساعدة"),
                BotCommand("geo", "تحديد موقع IP"),
                BotCommand("translate", "مترجم فوري"),
            ],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # أوامر المالك
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"), BotCommand("help","مساعدة"),
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

# ====== قاعدة البيانات ======
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None:
        return conn
    path = DB_PATH
    _ensure_parent(path)
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
        log.info("[db] using %s", path)
        return conn
    except sqlite3.OperationalError as e:
        alt = "/tmp/bot.db"
        _ensure_parent(alt)
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
          vip_since INTEGER DEFAULT 0
        );""")
        c.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in c.fetchall()}
        for col, ddl in [
            ("verified_ok","ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;"),
            ("verified_at","ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;"),
            ("vip_forever","ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0;"),
            ("vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;")
        ]:
            if col not in cols:
                _db().execute(ddl)
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
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
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
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0, "vip_forever": 0, "vip_since": 0}
        out = dict(r)
        for k in ("verified_ok","verified_at","vip_forever","vip_since"):
            out.setdefault(k, 0)
        return out

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

def ai_set_mode(uid: int|str, mode: str|None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id, mode, updated_at) VALUES (?, ?, strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=strftime('%s','now')",
            (str(uid), mode)
        ); _db().commit()

def ai_get_mode(uid: int|str):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
        r = c.fetchone(); return r["mode"] if r else None

# ====== دفعات ======
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
        if not r:
            return False
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

# ====== Paylink API ======
_paylink_token = None
_paylink_token_exp = 0

async def paylink_auth_token():
    global _paylink_token, _paylink_token_exp
    now = time.time()
    if _paylink_token and _paylink_token_exp > now + 10:
        return _paylink_token

    url = f"{PAYLINK_API_BASE}/auth"
    payload = {"apiId": PAYLINK_API_ID, "secretKey": PAYLINK_API_SECRET, "persistToken": False}
    s = await get_http_session()
    async with s.post(url, json=payload) as r:
        data = await r.json(content_type=None)
        if r.status >= 400:
            raise RuntimeError(f"auth failed: {data}")
        token = data.get("token") or data.get("access_token") or data.get("id_token") or data.get("jwt")
        if not token:
            raise RuntimeError(f"auth failed: {data}")
        _paylink_token = token
        _paylink_token_exp = now + 9*60
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
    s = await get_http_session()
    async with s.post(url, json=body, headers=headers) as r:
        data = await r.json(content_type=None)
        if r.status >= 400:
            raise RuntimeError(f"addInvoice failed: {data}")
        pay_url = data.get("url") or data.get("mobileUrl") or data.get("qrUrl")
        if not pay_url:
            raise RuntimeError(f"addInvoice failed: {data}")
        return pay_url, data

# ====== نصوص قصيرة ======
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق من القناة",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
    }
    return M.get(k, k)

# ====== الأقسام ======
SECTIONS = {
    # مجانية
    "python_zero": {
        "title": "🐍 بايثون من الصفر (مجاني)",
        "desc": "دليلك الكامل لتعلّم البايثون.",
        "is_free": True,
        "content": "ابدأ بالأوامر /help ثم جرّب الأقسام الذكية أدناه."
    },

    # === أقسام تقنية ذكية (AI + Tools) ===
    "ai_tools": {
        "title": "🤖 أقسام تقنية ذكية",
        "desc": "أدوات: بحث ذكي عن الأشخاص/المعلومات (OSINT مبسّط)، مولد نصوص احترافية، تحويل صوت إلى نص، مترجم فوري، صور AI.",
        "is_free": True
    },

    # === أقسام أمن وحماية (VIP) ===
    "security_vip": {
        "title": "🛡️ أقسام أمن وحماية (VIP)",
        "desc": "فحص روابط مع تقرير أمان + الدولة المستضيفة، IP Lookup مدمج، Email Checker.",
        "is_free": False
    },

    # === أقسام خدمية فورية ===
    "services_misc": {
        "title": "🧰 أقسام خدمية فورية",
        "desc": "مولد أرقام وهمية (API خارجي – VIP فقط)، ضغط/تحويل ملفات (صور→PDF)، تنزيل وسائط (YouTube/Twitter/Instagram) بجودة أعلى للـVIP.",
        "is_free": True
    },
}

# ====== لوحات الأزرار ======
def bottom_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = []
    rows.append([InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")])
    if is_vip:
        rows.append([InlineKeyboardButton("⭐ حسابك VIP", callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")])
    rows.append([InlineKeyboardButton("📂 الأقسام", callback_data="back_sections")])
    rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def sections_list_kb():
    rows = []
    # عرض الأقسام الرئيسية
    for key in ("ai_tools","security_vip","services_misc","python_zero"):
        sec = SECTIONS[key]
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{key}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def section_back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

# لوحات فرعية للأدوات
def ai_tools_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 بحث ذكي (OSINT مبسّط)", callback_data="ai_osint")],
        [InlineKeyboardButton("✍️ مولد نصوص احترافية", callback_data="ai_writer")],
        [InlineKeyboardButton("🎙️ تحويل صوت إلى نص", callback_data="ai_stt")],
        [InlineKeyboardButton("🌐 مترجم فوري (نص/صورة)", callback_data="ai_translate")],
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
        [InlineKeyboardButton("🗜️ ضغط/تحويل صور→PDF", callback_data="svc_convert")],
        [InlineKeyboardButton("⬇️ تنزيل وسائط (يوتيوب/تويتر/انستا)", callback_data="svc_media")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
    ])

# ====== تعديل آمن ======
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        else:
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

# ====== حالات العضوية ======
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except AttributeError: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except AttributeError: pass

# ====== التحقق من العضوية (VIP/المالك bypass) ======
_member_cache = {}  # {uid: (ok, expire)}
async def must_be_member_or_vip(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID:
        return True
    return await is_member(context, user_id, retries=1, backoff=0.5)

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force=False, retries=1, backoff=0.5) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now: return cached[0]

    targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]

    async def _check(target):
        try:
            cm = await context.bot.get_chat_member(target, user_id)
            return getattr(cm, "status", None)
        except Exception as e:
            log.warning("[is_member] target=%s ERROR: %s", target, e)
            return None

    ok = False
    for attempt in range(1, retries + 1):
        statuses = await asyncio.gather(*[_check(t) for t in targets], return_exceptions=False)
        for status in statuses:
            if status in ALLOWED_STATUSES:
                ok = True
                break
        if ok:
            break
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)

    _member_cache[user_id] = (ok, now + (600 if ok else 120))
    user_set_verify(user_id, ok)
    return ok

# ====== AI Helpers ======
def _chat_with_fallback(messages, temperature=0.7, max_tokens=None):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    fallback_models = [OPENAI_CHAT_MODEL, "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]
    seen, ordered = set(), []
    for m in fallback_models:
        if m and m not in seen:
            ordered.append(m); seen.add(m)
    last_err = None
    for model in ordered:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg or "exceeded your current quota" in msg:
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
            {"role":"system","content":"أجب بالعربية بإيجاز ووضوح. كن عمليًا ومباشرًا."},
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

# ====== Geo/IP ======
_IP_RE = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")

async def fetch_geo(query: str) -> dict|None:
    if not AIOHTTP_AVAILABLE:
        return None
    url = f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        s = await get_http_session()
        async with s.get(url) as r:
            data = await r.json(content_type=None)
            if data.get("status") != "success":
                return {"error": data.get("message","lookup failed")}
            return data
    except Exception as e:
        log.warning("[geo] fetch error: %s", e)
        return {"error": "network error"}

def fmt_geo(data: dict) -> str:
    if not data:
        return "⚠️ تعذّر جلب البيانات."
    if data.get("error"):
        return f"⚠️ {data['error']}"
    parts = []
    parts.append(f"🔎 الاستعلام: <code>{data.get('query','')}</code>")
    parts.append(f"🌍 الدولة/المنطقة: {data.get('country','?')} — {data.get('regionName','?')}")
    parts.append(f"🏙️ المدينة/الرمز: {data.get('city','?')} — {data.get('zip','-')}")
    parts.append(f"⏰ التوقيت: {data.get('timezone','-')}")
    parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    parts.append(f"🛰️ AS: {data.get('as','-')}")
    parts.append(f"📍 الإحداثيات: {data.get('lat','?')}, {data.get('lon','?')}")
    if data.get("reverse"):
        parts.append(f"🔁 Reverse: {data['reverse']}")
    parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join(parts)

# ====== أدوات أمنية مبسطة ======
async def basic_link_scan(url: str) -> str:
    """
    فحص أساسي: يتحقق من بروتوكول/تشفير/الشهادة/حلّ الدومين وبلد الاستضافة (عن طريق IP-API).
    لا يرسل أي روابط للمستخدم.
    """
    try:
        # استخراج الدومين
        m = re.match(r"^https?://([^/]+)/?", url.strip(), re.I)
        if not m:
            return "⚠️ صيغة رابط غير صحيحة. أرسل رابط يبدأ بـ http أو https."
        host = m.group(1)
        # حلّ IP
        ip = socket.gethostbyname(host)
        # شهادة SSL (إن كان https)
        ssl_info = "-"
        if url.lower().startswith("https://"):
            try:
                ctx = ssl.create_default_context()
                with socket.create_connection((host, 443), timeout=5) as sock:
                    with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                        cert = ssock.getpeercert()
                        issuer = dict(x[0] for x in cert.get('issuer', ())).get('organizationName', '-')
                        subject = dict(x[0] for x in cert.get('subject', ())).get('commonName', '-')
                        ssl_info = f"CN={subject} / ISSUER={issuer}"
            except Exception as e:
                ssl_info = f"ssl-error: {e}"
        # بلد الاستضافة
        geo = await fetch_geo(ip)
        country = geo.get("country","-") if isinstance(geo, dict) else "-"
        isp = geo.get("isp","-") if isinstance(geo, dict) else "-"
        asn = geo.get("as","-") if isinstance(geo, dict) else "-"
        return (
            f"🔗 الرابط: <code>{url}</code>\n"
            f"🌐 الدومين: <code>{host}</code>\n"
            f"🧭 IP: <code>{ip}</code>\n"
            f"🛡️ SSL: {ssl_info}\n"
            f"📍 الدولة: {country}\n"
            f"📡 ISP: {isp}\n"
            f"🛰️ ASN: {asn}\n"
            "⚠️ هذا فحص تقني أساسي فقط (لا يُعد حكماً قاطعاً على الأمان).\n"
            "✅ لا ترسل الروابط هنا، النتائج تُعرض نصياً فقط."
        )
    except Exception as e:
        return f"⚠️ تعذّر فحص الرابط: {e}"

def email_basic_check(email: str) -> str:
    """
    فحص بنيوي + محاولة معرفة المزوّد من الدومين. بدون إرسال روابط.
    """
    email = (email or "").strip()
    if not re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$", email):
        return "❌ الصيغة غير صحيحة."
    domain = email.split("@",1)[1].lower()
    provider = "-"
    for k,v in {
        "gmail.com":"Google",
        "outlook.com":"Microsoft",
        "hotmail.com":"Microsoft",
        "live.com":"Microsoft",
        "yahoo.com":"Yahoo",
        "icloud.com":"Apple",
        "proton.me":"Proton",
        "protonmail.com":"Proton"
    }.items():
        if domain.endswith(k):
            provider = v; break
    return f"✅ صالح بنيويًا.\n📮 المزود المحتمل: {provider}\n🌐 الدومين: {domain}"

# ====== تحويل صوت إلى نص ======
async def stt_bytes_to_text(name: str, b: bytes) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix="."+name.split(".")[-1]) as f:
            f.write(b); fp = f.name
        with open(fp, "rb") as f:
            r = client.audio.transcriptions.create(
                model=OPENAI_STT_MODEL, file=f, response_format="text"
            )
        return (r or "").strip()
    except Exception as e:
        return f"⚠️ تعذّر تحويل الصوت: {e}"

# ====== صور AI ======
async def ai_generate_image(prompt: str) -> bytes|None:
    if not AI_ENABLED or client is None:
        return None
    try:
        r = client.images.generate(model=OPENAI_IMAGE_MODEL, prompt=prompt, size="1024x1024")
        b64 = r.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        log.error("[img] generate error: %s", e)
        return None

# ====== تنزيل وسائط (اختياري) ======
def _yt_dlp_available():
    try:
        import yt_dlp  # noqa
        return True
    except Exception:
        return False

async def download_media(url: str, is_vip: bool) -> tuple[str, bytes] | tuple[None, None]:
    """
    بدون إرسال روابط للمستخدم. نستخدم yt_dlp إن توفر.
    VIP: جودة أعلى (أفضل فيديو/أوديو).
    """
    if not _yt_dlp_available():
        return None, None
    import yt_dlp, tempfile
    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "outtmpl": "%(title).80s.%(ext)s",
        "format": "bestvideo+bestaudio/best" if is_vip else "best[filesize<10M]/worst",
    }
    loop = asyncio.get_running_loop()
    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # تنزيل إلى ملف مؤقت
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix="."+(info.get("ext") or "mp4"))
            ydl.download([url])
            # ابحث عن الملف الناتج
            fn = None
            if "requested_downloads" in info:
                for r in info["requested_downloads"]:
                    fn = r.get("filepath")
            if not fn:
                # fallback: استخدم العنوان
                fn = max([p for p in os.listdir(".") if p.startswith(info.get("title",""))], key=len)
            with open(fn, "rb") as fsrc:
                data = fsrc.read()
            return info.get("title","media"), data
    try:
        title, data = await loop.run_in_executor(None, _run)
        return title, data
    except Exception as e:
        log.warning("[media] download error: %s", e)
        return None, None

# ====== تحويل صور إلى PDF/ضغط ======
def _pillow_available():
    try:
        from PIL import Image  # noqa
        return True
    except Exception:
        return False

async def compress_image(b: bytes, quality=70) -> bytes|None:
    if not _pillow_available():
        return None
    from PIL import Image
    import io
    try:
        im = Image.open(io.BytesIO(b))
        out = io.BytesIO()
        im.convert("RGB").save(out, format="JPEG", optimize=True, quality=quality)
        return out.getvalue()
    except Exception as e:
        log.warning("[img] compress error: %s", e)
        return None

async def images_to_pdf(images: list[bytes]) -> bytes|None:
    try:
        import img2pdf
        return img2pdf.convert(images)
    except Exception as e:
        log.warning("[pdf] convert error: %s", e)
        return None

# ====== العرض ======
def build_section_text(sec: dict) -> str:
    parts = []
    title = sec.get("title",""); desc = sec.get("desc","")
    content = sec.get("content")
    if title: parts.append(title)
    if desc: parts.append("\n"+desc)
    if content: parts.append("\n"+content)
    return "\n".join(parts).strip()

# ====== أوامر ======
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 الأوامر:\n"
        "/start – قائمة البداية\n"
        "/help – هذه المساعدة\n"
        "/geo – تحديد موقع IP\n"
        "/translate – مترجم فوري (أرسل النص مباشرة بعد الأمر)"
    )

async def geo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid, "geo_ip")
    await update.message.reply_text("📍 أرسل الآن **IP** أو **دومين** (مثال: 8.8.8.8 أو example.com).", parse_mode="HTML")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

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
               f"aiohttp={'ok' if AIOHTTP_AVAILABLE else 'missing'}\n"
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
               f"openai={v('openai')}\n"
               f"yt_dlp={v('yt-dlp')}\n"
               f"Pillow={v('Pillow')}\n"
               f"img2pdf={v('img2pdf')}\n"
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
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['created_at']))}")
    await update.message.reply_text("\n".join(txt))

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = context.args[0] if context.args else update.effective_user.id
    u = user_get(uid)
    txt = (f"UID: {u['id']}\n"
           f"premium={u.get('premium')}  vip_forever={u.get('vip_forever')}  vip_since={u.get('vip_since')}")
    await update.message.reply_text(txt)

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=1, backoff=0.5)
    await update.message.reply_text(f"member={ok} (check logs for details)")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 جار إعادة تشغيل الخدمة الآن...")
    os._exit(0)

# ====== /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ====== الأزرار ======
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id
    user_get(uid)
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=1, backoff=0.5)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك بالقناة.\nانضم ثم اضغط تحقّق.\n\n" + need_admin_text(), kb=gate_kb())
        return

    # VIP/مالك bypass
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "vip_badge":
        u = user_get(uid)
        since = u.get("vip_since", 0)
        since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ حسابك VIP (مدى الحياة)\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n", kb=bottom_menu_kb(uid)); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك مفعل VIP بالفعل (مدى الحياة).", kb=bottom_menu_kb(uid))
            return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ جاري إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
        ]))
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
            await safe_edit(q, "تعذّر إنشاء/فتح رابط الدفع حالياً. جرّب لاحقاً.", kb=sections_list_kb())
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_", "")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP (مدى الحياة) على حسابك. استمتع!", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ لم يصل إشعار الدفع بعد.\nإذا دفعت للتو فانتظر قليلاً ثم اضغط تحقّق مرة أخرى.\n"
                               "لو استمر التأخير، احتفظ بمرجعك وأرسل لقطة للإدارة.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تحقّق مرة أخرى", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", kb=sections_list_kb()); 
            return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')} — فعّل VIP من زر الترقية.", kb=sections_list_kb()); return

        if key == "ai_tools":
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}\n\nاختر أداة:", kb=ai_tools_kb()); return
        if key == "security_vip":
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}\n\nاختر أداة:", kb=security_kb()); return
        if key == "services_misc":
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}\n\nاختر خدمة:", kb=services_kb()); return

        text = build_section_text(sec)
        await safe_edit(q, text, kb=section_back_kb())
        return

    # أدوات AI
    if q.data == "ai_osint":
        ai_set_mode(uid, "osint")
        await safe_edit(q, "🔎 أدخل اسم شخص أو بريد إلكتروني (OSINT مبسّط).", kb=section_back_kb()); return
    if q.data == "ai_writer":
        ai_set_mode(uid, "writer")
        await safe_edit(q, "✍️ اكتب طلبك (مثال: اكتب إعلان لمنتج عطور بلهجة سعودية قصيرة).", kb=section_back_kb()); return
    if q.data == "ai_stt":
        ai_set_mode(uid, "stt")
        await safe_edit(q, "🎙️ أرسل ملاحظة صوتية الآن لتحويلها إلى نص (يدعم العربية).", kb=section_back_kb()); return
    if q.data == "ai_translate":
        ai_set_mode(uid, "translate")
        await safe_edit(q, "🌐 أرسل نصًا (أو صورة تحتوي نص) وسأترجمها فورًا.", kb=section_back_kb()); return
    if q.data == "ai_images":
        ai_set_mode(uid, "image_ai")
        await safe_edit(q, "🖼️ اكتب وصف الصورة التي تريدها (واقعية/كرتونية).", kb=section_back_kb()); return

    # أمن وحماية
    if q.data == "sec_linkscan":
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, tr("access_denied"), kb=sections_list_kb()); return
        ai_set_mode(uid, "linkscan")
        await safe_edit(q, "🧪 أرسل الرابط لفحصه (لن يتم إرسال أي روابط لك — تقرير نصي فقط).", kb=section_back_kb()); return
    if q.data == "sec_ip":
        ai_set_mode(uid, "geo_ip")
        await safe_edit(q, "🛰️ أرسل IP أو دومين لعرض الدولة/الشركة/ASN.", kb=section_back_kb()); return
    if q.data == "sec_email":
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, tr("access_denied"), kb=sections_list_kb()); return
        ai_set_mode(uid, "email_check")
        await safe_edit(q, "✉️ أرسل البريد الإلكتروني لفحصه.", kb=section_back_kb()); return

    # خدمات
    if q.data == "svc_vnum":
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, tr("access_denied"), kb=sections_list_kb()); return
        ai_set_mode(uid, "vnum")
        await safe_edit(q, "📱 أرسل الدولة المطلوبة بصيغة رمز (مثال: SA أو US).", kb=section_back_kb()); return
    if q.data == "svc_convert":
        ai_set_mode(uid, "convert")
        await safe_edit(q, "🗜️ أرسل صورة لتحويلها إلى PDF أو ضغطها (سأعطيك خيارات بعد الإرسال).", kb=section_back_kb()); return
    if q.data == "svc_media":
        ai_set_mode(uid, "media_dl")
        await safe_edit(q, "⬇️ أرسل رابط فيديو/صوت (YouTube/Twitter/Instagram). (VIP: جودة أعلى).", kb=section_back_kb()); return

    if q.data == "back_home":
        ai_set_mode(uid, None)
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return

# ====== رسائل عامة ======
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode = ai_get_mode(uid)
    text = (update.message.text or "").strip()

    # وضع تحديد الموقع
    if mode == "geo_ip":
        if not text: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        query = None
        m = _IP_RE.search(text)
        if m:
            query = m.group(0)
        elif _HOST_RE.match(text.lower()):
            query = text.lower()
        else:
            await update.message.reply_text("⚠️ صيغة غير صحيحة. أرسل IP مثل 8.8.8.8 أو دومين مثل example.com.")
            return
        sent = await update.message.reply_text("⏳ جاري الاستعلام …")
        data = await fetch_geo(query)
        out = fmt_geo(data)
        try:
            await sent.edit_text(out, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True)
        except Exception:
            await update.message.reply_text(out, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True)
        return

    # OSINT مبسّط (اسم/إيميل)
    if mode == "osint":
        if not text: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        # تحليل مبسّط بدون روابط: لو بريد → فحص بنيوي؛ لو اسم → ملخص عام (AI)
        if "@" in text and "." in text:
            out = email_basic_check(text)
            await update.message.reply_text(f"🔎 بحث مبسّط على البريد:\n{out}", reply_markup=section_back_kb())
        else:
            prompt = f"اعطني نقاط OSINT مبسّطة وعامة وآمنة حول الاسم التالي (بدون تخمينات شخصية حساسة): {text}"
            reply = ai_chat_reply(prompt)
            await update.message.reply_text(reply, reply_markup=section_back_kb())
        return

    # مولد النصوص
    if mode == "writer":
        if not text: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        prompt = ("اكتب المحتوى التالي بجودة إعلانية احترافية وبالعربية الفصحى السهلة، "
                  "مع نقاط واضحة وسطر ختامي للحث على الإجراء:\n\n") + text
        reply = ai_chat_reply(prompt)
        await update.message.reply_text(reply, reply_markup=section_back_kb()); 
        return

    # مترجم فوري (نص فقط هنا — الصور بالهاندلر أدناه)
    if mode == "translate" and text:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        reply = ai_chat_reply(f"ترجم بدقة واحترافية إلى العربية:\n{text}")
        await update.message.reply_text(reply, reply_markup=section_back_kb()); 
        return

    # فحص روابط (VIP)
    if mode == "linkscan":
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await update.message.reply_text(tr("access_denied"), reply_markup=sections_list_kb()); return
        if not text: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        report = await basic_link_scan(text)
        await update.message.reply_text(report, parse_mode="HTML", reply_markup=section_back_kb(), disable_web_page_preview=True)
        return

    # Email Checker (VIP)
    if mode == "email_check":
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await update.message.reply_text(tr("access_denied"), reply_markup=sections_list_kb()); return
        if not text: return
        out = email_basic_check(text)
        await update.message.reply_text(f"نتيجة الفحص:\n{out}", reply_markup=section_back_kb())
        return

    # تنزيل وسائط
    if mode == "media_dl":
        if not text: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VIDEO)
        is_vip = (user_is_premium(uid) or uid == OWNER_ID)
        title, data = await download_media(text, is_vip=is_vip)
        if not data:
            msg = "⚠️ تعذّر التنزيل أو المكتبة غير متوفرة. ثبّت yt-dlp على السيرفر."
            await update.message.reply_text(msg, reply_markup=section_back_kb())
            return
        try:
            await update.message.reply_video(video=data, caption=f"تم التنزيل: {title[:60]}", reply_markup=section_back_kb())
        except Exception:
            await update.message.reply_document(document=data, caption=f"تم التنزيل: {title[:60]}", reply_markup=section_back_kb())
        return

    # صور AI
    if mode == "image_ai":
        if not text: return
        if not AI_ENABLED:
            await update.message.reply_text(tr("ai_disabled"), reply_markup=section_back_kb()); return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
        img = await ai_generate_image(text)
        if not img:
            await update.message.reply_text("⚠️ تعذّر توليد الصورة حالياً.", reply_markup=section_back_kb()); return
        try:
            await update.message.reply_photo(photo=img, caption="🖼️ تم الإنشاء.", reply_markup=section_back_kb())
        except Exception as e:
            await update.message.reply_text(f"⚠️ لم أستطع إرسال الصورة: {e}", reply_markup=section_back_kb())
        return

    # تحويل/ضغط
    if mode == "convert":
        await update.message.reply_text("📎 أرسل صورة واحدة أو عدة صور لأقوم بضغطها أو تحويلها PDF.", reply_markup=section_back_kb())
        return

    # افتراضي
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# ====== وسائط: صوت/صور ======
async def on_voice_or_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    mode = ai_get_mode(uid)
    if mode != "stt":
        return
    file = update.message.voice or update.message.audio
    if not file:
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    f = await context.bot.get_file(file.file_id)
    b = await f.download_as_bytearray()
    name = (file.file_name or "voice.ogg") if getattr(file,"file_name",None) else "voice.ogg"
    text = await stt_bytes_to_text(name, bytes(b))
    await update.message.reply_text(f"📝 النص:\n{text}", reply_markup=section_back_kb())

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    mode = ai_get_mode(uid)
    # ترجمة صورة (OCR+ترجمة عبر نموذج رؤية)
    if mode == "translate" and AI_ENABLED:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            photo = update.message.photo[-1]
            f = await context.bot.get_file(photo.file_id)
            b = await f.download_as_bytearray()
            b64 = base64.b64encode(bytes(b)).decode()
            messages = [
                {"role":"system","content":"استخرج النص من الصورة ثم ترجم كل النص إلى العربية فقط."},
                {"role":"user","content":[
                    {"type":"input_text","text":"ترجم كل النص الظاهر إلى العربية."},
                    {"type":"input_image","image_url":{"url":"data:image/jpeg;base64,"+b64}}
                ]}
            ]
            r, err = _chat_with_fallback(messages, temperature=0.0)
            if err:
                await update.message.reply_text("⚠️ تعذّر المعالجة حالياً.", reply_markup=section_back_kb()); return
            out = (r.choices[0].message.content or "").strip()
            await update.message.reply_text(out, reply_markup=section_back_kb())
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطأ أثناء ترجمة الصورة: {e}", reply_markup=section_back_kb())
        return

    # تحويل/ضغط صور
    if mode == "convert":
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
        photo = update.message.photo[-1]
        f = await context.bot.get_file(photo.file_id)
        b = await f.download_as_bytearray()
        # اسأل المستخدم ماذا يريد
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗜️ ضغط JPG", callback_data="conv_compress")],
            [InlineKeyboardButton("📄 تحويل إلى PDF (يدعم عدة صور)", callback_data="conv_pdf_add")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
        ])
        context.user_data.setdefault("convert_images", []).append(bytes(b))
        await update.message.reply_text("اختر العملية:", reply_markup=kb)

# ====== أزرار التحويل ======
async def on_convert_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id
    if ai_get_mode(uid) != "convert":
        await q.answer(); return
    imgs: list[bytes] = context.user_data.get("convert_images", [])
    if q.data == "conv_compress":
        if not imgs:
            await safe_edit(q, "أرسل صورة أولًا.", kb=section_back_kb()); return
        out = await compress_image(imgs[-1], quality=70)
        if not out:
            await safe_edit(q, "⚠️ مكتبة Pillow غير متوفرة أو حدث خطأ.", kb=section_back_kb()); return
        await q.message.reply_document(InputFile(out, filename="compressed.jpg"), caption="تم الضغط ✅", reply_markup=section_back_kb())
        await q.answer(); return
    if q.data == "conv_pdf_add":
        await safe_edit(q, "📥 أرسل المزيد من الصور ثم اضغط زر «إنهاء PDF».", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ إنهاء PDF", callback_data="conv_pdf_done")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
        ])); await q.answer(); return
    if q.data == "conv_pdf_done":
        if not imgs:
            await safe_edit(q, "أرسل صورًا أولًا.", kb=section_back_kb()); return
        pdf = await images_to_pdf(imgs)
        if not pdf:
            await safe_edit(q, "⚠️ مكتبة img2pdf غير متوفرة أو حدث خطأ.", kb=section_back_kb()); return
        await q.message.reply_document(InputFile(pdf, filename="images.pdf"), caption="تم إنشاء PDF ✅", reply_markup=section_back_kb())
        context.user_data["convert_images"] = []
        await q.answer(); return

# ====== أرقام مؤقتة (واجهة موحدة) ======
async def vnum_request(country_code: str) -> str:
    base = (os.getenv("VNUM_API_BASE") or "").strip()
    key  = (os.getenv("VNUM_API_KEY") or "").strip()
    if not (base and key and AIOHTTP_AVAILABLE):
        return "⚠️ خدمة الأرقام المؤقتة غير مفعّلة حالياً."
    try:
        s = await get_http_session()
        payload = {"country": country_code.upper()}
        headers = {"Authorization": f"Bearer {key}"}
        async with s.post(base.rstrip("/")+"/getNumber", json=payload, headers=headers) as r:
            data = await r.json(content_type=None)
            if r.status >= 400: return f"⚠️ فشل الطلب: {data}"
            num = data.get("number") or "غير متاح"
            return f"📱 رقم جاهز للتفعيل (تجريبي): {num}\nℹ️ استخدم بمسؤوليتك وضمن القوانين."
    except Exception as e:
        return f"⚠️ خطأ في خدمة الأرقام: {e}"

# ====== أوامر المالك ======
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>")
        return
    user_grant(context.args[0])
    await update.message.reply_text(f"✅ تم تفعيل VIP مدى الحياة للمستخدم {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>")
        return
    user_revoke(context.args[0])
    await update.message.reply_text(f"❌ تم إلغاء VIP للمستخدم {context.args[0]}")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ====== نقاط الدخول ======
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("geo", geo_cmd))
    app.add_handler(CommandHandler("translate", geo_cmd, filters=None))  # إبقاء الواجهة موحّدة

    # أوامر المالك فقط
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

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(CallbackQueryHandler(on_convert_buttons, pattern="^conv_"))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO), on_voice_or_audio))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()







