# -*- coding: utf-8 -*-
import os, sqlite3, threading, time, asyncio
from pathlib import Path
from dotenv import load_dotenv

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
    load_dotenv(ENV_PATH)

# ====== إعدادات أساسية ======
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN مفقود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
try:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1")

# تحقق توافق httpx مع openai 1.x (يتعطل مع httpx>=0.28)
def _httpx_is_compatible() -> bool:
    try:
        from importlib.metadata import version
        v = version("httpx")
        parts = v.split(".")
        major = int(parts[0]); minor = int(parts[1]) if len(parts) > 1 else 0
        if major == 0 and minor >= 28: return False
        if major >= 1: return False
        return True
    except Exception:
        return True

HTTPX_OK = _httpx_is_compatible()

AI_ENABLED = bool(OPENAI_API_KEY) and (OpenAI is not None) and HTTPX_OK
client = OpenAI(api_key=OPENAI_API_KEY) if AI_ENABLED else None

OWNER_ID = int(os.getenv("OWNER_ID", "6468743821"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")
# رابط دردشتك الذي أعطيتني إياه (يُمكن تغييره من Environment عبر ADMIN_CONTACT_URL)
ADMIN_CONTACT_URL = os.getenv(
    "ADMIN_CONTACT_URL",
    "https://web.telegram.org/k/?account=2#6468743821"
).strip()

# قناة الاشتراك
MAIN_CHANNEL_USERNAMES = (os.getenv("MAIN_CHANNELS","ferpokss,Ferp0ks").split(","))
MAIN_CHANNEL_USERNAMES = [u.strip().lstrip("@") for u in MAIN_CHANNEL_USERNAMES if u.strip()]
MAIN_CHANNEL_LINK = f"https://t.me/{MAIN_CHANNEL_USERNAMES[0]}"

def need_admin_text() -> str:
    return f"⚠️ لو ما اشتغل التحقق: تأكّد أن البوت **مشرف** في @{MAIN_CHANNEL_USERNAMES[0]}."

def admin_http_url() -> str | None:
    if ADMIN_CONTACT_URL:
        return ADMIN_CONTACT_URL
    if OWNER_USERNAME:
        return f"https://t.me/{OWNER_USERNAME}"
    return None

WELCOME_PHOTO = os.getenv("WELCOME_PHOTO","assets/ferpoks.jpg")
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "هنا تلاقي مصادر وأدوات للتجارة الإلكترونية، بايثون، الأمن السيبراني وغيرهم.\n"
    "المحتوى المجاني متاح للجميع، ومحتوى VIP فيه ميزات أقوى. ✨"
)

CHANNEL_ID = None  # سيُحل عند الإقلاع

# ====== خادِم صحي لــ Render (اختياري) ======
SERVE_HEALTH = os.getenv("SERVE_HEALTH", "0") == "1"  # افتراضيًا متوقف
try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False

def _run_health_server():
    if not (SERVE_HEALTH and AIOHTTP_AVAILABLE):
        print("[health] aiohttp غير متوفر أو SERVE_HEALTH=0 — تخطي الخادم الصحي")
        return
    async def _health(_): return web.Response(text="OK")
    try:
        app = web.Application()
        app.router.add_get("/", _health)
        port = int(os.getenv("PORT", "10000"))
        print(f"[health] starting on 0.0.0.0:{port}")
        web.run_app(app, port=port)
    except Exception as e:
        print("[health] failed:", e)

# شغّل الخادم الصحي في ثريد جانبي
threading.Thread(target=_run_health_server, daemon=True).start()

# ====== عند الإقلاع ======
async def on_startup(app: Application):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print("[startup] delete_webhook:", e)

    global CHANNEL_ID
    CHANNEL_ID = None
    for u in MAIN_CHANNEL_USERNAMES:
        try:
            chat = await app.bot.get_chat(f"@{u}")
            CHANNEL_ID = chat.id
            print(f"[startup] resolved @{u} -> chat_id={CHANNEL_ID}")
            break
        except Exception as e:
            print(f"[startup] get_chat @{u} failed:", e)
    if CHANNEL_ID is None:
        print("[startup] ❌ could not resolve channel id; fallback to @username checks")

    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "بدء"),
                BotCommand("help", "مساعدة"),
                BotCommand("debugverify", "تشخيص التحقق"),
                BotCommand("dv", "تشخيص سريع"),
            ],
            scope=BotCommandScopeDefault()
        )
    except Exception as e:
        print("[startup] set_my_commands default:", e)

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
            ],
            scope=BotCommandScopeChat(chat_id=OWNER_ID)
        )
    except Exception as e:
        print("[startup] set_my_commands owner:", e)

# ====== قاعدة البيانات ======
_conn_lock = threading.Lock()
def _db():
    conn = getattr(_db, "_conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
        out = dict(r)
        out.setdefault("verified_ok", 0)
        out.setdefault("verified_at", 0)
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
        )
        _db().commit()

def ai_get_mode(uid: int|str):
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT mode FROM ai_state WHERE user_id=?", (str(uid),))
        r = c.fetchone()
        return r["mode"] if r else None

# ====== نصوص قصيرة ======
def tr(k: str) -> str:
    M = {
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
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
            "تنبيه: شراء/رشق متابعين قد يخالف سياسات المنصات ويعرّض الحسابات للإغلاق.\n"
            "بدائل آمنة:\n"
            "• تحسين المحتوى + الهاشتاقات\n"
            "• تعاون/مسابقات\n"
            "• إعلانات ممولة دقيقة الاستهداف\n"
            "• محتوى قصير متكرر مع CTA واضح"
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
            "However, my PlayStation Network (PSN) is currently linked incorrectly and I cannot link my own PSN.\n\n"
            "Could you please review my account and help with either unlinking the current PSN or allowing me to link my PSN again?\n\n"
            "Account details:\n- Email: ____\n- Display name: ____\n- Country/Region: ____\n\n"
            "Thank you for your help."
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
        "content": (
            "1) https://telegram.org/support\n"
            "2) اكتب طلباً واضحاً لرفع التقييد مع رقمك وإصدار التطبيق."
        )
    },
    "dev_test_cards": {
        "title": "💳 فيزا وهمية (بيئة اختبار)",
        "desc": "استخدم بطاقات الاختبار الرسمية (Stripe/PayPal Sandbox) داخل بيئات التطوير فقط.",
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
        "link": "https://digital-plus3.com/products/canva500?srsltid=AfmBOoq01P0ACvybFJkhb2yVBPSUPJadwrOw9LZmNxSUzWPDY8v_42C1",
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

# (اختياري) روابط الرشق الأصلية عبر متغير بيئة
if os.getenv("ENABLE_FOLLOW_LINKS", "0") == "1":
    SECTIONS["followers_links"] = {
        "title": "📈 مواقع رشق متابعين (مسؤوليتك)",
        "desc": "قد تخالف سياسات المنصات.",
        "is_free": True,
        "links": [
            "https://zyadat.com/",
            "https://followadd.com/",
            "https://smmcpan.com/",
            "https://seoclevers.com/",
            "https://followergi.com/",
            "https://seorrs.com/",
            "https://drd3m.com/ref/ixeuw"
        ]
    }

# ====== لوحات الأزرار ======
def gate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def bottom_menu_kb(uid: int):
    rows = [[InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
            [InlineKeyboardButton("⚡ ترقية إلى VIP", callback_data="upgrade")]]
    if admin_http_url():
        rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", url=admin_http_url())])
    else:
        rows.append([InlineKeyboardButton("📨 تواصل مع الإدارة", callback_data="contact_admin")])
    return InlineKeyboardMarkup(rows)

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

def vip_prompt_kb():
    if admin_http_url():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ اشترك الآن / تواصل", url=admin_http_url())],
            [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ اشترك الآن / تواصل", callback_data="contact_admin")],
            [InlineKeyboardButton(tr("back"), callback_data="back_sections")]
        ])

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
            await q.edit_message_text(text, reply_markup=kb)
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
            print("safe_edit error:", e)

# ====== حالات العضوية ======
ALLOWED_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
try:
    ALLOWED_STATUSES.add(ChatMemberStatus.OWNER)
except AttributeError:
    pass
try:
    ALLOWED_STATUSES.add(ChatMemberStatus.CREATOR)
except AttributeError:
    pass

# ====== التحقق من العضوية ======
_member_cache = {}  # {uid: (ok, expire)}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                    force=False, retries=3, backoff=0.7) -> bool:
    now = time.time()
    if not force:
        cached = _member_cache.get(user_id)
        if cached and cached[1] > now:
            return cached[0]

    for attempt in range(1, retries + 1):
        targets = [CHANNEL_ID] if CHANNEL_ID is not None else [f"@{u}" for u in MAIN_CHANNEL_USERNAMES]
        for target in targets:
            try:
                cm = await context.bot.get_chat_member(target, user_id)
                status = getattr(cm, "status", None)
                print(f"[is_member] try#{attempt} target={target} status={status} user={user_id}")
                ok = status in ALLOWED_STATUSES
                if ok:
                    _member_cache[user_id] = (True, now + 60)
                    user_set_verify(user_id, True)
                    return True
            except Exception as e:
                print(f"[is_member] try#{attempt} target={target} ERROR:", e)
        if attempt < retries:
            await asyncio.sleep(backoff * attempt)

    _member_cache[user_id] = (False, now + 60)
    user_set_verify(user_id, False)
    return False

# ====== AI ======
def _chat_with_fallback(messages):
    if not AI_ENABLED or client is None:
        return None, "ai_disabled"

    primary = (OPENAI_CHAT_MODEL or "").strip()
    fallbacks = []
    if primary: fallbacks.append(primary)
    for m in ["gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]:
        if m not in fallbacks:
            fallbacks.append(m)

    last_err = None
    for model in fallbacks:
        try:
            r = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7
            )
            return r, None
        except Exception as e:
            msg = str(e); last_err = msg
            if ("model_not_found" in msg or "Not found" in msg or "deprecated" in msg):
                continue
            if "insufficient_quota" in msg or "You exceeded your current quota" in msg:
                return None, "quota"
            if "invalid_api_key" in msg or "Incorrect API key" in msg:
                return None, "apikey"
            continue
    return None, (last_err or "unknown")

def ai_chat_reply(prompt: str) -> str:
    if not AI_ENABLED or client is None:
        if not HTTPX_OK:
            return "⚠️ تعذّر تفعيل AI بسبب نسخة httpx غير متوافقة. ثبّت httpx<0.28."
        return tr("ai_disabled")
    try:
        r, err = _chat_with_fallback([
            {"role":"system","content":"أجب بالعربية بإيجاز ووضوح. إن احتجت خطوات، اذكرها بنقاط."},
            {"role":"user","content":prompt}
        ])
        if err == "ai_disabled":
            return tr("ai_disabled")
        if err == "quota":
            return "⚠️ نفاد الرصيد في حساب OpenAI."
        if err == "apikey":
            return "⚠️ مفتاح OpenAI غير صالح أو مفقود."
        if r is None:
            return "⚠️ تعذّر التنفيذ حالياً. جرّب لاحقاً."
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return "⚠️ تعذّر التنفيذ حالياً. جرّب لاحقاً."

# ====== العرض ======
def build_section_text(sec: dict) -> str:
    parts = []
    title = sec.get("title", "")
    desc  = sec.get("desc", "")
    link  = sec.get("link")
    links = sec.get("links", [])
    content = sec.get("content")

    if title: parts.append(title)
    if desc:  parts.append("\n" + desc)
    if content: parts.append("\n" + content)

    if links:
        parts.append("\n🔗 روابط مفيدة:")
        for u in links: parts.append(u)

    if link and link not in links:
        parts.append("\n🔗 الرابط:"); parts.append(link)

    return "\n".join(parts).strip()

# ====== أوامر ======
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/help – مساعدة\n/debugverify – تشخيص التحقق\n/dv – تشخيص سريع")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(str(update.effective_user.id))

async def refresh_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await on_startup(context.application)
    await update.message.reply_text("✅ تم تحديث قائمة الأوامر.")

async def aidiag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        from importlib.metadata import version, PackageNotFoundError
        def v(pkg):
            try: return version(pkg)
            except PackageNotFoundError: return "not-installed"
        k = (os.getenv("OPENAI_API_KEY") or "").strip()
        msg = (
            f"AI_ENABLED={'ON' if AI_ENABLED else 'OFF'}\n"
            f"Key={'set(len=%d)'%len(k) if k else 'missing'}\n"
            f"Model={OPENAI_CHAT_MODEL}\n"
            f"httpx={v('httpx')} (ok={HTTPX_OK})\n"
            f"openai={v('openai')}"
        )
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
        msg = (
            f"python-telegram-bot={v('python-telegram-bot')}\n"
            f"httpx={v('httpx')}\n"
            f"httpcore={v('httpcore')}\n"
            f"openai={v('openai')}\n"
            f"python={os.sys.version.split()[0]}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"libdiag error: {e}")

async def debug_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    await update.message.reply_text(f"member={ok} (check logs for details)")

# ====== /start ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    user_get(uid)

    try:
        if Path(WELCOME_PHOTO).exists():
            with open(WELCOME_PHOTO, "rb") as f:
                await context.bot.send_photo(chat_id, InputFile(f), caption=WELCOME_TEXT_AR)
        else:
            await context.bot.send_message(chat_id, WELCOME_TEXT_AR)
    except Exception as e:
        print("[welcome] ERROR:", e)

    try:
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
    except Exception as e:
        print("[start] is_member ERROR:", e); ok = False

    if not ok:
        try:
            await context.bot.send_message(chat_id, "🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
            await context.bot.send_message(chat_id, need_admin_text())
        except Exception as e:
            print("[start] gate send ERROR:", e)
        return

    try:
        await context.bot.send_message(chat_id, "👇 القائمة:", reply_markup=bottom_menu_kb(uid))
        await context.bot.send_message(chat_id, "📂 الأقسام:", reply_markup=sections_list_kb())
    except Exception as e:
        print("[start] menu send ERROR:", e)

# ====== الأزرار ======
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if q.data == "verify":
        ok = await is_member(context, uid, force=True, retries=3, backoff=0.7)
        if ok:
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\nاختر من القائمة بالأسفل:", kb=bottom_menu_kb(uid))
            await q.message.reply_text("📂 الأقسام:", reply_markup=sections_list_kb())
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك أو تعذّر التحقق.\nانضم ثم اضغط تحقّق.\n\n" + need_admin_text(), kb=gate_kb())
        return

    if not await is_member(context, uid, retries=3, backoff=0.7):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", kb=gate_kb()); return

    if q.data == "myinfo":
        await safe_edit(q, f"👤 اسمك: {q.from_user.full_name}\n🆔 معرفك: {uid}\n\n— شارك المعرف مع الإدارة للترقية إلى VIP.", kb=bottom_menu_kb(uid)); return
    if q.data == "upgrade":
        await safe_edit(q, "💳 ترقية إلى VIP بـ 10$.\nتواصل مع الإدارة لإتمام الترقية:", kb=vip_prompt_kb()); return
    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", kb=bottom_menu_kb(uid)); return
    if q.data == "back_sections":
        await safe_edit(q, "📂 الأقسام:", kb=sections_list_kb()); return

    # تواصل مع الإدارة (Fallback)
    if q.data == "contact_admin":
        try:
            link = admin_http_url()
            msg = (
                f'📨 للتواصل مع الإدارة:\n'
                + (f'{link}\n' if link else '')
                + f'<a href="tg://user?id={OWNER_ID}">اضغط هنا لفتح الدردشة</a>\n'
                f'🆔 معرّفك: <code>{uid}</code>'
            )
            await q.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            print("[contact_admin] ERROR:", e)
        return

    # AI
    if q.data == "ai_chat":
        if not AI_ENABLED:
            await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, f"🔒 {SECTIONS['ai_hub']['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); 
            return
        ai_set_mode(uid, "ai_chat")
        await safe_edit(q, "🤖 وضع الدردشة مفعّل.\nأرسل سؤالك الآن.", kb=ai_stop_kb()); 
        return

    if q.data == "ai_stop":
        ai_set_mode(uid, None); await safe_edit(q, "🔚 تم إنهاء وضع الذكاء الاصطناعي.", kb=sections_list_kb()); return

    # الأقسام
    if q.data.startswith("sec_"):
        key = q.data.replace("sec_", "")
        sec = SECTIONS.get(key)
        if not sec: await safe_edit(q, "قريباً…", kb=sections_list_kb()); return

        if key == "ai_hub":
            if not AI_ENABLED: await safe_edit(q, tr("ai_disabled"), kb=vip_prompt_kb()); return
            if not (sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID):
                await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return
            await safe_edit(q, f"{sec['title']}\n\n{sec['desc']}\n\nاختر أداة:", kb=ai_hub_kb()); return

        allowed = sec.get("is_free") or user_is_premium(uid) or uid == OWNER_ID
        if not allowed:
            await safe_edit(q, f"🔒 {sec['title']}\n\n{tr('access_denied')}\n\n💳 10$ — راسل الإدارة.", kb=vip_prompt_kb()); return

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

# ====== رسائل عامة ======
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_get(uid)

    if not await is_member(context, uid, retries=3, backoff=0.7):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb()); return

    mode = ai_get_mode(uid)
    if mode == "ai_chat":
        t = (update.message.text or "").strip()
        if not t: return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await update.message.reply_text(ai_chat_reply(t), reply_markup=ai_stop_kb()); return

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

# ====== أخطاء عامة ======
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Error: {getattr(context, 'error', 'unknown')}")

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
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("refreshcmds", refresh_cmds))
    app.add_handler(CommandHandler("aidiag", aidiag))
    app.add_handler(CommandHandler("libdiag", libdiag))
    app.add_handler(CommandHandler(["debugverify","dv"], debug_verify))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
