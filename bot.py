# -*- coding: utf-8 -*-
# ------------------------------------------------------------
# FerpoKS Telegram Bot (organized menus, real integrations)
# python-telegram-bot v21.x (async) + health server for Render
# ------------------------------------------------------------
import os, sqlite3, threading, time, asyncio, re, json, logging, base64, socket, tempfile
from pathlib import Path
from io import BytesIO

from dotenv import load_dotenv
import aiohttp
from PIL import Image

# Telegram
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

# OpenAI
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# yt-dlp
try:
    import yt_dlp
except Exception:
    yt_dlp = None

# ====== ENV ======
ENV_PATH = Path(".env")
if ENV_PATH.exists() and not os.getenv("RENDER"):
    load_dotenv(ENV_PATH, override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN         = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is missing")

OWNER_ID          = int(os.getenv("OWNER_ID", "0") or "0")
OWNER_USERNAME    = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")
MAIN_CHANNELS     = [u.strip().lstrip("@") for u in (os.getenv("MAIN_CHANNELS","").split(",")) if u.strip()]
WELCOME_PHOTO     = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
PUBLIC_BASE_URL   = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
DB_PATH           = os.getenv("DB_PATH", "/var/data/bot.db")
TMP_DIR           = Path(os.getenv("TMP_DIR", "/tmp"))

# API Keys
OPENAI_API_KEY    = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_VISION     = os.getenv("OPENAI_VISION", "0") == "1"
IPINFO_TOKEN      = (os.getenv("IPINFO_TOKEN") or "").strip()
KICKBOX_API_KEY   = (os.getenv("KICKBOX_API_KEY") or "").strip()
URLSCAN_API_KEY   = (os.getenv("URLSCAN_API_KEY") or "").strip()

# External links
NUMBERS_URL       = os.getenv("NUMBERS_URL", "").strip()     # خدمة الأرقام المؤقتة (رابط خارجي)
VCC_URL           = os.getenv("VCC_URL", "").strip()         # فيزا افتراضية (رابط خارجي)
SMM_PANEL_URL     = os.getenv("SMM_PANEL_URL", "").strip()   # لوحة رشق/متابعين (رابط خارجي)

# Courses (روابطك)
COURSE_PYTHON_URL = os.getenv("COURSE_PYTHON_URL", "").strip()
COURSE_CYBER_URL  = os.getenv("COURSE_CYBER_URL", "").strip()
COURSE_EHACK_URL  = os.getenv("COURSE_EHACK_URL", "").strip()

# Unban links
UNBAN_INSTAGRAM_URL = os.getenv("UNBAN_INSTAGRAM_URL", "https://help.instagram.com/")
UNBAN_FACEBOOK_URL  = os.getenv("UNBAN_FACEBOOK_URL", "https://www.facebook.com/help/")
UNBAN_TELEGRAM_URL  = os.getenv("UNBAN_TELEGRAM_URL", "https://telegram.org/support")
UNBAN_EPIC_URL      = os.getenv("UNBAN_EPIC_URL", "https://www.epicgames.com/help/en-US/")

# Health server for Render
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "1") == "1"
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

# OpenAI client
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

MAX_UPLOAD_MB      = 47
MAX_UPLOAD_BYTES   = MAX_UPLOAD_MB * 1024 * 1024
CHANNEL_ID         = None

# ============ tiny HTTP server (/health) ============
def _run_health_server():
    if not (SERVE_HEALTH and AIOHTTP_AVAILABLE): 
        log.info("[health] disabled or aiohttp missing")
        return

    async def make_app():
        app = web.Application()
        async def _index(_): return web.json_response({"ok": True, "bot": "FerpoKS"})
        async def _health(_): return web.json_response({"ok": True, "ts": int(time.time())})
        app.router.add_get("/", _index)
        app.router.add_get("/health", _health)
        return app

    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _start():
            app = await make_app()
            runner = web.AppRunner(app)
            await runner.setup()
            port = int(os.getenv("PORT","10000"))
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            log.info("[health] serving on 0.0.0.0:%d", port)
        loop.run_until_complete(_start())
        try:
            loop.run_forever()
        finally:
            loop.stop(); loop.close()

    threading.Thread(target=_thread, daemon=True).start()

_run_health_server()

# ============ DB ============
_conn_lock = threading.RLock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn: return conn
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db._conn = conn
    log.info("[db] using %s", DB_PATH)
    return conn

def migrate_db():
    with _conn_lock:
        c = _db().cursor()
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY,
          premium INTEGER DEFAULT 0,
          verified_ok INTEGER DEFAULT 0,
          verified_at INTEGER DEFAULT 0,
          vip_forever INTEGER DEFAULT 0,
          vip_since INTEGER DEFAULT 0,
          pref_lang TEXT DEFAULT 'ar'
        );""")
        # sanity for users.id
        c.execute("PRAGMA table_info(users)")
        cols = {r["name"] for r in c.fetchall()}
        if "id" not in cols:
            log.warning("[db-migrate] users missing id - rebuilding")
            _db().execute("ALTER TABLE users RENAME TO users_old;")
            _db().execute("""
              CREATE TABLE users(
                id TEXT PRIMARY KEY, premium INTEGER DEFAULT 0, verified_ok INTEGER DEFAULT 0,
                verified_at INTEGER DEFAULT 0, vip_forever INTEGER DEFAULT 0, vip_since INTEGER DEFAULT 0,
                pref_lang TEXT DEFAULT 'ar');""")
            try:
                _db().execute("INSERT OR IGNORE INTO users(id) SELECT id FROM users_old;")
            except Exception: pass
            _db().execute("DROP TABLE users_old;")

        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state(
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          extra TEXT DEFAULT '{}',
          updated_at INTEGER
        );""")
        c.execute("PRAGMA table_info(ai_state)")
        cols = {r["name"] for r in c.fetchall()}
        if "extra" not in cols:
            _db().execute("ALTER TABLE ai_state ADD COLUMN extra TEXT DEFAULT '{}';")
        if "updated_at" not in cols:
            _db().execute("ALTER TABLE ai_state ADD COLUMN updated_at INTEGER;")
        _db().commit()

def init_db(): migrate_db()

def user_get(uid:int|str)->dict:
    uid=str(uid)
    with _conn_lock:
        c=_db().cursor()
        c.execute("SELECT * FROM users WHERE id=?",(uid,))
        r=c.fetchone()
        if not r:
            _db().execute("INSERT INTO users(id) VALUES (?)",(uid,)); _db().commit()
            return {"id":uid,"premium":0,"verified_ok":0,"verified_at":0,"vip_forever":0,"vip_since":0,"pref_lang":"ar"}
        return dict(r)

def user_is_premium(uid): 
    u=user_get(uid); return bool(u.get("premium") or u.get("vip_forever") or (uid==OWNER_ID))

def user_grant(uid):
    now=int(time.time())
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1, vip_forever=1, vip_since=COALESCE(NULLIF(vip_since,0),?) WHERE id=?",(now,str(uid))); _db().commit()

def prefs_set_lang(uid, lang):
    with _conn_lock: _db().execute("UPDATE users SET pref_lang=? WHERE id=?",(lang,str(uid))); _db().commit()

def ai_set_mode(uid, mode:str|None, extra:dict|None=None):
    with _conn_lock:
        _db().execute(
            "INSERT INTO ai_state(user_id,mode,extra,updated_at) VALUES (?,?,?,strftime('%s','now')) "
            "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, extra=excluded.extra, updated_at=strftime('%s','now')",
            (str(uid), mode, json.dumps(extra or {}, ensure_ascii=False))
        ); _db().commit()

def ai_get_mode(uid):
    with _conn_lock:
        c=_db().cursor(); c.execute("SELECT mode,extra FROM ai_state WHERE user_id=?",(str(uid),))
        r=c.fetchone()
        if not r: return None, {}
        try: extra=json.loads(r["extra"] or "{}")
        except Exception: extra={}
        return r["mode"], extra

# ====== i18n ======
LOCALE = {
"ar":{
 "welcome":"مرحباً بك في بوت فيربوكس 👋\nكل الخدمات تعمل داخل تيليجرام.\nاختر من القائمة بالأسفل.",
 "join_gate":"🔐 بعد الانضمام للقناة سيعمل البوت تلقائياً:",
 "admin_note":"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت **مشرف** في @{channel}.",
 "menu_main":"👇 القائمة الرئيسية",
 "btn_sections":"📂 الأقسام",
 "btn_contact":"📨 تواصل مع الإدارة",
 "btn_lang":"🌐 تغيير اللغة",
 "btn_me":"👤 معلوماتي",
 "btn_vip":"⚡ ترقية VIP",
 "btn_vip_badge":"⭐ حسابك VIP",
 "btn_back":"↩️ رجوع",
 "btn_lang_ar":"🇸🇦 العربية", "btn_lang_en":"🇺🇸 English",
 "myinfo":"👤 الاسم: {name}\n🆔 المعرّف: {id}\n🌐 اللغة: {lang}",
 "vip_on":"⭐ حسابك VIP (مدى الحياة).",
 "vip_off":"هذه الميزة خاصة بـ VIP.",
 "ai_disabled":"🧠 ميزة الذكاء الاصطناعي غير مفعلة.",
 "send_text":"أرسل نصاً الآن…",
 "send_ip":"📍 أرسل IP أو دومين (مثال: 8.8.8.8 أو example.com).",
 "send_email":"✉️ أرسل الإيميل لفحصه.",
 "send_url":"🛡️ أرسل الرابط لفحصه.",
 "send_media_url":"🎬 أرسل رابط الفيديو/الصوت (YouTube/TikTok/Twitter/Instagram…).",
 "send_voice":"🎙️ أرسل Voice أو ملف صوت (mp3/m4a/wav).",
 "send_image":"📷 أرسل صورة.",
 "done":"تم.",
 "sections_title":"اختر قسماً:",
 "sec_ai":"🤖 أدوات الذكاء الاصطناعي",
 "sec_security":"🛡️ أمن وحماية",
 "sec_media":"🎬 تحميل وسائط",
 "sec_files":"🗜️ أدوات ملفات",
 "sec_courses":"📚 دورات",
 "sec_smm":"📈 رشق/متابعين",
 "sec_nums":"☎️ أرقام مؤقتة",
 "sec_vcc":"💳 بطاقات افتراضية",
 "sec_unban":"🚫 فك الحظر (Unban)",
 "ai_stt":"🎙️ تحويل الصوت إلى نص",
 "ai_txi":"🖼️ نص → صورة (AI)",
 "ai_trans":"🌐 مترجم (نص/صورة)",
 "ai_chat":"🤖 AI Chat (VIP)",
 "ai_dark":"🌑 Dark GPT (VIP)",
 "security_ip":"🛰️ IP Lookup (IPinfo)",
 "security_email":"✉️ Email Checker (Kickbox)",
 "security_link":"🔗 فحص الروابط (urlscan)",
 "media_dl":"⬇️ تنزيل فيديو (MP4)",
 "file_img2pdf":"🖼️ صورة → PDF",
 "file_compress":"🗜️ ضغط صورة",
 "courses_python":"🐍 بايثون من الصفر",
 "courses_cyber":"🔐 الأمن السيبراني من الصفر",
 "courses_ehack":"🧑‍💻 الهاكر الأخلاقي",
 "smm_open":"افتح لوحة الرشق",
 "nums_open":"افتح خدمة الأرقام المؤقتة",
 "vcc_open":"افتح خدمة البطاقات الافتراضية",
 "unban_ig":"Instagram Appeal",
 "unban_fb":"Facebook Appeal",
 "unban_tg":"Telegram Support",
 "unban_epic":"Epic Games Support",
 "unban_text_ig":"Explain your account was mistakenly restricted. Provide ID if asked. Be polite and concise.",
 "unban_text_fb":"Request review for disabled account. Attach any required docs. Keep message short & clear.",
 "unban_text_tg":"Contact Telegram support with your phone number and issue details.",
 "unban_text_epic":"Open a ticket and describe the restriction and your Epic account email.",
 "img_trans_fail":"⚠️ لم أستطع قراءة النص من الصورة.",
 "pdf_ready":"✅ تم إنشاء PDF.",
 "compress_ok":"✅ تم ضغط الصورة.",
 "download_fail":"⚠️ تعذر تنزيل/إرسال الملف. جرّب رابطاً آخر.",
 "http_status":"🔎 حالة HTTP: {code}",
 "kb_contact":"تواصل مع الإدارة",
},
"en":{
 "welcome":"Welcome to FerpoKS Bot 👋\nEverything works inside Telegram.\nPick from the menu below.",
 "join_gate":"🔐 Join the channel and the bot will work automatically:",
 "admin_note":"⚠️ If verification fails, ensure the bot is **admin** in @{channel}.",
 "menu_main":"👇 Main Menu",
 "btn_sections":"📂 Sections",
 "btn_contact":"📨 Contact Admin",
 "btn_lang":"🌐 Change Language",
 "btn_me":"👤 My Info",
 "btn_vip":"⚡ Upgrade to VIP",
 "btn_vip_badge":"⭐ VIP Account",
 "btn_back":"↩️ Back",
 "btn_lang_ar":"🇸🇦 Arabic", "btn_lang_en":"🇺🇸 English",
 "myinfo":"👤 Name: {name}\n🆔 ID: {id}\n🌐 Language: {lang}",
 "vip_on":"⭐ Your account is VIP (lifetime).",
 "vip_off":"This feature is VIP-only.",
 "ai_disabled":"🧠 AI is not enabled.",
 "send_text":"Send your text…",
 "send_ip":"📍 Send IP or domain (e.g., 8.8.8.8 or example.com).",
 "send_email":"✉️ Send the email to check.",
 "send_url":"🛡️ Send the URL to scan.",
 "send_media_url":"🎬 Send a video/audio URL (YouTube/TikTok/Twitter/Instagram…).",
 "send_voice":"🎙️ Send a Voice note or audio file (mp3/m4a/wav).",
 "send_image":"📷 Send an image.",
 "done":"Done.",
 "sections_title":"Choose a section:",
 "sec_ai":"🤖 AI Tools",
 "sec_security":"🛡️ Security",
 "sec_media":"🎬 Media Downloader",
 "sec_files":"🗜️ File Tools",
 "sec_courses":"📚 Courses",
 "sec_smm":"📈 SMM / Followers",
 "sec_nums":"☎️ Temp Numbers",
 "sec_vcc":"💳 Virtual Cards",
 "sec_unban":"🚫 Unban",
 "ai_stt":"🎙️ Speech → Text",
 "ai_txi":"🖼️ Text → Image (AI)",
 "ai_trans":"🌐 Translator (Text/Image)",
 "ai_chat":"🤖 AI Chat (VIP)",
 "ai_dark":"🌑 Dark GPT (VIP)",
 "security_ip":"🛰️ IP Lookup (IPinfo)",
 "security_email":"✉️ Email Checker (Kickbox)",
 "security_link":"🔗 URL Scan (urlscan)",
 "media_dl":"⬇️ Download Video (MP4)",
 "file_img2pdf":"🖼️ Image → PDF",
 "file_compress":"🗜️ Compress Image",
 "courses_python":"🐍 Python From Scratch",
 "courses_cyber":"🔐 Cybersecurity From Scratch",
 "courses_ehack":"🧑‍💻 Ethical Hacking",
 "smm_open":"Open SMM Panel",
 "nums_open":"Open Temp Numbers",
 "vcc_open":"Open Virtual Cards",
 "unban_ig":"Instagram Appeal",
 "unban_fb":"Facebook Appeal",
 "unban_tg":"Telegram Support",
 "unban_epic":"Epic Games Support",
 "unban_text_ig":"Explain the account was restricted by mistake. Provide ID if requested. Be polite and concise.",
 "unban_text_fb":"Request a review for disabled account. Attach required docs. Keep it short & clear.",
 "unban_text_tg":"Contact Telegram support with phone number and issue details.",
 "unban_text_epic":"Open a ticket, describe the restriction and your Epic account email.",
 "img_trans_fail":"⚠️ Could not read text from image.",
 "pdf_ready":"✅ PDF created.",
 "compress_ok":"✅ Image compressed.",
 "download_fail":"⚠️ Couldn’t download/send the file. Try another URL.",
 "http_status":"🔎 HTTP status: {code}",
 "kb_contact":"Contact Admin",
}
}

def lang_of(uid)->str:
    try: return user_get(uid).get("pref_lang","ar") if uid else "ar"
    except Exception: return "ar"

def T(uid, key, **kw):
    l=lang_of(uid); m=LOCALE.get(l,LOCALE["ar"])
    s=m.get(key, key)
    if kw: s=s.format(**kw)
    return s

# ============ Membership ============
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try: ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except: pass
try: ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except: pass

_member_cache={}
async def is_member(context, user_id:int, force=False, retries=3, backoff=0.7)->bool:
    if user_is_premium(user_id): return True
    now=time.time()
    if not force:
        c=_member_cache.get(user_id)
        if c and c[1]>now: return c[0]
    targets=[CHANNEL_ID] if CHANNEL_ID else [f"@{u}" for u in MAIN_CHANNELS if u]
    for attempt in range(1,retries+1):
        for t in targets:
            try:
                cm=await context.bot.get_chat_member(t, user_id)
                ok=getattr(cm,"status",None) in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id]=(True, now+180); return True
            except Exception as e:
                log.warning("[is_member] #%d %s err=%s", attempt, t, e)
        if attempt<retries: await asyncio.sleep(backoff*attempt)
    _member_cache[user_id]=(False, now+60)
    return False

def admin_button_url()->str:
    return f"tg://resolve?domain={OWNER_USERNAME}" if OWNER_USERNAME else f"tg://user?id={OWNER_ID}"

# ============ Keyboards ============
def main_menu_kb(uid:int):
    rows=[
        [InlineKeyboardButton(T(uid,"btn_sections"), callback_data="menu_sections")],
        [InlineKeyboardButton(T(uid,"btn_lang"), callback_data="menu_lang")],
        [InlineKeyboardButton(T(uid,"btn_me"), callback_data="menu_me")],
    ]
    if user_is_premium(uid):
        rows.insert(1, [InlineKeyboardButton(T(uid,"btn_vip_badge"), callback_data="menu_vip")])
    else:
        rows.insert(1, [InlineKeyboardButton(T(uid,"btn_vip"), callback_data="menu_vip_up")])
    rows.append([InlineKeyboardButton(T(uid,"btn_contact"), url=admin_button_url())])
    return InlineKeyboardMarkup(rows)

def lang_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(LOCALE["ar"]["btn_lang_ar"], callback_data="lang_ar"),
         InlineKeyboardButton(LOCALE["en"]["btn_lang_en"], callback_data="lang_en")],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_home")]
    ])

def sections_root_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"sec_ai"), callback_data="sec_ai"),
         InlineKeyboardButton(T(uid,"sec_security"), callback_data="sec_security")],
        [InlineKeyboardButton(T(uid,"sec_media"), callback_data="sec_media"),
         InlineKeyboardButton(T(uid,"sec_files"), callback_data="sec_files")],
        [InlineKeyboardButton(T(uid,"sec_courses"), callback_data="sec_courses"),
         InlineKeyboardButton(T(uid,"sec_unban"), callback_data="sec_unban")],
        [InlineKeyboardButton(T(uid,"sec_smm"), callback_data="sec_smm"),
         InlineKeyboardButton(T(uid,"sec_nums"), callback_data="sec_nums")],
        [InlineKeyboardButton(T(uid,"sec_vcc"), callback_data="sec_vcc")],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_home")]
    ])

def sec_ai_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"ai_stt"), callback_data="ai_stt"),
         InlineKeyboardButton(T(uid,"ai_trans"), callback_data="ai_trans")],
        [InlineKeyboardButton(T(uid,"ai_txi"), callback_data="ai_txi")],
        [InlineKeyboardButton(T(uid,"ai_chat"), callback_data="ai_chat")],
        [InlineKeyboardButton(T(uid,"ai_dark"), callback_data="ai_dark")],  # VIP-gated link screen
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
    ])

def sec_security_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"security_ip"), callback_data="security_ip")],
        [InlineKeyboardButton(T(uid,"security_email"), callback_data="security_email")],
        [InlineKeyboardButton(T(uid,"security_link"), callback_data="security_link")],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
    ])

def sec_media_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"media_dl"), callback_data="media_dl")],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
    ])

def sec_files_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"file_img2pdf"), callback_data="file_img2pdf")],
        [InlineKeyboardButton(T(uid,"file_compress"), callback_data="file_compress")],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
    ])

def sec_courses_kb(uid:int):
    rows = []
    if COURSE_PYTHON_URL: rows.append([InlineKeyboardButton(T(uid,"courses_python"), url=COURSE_PYTHON_URL)])
    if COURSE_CYBER_URL:  rows.append([InlineKeyboardButton(T(uid,"courses_cyber"),  url=COURSE_CYBER_URL)])
    if COURSE_EHACK_URL:  rows.append([InlineKeyboardButton(T(uid,"courses_ehack"),  url=COURSE_EHACK_URL)])
    rows.append([InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")])
    return InlineKeyboardMarkup(rows)

def sec_unban_kb(uid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T(uid,"unban_ig"), url=UNBAN_INSTAGRAM_URL)],
        [InlineKeyboardButton(T(uid,"unban_fb"), url=UNBAN_FACEBOOK_URL)],
        [InlineKeyboardButton(T(uid,"unban_tg"), url=UNBAN_TELEGRAM_URL)],
        [InlineKeyboardButton(T(uid,"unban_epic"), url=UNBAN_EPIC_URL)],
        [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
    ])

def sec_links_kb(uid:int, url:str, back="back_sections"):
    btn = InlineKeyboardButton(url, url=url) if url else InlineKeyboardButton(T(uid,"kb_contact"), url=admin_button_url())
    return InlineKeyboardMarkup([[btn],[InlineKeyboardButton(T(uid,"btn_back"), callback_data=back)]])

# safe edit
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        elif kb is not None:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                if kb is not None: await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest: pass
        else:
            log.warning("safe_edit error: %s", e)

# ============ Helpers ============
_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
_URL_RE  = re.compile(r"https?://[^\s]+")

def resolve_ip(host:str)->str|None:
    try:
        infos = socket.getaddrinfo(host, None)
        for _,_,_,_,sockaddr in infos:
            ip=sockaddr[0]
            if ":" not in ip: return ip
        return infos[0][4][0] if infos else None
    except Exception: return None

async def http_head(url:str)->int|None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, allow_redirects=True, timeout=20) as r:
                return r.status
    except Exception:
        return None

# ============ Integrations ============
async def ipinfo_lookup(query:str)->dict:
    ip = query
    if _HOST_RE.match(query): 
        ip = resolve_ip(query) or query
    token = IPINFO_TOKEN
    if not token:
        return {"error":"IPINFO_TOKEN missing"}
    url=f"https://ipinfo.io/{ip}?token={token}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=20) as r:
                data=await r.json(content_type=None)
                if r.status>=400: return {"error": f"ipinfo error {r.status}: {data}"}
                return data
    except Exception as e:
        return {"error": f"network error: {e}"}

def fmt_ipinfo(uid:int, data:dict)->str:
    if "error" in data: return f"⚠️ {data['error']}"
    parts=[]
    ip=data.get("ip","?")
    parts.append(f"🔎 <b>{ip}</b>")
    parts.append(f"🌍 {data.get('city','?')}, {data.get('region','?')}, {data.get('country','?')}")
    if data.get("loc"): parts.append(f"📍 {data['loc']}")
    if data.get("org"): parts.append(f"🏢 {data['org']}")
    if data.get("asn"):
        asn=data["asn"]
        parts.append(f"🛰️ AS{asn.get('asn','?')} — {asn.get('name','?')}")
    if data.get("timezone"): parts.append(f"⏰ {data['timezone']}")
    parts.append("\nℹ️ Use this information for lawful purposes only.")
    return "\n".join(parts)

async def kickbox_verify(email:str)->dict:
    key=KICKBOX_API_KEY
    if not key: return {"error":"KICKBOX_API_KEY missing"}
    url=f"https://api.kickbox.com/v2/verify?email={email}&apikey={key}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=20) as r:
                data=await r.json(content_type=None)
                if r.status>=400: return {"error": f"kickbox error {r.status}: {data}"}
                return data
    except Exception as e:
        return {"error": f"network error: {e}"}

def fmt_kickbox(uid:int, data:dict)->str:
    if "error" in data: return f"⚠️ {data['error']}"
    lines=[
        f"📧 <b>{data.get('email','')}</b>",
        f"✅ result: {data.get('result')}  ({'risky' if data.get('risky') else 'ok'})",
        f"reason: {data.get('reason')}",
        f"disposable: {data.get('disposable')}, role: {data.get('role')}",
        f"domain: {data.get('domain')}, mx: {data.get('mx')}",
    ]
    if data.get("did_you_mean"):
        lines.append(f"❓ did_you_mean: {data['did_you_mean']}")
    return "\n".join(lines)

async def urlscan_submit(url:str)->dict:
    key=URLSCAN_API_KEY
    if not key: return {"error":"URLSCAN_API_KEY missing"}
    try:
        async with aiohttp.ClientSession() as s:
            headers={"API-Key":key,"Content-Type":"application/json"}
            payload={"url":url,"visibility":"public"}
            async with s.post("https://urlscan.io/api/v1/scan/", headers=headers, json=payload, timeout=25) as r:
                data=await r.json(content_type=None)
                if r.status>=400: return {"error": f"urlscan error {r.status}: {data}"}
                return data
    except Exception as e:
        return {"error": f"network error: {e}"}

async def urlscan_result(uuid:str)->dict:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://urlscan.io/api/v1/result/{uuid}", timeout=25) as r:
                data=await r.json(content_type=None)
                return data
    except Exception as e:
        return {"error": f"result error: {e}"}

def fmt_urlscan(uid:int, head_status:int|None, meta:dict|None, result:dict|None)->str:
    lines=[]
    if head_status is not None:
        lines.append(T(uid,"http_status", code=head_status))
    if meta and "uuid" in meta:
        lines.append(f"🧾 urlscan uuid: <code>{meta.get('uuid','')}</code>")
    if result and isinstance(result, dict):
        page = result.get("page",{})
        verdicts = result.get("verdicts",{}).get("overall",{})
        if page:
            lines.append(f"🌍 host: {page.get('domain','?')} — country: {page.get('country','?')}")
            lines.append(f"ℹ️ server: {page.get('server','?')}")
        if verdicts:
            lines.append(f"🛡️ verdict score: {verdicts.get('score','?')}  malicious={verdicts.get('malicious','?')}")
    if not lines:
        lines.append("ℹ️ Scan submitted. Use uuid above to query later.")
    return "\n".join(lines)

# ============ OpenAI helpers ============
def _chat(messages):
    if not AI_ENABLED or client is None: return None, "disabled"
    try:
        r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=messages, temperature=0.6)
        return r, None
    except Exception as e:
        return None, str(e)

async def stt_from_file(path:str)->str:
    if not AI_ENABLED or client is None: return LOCALE["ar"]["ai_disabled"]
    try:
        with open(path,"rb") as f:
            r=client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(r,"text","").strip() or "…"
    except Exception as e:
        return f"⚠️ {e}"

async def translate_text(text:str, target:str="ar")->str:
    if not AI_ENABLED or client is None: return LOCALE["ar"]["ai_disabled"]
    r,err=_chat([
        {"role":"system","content":"You are a professional translator. Keep meaning and formatting."},
        {"role":"user","content":f"Translate into {target}. Keep formatting.\n\n{text}"}
    ])
    if err: return f"⚠️ {err}"
    return (r.choices[0].message.content or "").strip()

async def translate_image(path:str, target:str="ar")->str:
    if not (AI_ENABLED and OPENAI_VISION and client): 
        return "⚠️ Image translation requires OPENAI_VISION=1 (and a vision-capable model)."
    try:
        with open(path,"rb") as f: b64=base64.b64encode(f.read()).decode()
        content=[{"type":"text","text":f"Extract the text from the image and translate it into {target}. Return only the translation."},
                 {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}]
        r = client.chat.completions.create(model=OPENAI_CHAT_MODEL, messages=[{"role":"user","content":content}], temperature=0)
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        return f"⚠️ {e}"

async def ai_write(prompt:str)->str:
    if not AI_ENABLED or client is None: return LOCALE["ar"]["ai_disabled"]
    r,err=_chat([
        {"role":"system","content":"اكتب نصًا عربيًا إعلانيًا جذابًا ومختصرًا بعناوين قصيرة وCTA واضح."},
        {"role":"user","content":prompt}
    ])
    if err: return f"⚠️ {err}"
    return (r.choices[0].message.content or "").strip()

async def ai_image(prompt:str)->bytes|None:
    if not AI_ENABLED or client is None: return None
    try:
        r=client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        return base64.b64decode(r.data[0].b64_json)
    except Exception as e:
        log.error("image gen: %s", e); return None

# ============ Media Downloader (force MP4 where possible) ============
async def download_media(url:str)->Path|None:
    if yt_dlp is None: 
        log.error("yt_dlp not installed"); return None
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl=str(TMP_DIR / "%(title).80s.%(id)s.%(ext)s")
    # نحاول اختيار صيغ MP4 مباشرة لتجنب الحاجة لـ ffmpeg
    ydl_opts={
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/bestvideo[ext=mp4]/b[ext=mp4]/b",  # يفضّل mp4
        "retries": 2,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info=ydl.extract_info(url, download=True)
            fname=ydl.prepare_filename(info)
            base, _ = os.path.splitext(fname)
            # ابحث عن فيديو mp4
            for ext in (".mp4",".m4v",".mov"):
                p=Path(base+ext)
                if p.exists() and p.is_file():
                    if p.stat().st_size<=MAX_UPLOAD_BYTES: 
                        return p
            # fallback: لو ما قدر يجيب mp4، جرّب أقرب ملف
            for ext in (".webm",".mkv",".mp4"):
                p=Path(base+ext)
                if p.exists() and p.is_file() and p.stat().st_size<=MAX_UPLOAD_BYTES:
                    return p
    except Exception as e:
        log.error("ydl: %s", e)
        return None
    return None

# ============ File tools ============
async def tg_download_to_path(bot, file_id:str, suffix:str="")->Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tf = await bot.get_file(file_id)
    fd, tmp = tempfile.mkstemp(prefix="tg_", suffix=suffix, dir=str(TMP_DIR))
    os.close(fd)
    await tf.download_to_drive(tmp)
    return Path(tmp)

def images_to_pdf(paths:list[Path])->Path|None:
    try:
        imgs=[Image.open(p).convert("RGB") for p in paths]
        if not imgs: return None
        out = TMP_DIR / f"images_{int(time.time())}.pdf"
        first, rest = imgs[0], imgs[1:]
        first.save(out, save_all=True, append_images=rest)
        return out
    except Exception as e:
        log.error("img->pdf: %s", e); return None

def compress_image(image_path:Path, quality:int=70)->Path|None:
    try:
        im=Image.open(image_path)
        out = TMP_DIR / f"compressed_{image_path.stem}.jpg"
        im.convert("RGB").save(out, "JPEG", optimize=True, quality=max(1,min(quality,95)))
        return out
    except Exception as e:
        log.error("compress: %s", e); return None

# ============ Commands ============
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    init_db()
    uid=update.effective_user.id; chat_id=update.effective_chat.id
    user_get(uid)

    # Welcome
    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO,"rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=T(uid,"welcome"))
        else:
            await context.bot.send_message(chat_id, T(uid,"welcome"))
    except Exception as e:
        log.warning("welcome send: %s", e)

    ok = await is_member(context, uid, force=True)
    if not ok:
        rows=[[InlineKeyboardButton("📣 Join", url=f"https://t.me/{MAIN_CHANNELS[0]}")],
              [InlineKeyboardButton("✅ Verify", callback_data="verify")]]
        await context.bot.send_message(chat_id, T(uid,"join_gate"), reply_markup=InlineKeyboardMarkup(rows))
        if uid==OWNER_ID and MAIN_CHANNELS:
            await context.bot.send_message(chat_id, T(uid,"admin_note", channel=MAIN_CHANNELS[0]))
        return

    await context.bot.send_message(chat_id, T(uid,"menu_main"), reply_markup=main_menu_kb(uid))
    await context.bot.send_message(chat_id, T(uid,"sections_title"), reply_markup=sections_root_kb(uid))

async def help_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def setlang_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /setlang ar|en"); return
    lang=context.args[0].lower()
    if lang not in ("ar","en"): lang="ar"
    prefs_set_lang(uid, lang)
    await update.message.reply_text(T(uid,"done"), reply_markup=main_menu_kb(uid))

# ============ Button handler ============
async def on_button(update:Update, context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=q.from_user.id
    await q.answer()

    if q.data=="verify":
        if await is_member(context, uid, force=True):
            await safe_edit(q, T(uid,"menu_main"), main_menu_kb(uid))
            try: await q.message.reply_text(T(uid,"sections_title"), reply_markup=sections_root_kb(uid))
            except: pass
        else:
            rows=[[InlineKeyboardButton("📣 Join", url=f"https://t.me/{MAIN_CHANNELS[0]}")],
                  [InlineKeyboardButton("✅ Verify", callback_data="verify")]]
            await safe_edit(q, T(uid,"join_gate"), InlineKeyboardMarkup(rows))
        return

    # Home/Sections
    if q.data=="back_home":
        await safe_edit(q, T(uid,"menu_main"), main_menu_kb(uid)); return
    if q.data=="menu_sections":
        await safe_edit(q, T(uid,"sections_title"), sections_root_kb(uid)); return

    # Language
    if q.data=="menu_lang":
        await safe_edit(q, " ", lang_kb(uid)); return
    if q.data in ("lang_ar","lang_en"):
        prefs_set_lang(uid, "ar" if q.data=="lang_ar" else "en")
        await safe_edit(q, T(uid,"menu_main"), main_menu_kb(uid)); return

    # Info/VIP
    if q.data=="menu_me":
        await safe_edit(q, T(uid,"myinfo", name=q.from_user.full_name, id=uid, lang=lang_of(uid).upper()), main_menu_kb(uid)); return
    if q.data=="menu_vip":
        await safe_edit(q, T(uid,"vip_on"), main_menu_kb(uid)); return
    if q.data=="menu_vip_up":
        await safe_edit(q, "💳 VIP is lifetime. Contact admin to activate.", 
                        InlineKeyboardMarkup([[InlineKeyboardButton(T(uid,"kb_contact"), url=admin_button_url())],
                                              [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_home")]])); return

    # Sections
    if q.data=="back_sections":
        await safe_edit(q, T(uid,"sections_title"), sections_root_kb(uid)); return
    if q.data=="sec_ai":
        await safe_edit(q, T(uid,"sec_ai"), sec_ai_kb(uid)); return
    if q.data=="sec_security":
        await safe_edit(q, T(uid,"sec_security"), sec_security_kb(uid)); return
    if q.data=="sec_media":
        await safe_edit(q, T(uid,"sec_media"), sec_media_kb(uid)); return
    if q.data=="sec_files":
        await safe_edit(q, T(uid,"sec_files"), sec_files_kb(uid)); return
    if q.data=="sec_courses":
        await safe_edit(q, T(uid,"sec_courses"), sec_courses_kb(uid)); return
    if q.data=="sec_unban":
        txt = f"IG:\n{T(uid,'unban_text_ig')}\n\nFB:\n{T(uid,'unban_text_fb')}\n\nTG:\n{T(uid,'unban_text_tg')}\n\nEpic:\n{T(uid,'unban_text_epic')}"
        await safe_edit(q, txt, sec_unban_kb(uid)); return
    if q.data=="sec_smm":
        await safe_edit(q, T(uid,"smm_open"), sec_links_kb(uid, SMM_PANEL_URL)); return
    if q.data=="sec_nums":
        await safe_edit(q, T(uid,"nums_open"), sec_links_kb(uid, NUMBERS_URL)); return
    if q.data=="sec_vcc":
        await safe_edit(q, T(uid,"vcc_open"), sec_links_kb(uid, VCC_URL)); return

    # AI items (VIP-gated)
    if q.data in ("ai_chat","ai_txi","ai_trans","ai_stt","ai_dark"):
        if not user_is_premium(uid):
            await safe_edit(q, T(uid,"vip_off"), sec_ai_kb(uid)); return
        if q.data=="ai_chat":
            ai_set_mode(uid, "ai_chat", {})
            await safe_edit(q, T(uid,"send_text"), sec_ai_kb(uid)); return
        if q.data=="ai_txi":
            ai_set_mode(uid, "ai_txi", {})
            await safe_edit(q, T(uid,"send_text"), sec_ai_kb(uid)); return
        if q.data=="ai_trans":
            ai_set_mode(uid, "ai_trans", {"to":lang_of(uid)})
            await safe_edit(q, T(uid,"send_text")+" / "+T(uid,"send_image"), sec_ai_kb(uid)); return
        if q.data=="ai_stt":
            ai_set_mode(uid, "ai_stt", {})
            await safe_edit(q, T(uid,"send_voice"), sec_ai_kb(uid)); return
        if q.data=="ai_dark":
            # لا نغيّر مكانه – نظهر رابط فتح بعد التأكد من VIP
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Open Dark GPT", url="https://flowgpt.com/chat/M0GRwnsc2MY0DdXPPmF4X")],
                [InlineKeyboardButton(T(uid,"btn_back"), callback_data="back_sections")]
            ])
            await safe_edit(q, T(uid,"ai_dark"), kb); return

    # Media / Files
    if q.data=="media_dl":
        ai_set_mode(uid, "media_dl", {}); await safe_edit(q, T(uid,"send_media_url"), sec_media_kb(uid)); return
    if q.data=="file_img2pdf":
        ai_set_mode(uid, "file_img2pdf", {"paths":[]}); await safe_edit(q, T(uid,"send_image"), sec_files_kb(uid)); return
    if q.data=="file_compress":
        ai_set_mode(uid, "file_compress", {}); await safe_edit(q, T(uid,"send_image"), sec_files_kb(uid)); return

# ============ Messages ============
async def guard_messages(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    user_get(uid)
    if not await is_member(context, uid):
        rows=[[InlineKeyboardButton("📣 Join", url=f"https://t.me/{MAIN_CHANNELS[0]}")],
              [InlineKeyboardButton("✅ Verify", callback_data="verify")]]
        await update.message.reply_text(T(uid,"join_gate"), reply_markup=InlineKeyboardMarkup(rows))
        return

    mode, extra = ai_get_mode(uid)
    msg = update.message

    # plain text
    if msg.text and not msg.text.startswith("/"):
        text = msg.text.strip()

        if mode=="ai_chat":
            if not AI_ENABLED: await msg.reply_text(T(uid,"ai_disabled")); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            r,err=_chat([{"role":"system","content":"Answer briefly in Arabic unless the user writes English."},{"role":"user","content":text}])
            out=(r.choices[0].message.content if r else f"⚠️ {err}") or "…"
            await msg.reply_text(out); return

        if mode=="ai_txi":
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
            img = await ai_image(text)
            if img:
                bio=BytesIO(img); bio.name="ai.png"
                await msg.reply_photo(InputFile(bio))
            else:
                await msg.reply_text(T(uid,"ai_disabled"))
            return

        if mode=="ai_trans":
            to=(extra or {}).get("to", lang_of(uid))
            out = await translate_text(text, to)
            await msg.reply_text(out); return

        if mode=="security_ip":
            data=await ipinfo_lookup(text)
            await msg.reply_text(fmt_ipinfo(uid, data), parse_mode="HTML"); return

        if mode=="security_email":
            data=await kickbox_verify(text)
            await msg.reply_text(fmt_kickbox(uid, data), parse_mode="HTML"); return

        if mode=="security_link":
            head = await http_head(text) if _URL_RE.search(text) else None
            meta = await urlscan_submit(text) if URLSCAN_API_KEY and _URL_RE.search(text) else None
            res  = None
            if meta and isinstance(meta, dict) and meta.get("uuid"):
                try:
                    await asyncio.sleep(5)
                    res = await urlscan_result(meta["uuid"])
                except Exception: pass
            await msg.reply_text(fmt_urlscan(uid, head, meta, res), parse_mode="HTML", disable_web_page_preview=True); return

        if mode=="media_dl":
            if not _URL_RE.search(text):
                await msg.reply_text(T(uid,"send_media_url")); return
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_DOCUMENT)
            p = await download_media(text)
            if p and p.exists() and p.stat().st_size<=MAX_UPLOAD_BYTES:
                try:
                    if p.suffix.lower() in (".mp4",".m4v",".mov"):
                        await msg.reply_video(InputFile(str(p)))
                    else:
                        await msg.reply_document(InputFile(str(p)))
                except Exception:
                    await msg.reply_text(T(uid,"download_fail"))
            else:
                await msg.reply_text(T(uid,"download_fail"))
            return

        if mode=="file_img2pdf":
            await msg.reply_text(T(uid,"send_image")); return

        if mode=="file_compress":
            await msg.reply_text(T(uid,"send_image")); return

    # voice/audio
    if (msg.voice or msg.audio) and mode=="ai_stt":
        file_id = msg.voice.file_id if msg.voice else msg.audio.file_id
        p = await tg_download_to_path(context.bot, file_id, ".ogg")
        text = await stt_from_file(str(p))
        await msg.reply_text(text); return

    # photo
    if msg.photo:
        photo = msg.photo[-1]
        p = await tg_download_to_path(context.bot, photo.file_id, ".jpg")
        if mode=="ai_trans":
            res = await translate_image(str(p), lang_of(uid))
            await msg.reply_text(res or T(uid,"img_trans_fail")); return
        if mode=="file_img2pdf":
            st=(extra or {}).get("paths",[])
            st.append(str(p)); ai_set_mode(uid,"file_img2pdf",{"paths":st})
            await msg.reply_text(f"✅ {len(st)} image(s) added. Send /makepdf to export."); return
        if mode=="file_compress":
            out = compress_image(p)
            if out and out.exists():
                await msg.reply_document(InputFile(str(out))); await msg.reply_text(T(uid,"compress_ok"))
            else:
                await msg.reply_text("⚠️ Failed."); 
            return

    # documents (images as files)
    if msg.document and mode in ("file_img2pdf","file_compress"):
        p = await tg_download_to_path(context.bot, msg.document.file_id, f"_{msg.document.file_name or ''}")
        if mode=="file_compress":
            out=compress_image(p)
            if out and out.exists():
                await msg.reply_document(InputFile(str(out))); await msg.reply_text(T(uid,"compress_ok"))
            else:
                await msg.reply_text("⚠️ Failed.")
            return
        if mode=="file_img2pdf":
            st=(extra or {}).get("paths",[])
            st.append(str(p)); ai_set_mode(uid,"file_img2pdf",{"paths":st})
            await msg.reply_text(f"✅ {len(st)} file(s) added. Send /makepdf to export."); return

    # default
    await update.message.reply_text(T(uid,"menu_main"), reply_markup=main_menu_kb(uid))

# ============ Commands for file flow ============
async def makepdf_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    mode, extra = ai_get_mode(uid)
    if mode!="file_img2pdf":
        await update.message.reply_text("Use /file then choose Image → PDF"); return
    paths=(extra or {}).get("paths",[])
    if not paths:
        await update.message.reply_text("Send images first, then /makepdf."); return
    pdf=images_to_pdf([Path(p) for p in paths])
    if pdf and pdf.exists() and pdf.stat().st_size<=MAX_UPLOAD_BYTES:
        await update.message.reply_document(InputFile(str(pdf)))
        await update.message.reply_text(T(uid,"pdf_ready"))
    else:
        await update.message.reply_text("⚠️ PDF too large or failed.")
    ai_set_mode(uid, None, {})

# ============ Owner helpers ============
async def cmd_id(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def grant(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=OWNER_ID: return
    if not context.args: await update.message.reply_text("Usage: /grant <user_id>"); return
    user_grant(context.args[0]); await update.message.reply_text("OK")

# ============ Errors ============
async def on_error(update:object, context:ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context,'error','unknown'))

# ============ Startup ============
async def on_startup(app:Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("delete_webhook: %s", e)
    global CHANNEL_ID
    CHANNEL_ID=None
    for u in MAIN_CHANNELS:
        try:
            chat=await app.bot.get_chat(f"@{u}")
            CHANNEL_ID=chat.id; log.info("[startup] @%s -> %s", u, CHANNEL_ID); break
        except Exception as e:
            log.warning("get_chat @%s: %s", u, e)
    # Commands
    try:
        await app.bot.set_my_commands(
            [BotCommand("start","Start"), BotCommand("help","Help"), BotCommand("makepdf","Export PDF"), BotCommand("setlang","Set language")],
            scope=BotCommandScopeDefault()
        )
        await app.bot.set_my_commands(
            [BotCommand("id","Your ID"), BotCommand("grant","Grant VIP")],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        log.warning("set_my_commands: %s", e)

def main():
    init_db()
    app=(Application.builder()
         .token(BOT_TOKEN)
         .post_init(on_startup)
         .concurrent_updates(True)
         .build())
    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(CommandHandler("makepdf", makepdf_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    # buttons
    app.add_handler(CallbackQueryHandler(on_button))
    # messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, guard_messages))
    app.add_handler(MessageHandler(filters.PHOTO, guard_messages))
    app.add_handler(MessageHandler(filters.Document.ALL, guard_messages))
    # errors
    app.add_error_handler(on_error)
    app.run_polling()

if __name__=="__main__":
    main()

