# -*- coding: utf-8 -*-
"""
Ferpoks Bot v5.1 — Auto-VIP via Webhook + Polling (loop-safe)
- تحقق عضوية القناة + كاش
- Telegram Webhook/Polling (اختياري)
- دفع VIP: ref + أزرار دفع
- تفعيل VIP تلقائي عبر Webhook /payhook + Poller احتياطي
- إدارة أقسام (إضافة/تعديل/حذف/عرض) مخزنة بقاعدة البيانات
- لوحة تحكم، بث، إحصائيات، أكواد Redeem
- ذكاء اصطناعي (OpenAI Responses API + Fallback)
- تتبّع تنزيلات
- إصلاح تضارب event loop: نُشغّل خدماتنا ضمن نفس اللوب، ونجعل PTB لا يُغلق اللوب (close_loop=False)
"""

import os, sqlite3, threading, time, asyncio, logging, json, random, string
from pathlib import Path
from dotenv import load_dotenv
import aiohttp
from aiohttp import web

# OpenAI اختياري
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
from telegram.error import BadRequest, Forbidden, NetworkError
from telegram.constants import ChatAction

# ========= الإعدادات =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

# Telegram Webhook (اختياري)
USE_TELEGRAM_WEBHOOK = os.getenv("USE_WEBHOOK", "0").strip().lower() in ("1", "true", "yes")
TELEGRAM_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
TELEGRAM_WEBHOOK_IP  = os.getenv("WEBHOOK_IP", "").strip()
TELEGRAM_WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
TELEGRAM_WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", f"/{BOT_TOKEN}")

DB_PATH = os.getenv("DB_PATH", "bot.db")

# دفع VIP
PRICE_USD = float(os.getenv("PRICE_USD", "10"))
PAYLINK_CHECKOUT_BASE = os.getenv("PAYLINK_CHECKOUT_BASE", "").strip()  # مثال: https://pay.example/checkout?ref={ref}
STRIPE_PAYMENT_LINK   = os.getenv("STRIPE_PAYMENT_LINK", "").strip()
PAY_VERIFY_ENDPOINT   = os.getenv("PAY_VERIFY_ENDPOINT", "").strip()    # fallback checker
PAY_VERIFY_AUTH       = os.getenv("PAY_VERIFY_AUTH", "").strip()
PAY_POLL_SECONDS      = int(os.getenv("PAY_POLL_SECONDS", "45"))

# Webhook داخلي للدفع
PAY_WEBHOOK_ENABLED = os.getenv("PAY_WEBHOOK_ENABLED", "1").strip().lower() in ("1","true","yes")
PAY_WEBHOOK_HOST    = os.getenv("PAY_WEBHOOK_HOST", "0.0.0.0")
PAY_WEBHOOK_PORT    = int(os.getenv("PAY_WEBHOOK_PORT", "8080"))
PAY_WEBHOOK_PATH    = os.getenv("PAY_WEBHOOK_PATH", "/payhook")
PAY_WEBHOOK_SECRET  = os.getenv("PAY_WEBHOOK_SECRET", "super-secret")

# OpenAI
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL  = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1")
USE_RESPONSES_API  = os.getenv("USE_RESPONSES_API", "1").strip().lower() in ("1","true","yes")
AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None)
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))

# القنوات
MAIN_CHANNEL_USERNAMES = [u.strip() for u in os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(",") if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

def need_admin_text() -> str:
    return (f"⚠️ لو ما اشتغل التحقق: لازم البوت يكون **مشرف** في @{MAIN_CHANNEL_USERNAMES[0]} "
            f"مع صلاحية رؤية الأعضاء.")

OWNER_DEEP_LINK = f"tg://user?id={OWNER_ID}"

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO", "assets/ferpoks.jpg")
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "هنا تلاقي مصادر وأدوات للتجارة الإلكترونية، بايثون، الأمن السيبراني وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

# لوجينغ
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("ferpoks-bot")

CHANNEL_ID = None  # سيُحل عند الإقلاع

# ========= أوامر البوت عند الإقلاع =========
async def on_startup(app: Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    global CHANNEL_ID
    CHANNEL_ID = None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id
            log.info(f"[startup] resolved @{u} -> chat_id={CHANNEL_ID}")
            break
        except Exception as e:
            log.warning(f"[startup] get_chat @{u} failed: {e}")
    if CHANNEL_ID is None:
        log.error("[startup] could not resolve channel id; falling back to @username api calls")

    await app.bot.set_my_commands(
        [
            BotCommand("start", "بدء"),
            BotCommand("help", "مساعدة"),
            BotCommand("buy", "شراء VIP"),
            BotCommand("paid", "تحقق حالة دفع"),
            BotCommand("debugverify", "تشخيص التحقق"),
            BotCommand("dv", "تشخيص سريع"),
        ],
        scope=BotCommandScopeDefault()
    )
    # للمالك
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
                BotCommand("id", "معرّفك"),
                BotCommand("grant", "منح VIP"),
                BotCommand("revoke", "سحب VIP"),
                BotCommand("refreshcmds", "تحديث الأوامر"),
                BotCommand("stats", "إحصائيات"),
                BotCommand("broadcast", "بث"),
                BotCommand("admin", "لوحة تحكم"),
                BotCommand("newcode", "كود تفعيل"),
                BotCommand("redeem", "تفعيل بالكود"),
                BotCommand("addsec", "إضافة قسم"),
                BotCommand("editsec", "تعديل قسم"),
                BotCommand("delsec", "حذف قسم"),
                BotCommand("listsec", "عرض الأقسام"),
                BotCommand("dv", "تشخيص سريع"),
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception:
        pass

# ========= قاعدة البيانات =========
_conn_lock = threading.Lock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
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
          ref TEXT,
          created_at INTEGER DEFAULT (strftime('%s','now'))
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS ai_state (
          user_id TEXT PRIMARY KEY,
          mode TEXT DEFAULT NULL,
          updated_at INTEGER
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS payments (
          ref TEXT PRIMARY KEY,
          user_id TEXT,
          created_at INTEGER,
          paid INTEGER DEFAULT 0,
          provider TEXT,
          meta TEXT
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS downloads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT,
          key TEXT,
          ts INTEGER
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS redeem_codes (
          code TEXT PRIMARY KEY,
          used_by TEXT,
          used_at INTEGER,
          notes TEXT
        );""")
        _db().execute("""
        CREATE TABLE IF NOT EXISTS sections (
          key TEXT PRIMARY KEY,
          title TEXT,
          desc TEXT,
          link TEXT,
          photo TEXT,
          is_free INTEGER DEFAULT 1,
          content TEXT,
          links TEXT
        );""")
        _db().execute("CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium);")
        _db().commit()

def init_db():
    migrate_db()
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT COUNT(*) AS n FROM sections"); n = c.fetchone()["n"]
        if n == 0:
            seed_sections = [
                ("python_zero", "🐍 بايثون من الصفر (مجاني)",
                 "دليلك الكامل لتعلّم البايثون من الصفر حتى الاحتراف مجانًا 🤩👑",
                 "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf",
                 None, 1, None, None),
                ("ecommerce_courses", "🛒 التجارة الإلكترونية (مجاني)",
                 "حزمة دورات وشروحات تجارة إلكترونية (أكثر من 7 ملفات).",
                 "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link",
                 None, 1, None, None),
                ("followers_safe", "🚀 نمو المتابعين (آمن)",
                 "بدائل آمنة للنمو بدل رشق متابعين المخالف.",
                 None, None, 1,
                 "• تحسين المحتوى + الهاشتاقات\n• تعاون/مسابقات\n• إعلانات ممولة دقيقة\n• فيديوهات قصيرة مع CTA",
                 "[]"),
                ("epic_recovery", "🎮 استرجاع حساب Epic (ربط PSN)",
                 "نموذج مراسلة شرعي لدعم Epic.",
                 None, None, 1, "نص إنجليزي جاهز للمراسلة.", None),
                ("virtual_numbers", "📱 أرقام مؤقتة (اختبار فقط)",
                 "تنبيه: استخدمها قانونيًا لأغراض تطوير/اختبار.",
                 None, None, 1, None,
                 '["https://receive-smss.com","https://smsreceivefree.com","http://sms24.me"]'),
                ("geolocation", "📍 تحديد الموقع عبر IP (معلومة عامة)",
                 "استخدم فقط بملكية/موافقة. تجنّب انتهاك الخصوصية.",
                 None, None, 1,
                 "أدخل IP لديك صلاحية فحصه؛ ستظهر معلومات عامة.",
                 '["https://www.geolocation.com/ar"]'),
                ("ai_hub", "🧠 الذكاء الاصطناعي (VIP)",
                 "مركز أدوات الذكاء الاصطناعي: دردشة AI.",
                 "https://t.me/ferpokss", None, 0, None, None),
                ("cyber_sec", "🛡️ الأمن السيبراني (VIP)",
                 "الأمن السيبراني من الصفر.",
                 "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A",
                 None, 0, None, None),
                ("canva_500", "🖼️ 500 دعوة Canva Pro (VIP)",
                 "دعوات كانفا برو مدى الحياة.",
                 "https://digital-plus3.com/products/canva500",
                 None, 0, None, None)
            ]
            _db().executemany("""
            INSERT OR IGNORE INTO sections(key,title,desc,link,photo,is_free,content,links)
            VALUES (?,?,?,?,?,?,?,?)
            """, seed_sections)
            _db().commit()

# ========== Helpers ==========
def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            _db().execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "premium": 0, "verified_ok": 0, "verified_at": 0, "ref": None}
        return dict(r)

def user_set_verify(uid: int|str, ok: bool):
    with _conn_lock:
        _db().execute("UPDATE users SET verified_ok=?, verified_at=? WHERE id=?",
                      (1 if ok else 0, int(time.time()), str(uid)))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])
def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),)); _db().commit()
def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),)); _db().commit()

def user_set_ref(uid: int|str, ref: str|None):
    with _conn_lock:
        _db().execute("UPDATE users SET ref=? WHERE id=?", (ref, str(uid))); _db().commit()

def create_payment(uid: int, provider: str) -> str:
    ref = "P" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    with _conn_lock:
        _db().execute("INSERT INTO payments(ref,user_id,created_at,paid,provider,meta) VALUES(?,?,?,?,?,?)",
                      (ref, str(uid), int(time.time()), 0, provider, "{}"))
        _db().commit()
    return ref

def mark_payment_paid(ref: str) -> bool:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT user_id FROM payments WHERE ref=?", (ref,))
        r = c.fetchone()
        if not r: return False
        uid = r["user_id"]
        _db().execute("UPDATE payments SET paid=1, meta=? WHERE ref=?", (json.dumps({"ts":int(time.time())}), ref))
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (uid,))
        _db().commit()
        return True

def is_payment_paid(ref: str) -> bool:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT paid FROM payments WHERE ref=?", (ref,))
        r = c.fetchone()
        return bool(r and r["paid"])

def log_download(uid: int, key: str):
    with _conn_lock:
        _db().execute("INSERT INTO downloads(user_id,key,ts) VALUES(?,?,?)",
                      (str(uid), key, int(time.time())))
        _db().commit()

def create_redeem(code: str, notes: str="") -> bool:
    with _conn_lock:
        try:
            _db().execute("INSERT INTO redeem_codes(code,notes) VALUES(?,?)",(code,notes)); _db().commit()
            return True
        except sqlite3.IntegrityError:
            return False

def use_redeem(code: str, uid: int) -> bool:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT used_by FROM redeem_codes WHERE code=?", (code,))
        r = c.fetchone()
        if not r or r["used_by"]:
            return False
        _db().execute("UPDATE redeem_codes SET used_by=?, used_at=? WHERE code=?",
                      (str(uid), int(time.time()), code))
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),))
        _db().commit()
        return True

# الأقسام
def sec_row_to_dict(r) -> dict:
    links = None
    if r["links"]:
        try:
            links = json.loads(r["links"])
        except Exception:
            links = None
    return {
        "key": r["key"], "title": r["title"], "desc": r["desc"], "link": r["link"],
        "photo": r["photo"], "is_free": bool(r["is_free"]), "content": r["content"],
        "links": links or []
    }

def load_sections_ordered() -> list[dict]:
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT * FROM sections ORDER BY is_free DESC, key ASC")
        rows = c.fetchall()
    return [sec_row_to_dict(r) for r in rows]

def build_section_text(sec: dict) -> str:
    parts = []
    if sec.get("title"): parts.append(sec["title"])
    if sec.get("desc"):  parts.append("\n" + sec["desc"])
    if sec.get("content"): parts.append("\n" + sec["content"])
    links = sec.get("links") or []
    if links:
        parts.append("\n🔗 روابط مفيدة:")
        for u in links:
            parts.append(u)
    link = sec.get("link")
    if link and (link not in links):
        parts.append("\n🔗 الرابط:"); parts.append(link)
    return "\n".join(parts).strip()

# ========= نصوص قصيرة =========
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "access_denied": "⚠️ هذا القسم خاص بمشتركي VIP.",
        "back": "↩️ رجوع",
        "ai_disabled": "🧠 ميزة الذكاء الاصطناعي غير مفعّلة حالياً (مفقود OPENAI_API_KEY).",
    }
    return M.get(k, k)

# ========= لوحات الأزرار =========
def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def bottom_menu_kb(uid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=OWNER_DEEP_LINK)],
    ])

_SECS = {}
def rebuild_sections_cache():
    global _SECS
    secs = load_sections_ordered()
    _SECS = {s["key"]: s for s in secs}

def _paginated_section_rows(keys: list[str], page: int = 0, per_page: int = 8):
    start = page * per_page; end = start + per_page
    rows = []
    for k in keys[start:end]:
        sec = _SECS[k]; lock = "🟢" if sec.get("is_free") else "🔒"
        rows.append([InlineKeyboardButton(f"{lock} {sec['title']}", callback_data=f"sec_{k}")])
    nav = []
    if start > 0: nav.append(InlineKeyboardButton("« السابق", callback_data=f"secpage_{page-1}"))
    if end < len(keys): nav.append(InlineKeyboardButton("التالي »", callback_data=f"secpage_{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])
    return rows

def sections_list_kb(page: int = 0):
    keys = list(_SECS.keys())
    return InlineKeyboardMarkup(_paginated_section_rows(keys, page))

def section_back_kb(page: int = 0):
    return InlineKeyboardMarkup([[InlineKeyboardButton("📂 رجوع للأقسام", callback_data=f"back_sections_{page}")]])

def vip_prompt_kb(ref: str | None = None):
    btns = []
    if PAYLINK_CHECKOUT_BASE:
        url = PAYLINK_CHECKOUT_BASE.replace("{ref}", ref or "ref")
        btns.append([InlineKeyboardButton("💳 Paylink", url=url)])
    if STRIPE_PAYMENT_LINK:
        btns.append([InlineKeyboardButton("💳 Stripe", url=STRIPE_PAYMENT_LINK)])
    btns.append([InlineKeyboardButton(tr("back"), callback_data="back_sections_0")])
    return InlineKeyboardMarkup(btns)

def ai_hub_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 دردشة AI", callback_data="ai_chat")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections_0")]
    ])

def ai_stop_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔚 إنهاء وضع الذكاء الاصطناعي", callback_data="ai_stop")],
        [InlineKeyboardButton("↩️ رجوع للأقسام", callback_data="back_sections_0")]
    ])

# ========= تعديل آمن =========
async def safe_edit(q, text=None, kb=None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                if kb is not None: await q.edit_message_reply_markup(reply_markup=kb)
            except BadRequest: pass
        else:
            log.warning("safe_edit error: %s", e)

# ========= حالات العضوية =========
ALLOWED_STATUSES = {"member", "administrator", "creator"}
_member_cache = {}

async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force=False, retries=3, backoff=0.7) -> bool:
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
                status = getattr(cm, "status", None)
                ok = (status in ALLOWED_STATUSES) if isinstance(status, str) else False
                if ok:
                    _member_cache[user_id] = (True, now + 120); user_set_verify(user_id, True); return True
            except Forbidden: pass
            except NetworkError: pass
            except Exception: pass
        if attempt < retries: await asyncio.sleep(backoff * attempt)
    _member_cache[user_id] = (False, now + 60); user_set_verify(user_id, False); return False

# ========= AI =========
def _ai_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        return tr("ai_disabled")
    try:
        if USE_RESPONSES_API:
            try:
                r = client.responses.create(
                    model=OPENAI_CHAT_MODEL,
                    input=[{"role":"user","content":[{"type":"input_text","text":prompt}]}],
                    text_format={"type":"text"}
                )
                txt = (getattr(r, "output_text", None) or "").strip()
                if txt: return txt
            except Exception as e:
                log.warning("Responses API failed: %s", e)
        r = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role":"system","content":"أجب بالعربية بإيجاز ووضوح. إن احتجت خطوات، اذكرها بنقاط."},
                {"role":"user","content":prompt}
            ],
            temperature=0.7
        )
        return (r.choices[0].message.content or "").strip()
    except Exception as e:
        msg = str(e).lower()
        if "quota" in msg: return "⚠️ نفاد الرصيد."
        if "api key" in msg: return "⚠️ مفتاح OpenAI غير صالح."
        return "⚠️ تعذّر التنفيذ حالياً."

# ========= مضاد سبام =========
_user_last_msg = {}
RATE_LIMIT = int(os.getenv("RATE_LIMIT","6"))
RATE_WINDOW = float(os.getenv("RATE_WINDOW","10.0"))

def anti_flood(uid: int) -> bool:
    now = time.time()
    L = _user_last_msg.get(uid, [])
    L = [t for t in L if now - t < RATE_WINDOW]; L.append(now)
    _user_last_msg[uid] = L
    return len(L) <= RATE_LIMIT

# ========= أوامر عامة =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 الأوامر:\n"
        "/start – بدء\n/help – مساعدة\n/buy – شراء VIP\n/paid <ref> – تحقق يدوي (اختياري)\n"
        "/debugverify – تشخيص التحقق\n/dv – تشخيص سريع"
    )

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application)
    await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    txt = f"member={ok}\n\n"; 
    if not ok: txt += need_admin_text()
    await update.message.reply_text(txt)

# ========= شراء VIP =========
def _pay_text(ref: str) -> str:
    return (
        f"💎 اشتراك VIP بقيمة {PRICE_USD}$\n"
        f"🔖 مرجع الدفع: {ref}\n\n"
        "اختر طريقة الدفع من الأزرار.\n"
        "بعد إتمام الدفع، سيتفعّل اشتراكك تلقائيًا خلال ثوانٍ.\n"
        "في حال تأخر التفعيل، أرسل: "
        f"`/paid {ref}` (اختياري كتحقق يدوي)."
    )

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ref = create_payment(uid, provider="link")
    await update.message.reply_text(_pay_text(ref), reply_markup=vip_prompt_kb(ref),
                                    disable_web_page_preview=True, parse_mode="Markdown")

async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم: /paid <ref>")
        return
    ref = context.args[0].strip()
    if is_payment_paid(ref):
        await update.message.reply_text("✅ تم التفعيل مسبقًا.")
        return
    ok = await verify_payment_remote(ref)
    await update.message.reply_text("✅ تم التفعيل الآن." if ok else "❌ لم يُرصد الدفع بعد.")

# ========= محقّق الدفع (Webhook + Polling) =========
async def verify_payment_remote(ref: str) -> bool:
    if not PAY_VERIFY_ENDPOINT:
        return False
    url = PAY_VERIFY_ENDPOINT.replace("{ref}", ref)
    headers = {}
    if PAY_VERIFY_AUTH: headers["Authorization"] = PAY_VERIFY_AUTH
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=headers, timeout=15) as r:
                if r.status != 200: return False
                data = await r.json()
                if bool(data.get("paid")):
                    return mark_payment_paid(ref)
    except Exception as e:
        log.warning("verify_payment_remote error: %s", e)
    return False

async def payment_poller(app: Application):
    if not PAY_VERIFY_ENDPOINT:
        log.info("payment poller disabled (no PAY_VERIFY_ENDPOINT)")
        return
    while True:
        try:
            with _conn_lock:
                c = _db().cursor()
                c.execute("SELECT ref FROM payments WHERE paid=0 ORDER BY created_at DESC LIMIT 30")
                refs = [r["ref"] for r in c.fetchall()]
            for ref in refs:
                try:
                    await verify_payment_remote(ref)
                except Exception as e:
                    log.warning("poll verify error %s: %s", ref, e)
            await asyncio.sleep(PAY_POLL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("payment_poller loop error: %s", e)
            await asyncio.sleep(10)

# —— Webhook داخلي يستقبل إشعار بوابة الدفع —— #
def _extract_ref_from_payload(data: dict) -> str | None:
    keys_direct = ["ref", "reference", "order_id", "invoice_id", "client_reference_id"]
    for k in keys_direct:
        if isinstance(data.get(k), str) and data[k]:
            return data[k]
    meta = data.get("metadata") or {}
    if isinstance(meta, dict):
        for k in ["ref", "client_reference_id", "reference"]:
            if isinstance(meta.get(k), str) and meta[k]:
                return meta[k]
    deep = data.get("data") or data.get("object") or {}
    if isinstance(deep, dict):
        got = _extract_ref_from_payload(deep)
        if got: return got
    return None

async def pay_webhook_handler(request: web.Request) -> web.Response:
    secret = request.headers.get("X-Webhook-Secret") or request.headers.get("X-Secret")
    if secret != PAY_WEBHOOK_SECRET:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    paid_flags = ["paid", "is_paid", "success", "status"]
    is_paid = False
    status_val = payload.get("status")
    if isinstance(status_val, str):
        is_paid = status_val.lower() in ("paid", "succeeded", "success", "captured")
    if not is_paid:
        for k in paid_flags:
            v = payload.get(k)
            if isinstance(v, bool) and v: is_paid = True

    ref = _extract_ref_from_payload(payload)
    if not ref:
        return web.json_response({"ok": False, "error": "no_ref"}, status=400)

    if is_paid:
        ok = mark_payment_paid(ref)
        return web.json_response({"ok": True, "activated": ok, "ref": ref})
    return web.json_response({"ok": True, "activated": False, "ref": ref})

async def run_pay_webhook_server():
    if not PAY_WEBHOOK_ENABLED:
        log.info("Payment webhook disabled.")
        return
    app = web.Application()
    app.add_routes([web.post(PAY_WEBHOOK_PATH, pay_webhook_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=PAY_WEBHOOK_HOST, port=PAY_WEBHOOK_PORT)
    await site.start()
    log.info(f"Payment webhook listening on http://{PAY_WEBHOOK_HOST}:{PAY_WEBHOOK_PORT}{PAY_WEBHOOK_PATH}")

# ========= إدارة الأقسام (مالك) =========
def _normalize_bool(v: str) -> int:
    return 1 if v.strip().lower() in ("1","true","yes","y","on","free","مجاني") else 0

async def addsec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    txt = update.message.text.split(" ",1)
    if len(txt) < 2 or "|" not in txt[1]:
        await update.message.reply_text("استخدم: /addsec key | title | desc | link | is_free(1/0)")
        return
    parts = [p.strip() for p in txt[1].split("|")]
    while len(parts) < 5: parts.append("")
    key, title, desc, link, is_free = parts[:5]
    with _conn_lock:
        _db().execute("""
        INSERT OR REPLACE INTO sections(key,title,desc,link,photo,is_free,content,links)
        VALUES(?,?,?,?,?,?,?,?)
        """,(key, title, desc, link, None, _normalize_bool(is_free), None, None))
        _db().commit()
    rebuild_sections_cache()
    await update.message.reply_text(f"✅ تمت إضافة/تحديث القسم: {key}")

async def editsec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if len(context.args) < 3:
        await update.message.reply_text("استخدم: /editsec <key> <field> <value>")
        return
    key = context.args[0]; field = context.args[1]; value = " ".join(context.args[2:])
    if field == "is_free": value = str(_normalize_bool(value))
    with _conn_lock:
        _db().execute(f"UPDATE sections SET {field}=? WHERE key=?", (value, key)); _db().commit()
    rebuild_sections_cache()
    await update.message.reply_text(f"✅ تم تعديل {field} للقسم {key}")

async def delsec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /delsec <key>")
        return
    key = context.args[0]
    with _conn_lock:
        _db().execute("DELETE FROM sections WHERE key=?", (key,)); _db().commit()
    rebuild_sections_cache()
    await update.message.reply_text(f"🗑️ تم حذف القسم: {key}")

async def listsec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    secs = load_sections_ordered()
    if not secs:
        await update.message.reply_text("لا توجد أقسام."); return
    lines = [f"- {s['key']} | {'🟢' if s['is_free'] else '🔒'} | {s['title']}" for s in secs]
    await update.message.reply_text("\n".join(lines))

# ========= لوحة التحكم =========
def _admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("🎟️ أكواد تفعيل", callback_data="admin_codes")],
        [InlineKeyboardButton("⬇️ التنزيلات الأخيرة", callback_data="admin_dl")],
        [InlineKeyboardButton("↩️ إغلاق", callback_data="admin_close")]
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text("لوحة التحكم:", reply_markup=_admin_kb())

# ========= إحصائيات/بث/أكواد =========
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT COUNT(*) AS n FROM users"); total = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM users WHERE premium=1"); vip = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM users WHERE verified_ok=1"); verf = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM payments WHERE paid=1"); paid = c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM redeem_codes WHERE used_by IS NOT NULL"); used = c.fetchone()["n"]
    await update.message.reply_text(
        f"👥 إجمالي: {total}\n⭐️ VIP: {vip}\n✅ متحققين: {verf}\n💳 مدفوعات مؤكدة: {paid}\n🎟️ أكواد مستخدمة: {used}"
    )

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not context.args:
        await update.message.reply_text("استخدم: /broadcast <رسالة>"); return
    text = update.message.text.split(" ", 1)[1]
    sent = 0; failed = 0
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT id FROM users")
        ids = [int(r["id"]) for r in c.fetchall()]
    for uid in ids:
        try:
            await context.bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1; await asyncio.sleep(0.02)
        except Exception:
            failed += 1
    await update.message.reply_text(f"تم الإرسال ✅ {sent}, فشل ❌ {failed}")

async def newcode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    code = "".join(random.choices(string.ascii_uppercase+string.digits, k=12))
    ok = create_redeem(code, notes="manual")
    await update.message.reply_text(f"✅ كود جديد: `{code}`" if ok else "❌ تعذر إنشاء كود.", parse_mode="Markdown")

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("استخدم: /redeem <CODE>"); return
    code = context.args[0].strip().upper()
    await update.message.reply_text("✅ تم تفعيل VIP." if use_redeem(code, uid) else "❌ كود غير صالح أو مستخدم.")

# ========= /start =========
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db(); rebuild_sections_cache()
    uid = update.effective_user.id; chat_id = update.effective_chat.id
    user_get(uid)
    if context.args: user_set_ref(uid, context.args[0][:64])

    try:
        if WELCOME_PHOTO and Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception: pass

    try:
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    except Exception:
        ok = False

    if not ok:
        await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        await context.bot.send_message(chat_id, need_admin_text()); return

    await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb(0))

# ========= الأزرار =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db(); rebuild_sections_cache()
    q = update.callback_query; uid = q.from_user.id
    await q.answer()
    data = q.data or ""

    # لوحة التحكم
    if uid == OWNER_ID and data.startswith("admin_"):
        if data == "admin_close":
            await safe_edit(q, "تم الإغلاق."); return
        if data == "admin_stats":
            with _conn_lock:
                c = _db().cursor()
                c.execute("SELECT COUNT(*) AS n FROM users"); total = c.fetchone()["n"]
                c.execute("SELECT COUNT(*) AS n FROM users WHERE premium=1"); vip = c.fetchone()["n"]
                c.execute("SELECT ref,paid,provider,created_at FROM payments ORDER BY created_at DESC LIMIT 5")
                pays = c.fetchall()
            s = [f"👥 {total} مستخدم | ⭐️ VIP: {vip}", "آخر 5 مدفوعات:"]
            for p in pays:
                s.append(f"- {p['ref']} | {'✅' if p['paid'] else '⌛'} | {p['provider']} | {time.strftime('%Y-%m-%d %H:%M', time.localtime(p['created_at']))}")
            await safe_edit(q, "\n".join(s), kb=_admin_kb()); return
        if data == "admin_codes":
            with _conn_lock:
                c = _db().cursor()
                c.execute("SELECT code,used_by,used_at FROM redeem_codes ORDER BY used_at DESC")
                rows = c.fetchall()
            lines = ["🎟️ الأكواد (الأحدث أولاً):"]
            for r in rows[:10]:
                status = f"✅ {r['used_by']}" if r["used_by"] else "متاح"
                when = time.strftime('%Y-%m-%d %H:%M', time.localtime(r['used_at'])) if r['used_at'] else "-"
                lines.append(f"- {r['code']} | {status} | {when}")
            await safe_edit(q, "\n".join(lines) if len(lines)>1 else "لا توجد أكواد.", kb=_admin_kb()); return
        if data == "admin_dl":
            with _conn_lock:
                c = _db().cursor()
                c.execute("SELECT user_id,key,ts FROM downloads ORDER BY ts DESC LIMIT 10")
                rows = c.fetchall()
            lines = ["⬇️ آخر التنزيلات:"]
            for r in rows:
                when = time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))
                lines.append(f"- {r['user_id']} | {r['key']} | {when}")
            await safe_edit(q, "\n".join(lines) if len(lines)>1 else "لا شيء.", kb=_admin_kb()); return

    # صفحات الأقسام
    if data.startswith("secpage_"):
        try: page = int(data.split("_",1)[1])
        except Exception: page = 0
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb(page)); return

    if data.startswith("back_sections"):
        try: page = int(data.split("_",2)[2])
        except Exception: page = 0
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb(page)); return

    if data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb(0))
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك.\nانضم ثم اضغط تحقّق.\n\n" + need_admin_text(), kb=gate_kb())
        return

    if not await is_member(context, uid, retries=3, backoff=0.7):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n\n— شارك المعرف مع الإدارة للترقية إلى VIP.", kb=bottom_menu_kb(uid)); return
    if data == "upgrade":
        ref = create_payment(uid, provider="link")
        await safe_edit(q, _pay_text(ref), kb=vip_prompt_kb(ref)); return
    if data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return

    # AI
    if data == "ai_chat":
        if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, f"🔒 الذكاء الاصطناعي (VIP)\n\n{tr('access_denied')}\n\n💳 {PRICE_USD}$ — استخدم /buy.", kb=vip_prompt_kb()); return
        ai_set_mode(uid, "ai_chat"); await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك الآن.", kb=ai_stop_kb()); return

    if data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb(0)); return

    # الأقسام
    if data.startswith("sec_"):
        key = data.replace("sec_", "")
        sec = _SECS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", kb=sections_list_kb(0)); return

        if key == "ai_hub":
            if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
            if not (sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID):
                await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 {PRICE_USD}$ — استخدم /buy.", kb=vip_prompt_kb()); return
            await safe_edit(q, f"{sec['title']}\n\n{sec['desc']}\n\nاختر أداة:", kb=ai_hub_kb()); return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            ref = create_payment(uid, provider="link")
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 {PRICE_USD}$ — اشترك للوصول.", kb=vip_prompt_kb(ref)); return

        text = build_section_text(sec)
        photo = sec.get("photo"); local = sec.get("local_file")
        try:
            kb = q.message.reply_markup.inline_keyboard
            page = int((kb[-1][0].callback_data or "back_sections_0").split("_")[-1])
        except Exception:
            page = 0

        if local and Path(local).exists():
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}", kb=section_back_kb(page))
            with open(local, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=text)
            log_download(uid, key)
        elif photo:
            await safe_edit(q, f"{sec['title']}\n\n{sec.get('desc','')}", kb=section_back_kb(page))
            try:
                await q.message.reply_photo(photo=photo, caption=text)
            except Exception:
                await q.message.reply_text(text, reply_markup=section_back_kb(page))
        else:
            await safe_edit(q, text, kb=section_back_kb(page))
        return

# ========= رسائل عامة =========
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not anti_flood(uid): return
    if not await is_member(context, uid, retries=3, backoff=0.7):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return
    mode = ai_get_mode(uid)
    if mode == "ai_chat":
        t = (update.message.text or "").strip()
        if not t: return
        try: await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        except Exception: pass
        await update.message.reply_text(_ai_reply(t), reply_markup=ai_stop_kb()); return
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    await update.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb(0))

# ========= أخطاء عامة =========
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("⚠️ Error: %s", getattr(context, 'error', 'unknown'))

# ========= بناء التطبيق =========
def build_app():
    init_db(); rebuild_sections_cache()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", user_grant))
    app.add_handler(CommandHandler("revoke", user_revoke))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler(["debugverify","dv"], debug_verify))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("newcode", newcode_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))
    app.add_handler(CommandHandler("addsec", addsec_cmd))
    app.add_handler(CommandHandler("editsec", editsec_cmd))
    app.add_handler(CommandHandler("delsec", delsec_cmd))
    app.add_handler(CommandHandler("listsec", listsec_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_error_handler(on_error)
    return app

# ========= التشغيل (Loop-safe) =========
def main():
    app = build_app()
    loop = asyncio.get_event_loop()

    # 1) Webhook الدفع (aiohttp) — يعمل كتاسك موازٍ
    loop.create_task(run_pay_webhook_server())

    # 2) Poller احتياطي للتحقق الدوري
    loop.create_task(payment_poller(app))

    # 3) شغّل تلجرام: polling أو webhook — مع منع إغلاق اللوب من PTB
    if USE_TELEGRAM_WEBHOOK and TELEGRAM_WEBHOOK_URL:
        log.info("Starting Telegram webhook on %s", TELEGRAM_WEBHOOK_URL + TELEGRAM_WEBHOOK_PATH)
        app.run_webhook(
            listen="0.0.0.0",
            port=TELEGRAM_WEBHOOK_PORT,
            url_path=TELEGRAM_WEBHOOK_PATH,
            webhook_url=TELEGRAM_WEBHOOK_URL + TELEGRAM_WEBHOOK_PATH,
            ip_address=TELEGRAM_WEBHOOK_IP or None,
            close_loop=False  # <-- مهم
        )
    else:
        log.info("Starting Telegram polling...")
        app.run_polling(close_loop=False)  # <-- مهم

if __name__ == "__main__":
    main()

