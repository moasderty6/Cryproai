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
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
SECRET_TOKEN = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:20]
PORT = int(os.getenv("PORT", 10000))

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_USER_ID = 6172153716

GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

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
    prices = [LabeledPrice(label="اشتراك البوت بـ 500 نجمة مدى الحياة ⭐" if lang=="ar" else "Subscribe Now with 500 ⭐ Lifetime", amount=500)]
    await bot.send_invoice(
        chat_id=chat_id,
        title="اشتراك VIP" if lang=="ar" else "VIP Subscription",
        description="اشترك الآن باستخدام 500 ⭐ للوصول الكامل" if lang=="ar" else "Subscribe Now with 500 ⭐ for full access",
        payload="stars_pay",
        provider_token="", 
        currency="XTR",
        prices=prices
    )

def get_payment_kb(lang):
    if lang == "ar":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT مدى الحياة)", callback_data="pay_crypto")],
            [InlineKeyboardButton(text=" اشترك الآن بـ 500 نجمة مدى الحياة⭐", callback_data="pay_stars")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Lifetime)", callback_data="pay_crypto")],
        [InlineKeyboardButton(text="⭐ Subscribe Now with 500 Stars Lifetime", callback_data="pay_stars")]
    ])

# --- رادار الفرص الذكي ---
async def ai_opportunity_radar(pool):
    while True:
        try:
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}

            async with httpx.AsyncClient(timeout=20) as client:
                # جلب أحدث العملات
                res = await client.get(
                    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                    headers=headers,
                    params={"limit": "100"}
                )
                coins = res.json()["data"]
                opportunities = []

                # فلترة العملات حسب حجم التداول، السعر، والسيولة
                for c in coins:
                    price = c["quote"]["USD"]["price"]
                    volume = c["quote"]["USD"]["volume_24h"]
                    change = c["quote"]["USD"]["percent_change_24h"]
                    marketcap = c["quote"]["USD"]["market_cap"]

                    if (
                        volume > 60_000_000 and
                        abs(change) > 5 and
                        marketcap > 120_000_000
                    ):
                        opportunities.append(c)

                if not opportunities:
                    opportunities = coins

                selected = random.choice(opportunities)

                symbol = selected["symbol"]
                price = selected["quote"]["USD"]["price"]
                volume = selected["quote"]["USD"]["volume_24h"]
                change = selected["quote"]["USD"]["percent_change_24h"]

                price_display = f"{price:.6f}" if price < 1 else f"{price:,.2f}"

                # ===== حساب قوة الفرصة بشكل محسّن =====
                change_score = min(50, abs(change) * 2.5)         # تأثير التغير السعري
                volume_score = min(50, volume / 2_000_000)        # تأثير حجم التداول
                score = int(change_score + volume_score)          # مجموع النقاط من 0-100

                # تحديد نوع الإشارة
                if score > 85:
                    signal = "Whale Activity 🐋 "
                elif score > 70:
                    signal = "Smart Money 🚨 "
                elif score > 60:
                    signal = "Breakout 📈 "
                else:
                    signal = "Momentum 🔥 "

                # تحليل AI
                insight_ar = await ask_groq(
                    f"اكتب سطرين قصيرين يصفان الزخم السعري وحجم التداول لعملة {symbol} بسعر {price_display}. عربي فقط.",
                    lang="ar"
                )
                insight_en = await ask_groq(
                    f"Write two short lines describing price momentum and trading activity for {symbol} at {price_display}. English only.",
                    lang="en"
                )

                # تلميح للمجانيين (بدون كشف الاسم)
                hint_ar = "📈 تحليل سريع: تم رصد حركة قوية محتملة في السوق قد تشير إلى فرصة قادمة."
                hint_en = "📈 Quick Analysis: Strong market activity detected, potential opportunity ahead."

                # --- مؤقتاً لإرسال الرادار لمستخدم واحد (ID الأدمن) ---
                users = await pool.fetch("SELECT user_id, lang FROM users_info")  # ID الأدمن

                for row in users:
                    uid = row["user_id"]
                    lang = row["lang"] or "ar"

                    paid = await is_user_paid(pool, uid)

                    if paid:
                        # VIP - كامل التفاصيل
                        if lang == "ar":
                            text = (
                                f"🚨 <b>رادار السوق الذكي VIP</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"💎 العملة: #{symbol}\n"
                                f"💵 السعر الحالي: ${price_display}\n"
                                f"⚡ نوع الإشارة: {signal}\n"
                                f"📊 قوة الفرصة: {score}/100\n\n"
                                f"📈 الرؤية الفنية:\n{insight_ar}\n"
                                f"━━━━━━━━━━━━━━"
                            )
                        else:
                            text = (
                                f"🚨 <b>VIP Smart Market Radar</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"💎 Coin: #{symbol}\n"
                                f"💵 Price: ${price_display}\n"
                                f"⚡ Signal: {signal}\n"
                                f"📊 Opportunity Score: {score}/100\n\n"
                                f"📈 Technical Insight:\n{insight_en}\n"
                                f"━━━━━━━━━━━━━━"
                            )
                    else:
                        # مجاني - يظهر الإشارة والتحليل بدون كشف الاسم
                        if lang == "ar":
                            text = (
                                f"📡 <b>رادار الإنفجارات السعرية</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"💎 العملة: ••••• 🔒\n"
                                f"⚡ نوع الإشارة: {signal}\n"
                                f"💵 السعر الحالي: ${price_display}\n"
                                f"📊 قوة الفرصة: {score}/100\n\n"
                                f"{hint_ar}\n\n"
                                f"اشترك VIP لكشف اسم العملة والأهداف.\n"
                                f"━━━━━━━━━━━━━━"
                            )
                        else:
                            text = (
                                f"📡 <b>Price Explosion Radar</b>\n"
                                f"━━━━━━━━━━━━━━\n"
                                f"💎 Coin: ••••• 🔒\n"
                                f"⚡ Signal: {signal}\n"
                                f"💵 Current Price: ${price_display}\n"
                                f"📊 Opportunity Score: {score}/100\n\n"
                                f"{hint_en}\n\n"
                                f"Subscribe VIP to unlock the coin and targets.\n"
                                f"━━━━━━━━━━━━━━"
                            )

                    try:
                        await bot.send_message(
                            uid,
                            text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=None if paid else get_payment_kb(lang)
                        )
                        await asyncio.sleep(0.05)

                    except Exception as e:
                        print(f"Could not send radar to user {uid}: {e}")
                        continue

        except Exception as e:
            print(f"Radar Error: {e}")

        # الانتظار قبل الدورة القادمة (6 ساعات)
        await asyncio.sleep(84000)  # 6 ساعات # انتطار الدورة القادمة
async def daily_channel_post():
    # معرف القناة (تأكد من كتابة يوزر قناتك هنا)
    CHANNEL_ID = "@AiCryptoGPT" 
    
    while True:
        try:
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
            async with httpx.AsyncClient() as client:
                # نجلب أفضل 100 عملة لنختار منها
                res = await client.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest", 
                                     headers=headers, params={"limit": "100"})
                
                if res.status_code == 200:
                    selected_coin = random.choice(res.json()["data"])
                    symbol = selected_coin["symbol"]
                    price = selected_coin["quote"]["USD"]["price"]
                    price_display = f"{price:.4f}" if price > 1 else f"{price:.8f}"
                    
                    # توليد أرقام عشوائية للمؤشرات
                    vol_val = round(random.uniform(40, 150), 1)
                    trend_val = random.randint(40, 98)

                    # دالة لتحديد وصف القوة بناءً على الرقم
                    def get_power_desc(val):
                        if val < 50: return "ضعيف ⚠️"
                        elif 50 <= val < 60: return "متوسط ⚖️"
                        elif 60 <= val < 80: return "قوي 💪"
                        else: return "قوي جداً 🔥"

                    vol_desc = get_power_desc(vol_val)
                    trend_desc = get_power_desc(trend_val)

                    # صياغة المنشور بالتنسيق المطلوب بالضبط
                    post_text = (
                        f"━━━━━━━━━━━━\n"
                        f"🚨 **SMART MONEY ALERT**\n"
                        f"━━━━━━━━━━━━\n"
                        f"⏱️ الفريم: 15m\n"
                        f"💰 العملة: `{symbol}USDT`\n"
                        f"💵 السعر: `{price_display}`\n"
                        f"━━━━━━━━━━━━\n"
                        f"▪️ الحالة: ✅ إغلاق شمعة\n"
                        f"▪️ قوة الحجم: {vol_val}% ({vol_desc})\n"
                        f"▪️ قوة الاتجاه: {trend_val}% ({trend_desc})\n"
                        f"━━━━━━━━━━━━\n"
                        f"🔒 الاتجاه والأهداف مخفية\n"
                        f"━━━━━━━━━━━━\n"
                        f"👁️‍🗨️ لمعرفة الاتجاه + TP/SL\n"
                        f"اضغط هنا 👇"
                    )

                    # إعداد الزر لفتح البوت
                    bot_info = await bot.get_me()
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🖥 تحليل الاتجاه الآن", url=f"https://t.me/{bot_info.username}?start=analyze_{symbol}")
                    ]])

                    # إرسال المنشور للقناة
                    await bot.send_message(CHANNEL_ID, post_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                    print(f"✅ تم نشر توصية القناة لعملة {symbol}")

        except Exception as e:
            print(f"Error in channel post: {e}")
            
        # الانتظار 24 ساعة (86400 ثانية)
        await asyncio.sleep(21600) 


# --- نظام الـ AI ---
async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data
            )
            ans = res.json()["choices"][0]["message"]["content"]
            return ans
    except:
        return "⚠️ Error generating analysis"

# --- الأوامر ---
@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    pool = dp['db_pool']
    
    # 1. إجمالي المستخدمين
    total = await pool.fetchval("SELECT count(*) FROM users_info")
    # 2. إجمالي المشتركين VIP
    vips = await pool.fetchval("SELECT count(*) FROM paid_users")
    # 3. إجمالي الذين استخدموا التجربة المجانية (الموجودين في جدول trial_users)
    total_trials = await pool.fetchval("SELECT count(*) FROM trial_users")
    # 4. النشطين اليوم (الذين أرسلوا رسائل اليوم)
    active_today = await pool.fetchval("SELECT count(*) FROM users_info WHERE last_active = CURRENT_DATE")
    
    msg = (f"📊 **إحصائيات البوت المتقدمة:**\n"
           f"───────────────────\n"
           f"👥 **إجمالي القاعدة:** `{total}` مستخدم\n"
           f"🔥 **النشاط اليومي:** `{active_today}` مستخدم نشط\n"
           f"🎁 **مستخدمي التجربة:** `{total_trials}` شخص\n"
           f"💎 **المشتركين VIP:** `{vips}` مشترك")
    
    await m.answer(msg, parse_mode=ParseMode.MARKDOWN)

    
@dp.message(Command("admin"))
async def admin_cmd(m: types.Message):
    await m.answer(
        "📌 للتواصل مع الدعم، يرجى التواصل مع هذا الحساب:\n@AiCrAdmin\n\n"
        "📌 For support, contact:\n@AiCrAdmin"
    )
@dp.message(Command("clean"))
async def clean_db_cmd(m: types.Message):
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ لا تملك صلاحية استخدام هذا الأمر.")
    
    pool = dp['db_pool']
    async with pool.acquire() as conn:
        # حذف المستخدمين الذين ليس لديهم تجربة ولم يشتركوا
        deleted_count = await conn.execute("""
            DELETE FROM users_info
            WHERE user_id NOT IN (SELECT user_id FROM paid_users)
            AND user_id NOT IN (SELECT user_id FROM trial_users)
        """)
    
    await m.answer(f"✅ تم تنظيف قاعدة البيانات. عدد المستخدمين المحذوفين: {deleted_count}")
    
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    async with dp['db_pool'].acquire() as conn:
        await conn.execute("INSERT INTO users_info (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar"), InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]])
    await m.answer("👋 أهلاً بك، يرجى اختيار لغتك:\nWelcome, please choose your language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]

    try:
        async with dp['db_pool'].acquire() as conn:
            await conn.execute(
                "UPDATE users_info SET lang = $1 WHERE user_id = $2",
                lang,
                cb.from_user.id
            )
    except Exception as e:
        print(f"DB Error in set_lang: {e}")
        return await cb.answer("Server busy, try again...", show_alert=True)
    
    is_paid = await is_user_paid(dp['db_pool'], cb.from_user.id)
    has_tr = await has_trial(dp['db_pool'], cb.from_user.id)

    if is_paid:
        msg = "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل." if lang == "ar" else "✅ Welcome back! Your subscription is active.\nSend a coin symbol to analyze."
    elif has_tr:
        msg = "🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة للتحليل." if lang == "ar" else "🎁 You have one free trial! Send a coin symbol for analysis."
    else:
        msg = "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ لمرة واحدة." if lang == "ar" else "⚠️ Your free trial has ended. For full access, please subscribe for a one-time fee of 10 USDT or 500 ⭐."
    
    await cb.message.edit_text(msg, reply_markup=None if (is_paid or has_tr) else get_payment_kb(lang))

# --- التعامل مع الرموز ---
@dp.message(F.text)
async def handle_symbol(m: types.Message):
    if m.text.startswith('/'):
        return

    uid = m.from_user.id
    pool = dp['db_pool']

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users_info (user_id, last_active)
            VALUES ($1, CURRENT_DATE)
            ON CONFLICT (user_id)
            DO UPDATE SET last_active = CURRENT_DATE
        """, uid)
    # --------------------------------------------

    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    # ... باقي كود الدالة كما هو ...

    lang = user['lang'] if user else "ar"
    
    # 1. التحقق من الصلاحية
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await m.answer(
            "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ لمرة واحدة." if lang=="ar" 
            else "⚠️ Your free trial has ended. For full access, please subscribe for a one-time fee of 10 USDT or 500 ⭐.", 
            reply_markup=get_payment_kb(lang)
        )
    
    sym = m.text.strip().upper()
    
    # 2. إرسال رسالة الانتظار وتخزينها في متغير
    status_msg = await m.answer("⏳ جاري جلب السعر..." if lang=="ar" else "⏳ Fetching price...")

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={sym}", 
                headers={"X-CMC_PRO_API_KEY": CMC_KEY},
                timeout=10
            )
            data = res.json()

            # التحقق مما إذا كان الـ API قد أعاد خطأ أو لم يجد العملة
            if res.status_code != 200 or "data" not in data or sym not in data["data"]:
                raise ValueError("Symbol not found")

            price = data["data"][sym]["quote"]["USD"]["price"]
            
            # تخزين البيانات في الجلسة
            user_session_data[uid] = {"sym": sym, "price": price, "lang": lang}
            
            # 3. تحديث رسالة الانتظار بالخيارات الجديدة في حال النجاح
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="أسبوعي" if lang=="ar" else "Weekly", callback_data="tf_weekly"),
                InlineKeyboardButton(text="يومي" if lang=="ar" else "Daily", callback_data="tf_daily"),
                InlineKeyboardButton(text="4 ساعات" if lang=="ar" else "4H", callback_data="tf_4h")
            ]])
            
            await status_msg.edit_text(
                f"✅ العملة: {sym}\n💵 السعر: ${price:.6f}\n⏳ اختر الإطار الزمني للتحليل:" if lang=="ar" 
                else f"✅ Symbol: {sym}\n💵 Price: ${price:.6f}\n⏳ Select timeframe for analysis:", 
                reply_markup=kb
            )

    except Exception as e:
        # 4. في حال حدوث أي خطأ، يتم تعديل رسالة "جاري الجلب" لتوضيح الخطأ
        error_text = (
            f"❌ الرمز `{sym}` غير صحيح. تأكد من كتابة الرمز بشكل صحيح (مثل BTC أو ETH)." if lang=="ar" 
            else f"❌ Symbol `{sym}` is invalid. Please check the ticker (e.g., BTC, ETH)."
        )
        await status_msg.edit_text(error_text, parse_mode=ParseMode.MARKDOWN)
async def get_twelve_indicators(symbol: str, interval: str):

    url = "https://api.twelvedata.com/indicators"

    params = {
        "symbol": f"{symbol}/USDT",
        "interval": interval,
        "apikey": TWELVEDATA_API_KEY,
        "rsi": True,
        "macd": True,
        "bbands": True,
        "ema": True
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        data = r.json()

    rsi = float(data["rsi"]["value"])
    macd = float(data["macd"]["macd"])
    ema = float(data["ema"]["value"])

    bb_upper = float(data["bbands"]["upper_band"])
    bb_lower = float(data["bbands"]["lower_band"])

    return {
        "rsi": rsi,
        "macd": macd,
        "ema": ema,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower
    }
@dp.callback_query(F.data.startswith("tf_"))
async def run_analysis(cb: types.CallbackQuery):

    uid = cb.from_user.id
    pool = dp['db_pool']
    data = user_session_data.get(uid)

    if not data:
        return

    lang = data['lang']
    sym = data['sym']
    price = data['price']
    tf = cb.data.replace("tf_", "")

    # --- تحقق الاشتراك ---
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await cb.message.edit_text(
            "⚠️ انتهت تجربتك المجانية." if lang=="ar" else "⚠️ Trial ended.",
            reply_markup=get_payment_kb(lang)
        )

    try:
        await cb.message.edit_text(
            "🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing..."
        )
    except:
        pass

    # تحويل الفريم
    tf_map = {
        "4h": "4h",
        "daily": "1day",
        "weekly": "1week"
    }

    interval = tf_map.get(tf, "4h")

    try:

        indicators = await get_twelve_indicators(sym, interval)

        rsi = indicators["rsi"]
        macd = indicators["macd"]
        bb_upper = indicators["bb_upper"]
        bb_lower = indicators["bb_lower"]

        # --- حساب الاتجاه ---
        if rsi > 55 and macd > 0:
            trend = "Bullish"
        elif rsi < 45 and macd < 0:
            trend = "Bearish"
        else:
            trend = "Neutral"

        # --- دعم ومقاومة ---
        support = bb_lower
        resistance = bb_upper

        # --- حساب التارجت ---
        if trend == "Bullish":

            tp1 = price * 1.02
            tp2 = price * 1.04
            tp3 = price * 1.06
            sl = price * 0.97

        elif trend == "Bearish":

            tp1 = price * 0.98
            tp2 = price * 0.96
            tp3 = price * 0.94
            sl = price * 1.03

        else:

            tp1 = price * 1.01
            tp2 = price * 1.02
            tp3 = price * 1.03
            sl = price * 0.98

        # --- إنشاء البرومبت ---
        if lang == "ar":

            prompt = f"""
قم بتحليل عملة {sym}

السعر الحالي: {price:.6f}
الإطار الزمني: {tf}

البيانات الفنية:

RSI: {rsi}
MACD: {macd}

الدعم: {support}
المقاومة: {resistance}

TP1: {tp1}
TP2: {tp2}
TP3: {tp3}

Stop Loss: {sl}

اكتب التحليل بنفس التنسيق التالي تمامًا باستخدام HTML.

📊 <b>التحليل العام</b>
الاتجاه: صاعد او هابط

📉 <b>الدعم والمقاومة</b>
الدعم الأقرب:
المقاومة الأقرب:

🎯 <b>الأهداف السعرية</b>
TP1:
TP2:
TP3:

🛑 <b>وقف الخسارة</b>
Stop Loss:

📈 <b>تحليل المؤشرات</b>
RSI:
MACD:
Bollinger Bands:
Volume:
"""

        else:

            prompt = f"""
Analyze {sym}

Current price: {price}

Timeframe: {tf}

Indicators:

RSI: {rsi}
MACD: {macd}

Support: {support}
Resistance: {resistance}

Targets:

TP1 {tp1}
TP2 {tp2}
TP3 {tp3}

Stop Loss {sl}

Write the analysis EXACTLY in this HTML format.

📊 <b>Market Overview</b>
Trend:

📉 <b>Support & Resistance</b>
Nearest Support:
Nearest Resistance:

🎯 <b>Price Targets</b>
TP1:
TP2:
TP3:

🛑 <b>Stop Loss</b>
Stop Loss:

📈 <b>Indicator Analysis</b>
RSI:
MACD:
Bollinger Bands:
Volume:
"""

        result = await ask_groq(prompt, lang)

        await cb.message.answer(result, parse_mode=ParseMode.HTML)

    except Exception as e:

        print(f"Analysis error: {e}")

        await cb.message.answer(
            "❌ حدث خطأ أثناء التحليل" if lang=="ar"
            else "❌ Error generating analysis"
        )

    # --- استدعاء API داخل الدالة فقط ---
    res = await ask_groq(prompt, lang=lang)
    await cb.message.answer(res, parse_mode=ParseMode.HTML)
    
    if not (await is_user_paid(pool, uid)):
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        await cb.message.answer("⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ لمرة واحدة." if lang=="ar" else "⚠️ Your free trial has ended. For full access, please subscribe for a one-time fee of 10 USDT or 500 ⭐.", reply_markup=get_payment_kb(lang))

# --- الدفع الكريبتو ---
@dp.callback_query(F.data == "pay_crypto")
async def crypto_pay(cb: types.CallbackQuery):
    uid, pool = cb.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    
    await cb.message.edit_text(
        "⏳ يتم إنشاء رابط الدفع، يرجى الانتظار..." if lang == "ar" else "⏳ Generating payment link, please wait..."
    )

    invoice_url = await create_nowpayments_invoice(cb.from_user.id)
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 ادفع الآن" if lang=="ar" else "💳 Pay Now", url=invoice_url)]])
        msg = (
            "✅ تم إنشاء رابط الدفع.\nلإتمام الاشتراك، ادفع عبر الرابط أدناه.\n\nUSDT (BEP20)"
            if lang == "ar"
            else "✅ Payment link created.\nTo complete your subscription, pay via the link below.\n\nUSDT (BEP20)"
        )
        await cb.message.edit_text(msg, reply_markup=kb)
    else:
        await cb.message.edit_text(
            "❌ حدث خطأ. يرجى المحاولة مرة أخرى لاحقاً." if lang == "ar" else "❌ An error occurred. Please try again later."
        )

@dp.callback_query(F.data == "pay_stars")
async def stars_pay_call(cb: types.CallbackQuery):
    await cb.answer()
    uid, pool = cb.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    await send_stars_invoice(cb.from_user.id, lang=user['lang'] if user else "ar")

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery): await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message(F.successful_payment)
async def success_pay(m: types.Message):
    uid, pool = m.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", m.from_user.id)
    await m.answer(
        "✅ تم تأكيد الدفع بنجاح! شكراً لاشتراكك. يمكنك الآن استخدام البوت بشكل كامل."
        if lang == "ar" else
        "✅ Payment confirmed! Thank you for subscribing. You can now use the bot fully."
    )

# --- Webhook NOWPayments (IPN) ---
async def nowpayments_ipn(req: web.Request):
    try:
        data = await req.json()
        status = data.get("payment_status")
        order_id = data.get("order_id") 

        print(f"إشعار دفع جديد: الحالة {status} للمستخدم {order_id}")

        if status in ["finished", "confirmed"]:
            if order_id:
                user_id = int(order_id)
                pool = req.app['db_pool']
                
                async with pool.acquire() as conn:
                    # 1. تفعيل المستخدم في جدول الـ VIP
                    await conn.execute(
                        "INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                        user_id
                    )
                    
                    # 2. جلب لغة المستخدم من قاعدة البيانات
                    user_row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", user_id)
                    user_lang = user_row['lang'] if user_row and user_row['lang'] else "ar"

                # 3. تحديد نص الرسالة بناءً على اللغة
                if user_lang == "ar":
                    msg = "✅ تم تأكيد الدفع بنجاح! شكراً لاشتراكك. يمكنك الآن استخدام البوت بشكل كامل."
                else:
                    msg = "✅ Payment confirmed! Thank you for subscribing. You can now use the bot fully."

                # 4. إرسال الرسالة
                try:
                    await bot.send_message(user_id, msg)
                except Exception as e:
                    print(f"Could not send message to user {user_id}: {e}")
                
                print(f"🎉 User {user_id} upgraded to VIP ({user_lang})")

        return web.Response(text="ok")
    except Exception as e:
        print(f"IPN Error: {e}")
        return web.Response(text="error", status=500)


# --- السيرفر ---
async def handle_webhook(req: web.Request):
    try:
        data = await req.json()
        asyncio.create_task(dp.feed_update(bot, types.Update(**data)))
        return web.Response(text="ok")
    except Exception as e:
        print(f"Webhook error: {e}")
        return web.Response(text="error", status=500)

async def on_startup(app):
    pool = await asyncpg.create_pool(
    DATABASE_URL,
    min_size=0,                   # لا اتصالات مفتوحة وقت الخمول
    max_size=5,                   # عدد الاتصالات المتزامنة كافي للبوت المتوسط
    command_timeout=60,
    timeout=60,
    max_inactive_connection_lifetime=60  # اغلاق الاتصالات الغير مستخدمة
)

    app['db_pool'] = dp['db_pool'] = pool

    # 🔥 تأكد الاتصال اشتغل قبل استقبال المستخدمين
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        print("✅ Database connected successfully")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT)")
        # داخل دالة on_startup ابحث عن سطر إنشاء الجداول وأضف هذا:
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
        
        # ✅ إضافة المستخدمين المدفوعين مباشرة بدون تكرار
        initial_paid_users = {1811762192, 756814703}  # استخدام مجموعة لتجنب التكرار
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
    
    #asyncio.create_task(ai_opportunity_radar(pool))  # تم التعليق لإيقاف الرادار عند التشغيل
    #asyncio.create_task(daily_channel_post())
    await bot.set_webhook(f"{WEBHOOK_URL}/")

app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)