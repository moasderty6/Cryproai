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
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            result = res.json()
            if "choices" in result:
                content = result["choices"][0]["message"]["content"]
                return clean_response(content, lang=lang).strip()
            return "❌"
    except Exception: return "❌"

async def get_price_cmc(symbol):
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()["data"][symbol.upper()]["quote"]["USD"]["price"]
    except: return None

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

# --- رادار الفرص الذكي (تعديل اللغات والتنسيق) ---
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
                        
                        # طلب تحليل بلغة واحدة فقط لكل مستخدم لمنع الخلط
                        if is_paid:
                            prompt = f"Give a very short 2-line technical breakout insight for #{symbol} at ${price_display}. Answer strictly in {lang} language only. No headers."
                            insight = await ask_groq(prompt, lang=lang)
                            text = (f"🚨 **VIP BREAKOUT ALERT**\n\n"
                                    f"💎 **العملة:** #{symbol.upper()}\n"
                                    f"💵 **السعر:** `${price_display}`\n"
                                    f"📈 **الرؤية:**\n{insight}")
                        else:
                            prompt = f"Write a 1-line technical breakout hint for a coin at ${price_display}. DO NOT mention the coin name. Answer strictly in {lang} language only. No headers."
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

# --- الأوامر الرئيسية ---
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar"), InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]])
    await m.answer("👋 اختر لغتك / Choose Language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("UPDATE users_info SET lang = $1 WHERE user_id = $2", lang, cb.from_user.id)
    await cb.message.edit_text("✅ أرسل رمز العملة (مثلاً BTC)" if lang=="ar" else "✅ Send coin symbol (e.g. BTC)")

@dp.message(F.text)
async def handle_symbol(m: types.Message):
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="أسبوعي" if lang=="ar" else "Weekly", callback_data="tf_weekly"),
        InlineKeyboardButton(text="يومي" if lang=="ar" else "Daily", callback_data="tf_daily"),
        InlineKeyboardButton(text="4 ساعات" if lang=="ar" else "4H", callback_data="tf_4h")
    ]])
    await m.answer(f"💵 {sym}: ${price:.6f}\nاختر الإطار الزمني:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tf_"))
async def run_full_analysis(cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = user_session_data.get(uid)
    if not data: return
    lang, sym, price, tf = data['lang'], data['sym'], data['price'], cb.data.replace("tf_", "")
    await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")
    
    # --- البرومبت الأصلي الطويل (دون أي اختصار) ---
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

    res = await ask_groq(prompt, lang=lang)
    await cb.message.answer(res)
    
    if not (await is_user_paid(dp['db_pool'], uid)):
        async with dp['db_pool'].acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

# --- نظام التشغيل ---
async def handle_webhook(req: web.Request):
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
app.router.add_post("/", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
