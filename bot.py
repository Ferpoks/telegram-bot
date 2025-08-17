# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, hashlib, socket, tempfile, subprocess, shutil
from pathlib import Path
from html import escape as _escape

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

# ===== OpenAI (اختياري) =====
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

# ===== HTTP / IO =====
import aiohttp
from PIL import Image
from dotenv import load_dotenv

try:
    import whois as pywhois
except Exception:
    pywhois = None
try:
    import dns.resolver as dnsresolver
    import dns.exception as dnsexception
except Exception:
    dnsresolver = None

# لعمليات PDF->Word محلياً
try:
    from pdf2docx import Converter as _PDF2DOCX_Converter
except Exception:
    _PDF2DOCX_Converter = None

# لتحويل Word->PDF عبر ConvertAPI (يتطلب CONVERTAPI_SECRET)
try:
    import convertapi as _convertapi
except Exception:
    _convertapi = None

# ---- تحميل .env محلياً (ليس مطلوباً في Render لو المتغيرات موجودة) ----
if Path(".env").exists() and not os.getenv("RENDER"):
    load_dotenv(".env", override=True)

# ===== إعدادات عامة =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

# قاعدة البيانات بمسار قابل للكتابة على Render
DB_PATH = os.getenv("DB_PATH", "./data/bot.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

# تفعيل/تعطيل بعض الوحدات عبر بيئة
FILES_ENABLED = os.getenv("FILES_ENABLED", "1") == "1"

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION = os.getenv("OPENAI_VISION", "0") == "1"
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
_openai = None
def _ensure_openai():
    global _openai
    if _openai is None and AI_ENABLED and OpenAI is not None:
        try:
            _openai = OpenAI(api_key=OPENAI_API_KEY)
        except Exception as e:
            log.error("[openai-init] %s", e)

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ferpo_ksa").strip().lstrip("@")
def admin_button_url() -> str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# قنوات التحقق
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(",") if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

# الترحيب
WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")  # احتياطي
WELCOME_ANIMATION = os.getenv("WELCOME_ANIMATION","").strip()    # يفضّل mp4/gif/webm. webp يتحول (لو فيه ffmpeg)

# دفع/VIP
PAY_WEBHOOK_ENABLE = os.getenv("PAY_WEBHOOK_ENABLE", "1") == "1"
PAY_WEBHOOK_SECRET = os.getenv("PAY_WEBHOOK_SECRET", "").strip()
PAYLINK_API_BASE   = os.getenv("PAYLINK_API_BASE", "https://restapi.paylink.sa/api").rstrip("/")
PAYLINK_API_ID     = os.getenv("PAYLINK_API_ID", "").strip()
PAYLINK_API_SECRET = os.getenv("PAYLINK_API_SECRET", "").strip()
PUBLIC_BASE_URL    = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
VIP_PRICE_SAR      = float(os.getenv("VIP_PRICE_SAR", "10"))
USE_PAYLINK_API    = os.getenv("USE_PAYLINK_API", "1") == "1"
PAYLINK_CHECKOUT_BASE = (os.getenv("PAYLINK_CHECKOUT_BASE") or "").strip()

# الأمن (مفاتيح خارجية)
URLSCAN_API_KEY = (os.getenv("URLSCAN_API_KEY") or "").strip()
KICKBOX_API_KEY = (os.getenv("KICKBOX_API_KEY") or "").strip()
IPINFO_TOKEN    = (os.getenv("IPINFO_TOKEN") or "").strip()

# الروابط
FOLLOWERS_LINKS = [u for u in [
    os.getenv("FOLLOW_LINK_1","https://smmcpan.com/"),
    os.getenv("FOLLOW_LINK_2","https://saudifollow.com/"),
    os.getenv("FOLLOW_LINK_3","https://drd3m.me/"),
] if u]

# الألعاب والاشتراكات
GAMES_LINKS = [
    ("G2A",     os.getenv("GAMES_G2A",    "https://www.g2a.com/")),
    ("Kinguin", os.getenv("GAMES_KINGUIN","https://www.kinguin.net/")),
    ("GAMIVO",  os.getenv("GAMES_GAMIVO", "https://www.gamivo.com/")),
    ("Eneba",   os.getenv("GAMES_ENEBA",  "https://www.eneba.com/")),
]

# Adobe (Windows)
ADOBE_DOC_URL = os.getenv("ADOBE_WIN_URL", "https://docs.google.com/document/d/1gEbrkUBi0SPd69X1XPnbh8RnaE6_IrKD9f95iXbFXV4/edit?tab=t.0#heading=h.atsysbnclvpy")

# الدورات (وضع أي روابط موقّتة عن طريق ENV براحـتك)
COURSE_PYTHON_URL = os.getenv("COURSE_PYTHON_URL","https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf")
COURSE_CYBER_URL  = os.getenv("COURSE_CYBER_URL","https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf")
COURSE_EH_URL     = os.getenv("COURSE_EH_URL","https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A")
COURSE_ECOM_URL   = os.getenv("COURSE_ECOM_URL","https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?hl=ar")

DARK_GPT_URL = os.getenv("DARK_GPT_URL", "https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X")

MAX_UPLOAD_MB = 47
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

CHANNEL_ID = None

# ===== Health/Webhook server =====
SERVE_HEALTH = os.getenv("SERVE_HEALTH","1") == "1" or PAY_WEBHOOK_ENABLE
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _public_url(path: str) -> str:
    base = PUBLIC_BASE_URL or ""
    if not base:
        host = os.getenv("RENDER_EXTERNAL_HOSTNAME","").strip()
        if host: base = f"https://{host}"
    return (base or "").rstrip("/") + path

async def _payhook_aiohttp(request):
    if PAY_WEBHOOK_SECRET and request.headers.get("X-PL-Secret") != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok": False, "error":"bad secret"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {"raw": await request.text()}
    def _find(obj):
        if isinstance(obj, dict):
            for k in ("orderNumber","merchantOrderNumber","merchantOrderNo","ref","reference","customerRef","customerReference"):
                if k in obj and str(obj[k]).strip():
                    return str(obj[k]).strip()
            for v in obj.values():
                r = _find(v)
                if r: return r
        elif isinstance(obj, list):
            for v in obj:
                r = _find(v)
                if r: return r
        else:
            s = str(obj); m = re.search(r"(\d{6,}-\d{9,})", s)
            if m: return m.group(1)
        return None
    ref = _find(data)
    if not ref:
        return web.json_response({"ok": False, "error":"no-ref"}, status=200)
    activated = payments_mark_paid_by_ref(ref, raw=data)
    return web.json_response({"ok": True, "ref": ref, "activated": bool(activated)}, status=200)

def _run_http_server():
    if not SERVE_HEALTH: return
    host, port = "0.0.0.0", int(os.getenv("PORT","10000"))
    if AIOHTTP_AVAILABLE:
        async def _start():
            app = web.Application()
            async def _ok(_): return web.json_response({"ok":True})
            app.router.add_get("/", _ok); app.router.add_get("/health", _ok)
            if PAY_WEBHOOK_ENABLE:
                app.router.add_post("/payhook", _payhook_aiohttp)
                app.router.add_get("/payhook", _ok)
            runner = web.AppRunner(app); await runner.setup()
            site = web.TCPSite(runner, host, port); await site.start()
            log.info("[http] serving on %s:%d", host, port)
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        loop.run_until_complete(_start())
        threading.Thread(target=loop.run_forever, daemon=True).start()
    else:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class _H(BaseHTTPRequestHandler):
            def _send(self, code=200, body=b'{"ok":true}'):
                self.send_response(code); self.send_header("Content-Type","application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path in ("/","/health"): self._send(200)
                else: self._send(404, b'{"ok":false}')
        HTTPServer((host,port), _H).serve_forever()
_run_http_server()

# ===== ffmpeg helpers =====
def _ensure_bin_on_path():
    b = Path.cwd()/ "bin"
    if b.exists(): os.environ["PATH"] = f"{str(b)}:{os.environ.get('PATH','')}"
_ensure_bin_on_path()
def ffmpeg_path(): p=shutil.which("ffmpeg"); return p or (str(Path.cwd()/ "bin"/"ffmpeg") if (Path.cwd()/ "bin"/"ffmpeg").exists() else None)
FFMPEG = ffmpeg_path()
if FFMPEG: log.info("[ffmpeg] FOUND at %s", FFMPEG)
else:      log.warning("[ffmpeg] MISSING (WEBP animation won’t convert)")

async def fetch_to_tmp(url: str, suffix: str) -> Path:
    fd, tmp = tempfile.mkstemp(prefix="dl_", suffix=suffix, dir=str(TMP_DIR)); os.close(fd)
    p = Path(tmp)
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=60) as r:
            r.raise_for_status()
            p.write_bytes(await r.read())
    return p

def animated_webp_to_mp4(webp: Path) -> Path|None:
    if not FFMPEG: return None
    out = TMP_DIR / f"anim_{int(time.time())}.mp4"
    try:
        cmd = [FFMPEG, "-y", "-i", str(webp), "-movflags","faststart","-pix_fmt","yuv420p",
               "-vf","scale=trunc(iw/2)*2:trunc(ih/2)*2","-loop","0","-t","4", str(out)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out if out.exists() else None
    except Exception as e:
        log.error("[webp->mp4] %s", e); return None

async def send_welcome_media(bot, chat_id: int):
    url = WELCOME_ANIMATION
    if url:
        try:
            low = url.lower()
            if any(low.endswith(ext) for ext in (".mp4",".gif",".webm",".m4v")):
                await bot.send_animation(chat_id, animation=url, disable_notification=True); return
            if low.endswith(".webp"):
                dl = await fetch_to_tmp(url, ".webp")
                mp4 = animated_webp_to_mp4(dl)
                if mp4:
                    await bot.send_animation(chat_id, animation=InputFile(str(mp4)), disable_notification=True); return
        except Exception as e:
            log.warning("[welcome] %s", e)
    # fallback
    if WELCOME_PHOTO and (WELCOME_PHOTO.startswith("http") or Path(WELCOME_PHOTO).exists()):
        await bot.send_photo(chat_id, photo=(WELCOME_PHOTO if WELCOME_PHOTO.startswith("http") else InputFile(WELCOME_PHOTO)), disable_notification=True)

# ===== i18n =====
def T(key: str, lang="ar", **kw) -> str:
    AR = {
        "start_pick_lang": "اختر لغتك:",
        "lang_ar": "العربية", "lang_en": "English",
        "hello_name": "مرحباً {name} 👋\nهذا بوت فيربوكس — فيه: 🤖 ذكاء اصطناعي (VIP), 🛡️ أمن (VIP), 🧰 خدمات (Adobe + ألعاب), 🎓 دورات, 📈 رشق.",
        "main_menu": "👇 القائمة الرئيسية",
        "btn_myinfo":"👤 معلوماتي","btn_lang":"🌐 تغيير اللغة","btn_vip":"⭐ حساب VIP","btn_contact":"📨 تواصل مع الإدارة","btn_sections":"📂 الأقسام",
        "sections":"📂 الأقسام",
        "sec_ai":"🤖 أدوات الذكاء الاصطناعي (VIP)","sec_security":"🛡️ الأمن (VIP)","sec_services":"🧰 الخدمات","sec_unban":"🚫 فك الباند","sec_courses":"🎓 الدورات","sec_boost":"📈 رشق متابعين","sec_darkgpt":"🕶️ Dark GPT (VIP)",
        "vip_only":"هذه الميزة للمشتركين VIP فقط.","go_pay":"🚀 ترقية VIP","back":"↩️ رجوع",
        "page_services":"🧰 الخدمات:","btn_games":"🎮 الألعاب والاشتراكات","btn_adobe":"🅰️ Adobe (Windows)","games_list":"اختر موقعاً:","adobe_open":"سيفتح مستند برامج Adobe لويندوز.",
        "page_courses":"🎓 الدورات:","course_python":"بايثون من الصفر","course_cyber":"الأمن السيبراني من الصفر","course_eh":"الهكر الأخلاقي","course_ecom":"التجارة الإلكترونية",
        "page_boost":"📈 رشق متابعين:","boost_desc":"روابط لخدمات زيادة المتابعين (استخدمها بمسؤولية).",
        "unban_desc":"اختر المنصة للحصول على رسالة قوية لرفع الحظر (انسخها وقدّمها للدعم):",
        "ai_chat_on":"🤖 وضع الدردشة مفعّل. اكتب سؤالك.","ai_chat_off":"🔚 تم إنهاء وضع الذكاء الاصطناعي.","send_text":"أرسل النص الآن…",
        "security_send_url":"🛡️ أرسل الرابط للفحص.","security_send_email":"✉️ أرسل الإيميل للفحص.","security_send_geo":"📍 أرسل IP أو دومين.",
        "vip_status_on":"⭐ حسابك VIP (مدى الحياة).","gate_join":"🔐 انضم للقناة لاستخدام البوت:","verify":"✅ تحقّق","verify_done":"👌 تم التحقق.","not_verified":"❗️ غير متحقق.",
        "page_files":"🗂️ أدوات الملفات:","btn_jpg2pdf":"JPG → PDF (محلي)","btn_pdf2word_local":"PDF → Word (محلي)","btn_word2pdf":"Word → PDF (ConvertAPI)","btn_img2png":"صورة → PNG","btn_img2webp":"صورة → WEBP",
    }
    EN = {
        "start_pick_lang":"Pick your language:","lang_ar":"العربية","lang_en":"English",
        "hello_name":"Welcome {name} 👋\nFerpoks Bot includes: 🤖 AI (VIP), 🛡️ Security (VIP), 🧰 Services (Adobe + Games), 🎓 Courses, 📈 Growth.",
        "main_menu":"👇 Main menu",
        "btn_myinfo":"👤 My info","btn_lang":"🌐 Change language","btn_vip":"⭐ VIP Account","btn_contact":"📨 Contact Admin","btn_sections":"📂 Sections",
        "sections":"📂 Sections",
        "sec_ai":"🤖 AI Tools (VIP)","sec_security":"🛡️ Security (VIP)","sec_services":"🧰 Services","sec_unban":"🚫 Unban","sec_courses":"🎓 Courses","sec_boost":"📈 Followers","sec_darkgpt":"🕶️ Dark GPT (VIP)",
        "vip_only":"VIP only feature.","go_pay":"🚀 Upgrade VIP","back":"↩️ Back",
        "page_services":"🧰 Services:","btn_games":"🎮 Games & Subscriptions","btn_adobe":"🅰️ Adobe (Windows)","games_list":"Pick a store:","adobe_open":"Opens the Adobe (Windows) document.",
        "page_courses":"🎓 Courses:","course_python":"Python from Zero","course_cyber":"Cybersecurity from Zero","course_eh":"Ethical Hacking","course_ecom":"E-commerce",
        "page_boost":"📈 Followers:","boost_desc":"Growth sites (use responsibly).",
        "unban_desc":"Pick a platform to copy a strong unban message:",
        "ai_chat_on":"🤖 Chat mode enabled.","ai_chat_off":"🔚 Chat mode stopped.","send_text":"Send your text…",
        "security_send_url":"🛡️ Send a URL to scan.","security_send_email":"✉️ Send an email to check.","security_send_geo":"📍 Send an IP or domain.",
        "page_files":"🗂️ File Tools:","btn_jpg2pdf":"JPG → PDF (local)","btn_pdf2word_local":"PDF → Word (local)","btn_word2pdf":"Word → PDF (ConvertAPI)","btn_img2png":"Image → PNG","btn_img2webp":"Image → WEBP",
    }
    D = AR if lang=="ar" else EN
    kw = {k:_escape(str(v)) for k,v in kw.items()}
    s = D.get(key, key)
    try: return s.format(**kw)
    except Exception: return s

# ===== DB =====
_conn_lock = threading.RLock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn: return conn
    conn = sqlite3.connect(DB_PATH, check_same_thread=False); conn.row_factory = sqlite3.Row
    _db._conn = conn; log.info("[db] %s", DB_PATH); return conn

def migrate_db():
    with _conn_lock:
        _db().execute("""CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0,
          vip_forever INTEGER DEFAULT 0,
          vip_since INTEGER DEFAULT 0,
          pref_lang TEXT DEFAULT 'ar'
        );""")
        _db().execute("""CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT,
          extra TEXT,
          updated_at INTEGER
        );""")
        _db().execute("""CREATE TABLE IF NOT EXISTS payments (
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

def user_get(uid) -> dict:
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT * FROM users WHERE id=?", (str(uid),))
        r=c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?)",(str(uid),)); _db().commit()
            return {"id":str(uid),"premium":0,"verified_ok":0,"verified_at":0,"vip_forever":0,"vip_since":0,"pref_lang":"ar"}
        return dict(r)
def user_set_verify(uid, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?", (1 if ok else 0, int(time.time()), str(uid))); _db().commit()
def user_is_premium(uid) -> bool:
    u=user_get(uid); return bool(u.get("premium") or u.get("vip_forever"))
def user_grant(uid):
    now=int(time.time())
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0),?) WHERE id=?",(now,str(uid))); _db().commit()
def user_revoke(uid):
    with _conn_lock: _db().execute("UPDATE users SET premium=0, vip_forever=0 WHERE id=?", (str(uid),)); _db().commit()
def prefs_set_lang(uid, lang):
    with _conn_lock: _db().execute("UPDATE users SET pref_lang=? WHERE id=?", (lang, str(uid))); _db().commit()
def ai_set_mode(uid, mode: str|None, extra: dict|None=None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state (user_id,mode,extra,updated_at) VALUES (?,?,?,strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, extra=excluded.extra, updated_at=strftime('%s','now')",
            (str(uid), mode, json.dumps(extra or {}, ensure_ascii=False))
        ); _db().commit()
def ai_get_mode(uid):
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT mode, extra FROM ai_state WHERE user_id=?", (str(uid),))
        r=c.fetchone()
        if not r: return None, {}
        try: extra=json.loads(r["extra"] or "{}")
        except Exception: extra={}
        return r["mode"], extra

# ===== Payments =====
def payments_new_ref(uid: int) -> str: return f"{uid}-{int(time.time())}"
def payments_create(uid: int, amount: float, provider="paylink", ref: str|None=None) -> str:
    ref=ref or payments_new_ref(uid)
    with _conn_lock:
        _db().execute("INSERT OR REPLACE INTO payments (ref,user_id,amount,provider,status,created_at) VALUES (?,?,?,?,?,?)",
                      (ref, str(uid), amount, provider, "pending", int(time.time())))
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
        if r["status"]=="paid": user_grant(r["user_id"]); return True
        _db().execute("UPDATE payments SET status='paid', paid_at=?, raw=? WHERE ref=?", (int(time.time()), json.dumps(raw, ensure_ascii=False), ref)); _db().commit()
    user_grant(r["user_id"]); return True

# ===== الأمن =====
_URL_RE = re.compile(r"https?://[^\s]+")
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")

async def http_head(url: str) -> int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=15) as r: return r.status
    except Exception: return None
def resolve_ip(host: str) -> str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for _,_,_,_,sockaddr in infos:
            ip=sockaddr[0]; 
            if ":" not in ip: return ip
        return None
    except Exception: return None
async def fetch_geo(query: str) -> dict|None:
    url=f"http://ip-api.com/json/{query}?fields=status,message,country,regionName,city,isp,org,as,query,lat,lon,timezone,zip,reverse"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r: return await r.json(content_type=None)
    except Exception: return {"status":"fail","message":"network error"}
def fmt_geo(data: dict) -> str:
    if not data or data.get("status")!="success":
        return f"⚠️ {data.get('message','lookup failed') if data else 'lookup failed'}"
    parts = [
        f"🔎 query: <code>{_escape(data.get('query',''))}</code>",
        f"🌍 {data.get('country','?')} — {data.get('regionName','?')}",
        f"🏙️ {data.get('city','?')} — {data.get('zip','-')}",
        f"⏰ {data.get('timezone','-')}",
        f"📡 ISP/ORG: {data.get('isp','-')} / {data.get('org','-')}",
        f"🛰️ AS: {data.get('as','-')}",
        f"📍 {data.get('lat','?')}, {data.get('lon','?')}",
    ]
    if data.get("reverse"): parts.append(f"🔁 Reverse: {_escape(str(data['reverse']))}")
    return "\n".join(parts)
def is_valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", e or ""))
def md5_hex(s: str) -> str: return hashlib.md5(s.strip().lower().encode()).hexdigest()

async def urlscan_lookup(u: str) -> str:
    if not URLSCAN_API_KEY: return "ℹ️ ضع URLSCAN_API_KEY لتفعيل الفحص."
    try:
        headers={"API-Key":URLSCAN_API_KEY,"Content-Type":"application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post("https://urlscan.io/api/v1/scan/", headers=headers, json={"url":u,"visibility":"unlisted"}, timeout=30) as r:
                data = await r.json(content_type=None)
        if r.status==401: return "❌ URLScan: مفتاح غير صالح (401)."
        link=data.get("result") or ""; return f"urlscan: {link or 'submitted'}"
    except Exception as e:
        return f"urlscan error: {e}"
async def kickbox_lookup(email: str) -> str:
    if not KICKBOX_API_KEY: return "ℹ️ ضع KICKBOX_API_KEY لتفعيل فحص الإيميل."
    try:
        params={"email":email, "apikey":KICKBOX_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.kickbox.com/v2/verify", params=params, timeout=20) as r:
                data = await r.json(content_type=None)
        if r.status==401: return "❌ Kickbox: مفتاح غير صالح (401)."
        return f"Kickbox: result={data.get('result')} reason={data.get('reason')}"
    except Exception as e:
        return f"kickbox error: {e}"
async def ipinfo_lookup(query: str) -> str:
    if not IPINFO_TOKEN: return "ℹ️ ضع IPINFO_TOKEN لتفعيل ipinfo."
    try:
        url=f"https://ipinfo.io/{query}?token={IPINFO_TOKEN}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r: data=await r.json(content_type=None)
        if r.status==401: return "❌ ipinfo: مفتاح غير صالح (401)."
        keys=["ip","hostname","city","region","country","loc","org","asn"]
        parts=[f"{k}: {data.get(k,'-')}" for k in keys if k in data]
        return "ipinfo:\n"+"\n".join(parts)
    except Exception as e:
        return f"ipinfo error: {e}"
def whois_domain(domain: str) -> dict|None:
    if pywhois is None: return {"error":"python-whois غير مثبت"}
    try:
        w=pywhois.whois(domain)
        return {
            "domain_name": str(getattr(w,"domain_name",None)),
            "registrar": getattr(w,"registrar",None),
            "creation_date": str(getattr(w,"creation_date",None)),
            "expiration_date": str(getattr(w,"expiration_date",None)),
            "emails": getattr(w,"emails",None),
        }
    except Exception as e:
        return {"error": f"whois error: {e}"}
async def link_scan(u: str) -> str:
    if not _URL_RE.search(u or ""): return "⚠️ أرسل رابط يبدأ بـ http:// أو https://"
    m = re.match(r"https?://([^/]+)", u); host=m.group(1) if m else ""
    ip = resolve_ip(host) if host else None
    status = await http_head(u)
    geo_txt = fmt_geo(await fetch_geo(ip)) if ip else "⚠️ تعذّر حلّ IP للمضيف."
    pieces = [f"🔗 <code>{_escape(u)}</code>", f"المضيف: <code>{_escape(host)}</code>"]
    pieces.append(f"🔎 حالة HTTP: {status if status is not None else 'فشل HEAD'}")
    pieces.append(await urlscan_lookup(u))
    return "\n".join(pieces) + f"\n\n{geo_txt}"

# ===== AI (chat/translate/write/STT) =====
def _chat_with_fallback(messages):
    if not AI_ENABLED or OpenAI is None: return None, "ai_disabled"
    _ensure_openai()
    if _openai is None: return None, "ai_disabled"
    models = [OPENAI_CHAT_MODEL, "gpt-4o-mini","gpt-4.1-mini","gpt-4o","gpt-4.1","gpt-3.5-turbo"]
    last = None
    for m in dict.fromkeys([x for x in models if x]):
        try:
            r = _openai.chat.completions.create(model=m, messages=messages, temperature=0.6, timeout=60)
            return r, None
        except Exception as e:
            s=str(e); last=s
            if "invalid_api_key" in s or "Incorrect API key" in s: return None, "apikey"
            if "insufficient_quota" in s or "exceeded" in s:      return None, "quota"
    return None, (last or "unknown")
def ai_chat_reply(prompt: str) -> str:
    r, err = _chat_with_fallback([
        {"role":"system","content":"أجب بإيجاز ووضوح بالعربية/الإنجليزية."},
        {"role":"user","content":prompt}
    ])
    if err == "ai_disabled": return T("ai_disabled", lang="ar")
    if err == "apikey": return "⚠️ مفتاح OpenAI غير صالح."
    if err == "quota": return "⚠️ الرصيد غير كافٍ."
    return (r.choices[0].message.content or "").strip() if r else "⚠️ تعذّر التنفيذ."
async def translate_auto(text: str) -> str:
    is_ar = bool(re.search(r"[\u0600-\u06FF]", text))
    to_lang = "en" if is_ar else "ar"
    r, err = _chat_with_fallback([
        {"role":"system","content":"You are a precise translator. Keep meaning and formatting."},
        {"role":"user","content": f"Translate to {to_lang}. Keep formatting:\n\n{text}"}
    ])
    if err: return "⚠️ تعذّر الترجمة حالياً."
    out = (r.choices[0].message.content or "").strip()
    if is_ar:
        return f"**Arabic → English**\n\nOriginal (AR):\n{text}\n\nTranslation (EN):\n{out}"
    else:
        return f"**English → Arabic**\n\nOriginal (EN):\n{text}\n\nالترجمة (AR):\n{out}"
async def ai_write(prompt: str) -> str:
    r, err = _chat_with_fallback([
        {"role":"system","content":"Copywriter: اكتب نصاً إعلانياً واضحاً ومقنعاً بعناوين قصيرة وCTA."},
        {"role":"user","content":prompt}
    ])
    if err: return "⚠️ تعذّر التوليد حالياً."
    return (r.choices[0].message.content or "").strip()
async def tts_whisper_from_file(filepath: str) -> str:
    if not AI_ENABLED or OpenAI is None: return T("ai_disabled", lang="ar")
    _ensure_openai(); 
    try:
        with open(filepath, "rb") as f:
            resp = _openai.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "").strip() or "⚠️ لم أستطع استخراج النص."
    except Exception as e:
        log.error("[whisper] %s", e); return "⚠️ تعذّر التحويل."

# ===== Telegram UI =====
def gate_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 " + ("الانضمام للقناة" if lang=="ar" else "Join Channel"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(T("verify", lang=lang), callback_data="verify")]
    ])
def main_menu_kb(uid: int, lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T("btn_myinfo", lang=lang), callback_data="myinfo")],
        [InlineKeyboardButton(T("btn_lang", lang=lang), callback_data="pick_lang")],
        [InlineKeyboardButton(T("btn_vip", lang=lang), callback_data="vip")],
        [InlineKeyboardButton(T("btn_contact", lang=lang), url=admin_button_url())],
        [InlineKeyboardButton(T("btn_sections", lang=lang), callback_data="sections")],
    ])
def sections_kb(lang="ar"):
    rows = [
        [InlineKeyboardButton(T("sec_ai", lang=lang), callback_data="sec_ai")],
        [InlineKeyboardButton(T("sec_security", lang=lang), callback_data="sec_security")],
        [InlineKeyboardButton(T("sec_services", lang=lang), callback_data="sec_services")],
        [InlineKeyboardButton(T("sec_unban", lang=lang), callback_data="sec_unban")],
        [InlineKeyboardButton(T("sec_courses", lang=lang), callback_data="sec_courses")],
        [InlineKeyboardButton(T("sec_boost", lang=lang), callback_data="sec_boost")],
        [InlineKeyboardButton(T("sec_darkgpt", lang=lang), callback_data="sec_darkgpt")],
        [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")],
    ]
    if FILES_ENABLED:
        rows.insert(5, [InlineKeyboardButton(T("page_files", lang=lang), callback_data="sec_files")])
    return InlineKeyboardMarkup(rows)
def vip_only_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T("go_pay", lang=lang), callback_data="vip")],
        [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
    ])
def ai_stop_kb(lang="ar"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 " + ("إنهاء" if lang=="ar" else "Stop"), callback_data="ai_stop")],
        [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
    ])
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("safe_edit: %s", e)

# ===== تحقق الاشتراك =====
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except Exception: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except Exception: pass

_member_cache = {}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int, force=False, retries=3, backoff=0.7) -> bool:
    now=time.time()
    if not force:
        cached=_member_cache.get(user_id)
        if cached and cached[1] > now: return cached[0]
    targets=[CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
    for _ in range(retries):
        for target in targets:
            try:
                cm = await context.bot.get_chat_member(target, user_id)
                ok = getattr(cm, "status", None) in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id]=(True, now+60); user_set_verify(user_id, True); return True
            except Exception as e:
                log.warning("[is_member] %s", e)
        await asyncio.sleep(backoff)
    _member_cache[user_id]=(False, now+60); user_set_verify(user_id, False); return False
async def must_be_member_or_vip(context, user_id: int) -> bool:
    if user_is_premium(user_id) or user_id == OWNER_ID: return True
    return await is_member(context, user_id, retries=3, backoff=0.7)

# ===== قوالب فك الباند (قوية) =====
UNBAN_TEMPLATES = {
"instagram": """Hello Instagram Support,

My account appears to be disabled or restricted by mistake. I have always followed your Community Guidelines, and I believe this was triggered in error (possibly by automated systems).
I kindly request a manual review of my account and restoration of access. I’m ready to provide any additional information you may need.

Thank you for your time and help.""",
"facebook": """Hello Facebook Support,

My account was restricted/disabled in error. I always respect the Community Standards and did not engage in harmful activity.
Please conduct a manual review of my case and reinstate access. I appreciate your assistance.

Thank you.""",
"telegram": """Hello Telegram Support,

My account/channel seems to be limited by mistake. I use Telegram in compliance with the Terms of Service and local laws.
Kindly review my case and lift the restriction. I am available to provide any details required.

Many thanks.""",
"epic": """Hello Epic Games Support,

My account was banned by mistake. I strictly follow your rules and have not engaged in cheating, harassment, or abuse.
Please review my case manually and remove the ban if possible.

Thank you for your support."""
}
UNBAN_LINKS = {
    "instagram": "https://help.instagram.com/contact/606967319425038",
    "facebook":  "https://www.facebook.com/help/contact/260749603972907",
    "telegram":  "https://telegram.org/support",
    "epic":      "https://www.epicgames.com/help/en-US/c4059"
}

# ===== أدوات الملفات =====
def images_to_pdf(image_paths: list[Path]) -> Path|None:
    try:
        imgs=[Image.open(p).convert("RGB") for p in image_paths]
        if not imgs: return None
        out = TMP_DIR / f"images_{int(time.time())}.pdf"
        imgs[0].save(out, save_all=True, append_images=imgs[1:])
        return out
    except Exception as e:
        log.error("[img->pdf] %s", e); return None
def pdf_to_word_local(pdf_path: Path) -> Path|None:
    if _PDF2DOCX_Converter is None: log.error("[pdf2docx] not installed"); return None
    try:
        out = TMP_DIR / f"out_{int(time.time())}.docx"
        cv = _PDF2DOCX_Converter(str(pdf_path)); cv.convert(str(out)); cv.close()
        return out if out.exists() else None
    except Exception as e:
        log.error("[pdf->word] %s", e); return None
async def word_to_pdf_convertapi(doc_path: Path) -> Path|None:
    secret = os.getenv("CONVERTAPI_SECRET","").strip()
    if not secret or _convertapi is None: return None
    try:
        _convertapi.api_secret=secret
        result=_convertapi.convert('pdf', {'File': str(doc_path)})
        out = TMP_DIR / f"out_{int(time.time())}.pdf"
        result.file.save(str(out))
        return out if out.exists() else None
    except Exception as e:
        log.error("[word->pdf] %s", e); return None
def image_to_format(img_path: Path, fmt: str) -> Path|None:
    try:
        im=Image.open(img_path).convert("RGB")
        out=TMP_DIR / f"img_{int(time.time())}.{fmt.lower()}"
        im.save(out, format=fmt.upper()); return out if out.exists() else None
    except Exception as e:
        log.error("[img->%s] %s", fmt, e); return None

# ===== تنزيل من تيليجرام =====
async def tg_download_to_path(bot, file_id: str, suffix: str = "") -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    f = await bot.get_file(file_id)
    fd, tmp_path = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await f.download_to_drive(tmp_path)
    return Path(tmp_path)

# ===== Startup / Commands =====
async def on_startup(app: Application):
    try: await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e: log.warning("delete_webhook: %s", e)
    global CHANNEL_ID
    CHANNEL_ID=None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat=await app.bot.get_chat(f"@{u}"); CHANNEL_ID=chat.id; break
        except Exception as e:
            log.warning("get_chat @%s: %s", u, e)
    try:
        await app.bot.set_my_commands([BotCommand("start","Start"), BotCommand("help","Help")], scope=BotCommandScopeDefault())
        await app.bot.set_my_commands(
            [BotCommand("start","Start"), BotCommand("help","Help"),
             BotCommand("id","ID"), BotCommand("grant","Grant VIP"), BotCommand("revoke","Revoke VIP"),
             BotCommand("vipinfo","VIP Info"), BotCommand("refreshcmds","Refresh"),
             BotCommand("aidiag","AI diag"), BotCommand("libdiag","Libs"), BotCommand("makepdf","Make PDF"), BotCommand("restart","Restart")],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("set_my_commands: %s", e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid=update.effective_user.id; chat_id=update.effective_chat.id
    u=user_get(uid); lang=u.get("pref_lang","ar")
    # وسائط ترحيب
    await send_welcome_media(context.bot, chat_id)
    # ترحيب باسم المستخدم + ملخص
    name = (update.effective_user.username and "@"+update.effective_user.username) or (update.effective_user.first_name or "صديقي")
    await context.bot.send_message(chat_id, T("hello_name", lang=lang, name=name) + "\n\n" + T("main_menu", lang=lang),
                                   reply_markup=main_menu_kb(uid, lang), parse_mode="HTML", disable_web_page_preview=True)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang=user_get(update.effective_user.id).get("pref_lang","ar")
    await update.message.reply_text(T("main_menu", lang=lang), reply_markup=main_menu_kb(update.effective_user.id, lang))

# ===== Buttons =====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q=update.callback_query; uid=q.from_user.id
    u=user_get(uid); lang=u.get("pref_lang","ar")
    await q.answer()

    if q.data in ("set_lang_ar","set_lang_en"):
        new="ar" if q.data.endswith("_ar") else "en"; prefs_set_lang(uid,new)
        await safe_edit(q, T("hello_name", lang=new, name=(q.from_user.username and "@"+q.from_user.username) or (q.from_user.first_name or "Friend")) + "\n\n" + T("main_menu", lang=new), kb=main_menu_kb(uid,new)); return

    if q.data == "pick_lang":
        await safe_edit(q, T("start_pick_lang", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("lang_ar", lang=lang), callback_data="set_lang_ar"),
             InlineKeyboardButton(T("lang_en", lang=lang), callback_data="set_lang_en")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
        ])); return

    if q.data == "verify":
        if await is_member(context, uid, force=True): await safe_edit(q, T("verify_done", lang=lang), kb=main_menu_kb(uid, lang))
        else: await safe_edit(q, T("gate_join", lang=lang), kb=gate_kb(lang))
        return

    if not await must_be_member_or_vip(context, uid):
        await safe_edit(q, T("gate_join", lang=lang), kb=gate_kb(lang)); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 {q.from_user.full_name}\n🆔 {uid}", kb=main_menu_kb(uid, lang)); return

    if q.data == "back_home":
        await safe_edit(q, T("main_menu", lang=lang), kb=main_menu_kb(uid, lang)); return

    def need_vip():
        return not (user_is_premium(uid) or uid == OWNER_ID)

    # الأقسام
    if q.data == "sections":
        await safe_edit(q, T("sections", lang=lang), kb=sections_kb(lang)); return

    # AI (VIP)
    if q.data == "sec_ai":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        await safe_edit(q, "🤖 اختر أداة:", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 دردشة", callback_data="ai_chat")],
            [InlineKeyboardButton("🌐 ترجمة تلقائية", callback_data="ai_translate")],
            [InlineKeyboardButton("✍️ كتابة إعلانية", callback_data="ai_writer")],
            [InlineKeyboardButton("🎙️ تحويل صوت لنص", callback_data="ai_stt")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return
    if q.data == "ai_chat":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        if not AI_ENABLED: await safe_edit(q, "🧠 OpenAI غير مفعّل.", kb=sections_kb(lang)); return
        ai_set_mode(uid,"ai_chat"); await safe_edit(q, T("ai_chat_on", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_writer":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        ai_set_mode(uid,"writer"); await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_translate":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        ai_set_mode(uid,"translate"); await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_stt":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        ai_set_mode(uid,"stt"); await safe_edit(q, T("send_text", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, T("ai_chat_off", lang=lang), kb=sections_kb(lang)); return

    # الأمن (VIP)
    if q.data == "sec_security":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        await safe_edit(q, "🛡️ اختر أداة:", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 فحص رابط", callback_data="sec_security_url")],
            [InlineKeyboardButton("📧 فحص إيميل", callback_data="sec_security_email")],
            [InlineKeyboardButton("🛰️ موقع IP/دومين", callback_data="sec_security_geo")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return
    if q.data == "sec_security_url":
        ai_set_mode(uid, "link_scan"); await safe_edit(q, T("security_send_url", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "sec_security_email":
        ai_set_mode(uid, "email_check"); await safe_edit(q, T("security_send_email", lang=lang), kb=ai_stop_kb(lang)); return
    if q.data == "sec_security_geo":
        ai_set_mode(uid, "geo_ip"); await safe_edit(q, T("security_send_geo", lang=lang), kb=ai_stop_kb(lang)); return

    # الخدمات
    if q.data == "sec_services":
        rows = [
            [InlineKeyboardButton(T("btn_games", lang=lang), callback_data="games_subs")],
            [InlineKeyboardButton(T("btn_adobe", lang=lang), url=ADOBE_DOC_URL)],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")],
        ]
        await safe_edit(q, T("page_services", lang=lang), kb=InlineKeyboardMarkup(rows)); return
    if q.data == "games_subs":
        rows = [[InlineKeyboardButton(title, url=link)] for title,link in GAMES_LINKS]
        rows.append([InlineKeyboardButton(T("back", lang=lang), callback_data="sec_services")])
        await safe_edit(q, T("games_list", lang=lang), kb=InlineKeyboardMarkup(rows)); return

    # فك الباند
    if q.data == "sec_unban":
        await safe_edit(q, T("unban_desc", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("Instagram", callback_data="unban_instagram")],
            [InlineKeyboardButton("Facebook", callback_data="unban_facebook")],
            [InlineKeyboardButton("Telegram", callback_data="unban_telegram")],
            [InlineKeyboardButton("Epic Games", callback_data="unban_epic")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return
    if q.data.startswith("unban_"):
        key=q.data.replace("unban_",""); msg=UNBAN_TEMPLATES.get(key,""); link=UNBAN_LINKS.get(key,"")
        await safe_edit(q, f"📋 Copy & send:\n<code>{_escape(msg)}</code>\n\n🔗 {link}", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_unban")]])); return

    # الدورات
    if q.data == "sec_courses":
        rows = [
            [InlineKeyboardButton(T("course_python", lang=lang), url=COURSE_PYTHON_URL)],
            [InlineKeyboardButton(T("course_cyber",  lang=lang), url=COURSE_CYBER_URL)],
            [InlineKeyboardButton(T("course_eh",     lang=lang), url=COURSE_EH_URL)],
            [InlineKeyboardButton(T("course_ecom",   lang=lang), url=COURSE_ECOM_URL)],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")],
        ]
        await safe_edit(q, T("page_courses", lang=lang), kb=InlineKeyboardMarkup(rows)); return

    # Dark GPT (VIP)
    if q.data == "sec_darkgpt":
        if need_vip(): await safe_edit(q, T("vip_only", lang=lang), kb=vip_only_kb(lang)); return
        await safe_edit(q, "🕶️ Dark GPT", kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("Open", url=DARK_GPT_URL)],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return

    # الملفات
    if q.data == "sec_files" and FILES_ENABLED:
        await safe_edit(q, T("page_files", lang=lang), kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("btn_jpg2pdf", lang=lang), callback_data="file_jpg2pdf")],
            [InlineKeyboardButton(T("btn_pdf2word_local", lang=lang), callback_data="file_pdf2word_local")],
            [InlineKeyboardButton(T("btn_word2pdf", lang=lang), callback_data="file_word2pdf")],
            [InlineKeyboardButton(T("btn_img2png", lang=lang), callback_data="file_img2png")],
            [InlineKeyboardButton(T("btn_img2webp", lang=lang), callback_data="file_img2webp")],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="sections")]
        ])); return
    if q.data == "file_jpg2pdf" and FILES_ENABLED:
        ai_set_mode(uid, "file_img_to_pdf", {"paths":[]}); await safe_edit(q, "📌 أرسل صورة واحدة أو أكثر… ثم /makepdf", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return
    if q.data == "file_pdf2word_local" and FILES_ENABLED:
        ai_set_mode(uid, "file_pdf2word_local"); await safe_edit(q, "📌 أرسل PDF وسأحوّله إلى Word محليًا.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return
    if q.data == "file_word2pdf" and FILES_ENABLED:
        ai_set_mode(uid, "file_word2pdf"); await safe_edit(q, "📌 أرسل DOC/DOCX لتحويله إلى PDF (ConvertAPI).", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return
    if q.data == "file_img2png" and FILES_ENABLED:
        ai_set_mode(uid, "file_img2png"); await safe_edit(q, "📌 أرسل صورة وسأرجع لك نسخة PNG.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return
    if q.data == "file_img2webp" and FILES_ENABLED:
        ai_set_mode(uid, "file_img2webp"); await safe_edit(q, "📌 أرسل صورة وسأرجع لك نسخة WEBP.", kb=InlineKeyboardMarkup([[InlineKeyboardButton(T("back", lang=lang), callback_data="sec_files")]])); return

    # VIP: شاشة دفع مبسطة
    if q.data == "vip":
        ref = payments_create(uid, VIP_PRICE_SAR, "paylink")
        pay_url = PAYLINK_CHECKOUT_BASE.format(ref=ref) if ("{ref}" in PAYLINK_CHECKOUT_BASE) else (PAYLINK_CHECKOUT_BASE or "https://paylink.sa")
        txt = f"💳 ترقية VIP مدى الحياة ({VIP_PRICE_SAR:.2f} SAR)\nمرجعك: <code>{ref}</code>"
        await safe_edit(q, txt, kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(T("go_pay", lang=lang), url=pay_url)],
            [InlineKeyboardButton(T("back", lang=lang), callback_data="back_home")]
        ])); return

# ===== رسائل =====
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    u=user_get(uid); lang=u.get("pref_lang","ar")
    if not await must_be_member_or_vip(context, uid):
        await update.message.reply_text(T("gate_join", lang=lang), reply_markup=gate_kb(lang)); return
    mode, extra = ai_get_mode(uid)
    msg = update.message

    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()
        if mode == "ai_chat":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await update.message.reply_text(ai_chat_reply(text)); return
        if mode == "writer":
            out = await ai_write(text); await update.message.reply_text(out); return
        if mode == "translate":
            out = await translate_auto(text); await update.message.reply_text(out); return
        if mode == "link_scan":
            out = await link_scan(text); await update.message.reply_text(out, parse_mode="HTML", disable_web_page_preview=True); return
        if mode == "email_check":
            if not is_valid_email(text): await update.message.reply_text("⚠️ صيغة الإيميل غير صحيحة."); return
            domain = text.split("@",1)[1]
            # MX
            mx_txt = "dnspython غير مثبت"
            if dnsresolver:
                try:
                    answers = dnsresolver.resolve(domain, "MX")
                    mx_hosts = [str(r.exchange).rstrip(".") for r in answers]
                    mx_txt = ", ".join(mx_hosts[:5]) if mx_hosts else "لا يوجد"
                except dnsexception.DNSException:
                    mx_txt = "لا يوجد (فشل)"
            # Gravatar
            g_url = f"https://www.gravatar.com/avatar/{md5_hex(text)}?d=404"
            g_st = await http_head(g_url); grav = "✅ موجود" if g_st and 200 <= g_st < 300 else "❌ غير موجود"
            ipi = await ipinfo_lookup(domain)
            await update.message.reply_text(f"📧 {text}\n📮 MX: {mx_txt}\n🖼️ Gravatar: {grav}\n{ipi}"); return
        if mode == "geo_ip":
            target = text
            query = resolve_ip(target) if _HOST_RE.match(target) else target
            data = await fetch_geo(query)
            await update.message.reply_text(fmt_geo(data), parse_mode="HTML"); return

    # ملفات وصوت وصور
    if msg.voice or msg.audio:
        if mode == "stt":
            file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
            p = await tg_download_to_path(context.bot, file_id, suffix=".ogg")
            out = await tts_whisper_from_file(str(p))
            await update.message.reply_text(out); return

    if FILES_ENABLED and (msg.photo or msg.document):
        if msg.photo:
            photo = msg.photo[-1]
            p = await tg_download_to_path(context.bot, photo.file_id, suffix=".jpg")
            if mode == "file_img2png":
                outp = image_to_format(p, "png"); 
                await (update.message.reply_document(InputFile(str(outp))) if outp else update.message.reply_text("⚠️ فشل التحويل إلى PNG.")); return
            if mode == "file_img2webp":
                outp = image_to_format(p, "webp"); 
                await (update.message.reply_document(InputFile(str(outp))) if outp else update.message.reply_text("⚠️ فشل التحويل إلى WEBP.")); return
            if mode == "file_img_to_pdf":
                st_paths = (extra or {}).get("paths", []); st_paths.append(str(p))
                ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
                await update.message.reply_text(f"✅ تمت إضافة صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا أخرى."); return

        if msg.document:
            filename = msg.document.file_name or ""; suffix = "_" + filename
            p = await tg_download_to_path(context.bot, msg.document.file_id, suffix=suffix)
            low = filename.lower()
            if mode == "file_img_to_pdf":
                # لو كانت صورة كمستند
                try:
                    Image.open(p)
                    st_paths = (extra or {}).get("paths", []); st_paths.append(str(p))
                    ai_set_mode(uid, "file_img_to_pdf", {"paths": st_paths})
                    await update.message.reply_text(f"✅ تمت إضافة ملف صورة ({len(st_paths)}). أرسل /makepdf للإخراج أو أرسل صورًا أخرى."); return
                except Exception:
                    await update.message.reply_text("⚠️ الملف ليس صورة صالحة."); return
            if mode == "file_pdf2word_local":
                if not low.endswith(".pdf"): await update.message.reply_text("⚠️ أرسل PDF."); return
                out = pdf_to_word_local(p)
                if out and out.exists() and out.stat().st_size <= MAX_UPLOAD_BYTES:
                    await update.message.reply_document(InputFile(str(out)))
                else:
                    await update.message.reply_text("⚠️ فشل التحويل (PDF → Word).")
                return
            if mode == "file_word2pdf":
                if not (low.endswith(".doc") or low.endswith(".docx")): await update.message.reply_text("⚠️ أرسل ملف Word (DOC/DOCX)."); return
                out = await word_to_pdf_convertapi(p)
                if out is None: await update.message.reply_text("⚠️ تحتاج تعيين المتغير CONVERTAPI_SECRET لتفعيل Word → PDF.")
                elif out.exists() and out.stat().st_size <= MAX_UPLOAD_BYTES: await update.message.reply_document(InputFile(str(out)))
                else: await update.message.reply_text("⚠️ فشل التحويل (Word → PDF).")
                return
            if mode == "file_img2png":
                outp = image_to_format(p, "png"); 
                await (update.message.reply_document(InputFile(str(outp))) if outp else update.message.reply_text("⚠️ فشل التحويل إلى PNG.")); return
            if mode == "file_img2webp":
                outp = image_to_format(p, "webp"); 
                await (update.message.reply_document(InputFile(str(outp))) if outp else update.message.reply_text("⚠️ فشل التحويل إلى WEBP.")); return

    if not mode:
        await update.message.reply_text(T("main_menu", lang=lang), reply_markup=main_menu_kb(uid, lang))

# ===== أوامر إضافية =====
async def makepdf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FILES_ENABLED:
        await update.message.reply_text("❌ أدوات الملفات غير مفعلة."); return
    uid=update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode != "file_img_to_pdf":
        await update.message.reply_text("هذه الأداة تعمل بعد اختيار (JPG → PDF) من الأقسام."); return
    paths = (extra or {}).get("paths", [])
    if not paths:
        await update.message.reply_text("لم يتم استلام أي صور بعد. أرسل صورًا ثم /makepdf."); return
    pdf = images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size <= MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
    else:
        await update.message.reply_text("⚠️ فشل إنشاء PDF أو الحجم كبير.")
    ai_set_mode(uid, None, {})

# ===== أوامر المالك =====
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("Usage: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text("✅ granted")
async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args: await update.message.reply_text("Usage: /revoke <user_id>"); return
    user_revoke(context.args[0]); await update.message.reply_text("❌ revoked")
async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application); await update.message.reply_text("✅ refreshed")
async def aidiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    from importlib.metadata import version, PackageNotFoundError
    def v(p): 
        try: return version(p)
        except PackageNotFoundError: return "not-installed"
    await update.message.reply_text(
        f"AI={'ON' if AI_ENABLED else 'OFF'} key={'set' if OPENAI_API_KEY else 'missing'} model={OPENAI_CHAT_MODEL}\n"
        f"openai={v('openai')} aiohttp={v('aiohttp')} Pillow={v('Pillow')}\n"
        f"pdf2docx={v('pdf2docx')} convertapi={v('convertapi')}\n"
        f"python={os.sys.version.split()[0]} ffmpeg={'OK' if FFMPEG else 'MISSING'}"
    )
async def libdiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    from importlib.metadata import version, PackageNotFoundError
    def v(p): 
        try: return version(p)
        except PackageNotFoundError: return "not-installed"
    await update.message.reply_text(
        f"python-telegram-bot={v('python-telegram-bot')}\n"
        f"aiohttp={v('aiohttp')}\n"
        f"python-whois={v('python-whois')}\n"
        f"dnspython={v('dnspython')}\n"
        f"Pillow={v('Pillow')}\n"
        f"pdf2docx={v('pdf2docx')}\n"
        f"convertapi={v('convertapi')}\n"
        f"python={os.sys.version.split()[0]}"
    )
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("🔄 Restarting…"); os._exit(0)
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("ERR: %s", getattr(context,'error','unknown'))

# ===== Main =====
def main():
    init_db()
    app = (Application.builder().token(BOT_TOKEN).post_init(on_startup).concurrent_updates(True).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))
    # مالك
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
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

if __name__ == "__main__":
    main()
