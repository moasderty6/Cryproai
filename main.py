import asyncio
import os
import re
import json
import hmac
import hashlib
import asyncpg
import httpx
import random
import time
import base64
import pandas as pd
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
GATE_API_KEY = "a3f6a57b42f6106011e6890049e57b2e"
GATE_API_SECRET = "1ac18e0a690ce782f6854137908a6b16eb910cf02f5b95fa3c43b670758f79bc"
GATE_BASE = "https://api.gateio.ws/api/v4/spot/candlesticks"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
SECRET_TOKEN = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:20]
PORT = int(os.getenv("PORT", 10000))

NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_USER_ID = 6172153716

GROQ_MODEL = "llama-3.3-70b-versatile"

# --- إعداد البوت ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
user_session_data = {}

# --- وظائف قاعدة البيانات ---
async def extend_user_subscription(pool, user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO paid_users (user_id, expiry_date) 
            VALUES ($1, CURRENT_TIMESTAMP + INTERVAL '30 days') 
            ON CONFLICT (user_id) DO UPDATE 
            SET expiry_date = GREATEST(COALESCE(paid_users.expiry_date, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP) + INTERVAL '30 days'
        """, user_id)

async def is_user_paid(pool, user_id: int):
    query = """
        SELECT 1 FROM paid_users 
        WHERE user_id = $1 AND (expiry_date IS NULL OR expiry_date > CURRENT_TIMESTAMP)
    """
    res = await pool.fetchval(query, user_id)
    return bool(res)


async def has_trial(pool, user_id: int):
    res = await pool.fetchval("SELECT 1 FROM trial_users WHERE user_id = $1", user_id)
    return not bool(res)
def format_price(price):
    if price is None:
        return "0.0"
    price = float(price)
    
    if price >= 1:
        return f"{price:,.2f}"      # للعملات مثل BTC (65,000.00)
    elif price >= 0.001:
        return f"{price:.4f}"       # للعملات مثل ADA (0.4500)
    else:
        # للعملات الصفرية مثل SHIB، نعرض حتى 10 أرقام ونحذف الأصفار الزائدة
        return f"{price:.10f}".rstrip('0').rstrip('.')

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
    prices = [LabeledPrice(label="اشتراك البوت بـ 500 شهرياً ⭐" if lang=="ar" else "Subscribe Now with 500 ⭐ Monthly", amount=500)]
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
            [InlineKeyboardButton(text="💎 اشترك الآن (10 USDT شهرياً)", callback_data="pay_crypto")],
            [InlineKeyboardButton(text="⭐ اشترك الآن بـ 500 نجمة شهرياً", callback_data="pay_stars")],
            [InlineKeyboardButton(text="🎁 احصل على شهر مجاني (دعوة أصدقاء)", callback_data="pay_invite")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Subscribe Now (10 USDT Monthly)", callback_data="pay_crypto")],
        [InlineKeyboardButton(text="⭐ Subscribe Now with 500 Stars Monthly", callback_data="pay_stars")],
        [InlineKeyboardButton(text="🎁 Get a Free Month (Invite Friends)", callback_data="pay_invite")]
    ])

# --- رادار الفرص الذكي ---
# --- دوال الرادار المساعدة (ضعها فوق دالة الرادار) ---
async def get_btc_trend(client):
    """جلب حالة البيتكوين لمعرفة ترند السوق العام"""
    try:
        res = await client.get("https://api.gateio.ws/api/v4/spot/candlesticks", params={
            "currency_pair": "BTC_USDT", "interval": "1d", "limit": 25
        })
        if res.status_code == 200:
            data = res.json()
            close_prices = [float(c[2]) for c in data]
            sma20 = sum(close_prices[-20:]) / 20
            # هل البيتكوين فوق متوسط 20 يوم؟
            return close_prices[-1] > sma20
    except:
        pass
    return True # افتراضي في حال فشل الـ API

async def get_orderbook_pressure(client, symbol):
    """حساب ضغط الشراء من دفتر الأوامر (حيتان مخفية)"""
    try:
        res = await client.get("https://api.gateio.ws/api/v4/spot/order_book", params={
            "currency_pair": f"{symbol}_USDT", "limit": 50
        })
        if res.status_code == 200:
            data = res.json()
            bids = sum([float(b[1]) for b in data.get("bids", [])]) # قوة الدعم (الشراء)
            asks = sum([float(a[1]) for a in data.get("asks", [])]) # قوة المقاومة (البيع)
            if asks == 0: return 1.0
            return bids / asks
    except:
        pass
    return 1.0


# --- الرادار الخارق المعدل ---
async def ai_opportunity_radar(pool):
    try:
        headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
        STABLE_COINS = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD","USDP","GUSD","USDD","LUSD"}

        async with httpx.AsyncClient(timeout=25) as client:
            
            # 1. تحديد بوصلة السوق (Bitcoin Trend)
            is_btc_bullish = await get_btc_trend(client)

            # 2. جلب قائمة العملات
            res = await client.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                headers=headers,
                params={"limit": "250"}
            )
            coins = res.json()["data"]

            best_coin = None
            best_score = 0
            best_meta = None

            for c in coins:
                symbol = c["symbol"]
                if symbol in STABLE_COINS: continue

                volume = c["quote"]["USD"]["volume_24h"]
                marketcap = c["quote"]["USD"]["market_cap"]
                change24 = c["quote"]["USD"]["percent_change_24h"]

                # 🛑 الفلترة الذكية (Hard Filters)
                if volume < 5_000_000 or marketcap < 10_000_000: continue
                if abs(change24) < 0.4: continue

                price = c["quote"]["USD"]["price"]

                # 📊 جلب بيانات 1H كفريم أساسي (نفس نظامك القديم)
                candles = await get_candles_gate(f"{symbol}_USDT", "1h", limit=120)
                if not candles: continue

                df = pd.DataFrame(candles)
                df = df.iloc[:, :6]
                df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                for col in ["close","high","low","open","volume"]:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

                last_rsi, last_macd_diff, last_bb, last_vol, high, low = compute_indicators(candles)

                ema50 = df["close"].ewm(span=50).mean()
                ema200 = df["close"].ewm(span=200).mean()
                trend_up = df["close"].iloc[-1] > ema200.iloc[-1]

                avg_vol = df["volume"].rolling(20).mean()
                volume_spike = df["volume"].iloc[-1] > (avg_vol.iloc[-1] * 2.5)

                range_20 = df["high"].rolling(20).max() - df["low"].rolling(20).min()
                squeeze = range_20.iloc[-1] < (price * 0.07)
                recent_high = df["high"].rolling(20).max().iloc[-2]
                breakout = price > recent_high

                fake_move = (high - low) / price > 0.35
                if fake_move: continue

                # -----------------
                # 🎯 1H BASE SCORING
                # -----------------
                base_score = 0
                if 40 <= last_rsi <= 55: base_score += 15
                elif 35 <= last_rsi < 40: base_score += 10
                
                if last_macd_diff > 0:
                    base_score += 15
                    if last_macd_diff > 0.002: base_score += 5
                
                if trend_up: base_score += 15
                
                if volume_spike:
                    base_score += 20
                    if df["volume"].iloc[-1] > (avg_vol.iloc[-1] * 3): base_score += 5
                
                if squeeze: base_score += 10
                if breakout: base_score += 15
                if marketcap < 150_000_000: base_score += 5

                if not volume_spike: base_score -= 15
                if last_rsi < 35: base_score -= 10
                if not trend_up: base_score -= 15

                body = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
                if body < (price * 0.003): base_score -= 5

                # ----------------------------------------------------
                # 🚀 SUPER RADAR: Multi-Timeframe & Orderbook Funnel
                # ----------------------------------------------------
                # نفحص بعمق فقط العملات التي لديها أساس قوي (توفر استهلاك الـ API)
                score = base_score
                if score >= 45: 
                    # 1. تأثير البيتكوين (الترند العام)
                    if is_btc_bullish: score += 10
                    else: score -= 5 # عقوبة إذا كان السوق ينزف

                    # 2. فريم 15 دقيقة (نقطة الدخول والانفجار اللحظي)
                    candles_15m = await get_candles_gate(f"{symbol}_USDT", "15m", limit=30)
                    if candles_15m:
                        df_15m = pd.DataFrame(candles_15m).iloc[:, :6]
                        df_15m.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                        for col in ["close","volume","open"]: df_15m[col] = pd.to_numeric(df_15m[col], errors='coerce')
                        
                        vol_15m_avg = df_15m["volume"].rolling(10).mean().iloc[-1]
                        # فوليوم انفجاري لحظي الآن
                        if df_15m["volume"].iloc[-1] > (vol_15m_avg * 2): score += 15
                        # شمعة 15 دقيقة خضراء صاعدة
                        if df_15m["close"].iloc[-1] > df_15m["open"].iloc[-1]: score += 5

                    # 3. فريم 4 ساعات (التأكيد الكلي)
                    candles_4h = await get_candles_gate(f"{symbol}_USDT", "4h", limit=60)
                    if candles_4h:
                        df_4h = pd.DataFrame(candles_4h).iloc[:, :6]
                        df_4h.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                        df_4h["close"] = pd.to_numeric(df_4h["close"], errors='coerce')
                        ema50_4h = df_4h["close"].ewm(span=50).mean().iloc[-1]
                        # ترند الماكرو إيجابي
                        if df_4h["close"].iloc[-1] > ema50_4h: score += 10

                    # 4. دفتر الأوامر (السيولة المخفية)
                    buy_pressure = await get_orderbook_pressure(client, symbol)
                    if buy_pressure > 2.0: score += 20 # حيتان تشتري بجنون
                    elif buy_pressure > 1.3: score += 10

                # -----------------
                # 🔒 Clamp & Elite Filter
                # -----------------
                score = max(0, min(score, 100))

                if score >= 90:
                    if not (volume_spike and breakout and trend_up):
                        score = 85

                # -----------------
                # اختيار الأفضل
                # -----------------
                if score > best_score:
                    best_score = score
                    best_coin = c
                    best_meta = {
                        "symbol": symbol,
                        "price": price
                    }

                await asyncio.sleep(0.15)

            # -----------------
            # فلترة نهائية وتجهيز الإرسال
            # -----------------
            if not best_coin or best_score < 60:
                print("Radar: No strong setup.")
                return

            if best_score >= 90:
                signal = "💣 SMART MONEY"
            elif best_score >= 80:
                signal = "🚀 STRONG BREAKOUT"
            elif best_score >= 70:
                signal = "🎯 HIGH PROBABILITY"
            else:
                signal = "⚡ EARLY SETUP"

            symbol = best_meta["symbol"]
            price = best_meta["price"]

            insight_ar = await ask_groq(
                f"اشرح باختصار سبب احتمال صعود {symbol} بسبب الفوليوم والتجميع والاختراق. سطرين فقط.",
                lang="ar"
            )

            insight_en = await ask_groq(
                f"Explain briefly why {symbol} may pump based on volume, accumulation and breakout. 2 lines.",
                lang="en"
            )

            # --- إرسال الإشعارات للمستخدمين (نفس نصوصك تماماً) ---
            # --- إرسال الإشعارات للمستخدمين ---
            users = await pool.fetch("SELECT user_id, lang FROM users_info WHERE user_id IN ($1, $2, $3)", ADMIN_USER_ID, 8241472209, 565965404)

            for row in users:
                uid = row["user_id"]
                lang = row["lang"] or "ar"
                paid = await is_user_paid(pool, uid)

                # ---------- VIP ----------
                if paid:
                    if lang == "ar":
                        text = (
                            f"🚨 <b>رادار السوق الذكي VIP</b>\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"💎 العملة: #{symbol}\n"
                            f"💵 السعر: ${format_price(price)}\n"
                            f"⚡ الإشارة: {signal}\n"
                            f"📊 السكور: {best_score}/100\n\n"
                            f"📈 التحليل:\n{insight_ar}\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"📌 نتائج تحليلات البوت: @N_Results"
                        )
                    else:
                        text = (
                            f"🚨 <b>VIP Smart Market Radar</b>\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"💎 Coin: #{symbol}\n"
                            f"💵 Price: ${format_price(price)}\n"
                            f"⚡ Signal: {signal}\n"
                            f"📊 Score: {best_score}/100\n\n"
                            f"📈 Insight:\n{insight_en}\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"📌 Bot Results: @N_Results"
                        )

                # ---------- FREE ----------
                else:
                    if lang == "ar":
                        text = (
                            f"📡 <b>رادار الإنفجارات السعرية</b>\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"💎 العملة: •••• 🔒\n"
                            f"⚡ الإشارة: {signal}\n"
                            f"📊 السكور: {best_score}/100\n\n"
                            f"🔥 تم رصد تجميع قوي + فوليوم غير طبيعي\n"
                            f"🚀 احتمال انفجار سعري قريب\n\n"
                            f"اشترك VIP لكشف اسم العملة والأهداف\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"📌 نتائج تحليلات البوت: @N_Results"
                        )
                    else:
                        text = (
                            f"📡 <b>Price Explosion Radar</b>\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"💎 Coin: •••• 🔒\n"
                            f"⚡ Signal: {signal}\n"
                            f"📊 Score: {best_score}/100\n\n"
                            f"🔥 Strong accumulation + abnormal volume\n"
                            f"🚀 Possible breakout soon\n\n"
                            f"Subscribe VIP to unlock the coin and exact targets.\n"
                            f"━━━━━━━━━━━━━━\n"
                            f"📌 Bot Results: @N_Results"
                        )

                try:
                    await bot.send_message(
                        uid,
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=None if paid else get_payment_kb(lang)
                    )
                except:
                    continue

    except Exception as e:
        print(f"Radar Error: {e}")

        # تم تصحيح وقت الانتظار ليكون 6 ساعات بالضبط (6 * 60 * 60)
  # 6 ساعات # انتطار الدورة القادمة
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
# --- النسخة الجديدة والمستقرة ---
async def ask_groq(prompt, lang="ar"):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    data = {
        "model": GROQ_MODEL, 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,  # 👈 هاد اللي بيمنع الـ AI من تغيير رأيه (برودة وهدوء)
        "max_tokens": 800    # 👈 زيادة المساحة عشان التحليل ما ينقطع
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=data
            )
            # إضافة سطر التأكد من الاستجابة لضمان عدم وجود أخطاء صامتة
            res.raise_for_status() 
            
            ans = res.json()["choices"][0]["message"]["content"]
            return ans
    except Exception as e:
        print(f"Error in ask_groq: {e}")
        return "⚠️ Error generating analysis"

# --- الأوامر ---
@dp.message(Command("convert_old"))
async def convert_old_users_cmd(m: types.Message):
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ هذا الأمر للأدمن فقط")
    
    pool = dp['db_pool']
    
    # 1. جلب المستخدمين القدامى (الذين ليس لديهم تاريخ انتهاء)
    old_users = await pool.fetch("SELECT user_id FROM paid_users WHERE expiry_date IS NULL")
    
    if not old_users:
        return await m.answer("⚠️ لا يوجد مشتركون بنظام مدى الحياة لتحويلهم.")

    async with pool.acquire() as conn:
        # 2. تحديث قاعدة البيانات وإعطائهم 30 يوم من الآن
        await conn.execute("""
            UPDATE paid_users 
            SET expiry_date = CURRENT_TIMESTAMP + INTERVAL '30 days'
            WHERE expiry_date IS NULL
        """)

    # 3. صياغة الرسائل التوضيحية
    msg_ar = (
        "📢 <b>تحديث هام بخصوص اشتراكك</b>\n"
        "━━━━━━━━━━━━━━\n\n"
        "مرحباً بك عميلنا العزيز،\n"
        "لضمان استمرارية عمل البوت بأعلى كفاءة وتغطية التكاليف التشغيلية (لخوادم الذكاء الاصطناعي وجلب البيانات)، تم تحديث نظام الاشتراك ليكون <b>شهرياً</b>.\n\n"
        "🎁 <b>تقديراً لدعمك المبكر لنا</b>:\n"
        "تم منحك فترة مجانية لمدة <b>30 يوماً</b> تبدأ من هذه اللحظة. سيُطلب منك تجديد الاشتراك الشهري بعد انتهاء هذا الشهر.\n\n"
        "شكراً لتفهمك ودعمك المستمر لنجاح بوتنا! 💎"
    )
    
    msg_en = (
        "📢 <b>Important Update Regarding Your Subscription</b>\n"
        "━━━━━━━━━━━━━━\n\n"
        "Dear valued user,\n"
        "To ensure the bot continues operating at maximum efficiency and to cover operational costs (AI and data servers), our subscription model has been updated to a <b>monthly</b> plan.\n\n"
        "🎁 <b>As a token of appreciation for your early support</b>:\n"
        "Your account has been granted a free <b>30-day</b> period starting today. Monthly renewals will apply after this month ends.\n\n"
        "Thank you for your understanding and continuous support for NaiF CHarT! 💎"
    )

    await m.answer(f"⏳ جاري تحويل {len(old_users)} اشتراك وإرسال الرسائل التوضيحية...")
    sent_count = 0

    # 4. إرسال الرسالة لكل مستخدم قديم
    for row in old_users:
        uid = row["user_id"]
        
        # معرفة لغة المستخدم
        user_info = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
        lang = user_info['lang'] if user_info and user_info['lang'] else "ar"
        
        text = msg_ar if lang == "ar" else msg_en
        
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            sent_count += 1
            await asyncio.sleep(0.05) # أمان لتجنب حظر التليجرام بسبب الإرسال السريع
        except Exception as e:
            print(f"Failed to send to {uid}: {e}")
            continue

    await m.answer(f"✅ تمت العملية بنجاح!\n👥 تم تحويل اشتراك {len(old_users)} مستخدم.\n📨 تم إيصال الرسالة إلى {sent_count} منهم.")

@dp.message(Command("sendphoto"))
async def send_photo_to_trials(m: types.Message):
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ هذا الأمر للأدمن فقط")

    pool = dp['db_pool']

    # جلب مستخدمي التجربة فقط واستثناء VIP
    users = await pool.fetch("""
    SELECT u.user_id
    FROM users_info u
    JOIN trial_users t ON u.user_id = t.user_id
    WHERE u.user_id NOT IN (SELECT user_id FROM paid_users)
    """)

    # تأكد أن الأدمن أرسل صورة مع الأمر
    if not m.photo:
        return await m.answer("❌ أرسل الأمر مع صورة")

    photo = m.photo[-1].file_id  # أعلى جودة
    caption = m.caption or ""

    sent = 0

    for row in users:
        try:
            await bot.send_photo(
                chat_id=row["user_id"],
                photo=photo,
                caption=caption
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            continue

    await m.answer(f"✅ تم إرسال الصورة إلى {sent} مستخدم تجربة")
@dp.message(Command("send"))
async def broadcast_message(m: types.Message):
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ هذا الأمر مخصص للأدمن فقط.")

    # استخراج نص الرسالة بعد الأمر
    text = m.text.replace("/send", "").strip()

    if not text:
        return await m.answer("⚠️ اكتب الرسالة بعد الأمر.\n\nمثال:\n/send مرحباً بالجميع")

    pool = dp['db_pool']

    users = await pool.fetch("SELECT user_id FROM users_info")

    sent = 0
    failed = 0

    await m.answer(f"🚀 جاري الإرسال إلى {len(users)} مستخدم...")

    for row in users:
        try:
            await bot.send_message(row["user_id"], text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1

    await m.answer(
        f"✅ انتهى الإرسال\n\n"
        f"📨 تم الإرسال: {sent}\n"
        f"❌ فشل: {failed}"
    )
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
@dp.message(Command("results"))
async def admin_cmd(m: types.Message):
    await m.answer(
        "📌 قناة النتائج، لمشاهدة احدث نتائج البوت:\n@N_Results\n\n"
        "📌 For bor results, in channel:\n@N_Results"
    )
@dp.message(Command("radar"))
async def radar_cmd(m: types.Message):

    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ هذا الأمر للأدمن فقط")

    await m.answer("📡 جاري تشغيل الرادار...")

    asyncio.create_task(ai_opportunity_radar(dp['db_pool']))
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
    # استخراج رقم الشخص الذي أرسل الدعوة
    args = m.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
        if referrer_id == m.from_user.id: 
            referrer_id = None # منع المستخدم من دعوة نفسه

    async with dp['db_pool'].acquire() as conn:
        # تسجيل المستخدم مع حفظ رقم المستدعي
        await conn.execute("""
            INSERT INTO users_info (user_id, invited_by) 
            VALUES ($1, $2) 
            ON CONFLICT (user_id) DO NOTHING
        """, m.from_user.id, referrer_id)
        
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
        msg = "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ شهرياً." if lang == "ar" else "⚠️ Your free trial has ended. For full access, please subscribe for a Monthly fee of 10 USDT or 500 ⭐."
    
    await cb.message.edit_text(msg, reply_markup=None if (is_paid or has_tr) else get_payment_kb(lang))

# --- التعامل مع الرموز ---
# --- التعامل مع الرموز ---

async def search_dex_coin(symbol: str):
    """تبحث عن العملة في DexScreener بناءً على التوثيق الرسمي"""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url)
            data = res.json()
            if data.get("pairs") and len(data["pairs"]) > 0:
                best_pair = data["pairs"][0]
                return {
                    "network": best_pair["chainId"],
                    "pool_address": best_pair["pairAddress"],
                    "price": float(best_pair.get("priceUsd", 0)),
                    "volume_24h": float(best_pair.get("volume", {}).get("h24", 0)),
                    "base_symbol": best_pair.get("baseToken", {}).get("symbol", symbol)
                }
    except Exception as e:
        print(f"DexScreener Error: {e}")
    return None

async def get_candles_dex(network: str, pool_address: str, interval: str, limit: int = 120):
    """تجلب الشموع من GeckoTerminal وتعيد ترتيبها لتطابق تنسيق Gate.io"""
    if interval == "1d" or interval == "1w":
        timeframe = "day"
        aggregate = 1
    else:
        timeframe = "hour"
        aggregate = 4

    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json()
                ohlcv_list = data["data"]["attributes"]["ohlcv_list"]
                formatted_candles = []
                for candle in ohlcv_list:
                    t, o, h, l, c, v = candle
                    formatted_candles.append([t, v, c, h, l, o])
                return formatted_candles[::-1] 
    except Exception as e:
        print(f"GeckoTerminal Error: {e}")
    return None

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

    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await m.answer(
            "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 1500 ⭐ شهرياً." if lang=="ar" 
            else "⚠️ Your free trial has ended. For full access, please subscribe for a Monthly fee of 10 USDT or 500 ⭐.", 
            reply_markup=get_payment_kb(lang)
        )
    
    user_sym = m.text.strip().upper()
    symbol_map = {"XAU": "PAXG", "GOLD": "PAXG"}
    sym = symbol_map.get(user_sym, user_sym)
    
    status_msg = await m.answer("⏳ جاري جلب السعر..." if lang=="ar" else "⏳ Fetching price...")

    try:
        async with httpx.AsyncClient() as client:
            pair = f"{sym}_USDT"
            res_gate = await client.get(
                "https://api.gateio.ws/api/v4/spot/tickers",
                params={"currency_pair": pair},
                timeout=10
            )
            data_gate = res_gate.json()

            if isinstance(data_gate, list) and len(data_gate) > 0 and "last" in data_gate[0]:
                price = float(data_gate[0]["last"])
                
                # ✅ التعديل هنا: نأخذ الفوليوم من Gate.io كقيمة افتراضية (quote_volume يمثل الفوليوم بـ USDT)
                volume_24h = float(data_gate[0].get("quote_volume", 0))

                # محاولة جلب الفوليوم الأدق من CMC
                try:
                    res_cmc = await client.get(
                        f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={sym}",
                        headers={"X-CMC_PRO_API_KEY": CMC_KEY},
                        timeout=5
                    )
                    data_cmc = res_cmc.json()
                    if res_cmc.status_code == 200 and sym in data_cmc.get("data", {}):
                        # إذا نجح، استبدل فوليوم Gate.io بفوليوم CMC الكلي
                        volume_24h = float(data_cmc["data"][sym]["quote"]["USD"]["volume_24h"])
                except:
                    pass # إذا فشل CMC، سيبقى الفوليوم محتفظاً بقيمة Gate.io ولن يصبح 0

                user_session_data[uid] = {
                    "sym": sym, "price": price, "volume_24h": volume_24h, 
                    "lang": lang, "is_dex": False
                }
            else:
                raise ValueError("Symbol not found in Gate.io")


    except Exception:
        dex_data = await search_dex_coin(sym)
        if dex_data:
            sym = dex_data["base_symbol"]
            price = dex_data["price"]
            user_session_data[uid] = {
                "sym": sym, "price": price, "volume_24h": dex_data["volume_24h"], 
                "lang": lang, "is_dex": True, 
                "network": dex_data["network"], "pool_address": dex_data["pool_address"]
            }
        else:
            error_text = (
                f"❌ الرمز `{sym}` غير صحيح أو غير متوفر في منصات التداول." if lang=="ar" 
                else f"❌ Symbol `{sym}` is invalid or not found on exchanges."
            )
            return await status_msg.edit_text(error_text, parse_mode=ParseMode.MARKDOWN)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="أسبوعي" if lang=="ar" else "Weekly", callback_data="tf_weekly"),
        InlineKeyboardButton(text="يومي" if lang=="ar" else "Daily", callback_data="tf_daily"),
        InlineKeyboardButton(text="4 ساعات" if lang=="ar" else "4H", callback_data="tf_4h")
    ]])
    
    coin_type = "🌐 DEX" if user_session_data[uid].get("is_dex") else "🏦 CEX"
    
    await status_msg.edit_text(
        f"✅ العملة: {sym} ({coin_type})\n💵 السعر: ${format_price(price)}\n⏳ اختر الإطار الزمني للتحليل:" if lang=="ar" 
        else f"✅ Symbol: {sym} ({coin_type})\n💵 Price: ${format_price(price)}\n⏳ Select timeframe for analysis:",
        reply_markup=kb
    )

# --- توقيع الهيدر للـ API ---
def gate_sign(params: dict):
    return {}

# --- جلب الشموع ---
async def get_candles_gate(symbol: str, interval: str, limit: int = 250):
    async with httpx.AsyncClient() as client:
        res = await client.get(GATE_BASE, params={
            "currency_pair": symbol,
            "interval": interval,
            "limit": limit
        })
        if res.status_code == 200:
            return res.json()
        return None

# --- حساب المؤشرات ---
def compute_indicators(candles):
    df = pd.DataFrame(candles)
    df = df.iloc[:, :6] 
    df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
    
    for col in ["close", "high", "low", "open", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    last_rsi = rsi_val.iloc[-1]

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    macd_val = ema12 - ema26
    signal = macd_val.ewm(span=9, adjust=False).mean()
    last_macd_diff = macd_val.iloc[-1] - signal.iloc[-1]

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std(ddof=0) 
    upper_band = sma20 + 2*std20
    lower_band = sma20 - 2*std20
    last_bb = (df["close"].iloc[-1], lower_band.iloc[-1], upper_band.iloc[-1])

    last_vol = df["volume"].iloc[-1]
    recent = df.tail(20)
    high_price = recent["high"].max()
    low_price = recent["low"].min()

    return last_rsi, last_macd_diff, last_bb, last_vol, high_price, low_price

# --- دالة التحليل المعدلة ---
@dp.callback_query(F.data.startswith("tf_"))
async def run_analysis(cb: types.CallbackQuery):
    uid, pool = cb.from_user.id, dp['db_pool']
    data = user_session_data.get(uid)
    
    if not data:
        return await cb.answer("⚠️ انتهت الجلسة، يرجى إرسال الرمز من جديد.", show_alert=True)

    lang = data.get('lang', 'ar')
    sym = data.get('sym')
    price = data.get('price')
    volume_24h = data.get('volume_24h', 0)
    tf = cb.data.replace("tf_", "")
    
    if not (await is_user_paid(pool, uid)) and not (await has_trial(pool, uid)):
        return await cb.message.edit_text(
            "⚠️ انتهت تجربتك المجانية." if lang=="ar" else "⚠️ Trial ended.",
            reply_markup=get_payment_kb(lang)
        )

    await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")

    clean_sym = sym.replace("USDT", "").strip().upper()
    is_dex = data.get('is_dex', False)
    
    if is_dex:
        network = data.get('network')
        pool_address = data.get('pool_address')
        gate_interval = {"4h":"4h", "daily":"1d", "weekly":"1w"}.get(tf, "4h")
        candles = await get_candles_dex(network, pool_address, gate_interval, limit=120)
    else:
        gate_interval = {"4h":"4h", "daily":"1d", "weekly":"1w"}.get(tf, "4h")
        candles = await get_candles_gate(f"{clean_sym}_USDT", gate_interval, limit=250)

    if candles:
        last_rsi, last_macd, last_bb, last_vol, high, low = compute_indicators(candles)
    else:
        last_rsi, last_macd, last_bb, last_vol, high, low = 50.0, 0.0, (price, price*0.95, price*1.05), 0.0, price*1.05, price*0.95
        
    price_fmt = format_price(price)
    low_fmt = format_price(low)
    high_fmt = format_price(high)
    bb0_fmt = format_price(last_bb[0])
    bb1_fmt = format_price(last_bb[1])
    bb2_fmt = format_price(last_bb[2])
    macd_fmt = format_price(last_macd) if last_macd is not None else "0.0"
    safe_rsi = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"
    vol24_fmt = format_price(volume_24h)

    if lang == "ar":
        prompt = f"""
أنت محلل فني خبير في شركة "NaiF CHarT". حلل عملة {clean_sym} بناءً على البيانات التالية:
السعر الحالي: {price_fmt}$ | الإطار: {tf} | RSI: {safe_rsi} | MACD: {"صاعد" if (last_macd or 0)>0 else "هابط"}
البولينجر: السعر {bb0_fmt} (نطاق {bb1_fmt} - {bb2_fmt}) | الفوليوم: {vol24_fmt}

⚠️ الالتزام التام بهذا التنسيق (استخدم وسوم HTML فقط):
⚠️ قواعد صارمة:

إذا كان الاتجاه "صاعد":
- يجب أن تكون TP1 و TP2 و TP3 أعلى من السعر الحالي

إذا كان الاتجاه "هابط":
- يجب أن تكون TP1 و TP2 و TP3 أقل من السعر الحالي

📊 <b>التحليل العام</b>
الاتجاه: (اكتب صاعد أو هابط)

📉 <b>الدعم والمقاومة</b>
الدعم الأقرب: {low_fmt} دولار
المقاومة الأقرب: {high_fmt} دولار

🎯 <b>الأهداف السعرية</b>
TP1: (ضع رقم منطقي)
TP2: (ضع رقم منطقي)
TP3: (ضع رقم منطقي)

🛑 <b>وقف الخسارة</b>
Stop Loss: (ضع رقم منطقي)

📈 <b>تحليل المؤشرات</b>
- RSI: {safe_rsi} (اكتب القيمةواشرح باختصار شديد سطر واحد)
- MACD: {macd_fmt} (اكتب القيمة واشرح باختصار شديد سطر واحد)
- Bollinger Bands: (اشرح باختصار شديد سطر واحد)
- Volume: {vol24_fmt} (اكتب القيمة واشرح باختصار شديد سطر واحد)

**ملاحظة: لا تكتب مقدمات ولا جرايد، خليك محدد ومختصر ومرتب.**
"""
    else:
        prompt = f"""
You are an expert Technical Analyst at "NaiF CHarT". Analyze {clean_sym} based on:
Price: {price_fmt}$ | Timeframe: {tf} | RSI: {safe_rsi} | MACD: {"Bullish" if (last_macd or 0)>0 else "Bearish"}
Bollinger: {bb0_fmt} (Range {bb1_fmt}-{bb2_fmt}) | Volume: {vol24_fmt}

⚠️ Strictly follow this HTML format:
Strict rule:

If Trend = Bullish
TP targets MUST be above current price.

If Trend = Bearish
TP targets MUST be below current price.

<b>📊 Market Overview</b>
Trend: (Bullish/Bearish)

<b>📉 Support & Resistance</b>
Nearest Support: <code>{low_fmt}</code> $Nearest Resistance: <code>{high_fmt}</code>$

<b>🎯 Price Targets</b>
TP1: <code>(Price)</code>
TP2: <code>(Price)</code>
TP3: <code>(Price)</code>

<b>🛑 Stop Loss</b>
Stop Loss: <code>(Price)</code>

<b>📈 Indicator Analysis</b>
• RSI: {safe_rsi} (value and One short sentence)
• MACD: {macd_fmt} (value and One short sentence)
• Bollinger Bands: (One short sentence)
• Volume: {vol24_fmt} (value and One short sentence)

<b>Note: No intro/outro, strictly follow the headers above.</b>
"""

    res = await ask_groq(prompt, lang=lang)
    await cb.message.answer(res, parse_mode=ParseMode.HTML)
    
    if not (await is_user_paid(pool, uid)):
        async with pool.acquire() as conn:
            # إضافة المستخدم لجدول التجربة المجانية ومعرفة هل هو إدخال جديد
            res = await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
            
            # إذا كان المستخدم يستهلك التجربة لأول مرة
                        # إذا كان المستخدم يستهلك التجربة لأول مرة
            if "INSERT 0 1" in res:
                # نبحث هل هناك شخص قام بدعوته؟
                inviter = await conn.fetchrow("SELECT invited_by FROM users_info WHERE user_id = $1", uid)
                if inviter and inviter['invited_by']:
                    inviter_id = inviter['invited_by']
                    
                    # زيادة نقاط المستدعي
                    await conn.execute("UPDATE users_info SET ref_count = COALESCE(ref_count, 0) + 1 WHERE user_id = $1", inviter_id)
                    current_count = await conn.fetchval("SELECT ref_count FROM users_info WHERE user_id = $1", inviter_id)
                    
                    # 🌐 جلب لغة المستدعي لتحديد لغة الرسالة
                    inviter_lang_row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", inviter_id)
                    inv_lang = inviter_lang_row['lang'] if inviter_lang_row and inviter_lang_row['lang'] else "ar"
                    
                    try:
                        # إذا لم يصل للـ 10، نرسل له تنبيه تشجيعي
                        if current_count < 10:
                            msg_ar = f"🎁 <b>نقطة جديدة!</b>\nصديقك استخدم التجربة المجانية.\nرصيدك الحالي: {current_count}/10 نقاط."
                            msg_en = f"🎁 <b>New Point!</b>\nYour friend used the free trial.\nCurrent balance: {current_count}/10 points."
                            await bot.send_message(inviter_id, msg_ar if inv_lang == "ar" else msg_en, parse_mode=ParseMode.HTML)
                        
                        # إذا وصل لـ 10، نفعل الشهر المجاني ونصفر العداد
                        else:
                            await extend_user_subscription(pool, inviter_id)
                            await conn.execute("UPDATE users_info SET ref_count = 0 WHERE user_id = $1", inviter_id)
                            
                            win_msg_ar = "🎉 <b>مبروك!</b>\nلقد دعوت 10 أشخاص بنجاح واستهلكوا تجربتهم.\nتم تفعيل اشتراك <b>شهر VIP مجاني</b> في حسابك مكافأة من نظام الدعوات!"
                            win_msg_en = "🎉 <b>Congratulations!</b>\nYou have successfully invited 10 friends who used their trial.\nA <b>Free VIP Month</b> has been activated in your account as a reward from the invite system!"
                            await bot.send_message(inviter_id, win_msg_ar if inv_lang == "ar" else win_msg_en, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        print(f"Ref notification error: {e}")


        await cb.message.answer("⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ شهرياً." if lang=="ar" else "⚠️ Your free trial has ended. For full access, please subscribe for a Monthly fee of 10 USDT or 500 ⭐.", reply_markup=get_payment_kb(lang))

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
    
    await extend_user_subscription(pool, uid)
    
    await m.answer(
        "✅ تم تأكيد الدفع بنجاح! شكراً لاشتراكك.\nتم تفعيل اشتراكك كـ VIP لمدة 30 يوماً."
        if lang == "ar" else
        "✅ Payment confirmed! Thank you for subscribing.\nYour VIP subscription is active for 30 days."
    )

@dp.callback_query(F.data == "pay_invite")
async def invite_pay_call(cb: types.CallbackQuery):
    uid, pool = cb.from_user.id, dp['db_pool']
    user = await pool.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
    lang = user['lang'] if user else "ar"
    
    bot_info = await bot.get_me()
    count = await pool.fetchval("SELECT ref_count FROM users_info WHERE user_id = $1", uid)
    count = count or 0 
    
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    
    if lang == "ar":
        msg = (
            "💎 <b>احصل على اشتراك VIP مجاناً!</b>\n"
            "━━━━━━━━━━━━━━\n"
            "ادعُ أصدقاءك لاستخدام البوت واحصل على اشتراك VIP مجاني كبديل للدفع.\n\n"
            "🎁 <b>المكافأة:</b> شهر VIP مجاني لكل 10 أشخاص يستخدمون التجربة المجانية.\n\n"
            f"📊 <b>رصيدك الحالي:</b> {count}/10 نقاط\n"
            f"🔗 <b>رابطك الخاص:</b>\n{ref_link}\n"
            "━━━━━━━━━━━━━━\n"
            "انسخ الرابط وشاركه الآن لتفعيل اشتراكك تلقائياً عند اكتمال العدد!"
        )
    else:
        msg = (
            "💎 <b>Get a FREE VIP Subscription!</b>\n"
            "━━━━━━━━━━━━━━\n"
            "Invite friends to use the bot and get a free VIP subscription instead of paying.\n\n"
            "🎁 <b>Reward:</b> 1 Free VIP Month for every 10 friends who use their free trial.\n\n"
            f"📊 <b>Current Balance:</b> {count}/10 points\n"
            f"🔗 <b>Your Invite Link:</b>\n{ref_link}\n"
            "━━━━━━━━━━━━━━\n"
            "Copy the link and share it now to automatically activate your subscription!"
        )
        
    # نعرض له الرابط ونبقي أزرار الدفع موجودة في حال غير رأيه وقرر يدفع
    await cb.message.edit_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_payment_kb(lang))

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
                    await extend_user_subscription(pool, user_id)
                    
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
        min_size=1,
        max_size=10,
        command_timeout=60,
        timeout=60,
        max_inactive_connection_lifetime=60
    )

    app['db_pool'] = dp['db_pool'] = pool

    # 🔥 تأكد الاتصال اشتغل قبل استقبال المستخدمين
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        print("✅ Database connected successfully")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        
    # ⚠️ إنشاء وتحديث الجداول (يجب أن يكون خارج الـ except)
    async with pool.acquire() as conn:
        # 1. إنشاء الجداول بالشكل الجديد
        await conn.execute("CREATE TABLE IF NOT EXISTS users_info (user_id BIGINT PRIMARY KEY, lang TEXT, last_active DATE)")
        await conn.execute("CREATE TABLE IF NOT EXISTS paid_users (user_id BIGINT PRIMARY KEY, expiry_date TIMESTAMP)")
        await conn.execute("CREATE TABLE IF NOT EXISTS trial_users (user_id BIGINT PRIMARY KEY)")
        
        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")

        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 756814703}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    #asyncio.create_task(ai_opportunity_radar(pool))
    #asyncio.create_task(daily_channel_post())
    await bot.set_webhook(f"{WEBHOOK_URL}/")


app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)