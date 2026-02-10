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
PORT = int(os.getenv("PORT", 8000))

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
STARS_PROVIDER_TOKEN = os.getenv("STARS_PROVIDER_TOKEN")
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

# --- دوال المساعدة ---
def clean_response(text, lang="ar"):
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)

async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            result = res.json()
            if "choices" in result:
                content = result["choices"][0]["message"]["content"]
                return clean_response(content, lang=lang).strip()
            return "❌ AI Limit Reached"
    except Exception: return "❌ التحليل غير متاح حالياً."

async def get_price_cmc(symbol):
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()["data"][symbol.upper()]["quote"]["USD"]["price"]
    except: return None

# --- لوحات المفاتيح ---
def get_payment_kb(lang):
    if lang == "ar":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_with_crypto")],
            [InlineKeyboardButton(text="⭐ اشترك الآن بـ 500 نجمة مدى الحياة", callback_data="pay_with_stars")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Lifetime)", callback_data="pay_with_crypto")],
        [InlineKeyboardButton(text="⭐ Subscribe Now with 500 Stars Lifetime", callback_data="pay_with_stars")]
    ])

timeframe_kb_ar = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="أسبوعي", callback_data="tf_weekly"),
    InlineKeyboardButton(text="يومي", callback_data="tf_daily"),
    InlineKeyboardButton(text="4 ساعات", callback_data="tf_4h")
]])

timeframe_kb_en = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="Weekly", callback_data="tf_weekly"),
    InlineKeyboardButton(text="Daily", callback_data="tf_daily"),
    InlineKeyboardButton(text="4H", callback_data="tf_4h")
]])

# --- رادار الفرص (VIP + مجاني مع تحليل تلميحي) ---# --- رادار الفرص (تعديل لمنع خلط النصوص وتحسين التنسيق) ---
async def ai_opportunity_radar(pool):
    print("🚀 AI Radar is active...")
    while True:
        try:
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
            async with httpx.AsyncClient() as client:
                res = await client.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest", 
                                     headers=headers, params={"limit": "50"})
                if res.status_code == 200:
                    watch_list = res.json()["data"]
                    selected_coin = random.choice(watch_list)
                    symbol = selected_coin["symbol"]
                    price = selected_coin["quote"]["USD"]["price"]
                    price_display = f"{price:.8f}" if price < 1 else f"{price:,.2f}"

                    # تعديل البرومبت ليكون صارمًا (بدون مقدمات)
                    vip_prompt = (
                        f"Analyze #{symbol} at ${price_display}. Give a 2-line technical insight. "
                        f"Rules: Start immediately with the analysis. No 'Technical insight in English/Arabic'. "
                        f"No introductions. Format: [English Analysis] \n\n [Arabic Analysis]"
                    )
                    vip_insight = await ask_groq(vip_prompt)

                    # تعديل برومبت المجانيين لمنع الجمل الإضافية
                    free_prompt = (
                        f"Write a 1-line technical breakout hint for a coin at price ${price_display}. "
                        f"Format strictly as: AR: [Arabic hint] \nEN: [English hint]. "
                        f"Do not mention coin names. No introductory text."
                    )
                    free_insight = await ask_groq(free_prompt)

                    users = await pool.fetch("SELECT user_id, lang FROM users_info")
                    for row in users:
                        uid, lang = row['user_id'], row['lang'] or "ar"
                        is_paid = await is_user_paid(pool, uid)
                        
                        try:
                            if is_paid:
                                text = (
                                    f"🚨 **[ VIP BREAKOUT ALERT ]**\n\n"
                                    f"💎 **العملة:** #{symbol.upper()}\n"
                                    f"💵 **السعر:** `${price_display}`\n"
                                    f"📈 **الرؤية الفنية:**\n\n{vip_insight}"
                                )
                                await bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
                            else:
                                if lang == "ar":
                                    blurred = (
                                        f"📡 **[ رادار الفرص الذكي ]**\n"
                                        f"───────────────────\n"
                                        f"🔥 **تم رصد انفجار سعري محتمل الآن!**\n\n"
                                        f"📊 **العملة:** `•••••` 🔒\n"
                                        f"💰 **السعر الحالي:** `${price_display}`\n\n"
                                        f"📈 **تلميح تقني:**\n{free_insight}\n\n"
                                        f"📢 **اشترك الآن لكشف اسم العملة والحصول على الأهداف!**"
                                    )
                                else:
                                    blurred = (
                                        f"📡 **[ SMART RADAR ALERT ]**\n"
                                        f"───────────────────\n"
                                        f"🔥 **Potential Breakout Detected!**\n\n"
                                        f"📊 **Symbol:** `•••••` 🔒\n"
                                        f"💰 **Current Price:** `${price_display}`\n\n"
                                        f"📈 **Technical Hint:**\n{free_insight}\n\n"
                                        f"📢 **Subscribe to VIP to unlock the symbol!**"
                                    )
                                await bot.send_message(uid, blurred, reply_markup=get_payment_kb(lang), parse_mode=ParseMode.MARKDOWN)
                            await asyncio.sleep(0.05) 
                        except: continue
        except Exception as e: print(f"⚠️ Radar Error: {e}")
        await asyncio.sleep(14400)


# ---Handlers ---
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await m.answer("👋 اختر لغتك / Choose Language:", 
                  reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                      [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
                      [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
                  ]))

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("UPDATE users_info SET lang = $1 WHERE user_id = $2", lang, cb.from_user.id)
    
    paid = await is_user_paid(dp['db_pool'], cb.from_user.id)
    msg = "✅ أرسل رمز العملة للتحليل" if lang=="ar" else "✅ Send symbol to analyze"
    if not paid and not (await has_trial(dp['db_pool'], cb.from_user.id)):
        return await cb.message.edit_text("⚠️ انتهت التجربة.", reply_markup=get_payment_kb(lang))
    await cb.message.edit_text(msg)

@dp.message(F.text)
async def handle_input(m: types.Message):
    if m.text.startswith('/'): return
    uid, pool = m.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await m.answer("⚠️ اشترك للمتابعة", reply_markup=get_payment_kb(lang))

    sym = m.text.strip().upper()
    price = await get_price_cmc(sym)
    if not price: return await m.answer("❌ عملة غير مدعومة")

    user_session_data[uid] = {"sym": sym, "price": price, "lang": lang}
    kb = timeframe_kb_ar if lang == "ar" else timeframe_kb_en
    await m.answer(f"💵 {sym}: ${price:.6f}\nإطار العمل:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tf_"))
async def run_analysis(cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = user_session_data.get(uid)
    if not data: return
    
    lang, sym, price, tf = data['lang'], data['sym'], data['price'], cb.data.replace("tf_", "")
    await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")
    
    # --- البرومبت الأصلي الخاص بك (تمت إعادته بالكامل) ---
    if lang == "ar":
        prompt = (
            f"سعر العملة {sym} الآن هو {price:.6f}$.\n"
            f"قم بتحليل التشارت للإطار الزمني {tf} باستخدام مؤشرات شاملة:\n"
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
            f"The current price of {sym} is ${price:.6f}.\n"
            f"Analyze the {tf} chart using comprehensive indicators:\n"
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
    
    if not (await is_user_paid(dp['db_pool'], uid)):
        async with dp['db_pool'].acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

# --- نظام المدفوعات والويب هوك ---
@dp.callback_query(F.data == "pay_with_crypto")
async def crypto_pay(cb: types.CallbackQuery):
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    data = {"price_amount": 10, "price_currency": "usd", "order_id": str(cb.from_user.id), 
            "ipn_callback_url": f"{WEBHOOK_URL}/webhook/nowpayments"}
    async with httpx.AsyncClient() as client:
        res = await client.post("https://api.nowpayments.io/v1/invoice", headers=headers, json=data)
        if res.status_code < 300:
            url = res.json().get("invoice_url")
            await cb.message.answer(f"💳 رابط الدفع USDT (BEP20):\n{url}")

@dp.callback_query(F.data == "pay_with_stars")
async def stars_pay(cb: types.CallbackQuery):
    await bot.send_invoice(cb.from_user.id, title="VIP Access", description="Lifetime Subscription", 
                           payload="stars", provider_token=STARS_PROVIDER_TOKEN, currency="XTR", 
                           prices=[LabeledPrice(label="VIP", amount=500)])

@dp.pre_checkout_query()
async def pre_checkout(pq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pq.id, ok=True)

@dp.message(F.content_type == "successful_payment")
async def pay_success(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await m.answer("✅ تم التفعيل!")

async def handle_tg_webhook(req: web.Request):
    if req.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET_TOKEN: return web.Response(status=403)
    await dp.feed_update(bot, types.Update(**(await req.json())))
    return web.Response(text="ok")

async def on_startup(app_instance):
    pool = await asyncpg.create_pool(DATABASE_URL)
    app_instance['db_pool'] = dp['db_pool'] = pool
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT)")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
    asyncio.create_task(ai_opportunity_radar(pool))
    await bot.set_webhook(url=f"{WEBHOOK_URL}/", secret_token=SECRET_TOKEN)

app = web.Application()
app.router.add_post("/", handle_tg_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
