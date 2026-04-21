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
import ta
import pandas as pd
import uuid
import numpy as np
import datetime
import websockets

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
BINANCE_API_KEY = "rvApoDI6XRYcki1r2QTnPUBs3QwESzrpTVKohgjbK1zxSzlvrFPxAbZKr94xA2Lx"
BINANCE_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com"
]

def get_random_binance_base():
    return random.choice(BINANCE_BASES)
BINANCE_HEADERS = {"X-MBX-APIKEY": BINANCE_API_KEY}
GATE_API_KEY = "a3f6a57b42f6106011e6890049e57b2e"
GATE_API_SECRET = "1ac18e0a690ce782f6854137908a6b16eb910cf02f5b95fa3c43b670758f79bc"
GATE_BASE = "https://api.gateio.ws/api/v4/spot/candlesticks"
BLACKLISTED_COINS = {"TOMO", "COCOS", "LRC", "BUSD", "TUSD", "USDC", "USDE", "BFUSD", "RLUSD", "POLY", "XUSD"}
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
# إشارة مرور للتحكم في طلبات بايننس لمنع الحظر
binance_rate_limit_event = asyncio.Event()
binance_rate_limit_event.set() # الإشارة خضراء افتراضياً (مسموح بالطلبات)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
radar_pending_approvals = {}
user_session_data = {}
# طابور معالجة العملات المنفجرة (لحماية الـ API من الضغط)
radar_processing_queue = asyncio.Queue()
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
# ذاكرة مؤقتة لبيانات السلسلة لتجنب استنفاد الـ API (Cache)
ON_CHAIN_CACHE = {"usdt_inflow_score": 0.0, "last_updated": 0}

async def get_whale_inflow_score():
    """
    تتبع حركة دخول الدولار الرقمي (USDT/USDC) لمنصات التداول.
    حالياً نستخدم محاكاة (Mock) يمكن استبدالها بـ API حقيقي من CryptoQuant أو Glassnode مستقبلاً.
    """
    current_time = time.time()
    
    # القراءة من الذاكرة إذا لم يمر 60 ثانية (حماية السيرفر والـ API)
    if current_time - ON_CHAIN_CACHE["last_updated"] < 60:
        return ON_CHAIN_CACHE["usdt_inflow_score"]

    try:
        # 💡 [مستقبلاً]: هنا تضع طلب الـ API الفعلي لـ CryptoQuant مثلاً
        # async with httpx.AsyncClient() as client:
        #     res = await client.get("https://api.cryptoquant.com/v1/exchange-flows/inflow")
        #     inflow_volume = res.json()['data']['volume']
        
        # محاكاة مؤقتة: توليد رقم عشوائي يمثل تدفق الحيتان (من 0 إلى 10)
        # في الواقع، هذا سيقرأ حجم دخول الدولار للمنصة اللحظي
        mock_inflow_volume = random.uniform(2_000_000, 50_000_000) 
        
        # تحويل الحجم إلى سكور من 0 إلى 10
        score = min(max((mock_inflow_volume - 5_000_000) / 4_000_000, 0.0), 10.0)
        
        ON_CHAIN_CACHE["usdt_inflow_score"] = score
        ON_CHAIN_CACHE["last_updated"] = current_time
        
        return score
    except Exception as e:
        print(f"⚠️ On-Chain Fetch Error: {e}")
        return 0.0
import xgboost as xgb
import pandas as pd
import numpy as np

# تخزين النموذج في الذاكرة الحية
AI_QUANT_MODEL = None
MIN_TRAINING_SAMPLES = 100 # أقل عدد صفقات مطلوب لتدريب الذكاء الاصطناعي

def train_xgboost_sync(records):
    """
    الدالة الثقيلة لتدريب النموذج (تعمل في مسار منفصل لكي لا تجمد التيليجرام).
    """
    global AI_QUANT_MODEL
    df = pd.DataFrame(records)
    
    # تحديد المدخلات (Features) والمخرجات (Labels)
    X = df[['z_score', 'cvd', 'imbalance', 'adx', 'rsi', 'whale_inflow_score']]
    y = df['label']
    
    # إعدادات متقدمة للبيانات المالية المليئة بالضوضاء
    model = xgb.XGBClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.05, 
        subsample=0.8, colsample_bytree=0.8, eval_metric='logloss'
    )
    model.fit(X, y)
    AI_QUANT_MODEL = model
    return True

async def ai_trainer_worker(pool):
    """
    عامل خلفي يستيقظ كل 12 ساعة لتدريب الذكاء الاصطناعي وتحديث ذكائه
    بناءً على أحدث الصفقات التي تمت معالجتها.
    """
    await asyncio.sleep(60) # انتظر دقيقة عند تشغيل البوت
    
    while True:
        try:
            async with pool.acquire() as conn:
                # جلب البيانات التي تم الحكم عليها (0 فاشلة، 1 ناجحة)
                records = await conn.fetch("""
                    SELECT z_score, cvd, imbalance, adx, rsi, whale_inflow_score, label
                    FROM ml_training_data 
                    WHERE label IN (0, 1)
                """)
                
                if len(records) >= MIN_TRAINING_SAMPLES:
                    print(f"🧠 [AI Trainer] Training model on {len(records)} historical signals...")
                    # تشغيل التدريب في مسار خلفي
                    records_dict = [dict(r) for r in records]
                    await asyncio.to_thread(train_xgboost_sync, records_dict)
                    print("✅ [AI Trainer] Model updated successfully! AI is getting smarter.")
                else:
                    print(f"⏳ [AI Trainer] Not enough data yet ({len(records)}/{MIN_TRAINING_SAMPLES}). Collecting...")
                    
        except Exception as e:
            print(f"⚠️ AI Trainer Error: {e}")
            
        await asyncio.sleep(43200) # ينام 12 ساعة

def predict_signal_sync(features: dict) -> float:
    """ يتوقع احتمالية النجاح من 0% إلى 100% """
    if AI_QUANT_MODEL is None:
        return -1.0 # -1 تعني أن الذكاء الاصطناعي لم يتدرب بعد
        
    input_data = pd.DataFrame([features])
    # استخراج احتمالية الفئة 1 (نجاح)
    prob = AI_QUANT_MODEL.predict_proba(input_data)[0][1]
    return float(prob) * 100 # إرجاع النسبة المئوية

# --- دوال الرادار المساعدة (ضعها فوق دالة الرادار) ---
async def get_recent_orderflow_delta(symbol, client, limit=500):
    """
    بديل سريع وآمن للـ WebSocket: يقرأ آخر 500 صفقة تمت لتحديد الشراء/البيع العدواني
    """
    try:
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/trades", params={"symbol": symbol, "limit": limit})

        if res.status_code == 200:
            trades = res.json()
            delta = 0.0
            for t in trades:
                amount = float(t['qty']) * float(t['price'])
                is_buyer_maker = t['isBuyerMaker'] 
                
                if not is_buyer_maker: # المشتري هو Taker (شراء ماركت/عدواني)
                    delta += amount
                else: # البائع هو Taker (بيع ماركت/عدواني)
                    delta -= amount
            return delta
    except:
        pass
    return 0.0
                
# البنية: {"BTCUSDT": {"volume": 1000000, "price": 65000, "last_update": 1712000000}}
live_market_memory = {}

async def smart_radar_watchdog(pool):
    """
    مستشعر النبض اللحظي (Producer): وظيفته فقط التقاط الشذوذ ورميه في الطابور بسرعة البرق
    """
    url = "wss://stream.binance.com:9443/ws/!miniTicker@arr"
    print("🟢 جاري الاتصال بـ Binance WebSocket لمراقبة السيولة اللحظية...")

    MIN_VOLUME_USD = 1_000_000  
    VOLUME_SPIKE_THRESHOLD = 0.05  
    PRICE_SPIKE_THRESHOLD = 0.01   

    while True:
        # --- إضافة: تنظيف الذاكرة المؤقتة من العملات الخاملة لمنع انهيار السيرفر ---
        current_time_cleanup = time.time()
        # حذف أي عملة لم تتحدث منذ أكثر من ساعة (3600 ثانية)
        keys_to_delete = [k for k, v in live_market_memory.items() if current_time_cleanup - v['last_update'] > 3600]
        for k in keys_to_delete:
            del live_market_memory[k]
        # ------------------------------------------------------------
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                print("✅ تم الاتصال بنجاح! الرادار اللحظي يعمل الآن.")
                
                async for message in ws:
                    data = json.loads(message)
                    current_time = time.time()

                    for ticker in data:
                        symbol = ticker['s']
                        if not symbol.endswith("USDT"): continue

                        clean_sym = symbol.replace("USDT", "")
                        # 🚫 الحظر الجذري قبل إدخالها للذاكرة اللحظية
                        if clean_sym in BLACKLISTED_COINS: continue

                        current_vol = float(ticker['q']) 
                        current_price = float(ticker['c'])
                        # ... باقي الكود كما هو ...

                        if symbol in live_market_memory:
                            old_data = live_market_memory[symbol]
                            old_vol = old_data['volume']
                            old_price = old_data['price']
                            time_diff = current_time - old_data['last_update']

      
                            if time_diff >= 60 and old_vol > 0:
                                vol_change = (current_vol - old_vol) / old_vol
                                price_change = (current_price - old_price) / old_price
                            
                                # التعديل الجذري: السعر يجب أن يكون ثابتاً تقريباً (أقل من 0.5% حركة) مع دخول فوليوم ضخم = تجميع صامت
                                MAX_PRICE_SPIKE = 0.005 
                            
                                if current_vol >= MIN_VOLUME_USD and vol_change >= VOLUME_SPIKE_THRESHOLD and abs(price_change) <= MAX_PRICE_SPIKE:
                                    print(f"👀 WebSocket is watching {symbol} | Vol: {current_vol}") 
                                    # إرسال العملة إلى الطابور فوراً للتحليل العميق
                                    coin_mock_data = {
                                        "symbol": symbol.replace("USDT", ""),
                                        "quote": {"USD": {"price": current_price}}
                                    }
                                    await radar_processing_queue.put(coin_mock_data)

                                    
                            live_market_memory[symbol] = {'volume': current_vol, 'price': current_price, 'last_update': current_time}
                        else:
                            live_market_memory[symbol] = {'volume': current_vol, 'price': current_price, 'last_update': current_time}

        except Exception as e:
            print(f"⚠️ خطأ في الرادار اللحظي: {e} - إعادة الاتصال...")
            await asyncio.sleep(3)


async def radar_worker_process(pool):
    """
    العامل الصامت (Consumer): يأخذ العملات من الطابور واحدة تلو الأخرى ويحللها بعمق
    هذا يمنع حظر منصات التداول لك بسبب كثرة الطلبات اللحظية.
    """
    sem = asyncio.Semaphore(5) # السماح بـ 5 تحليلات متزامنة فقط
    
    # ننتظر قليلاً حتى يعمل البوت
    await asyncio.sleep(10)
    print("👷‍♂️ عامل التحليل العميق (Worker) جاهز لمعالجة الإشارات...")
    
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            # اسحب عملة من الطابور
            coin_mock_data = await radar_processing_queue.get()
            symbol = f"{coin_mock_data['symbol']}USDT"
            
            async with pool.acquire() as conn:
                # فحص هل أرسلنا العملة في آخر 12 ساعة؟
                is_signaled = await conn.fetchval("""
                    SELECT 1 FROM radar_history 
                     WHERE symbol = $1 AND last_signaled > CURRENT_TIMESTAMP - INTERVAL '7 days'
                """, symbol)

                if not is_signaled:
                    print(f"🚨 [تحليل عميق] جاري تشريح {symbol}...")
                    
                    await conn.execute("""
                        INSERT INTO radar_history (symbol, last_signaled)
                        VALUES ($1, CURRENT_TIMESTAMP)
                        ON CONFLICT (symbol) DO UPDATE SET last_signaled = CURRENT_TIMESTAMP
                    """, symbol)

                    # جلب الماكرو اللحظي
                    market_regime = await detect_market_regime(client)
                    # إرسالها للتحليل
                    asyncio.create_task(analyze_radar_coin(coin_mock_data, client, market_regime, sem))
            
            # إخبار الطابور أن المهمة انتهت
            radar_processing_queue.task_done()
            await asyncio.sleep(1) # استراحة ثانية بين كل تحليل لحماية الـ API

# دالة وسيطة لتجهيز البيانات قبل التحليل العميق
async def trigger_deep_analysis(coin_mock_data, sem, pool):
    async with httpx.AsyncClient(timeout=30) as client:
        # 🟢 هنا نربط تصنيف السوق الماكرو (Market Regime)
        market_regime = await detect_market_regime(client)
        await analyze_radar_coin(coin_mock_data, client, market_regime, sem)


async def get_btc_trend(client):
    """جلب حالة البيتكوين لمعرفة ترند السوق العام من Binance"""
    try:
        # ✅ التصحيح: استدعاء الدالة التي تجلب رابطاً عشوائياً وتصحيح مسار الـ API
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/klines", params={
            "symbol": "BTCUSDT", "interval": "1d", "limit": 25
        })
        if res.status_code == 200:
            data = res.json()
            # في بايننس الإغلاق هو المؤشر رقم 4
            close_prices = [float(c[4]) for c in data]
            sma20 = sum(close_prices[-20:]) / 20
            return close_prices[-1] > sma20
    except:
        pass
    return True # افتراضي في حال فشل الـ API
 # افتراضي في حال فشل الـ API
async def get_micro_cvd_absorption(symbol, client):
    """
    يكتشف التجميع الصامت قبل الانفجار بجلب فريم الدقيقة لآخر 120 دقيقة.
    """
    try:
        # جلب بيانات دقيقة جداً لآخر ساعتين
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/klines", params={

            "symbol": symbol, "interval": "1m", "limit": 120
        }, timeout=5.0)
        
        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "trades", "tbv", "tqav", "ignore"])
            
            # تحويل البيانات إلى أرقام
            df["v"] = pd.to_numeric(df["v"])
            df["tbv"] = pd.to_numeric(df["tbv"]) # Taker Buy Volume
            df["c"] = pd.to_numeric(df["c"])
            
            # حساب الدلتا اللحظية لكل دقيقة
            df["sell_vol"] = df["v"] - df["tbv"]
            df["delta"] = df["tbv"] - df["sell_vol"]
            df["cvd"] = df["delta"].cumsum()
            
            # قياس الشذوذ بين السعر والسيولة
            price_change = (df["c"].iloc[-1] - df["c"].iloc[0]) / df["c"].iloc[0]
            cvd_trend = df["cvd"].iloc[-1] - df["cvd"].iloc[0]
            total_vol = df["v"].sum()
            
            # 🎯 معادلة القناص: السعر شبه ثابت (أقل من 1.5% حركة)، لكن الـ CVD يرتفع بانفجار (الحوت يمتص العروض)
                        # 🎯 معادلة القناص: السعر شبه ثابت (أقل من 2% حركة)، لكن الـ CVD يرتفع 
            if abs(price_change) < 0.02 and cvd_trend > (total_vol * 0.15):
                return 30.0, "Micro_Silent_Accumulation" 
            
            # كشف التصريف المخفي
            elif price_change > 0.02 and cvd_trend < -(total_vol * 0.15):
                return -25.0, "Hidden_Distribution"

                
    except Exception as e:
        pass
    return 0.0, None
async def get_institutional_orderflow(symbol, client, minutes=15):
    """
    يسحب الصفقات المجمعة لآخر 15 دقيقة متجاوزاً فخ الصفقات الفردية الوهمية.
    """
    import time
    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)
    
    try:
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/aggTrades", params={

            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time
        }, timeout=5.0)
        
        if res.status_code == 200:
            trades = res.json()
            buy_vol = 0.0
            sell_vol = 0.0
            
            for t in trades:
                amount = float(t['q']) * float(t['p'])
                # 'm' == True تعني أن البائع هو صانع السوق (شخص ما قام بالبيع كـ Taker)
                if t['m']: 
                    sell_vol += amount
                else: 
                    buy_vol += amount
                    
            delta = buy_vol - sell_vol
            return delta, buy_vol, sell_vol
    except Exception:
        pass
    return 0.0, 0.0, 0.0

import ta
import pandas as pd

async def detect_market_regime(client):
    """
    تحليل حالة السوق العامة (الماكرو) بناءً على حركة البيتكوين.
    """
    # جلب شمعة الـ 4 ساعات للبيتكوين لتحديد الاتجاه العام
        # جلب شمعة الـ 4 ساعات للبيتكوين لتحديد الاتجاه العام
    base_url = get_random_binance_base()
    res = await client.get(f"{base_url}/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "4h", "limit": 100})
    if res.status_code != 200:
        return {"trend": "Neutral", "volatility": "Normal", "adx": 20}

    data = res.json()
    df = pd.DataFrame(data).iloc[:, :6]
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col])

    # 1. قياس قوة الاتجاه باستخدام ADX
    adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14, fillna=True)
    current_adx = adx_ind.adx().iloc[-1]

    # 2. قياس الاتجاه باستخدام تقاطع المتوسطات (EMA)
    ema20 = df['close'].ewm(span=20).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50).mean().iloc[-1]

    # 3. قياس التذبذب (Volatility) باستخدام ATR
    atr_ind = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14, fillna=True)
    current_atr = atr_ind.average_true_range().iloc[-1]
    mean_atr = atr_ind.average_true_range().mean()

    # --- التصنيف ---
    regime = "Unknown"
    volatility = "Normal"

    if current_adx < 25:
        regime = "Ranging" # سوق عرضي مميت (Choppy)
    elif ema20 > ema50:
        regime = "Trending_Bull" # ترند صاعد قوي
    elif ema20 < ema50:
        regime = "Trending_Bear" # ترند هابط قوي

    if current_atr > mean_atr * 1.5:
        volatility = "High_Vol" # تذبذب عالي (خطر التصفيات)
    elif current_atr < mean_atr * 0.7:
        volatility = "Low_Vol" # ضغط سيولة (انفجار قادم)

    print(f"🌍 Market Regime: {regime} | Volatility: {volatility} | ADX: {current_adx:.1f}")
    
    return {"trend": regime, "volatility": volatility, "adx": current_adx}

import websockets
import json
import asyncio
import time

async def detect_flash_spoofing_ws(symbol: str, duration: float = 4.0):
    """
    تسجيل فيديو للأوردر بوك لمدة 4 ثوانٍ (بمعدل 10 إطارات في الثانية)
    لكشف الجدران التي تظهر وتختفي بسرعة البرق (Flash Spoofing).
    """
    clean_symbol = symbol.replace("USDT", "").lower() + "usdt"
    # نطلب أفضل 20 مستوى، بتحديث كل 100 ملي ثانية
    ws_url = f"wss://stream.binance.com:9443/ws/{clean_symbol}@depth20@100ms"
    
    total_bids_vol = 0
    total_asks_vol = 0
    frames_count = 0
    
    # لتتبع حجم أكبر جدار شراء وبيع في كل إطار
    max_bid_walls = []
    max_ask_walls = []

    try:
        # نفتح الاتصال لمدة محددة فقط (مثلاً 4 ثوانٍ)
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            start_time = time.time()
            
            while time.time() - start_time < duration:
                try:
                    # ننتظر التحديث (إذا تأخر أكثر من ثانية نتجاوزه)
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(msg)
                    
                    bids = data.get('bids', [])
                    asks = data.get('asks', [])
                    
                    if not bids or not asks:
                        continue

                    # حساب السيولة في هذا الإطار (Frame)
                    current_bids_vol = sum(float(b[0]) * float(b[1]) for b in bids)
                    current_asks_vol = sum(float(a[0]) * float(a[1]) for a in asks)
                    
                    total_bids_vol += current_bids_vol
                    total_asks_vol += current_asks_vol
                    frames_count += 1
                    
                    # تسجيل أكبر جدار في هذه اللحظة (أكبر أوردر مفرد)
                    max_bid_walls.append(max(float(b[0]) * float(b[1]) for b in bids))
                    max_ask_walls.append(max(float(a[0]) * float(a[1]) for a in asks))

                except asyncio.TimeoutError:
                    continue # تجاهل التأخير المؤقت
                    
    except Exception as e:
        print(f"WS Depth Error for {symbol}: {e}")
        return None

    if frames_count < 10: # إذا لم نلتقط بيانات كافية (أقل من 10 لقطات)
        return None

    # --- محرك كشف التلاعب المالي (Quant Engine) ---
    
    # 1. هل هناك جدار وهمي (Spoof) ظهر واختفى فجأة؟
    # إذا كان أكبر جدار شراء مسجل أكبر بـ 5 أضعاف من متوسط الجدران الأخرى، فهذا يعني أنه جدار ظهر للحظة واختفى
    avg_bid_wall = sum(max_bid_walls) / len(max_bid_walls)
    max_recorded_bid = max(max_bid_walls)
    is_bid_spoof = max_recorded_bid > (avg_bid_wall * 5.0)

    avg_ask_wall = sum(max_ask_walls) / len(max_ask_walls)
    max_recorded_ask = max(max_ask_walls)
    is_ask_spoof = max_recorded_ask > (avg_ask_wall * 5.0)

    # 2. الخلل العام في السيولة (Orderbook Imbalance)
    avg_bids = total_bids_vol / frames_count
    avg_asks = total_asks_vol / frames_count
    imbalance = (avg_bids - avg_asks) / (avg_bids + avg_asks) if (avg_bids + avg_asks) > 0 else 0

    return {
        "imbalance": round(imbalance, 2), # من -1 (سيطرة بائعين) إلى 1 (سيطرة مشترين)
        "is_bid_spoof": is_bid_spoof,     # فخ شراء وهمي (لتصريف العملة)
        "is_ask_spoof": is_ask_spoof,     # فخ بيع وهمي (لتجميع العملة بسعر رخيص)
        "real_support": avg_bids
    }


async def get_aggregated_orderbook(client: httpx.AsyncClient, symbol: str):
    """
    جلب ودمج الأوردر بوك من 8 منصات لقراءة ضغط الحيتان
    Binance, Bybit, Gate.io, KuCoin, OKX, MEXC, Bitget, HTX
    """
    sym_binance_mexc = f"{symbol}USDT"
    sym_gate = f"{symbol}_USDT"
    sym_kucoin_okx = f"{symbol}-USDT"
    sym_htx = f"{symbol.lower()}usdt" # HTX تتطلب الحروف الصغيرة

    urls = {
        "binance": f"{get_random_binance_base()}/api/v3/depth?symbol={sym_binance_mexc}&limit=50",
        "bybit": f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={sym_binance_mexc}&limit=50",
        "gate": f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={sym_gate}&limit=50",
        "kucoin": f"https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol={sym_kucoin_okx}",
        "okx": f"https://www.okx.com/api/v5/market/books?instId={sym_kucoin_okx}&sz=50",
        "mexc": f"https://api.mexc.com/api/v3/depth?symbol={sym_binance_mexc}&limit=50",
        "bitget": f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={sym_binance_mexc}&type=step0&limit=50",
        "htx": f"https://api.huobi.pro/market/depth?symbol={sym_htx}&type=step0"
    }

    async def fetch_ob(exchange, url):
        try:
            res = await client.get(url, timeout=3.0) 
            if res.status_code == 200:
                data = res.json()
                bids_vol, asks_vol = 0.0, 0.0
                
                # 1. Binance, MEXC, Gate
                if exchange in ["binance", "mexc", "gate"]:
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in data.get("bids", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in data.get("asks", []))
                # 2. Bybit
                elif exchange == "bybit":
                    result = data.get("result", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in result.get("b", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in result.get("a", []))
                # 3. KuCoin
                elif exchange == "kucoin":
                    d = data.get("data", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", [])[:50])
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", [])[:50])
                # 4. OKX
                elif exchange == "okx":
                    d = data.get("data", [{}])[0]
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", []))
                # 5. Bitget (الجديدة)
                elif exchange == "bitget":
                    d = data.get("data", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", []))
                # 6. HTX (الجديدة)
                elif exchange == "htx":
                    d = data.get("tick", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", [])[:50])
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", [])[:50])

                return exchange, bids_vol, asks_vol
        except:
            pass 
        return exchange, 0.0, 0.0

    tasks = [fetch_ob(ex, url) for ex, url in urls.items()]
    results = await asyncio.gather(*tasks)

    total_bids_usd = 0.0
    total_asks_usd = 0.0

    # ----- الطباعة في اللوغ لمراقبة الضغط المؤسساتي -----
    print(f"\n📊 --- تفاصيل الأوردر بوك لعملة {symbol} ---", flush=True)
    for exchange, bids, asks in results:
        total_bids_usd += bids
        total_asks_usd += asks
        # طباعة المنصات التي تحتوي على بيانات فقط
        if bids > 0 or asks > 0:
            print(f"🔹 {exchange.upper():<8}: Bids = ${bids:,.0f} | Asks = ${asks:,.0f}", flush=True)
            
    print(f"🌍 الإجمالي اللحظي (8 منصات): Bids = ${total_bids_usd:,.0f} | Asks = ${total_asks_usd:,.0f}", flush=True)
    print("------------------------------------------\n", flush=True)

    # حساب نسبة الخلل (Imbalance)
    if total_asks_usd == 0:
        return 999.0 if total_bids_usd > 0 else 1.0 
    
    return total_bids_usd / total_asks_usd

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
                    params={"limit": "5000"} # جلب أهم 250 عملة
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
        await asyncio.sleep(900)


async def verify_global_liquidity(symbol: str, client: httpx.AsyncClient):
    """
    التحقق من أن الفوليوم الانفجاري مدعوم من منصات أخرى 
    (Gate.io, Bybit, OKX) لتأكيد أن التجميع حقيقي وليس وهمياً.
    """
    clean_symbol = symbol.replace("_", "").replace("-", "")
    
    # تجهيز الروابط (نطلب شمعة اليوم فقط لتقليل الضغط)
    urls = {
        "gate": f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={clean_symbol}_USDT&interval=1d&limit=2",
        "bybit": f"https://api.bybit.com/v5/market/kline?category=spot&symbol={clean_symbol}USDT&interval=D&limit=2",
        "okx": f"https://www.okx.com/api/v5/market/candles?instId={clean_symbol}-USDT&bar=1D&limit=2"
    }

    async def fetch_vol(exchange, url):
        try:
            res = await client.get(url, timeout=4.0)
            if res.status_code == 200:
                data = res.json()
                if exchange == "gate" and data:
                    return float(data[0][1]) # الفوليوم في Gate
                elif exchange == "bybit" and data.get("result", {}).get("list"):
                    return float(data["result"]["list"][0][5]) # الفوليوم في Bybit
                elif exchange == "okx" and data.get("data"):
                    return float(data["data"][0][5]) # الفوليوم في OKX
        except Exception:
            pass
        return 0.0

    tasks = [fetch_vol(ex, url) for ex, url in urls.items()]
    results = await asyncio.gather(*tasks)
    
    total_alt_volume = sum(results)
    return total_alt_volume
def detect_smart_money_absorption(df):
    """
    اكتشاف التجميع المؤسساتي باستخدام CVD من بيانات بايننس
    """
    df["taker_buy_vol"] = pd.to_numeric(df["taker_buy_vol"], errors='coerce')
    df["taker_sell_vol"] = df["volume"] - df["taker_buy_vol"]
    
    df["delta"] = df["taker_buy_vol"] - df["taker_sell_vol"]
    df["cvd"] = df["delta"].cumsum()

    recent = df.tail(20)
    
    price_change_pct = (recent["close"].iloc[-1] - recent["close"].iloc[0]) / recent["close"].iloc[0]
    cvd_change = recent["cvd"].iloc[-1] - recent["cvd"].iloc[0]
    
    score_boost = 0.0
    signal_upgrade = None

    # 🟢 تم تخفيض السكور ليكون منطقياً وضبط اسم الإشارة الداخلي
        # تعديل: مقارنة الـ CVD بمتوسط الحجم بنسبة منطقية (0.5 بدلاً من 3 أضعاف المستحيلة)
    # 1. تجميع شرائي صامت (نطاق ضيق)
    if abs(price_change_pct) <= 0.015 and cvd_change > (recent["volume"].mean() * 0.3):
        score_boost = 25.0 
        signal_upgrade = "Whale_CVD"
    # 2. الامتصاص (Limit Absorption): الناس تبيع (CVD سلبي) والسعر يرفض الهبوط!
    elif price_change_pct >= -0.01 and cvd_change < -(recent["volume"].mean() * 0.4):
        score_boost = 20.0
        signal_upgrade = "Limit_Absorption"

    elif price_change_pct > 0.05 and cvd_change < 0:
        score_boost = -20.0
        signal_upgrade = "Fake_Pump"

    return score_boost, signal_upgrade

async def get_futures_liquidity(symbol: str, client: httpx.AsyncClient, current_price: float, old_price: float):
    fapi_base = "https://fapi.binance.com"
    pair = f"{symbol}USDT"

    try:
        oi_url = f"{fapi_base}/futures/data/openInterestHist?symbol={pair}&period=15m&limit=2"
        funding_url = f"{fapi_base}/fapi/v1/premiumIndex?symbol={pair}"

        oi_res, fund_res = await asyncio.gather(
            client.get(oi_url, timeout=3.0),
            client.get(funding_url, timeout=3.0)
        )

        if oi_res.status_code == 200 and fund_res.status_code == 200:
            oi_data = oi_res.json()
            fund_data = fund_res.json()

            if len(oi_data) < 2: return 0.0, None

            old_oi = float(oi_data[0]["sumOpenInterest"])
            current_oi = float(oi_data[-1]["sumOpenInterest"])
            oi_change_pct = (current_oi - old_oi) / old_oi
            price_change_pct = (current_price - old_price) / old_price
            funding_rate = float(fund_data.get("lastFundingRate", 0.0))

            score_modifier = 0.0
            futures_signal = None

            # 🟢 تم تخفيض السكور وضبط الـ Tags
            if price_change_pct > 0.01 and oi_change_pct > 0.02: 
                score_modifier += 15.0
                futures_signal = "OI_Rising"
            elif price_change_pct > 0.01 and oi_change_pct < -0.02:
                score_modifier -= 25.0
                futures_signal = "Short_Covering"
            
            if funding_rate < -0.0005: 
                score_modifier += 12.0
                if not futures_signal: futures_signal = "Short_Squeeze"
            elif funding_rate > 0.0005:
                score_modifier -= 10.0

            return score_modifier, futures_signal
    except Exception: pass
    return 0.0, None

def calculate_volume_zscore(df, window=720):
    """
    محرك الشذوذ الإحصائي (Volume Z-Score).
    window=720 لأننا نستخدم فريم 1h (24 ساعة * 30 يوم = 720 شمعة).
    """
    # حساب المتوسط المتحرك (Mean) للفوليوم لآخر 30 يوم
    rolling_mean = df["volume"].rolling(window=window, min_periods=100).mean()
    
    # حساب الانحراف المعياري (Standard Deviation)
    rolling_std = df["volume"].rolling(window=window, min_periods=100).std(ddof=0)
    
    # تطبيق معادلة Z-Score
    df["z_score"] = (df["volume"] - rolling_mean) / rolling_std
    
    current_z = df["z_score"].iloc[-1]
    last_mean = rolling_mean.iloc[-1]
    last_std = rolling_std.iloc[-1]
    
    # حماية من القسمة على صفر في العملات الميتة جداً
    if pd.isna(current_z) or current_z == float('inf'):
        current_z = 0.0

    return current_z, last_mean, last_std
def process_dataframe_sync(candles_data):
    """دالة خارجية لمعالجة البيانات بدون تجميد البوت"""
    df = pd.DataFrame(candles_data)
    df = df.iloc[:, :7] 
    df.columns = ["timestamp", "volume", "close", "high", "low", "open", "taker_buy_vol"]
    for col in df.columns: 
        df[col] = pd.to_numeric(df[col], errors='coerce')

    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss = (-1 * delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + (gain / loss)))
    last_rsi_val = df["rsi"].iloc[-1]
    
    try: 
        current_adx_val = float(ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14, fillna=True).adx().iloc[-1])
    except: 
        current_adx_val = 0.0

    current_z_val, vol_mean_val, vol_std_val = calculate_volume_zscore(df, window=720)
    
    return df, last_rsi_val, current_adx_val, current_z_val, vol_mean_val, vol_std_val

async def analyze_radar_coin(c, client, market_regime, sem):
    async with sem:  
        try:
            symbol = c["symbol"]
            price = float(c["quote"]["USD"]["price"])
            
            candles = await get_candles_binance(f"{symbol}USDT", "1h", limit=750)
            if not candles: return None

                        # --- هذا هو الكود البديل (سطر واحد يستدعي الدالة اللي فوق في الخلفية) ---
            df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)

            # 🟢 البداية من سكور 20 لتوزيع النسب باحترافية
            score = 20.0
            tags = [] # قائمة لتجميع نوع الحركات
            
            pool = dp['db_pool']
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO market_memory (symbol, vol_mean, vol_stddev, z_score, last_updated)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                    ON CONFLICT (symbol) DO UPDATE 
                    SET vol_mean = EXCLUDED.vol_mean, vol_stddev = EXCLUDED.vol_stddev, z_score = EXCLUDED.z_score, last_updated = CURRENT_TIMESTAMP
                """, symbol, vol_mean, vol_std, current_z)

            # 1. فلتر الفوليوم
            # 1. فلتر الفوليوم (بمنطق تجنب الـ FOMO)
# نحسب حركة السعر في آخر 10 شموع لنتأكد أننا لا نشتري قمة
                        # 1. فلتر الفوليوم (بمنطق تجنب الـ FOMO)
            recent_pump = (df["close"].iloc[-1] - df["close"].iloc[-10]) / df["close"].iloc[-10]
            
            if current_z >= 4.0:
                if recent_pump > 0.05: 
                    score -= 15.0 
                    tags.append("Late_FOMO")
                else: 
                    score += 25.0 
                    tags.append("Z_Anom_Silent")
            
            elif 1.0 <= current_z <= 3.5: # خفضنا النطاق الذهبي ليبدأ من 1.0
                score += 20.0
                tags.append("Smart_Accumulation")
            
            elif current_z < -0.5: # بدل 0، نعطي مساحة للعملات الهادئة جداً
                return None


            # 2. فلتر CVD
            micro_cvd_boost, micro_cvd_signal = await get_micro_cvd_absorption(symbol, client)
            score += micro_cvd_boost
            if micro_cvd_signal: 
                tags.append(micro_cvd_signal)
            # 3. فلتر المشتقات
            old_price_val = df["close"].iloc[-3] if len(df) > 3 else df["open"].iloc[0]
            futures_boost, futures_signal = await get_futures_liquidity(symbol, client, price, old_price_val)
            score += futures_boost
            if futures_signal: tags.append(futures_signal)

            # 4. المؤشرات الكلاسيكية
            sma20 = df["close"].rolling(20).mean()
            std20 = df["close"].rolling(20).std(ddof=0)
            df["upper_band"] = sma20 + 2*std20
            df["lower_band"] = sma20 - 2*std20
            squeeze_pct = (df["upper_band"].iloc[-1] - df["lower_band"].iloc[-1]) / sma20.iloc[-1]
            
            if squeeze_pct < 0.05: 
                score += 10.0
                tags.append("Squeeze")
            
            ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            if price < ema200_val and last_rsi > 30 and df["rsi"].iloc[-10:-1].min() < 30:
                score += 10.0 
                tags.append("RSI_Div")

            # 5. الفحص العميق (Order Flow + Global)            # 5. الفحص العميق (Order Flow + Global)            # 5. الفحص العميق (Order Flow + Global)# --- استبدال الفحص العميق رقم 5 بالتالي ---            # 5. الفحص العميق (Order Flow + Global)
            if score >= 35.0:
                # 🔴 التحديث الجديد: تشغيل فيديو الأوردر بوك لمدة 4 ثوانٍ
                depth_data = await detect_flash_spoofing_ws(symbol, duration=4.0)
                
                if depth_data:
                    # 🚫 كشف فخ الشراء: حوت يضع جدار شراء ضخم ويسحبه ليوهمنا بالصعود (يصرف علينا)
                    if depth_data['is_bid_spoof']:
                        score -= 25.0 
                        tags.append("Flash_Spoofing_Manipulation (Trap)")
                    
                    # ✅ كشف فخ البيع: حوت يضع جدار بيع ضخم ويسحبه لكي يضغط السعر ويجمع براحته
                    elif depth_data['is_ask_spoof'] and micro_cvd_boost > 0:
                        score += 20.0 
                        tags.append("Whale_Spoofing_Accumulation")
                    
                    # الخلل الحقيقي المستقر لصالح المشترين
                    elif depth_data['imbalance'] > 0.3:
                        score += 15.0 
                        tags.append("OB_Buy")
                    elif depth_data['imbalance'] < -0.3:
                        score -= 15.0 # هروب مبكر لو البائعين مسيطرين


            
                # فحص السيولة المؤسساتية لآخر 15 دقيقة
                delta_usd, buy_v, sell_v = await get_institutional_orderflow(symbol, client, minutes=15)
                
                # إذا كان حجم الشراء ضعف حجم البيع، والسيولة ضخمة (أكثر من نصف مليون دولار في 15 دقيقة)
                if buy_v > (sell_v * 2.0) and buy_v > 500_000:
                    score += 25.0
                    tags.append("Institutional_Buy_Spike")
                elif sell_v > (buy_v * 1.5):
                    score -= 20.0 # هروب مبكر
            
                # فحص السيولة العالمية
                global_ob_pressure = await get_aggregated_orderbook(client, symbol)
                global_alt_volume = await verify_global_liquidity(symbol, client)
                if global_ob_pressure >= 1.5: score += 10.0
                elif global_ob_pressure < 0.8: score -= 15.0

                if global_alt_volume > 100000: score += 10.0

            # 6. الماكرو
            if isinstance(market_regime, dict):
                if market_regime['trend'] == "Trending_Bear" and ("Z_Anom" in tags or "OI_Rising" in tags):
                    score -= 15.0
                elif market_regime['trend'] == "Trending_Bull":
                    score += 5.0

            # 🟢 ضبط السكور ليكون مستحيلاً وصوله 100 (أقصى شيء بالواقع 95-97)
            score = round(max(0.0, min(score, 98.5)), 1)
            
            # 🟢 محرك تسمية الإشارة الذكي (Short & Punchy Signal Names)
                        # 🟢 محرك تسمية الإشارة الذكي (المصحح والمطور)
            final_signal = "High Probability Setup 🎯"
            
            # 1. إشارات الخطر (تمنع إرسال العملة)
            if "Fake_Pump" in tags or "Flash_Spoofing_Manipulation" in tags or "Short_Covering" in tags or "Late_FOMO" in tags or "Hidden_Distribution" in tags:
                return None 
                
            # 2. الإشارات الأسطورية (سكور فوق 90)
            if score >= 90.0:
                final_signal = "Massive Institutional Accumulation 🐋"
            
            # 3. الإشارات القوية المحددة (تم تصحيحها)
            elif "Micro_Silent_Accumulation" in tags and "Institutional_Buy_Spike" in tags: 
                final_signal = "Aggressive Whale Accumulation 🐋"
            elif "Short_Squeeze" in tags: 
                final_signal = "(Short Squeeze) 🔥"
            elif "Z_Anom_Silent" in tags and "OI_Rising" in tags: 
                final_signal = "(Derivatives Pump) 🚀"
            elif "Squeeze" in tags and "OB_Buy" in tags: 
                final_signal = "(Liquidity Breakout) ⚡"
            elif "Micro_Silent_Accumulation" in tags or "Wall_Absorption_Pre_Breakout" in tags: 
                final_signal = "Silent Institutional Accumulation 🧲"
            elif "Z_Anom_Silent" in tags or "Smart_Accumulation" in tags: 
                final_signal = "Smart Money Inflow 💸"

            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
            current_vol_ratio = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0

            # 🟢 1. تحديد الإشارات الحيوية (تم تصحيح المفاتيح الذهبية)
            golden_tags = {"Z_Anom_Silent", "Smart_Accumulation", "Micro_Silent_Accumulation", "Institutional_Buy_Spike", "OB_Buy", "Squeeze"}
            
            # كم إشارة ذهبية اجتمعت في هذه العملة؟
            confluence_count = sum(1 for tag in tags if tag in golden_tags)

            # 🟢 2. فلتر الاتجاه المعاكس (لا تشتري سكين تسقط)
            is_macro_downtrend = price < ema200_val

            # 🟢 3. شروط القناص النهائي:
            required_score = 65.0 if (market_regime['trend'] == "Trending_Bear" or is_macro_downtrend) else 55.0
            required_confluence = 2 if (market_regime['trend'] == "Trending_Bear" or is_macro_downtrend) else 1

            # إرجاع النتيجة فقط إذا تحقق السكور + الإجماع الفني
                        # --- 🧠 التكامل مع الذكاء الاصطناعي وبيانات السلسلة ---
            if score >= required_score and confluence_count >= required_confluence:    
                
                # 1. جلب بيانات السلسلة (On-Chain)
                whale_inflow = await get_whale_inflow_score()
                
                # 2. تجهيز الملامح (Features) للذكاء الاصطناعي
                ml_features = {
                    'z_score': float(current_z),
                    'cvd': 1.0 if "Micro_Silent_Accumulation" in tags else 0.0,
                    'imbalance': float(locals().get('global_ob_pressure', 0)),
                    'adx': float(current_adx),
                    'rsi': float(last_rsi),
                    'whale_inflow_score': float(whale_inflow)
                }
                
                # 3. سؤال الذكاء الاصطناعي عن رأيه (في مسار خلفي)
                ai_probability = await asyncio.to_thread(predict_signal_sync, ml_features)
                
                # 4. قرار الفلترة المزدوج:
                # إذا كان الـ AI متدرباً، نعتمد على رأيه (مثلاً يرفض أي صفقة احتمال نجاحها أقل من 60%)
                if ai_probability != -1.0:
                    if ai_probability < 60.0:
                        return None # الذكاء الاصطناعي يرفض الصفقة رغم أن السكور الكلاسيكي عالي!
                    final_score = ai_probability # نستبدل السكور القديم بنسبة النجاح الذكية
                    ai_status = "Active 🧠"
                else:
                    final_score = score # نستخدم السكور القديم لأن الـ AI يجمع البيانات حالياً
                    ai_status = "Learning ⏳"
                
                return {
                    "symbol": symbol, "price": price, "score": final_score,
                    "rsi": round(last_rsi, 2), "adx": round(current_adx, 2),
                    "macd": current_z, 
                    "vol_ratio": round(current_vol_ratio, 2),
                    "ob_pressure": round(locals().get('global_ob_pressure', 1.0), 2),
                    "signal_type": final_signal,
                    "confluence": confluence_count,
                    "ml_features": ml_features, # تمريرها لكي يتم تسجيلها لاحقاً
                    "ai_status": ai_status
                }
            return None  
        except Exception as e:
            print(f"Error in analyze_radar_coin: {e}")
            return None  



async def handle_binance_rate_limit(retry_after: int = 60):
    """توقف الرادار بالكامل عند استقبال 429 لمنع حظر 418"""
    # نتأكد أن الإشارة خضراء حتى لا نقوم بتشغيل المؤقت أكثر من مرة
    if binance_rate_limit_event.is_set():
        print(f"⚠️ [نظام الحماية] بايننس أرسلت تحذير (429)! إيقاف جميع الطلبات لمدة {retry_after} ثانية...")
        
        # تحويل الإشارة إلى حمراء (تجميد كل المهام التي تنتظر الإشارة)
        binance_rate_limit_event.clear() 
        
        # ننتظر الفترة المطلوبة من بايننس
        await asyncio.sleep(retry_after)
        
        print("🟢 [نظام الحماية] انتهاء فترة التوقف. استئناف عمل الرادار...")
        
        # إرجاع الإشارة خضراء (تستيقظ جميع المهام وتكمل عملها تلقائياً)
        binance_rate_limit_event.set() 
async def log_signal_for_ml(pool, symbol: str, price: float, features: dict):
    """
    تسجيل بيانات الإشارة في قاعدة البيانات فور التقاطها من الرادار (حتى قبل موافقة الأدمن).
    يمنع التكرار: لا يسجل نفس العملة إذا تم تسجيلها في آخر 4 ساعات.
    """
    async with pool.acquire() as conn:
        # 🛡️ فلتر التكرار الذكي: إذا وصلت العملة للأدمن وعمل مسح (Clear)، لن تسجل كبيانات مكررة
        exists = await conn.fetchval("""
            SELECT 1 FROM ml_training_data 
            WHERE symbol = $1 AND signal_time > CURRENT_TIMESTAMP - INTERVAL '4 hours'
        """, symbol)
        
        if exists:
            return # العملة مسجلة حديثاً، تجاهل التسجيل المزدوج لحماية جودة التدريب

        await conn.execute("""
            INSERT INTO ml_training_data 
            (symbol, entry_price, z_score, cvd, imbalance, adx, rsi)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, 
        symbol, price, 
        features.get('z_score', 0.0), features.get('cvd', 0.0), 
        features.get('imbalance', 0.0), features.get('adx', 0.0), features.get('rsi', 0.0))
        print(f"🧠 [ML Logger] Data captured for {symbol} at ${price}")

async def ml_inspector_worker(pool):
    """
    عامل خلفي يعمل بصمت. يفحص الإشارات التي مر عليها 4 ساعات.
    يحسب أقصى قمة وصل لها السعر (Highest High) لتحديد الربح الفعلي.
    """
    await asyncio.sleep(120) # انتظار دقيقتين بعد تشغيل السيرفر ليهدأ
    print("🕵️‍♂️ ML Inspector is running in the background...")
    
    while True:
        try:
            async with pool.acquire() as conn:
                # جلب العملات المعلقة التي مر عليها أكثر من 4 ساعات
                pending = await conn.fetch("""
                    SELECT id, symbol, entry_price, EXTRACT(EPOCH FROM signal_time) as sig_ts
                    FROM ml_training_data 
                    WHERE label = -1 AND signal_time <= CURRENT_TIMESTAMP - INTERVAL '4 hours'
                """)
                
                if not pending:
                    await asyncio.sleep(600) # ينام 10 دقائق إذا لم يجد شيئاً
                    continue
                    
                async with httpx.AsyncClient(timeout=10) as client:
                    for row in pending:
                        sym = f"{row['symbol']}USDT"
                        entry = row['entry_price']
                        start_time_ms = int(row['sig_ts'] * 1000)
                        
                        # جلب شموع 15 دقيقة لتغطي الـ 4 ساعات التالية للإشارة
                        base_url = get_random_binance_base()
                        res = await client.get(
                            f"{base_url}/api/v3/klines",
                            params={"symbol": sym, "interval": "15m", "startTime": start_time_ms, "limit": 20}
                        )
                        
                        if res.status_code == 200:
                            klines = res.json()
                            if not klines: continue
                            
                            # استخراج أعلى قمة وصل لها السعر في الـ 4 ساعات (الرقم 2 هو الـ High)
                            highest_high = max([float(k[2]) for k in klines])
                            
                            # حساب أقصى نسبة ربح
                            max_profit_pct = ((highest_high - entry) / entry) * 100
                            
                            # 🎯 التقييم: إذا صعدت 2% فأكثر نعتبرها صفقة ناجحة (1)، غير ذلك فاشلة (0)
                            label = 1 if max_profit_pct >= 2.0 else 0
                            
                            await conn.execute("""
                                UPDATE ml_training_data 
                                SET max_profit_pct = $1, label = $2 
                                WHERE id = $3
                            """, max_profit_pct, label, row['id'])
                            
                            print(f"📊 [ML Labeling] {sym} evaluated. Max Profit: {max_profit_pct:.2f}%. Label: {label}")
                            
                        await asyncio.sleep(0.5) # حماية الـ API
                        
        except Exception as e:
            print(f"⚠️ ML Inspector Error: {e}")
            
        await asyncio.sleep(600) # إعادة الفحص كل 10 دقائق

async def ai_opportunity_radar(pool):
    print("🚀 تم تشغيل الرادار الشامل (وضع صيد القيعان)...")
    sem = asyncio.Semaphore(5)
    
    while True:
        try:
            print("🔍 جاري جلب 1000 عملة للبحث عن الجواهر المنسية...")
            STABLE_COINS = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD"}

            async with pool.acquire() as conn:
                records = await conn.fetch("""
                    SELECT symbol FROM radar_history 
                    WHERE last_signaled > CURRENT_TIMESTAMP - INTERVAL '7 days'
                """)
                ignored_symbols = {r['symbol'] for r in records}

            async with httpx.AsyncClient(timeout=30) as client:
                
                # 👇👇 هذا هو السطر الجديد (نقطة التفتيش / البريك) 👇👇
                await binance_rate_limit_event.wait()
                
                # 🟢 التعديل هنا: جلب بيانات الماكرو الجديدة بدل البوليان القديم
                market_regime = await detect_market_regime(client)
                
                # جلب بيانات بايننس اللحظية (24hr Ticker) بدون أي تأخير
                                # جلب بيانات بايننس اللحظية (24hr Ticker) بدون أي تأخير
                base_url = get_random_binance_base()
                res = await client.get(f"{base_url}/api/v3/ticker/24hr", timeout=10)

                
                if res.status_code != 200:
                    await bot.send_message(ADMIN_USER_ID, "❌ فشل الاتصال بـ Binance API. سيتم إعادة المحاولة...")
                    await asyncio.sleep(60)
                    continue
                
                all_tickers = res.json()
                coins = []
                
                for t in all_tickers:
                    symbol = t["symbol"]
                    if not symbol.endswith("USDT"): continue # نأخذ أزواج التيثر فقط
                    
                    clean_sym = symbol.replace("USDT", "")
                    if clean_sym in STABLE_COINS or clean_sym in ignored_symbols or clean_sym in BLACKLISTED_COINS: 
                        continue
                    
                    vol_usd = float(t["quoteVolume"])
                    price_change = float(t["priceChangePercent"])
                    
                    # 🟢 الفلترة السحرية: 
                    if vol_usd >= 1_000_000 and -10.0 <= price_change <= 5.0:
                        # تغليف البيانات لتطابق هيكلة كودك القديمة تماماً عشان ما ينكسر دالة التحليل
                        coins.append({
                            "symbol": clean_sym,
                            "quote": {"USD": {"price": float(t["lastPrice"])}},
                            "volume": vol_usd # 👈 أضفنا الفوليوم هنا للترتيب
                        })
                
                # 👈 التعديل هنا: نرتب باستخدام x["volume"] 
                                # 👈 التعديل هنا: نرتب باستخدام x["volume"] 
                coins = sorted(coins, key=lambda x: x["volume"], reverse=True)[:300]
                
                # ✅ التعديل الجديد: إرسال الطلبات بالتدريج لحماية الـ API من الحظر
                tasks = []
                for c in coins:
                    await asyncio.sleep(0.2) # استراحة 200 ملي ثانية بين كل عملة
                    task = asyncio.create_task(analyze_radar_coin(c, client, market_regime, sem))
                    tasks.append(task)
                    
                results = await asyncio.gather(*tasks)
                
                valid_signals = [r for r in results if r is not None]
                valid_signals.sort(key=lambda x: x['score'], reverse=True)

                if not valid_signals:
                    print("😴 لم يتم العثور على فرص حالياً... إعادة البحث التلقائي بعد 15 دقائق.")
                    await asyncio.sleep(250)
                    continue

                # تجهيز الرسالة للأدمن لأقوى عملة
                best_meta = valid_signals[0]
                best_score = best_meta['score']
                symbol = best_meta['symbol']
                price = best_meta['price']
                signal = best_meta.get('signal_type', "🎯 BOTTOM SNIPED") 

                async with pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO radar_history (symbol, last_signaled)
                        VALUES ($1, CURRENT_TIMESTAMP)
                        ON CONFLICT (symbol) DO UPDATE
                        SET last_signaled = CURRENT_TIMESTAMP
                    """, symbol)

                                # تنسيق الأرقام لضمان عدم ظهور أرقام طويلة جداً
                z_score_val = f"{best_meta['macd']:.2f}"
                vol_ratio_val = f"{best_meta['vol_ratio']:.2f}"
                ob_pressure_val = f"{best_meta.get('ob_pressure', 1.0):.2f}"

                prompt_ar = f"""
أنت محلل كمي (Quant) في NaiF CHarT Intelligence Lab.
اكتب تقرير فحص سريع لعملة {symbol} بناءً على هذه المعطيات:
- Z-Score (مؤشر التجميع الصامت): {z_score_val}
- تدفق السيولة الذكية: أعلى بـ {vol_ratio_val} ضعف.
- ضغط الأوردر بوك: المشترون أقوى بـ {ob_pressure_val}x.
- قوة الإجماع التقني: {best_meta.get('confluence', 0)}
- مؤشرات الاتجاه: ADX: {best_meta['adx']} | RSI: {best_meta['rsi']}

التعليمات الصارمة:
1. التنويع: لا تكرر نفس العبارات في كل تحليل. استخدم زوايا مختلفة (مرة ركز على امتصاص العروض، ومرة على الشراء الهجومي، ومرة على جفاف السيولة البيعية).
2. اكتب 3 نقاط قصيرة جداً (لا تتجاوز 3 أسطر) باستخدام HTML (•).
3. لا تقم بسرد الأرقام كما هي بشكل ممل، بل ادمجها في التحليل (مثلاً: "تضاعف السيولة بـ {vol_ratio_val} مرات يؤكد...").
4. ممنوع استخدام كلمات الإثارة (انفجار، صاروخ، فرصة ذهبية، هائل). حافظ على لغة مؤسساتية جافة.

الناتج باللغة العربية فقط:
"""

                prompt_en = f"""
You are a Quant Analyst at NaiF CHarT Intelligence Lab.
Write a dynamic, real-time brief for {symbol} based on these metrics:
- Z-Score (Silent Accumulation): {z_score_val}
- Smart Money Inflow: {vol_ratio_val}x higher.
- Orderbook Pressure: Buyers dominate by {ob_pressure_val}x.
- Technical Confluence: {best_meta.get('confluence', 0)}
- Trend Indicators: ADX: {best_meta['adx']} | RSI: {best_meta['rsi']}

Strict Instructions:
1. Variety: Do not use the same phrasing every time. Vary your analytical angle (e.g., focus on supply absorption, aggressive market buying, or liquidity dry-up).
2. Write exactly 3 short bullet points using HTML (•). Maximum 3 lines total.
3. Do not just robotically list the numbers. Weave them into the analysis (e.g., "A {vol_ratio_val}x volume spike indicates...").
4. ZERO hype words (moon, massive, explosion, huge). Keep a dry, institutional tone.

Output in English only:
"""


                insight_ar = await ask_groq(prompt_ar, lang="ar")
                insight_en = await ask_groq(prompt_en, lang="en")

                signal_id = str(uuid.uuid4())[:8] 
                radar_pending_approvals[signal_id] = {
                    "symbol": symbol, "price": price, "signal": signal, "score": best_score,
                    "insight_ar": insight_ar, "insight_en": insight_en
                }
                # 🧠 [التحديث الجديد]: تسجيل البيانات للذكاء الاصطناعي فور وصولها للرادار (قبل الموافقة)
                ml_features = {
                    'z_score': float(best_meta.get('macd', 0)), # استخدمنا MACD لأنك خزنته كـ z-score
                    'cvd': 1.0 if "Micro_Silent_Accumulation" in tags else 0.0, # تبسيط مبدئي
                    'imbalance': float(best_meta.get('ob_pressure', 0)),
                    'adx': float(best_meta.get('adx', 0)),
                    'rsi': float(best_meta.get('rsi', 0))
                }
                # تشغيل التسجيل في الخلفية لكي لا يؤخر إرسال الرسالة للأدمن
                asyncio.create_task(log_signal_for_ml(pool, symbol, price, best_meta['ml_features']))

                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ موافقة ونشر للمشتركين", callback_data=f"rad_app_{signal_id}")],
                    [InlineKeyboardButton(text="❌ إلغاء وتجاهل", callback_data=f"rad_rej_{signal_id}")]
                ])

                admin_text = (
                    f"⚠️ <b>تنبيه أدمن: قناص القيعان أنهى المسح 🎯</b>\n"
                    f"🏆 <b>أفضل عملة:</b> #{symbol}\n"
                    f"💵 السعر: ${format_price(price)}\n"
                    f"⚡ نوع التجميع: {signal}\n"
                    f"📊 السكور: <b>{best_score}/100</b>\n\n"
                    f"📝 <b>التحليل:</b>\n{insight_ar}\n\n"
                    f"هل تريد الموافقة على نشرها؟"
                )

                await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                print(f"✅ تم اصطياد قاع {symbol} بسكور {best_score}!")
                
                # 🟢 التعديل هنا: حذفنا break ووضعنا استراحة 5 دقائق (300 ثانية)
                print("⏱️ الرادار يدخل في استراحة لمدة 5 دقائق قبل بدء البحث التالي...")
                await asyncio.sleep(300) 


        except Exception as e:
            print(f"Radar Error: {e}")
            await asyncio.sleep(60)
            continue
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

@dp.message(Command("clear_radar"))
async def clear_radar_memory_cmd(m: types.Message):
    # التأكد أن الأمر للأدمن فقط
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ لا تملك صلاحية استخدام هذا الأمر.")
    
    pool = dp['db_pool']
    async with pool.acquire() as conn:
        # مسح جميع العملات المسجلة في الذاكرة
        await conn.execute("DELETE FROM radar_history")
    
    await m.answer("🧹 <b>تم تنظيف ذاكرة الرادار بنجاح!</b>\nالرادار الآن جاهز لاصطياد أي عملة قوية حتى لو قام بإرسالها مسبقاً في الأيام الماضية.", parse_mode=ParseMode.HTML)

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

async def search_dex_coin(symbol: str, retries: int = 3):
    """تبحث عن العملة في DexScreener مع نظام حماية من الحظر اللحظي"""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(retries):
            try:
                res = await client.get(url)
                
                if res.status_code == 200:
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
                    return None # العملة غير موجودة فعلاً
                    
                elif res.status_code == 429: # حظر مؤقت من DexScreener
                    await asyncio.sleep(2)
                    continue
                    
            except Exception as e:
                if attempt == retries - 1:
                    print(f"DexScreener Error after 3 attempts: {e}")
                await asyncio.sleep(1)
                
    return None


import pandas as pd

async def get_candles_dex(network: str, pool_address: str, interval: str, limit: int = 500, retries: int = 3):
    """تجلب الشموع من GeckoTerminal مع تجميع الشموع الأسبوعية إذا طلبها المستخدم"""
    
    # 1. تحديد الفريم الذي سنسحبه من الـ API
    if interval == "1w":
        timeframe, aggregate = "day", 1
        fetch_limit = 500 # نسحب 500 يوم لتكوين حوالي 71 شمعة أسبوعية
    elif interval == "1d" or interval == "daily":
        timeframe, aggregate = "day", 1
        fetch_limit = limit
    else: 
        timeframe, aggregate = "hour", 4
        fetch_limit = limit

    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit={fetch_limit}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json;version=20230302"
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(retries):
            try:
                res = await client.get(url, headers=headers, timeout=15)
                
                if res.status_code == 200:
                    data = res.json()
                    ohlcv_list = data["data"]["attributes"]["ohlcv_list"]
                    
                    if not ohlcv_list or len(ohlcv_list) < 3:
                        return None 
                        
                    formatted_candles = []
                    for candle in ohlcv_list:
                        t, o, h, l, c, v = candle
                        formatted_candles.append([t, v, c, h, l, o])
                        
                    # عكس الترتيب ليصبح من الأقدم للأحدث
                    formatted_candles = formatted_candles[::-1] 
                    
                    # 🟢 التجميع السحري للشموع الأسبوعية باستخدام Pandas
                    if interval == "1w":
                        df = pd.DataFrame(formatted_candles, columns=["timestamp", "volume", "close", "high", "low", "open"])
                        
                        # تحويل وقت الشمعة إلى نوع Datetime كفهرس للتجميع
                        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
                        df.set_index('datetime', inplace=True)
                        
                        # هندسة الشمعة الأسبوعية
                        resample_rules = {
                            'open': 'first',     # الافتتاح هو افتتاح يوم الإثنين
                            'high': 'max',       # الأعلى في الأسبوع
                            'low': 'min',        # الأدنى في الأسبوع
                            'close': 'last',     # الإغلاق هو إغلاق يوم الأحد
                            'volume': 'sum',     # مجموع فوليوم الأسبوع كامل
                            'timestamp': 'first' 
                        }
                        
                        # التجميع بناءً على أسبوع يبدأ يوم الإثنين (W-MON)
                        weekly_df = df.resample('W-MON').agg(resample_rules).dropna()
                        
                        # إعادة ترتيب الأعمدة كما يتوقعها البوت
                        weekly_candles = weekly_df[["timestamp", "volume", "close", "high", "low", "open"]].values.tolist()
                        return weekly_candles
                        
                    return formatted_candles
                
                elif res.status_code in [429, 403, 500, 502, 503, 504]:
                    await asyncio.sleep(2)
                    continue
                else:
                    break
                    
            except Exception as e:
                await asyncio.sleep(1)
                
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

    # --- بداية الكود الديناميكي الجديد ---
    binance_success = False
    
    async with httpx.AsyncClient() as client:
        pair = f"{sym}USDT" 
        
        # نظام المحاولات الذكي (3 محاولات لامتصاص أي تأخير أو Cold Start)
        for attempt in range(3):
            try:
                base_url = get_random_binance_base()
                res_binance = await client.get(
                    f"{base_url}/api/v3/ticker/24hr",
                    params={"symbol": pair},
                    timeout=5.0 # تايم أوت قصير عشان المحاولات تكون سريعة
                )
                
                if res_binance.status_code == 200:
                    data_binance = res_binance.json()
                    price = float(data_binance["lastPrice"])
                    volume_24h = float(data_binance["quoteVolume"])

                    user_session_data[uid] = {
                        "sym": sym, "price": price, "volume_24h": volume_24h, 
                        "lang": lang, "is_dex": False
                    }
                    binance_success = True
                    break # نجحنا! نخرج من حلقة المحاولات
                    
                elif res_binance.status_code in [400, 404]:
                    # بايننس تقول صراحة: العملة غير موجودة لدي.
                    # نخرج فوراً للبحث في الديكس دون تضييع وقت
                    break 
                    
                else:
                    # خطأ سيرفر مؤقت، ننتظر ثانية ونحاول مجدداً
                    await asyncio.sleep(1)
                    
            except httpx.RequestError:
                # خطأ انقطاع اتصال أو Timeout (يحدث غالباً أول ثواني بعد التشغيل)
                await asyncio.sleep(1)

    # إذا فشلت بايننس (سواء العملة غير موجودة، أو السيرفر واقع بعد 3 محاولات) ننتقل للديكس
    if not binance_success:
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
                f"❌ الرمز `{sym}` غير صحيح أو غير متوفر في المنصات المركزية واللامركزية." if lang=="ar" 
                else f"❌ Symbol `{sym}` is invalid or not found on CEX/DEX."
            )
            return await status_msg.edit_text(error_text, parse_mode=ParseMode.MARKDOWN)
    # --- نهاية الكود الديناميكي الجديد ---

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
async def get_candles_binance(symbol: str, interval: str, limit: int = 500, retries: int = 3):
    clean_symbol = symbol.replace("_", "") 
    
    async with httpx.AsyncClient() as client:
        for attempt in range(retries):
            # 🛑 انتظار الإشارة الخضراء قبل إرسال أي طلب لبايننس
            await binance_rate_limit_event.wait()

            try:
                base_url = get_random_binance_base()
                res = await client.get(
                    f"{base_url}/api/v3/klines",
                    params={"symbol": clean_symbol, "interval": interval, "limit": limit},
                    timeout=10
                )

                if res.status_code == 200:
                    data = res.json()
                    formatted_candles = []
                    for c in data:
                        formatted_candles.append([
                            str(int(c[0] / 1000)), c[5], c[4], c[2], c[3], c[1], c[9]
                        ])
                    return formatted_candles
                
                # 🚨 هنا يتم اصطياد التحذير قبل الحظر!
                elif res.status_code == 429:
                    # قراءة وقت الانتظار المطلوب من هيدر بايننس (أو افتراض 60 ثانية)
                    retry_after = int(res.headers.get("Retry-After", 60))
                    
                    # تفعيل حالة الطوارئ وإيقاف باقي الرادار
                    asyncio.create_task(handle_binance_rate_limit(retry_after))
                    
                    # تأخير بسيط للمحاولة الحالية
                    await asyncio.sleep(2) 
                    
                elif res.status_code == 418:
                    print("❌ [كارثة] حظر IP كامل (418)! خادمك محظور من بايننس.")
                    # إيقاف إجباري لمدة 5 دقائق على الأقل لمحاولة تهدئة السيرفر
                    asyncio.create_task(handle_binance_rate_limit(300))
                    return None
                
                else:
                    # ✅ التعديل هنا: منعنا البوت من الاستسلام وطبعنا الخطأ في الكونسول لمعرفة السبب
                    print(f"⚠️ فشل جلب الشموع (المحاولة {attempt+1}): {res.status_code} - {res.text}")
                    await asyncio.sleep(1) 
                    
            except Exception as e:
                if attempt == retries - 1:
                    print(f"Error fetching Binance candles for {clean_symbol}: {e}")
                await asyncio.sleep(1)
                
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
def detect_nearest_fvg(df, current_price, trend_direction):
    """
    محرك اكتشاف فجوات السيولة (Fair Value Gaps - FVG).
    يبحث عن الفجوات غير المغلقة في آخر 30 شمعة لتحديد الأهداف المغناطيسية للخوارزميات.
    """
    # نأخذ آخر 30 شمعة لنبحث عن الفجوات الطازجة
    recent = df.tail(30).reset_index(drop=True)
    fvgs = []
    
    # فحص كل 3 شموع متتالية
    for i in range(2, len(recent)):
        c1_high = recent.loc[i-2, 'high']
        c1_low = recent.loc[i-2, 'low']
        c3_high = recent.loc[i, 'high']
        c3_low = recent.loc[i, 'low']
        
        # Bullish FVG (فجوة شرائية)
        if c1_high < c3_low:
            fvgs.append({'top': c3_low, 'bottom': c1_high})
        # Bearish FVG (فجوة بيعية)
        elif c1_low > c3_high:
            fvgs.append({'top': c1_low, 'bottom': c3_high})

    if not fvgs:
        return None

    best_fvg_target = None
    min_dist = float('inf')

    for fvg in fvgs:
        mid_fvg = (fvg['top'] + fvg['bottom']) / 2
        dist = abs(current_price - mid_fvg)
        
        # إذا الترند صاعد، المغناطيس هو فجوة مفتوحة أعلى السعر الحالي
        if trend_direction == "Bullish" and current_price < fvg['bottom']:
            if dist < min_dist:
                min_dist = dist
                best_fvg_target = fvg['bottom'] # حافة الفجوة السفلى هي المغناطيس
                
        # إذا الترند هابط، المغناطيس هو فجوة مفتوحة أسفل السعر الحالي
        elif trend_direction == "Bearish" and current_price > fvg['top']:
            if dist < min_dist:
                min_dist = dist
                best_fvg_target = fvg['top'] # حافة الفجوة العليا هي المغناطيس

    return best_fvg_target

def calculate_smart_trend_and_targets(df, current_price, db_vol_change, lang="ar", override_trend=None):
    
    # 🟢 الحل الجذري: إجبار تحويل الأعمدة إلى أرقام (Floats) قبل أي عملية حسابية
    # هنا بيبدأ كودك القديم طبيعي جداً
        # 🟢 الحل الجذري: إجبار تحويل الأعمدة إلى أرقام (Floats) قبل أي عملية حسابية
    for col in ['high', 'low', 'close', 'open', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['prev_close'])
    df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    # ... وتكمل باقي الكود ...

    atr = df['tr'].rolling(14).mean().iloc[-1]

    if pd.isna(atr) or atr == 0:
        atr = current_price * 0.02 
    atr = min(atr, current_price * 0.10)

    # 🌟 2. حساب المؤشرات الهيكلية
    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    
    if len(df) >= 200:
        ema200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1] 
        macro_bull = current_price > ema200
    else:
        macro_bull = current_price > ema50

    # 🌟 حساب Anchored VWAP
    df['datetime'] = pd.to_datetime(pd.to_numeric(df['timestamp']), unit='s')
    df['date'] = df['datetime'].dt.date
    df['typical_volume'] = ((df['high'] + df['low'] + df['close']) / 3) * df['volume']
    df['cum_vol'] = df.groupby('date')['volume'].cumsum()
    df['cum_pv'] = df.groupby('date')['typical_volume'].cumsum()
    df['anchored_vwap'] = df['cum_pv'] / df['cum_vol']
    vwap_val = df['anchored_vwap'].iloc[-1]
    vwap_bull = current_price > vwap_val

    # حساب ADX الحقيقي
    try:
        adx_indicator = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14, fillna=True)
        real_adx_value = float(adx_indicator.adx().iloc[-1])
    except:
        real_adx_value = 0.0

        # التحكم في الاتجاه بناءً على أمر الرادار (إن وُجد) لمنع التضارب
    if override_trend:
        trend_direction = override_trend
        micro_bull = (trend_direction == "Bullish")
    else:
        micro_bull = ema20 > ema50
        trend_direction = "Bullish" if micro_bull else "Bearish"


    if real_adx_value < 20:
        trend_strength = "ضعيف" if lang == "ar" else "Weak"
        market_action = "تجميع عرضي للسيولة حول الفيواب اليومي" if lang == "ar" else "Liquidity struggle and accumulation around daily VWAP in a ranging market"
    else: 
        if micro_bull:
            if macro_bull and vwap_bull:
                trend_strength = ("قوي" if real_adx_value >= 40 else ("قوي" if real_adx_value >= 25 else "جيد")) if lang == "ar" else ("Strong" if real_adx_value >= 40 else ("Strong" if real_adx_value >= 25 else "Good"))
                market_action = "دخول سيولة عالية حقيقية" if lang == "ar" else "True high liquidity inflow"
            elif not macro_bull and vwap_bull:
                trend_strength = "متوسط" if lang == "ar" else "Moderate"
                market_action = "سيولة شرائية لحظية تعاكس الاتجاه العام الهابط (ارتداد)" if lang == "ar" else "Momentary buying liquidity countering the macro downtrend (Bounce)"
            elif macro_bull and not vwap_bull:
                trend_strength = "ضعيف" if lang == "ar" else "Weak"
                market_action = "صعود غير مدعوم بالسيولة" if lang == "ar" else "Upward movement lacking liquidity support"
            else:
                trend_strength = "ضعيف ومخادع" if lang == "ar" else "Weak & Fake"
                market_action = "فخ مشتريات للتعليق في القمة" if lang == "ar" else "Bull trap to catch late buyers"
        else: # Bearish
            if not macro_bull and not vwap_bull:
                trend_strength = ("قوي" if real_adx_value >= 40 else ("قوي" if real_adx_value >= 25 else "جيد")) if lang == "ar" else ("Strong" if real_adx_value >= 40 else ("Strong" if real_adx_value >= 25 else "Good"))
                market_action = "تصريف قوي" if lang == "ar" else "Strong distribution/selling pressure"
            elif macro_bull and not vwap_bull:
                trend_strength = "متوسط" if lang == "ar" else "Moderate"
                market_action = "جني أرباح طبيعي وتصحيح ضمن ترند صاعد عام" if lang == "ar" else "Natural profit-taking and correction within a macro uptrend"
            elif not macro_bull and vwap_bull:
                trend_strength = "مخادع (خطر ارتداد)" if lang == "ar" else "Fake (Bounce Risk)"
                market_action = "سيولة شرائية مؤقتة تعاكس الترند الهابط العام" if lang == "ar" else "Temporary buying liquidity countering the macro downtrend"

            else:
                trend_strength = "ضعيف ومخادع" if lang == "ar" else "Weak & Fake"
                market_action = "فخ بيعي لتخويف المتداولين" if lang == "ar" else "Bear trap to shake out retail traders"

    if db_vol_change > 80:
        market_action += " + فوليوم انفجاري" if lang == "ar" else " + Explosive Volume"
    elif db_vol_change < 15:
        market_action += " + فوليوم ميت" if lang == "ar" else " + Dead Volume"

    # ==========================================
    # 🎯 التعديل الجذري: حساب الأهداف بالـ VPVR
    # ==========================================
    sl, tp1, tp2, tp3 = calculate_vpvr_levels(df, current_price, trend_direction)

    # 🧲 تشغيل صائد فجوات السيولة (FVG)
    fvg_target = detect_nearest_fvg(df, current_price, trend_direction)
    
    if fvg_target:
        # تنسيق السعر لتجنب الأرقام الطويلة
        fvg_display = f"{fvg_target:,.4f}" if fvg_target > 1 else f"{fvg_target:.8f}"
        market_action += f" [هدف مغناطيسي عند: {fvg_display}]" if lang == "ar" else f" [Magnetic FVG Target at: {fvg_display}$]"


    try:
        support = df['low'].rolling(window=50, min_periods=1).min().iloc[-1]
        if pd.isna(support) or support <= 0:
            support = current_price * 0.90 
    except:
        support = current_price * 0.90

    try:
        resistance = df['high'].rolling(window=50, min_periods=1).max().iloc[-1]
        if pd.isna(resistance) or resistance <= 0:
            resistance = current_price * 1.10 
    except:
        resistance = current_price * 1.10

    return trend_direction, trend_strength, market_action, real_adx_value, sl, tp1, tp2, tp3, support, resistance

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
import numpy as np

def calculate_vpvr_levels(df, current_price, trend_direction, num_bins=50):
    try:
        min_price = df['low'].min()
        max_price = df['high'].max()
        price_bins = np.linspace(min_price, max_price, num_bins)
        
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['bin_index'] = np.digitize(df['typical_price'], price_bins) - 1
        vol_profile = df.groupby('bin_index')['volume'].sum()

        profile = []
        for idx, vol in vol_profile.items():
            if 0 <= idx < len(price_bins):
                profile.append({'price': price_bins[idx], 'volume': vol})

        profile_df = pd.DataFrame(profile)
        if profile_df.empty:
            raise ValueError("Empty Profile")

        above_price = profile_df[profile_df['price'] > current_price]
        below_price = profile_df[profile_df['price'] < current_price]

        # 🛡️ إعدادات الحماية وإدارة المخاطر الجديدة
        MAX_SL_PCT = 0.05  # أقصى مسافة لوقف الخسارة (5%)
        MIN_TP_PCT = 0.015 # أقل مسافة للهدف الأول (1.5%)

        if trend_direction == "Bullish":
            # فلترة الأهداف لتكون بعيدة منطقياً عن السعر الحالي
            valid_targets = above_price[above_price['price'] >= current_price * (1 + MIN_TP_PCT)]
            targets = valid_targets.nlargest(3, 'volume').sort_values('price')
            tps = targets['price'].tolist()

            support_node = below_price.nlargest(1, 'volume')
            support_price = support_node['price'].iloc[0] if not support_node.empty else current_price * 0.95

            lvns_below_support = below_price[below_price['price'] < support_price]
            if not lvns_below_support.empty:
                sl_price = lvns_below_support.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = support_price * 0.98

            # تطبيق سقف حماية الستوب
            if sl_price < current_price * (1 - MAX_SL_PCT):
                sl_price = current_price * (1 - MAX_SL_PCT)

        else: # Bearish
            valid_targets = below_price[below_price['price'] <= current_price * (1 - MIN_TP_PCT)]
            targets = valid_targets.nlargest(3, 'volume').sort_values('price', ascending=False)
            tps = targets['price'].tolist()

            res_node = above_price.nlargest(1, 'volume')
            res_price = res_node['price'].iloc[0] if not res_node.empty else current_price * 1.05

            lvns_above_res = above_price[above_price['price'] > res_price]
            if not lvns_above_res.empty:
                sl_price = lvns_above_res.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = res_price * 1.02

            # تطبيق سقف حماية الستوب
            if sl_price > current_price * (1 + MAX_SL_PCT):
                sl_price = current_price * (1 + MAX_SL_PCT)

        # ترتيب الأهداف منطقياً مع حماية النواقص إذا لم يتم العثور على 3 عقد
        if trend_direction == "Bullish":
            tp1 = tps[0] if len(tps) > 0 else current_price * (1 + MIN_TP_PCT)
            tp2 = tps[1] if len(tps) > 1 else tp1 * 1.02
            tp3 = tps[2] if len(tps) > 2 else tp2 * 1.02
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3])
            sl_price = min(sl_price, current_price * 0.99)
        else:
            tp1 = tps[0] if len(tps) > 0 else current_price * (1 - MIN_TP_PCT)
            tp2 = tps[1] if len(tps) > 1 else tp1 * 0.98
            tp3 = tps[2] if len(tps) > 2 else tp2 * 0.98
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3], reverse=True)
            sl_price = max(sl_price, current_price * 1.01)

        return sl_price, tp1, tp2, tp3

    except Exception as e:
        print(f"VPVR Error: {e}")
        # أهداف احتياطية محمية في حال فشل الحساب
        return current_price*0.95, current_price*1.02, current_price*1.04, current_price*1.06

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
        candles = await get_candles_dex(network, pool_address, gate_interval, limit=500)
    else:
        # بايننس تستخدم نفس مسميات الفريمات تقريباً
        gate_interval = {"4h":"4h", "daily":"1d", "weekly":"1w"}.get(tf, "4h")
        candles = await get_candles_binance(f"{clean_sym}USDT", gate_interval, limit=500)

    # 🛑 التغيير الموضعي: جدار حماية يوقف الدالة فوراً إذا مافي شموع كافية
    if not candles or len(candles) < 3:
        if lang == "ar":
            error_msg = f"⚠️ <b>عذراً، بيانات الإطار الزمني غير كافية لعملة {clean_sym} حالياً.</b>\n🔄 يرجى اختيار إطار زمني أقل (مثل 4 ساعات)."
        else:
            error_msg = f"⚠️ <b>Sorry, insufficient data for {clean_sym} on this timeframe.</b>\n🔄 Please choose a lower timeframe (like 4H)."
        
        try:
            return await cb.message.edit_text(error_msg, parse_mode=ParseMode.HTML)
        except Exception:
            return await cb.message.answer(error_msg, parse_mode=ParseMode.HTML)

    # 🟢 لاحظ هنا: شلنا (if candles:) وكل الأسطر اللي تحتها رجعناها لورا مسافة عشان تصير أساسية بالدالة
    # 🟢 التعديل الأول: نقل حساب المؤشرات إلى الخلفية لمنع تعليق البوت
    last_rsi, last_macd, last_bb, last_vol, _, _ = await asyncio.to_thread(compute_indicators, candles)
        
    import pandas as pd 
    df = pd.DataFrame(candles)
    df = df.iloc[:, :6]
    df.columns = ["timestamp", "volume", "close", "high", "low", "open"]

    for col in ["close", "high", "low", "open", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    # ... (كمل باقي الكود من هنا: سحب الفوليوم من الداتا بيز، وتعريف الـ prompt بدون ما تخليهم جوا if) ...


        # 🔥 سحب الفوليوم من قاعدة البيانات        # 🔥 سحب الفوليوم من قاعدة البيانات        # 🔥 حساب تغير الفوليوم الحقيقي مباشرة من بيانات بايننس (أدق وأسرع من CMC)        # 🔥 حساب تغير الفوليوم الحقيقي
        db_vol_float = 0.0
        try:
            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
            if avg_vol_20 > 0:
                db_vol_float = ((avg_vol_5 / avg_vol_20) - 1) * 100 
        except: pass

        # 1. ⚡ جلب البيانات المؤسساتية أولاً لمعرفة النية المخفية (قبل وضع الأهداف)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                old_price_val = df["close"].iloc[-3] if len(df) > 3 else price
                cvd_task = get_micro_cvd_absorption(f"{clean_sym}USDT", client)
                flow_task = get_institutional_orderflow(f"{clean_sym}USDT", client, minutes=15)
                futures_task = get_futures_liquidity(clean_sym, client, price, old_price_val)
                
                (cvd_boost, cvd_sig), (delta_usd, buy_v, sell_v), (fut_boost, fut_sig) = await asyncio.gather(
                    cvd_task, flow_task, futures_task
                )
                z_score, _, _ = calculate_volume_zscore(df, window=720)
        except Exception:
            cvd_sig, buy_v, sell_v, fut_sig, z_score = None, 0, 0, None, 0

        # 2. كشف الفخاخ وتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        classic_trend = "Bullish" if ema20 > ema50 else "Bearish"
        
        final_trend_dir = classic_trend
        
        # أ. شروط انعكاس الاتجاه من هابط إلى صاعد (اصطياد القاع)
        if classic_trend == "Bearish":
            # إما الحيتان تشتري الآن، أو المؤشرات (RSI/MACD) تؤكد ارتداداً صريحاً من القاع
            if (cvd_sig == "Micro_Silent_Accumulation" or buy_v > sell_v * 1.5) or (last_rsi < 40 and last_macd > 0):
                final_trend_dir = "Bullish" 
                
        # ب. شروط انعكاس الاتجاه من صاعد إلى هابط (الهروب من القمة المخادعة)
        elif classic_trend == "Bullish":
            # إما الحيتان تصرف الآن، أو المؤشرات تؤكد تشبعاً بيعياً مع تقاطع سلبي
            if (cvd_sig == "Hidden_Distribution" or sell_v > buy_v * 1.5) or (last_rsi > 70 and last_macd < 0):
                final_trend_dir = "Bearish" 

        # 3. حساب الدعم والمقاومة والأهداف بناءً على الاتجاه "المُوحّد" لمنع التضارب
                # 3. حساب الدعم والمقاومة والأهداف في الخلفية لمنع التضارب والتعليق
        trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = await asyncio.to_thread(
            calculate_smart_trend_and_targets, df, price, db_vol_float, lang, final_trend_dir
        )


        # 4. تكييف النص ليطابق الاكتشاف المؤسساتي بدقة
        if trend_dir == "Bullish":
            if classic_trend == "Bearish":
                trend_str = "قوي (اكتشاف فخ بيعي وانعكاس)" if lang == "ar" else "Strong (Bear Trap)"
                market_action += " | الحيتان تشتري سراً وتمتص العروض للهبوط" if lang == "ar" else " | Whales absorbing supply"
            elif fut_sig == "Short_Squeeze":
                trend_str = "انفجار سعري وشيك" if lang == "ar" else "Imminent Squeeze"
        elif trend_dir == "Bearish":
            if classic_trend == "Bullish":
                trend_str = "(تصريف مخفي في القمة)" if lang == "ar" else "(Hidden Distribution)"
                market_action += " | فخ شرائي، الحيتان تفرغ محافظها" if lang == "ar" else " | Bull trap, whales distributing"
            elif fut_sig == "Short_Covering":
                trend_str = "ضعيف - إغلاق شورت" if lang == "ar" else "Weak - Short Covering"

        # 🟢 استعادة تعريف متغيرات RSI و MACD لتجنب خطأ NameError
        macd_fmt = format_price(last_macd) if 'last_macd' in locals() and last_macd is not None else "0.0"
        safe_rsi = f"{last_rsi:.2f}" if 'last_rsi' in locals() and last_rsi is not None else "N/A"
        if lang == "ar":
            real_trend = "صاعد" if trend_dir == "Bullish" else "هابط"
            trend_strength = trend_str
            
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
•Liquidity: {market_action} (اكتب سطر يعلق على هذه الحالة بالعربية فقط ولا حرف غير عربي)
•RSI ({safe_rsi}): (اكتب سطر واحد يوضح التشبع أو الحياد بالعربية فقط ولا حرف غير عربي)
•MACD ({macd_fmt}): (اكتب سطر واحد يوضح الزخم بالعربية فقط ولا حرف غير عربي)
•ADX ({adx_val:.1f}): (اكتب سطر واحد يوضح قوة الترند بالعربية فقط ولا حرف غير عربي)
"""
        else:
            real_trend = "Bullish" if trend_dir == "Bullish" else "Bearish"
            trend_strength = trend_str
            
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
             
        # 🟢 الجديد: إنشاء جدول تتبع العملات المكتشفة في الرادار
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS radar_history (
                symbol TEXT PRIMARY KEY,
                last_signaled TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
                # 🧠 الجديد: إنشاء جدول تدريب الذكاء الاصطناعي المؤسساتي (ML Data)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ml_training_data (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                signal_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                entry_price DOUBLE PRECISION,
                z_score DOUBLE PRECISION,
                cvd DOUBLE PRECISION,
                imbalance DOUBLE PRECISION,
                adx DOUBLE PRECISION,
                rsi DOUBLE PRECISION,
                max_profit_pct DOUBLE PRECISION DEFAULT 0.0,
                label INTEGER DEFAULT -1
            )
        """)
        # فهرس لتسريع بحث العامل الخلفي (Performance Optimization)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_pending ON ml_training_data(label, signal_time)")

                # ترقية جدول الذاكرة ليعمل بنظام الشذوذ الإحصائي
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS vol_mean DOUBLE PRECISION DEFAULT 0")
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS vol_stddev DOUBLE PRECISION DEFAULT 0")
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS z_score DOUBLE PRECISION DEFAULT 0")
        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS whale_inflow_score DOUBLE PRECISION DEFAULT 0.0")
        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    asyncio.create_task(smart_radar_watchdog(pool))
    asyncio.create_task(radar_worker_process(pool))
    asyncio.create_task(ai_trainer_worker(pool)) # 🧠 تشغيل مدرب الذكاء الاصطناعي
    asyncio.create_task(ml_inspector_worker(pool)) # 🧠 تشغيل محقق الذكاء الاصطناعي
    await bot.set_webhook(f"{WEBHOOK_URL}/")


app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)