# -*- coding: utf-8 -*-
import os, sqlite3, threading, time
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InputFile, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest

# ========= بيئة التشغيل =========
ENV_PATH = Path(".env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN غير موجود")

DB_PATH = os.getenv("DB_PATH", "/var/data/bot.db")
_conn_lock = threading.Lock()

def _db():
    conn = getattr(_db, "_conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _db._conn = conn
    return conn

def init_db():
    with _conn_lock:
        _db().execute("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          lang TEXT DEFAULT 'ar',
          premium INTEGER DEFAULT 0
        );
        """)
        _db().commit()

def user_get(uid: int|str) -> dict:
    uid = str(uid)
    with _conn_lock:
        c = _db().cursor()
        c.execute("SELECT id, lang, premium FROM users WHERE id=?", (uid,))
        r = c.fetchone()
        if not r:
            c.execute("INSERT INTO users (id) VALUES (?);", (uid,))
            _db().commit()
            return {"id": uid, "lang": "ar", "premium": 0}
        return {"id": r["id"], "lang": r["lang"], "premium": r["premium"]}

def user_grant(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=1 WHERE id=?", (str(uid),))
        _db().commit()

def user_revoke(uid: int|str):
    with _conn_lock:
        _db().execute("UPDATE users SET premium=0 WHERE id=?", (str(uid),))
        _db().commit()

def user_is_premium(uid: int|str) -> bool:
    return bool(user_get(uid)["premium"])

# ========= ثوابت =========
OWNER_ID = 6468743821                 # حسابك فقط
ADMIN_IDS = {OWNER_ID}                # أوامر المدير محصورة عليك

# القناة:
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID", "-1002840134926"))  # معرّف القناة
MAIN_CHANNEL_LINK = "https://t.me/+oIYmTi_gWuxiNmZk"                   # زر الانضمام

# رابط محادثتك (يعمل بدون @يوزر)
OWNER_DEEP_LINK = "tg://user?id=6468743821"

WELCOME_PHOTO = "assets/ferpoks.jpg"
WELCOME_TEXT_AR = (
    "مرحباً بك في بوت فيربوكس 🔥\n"
    "تعرف على أرخص المصادر، موردي الاشتراكات، أدوات زيادة المتابعين والمزيد.\n"
    "🎯 افعل كل شيء بنفسك."
)

# الروابط/الأقسام
LINKS = {
    "suppliers_pack": {
        "title": "📦 بكج الموردين",
        "desc": "ملف شامل لأرقام ومصادر الموردين.",
        "buttons": [
            ("فتح المستند", "https://docs.google.com/document/d/1rR2nJMUNDoj0cogeenVh9fYVs_ZTM5W0bl0PBIOVwL0/edit?tab=t.0"),
        ],
    },
    "kash_malik": {
        "title": "♟️ كش ملك",
        "desc": "مرجع كبير حول التجارة والتواصل الاجتماعي.",
        "local_file": "assets/kash-malik.docx",  # ضع الملف بهذا الاسم إن أردت إرساله
    },
    "cyber_sec": {
        "title": "🛡️ الأمن السيبراني",
        "desc": "مراجع ودورات الأمن السيبراني.",
        "buttons": [
            ("ملف 1", "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/pZ0spOmm1K0dA2qAzUuWUb4CcMMjUPTbn7WMRwAc.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250810%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250810T000214Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=aef54ed1c5d583f14beac04516dcf0c69059dfd3a3bf1f9618ea96310841d939"),
            ("ملف/مجلد 2", "https://www.mediafire.com/folder/r26pp5mpduvnx/%D8%AF%D9%88%D8%B1%D8%A9_%D8%A7%D9%84%D9%87%D8%A7%D9%83%D8%B1_%D8%A7%D9%84%D8%A7%D8%AE%D9%84%D8%A7%D9%82%D9%8A_%D8%B9%D8%A8%D8%AF%D8%A7%D9%84%D8%B1%D8%AD%D9%85%D9%86_%D9%88%D8%B5%D9%81%D9%8A"),
        ],
    },
    "python_zero": {
        "title": "🐍 البايثون من الصفر",
        "desc": "ابدأ بايثون من الصفر بمراجع منظّمة.",
        "buttons": [
            ("ملف PDF", "https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf?X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAT2PZV5Y3LHXL7XVA%2F20250810%2Feu-central-1%2Fs3%2Faws4_request&X-Amz-Date=20250810T000415Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Signature=d6a041d82021f272e48ba56510e8abc389c1ff27a01666a152d7b7363236e5a6"),
        ],
    },
    "adobe_win": {
        "title": "🎨 برامج الأدوبي (ويندوز)",
        "desc": "روابط برامج Adobe للويندوز (سيتم الإضافة لاحقاً).",
        "buttons": [("قريباً", MAIN_CHANNEL_LINK)],
    },
    "ecommerce_courses": {
        "title": "🛒 دورات التجارة الإلكترونية",
        "desc": "حزمة دورات وشروحات تجارة إلكترونية (أكثر من 7 ملفات).",
        "buttons": [
            ("فتح المجلد", "https://drive.google.com/drive/folders/1-UADEMHUswoCyo853FdTu4R4iuUx_f3I?usp=drive_link"),
        ],
    },
    "canva_500": {
        "title": "🖼️ 500 دعوة كانفا برو",
        "desc": "دعوات كانفا برو مدى الحياة.",
        "buttons": [("زيارة الصفحة", "https://digital-plus3.com/products/canva500?srsltid=AfmBOoq01P0ACvybFJkhb2yVBPSUPJadwrOw9LZmNxSUzWPDY8v_42C1")],
    },
    "dark_gpt": {
        "title": "🕶️ Dark GPT",
        "desc": "يضاف لاحقاً.",
        "buttons": [("قريباً", MAIN_CHANNEL_LINK)],
    },
}

# ========= نصوص =========
def tr(k: str) -> str:
    M = {
        "follow_gate": "🔐 يجب الاشتراك بالقناة أولاً.",
        "follow_btn": "📣 الانضمام للقناة",
        "check_btn": "✅ تحقّق",
        "owner_contact": "📨 تواصل مع الإدارة",
        "subscribe_10": "💳 تفعيل بـ 10$",
        "access_denied": "⚠️ لا تملك اشتراكًا مُفعّلاً بعد.",
        "access_ok": "✅ تم تفعيل اشتراكك.",
        "back": "↩️ رجوع",
    }
    return M.get(k, k)

# ========= كاش عضوية القناة =========
_member_cache = {}
async def is_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    now = time.time()
    cached = _member_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]
    try:
        cm = await context.bot.get_chat_member(MAIN_CHANNEL_ID, user_id)
        ok = cm.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER
        )
    except Exception:
        ok = False
    _member_cache[user_id] = (ok, now + 600)
    return ok

# ========= تعديل آمن =========
async def safe_edit(q, text: str | None = None, kb: InlineKeyboardMarkup | None = None):
    try:
        if text is not None:
            await q.edit_message_text(text, reply_markup=kb)
        else:
            await q.edit_message_reply_markup(reply_markup=kb)
    except BadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg or "لم يتم تعديل" in msg:
            if kb is not None and text is not None:
                try:
                    await q.edit_message_reply_markup(reply_markup=kb)
                except BadRequest:
                    pass
        else:
            raise

# ========= لوحات الأزرار =========
def gate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr("follow_btn"), url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(tr("check_btn"), callback_data="verify")]
    ])

def bottom_menu_kb(uid: int) -> InlineKeyboardMarkup:
    # 3 أزرار فقط كما طلبت
    rows = [
        [InlineKeyboardButton("👤 معلوماتي", callback_data="myinfo")],
        [InlineKeyboardButton("⚡ تفعيل البوت", callback_data="subscribe")],
        [InlineKeyboardButton("📨 تواصل مع الإدارة", url=OWNER_DEEP_LINK)],
    ]
    # لا نضيف أي زر إدارة هنا نهائياً
    return InlineKeyboardMarkup(rows)

def sections_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(LINKS["suppliers_pack"]["title"], callback_data="sec_suppliers_pack")],
        [InlineKeyboardButton(LINKS["kash_malik"]["title"], callback_data="sec_kash_malik")],
        [InlineKeyboardButton(LINKS["cyber_sec"]["title"], callback_data="sec_cyber_sec")],
        [InlineKeyboardButton(LINKS["python_zero"]["title"], callback_data="sec_python_zero")],
        [InlineKeyboardButton(LINKS["adobe_win"]["title"], callback_data="sec_adobe_win")],
        [InlineKeyboardButton(LINKS["ecommerce_courses"]["title"], callback_data="sec_ecommerce_courses")],
        [InlineKeyboardButton(LINKS["canva_500"]["title"], callback_data="sec_canva_500")],
        [InlineKeyboardButton(LINKS["dark_gpt"]["title"], callback_data="sec_dark_gpt")],
        [InlineKeyboardButton(tr("back"), callback_data="back_home")],
    ]
    return InlineKeyboardMarkup(rows)

# ========= أوامر / =========
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ما نُظهر أوامر المدير هنا للمستخدمين
    await update.message.reply_text("📜 الأوامر:\n/start – بدء\n/id – رقمك")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))

# ========= /start =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    uid = update.effective_user.id
    user_get(uid)

    # ترحيب بصورة أو نص
    if Path(WELCOME_PHOTO).exists():
        with open(WELCOME_PHOTO, "rb") as f:
            await context.bot.send_photo(update.effective_chat.id, InputFile(f), caption=WELCOME_TEXT_AR)
    else:
        await update.message.reply_text(WELCOME_TEXT_AR)

    # لازم الاشتراك أولاً
    if not await is_member(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())
        return

    # قائمة سفليّة (3 أزرار) + إن كان بريميوم أو أنت → الأقسام
    await update.message.reply_text("👇 القائمة:", reply_markup=bottom_menu_kb(uid))
    if user_is_premium(uid) or uid == OWNER_ID:
        await update.message.reply_text("📂 الأقسام:", reply_markup=sections_kb(uid))

# ========= الأزرار =========
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    # verify يعمل حتى لغير المشتركين
    if q.data == "verify":
        if await is_member(context, uid):
            # بعد التحقق مباشرة: نعرض له السعر + زر محادثتك
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ اشترك الآن / تواصل", url=OWNER_DEEP_LINK)],
                [InlineKeyboardButton(tr("back"), callback_data="back_home")]
            ])
            await safe_edit(q, "👌 تم التحقق من اشتراكك بالقناة.\n\n💳 السعر: 10$ للوصول الكامل.\nبعد الدفع سيتم تفعيل حسابك.", kb)
        else:
            await safe_edit(q, "❗️ ما زلت غير مشترك. انضم ثم اضغط تحقّق.", gate_kb())
        return

    # باقي الأزرار تتطلب اشتراك قناة
    if not await is_member(context, uid):
        await safe_edit(q, "🔐 انضم للقناة لاستخدام البوت:", gate_kb())
        return

    if q.data == "myinfo":
        name = q.from_user.full_name
        uid_txt = str(uid)
        txt = f"👤 اسمك: {name}\n🆔 معرفك: {uid_txt}\n\n— شارك المعرف مع الإدارة للتفعيل."
        await safe_edit(q, txt, bottom_menu_kb(uid))
        return

    if q.data == "subscribe":
        if user_is_premium(uid) or uid == OWNER_ID:
            # مفعل: أظهر الأقسام
            await safe_edit(q, "✅ اشتراكك مفعّل. اختر قسماً:", sections_kb(uid))
        else:
            # غير مفعل: أظهر السعر + رابط محادثتك
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ اشترك الآن / تواصل", url=OWNER_DEEP_LINK)],
                [InlineKeyboardButton(tr("back"), callback_data="back_home")]
            ])
            await safe_edit(q, "💳 السعر: 10$ للوصول الكامل.\nبعد الدفع سيتم تفعيل حسابك.", kb)
        return

    if q.data == "back_home":
        await safe_edit(q, "👇 القائمة:", bottom_menu_kb(uid))
        return

    # فتح الأقسام — فقط للمفعّلين أو أنت
    if q.data.startswith("sec_"):
        if not (user_is_premium(uid) or uid == OWNER_ID):
            await safe_edit(q, tr("access_denied"), bottom_menu_kb(uid))
            return
        key = q.data.replace("sec_", "")
        sec = LINKS.get(key)
        if not sec:
            await safe_edit(q, "قريباً…", sections_kb(uid))
            return

        title, desc = sec["title"], sec["desc"]
        rows = []
        for text, url in sec.get("buttons", []):
            rows.append([InlineKeyboardButton(text, url=url)])
        rows.append([InlineKeyboardButton(tr("back"), callback_data="back_home")])

        local = sec.get("local_file")
        if local and Path(local).exists():
            await safe_edit(q, f"{title}\n\n{desc}")
            with open(local, "rb") as f:
                await q.message.reply_document(InputFile(f), caption=title, reply_markup=InlineKeyboardMarkup(rows))
        else:
            await safe_edit(q, f"{title}\n\n{desc}", InlineKeyboardMarkup(rows))
        return

# ========= أوامر المدير (مخفية عن الجميع) =========
async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("استخدم: /grant <user_id>"); return
    user_grant(context.args[0])
    await update.message.reply_text(f"✅ تم تفعيل {context.args[0]}")

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("استخدم: /revoke <user_id>"); return
    user_revoke(context.args[0])
    await update.message.reply_text(f"❌ تم إلغاء {context.args[0]}")

# أي رسالة نصية من غير مشترك → بوابة الاشتراك
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_member(context, uid):
        await update.message.reply_text("🔐 انضم للقناة لاستخدام البوت:", reply_markup=gate_kb())

# تنظيف Webhook + ضبط أوامر /
async def on_startup(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_my_commands([
        BotCommand("start", "بدء البوت"),
        BotCommand("help", "مساعدة"),
        BotCommand("id", "معرّفك"),
    ])

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
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    app.run_polling()

if __name__ == "__main__":
    main()

