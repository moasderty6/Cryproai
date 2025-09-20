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

load_dotenv()

# ==== ENV Vars ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
NOWPAY_API_KEY = os.getenv("NOWPAY_API_KEY")
NOWPAY_IPN_SECRET = os.getenv("NOWPAY_IPN_SECRET")
PORT = int(os.getenv("PORT", 8000))
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"

# ✅ التصحيح هنا
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
USERS_FILE = "users.json"

# === دعم تخزين المستخدمين في ملف JSON ===
def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

user_lang = load_users()


# === تنظيف النصوص ===
def clean_response(text, lang="ar"):
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else:
        return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)


# === طلب من Groq ===
async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            result = res.json()
            content = result["choices"][0]["message"]["content"]
            return clean_response(content, lang=lang).strip()
    except Exception as e:
        print("❌ Error from AI:", e)
        return "❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Analysis failed."


# === جلب السعر من CMC ===
async def get_price_cmc(symbol):
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                return None
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except:
        return None


# === لوحات اللغات ===
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])

timeframe_keyboard = InlineKeyboardMarkup(inline_keyboard=[
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


# === /start ===
@dp.message(F.text == "/start")
async def start(m: types.Message):
    uid = str(m.from_user.id)
    if uid not in user_lang:
        user_lang[uid] = {"lang": "ar", "paid": False}
        save_users(user_lang)
    await m.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)


# === /status ===
@dp.message(F.text == "/status")
async def status_handler(m: types.Message):
    lang = user_lang.get(str(m.from_user.id), {}).get("lang", "ar")
    count = len(user_lang)
    msg = f"📊 عدد المستخدمين: {count}" if lang == "ar" else f"📊 Total users: {count}"
    await m.answer(msg)


# === اختيار اللغة ===
@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = str(cb.from_user.id)

    if uid not in user_lang:
        user_lang[uid] = {"lang": lang, "paid": False}
    else:
        user_lang[uid]["lang"] = lang
    save_users(user_lang)

    if not user_lang[uid].get("paid"):  
        # زر الدفع حسب اللغة
        if lang == "ar":
            pay_btn = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع 1$ للاشتراك", callback_data="pay_1usd")]]
            )
            await cb.message.edit_text("⚡ قبل الاستخدام، لازم تدفع 1$ مدى الحياة:", reply_markup=pay_btn)
        else:
            pay_btn = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 Pay $1 for lifetime access", callback_data="pay_1usd")]]
            )
            await cb.message.edit_text("⚡ Before using the bot, you need to pay $1 one-time:", reply_markup=pay_btn)
    else:
        await cb.message.edit_text("✅ حسابك مفعل بالفعل!\nأرسل رمز العملة:" if lang == "ar" else "✅ Your account is already active!\nSend a coin symbol:")


# === زر الدفع NowPayments ===
@dp.callback_query(F.data == "pay_1usd")
async def create_payment(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_lang.get(uid, {}).get("lang", "ar")

    async with httpx.AsyncClient() as client:
        headers = {"x-api-key": NOWPAY_API_KEY, "Content-Type": "application/json"}
        data = {
            "price_amount": 1.0,
            "price_currency": "usd",
            "pay_currency": "usdttrc20",
            "ipn_callback_url": f"{WEBHOOK_URL}/ipn",
            "order_id": uid,
            "order_description": "Lifetime Bot Subscription"
        }
        res = await client.post("https://api.nowpayments.io/v1/payment", headers=headers, json=data)
        result = res.json()

    if "payment_url" in result:
        msg = "💳 ادفع عبر الرابط:" if lang == "ar" else "💳 Pay using this link:"
        await cb.message.answer(f"{msg}\n{result['payment_url']}")
    else:
        await cb.message.answer("❌ خطأ بإنشاء الدفع." if lang == "ar" else "❌ Failed to create payment.")


# === استقبال IPN من NowPayments ===
async def ipn_handler(req):
    try:
        body = await req.json()
    except Exception as e:
        print(f"❌ Failed to decode IPN JSON: {e}")
        return web.Response(status=400, text="Invalid JSON")

    print("🔔 IPN Received:", body)
    if body.get("payment_status") == "finished":
        uid = str(body.get("order_id"))
        if uid in user_lang:
            user_lang[uid]["paid"] = True
            save_users(user_lang)
            await bot.send_message(int(uid), "✅ تم تفعيل اشتراكك مدى الحياة! 🎉" if user_lang[uid]["lang"] == "ar" else "✅ Your lifetime subscription is now active! 🎉")
    return web.Response(status=200)


# === التعامل مع رمز العملة ===
@dp.message(F.text)
async def handle_symbol(m: types.Message):
    uid = str(m.from_user.id)
    lang = user_lang.get(uid, {}).get("lang", "ar")

    # تحقق من الدفع
    if not user_lang.get(uid, {}).get("paid"):
        msg = "❌ لازم تدفع 1$ أولاً لاستخدام البوت." if lang == "ar" else "❌ You need to pay $1 first to use the bot."
        await m.answer(msg)
        return

    text = m.text.strip().lower()
    if text in ["/start", "start", "start=1"]:
        await start(m)
        return

    sym = text
    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")

    price = await get_price_cmc(sym)
    if not price:
        await m.answer("❌ لم أتمكن من جلب السعر الحالي للعملة." if lang == "ar"
                       else "❌ Couldn't fetch current price.")
        return

    await m.answer(f"💵 السعر الحالي: ${price:.6f}" if lang == "ar" else f"💵 Current price: ${price:.6f}")

    user_lang[uid+"_symbol"] = sym
    user_lang[uid+"_price"] = price
    save_users(user_lang)

    kb = timeframe_keyboard if lang == "ar" else timeframe_keyboard_en
    await m.answer("⏳ اختر الإطار الزمني للتحليل:" if lang == "ar" else "⏳ Select timeframe for analysis:", reply_markup=kb)


# === التعامل مع اختيار الإطار الزمني ===
@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_lang.get(uid, {}).get("lang", "ar")
    tf_map = {"tf_weekly": "weekly", "tf_daily": "daily", "tf_4h": "4h"}
    timeframe = tf_map[cb.data]
    sym = user_lang.get(uid+"_symbol")
    price = user_lang.get(uid+"_price")

    prompt = ""
    if lang == "ar":
        prompt = (
            f"سعر العملة {sym.upper()} الآن هو {price:.6f}$.\n"
            f"قم بتحليل التشارت للإطار الزمني {timeframe} باستخدام مؤشرات شاملة:\n"
            "- خطوط الدعم والمقاومة\n"
            "- RSI, MACD, MA\n"
            "- Bollinger Bands\n"
            "- Fibonacci Levels\n"
            "- Stochastic Oscillator\n"
            "- Volume Analysis\n"
            "- Trendlines باستخدام Regression\n"
            "ثم قدم:\n"
            "1. تقييم عام (صعود أم هبوط؟)\n"
            "2. أقرب مقاومة ودعم\n"
            "3. نطاق سعري مستهدف (Range)\n"
            "✅ استخدم العربية فقط\n"
            "❌ لا تشرح المشروع، فقط تحليل التشارت"
        )
    else:
        prompt = (
            f"The current price of {sym.upper()} is ${price:.6f}.\n"
            f"Analyze the {timeframe} chart using comprehensive indicators:\n"
            "- Support and Resistance\n"
            "- RSI, MACD, MA\n"
            "- Bollinger Bands\n"
            "- Fibonacci Levels\n"
            "- Stochastic Oscillator\n"
            "- Volume Analysis\n"
            "- Trendlines using Regression\n"
            "Then provide:\n"
            "1. General trend (up/down)\n"
            "2. Nearest resistance/support\n"
            "3. Target price range\n"
            "✅ Answer in English only\n"
            "❌ Don't explain the project, only chart analysis"
        )

    await cb.message.edit_text("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)


# === Webhook مع حماية JSON ===
async def handle_webhook(req):
    if req.method == "GET":
        return web.Response(text="✅ Bot is alive.")

    try:
        update = await req.json()
    except Exception as e:
        print(f"❌ Failed to decode JSON from webhook: {e}")
        body = await req.text()
        print(f"Raw body: {body}")
        return web.Response(status=400, text="Invalid JSON")

    try:
        await dp.feed_update(bot=bot, update=types.Update(**update))
    except Exception as e:
        print(f"❌ Failed to process update: {e}")
        return web.Response(status=500, text="Failed to process update")

    return web.Response()


# === Startup / Shutdown ===
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to {WEBHOOK_URL}")


async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


# === Main ===
async def main():
    app = web.Application()
    app.router.add_post("/", handle_webhook)
    app.router.add_get("/", handle_webhook)
    app.router.add_post("/ipn", ipn_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("✅ Bot is running...")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
