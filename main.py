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

# --- ذاكرة المستخدمين (تتم مزامنتها مع DB عند التشغيل) ---
paid_users = set()
trial_users = set()
user_data_cache = {} # كاش مؤقت للغة والعملة المختارة حالياً

def is_user_paid(user_id: int):
    return user_id in paid_users

def has_trial(uid: str):
    return str(uid) not in trial_users

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
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
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
            if res.status_code != 200: return None
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except: return None

async def create_nowpayments_invoice(user_id: int):
    url = "https://api.nowpayments.io/v1/invoice"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    data = {
        "price_amount": 10, "price_currency": "usd", "order_id": str(user_id),
        "ipn_callback_url": f"{WEBHOOK_URL}/webhook/nowpayments",
        "success_url": f"https://t.me/{(await bot.get_me()).username}",
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, json=data)
            if 200 <= res.status_code < 300: return res.json().get("invoice_url")
    except Exception as e: print(f"❌ NOWPayments error: {e}")
    return None

async def send_stars_invoice(chat_id: int, lang="ar"):
    prices = [LabeledPrice(label=" اشتراك البوت بـ 500 نجمة مدى الحياة⭐" if lang=="ar" else "Subscribe Now with 500 ⭐ Lifetime", amount=500)]
    await bot.send_invoice(
        chat_id=chat_id, title="اشتراك البوت" if lang=="ar" else "Subscribe Now",
        description="اشترك الآن باستخدام 500 ⭐ للوصول الكامل" if lang=="ar" else "Subscribe Now with 500 ⭐ Lifetime",
        provider_token=STARS_PROVIDER_TOKEN, currency="XTR", prices=prices, payload="stars_subscription"
    )

# --- وظيفة الرادار القوية مرتبطة بقاعدة البيانات ---
async def ai_opportunity_radar():
    watch_list = ["BTC", "ETH", "SOL", "BNB", "TIA", "FET", "INJ", "LINK"]
    print("🚀 AI Breakout Radar (DB-Linked) is active...")
    
    while True:
        await asyncio.sleep(14400) # كل 4 ساعات
        for symbol in watch_list:
            price = await get_price_cmc(symbol)
            if not price: continue
            
            prompt = f"Analyze {symbol} at ${price:,.2f}. Write a short, high-impact 'VIP Opportunity Alert' in Arabic. Tone: Professional, Urgent."
            ai_insight = await ask_groq(prompt, lang="ar")

            pool = dp['db_pool']
            async with pool.acquire() as conn:
                all_users_db = await conn.fetch("SELECT user_id, lang FROM users_info")

            for row in all_users_db:
                try:
                    user_id, lang = row['user_id'], row['lang'] or "ar"
                    if is_user_paid(user_id):
                        msg = (f"🚨 **[ VIP BREAKOUT ALERT ]** 🚨\n───────────────────\n"
                               f"💎 **العملة:** #{symbol.upper()}\n💵 **السعر الحالي:** `${price:,.4f}`\n"
                               f"📈 **رؤية الذكاء الاصطناعي:**\n\n*{ai_insight}*\n\n───────────────────\n⚡️ *الفرصة لا تنتظر المترددين!*")
                        await bot.send_message(user_id, msg, parse_mode=ParseMode.MARKDOWN)
                    else:
                        kb = payment_keyboard_ar if lang == "ar" else payment_keyboard_en
                        blurred = (f"📡 **[ رادار الذكاء الاصطناعي ]**\n───────────────────\n"
                                   f"⚠️ **تم رصد انفجار سعري محتمل لعملة من القائمة الذهبية!**\n\n"
                                   f"💎 **العملة:** `****` (مخفي للمشتركين فقط)\n📈 **الحالة:** تجميع حيتان واختراق وشيك.\n\n"
                                   f"🔥 اشترك الآن لكشف العملة والحصول على أهداف الدخول والخروج الدقيقة!") if lang == "ar" else \
                                  (f"📡 **[ AI MARKET RADAR ]**\n───────────────────\n"
                                   f"⚠️ **Potential breakout detected for a Top-Tier coin!**\n\n"
                                   f"💎 **Symbol:** `****` (Hidden for VIP only)\n📈 **Status:** Whale accumulation detected.\n\n"
                                   f"🔥 Subscribe now to unlock the symbol!")
                        await bot.send_message(user_id, blurred, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except: continue
            break

# --- لوحات الأزرار ---
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])
payment_keyboard_ar = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_with_crypto")],
    [InlineKeyboardButton(text=" اشترك الآن بـ 500 نجمة مدى الحياة⭐", callback_data="pay_with_stars")]
])
payment_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Lifetime)", callback_data="pay_with_crypto")],
    [InlineKeyboardButton(text="⭐ Subscribe Now with 500 Stars Lifetime", callback_data="pay_with_stars")]
])
timeframe_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="أسبوعي", callback_data="tf_weekly"), InlineKeyboardButton(text="يومي", callback_data="tf_daily"), InlineKeyboardButton(text="4 ساعات", callback_data="tf_4h")]
])
timeframe_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Weekly", callback_data="tf_weekly"), InlineKeyboardButton(text="Daily", callback_data="tf_daily"), InlineKeyboardButton(text="4H", callback_data="tf_4h")]
])

# --- أوامر البوت ---
@dp.message(F.text.in_({'/start', 'start'}))
async def start(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", m.from_user.id)
    await m.answer("👋 أهلاً بك، اختر لغتك للمتابعة | Welcome, choose your language:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = cb.from_user.id
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("UPDATE users_info SET lang = $1 WHERE user_id = $2", lang, uid)
    user_data_cache[uid] = {"lang": lang}
    if is_user_paid(uid):
        await cb.message.edit_text("✅ اشتراكك مفعل. أرسل رمز العملة." if lang == "ar" else "✅ Subscription active. Send symbol.")
    elif has_trial(str(uid)):
        await cb.message.edit_text("🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة." if lang == "ar" else "🎁 One free trial available! Send symbol.")
    else:
        await cb.message.edit_text("⚠️ انتهت التجربة. اشترك للوصول الكامل.", reply_markup=payment_keyboard_ar if lang=="ar" else payment_keyboard_en)

@dp.callback_query(F.data == "pay_with_crypto")
async def process_crypto_payment(cb: types.CallbackQuery):
    uid = cb.from_user.id
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    await cb.message.edit_text("⏳ جاري إنشاء الرابط..." if lang == "ar" else "⏳ Generating link...")
    invoice_url = await create_nowpayments_invoice(uid)
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع الآن" if lang=="ar" else "💳 Pay Now", url=invoice_url)]])
        await cb.message.edit_text("✅ ادفع عبر الرابط (USDT BEP20):" if lang == "ar" else "✅ Pay via link (USDT BEP20):", reply_markup=kb)
    else:
        await cb.message.edit_text("❌ خطأ، حاول لاحقاً.")
    await cb.answer()

@dp.callback_query(F.data == "pay_with_stars")
async def process_stars_payment(cb: types.CallbackQuery):
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", cb.from_user.id)
    await send_stars_invoice(cb.from_user.id, row['lang'] if row else "ar")
    await cb.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.content_type == "successful_payment")
async def successful_payment(msg: types.Message):
    user_id = msg.from_user.id
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    paid_users.add(user_id)
    await msg.answer("✅ تم تفعيل الاشتراك بنجاح!")

@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM users_info")
    await m.answer(f"ℹ️ عدد المستخدمين المسجلين: {count}")

@dp.message(Command("admin"))
async def admin_cmd(m: types.Message):
    await m.answer("📌 للدعم: @AiCrAdmin")

@dp.message(Command("reset_trials"))
async def reset_trials_cmd(m: types.Message):
    if m.from_user.id != ADMIN_USER_ID: return
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("DELETE FROM trial_users")
    trial_users.clear()
    await m.answer("✅ تم تصفير التجارب المجانية.")

@dp.message(F.text)
async def handle_symbol(m: types.Message):
    if m.text.startswith('/'): return
    uid = m.from_user.id
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    if not is_user_paid(uid) and not has_trial(str(uid)):
        await m.answer("⚠️ انتهت تجربتك.", reply_markup=payment_keyboard_ar if lang=="ar" else payment_keyboard_en)
        return
    sym = m.text.strip().lower()
    price = await get_price_cmc(sym)
    if not price:
        await m.answer("❌ تعذر جلب السعر.")
        return
    if uid not in user_data_cache: user_data_cache[uid] = {}
    user_data_cache[uid].update({"symbol": sym, "price": price})
    await m.answer(f"💵 السعر: ${price:.6f}\nاختر الإطار الزمني:", reply_markup=timeframe_keyboard if lang=="ar" else timeframe_keyboard_en)

@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    uid = cb.from_user.id
    async with dp['db_pool'].acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = row['lang'] if row else "ar"
    tf = cb.data.replace("tf_", "")
    u_cache = user_data_cache.get(uid, {})
    sym, price = u_cache.get("symbol"), u_cache.get("price")
    if not is_user_paid(uid) and not has_trial(str(uid)): return

    await cb.message.edit_text("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    
    # --- البرومبت التفصيلي (الذي طلبته) ---
    if lang == "ar":
        prompt = (f"سعر العملة {sym.upper()} الآن هو {price:.6f}$.\nقم بتحليل التشارت للإطار الزمني {tf} باستخدام مؤشرات شاملة:\n"
                  f"- خطوط الدعم والمقاومة\n- RSI, MACD, MA\n- Bollinger Bands\n- Fibonacci Levels\n- Stochastic Oscillator\n- Volume Analysis\n- Trendlines باستخدام Regression\n"
                  f"ثم قدم:\n1. تقييم عام (صعود أم هبوط؟)\n2. أقرب مقاومة ودعم\n3. ثلاثة أهداف مستقبلية (قصير، متوسط، بعيد المدى)\n✅ استخدم العربية فقط\n❌ لا تشرح المشروع، فقط تحليل التشارت")
    else:
        prompt = (f"The current price of {sym.upper()} is ${price:.6f}.\nAnalyze the {tf} chart using comprehensive indicators:\n"
                  f"- Support and Resistance\n- RSI, MACD, MA\n- Bollinger Bands\n- Fibonacci Levels\n- Stochastic Oscillator\n- Volume Analysis\n- Trendlines using Regression\n"
                  f"Then provide:\n1. General trend (up/down)\n2. Nearest resistance/support\n3. Three future price targets\n✅ Answer in English only\n❌ Don't explain the project, only chart analysis")

    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)
    if not is_user_paid(uid) and has_trial(str(uid)):
        trial_users.add(str(uid))
        async with dp['db_pool'].acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
    if not is_user_paid(uid):
        await cb.message.answer("للوصول الكامل، اشترك بـ 10 USDT.", reply_markup=payment_keyboard_ar if lang=="ar" else payment_keyboard_en)

# --- تشغيل التطبيق وقاعدة البيانات ---
async def on_startup(app_instance):
    pool = await asyncpg.create_pool(DATABASE_URL)
    app_instance['db_pool'] = pool
    dp['db_pool'] = pool
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT)")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
        p_records = await conn.fetch("SELECT user_id FROM paid_users"); paid_users.update(r['user_id'] for r in p_records)
        t_records = await conn.fetch("SELECT user_id FROM trial_users"); trial_users.update(str(r['user_id']) for r in t_records)
    asyncio.create_task(ai_opportunity_radar())
    await bot.set_webhook(f"{WEBHOOK_URL}/")

app = web.Application()
app.router.add_post("/", lambda r: dp.feed_update(bot, types.Update(**(asyncio.run(r.json()))))) 
app.on_startup.append(on_startup)
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
