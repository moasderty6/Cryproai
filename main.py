import asyncio
import os
import re
import json
import hmac
import hashlib
import asyncpg
import httpx
import random
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command

# --- تحميل الإعدادات ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
SECRET_TOKEN = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:20]
PORT = int(os.getenv("PORT", 10000))

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_USER_ID = 6172153716

GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"

# --- إعداد البوت ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
user_session_data = {}

# --- وظائف قاعدة البيانات ---
async def is_user_paid(pool, user_id: int):
    res = await pool.fetchval("SELECT 1 FROM paid_users WHERE user_id = $1", user_id)
    return bool(res)

async def has_trial(pool, user_id: int):
    res = await pool.fetchval("SELECT 1 FROM trial_users WHERE user_id = $1", user_id)
    return not bool(res)

# --- دوال المساعدة والدفع ---
async def create_nowpayments_invoice(user_id: int):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
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
            return res.json().get("invoice_url")
    except: return None

async def send_stars_invoice(chat_id: int, lang="ar"):
    prices = [LabeledPrice(label="اشتراك مدى الحياة ⭐" if lang=="ar" else "Lifetime Subscription ⭐", amount=500)]
    await bot.send_invoice(
        chat_id=chat_id,
        title="اشتراك VIP" if lang=="ar" else "VIP Subscription",
        description="فتح جميع ميزات البوت مدى الحياة" if lang=="ar" else "Unlock all features forever",
        payload="stars_pay",
        provider_token="", 
        currency="XTR",
        prices=prices
    )

def get_payment_kb(lang):
    if lang == "ar":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_crypto")],
            [InlineKeyboardButton(text=" اشتراك الآن بـ 500 نجمة مدى الحياة⭐", callback_data="pay_stars")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe (10 USDT Lifetime)", callback_data="pay_crypto")],
        [InlineKeyboardButton(text="⭐ Subscribe with 500 Stars", callback_data="pay_stars")]
    ])

# --- رادار الفرص الذكي (الشكل الأصلي) ---
async def ai_opportunity_radar(pool):
    while True:
        try:
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
            async with httpx.AsyncClient() as client:
                res = await client.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest", 
                                     headers=headers, params={"limit": "50"})
                if res.status_code == 200:
                    selected_coin = random.choice(res.json()["data"])
                    symbol = selected_coin["symbol"]
                    price = selected_coin["quote"]["USD"]["price"]
                    price_display = f"{price:.8f}" if price < 1 else f"{price:,.2f}"

                    users = await pool.fetch("SELECT user_id, lang FROM users_info")
                    for row in users:
                        uid, lang = row['user_id'], row['lang'] or "ar"
                        is_paid = await is_user_paid(pool, uid)
                        
                        if is_paid:
                            prompt = f"Give a very short 2-line technical breakout insight for #{symbol} at ${price_display}. Answer strictly in {lang} language only."
                            insight = await ask_groq(prompt, lang=lang)
                            text = (f"🚨 **VIP BREAKOUT ALERT**\n\n"
                                    f"💎 **العملة:** #{symbol.upper()}\n"
                                    f"💵 **السعر:** `${price_display}`\n"
                                    f"📈 **الرؤية:**\n{insight}")
                        else:
                            prompt = f"Write a 1-line technical breakout hint for a coin at ${price_display}. DO NOT mention the coin name. Answer strictly in {lang}."
                            insight = await ask_groq(prompt, lang=lang)
                            if lang == "ar":
                                text = (f"📡 **رادار الفرص الذكي**\n"
                                        f"───────────────────\n"
                                        f"🔥 **تم رصد انفجار سعري محتمل الآن!**\n\n"
                                        f"📊 **العملة:** `•••••` 🔒\n"
                                        f"💰 **السعر الحالي:** `${price_display}`\n"
                                        f"📈 **تلميح تقني:**\n_{insight}_\n\n"
                                        f"📢 **اشترك الآن لكشف اسم العملة والأهداف!**")
                            else:
                                text = (f"📡 **SMART RADAR ALERT**\n"
                                        f"───────────────────\n"
                                        f"🔥 **Potential Breakout Detected!**\n\n"
                                        f"📊 **Symbol:** `•••••` 🔒\n"
                                        f"💰 **Price:** `${price_display}`\n"
                                        f"📈 **Technical Hint:**\n_{insight}_\n\n"
                                        f"📢 **Subscribe VIP to unlock the symbol!**")
                        try:
                            await bot.send_message(uid, text, reply_markup=None if is_paid else get_payment_kb(lang), parse_mode=ParseMode.MARKDOWN)
                        except: pass
                        await asyncio.sleep(0.05)
        except: pass
        await asyncio.sleep(14400)

# --- نظام الـ AI ---
async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            ans = res.json()["choices"][0]["message"]["content"]
            if lang == "ar": return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', ans)
            return ans
    except: return "..."

# --- الأوامر ---
@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    pool = dp['db_pool']
    total = await pool.fetchval("SELECT count(*) FROM users_info")
    vips = await pool.fetchval("SELECT count(*) FROM paid_users")
    trials = await pool.fetchval("SELECT count(*) FROM trial_users")
    
    msg = (f"📊 **إحصائيات البوت:**\n"
           f"───────────────────\n"
           f"👥 **إجمالي المستخدمين:** `{total}`\n"
           f"💎 **المشتركين (VIP):** `{vips}`\n"
           f"🎁 **مستخدمي التجربة:** `{trials}`")
    await m.answer(msg, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar"), InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]])
    await m.answer("👋 أهلاً بك، يرجى اختيار لغتك:\nWelcome, please choose your language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("UPDATE users_info SET lang = $1 WHERE user_id = $2", lang, cb.from_user.id)
    
    is_paid = await is_user_paid(dp['db_pool'], cb.from_user.id)
    has_tr = await has_trial(dp['db_pool'], cb.from_user.id)

    if is_paid:
        msg = "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل." if lang == "ar" else "✅ Welcome back! Send a symbol to analyze."
    elif has_tr:
        msg = "🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة." if lang == "ar" else "🎁 You have one free trial! Send a symbol."
    else:
        msg = "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ لمرة واحدة." if lang == "ar" else "⚠️ Your free trial has ended. For full access, please subscribe for a one-time fee of 10 USDT or 500 ⭐."
    
    await cb.message.edit_text(msg, reply_markup=None if (is_paid or has_tr) else get_payment_kb(lang))

@dp.message(F.text)
async def handle_symbol(m: types.Message):
    if m.text.startswith('/'): return
    uid, pool = m.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await m.answer("⚠️ انتهت تجربتك المجانية.", reply_markup=get_payment_kb(lang))
    
    sym = m.text.strip().upper()
    await m.answer("⏳ جاري جلب السعر..." if lang=="ar" else "⏳ Fetching price...")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={sym}", headers={"X-CMC_PRO_API_KEY": CMC_KEY})
            price = res.json()["data"][sym]["quote"]["USD"]["price"]
    except: return await m.answer("❌ عملة غير مدعومة")
    
    user_session_data[uid] = {"sym": sym, "price": price, "lang": lang}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="أسبوعي" if lang=="ar" else "Weekly", callback_data="tf_weekly"),
        InlineKeyboardButton(text="يومي" if lang=="ar" else "Daily", callback_data="tf_daily"),
        InlineKeyboardButton(text="4 ساعات" if lang=="ar" else "4H", callback_data="tf_4h")
    ]])
    await m.answer(f"💵 {sym}: ${price:.6f}\n⏳ اختر الإطار الزمني للتحليل:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tf_"))
async def run_analysis(cb: types.CallbackQuery):
    uid, pool = cb.from_user.id, dp['db_pool']
    data = user_session_data.get(uid)
    if not data: return
    lang, sym, price, tf = data['lang'], data['sym'], data['price'], cb.data.replace("tf_", "")
    
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await cb.message.edit_text("⚠️ انتهت التجربة.", reply_markup=get_payment_kb(lang))

    await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")
    
    # --- برومبت التحليل (المحمي من التغيير) ---
    if lang == "ar":
        prompt = (f"سعر العملة {sym} الآن هو {price:.6f}$.\nقم بتحليل التشارت للإطار الزمني {tf} باستخدام مؤشرات شاملة:\n"
                  f"- خطوط الدعم والمقاومة\n- RSI, MACD, MA\n- Bollinger Bands\n- Fibonacci Levels\n- Stochastic Oscillator\n- Volume Analysis\n- Trendlines باستخدام Regression\n"
                  f"ثم قدم:\n1. تقييم عام (صعود أم هبوط؟)\n2. أقرب مقاومة ودعم\n3. ثلاثة أهداف مستقبلية\n✅ العربية فقط | ❌ لا تشرح المشروع")
    else:
        prompt = (f"Price of {sym}: ${price:.6f}. Analyze {tf} timeframe:\n- Support/Resistance, RSI, MACD, MA, BB, Fibonacci, Volume.\n"
                  f"Provide: 1. Trend, 2. Levels, 3. 3 Targets.\n✅ English only | ❌ No project info")

    res = await ask_groq(prompt, lang=lang)
    await cb.message.answer(res)
    
    if not (await is_user_paid(pool, uid)):
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        await cb.message.answer("للوصول الكامل، يرجى الاشتراك.", reply_markup=get_payment_kb(lang))

# --- الدفع ---
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

@dp.callback_query(F.data == "pay_stars")
async def stars_pay(cb: types.CallbackQuery):
    await cb.answer()
    user = await dp['db_pool'].fetchrow("SELECT lang FROM users_info WHERE user_id = $1", cb.from_user.id)
    await send_stars_invoice(cb.from_user.id, lang=user['lang'] if user else "ar")

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery): await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message(F.successful_payment)
async def success_pay(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await m.answer("✅ تم تفعيل VIP!")

# --- السيرفر ---
async def handle_webhook(req: web.Request):
    data = await req.json()
    asyncio.create_task(dp.feed_update(bot, types.Update(**data)))
    return web.Response(text="ok")

async def on_startup(app):
    pool = await asyncpg.create_pool(DATABASE_URL)
    app['db_pool'] = dp['db_pool'] = pool
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT)")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
    asyncio.create_task(ai_opportunity_radar(pool))
    await bot.set_webhook(f"{WEBHOOK_URL}/")

app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
