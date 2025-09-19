import asyncio
import os
import re
import json
import hmac
import hashlib
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import httpx
from dotenv import load_dotenv

# --- تحميل الإعدادات ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
## <<< أضفنا مفاتيح NOWPayments
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")

# --- إعداد البوت ---
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
USERS_FILE = "users.json"
## <<< ملف جديد لتخزين المشتركين
PAID_USERS_FILE = "paid_users.json"

# --- إدارة بيانات المستخدمين والاشتراكات ---
def load_data(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if filename == USERS_FILE else []

def save_data(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

user_lang = load_data(USERS_FILE)
paid_users = set(load_data(PAID_USERS_FILE)) # نستخدم set للبحث السريع

def is_user_paid(user_id: int):
    return user_id in paid_users

# --- دوال مساعدة (بدون تغيير) ---
def clean_response(text, lang="ar"):
    # ... الكود كما هو
    if lang == "ar": return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else: return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)

async def ask_groq(prompt, lang="ar"):
    # ... الكود كما هو
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            result = res.json(); content = result["choices"][0]["message"]["content"]
            return clean_response(content, lang=lang).strip()
    except Exception as e:
        print("❌ Error from AI:", e)
        return "❌ حدث خطأ أثناء تحليل التشارت." if lang == "ar" else "❌ Analysis failed."

async def get_price_cmc(symbol):
    # ... الكود كما هو
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200: return None
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except: return None

## <<< دالة جديدة لإنشاء فاتورة دفع كريبتو
async def create_nowpayments_invoice(user_id: int):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    data = {
        "price_amount": 3,  # السعر 3 دولار
        "price_currency": "usd",
        "order_id": str(user_id),
        "ipn_callback_url": f"{WEBHOOK_URL}/webhook/nowpayments",
        "success_url": f"https://t.me/{(await bot.get_me()).username}",
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json=data)
            if res.status_code == 201:
                return res.json().get("invoice_url")
    except Exception as e:
        print(f"❌ Error creating NOWPayments invoice: {e}")
    return None

# --- لوحات الأزرار ---
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])
## <<< زر الدفع الجديد
payment_keyboard_ar = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💎 اشترك الآن (3$ مدى الحياة)", callback_data="pay_with_crypto")]
])
payment_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💎 Subscribe Now ($3 Lifetime)", callback_data="pay_with_crypto")]
])
# (باقي الأزرار كما هي)
timeframe_keyboard = ...
timeframe_keyboard_en = ...

# === أوامر البوت ===
@dp.message(F.text.in_({'/start', 'start'}))
async def start(m: types.Message):
    uid = m.from_user.id
    lang = user_lang.get(str(uid), "ar")

    if is_user_paid(uid):
        await m.answer(
            "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل." if lang == "ar"
            else "✅ Welcome back! Your subscription is active.\nSend a coin symbol to analyze."
        )
    else:
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        await m.answer(
            "أهلاً بك في بوت تحليل العملات!\nللوصول الكامل، يرجى الاشتراك مقابل 3$ لمرة واحدة." if lang == "ar"
            else "Welcome to the Crypto Analysis Bot!\nFor full access, please subscribe for a one-time fee of $3.",
            reply_markup=kb
        )
        await m.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = cb.from_user.id
    user_lang[str(uid)] = lang
    save_data(USERS_FILE, user_lang)

    if is_user_paid(uid):
        await cb.message.edit_text("✅ أرسل رمز العملة:" if lang == "ar" else "✅ Send coin symbol:")
    else:
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        await cb.message.edit_text(
            "للمتابعة، يرجى الاشتراك." if lang == "ar" else "To continue, please subscribe.",
            reply_markup=kb
        )

## <<< معالج زر الدفع
@dp.callback_query(F.data == "pay_with_crypto")
async def process_crypto_payment(cb: types.CallbackQuery):
    lang = user_lang.get(str(cb.from_user.id), "ar")
    await cb.message.edit_text("⏳ يتم إنشاء رابط الدفع، يرجى الانتظار..." if lang == "ar" else "⏳ Generating payment link, please wait...")
    
    invoice_url = await create_nowpayments_invoice(cb.from_user.id)
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 افتح صفحة الدفع", url=invoice_url)]])
        await cb.message.edit_text(
            "تم إنشاء رابط الدفع بنجاح. لإتمام الاشتراك، ادفع عبر الرابط أدناه.\n💡 نصيحة: استخدم شبكة TRC-20 لرسوم منخفضة." if lang == "ar"
            else "Payment link created. To complete your subscription, pay via the link below.\n💡 Tip: Use the TRC-20 network for low fees.",
            reply_markup=kb
        )
    else:
        await cb.message.edit_text("❌ حدث خطأ. يرجى المحاولة مرة أخرى لاحقاً." if lang == "ar" else "❌ An error occurred. Please try again later.")
    await cb.answer()

# === التعامل مع رمز العملة ===
@dp.message(F.text)
async def handle_symbol(m: types.Message):
    ## <<< حماية الميزة بالتحقق من الاشتراك
    if not is_user_paid(m.from_user.id):
        lang = user_lang.get(str(m.from_user.id), "ar")
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        await m.answer(
            "⚠️ هذه الميزة للمشتركين فقط. يرجى الاشتراك أولاً." if lang == "ar"
            else "⚠️ This feature is for subscribers only. Please subscribe first.",
            reply_markup=kb
        )
        return

    # ... باقي الكود كما هو
    uid = str(m.from_user.id)
    lang = user_lang.get(uid, "ar")
    sym = m.text.strip().lower()
    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")
    price = await get_price_cmc(sym)
    # ... الخ

# === التعامل مع اختيار الإطار الزمني ===
@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    ## <<< حماية الميزة بالتحقق من الاشتراك
    if not is_user_paid(cb.from_user.id):
        await cb.answer("⚠️ هذه الميزة للمشتركين فقط.", show_alert=True)
        return

    # ... باقي الكود كما هو

# === Webhook ===
async def handle_telegram_webhook(req):
    # ... الكود كما هو
    update = await req.json()
    await dp.feed_update(bot=bot, update=types.Update(**update))
    return web.Response()

## <<< معالج إشعارات الدفع من NOWPayments
async def handle_nowpayments_webhook(req: web.Request):
    # التحقق من توقيع الطلب للأمان
    try:
        signature = req.headers.get("x-nowpayments-sig")
        body = await req.read()
        
        if not signature or not NOWPAYMENTS_IPN_SECRET:
            return web.Response(status=400, text="Missing signature or secret")

        # حساب التوقيع المتوقع
        h = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), body, hashlib.sha512)
        expected_signature = h.hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            print("❌ Invalid NOWPayments signature")
            return web.Response(status=401, text="Invalid signature")
        
        # التوقيع صحيح، قم بمعالجة البيانات
        data = json.loads(body)
        if data.get("payment_status") == "finished":
            user_id = int(data.get("order_id"))
            paid_users.add(user_id)
            save_data(PAID_USERS_FILE, list(paid_users))
            
            lang = user_lang.get(str(user_id), "ar")
            await bot.send_message(
                user_id,
                "✅ تم تأكيد الدفع بنجاح! شكراً لاشتراكك. يمكنك الآن استخدام البوت بشكل كامل." if lang == "ar"
                else "✅ Payment confirmed! Thank you for subscribing. You can now use the bot fully."
            )

        return web.Response(status=200, text="OK")

    except Exception as e:
        print(f"❌ Error in NOWPayments webhook: {e}")
        return web.Response(status=500, text="Internal Server Error")


async def main():
    app = web.Application()
    # مسار تيليجرام
    app.router.add_post("/", handle_telegram_webhook)
    ## <<< مسار جديد لاستقبال إشعارات الدفع
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
    
    # ... باقي الكود كما هو

if __name__ == "__main__":
    asyncio.run(main())
