import os
import sqlite3
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import openai

# تحميل متغيرات البيئة
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# قاعدة البيانات
conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    is_vip INTEGER DEFAULT 0
)
""")
conn.commit()

ADMIN_ID = 6468743821  # ID حسابك الإداري

# رسالة الترحيب
WELCOME_MSG = "🎉 أهلاً بك في البوت! اشترك بالقناة أولاً ثم فعّل الاشتراك للاستفادة من جميع المزايا."

# لوحة الأزرار الرئيسية
def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton("📢 الاشتراك بالقناة", url="https://t.me/+oIYmTi_gWuxiNmZk")],
    ]
    cursor.execute("SELECT is_vip FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        buttons.append([InlineKeyboardButton("🚀 الأقسام", callback_data="sections")])
    else:
        buttons.append([InlineKeyboardButton("💳 تفعيل الاشتراك (10$)", url="https://t.me/Ferp0ks")])
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("⚙️ أوامر الإدارة", callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "بدون معرف"
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    await update.message.reply_text(WELCOME_MSG, reply_markup=main_menu(user_id))

# عرض الأقسام
async def sections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("📚 الأمن السيبراني", url="https://www.mediafire.com/folder/r26pp5mpduvnx")],
        [InlineKeyboardButton("🐍 دورة بايثون", url="https://kyc-digital-files.s3.eu-central-1.amazonaws.com/digitals/xWNop/Y8WctvBLiA6u6AASeZX2IUfDQAolTJ4QFGx9WRCu.pdf")],
        [InlineKeyboardButton("🤖 الذكاء الاصطناعي", callback_data="ai_menu")]
    ]
    await update.callback_query.message.reply_text("📂 الأقسام المتاحة:", reply_markup=InlineKeyboardMarkup(buttons))

# قائمة الذكاء الاصطناعي
async def ai_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("💬 محادثة AI", callback_data="chat_ai")],
        [InlineKeyboardButton("🖼️ توليد صور AI", callback_data="image_ai")]
    ]
    await update.callback_query.message.reply_text("🤖 اختر خدمة AI:", reply_markup=InlineKeyboardMarkup(buttons))

# دردشة AI
async def chat_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("💬 أرسل رسالتك وسأرد عليك باستخدام الذكاء الاصطناعي.")
    context.user_data["mode"] = "chat"

# صورة AI
async def image_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("🖼️ أرسل وصف الصورة التي تريد إنشاءها.")
    context.user_data["mode"] = "image"

# استقبال الرسائل للذكاء الاصطناعي
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    if mode == "chat":
        prompt = update.message.text
        response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}])
        await update.message.reply_text(response.choices[0].message.content)
    elif mode == "image":
        prompt = update.message.text
        img = openai.Image.create(prompt=prompt, n=1, size="512x512")
        await update.message.reply_photo(img['data'][0]['url'])

# أوامر الإدارة
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    buttons = [
        [InlineKeyboardButton("✅ منح صلاحية VIP", callback_data="grant_vip")],
        [InlineKeyboardButton("❌ سحب صلاحية VIP", callback_data="revoke_vip")]
    ]
    await update.callback_query.message.reply_text("⚙️ لوحة الإدارة:", reply_markup=InlineKeyboardMarkup(buttons))

# منح VIP
async def grant_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("أرسل ID المستخدم لمنحه VIP.")
    context.user_data["mode"] = "grant_vip"

# سحب VIP
async def revoke_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("أرسل ID المستخدم لسحب VIP منه.")
    context.user_data["mode"] = "revoke_vip"

# معالجة ID
async def handle_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    if mode == "grant_vip":
        cursor.execute("UPDATE users SET is_vip=1 WHERE user_id=?", (int(update.message.text),))
        conn.commit()
        await update.message.reply_text("✅ تم منح VIP.")
    elif mode == "revoke_vip":
        cursor.execute("UPDATE users SET is_vip=0 WHERE user_id=?", (int(update.message.text),))
        conn.commit()
        await update.message.reply_text("❌ تم سحب VIP.")

# تشغيل البوت
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(sections, pattern="sections"))
app.add_handler(CallbackQueryHandler(ai_menu, pattern="ai_menu"))
app.add_handler(CallbackQueryHandler(chat_ai, pattern="chat_ai"))
app.add_handler(CallbackQueryHandler(image_ai, pattern="image_ai"))
app.add_handler(CallbackQueryHandler(admin_menu, pattern="admin_menu"))
app.add_handler(CallbackQueryHandler(grant_vip, pattern="grant_vip"))
app.add_handler(CallbackQueryHandler(revoke_vip, pattern="revoke_vip"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.Regex(r"^\d+$"), handle_admin_id))

if __name__ == "__main__":
    print("✅ Bot is running...")
    app.run_polling()
