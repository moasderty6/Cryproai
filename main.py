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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
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
STARS_PROVIDER_TOKEN = os.getenv("STARS_PROVIDER_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_USER_ID = 6172153716

# --- إعداد البوت ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- ذاكرة المستخدمين (تتم مزامنتها مع قاعدة البيانات عند التشغيل) ---
paid_users = set()
trial_users = set()
user_session_data = {} # لتخزين مؤقت للعملة والسعر الحاليين

# --- دوال التحقق من الصلاحيات ---
def is_user_paid(user_id: int):
    return user_id in paid_users

def has_trial(user_id: int):
    # نستخدم التحقق من النص لأن الـ DB تخزنها كـ BigInt ونحولها لـ String في الذاكرة
    return str(user_id) not in trial_users

# --- دوال مساعدة للتنظيف والاتصال بالـ AI ---
def clean_response(text, lang="ar"):
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else:
        return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)

async def ask_groq(prompt, lang="ar"):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
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
    except Exception as e:
        print(f"❌ Error in create_nowpayments_invoice: {e}")
    return None

async def send_stars_invoice(chat_id: int, lang="ar"):
    prices = [LabeledPrice(label=" اشتراك البوت بـ 500 نجمة مدى الحياة⭐" if lang=="ar" else "Subscribe Now with 500 ⭐ Lifetime", amount=500)]
    title = "اشتراك البوت" if lang=="ar" else "Subscribe Now"
    description = "اشترك الآن باستخدام 500 ⭐ للوصول الكامل" if lang=="ar" else "Subscribe Now with 500 ⭐ Lifetime"
    payload = "stars_subscription"
    currency = "XTR"
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        provider_token=STARS_PROVIDER_TOKEN,
        currency=currency,
        prices=prices,
        payload=payload
    )

# --- ميزة الرادار (AI Opportunity Radar) ---
async def ai_opportunity_radar():
    watch_list = ["BTC", "ETH", "SOL", "BNB", "TIA", "FET", "INJ", "LINK"]
    print("🚀 AI Breakout Radar is active...")
    
    while True:
        await asyncio.sleep(1440) # كل 4 ساعات
        for symbol in watch_list:
            price = await get_price_cmc(symbol)
            if not price: continue
            
            pool = dp.get('db_pool')
            if not pool: continue
            
            async with pool.acquire() as conn:
                users = await conn.fetch("SELECT user_id, lang FROM users_info")

            for row in users:
                user_id = row['user_id']
                lang = row['lang'] or "ar"
                
                prompt = (
                    f"Analyze the current price of {symbol} at ${price:,.2f}. "
                    f"Write a very short urgent breakout alert in {'Arabic' if lang=='ar' else 'English'}."
                )
                ai_insight = await ask_groq(prompt, lang=lang)

                try:
                    if is_user_paid(user_id):
                        alert_text = (
                            f"🚨 **[ VIP BREAKOUT ALERT ]** 🚨\n"
                            f"───────────────────\n"
                            f"💎 **العملة:** #{symbol.upper()}\n"
                            f"💵 **السعر الحالي:** `${price:,.4f}`\n"
                            f"📈 **رؤية الذكاء الاصطناعي:**\n\n"
                            f"*{ai_insight}*\n"
                        )
                        await bot.send_message(user_id, alert_text, parse_mode=ParseMode.MARKDOWN)
                    else:
                        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
                        blurred_text = (
                            f"📡 **[ رادار الذكاء الاصطناعي ]**\n"
                            f"───────────────────\n"
                            f"⚠️ **تم رصد انفجار سعري محتمل لعملة من القائمة الذهبية!**\n\n"
                            f"💎 **العملة:** `****` (مخفي للمشتركين فقط)\n"
                            f"🔥 اشترك الآن لكشف العملة والحصول على الأهداف!"
                        )
                        await bot.send_message(user_id, blurred_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except:
                    continue
            break

# --- لوحات الأزرار ---
language_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
    ]
)

payment_keyboard_ar = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_with_crypto")],
        [InlineKeyboardButton(text=" اشترك الآن بـ 500 نجمة مدى الحياة⭐", callback_data="pay_with_stars")]
    ]
)

payment_keyboard_en = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Lifetime)", callback_data="pay_with_crypto")],
        [InlineKeyboardButton(text="⭐ Subscribe Now with 500 Stars Lifetime", callback_data="pay_with_stars")]
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

# --- معالجة الأوامر ---
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute(
            "INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            m.from_user.id
        )
    await m.answer(
        "👋 أهلاً بك، يرجى اختيار لغتك للمتابعة:\nWelcome, please choose your language to continue:",
        reply_markup=language_keyboard
    )

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = cb.from_user.id
    
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("UPDATE users_info SET lang = $1 WHERE user_id = $2", lang, uid)
    
    if is_user_paid(uid):
        msg = "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل." if lang == "ar" else "✅ Welcome back! Your subscription is active.\nSend a coin symbol to analyze."
        await cb.message.edit_text(msg)
    elif has_trial(uid):
        msg = "🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة للتحليل." if lang == "ar" else "🎁 You have one free trial! Send a coin symbol for analysis."
        await cb.message.edit_text(msg)
    else:
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        msg = "⚠️ انتهت تجربتك المجانية. اشترك للمتابعة." if lang == "ar" else "⚠️ Your free trial has ended. Please subscribe."
        await cb.message.edit_text(msg, reply_markup=kb)

@dp.callback_query(F.data == "pay_with_crypto")
async def process_crypto_pay(cb: types.CallbackQuery):
    uid = cb.from_user.id
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    
    await cb.message.edit_text("⏳ جاري إنشاء الرابط..." if lang == "ar" else "⏳ Generating link...")
    invoice_url = await create_nowpayments_invoice(uid)
    
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع الآن" if lang=="ar" else "💳 Pay Now", url=invoice_url)]])
        await cb.message.edit_text("✅ تم إنشاء الرابط (USDT BEP20):" if lang == "ar" else "✅ Link created (USDT BEP20):", reply_markup=kb)
    else:
        await cb.message.edit_text("❌ حدث خطأ." if lang == "ar" else "❌ Error occurred.")

@dp.callback_query(F.data == "pay_with_stars")
async def process_stars_pay(cb: types.CallbackQuery):
    uid = cb.from_user.id
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    await send_stars_invoice(uid, lang)
    await cb.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pq.id, ok=True)

@dp.message(F.content_type == "successful_payment")
async def on_successful_payment(msg: types.Message):
    uid = msg.from_user.id
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
    paid_users.add(uid)
    await msg.answer("✅ تم تفعيل اشتراكك بنجاح!")

# --- معالجة الرسائل النصية والتحليل ---
@dp.message(F.text)
async def handle_symbol_input(m: types.Message):
    if m.text.startswith('/'): return
    uid = m.from_user.id
    
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    
    if not is_user_paid(uid) and not has_trial(uid):
        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
        await m.answer("⚠️ انتهت التجربة. اشترك للمتابعة.", reply_markup=kb)
        return

    sym = m.text.strip().upper()
    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")
    price = await get_price_cmc(sym)

    if not price:
        await m.answer("❌ تعذر العثور على العملة.")
        return

    user_session_data[uid] = {"sym": sym, "price": price, "lang": lang}
    kb = timeframe_keyboard if lang == "ar" else timeframe_keyboard_en
    await m.answer(f"💵 السعر: ${price:.6f}\nاختر الإطار الزمني:" if lang == "ar" else f"💵 Price: ${price:.6f}\nSelect timeframe:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tf_"))
async def on_timeframe_selected(cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = user_session_data.get(uid)
    if not data: return
    
    lang, sym, price = data['lang'], data['sym'], data['price']
    tf = cb.data.replace("tf_", "")
    
    await cb.message.edit_text("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    
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

    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)
    
    if not is_user_paid(uid) and has_trial(uid):
        async with dp['db_pool'].acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        trial_users.add(str(uid))
        await cb.message.answer("✨ انتهت تجربتك المجانية. نأمل أن يكون التحليل قد نال إعجابك!" if lang == "ar" else "✨ Trial ended. We hope you enjoyed the analysis!")

# --- الويب هوك وإدارة التطبيق ---
async def handle_telegram_webhook(req: web.Request):
    data = await req.json()
    await dp.feed_update(bot, types.Update(**data))
    return web.Response(text="ok")

async def handle_nowpayments_webhook(req: web.Request):
    signature = req.headers.get("x-nowpayments-sig")
    body = await req.read()
    h = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), body, hashlib.sha512)
    if hmac.compare_digest(h.hexdigest(), signature):
        data = json.loads(body)
        if data.get("payment_status") == "finished":
            uid = int(data.get("order_id"))
            async with req.app['db_pool'].acquire() as conn:
                await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
            paid_users.add(uid)
            await bot.send_message(uid, "✅ تم تفعيل اشتراكك!")
    return web.Response(text="ok")

async def on_startup(app_instance: web.Application):
    pool = await asyncpg.create_pool(DATABASE_URL)
    app_instance['db_pool'] = pool
    dp['db_pool'] = pool
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT)")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
        
        p_recs = await conn.fetch("SELECT user_id FROM paid_users")
        paid_users.update(r['user_id'] for r in p_recs)
        
        t_recs = await conn.fetch("SELECT user_id FROM trial_users")
        trial_users.update(str(r['user_id']) for r in t_recs)
        
    asyncio.create_task(ai_opportunity_radar())
    await bot.set_webhook(f"{WEBHOOK_URL}/")

app = web.Application()
app.router.add_post("/", handle_telegram_webhook)
app.router.add_post("/webhook/nowpayments", handle_nowpayments_webhook)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
