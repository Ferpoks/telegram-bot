import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("OWNER_ID", "0"))

# رقم الواتساب (بدون الصفر الأول)
WHATSAPP_NUMBER = "966578363737"  # 966 مفتاح السعودية

# ===== بيانات قابلة للتعديل =====
# المنتجات والأسعار
PRODUCTS = {
    "شاهد VIP": "20 ريال / شهر",
    "شاهد VIP سنة": "200 ريال",
    "نتفلكس": "35 ريال / شهر",
    "يوتيوب بريميوم": "15 ريال / شهر",
}

# العروض الحالية
OFFERS = [
    {"title": "عرض شاهد سنة + شهرين مجانًا", "details": "السعر: 200 ريال فقط 🎉"},
    {"title": "يوتيوب بريميوم 3 أشهر", "details": "السعر: 40 ريال (بدل 45)"},
]

# الكوبونات
COUPONS = {
    "WELCOME10": "خصم 10% لأول طلب",
    "VIP5": "خصم 5 ريال على شاهد VIP الشهري",
}

# ===== بناء القوائم =====
def main_menu():
    buttons = [
        [InlineKeyboardButton("🛒 شراء", callback_data="buy_menu")],
        [InlineKeyboardButton("🔥 العروض", callback_data="offers")],
        [InlineKeyboardButton("🎟️ الكوبونات", callback_data="coupons")],
        [InlineKeyboardButton("📝 طلب مخصص", callback_data="custom_request")],
        [InlineKeyboardButton("💬 تواصل مع الدعم", callback_data="contact_support")],
        [InlineKeyboardButton("📦 تتبع الطلب", callback_data="track_order")],
        [InlineKeyboardButton("📜 قائمة الأوامر", callback_data="help_all")],
    ]
    return InlineKeyboardMarkup(buttons)

def buy_menu():
    buttons = []
    for name, price in PRODUCTS.items():
        buttons.append([InlineKeyboardButton(f"{name} — {price}", callback_data=f"buy_{name}")])
    buttons.append([InlineKeyboardButton("↩️ رجوع للقائمة", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(buttons)

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رجوع", callback_data="back_to_menu")]])

# ===== أوامر أساسية =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك في متجرنا! اختر من القائمة:", reply_markup=main_menu())

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 القائمة الرئيسية:", reply_markup=main_menu())

# ===== معالجة الأزرار =====
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "لا يوجد"
    full_name = user.full_name

    if data == "back_to_menu":
        await q.edit_message_text("📋 القائمة الرئيسية:", reply_markup=main_menu())

    elif data == "help_all":
        await q.edit_message_text(
            "📜 الأوامر:\n/start - بدء البوت\n/menu - القائمة الرئيسية",
            reply_markup=back_kb()
        )

    elif data == "contact_support":
        msg = f"مرحباً، أحتاج المساعدة.\nاسمي: {full_name}\nيوزري: {username}\nمعرفي: {user_id}"
        wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote_plus(msg)}"
        await q.edit_message_text("💬 تواصل مع الدعم عبر الزر:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📲 واتساب الدعم", url=wa_url)],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_to_menu")]
        ]))

    elif data == "track_order":
        msg = f"مرحباً، أريد تتبع طلبي.\nاسمي: {full_name}\nيوزري: {username}\nمعرفي: {user_id}\nرقم الطلب:"
        wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote_plus(msg)}"
        await q.edit_message_text("📦 أرسل رقم الطلب عبر الواتساب:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📲 واتساب التتبع", url=wa_url)],
            [InlineKeyboardButton("↩️ رجوع", callback_data="back_to_menu")]
        ]))

    elif data == "offers":
        if not OFFERS:
            await q.edit_message_text("لا توجد عروض حالياً.", reply_markup=back_kb())
        else:
            lines = ["🔥 العروض الحالية:"]
            for o in OFFERS:
                lines.append(f"• {o['title']} — {o['details']}")
            await q.edit_message_text("\n".join(lines), reply_markup=back_kb())

    elif data == "coupons":
        if not COUPONS:
            await q.edit_message_text("لا توجد كوبونات حالياً.", reply_markup=back_kb())
        else:
            lines = ["🎟️ كوبونات متاحة:"]
            for code, desc in COUPONS.items():
                lines.append(f"• {code}: {desc}")
            # زر إرسال الكوبون على الواتساب
            msg = f"أريد استخدام كوبون:\nاسمي: {full_name}\nيوزري: {username}\nمعرفي: {user_id}\nالكوبون:"
            wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote_plus(msg)}"
            await q.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📲 إرسال كوبون على واتساب", url=wa_url)],
                    [InlineKeyboardButton("↩️ رجوع", callback_data="back_to_menu")]
                ])
            )

    elif data == "custom_request":
        # طلب مخصص: يفتح واتساب برسالة جاهزة
        msg = f"طلب مخصص:\nاسمي: {full_name}\nيوزري: {username}\nمعرفي: {user_id}\nالتفاصيل:"
        wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote_plus(msg)}"
        await q.edit_message_text(
            "📝 اكتب تفاصيل طلبك المخصص عبر واتساب:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📲 إرسال الطلب عبر واتساب", url=wa_url)],
                [InlineKeyboardButton("↩️ رجوع", callback_data="back_to_menu")]
            ])
        )

    elif data == "buy_menu":
        await q.edit_message_text("🛒 اختر منتجًا للشراء:", reply_markup=buy_menu())

    elif data.startswith("buy_"):
        product_name = data.replace("buy_", "")
        price = PRODUCTS.get(product_name, "غير محدد")
        msg = (
            f"مرحباً، أريد شراء: {product_name}\n"
            f"السعر: {price}\n"
            f"اسمي: {full_name}\nيوزري: {username}\nمعرفي: {user_id}"
        )
        wa_url = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote_plus(msg)}"
        await q.edit_message_text(
            f"🛒 المنتج: {product_name}\n💵 السعر: {price}\n\n📌 اضغط الزر للتواصل على واتساب:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📲 تواصل عبر واتساب", url=wa_url)],
                [InlineKeyboardButton("↩️ رجوع لقائمة الشراء", callback_data="buy_menu")]
            ])
        )

# ===== تشغيل البوت =====
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN غير موجود في ملف .env")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()

if __name__ == "__main__":
    main()
