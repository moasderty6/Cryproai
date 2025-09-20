import asyncio
import os
import re
import json
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
import httpx
from dotenv import load_dotenv

# --- تحميل متغيرات البيئة ---
load_dotenv()

# ==== متغيرات البيئة ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
NOWPAY_API_KEY = os.getenv("NOWPAY_API_KEY")
NOWPAY_IPN_SECRET = os.getenv("NOWPAY_IPN_SECRET") # مهم للتحقق من صحة الطلبات مستقبلاً
PORT = int(os.getenv("PORT", 8000))
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"

# --- إعدادات البوت والـ Dispatcher ---
# ✅ تم استخدام الطريقة الجديدة لتعريف parse_mode
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
USERS_FILE = "users.json"

# === دعم تخزين المستخدمين في ملف JSON ===
def load_users():
    """تحميل بيانات المستخدمين من ملف JSON."""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_users(users):
    """حفظ بيانات المستخدمين في ملف JSON."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

user_data = load_users()


# === تنظيف النصوص ===
def clean_response(text, lang="ar"):
    """إزالة الأحرف غير المرغوب فيها من استجابة الـ AI."""
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else:
        return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)


# === طلب من Groq AI ===
async def ask_groq(prompt, lang="ar"):
    """إرسال طلب إلى Groq API للحصول على تحليل."""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            res.raise_for_status()
            result = res.json()
            content = result["choices"][0]["message"]["content"]
            return clean_response(content, lang=lang).strip()
    except Exception as e:
        print(f"❌ Error from Groq AI: {e}")
        return "❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Analysis failed."


# === جلب السعر من CoinMarketCap ===
async def get_price_cmc(symbol):
    """جلب سعر العملة من CoinMarketCap API."""
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                return None
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except Exception as e:
        print(f"❌ Error from CMC: {e}")
        return None

# --- لوحات المفاتيح (Keyboards) ---
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])

timeframe_keyboard_ar = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="أسبوعي", callback_data="tf_weekly"),
        InlineKeyboardButton(text="يومي", callback_data="tf_daily"),
        InlineKeyboardButton(text="4 ساعات", callback_data="tf_4h")
    ]
])

timeframe_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Weekly", callback_data="tf_weekly"),
        InlineKeyboardButton(text="Daily", callback_data="tf_daily"),
        InlineKeyboardButton(text="4H", callback_data="tf_4h")
    ]
])


# === معالج أمر /start ===
@dp.message(F.text == "/start")
async def start(m: types.Message):
    uid = str(m.from_user.id)
    if uid not in user_data:
        user_data[uid] = {"lang": "ar", "paid": False}
        save_users(user_data)
    await m.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)


# === معالج أمر /status ===
@dp.message(F.text == "/status")
async def status_handler(m: types.Message):
    lang = user_data.get(str(m.from_user.id), {}).get("lang", "ar")
    count = len(user_data)
    msg = f"📊 عدد المستخدمين: {count}" if lang == "ar" else f"📊 Total users: {count}"
    await m.answer(msg)


# === معالج اختيار اللغة ===
@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = str(cb.from_user.id)

    if uid not in user_data:
        user_data[uid] = {"lang": lang, "paid": False}
    else:
        user_data[uid]["lang"] = lang
    save_users(user_data)

    # تحقق إذا كان المستخدم قد دفع
    if not user_data[uid].get("paid", False):
        if lang == "ar":
            pay_btn = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع 1$ للاشتراك", callback_data="pay_1usd")]]
            )
            await cb.message.edit_text("⚡ لاستخدام البوت، يجب عليك دفع 1$ لمرة واحدة للاشتراك مدى الحياة:", reply_markup=pay_btn)
        else:
            pay_btn = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 Pay $1 for lifetime access", callback_data="pay_1usd")]]
            )
            await cb.message.edit_text("⚡ To use the bot, you need to pay a one-time fee of $1 for lifetime access:", reply_markup=pay_btn)
    else:
        msg = "✅ حسابك مفعل بالفعل!\nأرسل رمز العملة للتحليل:" if lang == "ar" else "✅ Your account is already active!\nSend a coin symbol for analysis:"
        await cb.message.edit_text(msg)
    await cb.answer()


# === معالج زر الدفع (NowPayments) ===
@dp.callback_query(F.data == "pay_1usd")
async def create_payment(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_data.get(uid, {}).get("lang", "ar")

    await cb.message.edit_text("⏳ جاري إنشاء رابط الدفع..." if lang == "ar" else "⏳ Creating payment link...")

    async with httpx.AsyncClient() as client:
        headers = {"x-api-key": NOWPAY_API_KEY, "Content-Type": "application/json"}
        payload = {
            "price_amount": 1.0,
            "price_currency": "usd",
            "pay_currency": "usdtmatic", # يمكنك تغييرها لعملة أخرى
            "ipn_callback_url": f"{WEBHOOK_URL}/ipn",
            "order_id": uid,
            "order_description": f"Lifetime Bot Subscription for user {uid}"
        }
        try:
            res = await client.post("https://api.nowpayments.io/v1/payment", headers=headers, json=payload)
            res.raise_for_status() # سيطلق استثناء إذا كانت الاستجابة خطأ (مثل 4xx أو 5xx)
            result = res.json()

            if "payment_url" in result:
                msg = "💳 للدفع، استخدم الرابط التالي. سيتم تفعيل حسابك تلقائياً بعد الدفع:" if lang == "ar" else "💳 To pay, use the following link. Your account will be activated automatically after payment:"
                payment_link = result['payment_url']
                # لا نستخدم InlineKeyboard للرابط هنا لتجنب المشاكل
                await cb.message.answer(f"{msg}\n\n👉 {payment_link}")
            else:
                print("❌ NowPayments Error:", result)
                await cb.message.answer("❌ حدث خطأ أثناء إنشاء رابط الدفع. يرجى المحاولة مرة أخرى لاحقًا." if lang == "ar" else "❌ Failed to create payment link. Please try again later.")

        except httpx.HTTPStatusError as e:
            print(f"❌ HTTP Error creating payment: {e.response.text}")
            await cb.message.answer("❌ خطأ فني عند الاتصال بخدمة الدفع." if lang == "ar" else "❌ Technical error when contacting the payment service.")
        except Exception as e:
            print(f"❌ General Error creating payment: {e}")
            await cb.message.answer("❌ خطأ غير متوقع. حاول مرة أخرى." if lang == "ar" else "❌ An unexpected error occurred. Please try again.")
    await cb.answer()


# === معالج النصوص (لرموز العملات) ===
@dp.message(F.text)
async def handle_symbol(m: types.Message):
    uid = str(m.from_user.id)

    # إذا لم يكن المستخدم موجوداً، ابدأ من جديد
    if uid not in user_data:
        await start(m)
        return

    lang = user_data[uid].get("lang", "ar")

    # التحقق من الدفع
    if not user_data[uid].get("paid", False):
        msg = "❌ يجب عليك الاشتراك أولاً لاستخدام هذه الميزة. اضغط /start للاشتراك." if lang == "ar" else "❌ You must subscribe first to use this feature. Press /start to subscribe."
        await m.answer(msg)
        return

    # تجاهل الأوامر الأخرى والتركيز على رموز العملات
    text = m.text.strip()
    if text.startswith("/"):
        return

    sym = text.upper()
    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")

    price = await get_price_cmc(sym)
    if not price:
        await m.answer("❌ لم أتمكن من العثور على العملة أو جلب السعر. تأكد من الرمز." if lang == "ar"
                       else "❌ Couldn't find the coin or fetch the price. Check the symbol.")
        return

    await m.answer(f"💵 السعر الحالي لـ {sym}: ${price:,.6f}".replace(",", "_").replace("_", ",")) # تنسيق أفضل للسعر

    # حفظ البيانات المؤقتة للتحليل
    user_data[uid]["current_symbol"] = sym
    user_data[uid]["current_price"] = price
    save_users(user_data)

    kb = timeframe_keyboard_ar if lang == "ar" else timeframe_keyboard_en
    await m.answer("⏳ اختر الإطار الزمني للتحليل:" if lang == "ar" else "⏳ Select timeframe for analysis:", reply_markup=kb)


# === معالج اختيار الإطار الزمني ===
@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    if uid not in user_data or "current_symbol" not in user_data[uid]:
        await cb.message.edit_text("يرجى إرسال رمز العملة أولاً." if user_data.get(uid, {}).get("lang") == "ar" else "Please send a coin symbol first.")
        await cb.answer()
        return

    lang = user_data[uid].get("lang", "ar")
    tf_map = {"tf_weekly": "weekly", "tf_daily": "daily", "tf_4h": "4 hours"}
    timeframe_text = tf_map[cb.data]
    sym = user_data[uid]["current_symbol"]
    price = user_data[uid]["current_price"]

    prompt_ar = (
        f"أنت محلل فني خبير في سوق العملات الرقمية. سعر عملة {sym} الآن هو ${price:,.6f}.\n"
        f"قم بتحليل فني مفصل للشارت على الإطار الزمني ({timeframe_text}) باستخدام المؤشرات التالية:\n"
        "- مناطق الدعم والمقاومة الرئيسية.\n"
        "- مؤشرات الزخم (RSI, MACD).\n"
        "- المتوسطات المتحركة (MA).\n"
        "- مؤشر بولينجر باندز (Bollinger Bands).\n"
        "- مستويات فيبوناتشي (Fibonacci Retracement).\n"
        "- تحليل أحجام التداول (Volume Analysis).\n\n"
        "بناءً على التحليل، قدم تقريراً واضحاً وموجزاً يتضمن:\n"
        "1. **الاتجاه العام المتوقع:** (صعودي / هبوطي / عرضي).\n"
        "2. **أقرب منطقة مقاومة وأقرب منطقة دعم:**\n"
        "3. **نطاق سعري مستهدف:** (السعر المتوقع الوصول إليه).\n"
        "**ملاحظة هامة:** يجب أن يكون التحليل باللغة العربية فقط. لا تشرح ما هو مشروع العملة، ركز فقط على التحليل الفني للشارت."
    )
    prompt_en = (
        f"You are an expert technical analyst in the cryptocurrency market. The current price of {sym} is ${price:,.6f}.\n"
        f"Perform a detailed technical analysis of the chart on the ({timeframe_text}) timeframe using the following indicators:\n"
        "- Key support and resistance zones.\n"
        "- Momentum indicators (RSI, MACD).\n"
        "- Moving Averages (MA).\n"
        "- Bollinger Bands.\n"
        "- Fibonacci Retracement levels.\n"
        "- Volume Analysis.\n\n"
        "Based on the analysis, provide a clear and concise report including:\n"
        "1. **General Trend Outlook:** (Bullish / Bearish / Sideways).\n"
        "2. **Nearest Resistance and Support Zones:**\n"
        "3. **Target Price Range:** (The expected price target).\n"
        "**Important Note:** The analysis must be in English only. Do not explain the coin's project; focus strictly on technical chart analysis."
    )
    prompt = prompt_ar if lang == "ar" else prompt_en

    await cb.message.edit_text("🤖 جاري التحليل، قد يستغرق هذا بعض الوقت..." if lang == "ar" else "🤖 Analyzing, this may take a moment...")
    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)
    await cb.answer()


# --- إعدادات Webhook و aiohttp ---

# استقبال IPN من NowPayments
async def ipn_handler(req):
    try:
        body = await req.json()
        print("🔔 IPN Received from NowPayments:", body)
        # يمكنك إضافة تحقق من IPN Secret Key هنا لزيادة الأمان
        if body.get("payment_status") == "finished":
            uid = str(body.get("order_id"))
            if uid in user_data:
                user_data[uid]["paid"] = True
                save_users(user_data)
                lang = user_data[uid].get("lang", "ar")
                msg = "✅ تم تفعيل اشتراكك مدى الحياة بنجاح! 🎉\nيمكنك الآن إرسال رمز أي عملة لبدء التحليل." if lang == "ar" else "✅ Your lifetime subscription is now active! 🎉\nYou can now send any coin symbol to start the analysis."
                await bot.send_message(int(uid), msg)
        return web.Response(status=200)
    except Exception as e:
        print(f"❌ Error in IPN handler: {e}")
        return web.Response(status=500)

# معالج الـ Webhook الرئيسي للبوت
async def handle_webhook(req):
    url = str(req.url)
    if url.endswith("/ipn"):
        return await ipn_handler(req)
    
    if req.method == "GET":
        return web.Response(text="✅ Bot is alive and kicking!")
    
    try:
        update_data = await req.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot=bot, update=update)
        return web.Response(status=200)
    except Exception as e:
        print(f"❌ Error in main webhook handler: {e}")
        return web.Response(status=500)


async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}")
    print(f"✅ Webhook has been set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    print("🗑️ Webhook has been deleted.")


def main():
    app = web.Application()
    # تم دمج المسارات في معالج واحد لتجنب التعارض
    app.router.add_route('*', '/', handle_webhook) # يستقبل كل الطلبات على المسار الرئيسي
    app.router.add_route('*', '/ipn', ipn_handler) # مسار خاص ومستقل للـ IPN
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    print("🚀 Starting web server...")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
