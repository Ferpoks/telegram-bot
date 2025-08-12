# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, sys, logging
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
# ملاحظة: على Render أنت تستخدم Env Vars مباشرة، فما يحتاج .env هناك.
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

# ====== إعدادات أساسية ======
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")

def _ensure_parent(pth: str) -> bool:
    try:
        Path(pth).parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print("[db] cannot create parent dir for", pth, "->", e)
        return False

# === أزلنا فحص httpx نهائياً لضمان عدم تعطيل AI ===
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
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
    "هنا تلاقي مصادر وأدوات للتجارة الإلكترونية، بايثون، الأمن السيبراني وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

CHANNEL_ID = None  # سيُحل عند الإقلاع

# ====== إعدادات الدفع Paylink ======
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
    from aiohttp import web, ClientSession
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

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
        # favicon لإسكات 404
        async def _favicon(_):
            return web.Response(status=204)
        app.router.add_get("/favicon.ico", _favicon)

        if SERVE_HEALTH:
            async def _health(_):
                return web.json_response({"ok": True})
            app.router.add_get("/", _health)
        if PAY_WEBHOOK_ENABLE:
            app.router.add_post("/payhook", _payhook)
            async def _payhook_get(_):
                return web.json_response({"ok": True})
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

    # أوامر عامة للمستخدمين: فقط start/help
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
            ],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # أوامر المالك فقط
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
                BotCommand("id", "معرّفك"),
                BotCommand("grant", "منح VIP"),
                BotCommand("revoke", "سحب VIP"),
                BotCommand("refreshcmds", "تحديث الأوامر"),
                BotCommand("debugverify", "تشخيص التحقق"),
                BotCommand("dv", "تشخيص سريع"),
                BotCommand("aidiag", "تشخيص AI"),
                BotCommand("libdiag", "إصدارات المكتبات"),
                BotCommand("paylist", "آخر المدفوعات"),
                BotCommand("restart", "إعادة تشغيل الخدمة"),
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

# ====== قاعدة البيانات ======
# RLock لتفادي Deadlock
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
        c.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in c.fetchall()}
        if "verified_ok" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0;")
        if "verified_at" not in cols:
            _db().execute("ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0;")
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
    with _conn_lock:
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        _db().commit()
    migrate_db()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            c.execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0}
        out = dict(r); out.setdefault("verified_ok", 0); out.setdefault("verified_at", 0)
        return out

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?",
                      (1 if ok else 0, int(time.time()), str(uid)))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])

def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),))
        _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),))
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
    # خارج القفل نفعل VIP لتجنب deadlock
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT user_id, status FROM payments WHERE ref=?", (ref,))
        r = c.fetchone()
        if not r:
            return False
        if r["status"] == "paid":
            return True
        user_id = r["user_id"]
        _db().execute(
            "UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?",
            (int(time.time()), json.dumps(raw, ensure_ascii=False) if raw is not None else None, ref)
        )
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

# ====== Paylink API ======
_paylink_token = None
_paylink_token_exp = 0

async def paylink_auth_token():
    global _paylink_token, _paylink_token_exp
    now = time.time()
    if _paylink_token and _paylink_token_exp > now + 10:
        return _paylink_token

    url = f"{PAYLINK_API_BASE}/auth"
    payload = {
        "apiId": PAYLINK_API_ID,
        "secretKey": PAYLINK_API_SECRET,
        "persistToken": False
    }
    async with ClientSession() as s:
        async with s.post(url, json=payload, timeout=20) as r:
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
        "products": [
            {"title": "VIP Access", "price": amount, "qty": 1, "isDigital": True}
        ]
    }
    headers = {"Authorization": f"Bearer {token}"}
    async with ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
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
        "desc": "دليلك الكامل لتعلّم البايثون من الصفر حتى الاحتراف مجانًا 🤩👑",
        "link": "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf",
        "photo": None, "is_free": True,
    },
    "ecommerce_courses": {
        "title": "🛒 التجارة الإلكترونية (مجاني)",
        "desc": "حزمة دورات وشروحات تجارة إلكترونية (أكثر من 7 ملفات).",
        "link": "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link",
        "photo": None, "is_free": True,
    },
    "followers_safe": {
        "title": "🚀 نمو المتابعين (آمن)",
        "desc": (
            "تنبيه: شراء/رشق متابعين قد يخالف سياسات المنصات.\n"
            "بدائل آمنة:\n"
            "• تحسين المحتوى + الهاشتاقات\n"
            "• تعاون/مسابقات\n"
            "• إعلانات ممولة\n"
            "• محتوى قصير مع CTA واضح"
        ),
        "is_free": True,
        "links": []
    },
    "epic_recovery": {
        "title": "🎮 استرجاع حساب Epic (ربط PSN)",
        "desc": "نموذج مراسلة دعم Epic إذا تم اختراق الحساب وتم ربط PSN بغير علمك.",
        "is_free": True,
        "content": (
            "Hello Epic Games Support,\n\n"
            "My Epic account appears to have been compromised via a phishing link. "
            "I have already secured my account (changed password and enabled 2FA). "
            "However, my PSN is currently linked incorrectly.\n\n"
            "Please help unlink current PSN or let me link mine.\n"
            "- Email: ____\n- Display name: ____\n- Country: ____\n"
        )
    },
    "virtual_numbers": {
        "title": "📱 أرقام مؤقتة (اختبار فقط)",
        "desc": "استخدمها قانونياً وللاختبار فقط.",
        "is_free": True,
        "links": [
            "https://receive-smss.com",
            "https://smsreceivefree.com",
            "http://sms24.me"
        ]
    },
    "tg_unlimit": {
        "title": "✉️ فك تقييد تيليجرام",
        "desc": "خطوات مراسلة دعم تيليجرام لمراجعة القيود.",
        "is_free": True,
        "content": "1) https://telegram.org/support\n2) اكتب طلباً واضحاً لرفع التقييد."
    },
    "dev_test_cards": {
        "title": "💳 فيزا وهمية (بيئة اختبار)",
        "desc": "استخدم بطاقات الاختبار الرسمية داخل بيئات التطوير فقط.",
        "is_free": True
    },
    "plus_apps": {
        "title": "🆓 تطبيقات بلس وألعاب معدلة (iOS) — مسؤوليتك",
        "desc": "قد يخالف شروط Apple — تحمّل المسؤولية وافحص الروابط.",
        "is_free": True,
        "links": [
            "https://www.arabsiphone.com/category/iphone/",
            "https://www.emad1saleh.com",
            "https://a7.ae/Plus/",
            "https://www.majed9.com/p/plus.html?m=1",
            "http://www.adelrahmani.com/tweak/",
            "https://www.alarabydownloads.com/plus-applications-programs-iphone/"
        ]
    },
    "geolocation": {
        "title": "📍 تحديد الموقع عبر IP (عام)",
        "desc": "استخدم لأغراض مشروعة فقط.",
        "is_free": True,
        "links": ["https://www.geolocation.com/ar"],
        "content": "أدخل IP تملكه/مأذون به لعرض البلد والمدينة ومزود الخدمة."
    },

    # VIP
    "cyber_sec": {
        "title": "🛡️ الأمن السيبراني (VIP)",
        "desc": "الأمن السيبراني من الصفر.",
        "link": "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A",
        "photo": None, "is_free": False,
    },
    "canva_500": {
        "title": "🖼️ 500 دعوة Canva Pro (VIP)",
        "desc": "دعوات كانفا برو مدى الحياة.",
        "link": "https://digital-plus3.com/products/canva500",
        "photo": None, "is_free": False,
    },
    "dark_gpt": {
        "title": "🕶️ Dark GPT (VIP)",
        "desc": "أداة متقدمة، التفاصيل لاحقاً.",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
    },
    "adobe_win": {
        "title": "🎨 برامج Adobe (ويندوز) (VIP)",
        "desc": "روابط Adobe للويندوز (قريباً).",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
    },
    "ai_hub": {
        "title": "🧠 الذكاء الاصطناعي (VIP)",
        "desc": "مركز أدوات الذكاء الاصطناعي: دردشة AI.",
        "link": "https://t.me/ferpokss",
        "photo": None, "is_free": False,
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
    rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def sections_list_kb():
    rows = []
    for k, sec in SECTIONS.items():
        if not sec.get("title"): 
            continue
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

# ====== تعديل آمن ======
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
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
    return await is_member(context, user_id, retries=3, backoff=0.7)

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now: return cached[0]

    for attempt in range(1, retries + 1):
        targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
        for target in targets:
            try:
                cm = await context.bot.get_chat_member(target, user_id)
                status = getattr(cm, "status", None)
                log.info("[is_member] try#%d target=%s status=%s user=%s", attempt, target, status, user_id)
                ok = status in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id] = (True, now + 60)
                    user_set_verify(user_id, True); return True
            except Exception as e:
                log.warning("[is_member] try#%d target=%s ERROR: %s", attempt, target, e)
        if attempt < retries: await asyncio.sleep(backoff * attempt)

    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False); return False

# ====== AI ======
def _chat_with_fallback(messages):
    """يحاول بعدة نماذج حديثة بترتيب مفضل، ويعيد (response, err_code_or_None)."""
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"

    # ترتيب نماذج معقولة السعر/الأداء
    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = [m for m in [primary, "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1", "gpt-3.5-turbo"] if m]
    seen = set(); ordered = []
    for m in fallbacks:
        if m not in seen:
            ordered.append(m); seen.add(m)

    last_err = None
    for model in ordered:
        try:
            r = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                timeout=30  # يحتاج openai>=1.30 تقريباً
            )
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            # خرائط أخطاء مفيدة للمستخدم
            if "insufficient_quota" in msg or "You exceeded your current quota" in msg:
                return None, "quota"
            if "invalid_api_key" in msg or "Incorrect API key" in msg or "No API key provided" in msg:
                return None, "apikey"
            # جرّب نموذج آخر
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str) -> str:
    """واجهة سهلة لاستدعاء الشات وإرجاع نص عربي + رسائل أخطاء واضحة."""
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
        if r is None: return "⚠️ تعذّر التنفيذ حالياً. جرّب لاحقاً."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] unexpected: %s", e)
        return "⚠️ حدث خطأ غير متوقع أثناء الرد من AI."

# ====== العرض ======
def build_section_text(sec: dict) -> str:
    parts = []
    title = sec.get("title",""); desc = sec.get("desc","")
    link = sec.get("link"); links = sec.get("links",[])
    content = sec.get("content")
    if title: parts.append(title)
    if desc: parts.append("\n"+desc)
    if content: parts.append("\n"+content)
    if links:
        parts.append("\n🔗 روابط مفيدة:"); [parts.append(u) for u in links]
    if link and link not in links:
        parts.append("\n🔗 الرابط:"); parts.append(link)
    return "\n".join(parts).strip()

# ====== أوامر ======
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة")

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
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['created_at']))}")
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

# ====== /start ======
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

# ====== الأزرار ======
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

    # VIP/مالك bypass للانضمام
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "vip_badge":
        await safe_edit(q, "⭐ حسابك مفعل VIP — استمتع بكل الأقسام.", kb=bottom_menu_kb(uid)); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n", kb=bottom_menu_kb(uid)); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, "⭐ حسابك مفعل VIP بالفعل.", kb=bottom_menu_kb(uid))
            return

        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, f"⏳ جاري إنشاء رابط الدفع…\n🔖 مرجعك: <code>{ref}</code>", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
        ]))
        try:
            if USE_PAYLINK_API:
                pay_url, invoice = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)

            txt = (f"💳 ترقية إلى VIP ({VIP_PRICE_SAR:.2f} SAR)\n"
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
            await safe_edit(q, "🎉 تم تفعيل VIP بالفعل على حسابك. استمتع!", kb=bottom_menu_kb(uid))
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

    # الأقسام (مع حالة خاصة لـ AI Hub لعرض زر دردشة AI)
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", kb=sections_list_kb()); 
            return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')} — فعّل VIP من زر الترقية.", kb=sections_list_kb()); return

        # حالة خاصة: AI Hub
        if key == "ai_hub":
            if not AI_ENABLED:
                await safe_edit(q, f"{sec['title']}\n\n{tr('ai_disabled')}", kb=ai_hub_kb())
            else:
                await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}\n\nاختر أداة:", kb=ai_hub_kb())
            return

        # باقي الأقسام
        text = build_section_text(sec)
        local, photo = sec.get("local_file"), sec.get("photo")
        if local and Path(local).exists():
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}", kb=section_back_kb())
            with open(local, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=text)
        elif photo:
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}", kb=section_back_kb())
            try:
                await q.message.reply_photo(photo=photo, caption=text)
            except Exception:
                await q.message.reply_text(text, reply_markup=section_back_kb())
        else:
            await safe_edit(q, text, kb=section_back_kb())
        return

    # AI: تفعيل وضع الدردشة + إرسال رسالة واضحة (مو بس Edit)
    if q.data == "ai_chat":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=sections_list_kb()); 
            await q.message.reply_text(tr("ai_disabled"), reply_markup=sections_list_kb())
            return

        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك الآن.", kb=ai_stop_kb())
        # رسالة جديدة لضمان ظهور التنبيه مهما كان وضع الرسالة الأصلية
        try:
            await q.message.reply_text("🤖 أكتب سؤالك هنا…", reply_markup=ai_stop_kb())
        except Exception as e:
            log.warning("[ai_chat] reply error: %s", e)
        return

    if q.data == "ai_stop":
        ai_set_mode(uid, None)
        await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb())
        try:
            await q.message.reply_text("تم إيقاف وضع الذكاء الاصطناعي.", reply_markup=sections_list_kb())
        except Exception:
            pass
        return

# ====== رسائل عامة ======
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode = ai_get_mode(uid)
    if mode == "ai_chat":
        t = (update.message.text or "").strip()
        if not t: 
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        reply = ai_chat_reply(t)
        await update.message.reply_text(reply, reply_markup=ai_stop_kb()); 
        return

    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())

# ====== أوامر المالك ======
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ تم تفعيل {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ تم إلغاء {context.args[0]}")

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ====== نقطة التشغيل ======
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # أوامر المالك فقط
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
    app.add_handler(CommandHandler("paylist", paylist))
    app.add_handler(CommandHandler("debugverify", debug_verify))
    app.add_handler(CommandHandler("dv", debug_verify))
    app.add_handler(CommandHandler("restart", restart_cmd))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()


