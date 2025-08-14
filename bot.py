# -*- coding: utf-8 -*-
# Ferpoks TG Bot — compact organized build with AR/EN UI, language picker with flags,
# sections, real tools (media DL, OSINT, email/link checks), courses links, unban templates,
# temp numbers/VCC links, image->PDF/JPG tools, text+image translation (OpenAI Vision).
# Python 3.11+, python-telegram-bot 21.7

import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile
from pathlib import Path
from io import BytesIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ===== OpenAI (optional) =====
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

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

# ===== Net / utils =====
import urllib.parse as _urlparse
from PIL import Image
import aiohttp
try:
    import yt_dlp
except Exception:
    yt_dlp = None
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None
try:
    import whois as pywhois
except Exception:
    pywhois = None

# ===== ENV =====
ENV_PATH = Path(".env")
# على Render لن يتم تحميل .env إذا كان متغير RENDER موجود
if ENV_PATH.exists() and not os.getenv("RENDER"):
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=True)
    except Exception:
        pass

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "1") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")

# Links (قابلة للتغيير من البيئة)
SMM_LINKS = (os.getenv("SMM_LINKS", "https://smmbox.com,https://smmfollows.com")).split(",")
TEMP_NUMBERS_LINK = os.getenv("TEMP_NUMBERS_LINK", "https://5sim.net")
VCC_LINK = os.getenv("VCC_LINK", "https://wise.com")  # مزوّد بطاقات افتراضية شرعي
UNBAN_TG_LINK = os.getenv("UNBAN_TG_LINK", "https://t.me/SpamBot")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

# Paylink (إن رغبت)
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = (os.getenv("PAYLINK_API_ID") or "").strip()
PAYLINK_API_SECRET = (os.getenv("PAYLINK_API_SECRET") or "").strip()
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API","1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()
VIP_PRICE_SAR = float(os.getenv("VIP_PRICE_SAR","10"))

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")

MAX_UPLOAD_MB = 47
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}" if MAIN_CHANNEL_USERNAMES else ""

CHANNEL_ID = None

# ===== HTTP server (health + webhook) =====
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web
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
            r"[?&]ref=([\w\-:]+)", r"(\d{6,}-\d{9,})"
        ):
            m = re.search(pat, s); 
            if m and _looks_like_ref(m.group(1)): return m.group(1)
        return None
    if isinstance(obj, dict):
        for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
            v = obj.get(k); 
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

async def _payhook(request):
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
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
        log.info("[http] aiohttp غير متوفر أو الإعدادات لا تتطلب خادم ويب"); return
    async def _make_app():
        app = web.Application()
        async def _favicon(_): return web.Response(status=204)
        app.router.add_get("/favicon.ico", _favicon)
        if SERVE_HEALTH:
            async def _health(_): return web.json_response({"ok": True})
            app.router.add_get("/", _health); app.router.add_get("/health", _health)
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
            log.info("[http] serving on 0.0.0.0:%d (webhook=%s health=%s)", port, "ON" if PAY_WEBHOOK_ENABLE else "OFF", "ON" if SERVE_HEALTH else "OFF")
        loop.run_until_complete(_start())
        try: loop.run_forever()
        finally: loop.stop(); loop.close()
    threading.Thread(target=_thread_main, daemon=True).start()
_run_http_server()

# ===== Startup: resolve channel & commands =====
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

    # Commands (public minimal)
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start","Start"),
                BotCommand("help","Help"),
            ],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        log.warning("[startup] set_my_commands default: %s", e)

    # Owner-only
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("id","Your ID"),
                BotCommand("grant","Grant VIP"),
                BotCommand("revoke","Revoke VIP"),
                BotCommand("vipinfo","VIP Info"),
                BotCommand("refreshcmds","Refresh Commands"),
                BotCommand("aidiag","AI Diagnostics"),
                BotCommand("libdiag","Lib Versions"),
                BotCommand("paylist","Recent Payments"),
                BotCommand("restart","Restart"),
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("[startup] set_my_commands owner: %s", e)

# ===== DB =====
_conn_lock = threading.RLock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is not None: return conn
    path = DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
    log.info("[db] using %s", path)
    return conn

def migrate_db():
    with _conn_lock:
        c = _db().cursor()
        # users
        c.execute("""
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
        c.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in c.fetchall()}
        need_rebuild = "id" not in cols
        if need_rebuild:
            log.warning("[db-migrate] users table missing 'id'; rebuilding")
            _db().execute("DROP TABLE IF EXISTS users_tmp;")
            _db().execute("ALTER TABLE users RENAME TO users_tmp;")
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
            _db().execute("INSERT OR IGNORE INTO users (id,premium,verified_ok,verified_at,vip_forever,vip_since,pref_lang) SELECT id,premium,verified_ok,verified_at,vip_forever,vip_since,COALESCE(pref_lang,'ar') FROM users_tmp;")
            _db().execute("DROP TABLE users_tmp;")
        else:
            for col, ddl in [
                ("verified_ok","ALTER TABLE users ADD COLUMN verified_ok INTEGER DEFAULT 0"),
                ("verified_at","ALTER TABLE users ADD COLUMN verified_at INTEGER DEFAULT 0"),
                ("vip_forever","ALTER TABLE users ADD COLUMN vip_forever INTEGER DEFAULT 0"),
                ("vip_since","ALTER TABLE users ADD COLUMN vip_since INTEGER DEFAULT 0"),
                ("pref_lang","ALTER TABLE users ADD COLUMN pref_lang TEXT DEFAULT 'ar'"),
            ]:
                if col not in cols:
                    _db().execute(ddl)

        # ai_state
        c.execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        c.execute("PRAGMA table_info(ai_state)")
        cols2 = {row["name"] for row in c.fetchall()}
        if "extra" not in cols2:
            _db().execute("ALTER TABLE ai_state ADD COLUMN extra TEXT DEFAULT NULL;")
        if "updated_at" not in cols2:
            _db().execute("ALTER TABLE ai_state ADD COLUMN updated_at INTEGER;")

        # payments
        c.execute("""
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
        try:
            extra = json.loads(r["extra"] or "{}")
        except Exception:
            extra = {}
        return r["mode"], extra

# payments
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
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(x) for x in c.fetchall()]

# ===== Membership =====
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

# ====== I18N ======
# نوفّر فعلياً AR/EN. باقي اللغات تُعرض بالأزرار لكن fallback للإنجليزية برسالة تنبيه صغيرة.
LANG_OPTIONS = [
    ("en", "English 🇺🇸"), ("ar", "العربية 🇸🇦"),
    ("zh", "普通话 🇨🇳"), ("hi","हिंदी 🇮🇳"),
    ("es","Español 🇪🇸"), ("pt","Português 🇵🇹"),
    ("bn","বাংলা 🇧🇩"), ("ru","Русский 🇷🇺"),
    ("fr","Français 🇫🇷"), ("de","Deutsch 🇩🇪"),
    ("ja","日本語 🇯🇵"), ("ko","한국어 🇰🇷"),
    ("tr","Türkçe 🇹🇷"), ("vi","Tiếng Việt 🇻🇳"),
    ("sv","Svenska 🇸🇪"), ("it","Italiano 🇮🇹"),
    ("pl","Polski 🇵🇱"), ("nl","Nederlands 🇳🇱"),
    ("th","ไทย 🇹🇭"), ("id","Bahasa Indonesia 🇮🇩"),
    ("ms","Bahasa Melayu 🇲🇾"), ("el","Ελληνικά 🇬🇷"),
]

TR = {
    "en": {
        "welcome": "Welcome to Ferpoks Bot 🔥\nAll tools inside Telegram: AI, link scan, media downloader, STT, AI images, courses & more.\nFree content for everyone. VIP unlocks extra powers ✨",
        "menu": "👇 Main menu:",
        "sections": "📂 Sections:",
        "need_join": "🔐 Join the channel to use the bot:",
        "need_admin_text": "⚠️ If verification fails, make sure the bot is an **admin** in the main channel.",
        "vip_badge": "⭐ Your account is VIP (lifetime)",
        "my_info": "👤 Your name: {name}\n🆔 Your ID: {uid}\n🌐 Language: {lang}",
        "upgrade": "⚡ Upgrade to VIP",
        "vip_active": "🎉 VIP activated for your account!",
        "pay_create_wait": "⏳ Creating payment link…\n🔖 Ref: <code>{ref}</code>",
        "go_pay": "🚀 Go to payment",
        "verify_pay": "✅ Verify payment",
        "back": "↩️ Back",
        "verify_join": "✅ Verify channel",
        "join_channel": "📣 Join channel",
        "ai_disabled": "🧠 AI is currently disabled.",
        "lang_choose_title_en": "Please select your preferred language:\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
        "lang_choose_title_ar": "يرجى اختيار لغتك من القائمة أدناه:",
        "change_lang": "🌐 Change language",
        "contact_admin": "📨 Contact admin",
        "info_btn": "👤 My info",
        "sections_btn": "📂 Sections",
        "vip_btn": "⭐ Your VIP",
        "smm": "🚀 Followers Booster (external)",
        "temp_numbers": "☎️ Temporary Numbers",
        "vcc": "💳 Virtual Cards",
        "ai_tools": "🤖 AI Tools",
        "security": "🛡️ Cybersecurity",
        "courses": "🎓 Courses",
        "media": "⬇️ Media Downloader",
        "files": "🗜️ File Tools",
        "unban": "🔓 Unban Helper",
        "writer": "✍️ Ad Writer",
        "stt": "🎙️ Voice to Text",
        "translate": "🌐 Translator",
        "img_ai": "🖼️ AI Image",
        "geo": "🛰️ IP Lookup",
        "link_scan": "🔗 Link Scanner",
        "email_check": "✉️ Email Checker",
        "image_to_pdf": "🖼️ Image → PDF",
        "image_compress": "🗜️ Compress Image",
        "ai_chat": "🤖 AI Chat",
        "ai_stop": "🔚 Stop AI",
        "send_ip_or_host": "📍 Send an IP or domain…",
        "send_name_or_mail": "🔎 Send a username or email for OSINT.",
        "send_voice_or_audio": "🎙️ Send a Voice or audio file.",
        "send_text_for_translate": "🌐 Send text{img} to translate → {to}.",
        "send_link_scan": "🛡️ Send the URL to scan.",
        "send_email_check": "✉️ Send the email to check.",
        "send_media_url": "🎬 Send the video/audio URL.",
        "send_image_to_pdf": "🖼️ Send one or more images, then /makepdf.",
        "send_image_to_compress": "🗜️ Send an image; I'll return a compressed JPG.",
        "ai_chat_on": "🤖 AI chat enabled. Send your message.",
        "ai_chat_off": "🔚 AI mode stopped.",
        "not_supported_lang": "⚠️ Full UI is available in Arabic/English for now. Falling back to English.",
        "courses_menu": "🎓 Choose a course:",
        "course_python": "🐍 Python from Zero",
        "course_cyber0": "🛡️ Cybersecurity from Zero",
        "course_ethical": "🕵️ Ethical Hacking (playlist)",
        "open_link": "🌐 Open link",
        "unban_menu": "🔓 Pick a service to see template + support link:",
        "unban_ig": "Instagram Unban",
        "unban_fb": "Facebook Unban",
        "unban_tg": "Telegram Unban",
        "unban_epic": "Epic Games Unban",
        "template_sent": "📋 Template sent. Edit with your details, then open support.",
        "media_ready": "✅ Download ready.",
    },
    "ar": {
        "welcome": "مرحباً بك في بوت فيربوكس 🔥\nكل الأدوات داخل تيليجرام: ذكاء اصطناعي، فحص روابط، تحميل وسائط، تحويل صوت لنص، صور AI، دورات وأكثر.\nالمجاني للجميع و VIP يفتح ميزات أقوى ✨",
        "menu": "👇 القائمة الرئيسية:",
        "sections": "📂 الأقسام:",
        "need_join": "🔐 انضم للقناة لاستخدام البوت:",
        "need_admin_text": "⚠️ لو ما اشتغل التحقق: تأكّد أن البوت **مشرف** في القناة الأساسية.",
        "vip_badge": "⭐ حسابك VIP (مدى الحياة)",
        "my_info": "👤 اسمك: {name}\n🆔 معرفك: {uid}\n🌐 اللغة: {lang}",
        "upgrade": "⚡ ترقية إلى VIP",
        "vip_active": "🎉 تم تفعيل VIP على حسابك!",
        "pay_create_wait": "⏳ جاري إنشاء رابط الدفع…\n🔖 مرجع: <code>{ref}</code>",
        "go_pay": "🚀 الذهاب للدفع",
        "verify_pay": "✅ تحقّق الدفع",
        "back": "↩️ رجوع",
        "verify_join": "✅ تحقّق من القناة",
        "join_channel": "📣 الانضمام للقناة",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً.",
        "lang_choose_title_en": "Please select your preferred language:\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
        "lang_choose_title_ar": "يرجى اختيار لغتك من القائمة أدناه:",
        "change_lang": "🌐 تغيير اللغة",
        "contact_admin": "📨 تواصل مع الإدارة",
        "info_btn": "👤 معلوماتي",
        "sections_btn": "📂 الأقسام",
        "vip_btn": "⭐ حسابك VIP",
        "smm": "🚀 رشق/زيادة متابعين (خارجي)",
        "temp_numbers": "☎️ أرقام مؤقتة",
        "vcc": "💳 بطاقات افتراضية",
        "ai_tools": "🤖 أدوات الذكاء الاصطناعي",
        "security": "🛡️ الأمن السيبراني",
        "courses": "🎓 الدورات",
        "media": "⬇️ تنزيل الوسائط",
        "files": "🗜️ أدوات الملفات",
        "unban": "🔓 فك الحظر",
        "writer": "✍️ كاتب إعلانات",
        "stt": "🎙️ صوت → نص",
        "translate": "🌐 مترجم",
        "img_ai": "🖼️ صورة AI",
        "geo": "🛰️ IP Lookup",
        "link_scan": "🔗 فحص الروابط",
        "email_check": "✉️ فحص الإيميل",
        "image_to_pdf": "🖼️ صورة → PDF",
        "image_compress": "🗜️ تصغير صورة",
        "ai_chat": "🤖 دردشة AI",
        "ai_stop": "🔚 إيقاف AI",
        "send_ip_or_host": "📍 أرسل IP أو دومين…",
        "send_name_or_mail": "🔎 أرسل اسم/يوزر أو إيميل للفحص.",
        "send_voice_or_audio": "🎙️ أرسل Voice أو ملف صوت.",
        "send_text_for_translate": "🌐 أرسل نصّاً{img} للترجمة → {to}.",
        "send_link_scan": "🛡️ أرسل الرابط للفحص.",
        "send_email_check": "✉️ أرسل الإيميل للفحص.",
        "send_media_url": "🎬 أرسل رابط الفيديو/الصوت.",
        "send_image_to_pdf": "🖼️ أرسل صورة أو أكثر ثم /makepdf.",
        "send_image_to_compress": "🗜️ أرسل صورة وسأرجّع نسخة مضغوطة.",
        "ai_chat_on": "🤖 تم تفعيل وضع الدردشة. أرسل رسالتك.",
        "ai_chat_off": "🔚 تم إيقاف وضع الذكاء الاصطناعي.",
        "not_supported_lang": "⚠️ حالياً الواجهة الكاملة بالعربية/الإنجليزية. سيتم استخدام الإنجليزية مؤقتاً.",
        "courses_menu": "🎓 اختر دورة:",
        "course_python": "🐍 بايثون من الصفر",
        "course_cyber0": "🛡️ الأمن السيبراني من الصفر",
        "course_ethical": "🕵️ دورة الهاكر الأخلاقي",
        "open_link": "🌐 فتح الرابط",
        "unban_menu": "🔓 اختر الخدمة لعرض الرسالة + رابط الدعم:",
        "unban_ig": "فك حظر إنستقرام",
        "unban_fb": "فك حظر فيسبوك",
        "unban_tg": "فك حظر تيليجرام",
        "unban_epic": "فك حظر Epic Games",
        "template_sent": "📋 تم إرسال القالب. عدّل بياناتك ثم افتح الدعم.",
        "media_ready": "✅ تم التحميل.",
    }
}

def _lang(uid: int) -> str:
    return user_get(uid).get("pref_lang","ar")

def t(uid: int, key: str, **kw) -> str:
    L = TR.get(_lang(uid), TR["en"])
    txt = L.get(key, TR["en"].get(key, key))
    try:
        return txt.format(**kw)
    except Exception:
        return txt

def lang_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, (code, label) in enumerate(LANG_OPTIONS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"lang_{code}"))
        if i % 2 == 0:
            rows.append(row); row=[]
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)

# ===== Quick helpers =====
def gate_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"join_channel"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(t(uid,"verify_join"), callback_data="verify")]
    ])

def bottom_menu_kb(uid: int):
    is_vip = (user_is_premium(uid) or uid == OWNER_ID)
    rows = [
        [InlineKeyboardButton(t(uid,"info_btn"), callback_data="myinfo"),
         InlineKeyboardButton(t(uid,"change_lang"), callback_data="change_lang")],
        [InlineKeyboardButton("📨", url=admin_button_url(), callback_data="noop"),
         InlineKeyboardButton(t(uid,"sections_btn"), callback_data="back_sections")],
    ]
    if is_vip:
        rows.insert(1, [InlineKeyboardButton(t(uid,"vip_btn"), callback_data="vip_badge")])
    else:
        rows.insert(1, [InlineKeyboardButton(t(uid,"upgrade"), callback_data="upgrade")])
    return InlineKeyboardMarkup(rows)

def sections_list_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"ai_tools"), callback_data="sec_ai"),
         InlineKeyboardButton(t(uid,"media"), callback_data="sec_media")],
        [InlineKeyboardButton(t(uid,"security"), callback_data="sec_security"),
         InlineKeyboardButton(t(uid,"files"), callback_data="sec_files")],
        [InlineKeyboardButton(t(uid,"courses"), callback_data="sec_courses"),
         InlineKeyboardButton(t(uid,"unban"), callback_data="sec_unban")],
        [InlineKeyboardButton(t(uid,"smm"), url=SMM_LINKS[0] if SMM_LINKS else "https://google.com"),
         InlineKeyboardButton(t(uid,"temp_numbers"), url=TEMP_NUMBERS_LINK)],
        [InlineKeyboardButton(t(uid,"vcc"), url=VCC_LINK)],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_home")]
    ])

def ai_tools_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"ai_chat"), callback_data="ai_chat"),
         InlineKeyboardButton(t(uid,"ai_stop"), callback_data="ai_stop")],
        [InlineKeyboardButton(t(uid,"writer"), callback_data="ai_writer"),
         InlineKeyboardButton(t(uid,"stt"), callback_data="ai_stt")],
        [InlineKeyboardButton(t(uid,"translate"), callback_data="ai_tr"),
         InlineKeyboardButton(t(uid,"img_ai"), callback_data="ai_img")],
        [InlineKeyboardButton(t(uid,"geo"), callback_data="ai_geo")],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
    ])

def media_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("YouTube/Twitter/IG", callback_data="media_dl")],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
    ])

def files_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"image_to_pdf"), callback_data="file_pdf"),
         InlineKeyboardButton(t(uid,"image_compress"), callback_data="file_compress")],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
    ])

def courses_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"course_python"), callback_data="course_py")],
        [InlineKeyboardButton(t(uid,"course_cyber0"), callback_data="course_cyber0")],
        [InlineKeyboardButton(t(uid,"course_ethical"), callback_data="course_ethical")],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
    ])

def unban_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid,"unban_ig"), callback_data="unban_ig")],
        [InlineKeyboardButton(t(uid,"unban_fb"), callback_data="unban_fb")],
        [InlineKeyboardButton(t(uid,"unban_tg"), callback_data="unban_tg")],
        [InlineKeyboardButton(t(uid,"unban_epic"), callback_data="unban_epic")],
        [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
    ])

async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
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

# ====== Net helpers / tools ======
_URL_RE = re.compile(r"https?://[^\s]+")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_IP_RE = re.compile(r"\b(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b")

DISPOSABLE_DOMAINS = {"mailinator.com","tempmail.com","10minutemail.com","yopmail.com","guerrillamail.com","trashmail.com"}

async def http_head(url: str) -> int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=15) as r:
                return r.status
    except Exception:
        return None

def md5_hex(s: str) -> str:
    return hashlib.md5(s.strip().lower().encode()).hexdigest()

def resolve_ip(host: str) -> str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in infos:
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

def fmt_geo(uid: int, data: dict) -> str:
    if not data: return "⚠️"
    if data.get("error"): return f"⚠️ {data['error']}"
    L = []
    L.append(f"🔎 {data.get('query','')}")
    L.append(f"🌍 {data.get('country','?')} — {data.get('regionName','?')}")
    L.append(f"🏙️ {data.get('city','?')} — {data.get('zip','-')}")
    L.append(f"⏰ {data.get('timezone','-')}")
    L.append(f"📡 {data.get('isp','-')} / {data.get('org','-')}")
    L.append(f"🛰️ {data.get('as','-')}")
    L.append(f"📍 {data.get('lat','?')}, {data.get('lon','?')}")
    if data.get("reverse"): L.append(f"🔁 {data['reverse']}")
    L.append("\nℹ️ Use legally." if _lang(uid)=="en" else "\nℹ️ استخدم هذه المعلومات لأغراض مشروعة فقط.")
    return "\n".join(L)

def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))

def whois_domain(domain: str) -> dict|None:
    if pywhois is None:
        return {"error": "python-whois not installed"}
    try:
        w = pywhois.whois(domain)
        return {
            "domain_name": str(getattr(w,"domain_name",None)),
            "registrar": getattr(w, "registrar", None),
            "creation_date": str(getattr(w, "creation_date", None)),
            "expiration_date": str(getattr(w, "expiration_date", None)),
            "emails": getattr(w, "emails", None)
        }
    except Exception as e:
        return {"error": f"whois error: {e}"}

async def osint_email(email: str, uid:int) -> str:
    if not is_valid_email(email): return "⚠️ Invalid email" if _lang(uid)=="en" else "⚠️ صيغة الإيميل غير صحيحة."
    local, domain = email.split("@", 1)
    # MX
    if dnsresolver:
        try:
            answers = dnsresolver.resolve(domain, "MX")
            mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
            mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else "none"
        except dnsexception.DNSException:
            mx_txt = "none"
    else:
        mx_txt = "dnspython not installed"
    # Gravatar
    g_url = f"https://www.gravatar.com/avatar/{md5_hex(email)}?d=404"
    g_st = await http_head(g_url)
    grav = "✅ yes" if g_st and 200 <= g_st < 300 else "❌ no"
    # Resolve + geo
    ip = resolve_ip(domain)
    if ip:
        data = await fetch_geo(ip)
        geo_text = fmt_geo(uid, data)
    else:
        geo_text = "⚠️ cannot resolve domain IP" if _lang(uid)=="en" else "⚠️ تعذّر حلّ IP للدومين."
    # WHOIS
    w = whois_domain(domain)
    if w and not w.get("error"):
        w_txt = f"WHOIS:\n- Registrar: {w.get('registrar')}\n- Created: {w.get('creation_date')}\n- Expires: {w.get('expiration_date')}"
    else:
        w_txt = f"WHOIS: {w.get('error') if w else 'N/A'}"
    return "\n".join([
        f"📧 {email}",
        f"📮 MX: {mx_txt}",
        f"🖼️ Gravatar: {grav}",
        w_txt,
        "", geo_text
    ])

async def osint_username(name: str) -> str:
    uname = re.sub(r"[^\w\-.]+", "", name.strip())
    if not uname or len(uname) < 3:
        return "⚠️ Enter a valid username (≥3)." 
    # probe GitHub (public)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.github.com/users/{uname}", timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    return f"GitHub: ✅ — public_repos={data.get('public_repos')}, since {data.get('created_at')}"
                elif r.status == 404:
                    return "GitHub: ❌ not found"
                else:
                    return f"GitHub: unexpected {r.status}"
    except Exception as e:
        return f"GitHub: network error ({e})"

def classify_url(u: str) -> dict:
    try:
        p = _urlparse.urlparse(u)
        return {"ok": True, "scheme": p.scheme, "host": p.hostname, "path": p.path, "q": p.query}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def link_scan(u: str, uid:int) -> str:
    if not _URL_RE.search(u or ""):
        return "⚠️ Send a valid http(s) URL" if _lang(uid)=="en" else "⚠️ أرسل رابط يبدأ بـ http/https"
    meta = classify_url(u)
    if not meta.get("ok"):
        return "⚠️ invalid URL"
    host = meta.get("host") or ""
    scheme = meta.get("scheme")
    issues = []
    if scheme != "https": issues.append("❗️ no HTTPS")
    ip = resolve_ip(host) if host else None
    geo_txt = fmt_geo(uid, await fetch_geo(ip)) if ip else ("⚠️ cannot resolve host IP" if _lang(uid)=="en" else "⚠️ تعذّر حلّ IP للمضيف.")
    status = await http_head(u)
    if status is None: issues.append("⚠️ HEAD failed")
    else: issues.append(f"🔎 HTTP: {status}")
    return f"🔗 <code>{u}</code>\nHost: <code>{host}</code>\n" + "\n".join(issues) + f"\n\n{geo_txt}"

# ===== AI helpers =====
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"
    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = [m for m in [primary, "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"] if m]
    last_err = None
    for model in fallbacks:
        try:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.5, timeout=60)
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if "insufficient_quota" in msg or "exceeded" in msg: return None, "quota"
            if "api key" in msg.lower(): return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(uid:int, prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return t(uid,"ai_disabled")
    sys_ar = "أجب بالعربية بإيجاز ووضوح."
    sys_en = "Reply in concise and clear English."
    sysmsg = sys_ar if _lang(uid)=="ar" else sys_en
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":sysmsg},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled": return t(uid,"ai_disabled")
        if err == "quota": return "⚠️ OpenAI quota exceeded."
        if err == "apikey": return "⚠️ OpenAI API key invalid."
        if r is None: return "⚠️ Try later."
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[ai] unexpected: %s", e)
        return "⚠️ Error."

async def translate_text(uid:int, text: str, target_lang: str="ar") -> str:
    if not AI_ENABLED or client is None:
        return t(uid,"ai_disabled")
    messages = [
        {"role":"system","content":"You are a high-quality translator. Preserve meaning and formatting."},
        {"role":"user","content": f"Translate the following into {target_lang}. Keep formatting where possible:\n\n{text}"}
    ]
    r, err = _chat_with_fallback(messages)
    if err: return "⚠️ translation error"
    return (r.choices[0].message.content or "").strip()

async def translate_image_file(uid:int, path: str, target_lang: str="ar") -> str:
    if not (AI_ENABLED and client and OPENAI_VISION):
        return t(uid,"ai_disabled")
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content = [
            {"role":"user","content":[
                {"type":"text","text": f"Extract all text from the image and translate it to {target_lang}. Return only the translation."},
                {"type":"image_url","image_url":{"url": f"data:image/jpeg;base64,{b64}"}}
            ]}
        ]
        r = client.chat.completions.create(model="gpt-4o-mini", messages=content, temperature=0)
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        log.error("[vision-translate] %s", e)
        return "⚠️ cannot process image."

async def ai_write(uid:int, prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return t(uid,"ai_disabled")
    sysmsg = "اكتب نصًا عربيًا إعلانيًا جذابًا ومختصرًا، مع عناوين قصيرة وCTA واضح." if _lang(uid)=="ar" else "Write a catchy concise ad copy in English with short headings and clear CTA."
    r, err = _chat_with_fallback([{"role":"system","content":sysmsg},{"role":"user","content":prompt}])
    if err: return "⚠️ error"
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

# ===== Media Downloader (MP4-first, ffmpeg optional) =====
async def download_media(url: str) -> Path|None:
    if yt_dlp is None:
        log.warning("yt_dlp not installed")
        return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(TMP_DIR / "%(title).70s.%(ext)s")
    # اختيارات بدون ffmpeg قدر الإمكان + حد الحجم
    ydl_opts = {
        "outtmpl": outtmpl,
        # نحاول mp4/avc1 أولاً لتعمل على كل الأجهزة
        "format": (
            "bestvideo[ext=mp4][vcodec*=avc1][filesize<47M]+bestaudio[ext=m4a][acodec*=mp4a]/"
            "best[ext=mp4][filesize<47M]/bestvideo[filesize<47M]+bestaudio/best[filesize<47M]/best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "noplaylist": True,
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
        ],
        "prefer_ffmpeg": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fname = ydl.prepare_filename(info)
            # اختر الناتج النهائي الأنسب
            candidates = []
            base, _ = os.path.splitext(fname)
            for ext in (".mp4",".m4a",".webm",".mp3",".mkv"):
                p = Path(base + ext)
                if p.exists() and p.is_file():
                    candidates.append(p)
            # رجّع أول ملف حجمه ضمن الحد
            for p in candidates:
                if p.stat().st_size <= MAX_UPLOAD_BYTES:
                    return p
            # إن كان كبيراً جداً، حاول صوت فقط
            ydl_opts_audio = ydl_opts | {"format": "bestaudio[filesize<47M]/bestaudio", "merge_output_format": "m4a", "postprocessors": []}
            with yt_dlp.YoutubeDL(ydl_opts_audio) as y2:
                info2 = y2.extract_info(url, download=True)
                fname2 = y2.prepare_filename(info2)
                for ext2 in (".m4a",".mp3",".webm"):
                    p2 = Path(os.path.splitext(fname2)[0] + ext2)
                    if p2.exists() and p2.is_file() and p2.stat().st_size <= MAX_UPLOAD_BYTES:
                        return p2
    except Exception as e:
        log.error("[ydl] %s", e)
        return None
    return None

# ===== TG Download helper =====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ===== File tools =====
def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        images = []
        for p in image_paths:
            im = Image.open(p)
            if im.mode in ("RGBA","P"): im = im.convert("RGB")
            images.append(im)
        if not images: return None
        out_path = TMP_DIR / f"images_{int(time.time())}.pdf"
        first, rest = images[0], images[1:]
        first.save(out_path, "PDF", save_all=True, append_images=rest)
        return out_path
    except Exception as e:
        log.error("[img->pdf] %s", e); return None

def compress_image(image_path: Path, quality: int = 75) -> Path|None:
    try:
        im = Image.open(image_path)
        out_path = TMP_DIR / f"compressed_{image_path.stem}.jpg"
        im.convert("RGB").save(out_path, "JPEG", optimize=True, quality=max(1, min(quality, 95)))
        return out_path
    except Exception as e:
        log.error("[compress] %s", e); return None

# ===== Commands & Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    user_get(uid)

    # شاشة اختيار اللغة أول مرة
    if update.message:
        await update.message.reply_text(
            TR["en"]["lang_choose_title_en"] + "\n" + TR["ar"]["lang_choose_title_ar"],
            reply_markup=lang_keyboard()
        )
    else:
        await context.bot.send_message(chat_id, TR["en"]["lang_choose_title_en"] + "\n" + TR["ar"]["lang_choose_title_ar"], reply_markup=lang_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(t(uid,"menu"), reply_markup=bottom_menu_kb(uid))

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()

    # تغيير اللغة
    if q.data.startswith("lang_"):
        code = q.data.replace("lang_","")
        if code not in ("ar","en"):
            await safe_edit(q, t(uid,"not_supported_lang") + "\n\n" + t(uid,"menu"), kb=bottom_menu_kb(uid))
            prefs_set_lang(uid, "en")
        else:
            prefs_set_lang(uid, code)
            # رسالة ترحيب + قائمة
            try:
                if Path(WELCOME_PHOTO).exists():
                    with open(WELCOME_PHOTO, "rb") as f:
                        await q.message.reply_photo(InputFile(f), caption=TR[code]["welcome"])
                else:
                    await q.message.reply_text(TR[code]["welcome"])
            except Exception as e:
                log.warning("[welcome] %s", e)
            await safe_edit(q, t(uid,"menu"), kb=bottom_menu_kb(uid))
            await q.message.reply_text(t(uid,"sections"), reply_markup=sections_list_kb(uid))
        return

    # تحقق اشتراك
    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, t(uid,"menu"), kb=bottom_menu_kb(uid))
            await q.message.reply_text(t(uid,"sections"), reply_markup=sections_list_kb(uid))
        else:
            await safe_edit(q, t(uid,"need_join"), kb=gate_kb(uid))
            await q.message.reply_text(t(uid,"need_admin_text"))
        return

    # بوابة اشتراك
    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, t(uid,"need_join"), kb=gate_kb(uid)); return

    # أزرار أساسية
    if q.data == "myinfo":
        u = user_get(uid)
        await safe_edit(q, t(uid,"my_info", name=q.from_user.full_name, uid=uid, lang=u.get("pref_lang","ar").upper()), kb=bottom_menu_kb(uid)); return
    if q.data == "change_lang":
        await safe_edit(q, TR["en"]["lang_choose_title_en"] + "\n" + TR["ar"]["lang_choose_title_ar"], kb=lang_keyboard()); return
    if q.data == "back_home":
        await safe_edit(q, t(uid,"menu"), kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections":
        await safe_edit(q, t(uid,"sections"), kb=sections_list_kb(uid)); return

    if q.data == "vip_badge":
        u = user_get(uid)
        since = u.get("vip_since", 0); since_txt = time.strftime('%Y-%m-%d', time.gmtime(since)) if since else "N/A"
        await safe_edit(q, f"{t(uid,'vip_badge')}\n{since_txt}", kb=bottom_menu_kb(uid)); return

    if q.data == "upgrade":
        if user_is_premium(uid) or uid == OWNER_ID:
            await safe_edit(q, t(uid,"vip_badge"), kb=bottom_menu_kb(uid)); return
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        await safe_edit(q, t(uid,"pay_create_wait", ref=ref), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]]))
        try:
            if USE_PAYLINK_API and PAYLINK_API_ID and PAYLINK_API_SECRET:
                # auth + invoice
                token = await paylink_auth_token()
                pay_url, _ = await paylink_create_invoice(ref, VIP_PRICE_SAR, q.from_user.full_name or "Telegram User")
            else:
                pay_url = _build_pay_link(ref)
            await safe_edit(q,
                f"💳 VIP ({VIP_PRICE_SAR:.2f} SAR)\n🔖 <code>{ref}</code>",
                kb=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t(uid,"go_pay"), url=pay_url)],
                    [InlineKeyboardButton(t(uid,"verify_pay"), callback_data=f"verify_pay_{ref}")],
                    [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
                ])
            )
        except Exception as e:
            log.error("[upgrade] %s", e)
            await safe_edit(q, "Payment unavailable now.", kb=sections_list_kb(uid))
        return

    if q.data.startswith("verify_pay_"):
        ref = q.data.replace("verify_pay_","")
        st = payments_status(ref)
        if st == "paid" or user_is_premium(uid):
            await safe_edit(q, t(uid,"vip_active"), kb=bottom_menu_kb(uid))
        else:
            await safe_edit(q, "⌛ Not paid yet.", kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(uid,"verify_pay"), callback_data=f"verify_pay_{ref}")],
                [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
            ]))
        return

    # الأقسام
    if q.data == "sec_ai":
        ai_set_mode(uid, None)
        await safe_edit(q, t(uid,"ai_tools"), kb=ai_tools_kb(uid)); return
    if q.data == "sec_media":
        ai_set_mode(uid, "media_menu")
        await safe_edit(q, t(uid,"media"), kb=media_kb(uid)); return
    if q.data == "sec_security":
        ai_set_mode(uid, "security_menu")
        await safe_edit(q, t(uid,"security"), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid,"link_scan"), callback_data="btn_link_scan"),
             InlineKeyboardButton(t(uid,"email_check"), callback_data="btn_email_check")],
            [InlineKeyboardButton(t(uid,"geo"), callback_data="btn_geo")],
            [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
        ])); return
    if q.data == "sec_files":
        ai_set_mode(uid, "file_tools_menu")
        await safe_edit(q, t(uid,"files"), kb=files_kb(uid)); return
    if q.data == "sec_courses":
        ai_set_mode(uid, "courses_menu")
        await safe_edit(q, t(uid,"courses_menu"), kb=courses_kb(uid)); return
    if q.data == "sec_unban":
        ai_set_mode(uid, "unban_menu")
        await safe_edit(q, t(uid,"unban_menu"), kb=unban_kb(uid)); return

    # AI tools actions
    if q.data == "ai_chat":
        if not AI_ENABLED or client is None:
            await safe_edit(q, t(uid,"ai_disabled"), kb=ai_tools_kb(uid)); return
        ai_set_mode(uid, "ai_chat"); await safe_edit(q, t(uid,"ai_chat_on"), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(uid,"ai_stop"), callback_data="ai_stop")],
            [InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]
        ])); return
    if q.data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, t(uid,"ai_chat_off"), kb=ai_tools_kb(uid)); return
    if q.data == "ai_writer":
        ai_set_mode(uid, "writer"); await safe_edit(q, t(uid,"writer"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "ai_stt":
        ai_set_mode(uid, "stt"); await safe_edit(q, t(uid,"send_voice_or_audio"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "ai_tr":
        u = user_get(uid)
        ai_set_mode(uid, "translate", {"to": u.get("pref_lang","ar")})
        msg = t(uid,"send_text_for_translate", img=(" أو صورة" if OPENAI_VISION and _lang(uid)=="ar" else " or image" if OPENAI_VISION else ""), to=u.get("pref_lang","ar").upper())
        await safe_edit(q, msg, kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "ai_img":
        ai_set_mode(uid, "image_ai"); await safe_edit(q, t(uid,"img_ai"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "ai_geo":
        ai_set_mode(uid, "geo_ip"); await safe_edit(q, t(uid,"send_ip_or_host"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return

    # Security buttons
    if q.data == "btn_link_scan":
        ai_set_mode(uid, "link_scan"); await safe_edit(q, t(uid,"send_link_scan"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "btn_email_check":
        ai_set_mode(uid, "email_check"); await safe_edit(q, t(uid,"send_email_check"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "btn_geo":
        ai_set_mode(uid, "geo_ip"); await safe_edit(q, t(uid,"send_ip_or_host"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return

    # Media
    if q.data == "media_dl":
        ai_set_mode(uid, "media_dl"); await safe_edit(q, t(uid,"send_media_url"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return

    # Files
    if q.data == "file_pdf":
        ai_set_mode(uid, "file_img_to_pdf"); await safe_edit(q, t(uid,"send_image_to_pdf"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return
    if q.data == "file_compress":
        ai_set_mode(uid, "file_img_compress"); await safe_edit(q, t(uid,"send_image_to_compress"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"back"), callback_data="back_sections")]])); return

    # Courses
    if q.data.startswith("course_"):
        code = q.data
        if code == "course_py":
            url = "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250814%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250814T023808Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=d75356d7e59f7c55d29c07f605699f0348e5f078b6ceb421107c9f3202f545b1"
        elif code == "course_cyber0":
            url = "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250814%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250814T023837Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=137e2e87efb7f47e5c5f07c949a7ed7a90e392b3b4c2338e536b416cf23e1ac2"
        else:
            url = "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A"
        await safe_edit(q, t(uid,"open_link") + f"\n{url}", kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"open_link"), url=url)],[InlineKeyboardButton(t(uid,"back"), callback_data="sec_courses")]]))
        return

    # Unban templates
    if q.data.startswith("unban_"):
        svc = q.data.split("_",1)[1]
        if svc == "ig":
            supp = "https://help.instagram.com/contact/606967319425038"
            template = (
                "Subject: Appeal against mistaken ban\n\n"
                "Hello Instagram Support,\nMy account was disabled mistakenly. I confirm I did not violate the Community Guidelines. "
                "Please review my account and restore access.\nUsername: @YOUR_USERNAME\nEmail: YOUR_EMAIL\nAny additional info: ...\n\nThanks."
            )
        elif svc == "fb":
            supp = "https://www.facebook.com/help/contact/260749603972907"
            template = (
                "Subject: Review request for disabled account\n\n"
                "Hello Facebook Team,\nI believe my account was disabled by mistake. I always follow the Community Standards. "
                "Please review and reactivate it.\nFull name: ...\nProfile link/ID: ...\nEmail/Phone: ...\n\nThanks."
            )
        elif svc == "tg":
            supp = UNBAN_TG_LINK
            template = (
                "Hello Telegram,\nMy account was restricted due to spam by mistake. I respect the Terms of Service and will avoid any automated actions. "
                "Please remove the restriction.\nPhone: +...\nDetails: ...\n\nThanks."
            )
        else:
            supp = "https://www.epicgames.com/help/en-US/contact-us"
            template = (
                "Subject: Ban appeal\n\n"
                "Hello Epic Games Support,\nI believe my account ban was a mistake. Please review my case.\nAccount email: ...\nDisplay name: ...\nDetails: ...\n\nThanks."
            )
        await q.message.reply_text(f"📋\n{template}")
        await safe_edit(q, t(uid,"template_sent"), kb=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"open_link"), url=supp)],[InlineKeyboardButton(t(uid,"back"), callback_data="sec_unban")]]))
        return

# ===== Messages handler =====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text(t(uid,"need_join"), reply_markup=gate_kb(uid)); return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # نص
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()

        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(uid, text), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t(uid,"ai_stop"), callback_data="ai_stop")]])); return

        if mode == "geo_ip":
            target = text
            query = target
            if _HOST_RE.match(target):
                ip = resolve_ip(target)
                if ip: query = ip
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(uid, data), parse_mode="HTML"); return

        if mode == "writer":
            out = await ai_write(uid, text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "translate":
            to = (extra or {}).get("to","ar")
            out = await translate_text(uid, text, to)
            await update.message.reply_text(out); return

        if mode == "link_scan":
            out = await link_scan(text, uid)
            await update.message.reply_text(out, parse_mode="HTML", disable_web_page_preview=True); return

        if mode == "email_check":
            if "@" in text and "." in text:
                out = await osint_email(text, uid)
            else:
                out = await osint_username(text)
            await update.message.reply_text(out, parse_mode="HTML"); return

        if mode == "media_dl":
            if not _URL_RE.search(text):
                await update.message.reply_text(t(uid,"send_media_url")); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            path = await download_media(text)
            if path and path.exists() and path.stat().st_size <= MAX_UPLOAD_BYTES:
                try:
                    await update.message.reply_document(document=InputFile(str(path)))
                    await update.message.reply_text(t(uid,"media_ready"))
                except Exception:
                    await update.message.reply_text("⚠️ failed to send file")
            else:
                await update.message.reply_text("⚠️ cannot download or file too large.")
            return

        if mode == "file_tools_menu":
            await update.message.reply_text(t(uid,"files"), reply_markup=files_kb(uid)); return

        if mode == "numbers":
            await update.message.reply_text(TEMP_NUMBERS_LINK); return

        if mode in ("file_img_to_pdf", "file_img_compress"):
            await update.message.reply_text("📌 " + (t(uid,"send_image_to_pdf") if mode=="file_img_to_pdf" else t(uid,"send_image_to_compress"))); return

    # صوت
    if msg.voice or msg.audio:
        if ai_get_mode(uid)[0] == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            if not (AI_ENABLED and client):
                await update.message.reply_text(t(uid,"ai_disabled")); return
            try:
                with open(p, "rb") as f:
                    resp = client.audio.transcriptions.create(model="whisper-1", file=f)
                text = getattr(resp, "text","").strip() or "⚠️"
                await update.message.reply_text(text)
            except Exception as e:
                log.error("[whisper] %s", e)
                await update.message.reply_text("⚠️ STT failed.")
            return

    # صورة
    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")

        if mode == "translate" and OPENAI_VISION:
            out = await translate_image_file(uid, str(p), (extra or {}).get("to","ar"))
            await update.message.reply_text(out or "⚠️"); return

        if mode == "file_img_compress":
            outp = compress_image(p)
            if outp and outp.exists():
                await update.message.reply_document(InputFile(str(outp)))
            else:
                await update.message.reply_text("⚠️ compression failed.")
            return

        if mode == "file_img_to_pdf":
            st_paths = (extra or {}).get("paths", [])
            st_paths.append(str(p))
            ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
            await update.message.reply_text(f"✅ {len(st_paths)} image(s) added. /makepdf to export.")
            return

    # مستند (قد تكون صورة مرسلة كمستند)
    if msg.document:
        if mode in ("file_img_to_pdf","file_img_compress"):
            p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=f"_{msg.document.file_name or ''}")
            if mode == "file_img_compress":
                outp = compress_image(p)
                if outp and outp.exists():
                    await update.message.reply_document(InputFile(str(outp)))
                else:
                    await update.message.reply_text("⚠️ compression failed.")
                return
            if mode == "file_img_to_pdf":
                st_paths = (extra or {}).get("paths", [])
                st_paths.append(str(p))
                ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
                await update.message.reply_text(f"✅ {len(st_paths)} file(s) added. /makepdf to export.")
                return

    # افتراضي
    await update.message.reply_text(t(uid,"menu"), reply_markup=bottom_menu_kb(uid))

# makepdf
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("Use /file then choose Image → PDF."); return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("No images yet. Send images then /makepdf."); return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ Failed to build PDF or too large.")
    ai_set_mode(uid, "file_tools_menu", {})

# ===== Owner / admin =====
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text(f"✅ VIP granted to {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text(f"❌ VIP revoked for {context.args[0]}")

async def vipinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /vipinfo <user_id>"); return
    u = user_get(context.args[0])
    await update.message.reply_text(json.dumps(u, ensure_ascii=False, indent=2))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ Commands refreshed.")

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
        await update.message.reply_text("No payments."); return
    txt = []
    for r in rows:
        ts = time.strftime('%Y-%m-%d %H:%M', time.gmtime(r.get('created_at') or 0))
        txt.append(f"ref={r['ref']}  user={r['user_id']}  {r['status']}  at={ts}")
    await update.message.reply_text("\n".join(txt))

async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 Restarting…"); os._exit(0)

# Paylink helpers
_paylink_token = None
_paylink_token_exp = 0
async def paylink_auth_token():
    global _paylink_token, _paylink_token_exp
    now = time.time()
    if _paylink_token and _paylink_token_exp > now + 10: return _paylink_token
    url = f"{PAYLINK_API_BASE}/auth"
    payload = {"apiId": PAYLINK_API_ID, "secretKey": PAYLINK_API_SECRET, "persistToken": False}
    async with aiohttp.ClientSession() as s:
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
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=30) as r:
            data = await r.json(content_type=None)
            if r.status >= 400: raise RuntimeError(f"addInvoice failed: {data}")
            pay_url = data.get("url") or data.get("mobileUrl") or data.get("qrUrl")
            if not pay_url: raise RuntimeError(f"addInvoice failed: {data}")
            return pay_url, data

# Errors
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# Main
def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())

    # public minimal commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))

    # owner
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("vipinfo", vipinfo))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
    app.add_handler(CommandHandler("paylist", paylist))
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






