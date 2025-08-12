# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging, tempfile, shutil
from pathlib import Path
from functools import partial
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

# ====== ضبط اللوج ======
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ====== OpenAI (اختياري) ======
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InputFile, BotCommand, BotCommandScopeDefault, BotCommandScopeChat, Message
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatMemberStatus, ChatAction
from telegram.error import BadRequest

# ميديا/ملفات
import img2pdf
from PIL import Image
import aiohttp
import ssl

# yt-dlp (للتحميل من المنصات)
import yt_dlp

# ====== تحميل البيئة ======
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ====== إعدادات أساسية ======
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
DOWNLOAD_MAX_MB = int(os.getenv("DOWNLOAD_MAX_MB", "50"))  # حد أقصى لإرسال الملفات
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/ferpoks")).resolve()

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "whisper-1")
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "كل الأدوات داخل تيليجرام: ذكاء اصطناعي، أمن وحماية، تحميل وسائط، تحويل ملفات وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

# ====== الدفع / VIP مدى الحياة ======
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# أرقام مؤقتة (اختياري — VIP)
NUMBERS_API_BASE = os.getenv("NUMBERS_API_BASE","").strip()
NUMBERS_API_KEY  = os.getenv("NUMBERS_API_KEY","").strip()

# ====== خادِم ويب للويبهوك ======
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "0") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web, ClientSession
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

CHANNEL_ID = None

def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

def _ensure_parent(pth: str) -> bool:
    try:
        Path(pth).parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print("[fs] cannot create parent dir for", pth, "->", e)
        return False

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or ""
    if not base:
        base = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()}" if os.getenv("RENDER_EXTERNAL_HOSTNAME") else ""
    return (base or "").rstrip("/") + path

def _clean_base(url: str) -> str:
    u = (url or "").strip().strip('"').strip("'");  u = u.lstrip("=") if u.startswith("=") else u
    return u

def _build_pay_link(ref: str) -> str:
    base = _clean_base(PAYLINK_CHECKOUT_BASE)
    if "{ref}" in base:
        return base.format(ref=ref)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}ref={ref}"

# ====== WEBHOOK ======
def _looks_like_ref(s: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}-\d{9,}", s or ""))

def _find_ref_in_obj(obj):
    if not obj: return None
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
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok": False, "error": "bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    ref = _find_ref_in_obj(data)
    if not ref:
        log.warning("[payhook] no-ref; keys: %s", list(data.keys())[:8])
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
            async def _health(_): return web.json_response({"ok": True})
            app.router.add_get("/", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            async def _payhook_get(_): return web.json_response({"ok": True})
            app.router.add_get("/payhook", _payhook_get)
        return app
    def _thread_main():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _start():
            app = await _make_app(); runner = web.AppRunner(app)
            await runner.setup(); port = int(os.getenv("PORT", "10000"))
            site = web.TCPSite(runner, "0.0.0.0", port); await site.start()
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF")
        loop.run_until_complete(_start())
        try: loop.run_forever()
        finally: loop.stop(); loop.close()
    threading.Thread(target=_thread_main, daemon=True).start()

_run_http_server()

# ====== /startup ======
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
            log.info("[startup] resolved @%s -> id=%s", u, CHANNEL_ID)
            break
        except Exception as e:
            log.warning("[startup] get_chat @%s failed: %s", u, e)
    if CHANNEL_ID is None:
        log.error("[startup] ❌ could not resolve channel id; using @username checks")

    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"), BotCommand("help","مساعدة"),
                BotCommand("geo","تحديد موقع IP"),
                BotCommand("stt","تشغيل تحويل الصوت لنص"),
                BotCommand("trans","مترجم فوري"),
                BotCommand("osint","بحث ذكي"),
                BotCommand("copy","مولد نصوص")
            ],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","بدء"), BotCommand("help","مساعدة"),
                BotCommand("id","معرّفك"), BotCommand("grant","منح VIP"),
                BotCommand("revoke","سحب VIP"), BotCommand("vipinfo","معلومات VIP"),
                BotCommand("refreshcmds","تحديث الأوامر"), BotCommand("debugverify","تشخيص التحقق"),
                BotCommand("dv","تشخيص سريع"), BotCommand("aidiag","تشخيص AI"),
                BotCommand("libdiag","إصدارات المكتبات"), BotCommand("paylist","آخر المدفوعات"),
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
    if conn is not None: return conn
    _ensure_parent(DB_PATH)
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False); conn.row_factory = sqlite3.Row
        _db._conn = conn; log.info("[db] using %s", DB_PATH); return conn
    except sqlite3.OperationalError as e:
        alt = "/tmp/bot.db"; _ensure_parent(alt)
        log.warning("[db] fallback to %s because: %s", alt, e)
        conn = sqlite3.connect(alt, check_same_thread=False); conn.row_factory = sqlite3.Row
        _db._conn = conn; return conn

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
            ("vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0;"),
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
        _db().commit()

def init_db(): migrate_db()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor(); c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?);", (uid,)); _db().commit()
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0, "vip_forever": 0, "vip_since": 0}
        out = dict(r); out.setdefault("vip_forever",0); out.setdefault("vip_since",0); out.setdefault("verified_ok",0); out.setdefault("verified_at",0)
        return out

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?",
                      (1 if ok else 0, int(time.time()), str(uid))); _db().commit()

def user_is_premium(uid: int|str) -> bool:
    u = user_get(uid); return bool(u.get("premium")) or bool(u.get("vip_forever"))

def user_grant(uid: int|str):
    now = int(time.time())
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0), ?) WHERE id=?",
                      (now, str(uid))); _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE id=?", (str(uid),)); _db().commit()

def ai_set_mode(uid: int|str, mode: str|None):
    with _conn_lock:
        _db().execute("""CREATE TABLE IF NOT EXISTS ai_state (user_id TEXT PRIMARY KEY, mode TEXT, updated_at INTEGER);""")
        _db().execute(
            "INSERT INTO ai_state (user_id, mode, updated_at) VALUES (?, ?, strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=strftime('%s','now')",
            (str(uid), mode)
        ); _db().commit()

def ai_get_mode(uid: int|str):
    with _conn_lock:
        c = _db().cursor(); c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
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
        c = _db().cursor(); c.execute("SELECT status FROM payments WHERE ref=?", (ref,))
        r = c.fetchone(); return r["status"] if r else None

def payments_mark_paid_by_ref(ref: str, raw=None) -> bool:
    with _conn_lock:
        c = _db().cursor(); c.execute("SELECT user_id, status FROM payments WHERE ref=?", (ref,))
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
        c = _db().cursor(); c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
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
    async with ClientSession() as s:
        async with s.post(url, json=payload, timeout=20) as r:
            data = await r.json(content_type=None)
            if r.status >= 400: raise RuntimeError(f"auth failed: {data}")
            token = data.get("token") or data.get("access_token") or data.get("id_token") or data.get("jwt")
            if not token: raise RuntimeError(f"auth failed: {data}")
            _paylink_token = token; _paylink_token_exp = now + 9*60; return token

async def paylink_create_invoice(order_number: str, amount: float, client_name: str):
    token = await paylink_auth_token()
    url = f"{PAYLINK_API_BASE}/addInvoice"
    body = {
        "orderNumber": order_number, "amount": amount, "clientName": client_name or "Telegram User",
        "clientMobile": "0500000000", "currency": "SAR", "callBackUrl": _public_url("/payhook"),
        "displayPending": False, "note": f"VIP via Telegram #{order_number}",
        "products": [{"title": "VIP Access (Lifetime)", "price": amount, "qty": 1, "isDigital": True}]
    }
    headers = {"Authorization": f"Bearer {token}"}
    async with ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            data = await r.json(content_type=None)
            if r.status >= 400: raise RuntimeError(f"addInvoice failed: {data}")
            pay_url = data.get("url") or data.get("mobileUrl") or data.get("qrUrl")
            if not pay_url: raise RuntimeError(f"addInvoice failed: {data}")
            return pay_url, data

# ====== نصوص قصيرة ======
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق من القناة",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
    }; return M.get(k, k)

# ====== الأقسام ======
SECTIONS = {
    # مجانية
    "osint_person": {"title":"🧭 البحث الذكي (OSINT-lite)","desc":"اكتب اسم/إيميل وسنحاول جلب بيانات عامة متاحة.","is_free": True},
    "text_gen": {"title":"✍️ مولد نصوص/ردود","desc":"اكتب المطلوب (مثال: إعلان لعطر).","is_free": True},
    "voice_stt": {"title":"🎙️ تحويل الصوت لنص","desc":"أرسل ملاحظة صوتية وسنحوّلها لنص.","is_free": True},
    "translator": {"title":"🌐 مترجم فوري","desc":"أرسل نص وسنترجمه للغة التي تختارها.","is_free": True},
    "geolocation": {"title":"📍 تحديد موقع IP/Domain","desc":"أرسل IP أو دومين ونرجع لك التفاصيل.","is_free": True},

    # الأمن والحماية (VIP)
    "link_scanner": {"title":"🛡️ فحص الروابط (VIP)","desc":"أرسل رابط وسنقدّم تقرير مبسط + بلد الاستضافة.","is_free": False},
    "email_checker": {"title":"📧 Email Checker (VIP)","desc":"فحص صيغة الإيميل + MX.","is_free": False},

    # تحميل وسائط
    "media_dl": {"title":"⬇️ تحميل وسائط","desc":"أرسل رابط فيديو/صوت (يوتيوب/تويتر/إنستغرام).","is_free": True},

    # خدمية فورية
    "virtual_numbers": {"title":"📱 أرقام مؤقتة (VIP)","desc":"يتطلب مزوّد API — سنبلغك إن لم يكن مضبوط.","is_free": False},
    "file_tools": {"title":"🗂️ ضغط/تحويل ملفات","desc":"أرسل صورًا لتحويلها PDF أو ضغطها.","is_free": True},
    "ai_images": {"title":"🖼️ صور بالذكاء الاصطناعي","desc":"اكتب وصف الصورة المطلوبة وسنولّدها لك.","is_free": True},

    # موجودة سابقًا (VIP موارد)
    "cyber_sec": {"title":"🛡️ الأمن السيبراني (VIP)","desc":"مواد الأمن السيبراني.","link":"", "is_free": False},
    "canva_500": {"title":"🖼️ 500 دعوة Canva Pro (VIP)","desc":"دعوات مدى الحياة.","link":"", "is_free": False},
    "dark_gpt": {"title":"🕶️ Dark GPT (VIP)","desc":"أداة متقدمة قريبًا.","link":"", "is_free": False},
    "adobe_win": {"title":"🎨 Adobe لويندوز (VIP)","desc":"روابط قريبًا.","link":"", "is_free": False},
}

# ====== لوحات ======
def bottom_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = []
    rows.append([InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")])
    if is_vip:
        rows.append([InlineKeyboardButton("⭐ حسابك VIP", callback_data="vip_badge")])
    else:
        rows.append([InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")])
    rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def sections_list_kb():
    rows = []
    # ترتيب مخصص لإبراز الميزات الجديدة
    order = ["osint_person","text_gen","voice_stt","translator","geolocation","link_scanner","email_checker","media_dl","virtual_numbers","file_tools","ai_images","cyber_sec","canva_500","dark_gpt","adobe_win"]
    for key in order:
        sec = SECTIONS.get(key); 
        if not sec: continue
        lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{key}")])
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return InlineKeyboardMarkup(rows)

def section_back_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data="back_sections")]])

def ai_hub_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],[InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]])

def ai_stop_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("🔚 إنهاء وضع الذكاء الاصطناعي", callback_data="ai_stop")],[InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections")]])

# ====== أدوات عامة ======
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("safe_edit error: %s", e)

ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except: pass

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
                status = getattr(cm, "status", None); ok = status in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id]=(True, now+60); user_set_verify(user_id, True); return True
            except Exception as e:
                log.warning("[is_member] try#%d target=%s ERROR: %s", attempt, target, e)
        if attempt < retries: await asyncio.sleep(backoff*attempt)
    _member_cache[user_id]=(False, now+60); user_set_verify(user_id, False); return False

# ====== AI Utils ======
def _ai_chat(messages, temperature=0.7, max_tokens=None):
    if not AI_ENABLED or client is None: return None, "ai_disabled"
    try:
        r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=messages, temperature=temperature, max_tokens=max_tokens)
        return r, None
    except Exception as e:
        msg = str(e)
        if "quota" in msg or "exceeded" in msg: return None, "quota"
        if "api key" in msg.lower() or "unauthorized" in msg.lower(): return None, "apikey"
        return None, msg

async def ai_copywrite(prompt: str) -> str:
    if not AI_ENABLED: return "⚠️ ميزة الذكاء الاصطناعي غير مفعّلة حالياً."
    sysmsg = "أنت كاتب محتوى عربي محترف. اكتب نصًا قصيرًا مؤثرًا مع CTA واضح. حافظ على أسلوب بسيط وجذاب."
    r, err = _ai_chat([{"role":"system","content":sysmsg},{"role":"user","content":prompt}], temperature=0.8)
    if err: return "⚠️ تعذّر التوليد الآن." if err!="apikey" else "⚠️ مفتاح OpenAI غير صالح/مفقود."
    return (r.choices[0].message.content or "").strip()

async def ai_translate(text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED: return "⚠️ المترجم غير مفعّل (OpenAI غير مضبوط)."
    sysmsg = f"ترجم النص بدقة إلى اللغة الهدف ({target_lang}). لا تشرح، فقط الترجمة."
    r, err = _ai_chat([{"role":"system","content":sysmsg},{"role":"user","content":text}], temperature=0.2)
    if err: return "⚠️ تعذّر الترجمة الآن."
    return (r.choices[0].message.content or "").strip()

async def ai_image(prompt: str, size="1024x1024") -> bytes|None:
    if not AI_ENABLED: return None
    try:
        gen = client.images.generate(model=OPENAI_IMAGE_MODEL, prompt=prompt, size=size)
        url = gen.data[0].url
        async with ClientSession() as s:
            async with s.get(url, timeout=60) as r:
                return await r.read()
    except Exception as e:
        log.warning("[ai_images] %s", e); return None

async def ai_transcribe_voice(file_path: str) -> str:
    if not AI_ENABLED: return "⚠️ تحويل الصوت لنص غير مفعّل."
    try:
        with open(file_path, "rb") as f:
            tr = client.audio.transcriptions.create(model=OPENAI_STT_MODEL, file=f)
        return (tr.text or "").strip()
    except Exception as e:
        log.warning("[stt] %s", e); return "⚠️ تعذّر تحويل الصوت لنص حالياً."

# ====== أدوات الأقسام (شبكة) ======
IP_RE   = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")
HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
URL_RE  = re.compile(r"https?://[^\s]+")

async def http_json(url, timeout=15):
    try:
        async with ClientSession() as s:
            async with s.get(url, timeout=timeout) as r:
                return await r.json(content_type=None)
    except Exception as e:
        log.warning("[http_json] %s", e); return None

async def fetch_geo(query: str) -> dict|None:
    url = f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    return await http_json(url)

def fmt_geo(data: dict) -> str:
    if not data or data.get("status")!="success":
        msg = data.get("message","lookup failed") if isinstance(data,dict) else "lookup failed"
        return f"⚠️ تعذّر جلب البيانات: {msg}"
    parts = []
    parts.append(f"🔎 الاستعلام: <code>{data.get('query','')}</code>")
    parts.append(f"🌍 {data.get('country','?')} — {data.get('regionName','?')}")
    parts.append(f"🏙️ {data.get('city','?')} — {data.get('zip','-')}")
    parts.append(f"⏰ {data.get('timezone','-')}")
    parts.append(f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}")
    parts.append(f"🛰️ AS: {data.get('as','-')}")
    parts.append(f"📍 {data.get('lat','?')}, {data.get('lon','?')}")
    if data.get("reverse"): parts.append(f"🔁 Reverse: {data['reverse']}")
    parts.append("\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join(parts)

async def osint_lookup(q: str) -> str:
    # بسيط: DuckDuckGo API + إن كان إيميل: تحقق MX
    out = []
    if "@" in q and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", q):
        domain = q.split("@",1)[1].lower()
        mx = await http_json(f"https://dns.google/resolve?name={domain}&type=MX")
        mx_ok = False
        if mx and mx.get("Answer"):
            mx_ok = True
            exch = ", ".join(sorted({a["data"].split()[-1].rstrip(".") for a in mx["Answer"] if "data" in a}))
            out.append(f"✅ MX موجود: {exch}")
        else:
            out.append("⚠️ لا يوجد MX للدومين (قد يكون وهمياً).")
    ddg = await http_json(f"https://api.duckduckgo.com/?q={q}&format=json&no_redirect=1&no_html=1")
    if ddg:
        if ddg.get("AbstractText"): out.append("📌 " + ddg["AbstractText"])
        topics = ddg.get("RelatedTopics") or []
        tips = []
        for t in topics:
            if isinstance(t, dict) and t.get("Text"):
                tips.append("• " + t["Text"])
            if len(tips) >= 5: break
        if tips: out.append("\n".join(tips))
    return "\n".join(out) if out else "لم أجد نتائج ذات معنى."

async def link_scan(url: str) -> str:
    # تقرير مبسّط: حالة HTTP + IP الدولة + هل HTTPS
    try:
        host = re.sub(r"^https?://","",url).split("/")[0]
        # DNS IP
        dns = await http_json(f"https://dns.google/resolve?name={host}&type=A")
        ips = [a["data"] for a in (dns.get("Answer") or []) if "data" in a] if dns else []
        geo_txt = ""
        if ips:
            info = await fetch_geo(ips[0])
            if info and info.get("status")=="success":
                geo_txt = f"{info.get('country','?')} / {info.get('city','?')} (ISP: {info.get('isp','-')})"
        # HTTP check
        async with ClientSession() as s:
            async with s.get(url, timeout=15, allow_redirects=True, ssl=False) as r:
                code = r.status; server = r.headers.get("server","-"); ctype = r.headers.get("content-type","-")
        is_https = url.lower().startswith("https://")
        result = [f"🔗 URL: {url}","🔒 HTTPS: نعم" if is_https else "🔓 HTTPS: لا", f"📥 HTTP Status: {code}", f"🧩 Server: {server}", f"📄 Content-Type: {ctype}"]
        if geo_txt: result.append(f"🌍 استضافة تقريبية: {geo_txt}")
        result.append("\n⚠️ هذا فحص مبسّط، ليس بديلًا عن حلول أمان احترافية.")
        return "\n".join(result)
    except Exception as e:
        return f"⚠️ تعذّر الفحص: {e}"

async def email_check(addr: str) -> str:
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr): return "❌ صيغة الإيميل غير صحيحة."
    domain = addr.split("@",1)[1].lower()
    mx = await http_json(f"https://dns.google/resolve?name={domain}&type=MX")
    if mx and mx.get("Answer"):
        exch = ", ".join(sorted({a["data"].split()[-1].rstrip(".") for a in mx["Answer"] if "data" in a}))
        return f"✅ صيغة صحيحة + MX موجود: {exch}"
    return "⚠️ الصيغة صحيحة لكن لا يوجد MX — قد لا يستقبل رسائل."

# ====== yt-dlp تنزيل ======
EXECUTOR = ThreadPoolExecutor(max_workers=2)
def _download_media_blocking(url: str, outdir: Path, max_mb: int) -> tuple[Path|None,str]:
    outdir.mkdir(parents=True, exist_ok=True)
    temp_tpl = str(outdir / "%(title).80s.%(ext)s")
    ydl_opts = {
        "outtmpl": temp_tpl,
        "format": "bv*+ba/b[ext=mp4]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fpath = Path(ydl.prepare_filename(info))
    except Exception as e:
        return None, f"⚠️ فشل التحميل: {e}"
    # تحقق الحجم
    if fpath.exists():
        sz_mb = fpath.stat().st_size / (1024*1024)
        if sz_mb > max_mb:
            return None, f"⚠️ الملف كبير ({sz_mb:.1f}MB) أكبر من الحد {max_mb}MB."
        return fpath, "ok"
    return None, "⚠️ لم يتم العثور على الملف."

# ====== أدوات ملفات ======
def _images_to_pdf_blocking(img_paths: list[Path], out_pdf: Path) -> tuple[Path|None,str]:
    try:
        # img2pdf يحتاج صور RGB/JPEG/PNG
        imgs = []
        for p in img_paths:
            im = Image.open(p).convert("RGB")
            tmp = p.with_suffix(".rgb.jpg")
            im.save(tmp, format="JPEG", quality=95)
            imgs.append(tmp)
        with open(out_pdf, "wb") as f:
            f.write(img2pdf.convert([str(x) for x in imgs]))
        return out_pdf, "ok"
    except Exception as e:
        return None, f"⚠️ فشل التحويل: {e}"

def _compress_images_blocking(img_paths: list[Path], outdir: Path, quality: int=70, max_side: int=1600) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    outs = []
    for p in img_paths:
        im = Image.open(p)
        im.thumbnail((max_side,max_side))
        out = outdir / (p.stem + ".compressed.jpg")
        im.save(out, "JPEG", quality=quality, optimize=True)
        outs.append(out)
    return outs

# ====== أوامر عامة ======
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة\n/geo – تحديد موقع IP\n/stt – تحويل الصوت لنص\n/trans – مترجم فوري\n/osint – بحث ذكي\n/copy – مولد نصوص")

async def stt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid): 
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid,"stt_on")
    await update.message.reply_text("🎙️ تم تفعيل وضع تحويل الصوت لنص. أرسل الآن ملاحظة صوتية.")

async def trans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid): 
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid,"translate")
    await update.message.reply_text("🌐 أرسل النص المطلوب ترجمته. (اكتب في أول سطر رمز اللغة مثل: en أو ar أو fr)")

async def osint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid): 
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid,"osint")
    await update.message.reply_text("🧭 أرسل اسمًا أو بريدًا إلكترونيًا للبحث العام (OSINT-lite).")

async def copy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await must_be_member_or_vip(context, uid): 
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    ai_set_mode(uid,"gen_text")
    await update.message.reply_text("✍️ أرسل المطلوب (مثال: إعلان لعطر نسائي فخم).")

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
               f"openai={v('openai')}  yt-dlp={v('yt-dlp')}  pillow={v('Pillow')}")
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
               f"python={os.sys.version.split()[0]}")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def paylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    rows = payments_last(15)
    if not rows: await update.message.reply_text("لا توجد مدفوعات بعد."); return
    txt = []
    for r in rows:
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['created_at']))}")
    await update.message.reply_text("\n".join(txt))

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = context.args[0] if context.args else update.effective_user.id
    u = user_get(uid)
    since = time.strftime('%Y-%m-%d', time.gmtime(u.get("vip_since",0))) if u.get("vip_since") else "N/A"
    txt = (f"UID: {u['id']}\n"
           f"premium={u.get('premium')}  vip_forever={u.get('vip_forever')}  vip_since={since}")
    await update.message.reply_text(txt)

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok} (check logs)")

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 جار إعادة تشغيل الخدمة الآن..."); os._exit(0)

# ====== /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db(); TMP_DIR.mkdir(parents=True, exist_ok=True)
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    user_get(uid)
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO,"rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e: log.warning("[welcome] %s", e)
    ok = await must_be_member_or_vip(context, uid)
    if not ok:
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        await context.bot.send_message(chat_id, f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت مشرف في @{MAIN_CHANNEL_USERNAMES[0]}.")
        return
    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())

# ====== الأزرار ======
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق.\nاختر من القائمة:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك.\nانضم ثم اضغط تحقّق.\n", kb=gate_kb()); 
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "vip_badge":
        u = user_get(uid); since = u.get("vip_since", 0)
        since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"⭐ حسابك VIP (مدى الحياة)\nمنذ: {since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n", kb=bottom_menu_kb(uid)); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك مفعل VIP (مدى الحياة).", kb=bottom_menu_kb(uid)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([[InlineKeyboardButton(tr("back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API:
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            txt = (f"💳 ترقية إلى VIP مدى الحياة ({VIP_PRICE_SAR:.2f} SAR)\n"
                   f"🔖 مرجعك: <code>{ref}</code>\n"
                   f"بعد الدفع سيتم التفعيل تلقائيًا.")
            await safe_edit(q, txt, kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 الذهاب للدفع", url=pay_url)],
                [InlineKeyboardButton("✅ تحقّق الدفع", callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
            ]))
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "تعذّر إنشاء/فتح رابط الدفع حالياً.", kb=sections_list_kb())
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_",""); st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, "🎉 تم تفعيل VIP (مدى الحياة). استمتع!", kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ لم يصل إشعار الدفع بعد.\nاضغط تحقّق لاحقًا.", kb=InlineKeyboardMarkup([
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
        key = q.data.replace("sec_",""); sec = SECTIONS.get(key)
        if not sec: await safe_edit(q, "قريباً…", kb=sections_list_kb()); return
        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')} — فعّل VIP من زر الترقية.", kb=sections_list_kb()); return

        # تفعيل أوضاع لكل قسم
        if key == "geolocation":
            ai_set_mode(uid, "geo_ip")
            await safe_edit(q, "📍 أرسل IP أو دومين الآن…", kb=section_back_kb()); return
        if key == "osint_person":
            ai_set_mode(uid, "osint")
            await safe_edit(q, "🧭 أرسل اسم/إيميل للبحث العام (OSINT-lite).", kb=section_back_kb()); return
        if key == "text_gen":
            ai_set_mode(uid, "gen_text")
            await safe_edit(q, "✍️ أرسل المطلوب (مثال: إعلان لعطر).", kb=section_back_kb()); return
        if key == "voice_stt":
            ai_set_mode(uid, "stt_on")
            await safe_edit(q, "🎙️ أرسل ملاحظة صوتية وسنحوّلها لنص.", kb=section_back_kb()); return
        if key == "translator":
            ai_set_mode(uid, "translate")
            await safe_edit(q, "🌐 أرسل النص (واكتب أول سطر رمز اللغة الهدف مثل: ar / en / fr).", kb=section_back_kb()); return
        if key == "link_scanner":
            ai_set_mode(uid, "scan_link")
            await safe_edit(q, "🛡️ أرسل الرابط لفحصه.", kb=section_back_kb()); return
        if key == "email_checker":
            ai_set_mode(uid, "email_check")
            await safe_edit(q, "📧 أرسل البريد الإلكتروني لفحصه.", kb=section_back_kb()); return
        if key == "media_dl":
            ai_set_mode(uid, "media_dl")
            await safe_edit(q, f"⬇️ أرسل رابط الفيديو/الصوت (الحد {DOWNLOAD_MAX_MB}MB).", kb=section_back_kb()); return
        if key == "virtual_numbers":
            ai_set_mode(uid, "numbers")
            if not (NUMBERS_API_BASE and NUMBERS_API_KEY):
                await safe_edit(q, "ℹ️ ميزة الأرقام المؤقتة VIP متاحة لكن لم يتم إعداد مزوّد API بعد.\nزوّدنا بيانات المزود لتفعيلها.", kb=section_back_kb()); return
            await safe_edit(q, "📱 أرسل الدولة أو الخدمة المطلوبة للحصول على رقم (إذا كان المزود يدعمها).", kb=section_back_kb()); return
        if key == "file_tools":
            ai_set_mode(uid, "file_tools_wait")
            await safe_edit(q, "🗂️ أرسل **صورة/صور** الآن:\n- إن أرسلت صورة واحدة: سأعطيك خيار (تحويل PDF / ضغط)\n- إن أرسلت عدة صور: سأحولها PDF.", kb=section_back_kb()); return
        if key == "ai_images":
            ai_set_mode(uid, "ai_img")
            await safe_edit(q, "🖼️ أرسل وصف الصورة التي تريد توليدها.", kb=section_back_kb()); return

        await safe_edit(q, "قريباً…", kb=sections_list_kb()); return

    if q.data == "ai_chat":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=sections_list_kb()); 
            await q.message.reply_text(tr("ai_disabled"), reply_markup=sections_list_kb()); return
        ai_set_mode(uid, "ai_chat"); await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك…", kb=ai_stop_kb())
        try: await q.message.reply_text("🤖 أكتب سؤالك هنا…", reply_markup=ai_stop_kb())
        except Exception as e: log.warning("[ai_chat] reply error: %s", e)
        return

    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb())
        try: await q.message.reply_text("تم الإيقاف.", reply_markup=sections_list_kb())
        except: pass
        return

# ====== استلام الرسائل ======
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; msg: Message = update.message
    user_get(uid)
    if not await must_be_member_or_vip(context, uid):
        await msg.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode = ai_get_mode(uid)
    text = (msg.text or "").strip()

    # === أوضاع نصية ===
    if mode == "geo_ip":
        if not text: return
        m = IP_RE.search(text) or (HOST_RE.match(text.lower()) and re.match(r".", text))
        if not (m or HOST_RE.match(text.lower())):
            await msg.reply_text("⚠️ أرسل IP مثل 8.8.8.8 أو دومين مثل example.com."); return
        sent = await msg.reply_text("⏳ جاري الاستعلام…")
        data = await fetch_geo(text.strip()); reply = fmt_geo(data)
        try: await sent.edit_text(reply, parse_mode="HTML", reply_markup=section_back_kb())
        except: await msg.reply_text(reply, parse_mode="HTML", reply_markup=section_back_kb()); 
        return

    if mode == "osint":
        if not text: return
        sent = await msg.reply_text("🔎 بحث عام…")
        info = await osint_lookup(text)
        try: await sent.edit_text(info, reply_markup=section_back_kb())
        except: await msg.reply_text(info, reply_markup=section_back_kb())
        return

    if mode == "gen_text":
        if not text: return
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        out = await ai_copywrite(text)
        await msg.reply_text(out, reply_markup=section_back_kb()); return

    if mode == "translate":
        if not text: return
        lines = text.splitlines()
        target = (lines[0].strip().lower() if len(lines)>1 and re.fullmatch(r"[a-z]{2}", lines[0].strip().lower()) else "ar")
        content = "\n".join(lines[1:]) if target!="ar" else text
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        tr = await ai_translate(content, target); await msg.reply_text(tr, reply_markup=section_back_kb()); return

    if mode == "scan_link":
        if not text: return
        m = URL_RE.search(text)
        if not m: await msg.reply_text("⚠️ أرسل رابط يبدأ بـ http/https."); return
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        rep = await link_scan(m.group(0)); await msg.reply_text(rep, reply_markup=section_back_kb()); return

    if mode == "email_check":
        if not text: return
        rep = await email_check(text.strip()); await msg.reply_text(rep, reply_markup=section_back_kb()); return

    if mode == "media_dl":
        if not text: return
        m = URL_RE.search(text)
        if not m: await msg.reply_text("⚠️ أرسل رابط فيديو/صوت صالح."); return
        url = m.group(0)
        await msg.reply_text("⏳ جارِ التحميل… هذا قد يستغرق قليلًا حسب طول الفيديو.")
        with tempfile.TemporaryDirectory(dir=TMP_DIR) as td:
            loop = asyncio.get_running_loop()
            fpath, status = await loop.run_in_executor(EXECUTOR, partial(_download_media_blocking, url, Path(td), DOWNLOAD_MAX_MB))
            if not fpath:
                await msg.reply_text(status, reply_markup=section_back_kb()); return
            try:
                await msg.reply_document(document=InputFile(str(fpath)), caption="✅ تم التحميل.", reply_markup=section_back_kb())
            except Exception as e:
                await msg.reply_text(f"⚠️ تعذّر إرسال الملف: {e}", reply_markup=section_back_kb())
        return

    if mode == "numbers":
        # يتطلب API — إن كان مضبوط نقدر ننفذ استعلام بسيط (شكل عام)
        if not (NUMBERS_API_BASE and NUMBERS_API_KEY):
            await msg.reply_text("ℹ️ لم يتم إعداد مزوّد الأرقام المؤقتة بعد.", reply_markup=section_back_kb()); return
        await msg.reply_text("⏳ سيتم دمج مزود الأرقام عند تزويدنا بالتوثيق النهائي.", reply_markup=section_back_kb()); return

    if mode == "ai_img":
        if not text: return
        await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
        img = await ai_image(text, size="1024x1024")
        if not img: await msg.reply_text("⚠️ تعذّر توليد الصورة حالياً."); return
        with tempfile.NamedTemporaryFile(dir=TMP_DIR, suffix=".png", delete=False) as tf:
            tf.write(img); tf.flush(); p = Path(tf.name)
        try:
            await msg.reply_photo(InputFile(str(p)), caption="🖼️ تم التوليد.", reply_markup=section_back_kb())
        finally:
            try: p.unlink(missing_ok=True)
            except: pass
        return

    # === أوضاع وسائط ===
    if mode == "stt_on" and (msg.voice or msg.audio or msg.video_note):
        file = await (msg.voice or msg.audio or msg.video_note).get_file()
        with tempfile.NamedTemporaryFile(dir=TMP_DIR, suffix=".ogg", delete=False) as tf:
            await file.download_to_drive(custom_path=tf.name); tmp_path = tf.name
        await msg.reply_text("⏳ تحويل الصوت لنص…")
        txt = await ai_transcribe_voice(tmp_path)
        try: os.remove(tmp_path)
        except: pass
        await msg.reply_text(txt, reply_markup=section_back_kb()); return

    if mode and mode.startswith("file_tools"):
        # استقبال صور/مستندات
        photos = []
        if msg.photo:
            # أكبر حجم
            p = msg.photo[-1]; file = await p.get_file()
            with tempfile.NamedTemporaryFile(dir=TMP_DIR, suffix=".jpg", delete=False) as tf:
                await file.download_to_drive(custom_path=tf.name); photos.append(Path(tf.name))
        elif msg.document and (msg.document.mime_type or "").startswith("image/"):
            file = await msg.document.get_file()
            ext = "." + (msg.document.file_name.split(".")[-1] if msg.document.file_name and "." in msg.document.file_name else "jpg")
            with tempfile.NamedTemporaryFile(dir=TMP_DIR, suffix=ext, delete=False) as tf:
                await file.download_to_drive(custom_path=tf.name); photos.append(Path(tf.name))
        else:
            if mode=="file_tools_wait":
                await msg.reply_text("📎 أرسل صورة/صور (وليس ملفًا غير صورة)."); return

        if not photos:
            await msg.reply_text("📎 أرسل صورة واحدة أو عدة صور."); return

        # إن صور متعددة -> PDF مباشرة
        if len(photos) > 1 or mode == "file_tools_pdf":
            out_pdf = TMP_DIR / f"merged_{int(time.time())}.pdf"
            loop = asyncio.get_running_loop()
            pdf, status = await loop.run_in_executor(EXECUTOR, partial(_images_to_pdf_blocking, photos, out_pdf))
            if not pdf:
                await msg.reply_text(status); return
            try:
                await msg.reply_document(InputFile(str(pdf)), caption="✅ PDF جاهز.", reply_markup=section_back_kb())
            finally:
                try: pdf.unlink(missing_ok=True)
                except: pass
            return

        # صورة واحدة: اسأل المستخدم
        if mode == "file_tools_wait" and len(photos) == 1:
            ai_set_mode(uid, "file_tools_choice:" + str(photos[0]))
            await msg.reply_text("ماذا تريد؟\n1) تحويل إلى PDF\n2) ضغط الصورة", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 PDF", callback_data="ft_pdf")],
                [InlineKeyboardButton("📉 ضغط", callback_data="ft_compress")],
                [InlineKeyboardButton("↩️ رجوع", callback_data="back_sections")]
            ]))
            return

    # افتراضي
    await msg.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await msg.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# أزرار فرعية لأداة الملفات
async def on_ft_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id; await q.answer()
    mode = ai_get_mode(uid)
    if not (mode and mode.startswith("file_tools_choice:")):
        await safe_edit(q, "أرسل صورة أولًا.", kb=section_back_kb()); return
    img_path = Path(mode.split(":",1)[1])
    if q.data == "ft_pdf":
        ai_set_mode(uid,"file_tools_pdf")
        # نفّذ التحويل
        loop = asyncio.get_running_loop()
        out_pdf = TMP_DIR / f"single_{int(time.time())}.pdf"
        pdf, status = await loop.run_in_executor(EXECUTOR, partial(_images_to_pdf_blocking, [img_path], out_pdf))
        if not pdf:
            await safe_edit(q, status, kb=section_back_kb()); return
        try:
            await q.message.reply_document(InputFile(str(pdf)), caption="✅ PDF جاهز.", reply_markup=section_back_kb())
        finally:
            try: pdf.unlink(missing_ok=True)
            except: pass
        return
    if q.data == "ft_compress":
        ai_set_mode(uid,"file_tools_compress")
        loop = asyncio.get_running_loop()
        outs = await loop.run_in_executor(EXECUTOR, partial(_compress_images_blocking, [img_path], TMP_DIR / "compressed"))
        if not outs:
            await safe_edit(q, "⚠️ لم يتم الضغط.", kb=section_back_kb()); return
        for p in outs:
            await q.message.reply_document(InputFile(str(p)), caption="✅ صورة مضغوطة.", reply_markup=section_back_kb())
        return

# ====== أوامر المالك ======
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ تم تفعيل VIP مدى الحياة للمستخدم {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم إلغاء VIP للمستخدم {context.args[0]}")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ====== نقطة التشغيل ======
def main():
    init_db(); TMP_DIR.mkdir(parents=True, exist_ok=True)
    app = (Application.builder().token(BOT_TOKEN).post_init(on_startup).concurrent_updates(True).build())
    app.add_handler(CommandHandler("start", start)); app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("geo", start))  # يفتح الأقسام ثم geolocation من الزر
    app.add_handler(CommandHandler("stt", stt_cmd))
    app.add_handler(CommandHandler("trans", trans_cmd))
    app.add_handler(CommandHandler("osint", osint_cmd))
    app.add_handler(CommandHandler("copy", copy_cmd))

    # أوامر المالك
    app.add_handler(CommandHandler("id", cmd_id)); app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke)); app.add_handler(CommandHandler("vipinfo", vipinfo))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds)); app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag)); app.add_handler(CommandHandler("paylist", paylist))
    app.add_handler(CommandHandler("debugverify", debug_verify)); app.add_handler(CommandHandler("dv", debug_verify))
    app.add_handler(CommandHandler("restart", restart_cmd))

    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(?!ft_).+"))
    app.add_handler(CallbackQueryHandler(on_ft_buttons, pattern=r"^ft_"))

    # استقبال نص/صوت/صورة/ملفات
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    app.add_error_handler(on_error); app.run_polling()

if __name__ == "__main__":
    main()
``





