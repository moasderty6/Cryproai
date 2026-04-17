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
import uuid
import numpy as np

from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


# --- تحميل الإعدادات ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GATE_API_KEY = "a3f6a57b42f6106011e6890049e57b2e"
GATE_API_SECRET = "1ac18e0a690ce782f6854137908a6b16eb910cf02f5b95fa3c43b670758f79bc"
GATE_BASE = "https://api.gateio.ws/api/v4/spot/candlesticks"
# استخراج قائمة مفاتيح Groq
GROQ_KEYS_STR = os.getenv("GROQ_API_KEYS", "")
GROQ_API_KEYS = [k.strip() for k in GROQ_KEYS_STR.split(",") if k.strip()]
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
radar_pending_approvals = {}
# --- وظائف قاعدة البيانات ---
async def extend_user_subscription(db, user_id: int):
    # db هنا ممكن تكون pool أو conn، الاثنين فيهم execute
    await db.execute("""
        INSERT INTO paid_users (user_id, expiry_date) 
        VALUES ($1, CURRENT_TIMESTAMP + INTERVAL '30 days') 
        ON CONFLICT (user_id) DO UPDATE 
        SET expiry_date = GREATEST(COALESCE(paid_users.expiry_date, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP) + INTERVAL '30 days'
    """, user_id)

async def is_user_paid(db, user_id: int):
    query = """
        SELECT 1 FROM paid_users 
        WHERE user_id = $1 AND (expiry_date IS NULL OR expiry_date > CURRENT_TIMESTAMP)
    """
    res = await db.fetchval(query, user_id)
    return bool(res)

async def has_trial(db, user_id: int):
    res = await db.fetchval("SELECT 1 FROM trial_users WHERE user_id = $1", user_id)
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

async def update_market_memory_loop(pool):
    """مهمة خلفية لتحديث ذاكرة السوق لكل العملات بشكل دوري"""
    # ننتظر 10 ثواني بعد تشغيل البوت عشان نتأكد إن الداتا بيز اتصلت
    await asyncio.sleep(10) 
    
    while True:
        try:
            print("🔄 جاري سحب وتحديث ذاكرة الفوليوم (Market Memory) من CMC...")
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.get(
                    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                    headers=headers,
                    params={"limit": "250"} # جلب أهم 250 عملة
                )
                
                if res.status_code == 200:
                    coins = res.json()["data"]
                    
                    async with pool.acquire() as conn:
                        for c in coins:
                            symbol = c["symbol"]
                            # جلب نسبة التغير
                            vol_change = float(c["quote"]["USD"].get("volume_change_24h", 0))
                            
                            # تخزينها في قاعدة البيانات
                            await conn.execute("""
                                INSERT INTO market_memory (symbol, volume_change, last_updated)
                                VALUES ($1, $2, CURRENT_TIMESTAMP)
                                ON CONFLICT (symbol) DO UPDATE 
                                SET volume_change = EXCLUDED.volume_change, last_updated = CURRENT_TIMESTAMP
                            """, symbol, f"{vol_change:.1f}%")
                            
                    print("✅ تم تعبئة ذاكرة السوق بنجاح! البوت الآن يمتلك نظرة شاملة.")
        except Exception as e:
            print(f"Market Memory Loop Error: {e}")
        
        # ينام ويحدث البيانات كل 4 ساعات (14400 ثانية)
        await asyncio.sleep(14400)

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
                
                candles = await get_candles_gate(f"{symbol}_USDT", "1h", limit=120)
                if not candles: continue

                df = pd.DataFrame(candles)
                df = df.iloc[:, :6]
                df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                for col in ["close","high","low","open","volume"]:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

                # حساب المؤشرات القديمة (نحتاجها للـ RSI والماكدي)
                last_rsi, last_macd_diff, last_bb, last_vol, _, _ = compute_indicators(candles)

                # 🔥 التعديل الجبار: استدعاء دالة التحليل الرياضي للرادار
                trend_dir, trend_str, adx_val, _, _, _, _, _, _ = calculate_smart_trend_and_targets(df, price)

                avg_vol = df["volume"].rolling(20).mean()
                volume_spike = df["volume"].iloc[-1] > (avg_vol.iloc[-1] * 2.5)

                range_20 = df["high"].rolling(20).max() - df["low"].rolling(20).min()
                squeeze = range_20.iloc[-1] < (price * 0.07)
                recent_high = df["high"].rolling(20).max().iloc[-2]
                breakout = price > recent_high

                fake_move = (df["high"].iloc[-1] - df["low"].iloc[-1]) / price > 0.35
                if fake_move: continue

                # -----------------
                # 🎯 1H BASE SCORING (النظام المحدث)
                # -----------------
                base_score = 0
                
                # 1. فلتر الاتجاه وقوته (أهم شرط)
                if trend_dir == "Bullish":
                    base_score += 20  # مكافأة للترند الصاعد
                elif trend_dir == "Bearish":
                    base_score -= 30  # عقوبة قاسية للترند الهابط (تجنب السكاكين الساقطة)
                    
                if adx_val >= 25:
                    base_score += 15  # ترند قوي وحقيقي
                elif adx_val < 20:
                    base_score -= 20  # السوق عرضي وممل (تجنب الاختراقات الكاذبة)

                # 2. فلتر المؤشرات الكلاسيكية
                if 40 <= last_rsi <= 65: base_score += 10
                elif last_rsi > 75: base_score -= 10 # تشبع شرائي خطير
                
                if last_macd_diff > 0:
                    base_score += 10
                
                # 3. فلتر السيولة والاختراق
                if volume_spike:
                    base_score += 20
                    if df["volume"].iloc[-1] > (avg_vol.iloc[-1] * 3): base_score += 10
                else:
                    base_score -= 15 # لا ندخل بدون فوليوم
                
                if squeeze: base_score += 10
                if breakout: base_score += 15

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
                        # --- إرسال الإشارة للأدمن للموافقة ---
            signal_id = str(uuid.uuid4())[:8] # إنشاء معرف فريد للإشارة
            radar_pending_approvals[signal_id] = {
                "symbol": symbol,
                "price": price,
                "signal": signal,
                "score": best_score,
                "insight_ar": insight_ar,
                "insight_en": insight_en
            }

            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ موافقة ونشر للمشتركين", callback_data=f"rad_app_{signal_id}")
                ],
                [
                    InlineKeyboardButton(text="❌ إلغاء وتجاهل", callback_data=f"rad_rej_{signal_id}")
                ]
            ])

            admin_text = (
                f"⚠️ <b>تنبيه أدمن: رادار جديد ينتظر موافقتك</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"العملة: #{symbol}\n"
                f"السعر: ${format_price(price)}\n"
                f"الإشارة: {signal}\n"
                f"السكور: {best_score}/100\n\n"
                f"📝 <b>التحليل (عربي):</b>\n{insight_ar}\n\n"
                f"📝 <b>التحليل (إنجليزي):</b>\n{insight_en}\n"
                f"━━━━━━━━━━━━━━\n"
                f"هل تريد الموافقة على نشرها؟"
            )

            try:
                await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                print(f"Failed to send approval to admin: {e}")

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
    if not GROQ_API_KEYS:
        print("❌ لا يوجد مفاتيح Groq في الإعدادات!")
        return "⚠️ Error: API keys missing"

    data = {
        "model": GROQ_MODEL, 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,  
        "max_tokens": 800    
    }

    async with httpx.AsyncClient(timeout=45) as client:
        # 🔄 البوت سيمر على كل المفاتيح بالترتيب
        for api_key in GROQ_API_KEYS:
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            try:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=data
                )
                res.raise_for_status() # إذا كان هناك خطأ ليمت (429) سينتقل للـ except
                
                ans = res.json()["choices"][0]["message"]["content"]
                return ans # نجح التحليل! نخرج من الدالة ونعطي النتيجة للزبون
                
            except httpx.HTTPStatusError as e:
                # إذا كان الخطأ بسبب الليمت (429) أو مشكلة بالسيرفر، نجرب المفتاح اللي بعده
                if e.response.status_code == 429:
                    print(f"⚠️ المفتاح {api_key[:8]}... استنفذ الليمت. جاري تجربة المفتاح التالي...")
                    continue
                else:
                    print(f"⚠️ خطأ HTTP في المفتاح {api_key[:8]}... : {e}")
                    continue
            except Exception as e:
                print(f"⚠️ خطأ غير متوقع في المفتاح {api_key[:8]}... : {e}")
                continue # في حال فشل الاتصال تماماً، نجرب اللي بعده
                
    # إذا لفت الدوامة على كل الـ 10 مفاتيح وكلهم فيهم ليمت أو خطأ
    print("❌ كل مفاتيح Groq فشلت أو استنفذت الليمت!")
    return "⚠️ Error generating analysis. Server is highly loaded."

# --- الأوامر ---
# --- أزرار موافقة الأدمن على الرادار ---# --- أزرار موافقة الأدمن على الرادار ---
@dp.callback_query(F.data.startswith("rad_app_"))
async def approve_radar_signal(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)

    signal_id = cb.data.replace("rad_app_", "")
    data = radar_pending_approvals.get(signal_id)

    if not data:
        return await cb.message.edit_text("❌ انتهت صلاحية هذه الإشارة أو تم اتخاذ قرار مسبقاً.")

    await cb.message.edit_text(f"✅ تمت الموافقة! جاري إرسال إشارة {data['symbol']} لجميع المستخدمين...")

    pool = dp['db_pool']
    
    # 🛡️ استعلام واحد يجلب الجميع مع حالة اشتراكهم لعدم تدمير قاعدة البيانات
    async with pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT u.user_id, u.lang, 
                   CASE WHEN p.expiry_date > CURRENT_TIMESTAMP THEN true ELSE false END as is_paid
            FROM users_info u
            LEFT JOIN paid_users p ON u.user_id = p.user_id
        """)

    for row in users:
        uid = row["user_id"]
        lang = row["lang"] or "ar"
        paid = row["is_paid"] # أخذنا الحالة بدون ما نكلم الداتابيز مرة ثانية!

        # ---------- VIP ----------
        if paid:
            if lang == "ar":
                text = (
                    f"🚨 <b>رادار السوق الذكي VIP</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 العملة: #{data['symbol']}\n"
                    f"💵 السعر: ${format_price(data['price'])}\n"
                    f"⚡ الإشارة: {data['signal']}\n"
                    f"📊 السكور: {data['score']}/100\n\n"
                    f"📈 التحليل:\n{data['insight_ar']}\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📌 نتائج تحليلات البوت: @N_Results"
                )
            else:
                text = (
                    f"🚨 <b>VIP Smart Market Radar</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 Coin: #{data['symbol']}\n"
                    f"💵 Price: ${format_price(data['price'])}\n"
                    f"⚡ Signal: {data['signal']}\n"
                    f"📊 Score: {data['score']}/100\n\n"
                    f"📈 Insight:\n{data['insight_en']}\n"
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
                    f"⚡ الإشارة: {data['signal']}\n"
                    f"📊 السكور: {data['score']}/100\n\n"
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
                    f"⚡ Signal: {data['signal']}\n"
                    f"📊 Score: {data['score']}/100\n\n"
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
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Failed to send to {uid}: {e}")
            continue
            
    # مسح الإشارة من الذاكرة بعد نشرها
    del radar_pending_approvals[signal_id]

@dp.callback_query(F.data.startswith("rad_rej_"))
async def reject_radar_signal(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)

    signal_id = cb.data.replace("rad_rej_", "")
    
    if signal_id in radar_pending_approvals:
        del radar_pending_approvals[signal_id]

    await cb.message.edit_text("❌ تم تجاهل الإشارة ولن يتم إرسالها للمستخدمين.")
# --- إعداد حالة الانتظار ---
class ManageSub(StatesGroup):
    waiting_for_user_id = State()

# 1. أمر طلب الـ ID
@dp.message(Command("manage"))
async def manage_cmd(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_USER_ID:
        return
    await m.answer("✍️ أرسل الـ ID الخاص بالمستخدم الذي تريد تعديل اشتراكه:")
    await state.set_state(ManageSub.waiting_for_user_id)

# 2. استقبال الـ ID وإرسال الأزرار
@dp.message(ManageSub.waiting_for_user_id)
async def process_manage_id(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("❌ يرجى إرسال أرقام فقط (ID صحيح). أعد الإرسال:")
    
    target_id = int(m.text)
    await state.clear() # ننهي حالة الانتظار

    # إنشاء الأزرار
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ إضافة شهر", callback_data=f"sub_add_{target_id}"),
            InlineKeyboardButton(text="➖ خصم شهر", callback_data=f"sub_min_{target_id}")
        ]
    ])
    
    await m.answer(f"⚙️ <b>إدارة اشتراك المستخدم:</b> <code>{target_id}</code>\nاختر الإجراء المطلوب:", reply_markup=kb)

# 3. معالجة زر الإضافة
@dp.callback_query(F.data.startswith("sub_add_"))
async def add_month_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return
    
    target_id = int(cb.data.replace("sub_add_", ""))
    pool = dp['db_pool']
    
    async with pool.acquire() as conn:
        # إضافة شهر (نفس نظام الدفع تماماً)
        await conn.execute("""
            INSERT INTO paid_users (user_id, expiry_date) 
            VALUES ($1, CURRENT_TIMESTAMP + INTERVAL '30 days') 
            ON CONFLICT (user_id) DO UPDATE 
            SET expiry_date = GREATEST(COALESCE(paid_users.expiry_date, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP) + INTERVAL '30 days'
        """, target_id)
        
        new_date = await conn.fetchval("SELECT expiry_date FROM paid_users WHERE user_id = $1", target_id)
        
    await cb.message.edit_text(f"✅ <b>تمت الإضافة!</b>\nتم إضافة 30 يوم بنجاح للمستخدم: <code>{target_id}</code>\n📅 تاريخ الانتهاء الجديد: {new_date.strftime('%Y-%m-%d')}")

# 4. معالجة زر الخصم
@dp.callback_query(F.data.startswith("sub_min_"))
async def minus_month_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return
    
    target_id = int(cb.data.replace("sub_min_", ""))
    pool = dp['db_pool']
    
    async with pool.acquire() as conn:
        # خصم شهر
        res = await conn.execute("""
            UPDATE paid_users 
            SET expiry_date = expiry_date - INTERVAL '30 days' 
            WHERE user_id = $1
        """, target_id)
        
        if res == "UPDATE 1":
            new_date = await conn.fetchval("SELECT expiry_date FROM paid_users WHERE user_id = $1", target_id)
            await cb.message.edit_text(f"✅ <b>تم الخصم!</b>\nتم خصم 30 يوم بنجاح من المستخدم: <code>{target_id}</code>\n📅 تاريخ الانتهاء الجديد: {new_date.strftime('%Y-%m-%d')}")
        else:
            await cb.message.edit_text(f"❌ المستخدم <code>{target_id}</code> غير موجود في جدول المشتركين!")

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

@dp.message(Command("status"))
async def status_cmd(m: types.Message):
    pool = dp['db_pool']
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM users_info")
            vips = await conn.fetchval("SELECT count(*) FROM paid_users")
            total_trials = await conn.fetchval("SELECT count(*) FROM trial_users")
            active_today = await conn.fetchval("SELECT count(*) FROM users_info WHERE last_active = CURRENT_DATE")
        
        msg = (f"📊 **إحصائيات البوت المتقدمة:**\n"
               f"───────────────────\n"
               f"👥 **إجمالي القاعدة:** `{total}` مستخدم\n"
               f"🔥 **النشاط اليومي:** `{active_today}` مستخدم نشط\n"
               f"🎁 **مستخدمي التجربة:** `{total_trials}` شخص\n"
               f"💎 **المشتركين VIP:** `{vips}` مشترك")
        await m.answer(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Status Error: {e}")
    
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

    # 🛡️ فتح اتصال واحد محمي لكل العمليات لتجنب انقطاع Neon
    try:
        async with pool.acquire() as conn:
            # 1. تحديث تاريخ الظهور
            await conn.execute("""
                INSERT INTO users_info (user_id, last_active)
                VALUES ($1, CURRENT_DATE)
                ON CONFLICT (user_id)
                DO UPDATE SET last_active = CURRENT_DATE
            """, uid)

            # 2. جلب لغة المستخدم
            user = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", uid)
            lang = user['lang'] if user and user['lang'] else "ar"
            
            # 3 و 4. فحص الاشتراك والتجربة (نمرر conn بدلاً من pool للحفاظ على نفس الاتصال)
            paid = await is_user_paid(conn, uid)
            trial = await has_trial(conn, uid)
            
    except Exception as e:
        print(f"DB Error in handle_symbol: {e}")
        return await m.answer("⚠️ حدث خطأ في الاتصال بقاعدة البيانات. يرجى المحاولة مرة أخرى.")

    # فحص النتيجة ومنع المستخدم إذا انتهت تجربته
    if not paid and not trial:
        return await m.answer(
            "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ شهرياً." if lang=="ar" 
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
                                # محاولة جلب الفوليوم الأدق من CMC
                try:
                    res_cmc = await client.get(
                        f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={sym}",
                        headers={"X-CMC_PRO_API_KEY": CMC_KEY},
                        timeout=5
                    )
                    data_cmc = res_cmc.json()
                    if res_cmc.status_code == 200 and sym in data_cmc.get("data", {}):
                        quote_data = data_cmc["data"][sym]["quote"]["USD"]
                        # الفوليوم الكلي
                        volume_24h = float(quote_data["volume_24h"])
                        # نسبة تغير الفوليوم (مهم جداً لكشف الحيتان)
                        vol_change_cmc = float(quote_data.get("volume_change_24h", 0))
                        
                        # 🔥 تحديث قاعدة البيانات فوراً بنسبة تغير الفوليوم
                        async with pool.acquire() as conn:
                            await conn.execute("""
                                INSERT INTO market_memory (symbol, volume_change, last_updated)
                                VALUES ($1, $2, CURRENT_TIMESTAMP)
                                ON CONFLICT (symbol) DO UPDATE 
                                SET volume_change = EXCLUDED.volume_change, last_updated = CURRENT_TIMESTAMP
                            """, sym, f"{vol_change_cmc:.1f}%")
                except Exception as e:
                    print(f"CMC Fetch Error: {e}")

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
# ===== SMART INDICATORS =====

def compute_volume_delta(df):
    buy_vol = df[df["close"] > df["open"]]["volume"].sum()
    sell_vol = df[df["close"] < df["open"]]["volume"].sum()
    return buy_vol - sell_vol

def detect_candle_strength(df):
    last = df.iloc[-1]

    body = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]

    score = 0

    if lower_wick > body * 2:
        score += 10  # تجميع

    if upper_wick > body * 2:
        score -= 10  # تصريف

    return score

def detect_volatility(df):
    atr = (df["high"] - df["low"]).rolling(14).mean()
    current_atr = atr.iloc[-1]
    avg_atr = atr.mean()

    if current_atr < avg_atr * 0.7:
        return 10
    return 0

def compute_momentum(df):
    momentum = df["close"].diff()
    acceleration = momentum.diff()

    if acceleration.iloc[-1] > 0:
        return 10
    return -5

def detect_fake_breakout(df):
    recent_high = df["high"].rolling(20).max().iloc[-2]
    last = df.iloc[-1]

    if last["high"] > recent_high and last["close"] < recent_high:
        return -20
    return 0
import numpy as np # تأكد من إضافتها في الأعلى

def calculate_smart_trend_and_targets(df, current_price, db_vol_change):
    # 1. حساب الـ ATR
    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['prev_close'])
    df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    atr = df['tr'].rolling(14).mean().iloc[-1]

    if pd.isna(atr) or atr == 0:
        atr = current_price * 0.02 

    max_atr = current_price * 0.10
    atr = min(atr, max_atr)

    # 2. تحديد الاتجاه
    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]

    if ema20 >= ema50:
        trend_direction = "Bullish"
    else:
        trend_direction = "Bearish"

    distance_pct = abs(current_price - ema50) / ema50 * 100
    
    # 🔥 3. كشف نوايا الحيتان بناءً على دمج الاتجاه مع الفوليوم التراكمي
    market_action = "حركة طبيعية" 
    
    if trend_direction == "Bearish" and distance_pct < 4 and db_vol_change > 60:
        market_action = "تجميع حيتان مخفي (Accumulation)"
    elif trend_direction == "Bullish" and distance_pct > 6 and db_vol_change > 100:
        market_action = "خطر تصريف قمم (Distribution)"
    elif trend_direction == "Bullish" and db_vol_change < 20:
        market_action = "اختراق كاذب أو ضعيف (Fakeout)"
    elif trend_direction == "Bullish" and db_vol_change >= 40:
        market_action = "اختراق حقيقي مدعوم بسيولة"
    elif trend_direction == "Bearish" and db_vol_change > 60:
        market_action = "بيع هلع أو هبوط قوي (Panic Sell)"

    # تحديد قوة الاتجاه
    if "Fakeout" not in market_action:
        if distance_pct >= 8:
            trend_strength = "قوي"
            adx_dummy = 45.0
        elif distance_pct >= 4:
            trend_strength = "جيد"
            adx_dummy = 30.0
        elif distance_pct >= 1.5:
            trend_strength = "متوسط"
            adx_dummy = 20.0
        else:
            trend_strength = "ضعيف"
            adx_dummy = 12.0
    else:
        trend_strength = "ضعيف"
        adx_dummy = 15.0

    # 4. حساب الأهداف
    min_allowed_price = current_price * 0.000001 

    if trend_direction == "Bearish":
        sl = current_price + (atr * 1.5)
        tp1 = current_price - (atr * 1.5)
        tp2 = current_price - (atr * 2.5)
        tp3 = current_price - (atr * 3.5)
        
        sl = max(current_price * 1.005, sl) 
        tp1 = min(current_price * 0.995, max(min_allowed_price, tp1)) 
        tp2 = min(tp1 * 0.995, max(min_allowed_price, tp2)) 
        tp3 = min(tp2 * 0.995, max(min_allowed_price, tp3)) 
    else:
        sl = current_price - (atr * 1.5)
        tp1 = current_price + (atr * 1.5)
        tp2 = current_price + (atr * 2.5)
        tp3 = current_price + (atr * 3.5)
        
        sl = max(min_allowed_price, sl) 
        tp1 = max(current_price * 1.005, tp1) 
        tp2 = max(tp1 * 1.005, tp2) 
        tp3 = max(tp2 * 1.005, tp3) 

    support = df['low'].rolling(20).min().iloc[-1]
    resistance = df['high'].rolling(20).max().iloc[-1]

    return trend_direction, trend_strength, market_action, adx_dummy, sl, tp1, tp2, tp3, support, resistance



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

    try:
        await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")
    except Exception as e:
        if "message is not modified" in str(e):
            pass  
        else:
            print(f"Edit msg error in analysis: {e}")

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

    # ✅ بداية الإصلاح: إدخال الكود داخل الدالة بالمسافات الصحيحة
        # (داخل دالة run_analysis بعد تحويل df من الشموع)
    if candles:
        last_rsi, last_macd, last_bb, last_vol, _, _ = compute_indicators(candles) 
        
        import pandas as pd 
        df = pd.DataFrame(candles)
        df = df.iloc[:, :6]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open"]

        for col in ["close", "high", "low", "open", "volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # 🔥 سحب الفوليوم من قاعدة البيانات
        db_vol_float = 0.0
        try:
            async with pool.acquire() as conn:
                db_mem = await conn.fetchrow("SELECT volume_change FROM market_memory WHERE symbol = $1", clean_sym)
                if db_mem and db_mem['volume_change']:
                    vol_str = db_mem['volume_change'].replace('%', '').strip()
                    db_vol_float = float(vol_str)
        except Exception as e:
            print(f"Failed to fetch market_memory for {clean_sym}: {e}")

        # تمرير قيمة الفوليوم للدالة الذكية
        trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = calculate_smart_trend_and_targets(df, price, db_vol_float)
        
        if lang == "ar":
            real_trend = "صاعد" if trend_dir == "Bullish" else "هابط"
            trend_strength = trend_str
        else:
            real_trend = trend_dir
            trend_strength_en = {"قوي جداً": "Very Strong", "قوي": "Strong", "جيد": "Good", "متوسط": "Moderate", "ضعيف": "Weak", "ضعيف ومخادع": "Weak & Fake"}
            trend_strength = trend_strength_en.get(trend_str, trend_str)

    else:
        real_trend, trend_strength, market_action = ("غير معروف", "غير معروف", "لا توجد بيانات")
        calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = (price*0.9, price*1.05, price*1.1, price*1.15, price*0.95, price*1.05)
        adx_val = 0.0 

    macd_fmt = format_price(last_macd) if last_macd is not None else "0.0"
    safe_rsi = f"{last_rsi:.2f}" if last_rsi is not None else "N/A"

    # 🔥 تحديث البرومبت ليشمل الـ market_action
    if lang == "ar":
        prompt = f"""
أنت محلل فني خبير في شركة "NaiF CHarT". قم بصياغة هذا التحليل لعملة {clean_sym} بشكل احترافي ومختصر.
البيانات محسوبة رياضياً وجاهزة، ⚠️ يمنع منعاً باتاً تغيير أرقام الأهداف أو الوقف ⚠️، فقط قم بترتيبها في القالب المطلوب واكتب تعليقاً فنياً دقيقاً في سطر واحد لكل مؤشر.

⚠️ التزم بهذا القالب بحذافيره (استخدم HTML فقط):

📊 <b>التحليل لـ {clean_sym}</b> | {tf} | {format_price(price)}$
الاتجاه: {real_trend} ({trend_strength})

📉 <b>الدعم والمقاومة</b>
الدعم الأقرب: <code>{format_price(calc_sup)}</code>$
المقاومة الأقرب: <code>{format_price(calc_res)}</code>$

🎯 <b>الأهداف السعرية (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>وقف الخسارة (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>تحليل المؤشرات</b>
• Liquidity: {market_action} (اكتب سطر يعلق على هذه الحالة)
• RSI ({safe_rsi}): (اكتب سطر واحد يوضح التشبع أو الحياد)
• MACD ({macd_fmt}): (اكتب سطر واحد يوضح الزخم)
• ADX ({adx_val:.1f}): (اكتب سطر واحد يوضح قوة الترند)
"""
    else:
        prompt = f"""
You are an expert Technical Analyst at "NaiF CHarT". Format this analysis for {clean_sym} professionally and concisely.
The data is calculated mathematically and is completely ready. ⚠️ STRICT RULE: DO NOT change the TP or SL numbers ⚠️. Just arrange them in the required template and write a precise technical comment in one short line for each indicator.

⚠️ Strictly follow this template (Use HTML only):

📊 <b>Analysis: {clean_sym}</b> | {tf} | {format_price(price)}$
Trend: {real_trend} ({trend_strength})

📉 <b>Support & Resistance</b>
Nearest Support: <code>{format_price(calc_sup)}</code>$
Nearest Resistance: <code>{format_price(calc_res)}</code>$

🎯 <b>Price Targets (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>Stop Loss (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>Indicator Analysis</b>
• Liquidity: {market_action} (Write one line commenting on this action)
• RSI ({safe_rsi}): (Write one line explaining overbought/oversold or neutrality)
• MACD ({macd_fmt}): (Write one line explaining momentum)
• ADX ({adx_val:.1f}): (Write one line explaining trend strength)
"""


    res = await ask_groq(prompt, lang=lang)
    await cb.message.answer(res, parse_mode=ParseMode.HTML)
    
    if not (await is_user_paid(pool, uid)):
        async with pool.acquire() as conn:
            res = await conn.execute("INSERT INTO trial_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
            
            if "INSERT 0 1" in res:
                inviter = await conn.fetchrow("SELECT invited_by FROM users_info WHERE user_id = $1", uid)
                if inviter and inviter['invited_by']:
                    inviter_id = inviter['invited_by']
                    
                    await conn.execute("UPDATE users_info SET ref_count = COALESCE(ref_count, 0) + 1 WHERE user_id = $1", inviter_id)
                    current_count = await conn.fetchval("SELECT ref_count FROM users_info WHERE user_id = $1", inviter_id)
                    
                    inviter_lang_row = await conn.fetchrow("SELECT lang FROM users_info WHERE user_id = $1", inviter_id)
                    inv_lang = inviter_lang_row['lang'] if inviter_lang_row and inviter_lang_row['lang'] else "ar"
                    
                    try:
                        if current_count < 10:
                            msg_ar = f"🎁 <b>نقطة جديدة!</b>\nصديقك استخدم التجربة المجانية.\nرصيدك الحالي: {current_count}/10 نقاط."
                            msg_en = f"🎁 <b>New Point!</b>\nYour friend used the free trial.\nCurrent balance: {current_count}/10 points."
                            await bot.send_message(inviter_id, msg_ar if inv_lang == "ar" else msg_en, parse_mode=ParseMode.HTML)
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
        # نعرض له الرابط ونبقي أزرار الدفع موجودة في حال غير رأيه وقرر يدفع
    try:
        await cb.message.edit_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_payment_kb(lang))
        await cb.answer() # لإنهاء حالة التحميل في الزر
    except Exception as e:
        if "message is not modified" in str(e):
            # إذا ضغط على الزر وهو أصلاً فاتح نفس الرسالة نعطيه تنبيه خفيف
            await cb.answer("الرابط الخاص بك معروض أمامك بالفعل 👇🏼" if lang == "ar" else "Your link is already displayed 👇🏼")
        else:
            print(f"Edit message error: {e}")

# --- Webhook NOWPayments (IPN) ---
async def nowpayments_ipn(req: web.Request):
    try:
        data = await req.json()
        status = data.get("payment_status")
        order_id = data.get("order_id") 

        print(f"إشعار دفع جديد: الحالة {status} للمستخدم {order_id}")

        if status == "finished":
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
           # 🔥 الجديد: إنشاء جدول الذاكرة للفوليوم
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS market_memory (
                symbol TEXT PRIMARY KEY,
                market_phase TEXT,
                volume_change TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")

        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    #asyncio.create_task(ai_opportunity_radar(pool))
    asyncio.create_task(daily_channel_post())
    asyncio.create_task(update_market_memory_loop(pool)) # 🔥 تشغيل ذاكرة السوق
    await bot.set_webhook(f"{WEBHOOK_URL}/")


app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)