import asyncio
import os
import re
import json
import hmac
import hashlib
import asyncpg
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import httpx
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command

# --- تحميل الإعدادات ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_USER_ID = 6172153716

# --- إعداد البوت ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- ذاكرة المستخدمين ---
paid_users = set()
trial_users = set()
USERS_FILE = "users.json"

def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

user_lang = load_users()

def is_user_paid(user_id: int):
    return user_id in paid_users

def has_trial(uid: str):
    return uid not in trial_users

# --- دوال مساعدة ---
def clean_response(text, lang="ar"):
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else:
        return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)

async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data
            )
            result = res.json()
            content = result["choices"][0]["message"]["content"]
            return clean_response(content, lang=lang).strip()
    except Exception as e:
        print(f"❌ Error from AI: {e}")
        return "❌ حدث خطأ أثناء تحليل التشارت." if lang == "ar" else "❌ Analysis failed."

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

async def create_nowpayments_invoice(user_id: int):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {
        "x-api-key": NOWPAYMENTS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "price_amount": 10,
        "price_currency": "usd",
        "order_id": str(user_id),
        "ipn_callback_url": f"{WEBHOOK_URL}/webhook/nowpayments",
        "success_url": f"https://t.me/{(await bot.get_me()).username}",
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json=data)
            if 200 <= res.status_code < 300:
                return res.json().get("invoice_url")
            else:
                print(f"NOWPayments Error: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ CRITICAL ERROR in create_nowpayments_invoice: {e}")
    return None

# --- لوحات الأزرار ---
language_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
    ]
)

payment_keyboard_ar = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_with_crypto")]
    ]
)

payment_keyboard_en = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Lifetime)", callback_data="pay_with_crypto")]
    ]
)

timeframe_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="أسبوعي", callback_data="tf_weekly"),
            InlineKeyboardButton(text="يومي", callback_data="tf_daily"),
            InlineKeyboardButton(text="4 ساعات", callback_data="tf_4h")
        ]
    ]
)

timeframe_keyboard_en = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Weekly", callback_data="tf_weekly"),
            InlineKeyboardButton(text="Daily", callback_data="tf_daily"),
            InlineKeyboardButton(text="4H", callback_data="tf_4h")
        ]
    ]
)

# --- أوامر البوت ---
@dp.message(F.text.in_({'/start', 'start'}))
async def start(m: types.Message):
    await m.answer(
        "👋 أهلاً بك، يرجى اختيار لغتك للمتابعة:\nWelcome, please choose your language to continue:",
        reply_markup=language_keyboard
    )

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = str(cb.from_user.id)
    user_lang[uid] = lang
    save_users(user_lang)

    if is_user_paid(cb.from_user.id):
        await cb.message.edit_text(
            "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل."
            if lang == "ar"
            else "✅ Welcome back! Your subscription is active.\nSend a coin symbol to analyze."
        )
    else:
        if has_trial(uid):
            await cb.message.edit_text(
                "🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة للتحليل."
                if lang == "ar"
                else "🎁 You have one free trial! Send a coin symbol for analysis."
            )
        else:
            kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
            await cb.message.edit_text(
                "للوصول الكامل، يرجى الاشتراك مقابل 10 USDT لمرة واحدة."
                if lang == "ar"
                else "For full access, please subscribe for a one-time fee of 10 USDT.",
                reply_markup=kb
            )

@dp.callback_query(F.data == "pay_with_crypto")
async def process_crypto_payment(cb: types.CallbackQuery):
    lang = user_lang.get(str(cb.from_user.id), "ar")
    await cb.message.edit_text(
        "⏳ يتم إنشاء رابط الدفع، يرجى الانتظار..."
        if lang == "ar"
        else "⏳ Generating payment link, please wait..."
    )

    invoice_url = await create_nowpayments_invoice(cb.from_user.id)
    if invoice_url:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع الآن" if lang=="ar" else "💳 Pay Now", url=invoice_url)]]
        )
        msg = (
            "✅ تم إنشاء رابط الدفع.\nلإتمام الاشتراك، ادفع عبر الرابط أدناه.\n\nUSDT (BEP20)"
            if lang == "ar"
            else "✅ Payment link created.\nTo complete your subscription, pay via the link below.\n\nUSDT (BEP20)"
        )
        await cb.message.edit_text(msg, reply_markup=kb)
    else:
        await cb.message.edit_text(
            "❌ حدث خطأ. يرجى المحاولة مرة أخرى لاحقاً."
            if lang == "ar"
            else "❌ An error occurred. Please try again later."
        )
    await cb.answer()

@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    await m.answer(f"ℹ️ عدد المستخدمين الذين ضغطوا /start: {len(user_lang)}")

@dp.message(Command("admin"))
async def admin_cmd(m: types.Message):
    await m.answer(
        "📌 للتواصل مع الدعم، يرجى التواصل مع هذا الحساب:\n@AiCrAdmin\n\n"
        "📌 For support, contact:\n@AiCrAdmin"
    )

@dp.message(F.text)
async def handle_symbol(m: types.Message):
    if m.text.startswith('/'):
        return

    uid = str(m.from_user.id)
    lang = user_lang.get(uid, "ar")

    if not is_user_paid(m.from_user.id):
        if not has_trial(uid):
            kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
            await m.answer(
                "⚠️ انتهت تجربتك المجانية. يرجى الاشتراك أولاً."
                if lang == "ar"
                else "⚠️ This feature is for subscribers only. Please subscribe first.",
                reply_markup=kb
            )
            return
        else:
            trial_users.add(uid)

    sym = m.text.strip().lower()
    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")
    price = await get_price_cmc(sym)

    if not price:
        await m.answer(
            "❌ لم أتمكن من جلب السعر الحالي للعملة."
            if lang == "ar"
            else "❌ Couldn't fetch current price."
        )
        return

    await m.answer(
        f"💵 السعر الحالي: ${price:.6f}"
        if lang == "ar"
        else f"💵 Current price: ${price:.6f}"
    )

    user_lang[uid + "_symbol"] = sym
    user_lang[uid + "_price"] = price
    save_users(user_lang)

    kb = timeframe_keyboard if lang == "ar" else timeframe_keyboard_en
    await m.answer(
        "⏳ اختر الإطار الزمني للتحليل:"
        if lang == "ar"
        else "⏳ Select timeframe for analysis:",
        reply_markup=kb
    )

@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_lang.get(uid, "ar")

    if not is_user_paid(cb.from_user.id) and not has_trial(uid):
        await cb.answer("⚠️ هذه الميزة للمشتركين فقط.", show_alert=True)
        return

    tf_map = {
        "tf_weekly": "weekly",
        "tf_daily": "daily",
        "tf_4h": "4h"
    }

    timeframe = tf_map[cb.data]
    sym = user_lang.get(uid + "_symbol")
    price = user_lang.get(uid + "_price")

    if lang == "ar":
        prompt = (
            f"سعر العملة {sym.upper()} الآن هو {price:.6f}$.\n"
            f"قم بتحليل التشارت للإطار الزمني {timeframe} باستخدام مؤشرات شاملة:\n"
            f"- خطوط الدعم والمقاومة\n"
            f"- RSI, MACD, MA\n"
            f"- Bollinger Bands\n"
            f"- Fibonacci Levels\n"
            f"- Stochastic Oscillator\n"
            f"- Volume Analysis\n"
            f"- Trendlines باستخدام Regression\n"
            f"ثم قدم:\n"
            f"1. تقييم عام (صعود أم هبوط؟)\n"
            f"2. أقرب مقاومة ودعم\n"
            f"3. ثلاثة أهداف مستقبلية (قصير، متوسط، بعيد المدى)\n"
            f"✅ استخدم العربية فقط\n"
            f"❌ لا تشرح المشروع، فقط تحليل التشارت"
        )
    else:
        prompt = (
            f"The current price of {sym.upper()} is ${price:.6f}.\n"
            f"Analyze the {timeframe} chart using comprehensive indicators:\n"
            f"- Support and Resistance\n"
            f"- RSI, MACD, MA\n"
            f"- Bollinger Bands\n"
            f"- Fibonacci Levels\n"
            f"- Stochastic Oscillator\n"
            f"- Volume Analysis\n"
            f"- Trendlines using Regression\n"
            f"Then provide:\n"
            f"1. General trend (up/down)\n"
            f"2. Nearest resistance/support\n"
            f"3. Three future price targets\n"
            f"✅ Answer in English only\n"
            f"❌ Don't explain the project, only chart analysis"
        )

    await cb.message.edit_text("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)

    if not is_user_paid(cb.from_user.id) and has_trial(uid):
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        await cb.message.answer(
            "للوصول الكامل، يرجى الاشتراك مقابل 10 USDT لمرة واحدة."
            if lang == "ar"
            else "For full access, please subscribe for a one-time fee of 10 USDT.",
            reply_markup=kb
        )

# --- Webhook Handlers ---
async def handle_telegram_webhook(req: web.Request):
    try:
        update_data = await req.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot=bot, update=update)
    except Exception as e:
        print(f"❌ Error processing update: {e}")
    finally:
        return web.Response(status=200)

async def handle_nowpayments_webhook(req: web.Request):
    pool = req.app['db_pool']
    try:
        signature = req.headers.get("x-nowpayments-sig")
        body = await req.read()

        if not signature or not NOWPAYMENTS_IPN_SECRET:
            return web.Response(status=400, text="Configuration error")

        h = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), body, hashlib.sha512)
        expected_signature = h.hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            return web.Response(status=401, text="Invalid signature")

        data = json.loads(body)

        if data.get("payment_status") == "finished":
            user_id = int(data.get("order_id"))

            if user_id not in paid_users:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                        user_id
                    )

                paid_users.add(user_id)
                lang = user_lang.get(str(user_id), "ar")

                await bot.send_message(
                    user_id,
                    "✅ تم تأكيد الدفع بنجاح! شكراً لاشتراكك. يمكنك الآن استخدام البوت بشكل كامل."
                    if lang == "ar"
                    else "✅ Payment confirmed! Thank you for subscribing. You can now use the bot fully."
                )

        return web.Response(status=200, text="OK")

    except Exception as e:
        print(f"❌ Error in NOWPayments webhook: {e}")
        return web.Response(status=500, text="Internal Server Error")

# --- Healthcheck ---
async def health_check(req: web.Request):
    print("Health check endpoint was called by Render.")
    return web.Response(text="OK", status=200)

# --- Startup / Shutdown ---
async def on_startup(app_instance: web.Application):
    print("Connecting to database...")
    pool = await asyncpg.create_pool(DATABASE_URL)
    app_instance['db_pool'] = pool

    async with pool.acquire() as conn:
        # جدول المستخدمين المدفوعين
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paid_users (
                user_id BIGINT PRIMARY KEY
            );
        """)
        # جدول مستخدمي التجربة المجانية
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trial_users (
                user_id BIGINT PRIMARY KEY
            );
        """)
        # إضافة الأدمن تلقائياً
        await conn.execute(
            "INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            ADMIN_USER_ID
        )

        # تحميل المستخدمين المدفوعين من DB
        records = await conn.fetch("SELECT user_id FROM paid_users")
        paid_users.update(r['user_id'] for r in records)

        # تحميل مستخدمي التجربة من DB
        trial_records = await conn.fetch("SELECT user_id FROM trial_users")
        trial_users.update(str(r['user_id']) for r in trial_records)

    webhook_url = f"{WEBHOOK_URL}/"
    await bot.set_webhook(webhook_url)
    print(f"✅ Webhook set to {webhook_url}")

async def on_shutdown(app_instance: web.Application):
    print("ℹ️ Shutting down...")
    await app_instance['db_pool'].close()
    await bot.delete_webhook()
    await bot.session.close()

# --- App Init ---
app = web.Application()
app.router.add_get("/health", health_check)
app.router.add_post("/", handle_telegram_webhook)
app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    print("🚀 Starting bot locally for testing...")
    web.run_app(app, host="0.0.0.0", port=PORT)
