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
BLACKLISTED_COINS = {"TOMO", "EUR", "USD1", "COCOS", "LRC", "BUSD", "TUSD", "USDC", "USDE", "BFUSD", "RLUSD", "POLY", "XUSD", "U", "USDT", "DAI", "USDP", "FDUSD", "USDD", "PYUSD", "FRAX", "LUSD", "GUSD", "ZUSD", "VAI", "MAI", "DOLA", "EURC", "EURT", "EURS", "AEUR", "EURA", "TRY", "BRL", "ZAR"}
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
    قراءة توجه الحيتان الحقيقي من منصة Binance مباشرة.
    (تم التعديل لإرجاع النسبة الخام لتدريب الذكاء الاصطناعي بدقة متناهية)
    """
    try:
        url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
        params = {"symbol": "BTCUSDT", "period": "5m", "limit": 1}

        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)

            if res.status_code == 200:
                data = res.json()
                # 🟢 نرجع النسبة الحقيقية كما هي (مثلاً 1.45 أو 0.82) ليفهمها الـ AI
                return float(data[0]['longShortRatio'])

    except Exception as e:
        print(f"⚠️ Binance Whale Inflow Error: {e}")
        # 🟢 1.0 هو الرقم الصحيح هنا لأنه يعني (تعادل 50% شراء و 50% بيع) 
        # وضع 5.0 في حالة الخطأ كان سيدمر بيانات الـ AI ويجعله يعتقد أن هناك شراء جنوني!
        return 1.0 

    return 1.0

    return 5.0
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
        # تحديد المدخلات (11 بُعد مالي بدلاً من 6)
    X = df[['z_score', 'cvd', 'imbalance', 'adx', 'rsi', 'whale_inflow_score', 
            'ob_skewness', 'micro_volatility', 'cvd_divergence', 'funding_rate', 
            'volume_ratio', 'sp500_trend', 'sentiment_score']]

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
        
    # 🛠️ تم إرجاع المتغير للخلف ليكون خارج شرط الـ if
    input_data = pd.DataFrame([{
        'z_score': features.get('z_score', 0),
        'cvd': features.get('cvd_usd', 0),
        'imbalance': features.get('ofi_imbalance', 0),
        'adx': features.get('adx', 0),
        'rsi': features.get('rsi', 0),
        'whale_inflow_score': features.get('whale_inflow', 0),
        'ob_skewness': features.get('ob_skewness', 1.0),
        'micro_volatility': features.get('micro_volatility', 0.0),
        'cvd_divergence': features.get('cvd_divergence', 0.0),
        'funding_rate': features.get('funding_rate', 0.0),
        'volume_ratio': features.get('volume_ratio', 1.0),
        'sp500_trend': features.get('sp500_trend', 0.0),
        'sentiment_score': features.get('sentiment_score', 50.0)
    }])

    # استخراج احتمالية الفئة 1 (نجاح)
    prob = AI_QUANT_MODEL.predict_proba(input_data)[0][1]
    return float(prob) * 100 # إرجاع النسبة المئوية

# --- دوال الرادار المساعدة (ضعها فوق دالة الرادار) ---
async def get_recent_orderflow_delta(symbol, client, limit=500):
    """
    بديل سريع وآمن للـ WebSocket: يقرأ آخر 500 صفقة تمت لتحديد الشراء/البيع العدواني
    """
    try:
        # 🛑 حارس حماية الـ API
        await binance_rate_limit_event.wait()
        
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/trades", params={"symbol": symbol, "limit": limit})
# ... يكمل باقي الكود كما هو ...

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
                                # حساب التجميع الصامت
                                traded_usd_in_minute = current_vol - old_vol 
                                price_change = (current_price - old_price) / old_price
                                
                                MAX_PRICE_SPIKE = 0.005 

                                # 🧠 الشذوذ اللحظي الديناميكي (Dynamic Minute Spike)
                                # ضخ 0.15% من سيولة اليوم الكاملة خلال دقيقة واحدة يعتبر تجميعاً مرعباً
                                # الحد الأدنى 25 ألف دولار لتجاهل روبوتات التداول العشوائية في العملات الميتة
                                DYNAMIC_MINUTE_VOLUME = max(25_000.0, old_vol * 0.0015) 

                                if traded_usd_in_minute >= DYNAMIC_MINUTE_VOLUME and abs(price_change) <= MAX_PRICE_SPIKE:
                                    print(f"👀 Silent Accumulation Alert {symbol} | Injected: ${traded_usd_in_minute:,.0f} in {time_diff:.0f}s") 
                                    
                                    coin_mock_data = {
                                        "symbol": symbol.replace("USDT", ""),
                                        "quote": {"USD": {"price": current_price}}
                                    }
                                    await radar_processing_queue.put(coin_mock_data)
                                
                                # تصفير العداد للبدء في مراقبة الدقيقة التالية
                                live_market_memory[symbol] = {'volume': current_vol, 'price': current_price, 'last_update': current_time}

                                
                        else:
                            # إذا كانت العملة جديدة أول مرة تدخل الرادار
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
async def get_micro_cvd_absorption(symbol, client, base_interval="1m"):
    """
    يكتشف التجميع الصامت، يتأقلم مع الفريم الزمني المطلوب.
    """
    cvd_trend = 0.0 
    try:
        # 🛑 حارس حماية الـ API
        await binance_rate_limit_event.wait()
        
        # إذا كان الفريم كبير (يومي/أسبوعي)، نوسع عدسة الـ CVD لتقرأ فريم 15 دقيقة
        cvd_tf = "15m" if base_interval in ["1d", "1w"] else "1m"
        limit = 200 if cvd_tf == "15m" else 120
        
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/klines", params={
            "symbol": symbol, "interval": cvd_tf, "limit": limit
        }, timeout=5.0)
# ... يكمل باقي الكود كما هو ...

        
        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "trades", "tbv", "tqav", "ignore"])
            
            df["v"] = pd.to_numeric(df["v"])
            df["tbv"] = pd.to_numeric(df["tbv"]) 
            df["c"] = pd.to_numeric(df["c"])
            
            df["sell_vol"] = df["v"] - df["tbv"]
            df["delta"] = df["tbv"] - df["sell_vol"]
            df["cvd"] = df["delta"].cumsum()
            
            price_change = (df["c"].iloc[-1] - df["c"].iloc[0]) / df["c"].iloc[0]
            cvd_trend = df["cvd"].iloc[-1] - df["cvd"].iloc[0]
            total_vol = df["v"].sum()
            
            if abs(price_change) < 0.02 and cvd_trend > (total_vol * 0.15):
                return 30.0, "Micro_Silent_Accumulation", cvd_trend 

            elif price_change > 0.02 and cvd_trend < -(total_vol * 0.15):
                return -25.0, "Hidden_Distribution", cvd_trend 
            
            # 🟢 الإصلاح الجذري: إرجاع قيمة الـ CVD الحقيقية للذكاء الاصطناعي حتى لو لم يكن هناك شذوذ!
            return 0.0, None, cvd_trend
                
    except Exception as e:
        pass
    
    return 0.0, None, cvd_trend # 👈 إرجاع القيمة بدلاً من الأصفار المطلقة
async def detect_btc_relative_strength(symbol: str, client: httpx.AsyncClient):
    """
    [UPGRADED] Statistical Beta Decoupling Engine
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    
    alt_url = f"{get_random_binance_base()}/api/v3/klines?symbol={clean_sym}&interval=1m&limit=60"
    btc_url = f"{get_random_binance_base()}/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60"
    
    try:
        alt_res, btc_res = await asyncio.gather(
            client.get(alt_url, timeout=5.0),
            client.get(btc_url, timeout=5.0)
        )
        
        if alt_res.status_code != 200 or btc_res.status_code != 200: return 0.0
            
        alt_data, btc_data = alt_res.json(), btc_res.json()
        if len(alt_data) < 60 or len(btc_data) < 60: return 0.0
            
        alt_returns = pd.Series([float(c[4]) for c in alt_data]).pct_change().dropna()
        btc_returns = pd.Series([float(c[4]) for c in btc_data]).pct_change().dropna()
        
        # Calculate rolling Beta
        covariance = alt_returns.cov(btc_returns)
        btc_variance = btc_returns.var()
        
        beta = covariance / btc_variance if btc_variance != 0 else 1.0
        btc_total_change = btc_returns.sum() * 100
        alt_total_change = alt_returns.sum() * 100

        # Institutional Logic: Decoupling during a dump
        if btc_total_change < -0.5 and alt_total_change > 0:
            if beta < 0.5: # True decoupling
                return 10.0 # Massive hidden buyer
        elif btc_total_change > 0.5 and alt_total_change < -0.5:
             if beta < 0.5:
                return -8.0 # Hidden distribution
                
        return 2.0 if beta > 1.2 and btc_total_change > 0 else 0.0
        
    except Exception: return 0.0
def calculate_vwap_zscore(df, window=24):
    """
    محرك كشف الفومو (Late FOMO) باستخدام الانحراف المعياري للسعر عن VWAP
    window=24 تعني أننا نحسب الانحراف لآخر 24 ساعة (دورة يومية كاملة)
    """
    # 1. حساب السعر النموذجي (Typical Price)
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    pv = typical_price * df['volume']
    
    # 2. حساب الـ VWAP المتحرك
    rolling_vol = df['volume'].rolling(window=window, min_periods=1).sum()
    rolling_pv = pv.rolling(window=window, min_periods=1).sum()
    local_vwap = rolling_pv / rolling_vol
    
    # 3. حساب الانحراف المعياري للسعر
    rolling_std = typical_price.rolling(window=window, min_periods=1).std(ddof=0)
    
    # 4. استخراج Z-Score للسعر
    # (السعر الحالي - VWAP) / الانحراف المعياري
    vwap_zscore = (df['close'] - local_vwap) / (rolling_std + 1e-8) # 1e-8 لمنع القسمة على صفر
    
    return float(vwap_zscore.iloc[-1]), float(local_vwap.iloc[-1])
async def measure_ob_hollowness(symbol: str, client: httpx.AsyncClient, current_price: float):
    """
    يكتشف الجدران الوهمية (Hollow Orderbook).
    إذا كانت السيولة في أول 1% ضخمة، لكن لا يوجد شيء يحميها حتى 5%، فهذا فخ سيولة.
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    url = f"{get_random_binance_base()}/api/v3/depth?symbol={clean_sym}&limit=500"
    
    try:
        await binance_rate_limit_event.wait()
        res = await client.get(url, timeout=3.0)
        if res.status_code != 200: return False # في حال الفشل نمررها لتجنب تعطيل الرادار
        
        data = res.json()
        
        inner_bid_vol = 0.0 # حائط الصد الأول (0% إلى 1% تحت السعر)
        outer_bid_vol = 0.0 # العمق الداعم (1% إلى 5% تحت السعر)
        
        for b in data.get('bids', []):
            p, v = float(b[0]), float(b[1])
            drop_pct = (current_price - p) / current_price
            
            if drop_pct <= 0.01:
                inner_bid_vol += (p * v)
            elif drop_pct <= 0.05:
                outer_bid_vol += (p * v)
                
        # إذا كان العمق فارغاً تماماً
        if outer_bid_vol == 0: return True 
        
        # نسبة الهشاشة: إذا كان الجدار الأمامي أكبر من العمق الداعم بـ 3 أضعاف، فهو جدار وهمي (Spoof)
        hollowness_ratio = inner_bid_vol / outer_bid_vol
        
        if hollowness_ratio > 3.0:
            return True # 🚨 تحذير: أوردر بوك هش ومفرغ من الداخل!
            
        return False
        
    except Exception:
        return False

async def get_institutional_orderflow(symbol, client, minutes=15):
    """ 
    [UPGRADED] Tick-Level Footprint & Limit Absorption Detection 
    """
    import time
    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)
    
    try:
        await binance_rate_limit_event.wait()
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/aggTrades", params={
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": 1000 
        }, timeout=5.0)
        
        if res.status_code == 200:
            trades = res.json()
            if not trades: return 0.0, 0.0, 0.0, None
            
            buy_vol = 0.0
            sell_vol = 0.0
            cvd_array = []
            prices = []
            current_cvd = 0.0

            for t in trades:
                price = float(t['p'])
                amount = float(t['q']) * price
                prices.append(price)

                if t['m']: # Seller is maker (Aggressive Sell)
                    sell_vol += amount
                    current_cvd -= amount
                else:      # Buyer is maker (Aggressive Buy)
                    buy_vol += amount
                    current_cvd += amount
                
                cvd_array.append(current_cvd)
                    
            delta = buy_vol - sell_vol
            
            # --- Limit Absorption Engine ---
            price_series = pd.Series(prices)
            price_range_pct = (price_series.max() - price_series.min()) / price_series.min()
            
            signal = None
            total_vol = buy_vol + sell_vol
            
            # If price is ranging (< 0.5% movement) but CVD is exploding upwards
            if price_range_pct <= 0.005 and delta > (total_vol * 0.25):
                signal = "Limit_Absorption"
            
            return delta, buy_vol, sell_vol, signal
            
    except Exception as e:
        print(f"⚠️ Flow Error: {e}")
    return 0.0, 0.0, 0.0, None


async def detect_spot_perp_divergence(symbol: str, client: httpx.AsyncClient):
    clean_sym = symbol.replace("USDT", "") + "USDT"
    spot_url = f"{get_random_binance_base()}/api/v3/klines?symbol={clean_sym}&interval=1m&limit=60"
    fapi_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean_sym}&interval=1m&limit=60"
    
    try:
        # 🛑 حارس حماية الـ API (قبل إرسال الطلبات المزدوجة)
        await binance_rate_limit_event.wait()
        
        spot_res, fapi_res = await asyncio.gather(
            client.get(spot_url, timeout=5.0),
            client.get(fapi_url, timeout=5.0)
        )
# ... يكمل باقي الكود كما هو ...
        
        if spot_res.status_code != 200 or fapi_res.status_code != 200:
            return 0.0

        spot_data, fapi_data = spot_res.json(), fapi_res.json()
        if len(spot_data) < 60 or len(fapi_data) < 60:
            return 0.0

        spot_buy_vol = sum(float(c[9]) for c in spot_data)
        spot_total_vol = sum(float(c[5]) for c in spot_data)
        spot_delta = spot_buy_vol - (spot_total_vol - spot_buy_vol)
        
        fapi_buy_vol = sum(float(c[9]) for c in fapi_data)
        fapi_total_vol = sum(float(c[5]) for c in fapi_data)
        fapi_delta = fapi_buy_vol - (fapi_total_vol - fapi_buy_vol)

        if spot_total_vol == 0 or fapi_total_vol == 0:
            return 0.0

        # نظام زيادة وتنقيص النقاط البسيط
        if spot_delta > (spot_total_vol * 0.10) and fapi_delta < -(fapi_total_vol * 0.15):
            return 7.5  # حيتان السبوت تشتري بقوة
        elif spot_delta < -(spot_total_vol * 0.10) and fapi_delta > (fapi_total_vol * 0.15):
            return -8.5 # حيتان السبوت تصرف
        elif spot_delta > 0 and fapi_delta < 0:
            return 2.5  # تباين خفيف إيجابي
        elif spot_delta < 0 and fapi_delta > 0:
            return -2.5 # تباين خفيف سلبي
            
        return 0.0

    except Exception:
        return 0.0


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

async def detect_flash_spoofing_ws(symbol: str, duration: float = 12.0):
    """
    [UPGRADED] True Order Flow Imbalance (OFI) & Delta Tracking
    """
    clean_symbol = symbol.replace("USDT", "").lower() + "usdt"
    ws_url = f"wss://stream.binance.com:9443/ws/{clean_symbol}@depth20@250ms"
    
    frames = []
    try:
        async with websockets.connect(ws_url, ping_interval=None, close_timeout=1) as ws:
            start_time = time.time()
            while time.time() - start_time < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    data = json.loads(msg)
                    if not data.get('bids') or not data.get('asks'): continue
                    
                    bids = np.array([[float(p), float(v)] for p, v in data['bids'][:5]]) # Focus on top 5 levels for true OFI
                    asks = np.array([[float(p), float(v)] for p, v in data['asks'][:5]])
                    frames.append({'bids': bids, 'asks': asks})
                except: break
    except: return None

    if len(frames) < 5: return None

    # --- Advanced OFI Engine ---
    ofi_score = 0
    for i in range(1, len(frames)):
        prev_bid_vol = np.sum(frames[i-1]['bids'][:, 1])
        curr_bid_vol = np.sum(frames[i]['bids'][:, 1])
        prev_ask_vol = np.sum(frames[i-1]['asks'][:, 1])
        curr_ask_vol = np.sum(frames[i]['asks'][:, 1])
        
        # OFI Logic: Bid additions or Ask consumptions add to positive OFI
        delta_bid = curr_bid_vol - prev_bid_vol
        delta_ask = curr_ask_vol - prev_ask_vol
        
        if delta_bid > 0: ofi_score += 1
        if delta_ask < 0: ofi_score += 1
        if delta_bid < 0: ofi_score -= 1
        if delta_ask > 0: ofi_score -= 1

    bid_vols = [np.sum(f['bids'][:, 1]) for f in frames]
    ask_vols = [np.sum(f['asks'][:, 1]) for f in frames]
    
    bid_std = np.std(bid_vols) / (np.mean(bid_vols) + 1e-6)
    ask_std = np.std(ask_vols) / (np.mean(ask_vols) + 1e-6)

    return {
        "imbalance": round((np.mean(bid_vols) - np.mean(ask_vols)) / (np.mean(bid_vols) + np.mean(ask_vols) + 1e-6), 2),
        "ofi_trend": ofi_score, # + Score = Bulls adding liquidity, - Score = Bears adding liquidity
        "is_bid_spoof": bid_std > 0.8,
        "is_ask_spoof": ask_std > 0.8,
        "is_iceberg_buying": bid_std < 0.1 and np.mean(bid_vols) > np.mean(ask_vols)
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

        # 🛑 حارس حماية الـ API (قبل إرسال الطلبات المزدوجة)
        await binance_rate_limit_event.wait()

        oi_res, fund_res = await asyncio.gather(
            client.get(oi_url, timeout=3.0),
            client.get(funding_url, timeout=3.0)
        )
# ... يكمل باقي الكود كما هو ...

        if oi_res.status_code == 200 and fund_res.status_code == 200:
            oi_data = oi_res.json()
            fund_data = fund_res.json()

            if len(oi_data) < 2: return 0.0, None, 0.0


            old_oi = float(oi_data[0]["sumOpenInterest"])
            current_oi = float(oi_data[-1]["sumOpenInterest"])
            oi_change_pct = (current_oi - old_oi) / old_oi
            price_change_pct = (float(current_price) - float(old_price)) / float(old_price)
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
            
                        # ... كودك الحالي
            if funding_rate < -0.0005: 
                score_modifier += 12.0
                if not futures_signal: futures_signal = "Short_Squeeze"
            elif funding_rate > 0.0005:
                score_modifier -= 10.0

            return score_modifier, futures_signal, funding_rate # 👈 التعديل: أضفنا funding_rate للناتج
    except Exception: pass
    return 0.0, None, 0.0 # 👈 التعديل: أضفنا 0.0 للناتج في حال الخطأ

def calculate_volume_zscore(df, window=720):
    """
    محرك الشذوذ الإحصائي (Volume Z-Score).
    window=720 لأننا نستخدم فريم 1h (24 ساعة * 30 يوم = 720 شمعة).
    """
    # 🛡️ إجبار تحويل عمود الفوليوم إلى أرقام لتدمير أي نصوص قد تسبب انهيار (TypeError: str and float)
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    
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
async def detect_real_whale_trades(symbol: str, client: httpx.AsyncClient, volume_24h: float):
    clean_sym = symbol.replace("USDT", "") + "USDT"
    trades_url = f"{get_random_binance_base()}/api/v3/trades?symbol={clean_sym}&limit=1000"
    
    try:
        res = await client.get(trades_url, timeout=5.0)
        if res.status_code != 200:
            return 0.0
            
        trades = res.json()
        whale_buy_vol = 0.0
        whale_sell_vol = 0.0
        
        # 🧠 محرك الحدود المرنة (Dynamic Quant Bounds)
        # 0.2% من السيولة اليومية تعتبر صفقة حوت، بحد أدنى 15 ألف وحد أقصى 500 ألف
        MIN_BLOCK = 15_000.0   
        MAX_BLOCK = 500_000.0  
        WHALE_FACTOR = 0.002   
        
        DYNAMIC_WHALE_THRESHOLD = max(MIN_BLOCK, min(volume_24h * WHALE_FACTOR, MAX_BLOCK))
        
        for t in trades:
            trade_value_usd = float(t['qty']) * float(t['price'])
            if trade_value_usd >= DYNAMIC_WHALE_THRESHOLD:
                if not t['isBuyerMaker']: 
                    whale_buy_vol += trade_value_usd 
                else: 
                    whale_sell_vol += trade_value_usd 
                    
        if whale_buy_vol == 0 and whale_sell_vol == 0:
            return 0.0

        whale_delta = whale_buy_vol - whale_sell_vol
        
        # 🧠 نظام النقاط النسبي (Relative Scoring)
        # تقييم قوة الحيتان بناءً على تأثيرهم كنسبة من حجم التداول الكلي وليس كرقم ثابت
        delta_pct = whale_delta / (volume_24h + 1) # +1 لتجنب القسمة على صفر
        
        if delta_pct > 0.03: return 9.5     # حيتان تسيطر بأكثر من 3% من سيولة اليوم
        elif delta_pct > 0.01: return 5.5   # سيطرة معتدلة 1%
        elif delta_pct < -0.03: return -10.5 # تصريف عنيف
        elif delta_pct < -0.01: return -6.5
            
        return 0.0
        
    except Exception:
        return 0.0


async def analyze_radar_coin(c, client, market_regime, sem):
    async with sem:  
        try:
            symbol = c["symbol"]
            price = float(c["quote"]["USD"]["price"])
            
            candles = await get_candles_binance(f"{symbol}USDT", "1h", limit=750)
            if not candles: return None

                        # --- هذا هو الكود البديل (سطر واحد يستدعي الدالة اللي فوق في الخلفية) ---
            df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)
                        # ====================================================================
            # 🧠 محرك التقييم الديناميكي المؤسساتي (Dynamic Quant Scoring Engine)
            # ====================================================================
            
            tags = [] # قائمة لتجميع نوع الحركات
                        # ====================================================================
            # 🛡️ THE RUTHLESS FILTER: Liquidity Absorption Ratio (LAR) & Spot Lead
            # ====================================================================
            # أضف هذا السطر أينما كنت تحسب Z-Score للفوليوم
            current_vwap_z, current_vwap_price = calculate_vwap_zscore(df, window=24)

            # 1. حساب نسبة التذبذب للشمعة الحالية (Price Spread Percentage)
            current_high = df["high"].iloc[-1]
            current_low = df["low"].iloc[-1]
            candle_spread_pct = ((current_high - current_low) / current_low) * 100
            
            # 2. حساب مؤشر LAR (مع حماية من القسمة على صفر)            # 2. حساب مؤشر LAR (عملية رياضية سريعة جداً محلياً)
            lar_score = current_z / (candle_spread_pct + 0.001)

            # ==========================================================
            # 🛑 المرحلة الأولى: جدار الإعدام الرياضي (Fail-Fast Veto)
            # ==========================================================
            
            # أ. الهروب من الفومو (Late FOMO Veto):
            if current_z > 2.5 and candle_spread_pct > 4.0:
                tags.append("Late_FOMO_Pump")
                return None 

            # ب. فلتر العملات الميتة (Dead Asset Veto):
            if lar_score < 0.8 and current_z < 1.5:
                print(f"🗑️ {symbol} - قُتلت مبكراً (انعدام الامتصاص)") 
                return None 

            # إضافة الـ Tag للعملات القوية
            if lar_score >= 2.0 and current_z > 1.5:
                tags.append("High_Liquidity_Absorption")

            # ==========================================================
            # 🟢 المرحلة الثانية: فحص المشتقات (للعملات الناجية فقط)
            # ==========================================================
            # لم نصل إلى هذا السطر إلا والعملة تستحق دفع ثمن الـ API Call
            
            spot_lead_score = await detect_spot_perp_divergence(symbol, client)
            
            if spot_lead_score < -3.0:
                tags.append("Spot_Dumping_Fakeout")
                return None # تلاعب من صناع السوق


            # 1. مصفوفة الأوزان الديناميكية بناءً على بيئة السوق (Market Regime)
            # المجموع دائماً 1.0 (أي 100%)
            REGIME_WEIGHTS = {
                "Trending_Bull": {"vol": 0.30, "cvd": 0.25, "ob": 0.20, "deriv": 0.15, "tech": 0.10},
                "Trending_Bear": {"vol": 0.10, "cvd": 0.35, "ob": 0.30, "deriv": 0.15, "tech": 0.10},
                "Ranging":       {"vol": 0.15, "cvd": 0.35, "ob": 0.35, "deriv": 0.05, "tech": 0.10},
                "Unknown":       {"vol": 0.20, "cvd": 0.20, "ob": 0.20, "deriv": 0.20, "tech": 0.20}
            }

            current_regime = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"
            weights = REGIME_WEIGHTS.get(current_regime, REGIME_WEIGHTS["Unknown"])

            # 2. تهيئة محفظة النقاط (كل مؤشر سيتم تقييمه من 0 إلى 100)
            scores = {"vol": 0.0, "cvd": 0.0, "ob": 0.0, "deriv": 0.0, "tech": 0.0}

            # ----------------------------------------------------------------
            # أ. تقييم الفوليوم (Z-Score & Pump Penalty) - دوال متصلة
            # ----------------------------------------------------------------
            recent_pump = (df["close"].iloc[-1] - df["close"].iloc[-10]) / df["close"].iloc[-10]
            
            if current_z > 0:
                # تحويل الـ Z-Score إلى نسبة مئوية (الذروة الصحية عند 3.5)
                vol_raw = min((current_z / 3.5) * 100, 100) 
                
                # خنق السكور (Continuous Penalty) إذا كان هناك بمب عالي متأخر
                if current_z >= 3.5 and recent_pump > 0.02:
                    penalty_factor = max(0.0, 1.0 - (recent_pump * 15)) # يخنق السكور تدريجياً
                    vol_raw *= penalty_factor
                    if vol_raw < 30: tags.append("Late_FOMO")
                
                elif 1.0 <= current_z <= 3.5 and recent_pump <= 0.02:
                    tags.append("Smart_Accumulation")
                elif current_z > 3.5 and recent_pump <= 0.02:
                    tags.append("Z_Anom_Silent")
                    
                scores["vol"] = vol_raw
            # (أضف هذا تحت سطر: scores["vol"] = vol_raw)
                global_alt_volume = await verify_global_liquidity(symbol, client)
                if global_alt_volume > 100000: 
                    scores["vol"] = min(scores["vol"] + 15, 100) # تعزيز سلس لسكور الفوليوم

            # ----------------------------------------------------------------
            # ب. تقييم السيولة اللحظية (Micro CVD)
            # ----------------------------------------------------------------
            avg_vol_20 = df["volume"].tail(20).mean()
            micro_cvd_boost, micro_cvd_signal, micro_cvd_trend = await get_micro_cvd_absorption(f"{symbol}USDT", client, "1h")

            
            if avg_vol_20 > 0:
                # نسبة الـ CVD لمتوسط الفوليوم (0.3 تعتبر 100%)
                cvd_ratio = micro_cvd_trend / avg_vol_20
                
                if cvd_ratio > 0:
                    scores["cvd"] = min((cvd_ratio / 0.3) * 100, 100)
                    if cvd_ratio > 0.15 and abs(recent_pump) <= 0.015:
                        tags.append("Micro_Silent_Accumulation")
                else:
                    scores["cvd"] = 0 # تجميع سلبي
                    if cvd_ratio < -0.15 and recent_pump > 0.02:
                        tags.append("Hidden_Distribution")

            # ----------------------------------------------------------------
            # ج. تقييم الأوردر بوك (Spoofing & Global Pressure)
            # ----------------------------------------------------------------
            ob_raw = 50.0 # نقطة التعادل
            
            depth_data = await detect_flash_spoofing_ws(symbol, duration=4.0)
            global_ob_pressure = await get_aggregated_orderbook(client, symbol)
            
            # 👈 السطر الجديد الذي سيقوم بفحص هشاشة الأوردر بوك وتخزين النتيجة
            is_orderbook_hollow = await measure_ob_hollowness(symbol, client, price)
            
            if depth_data:
                imbalance = depth_data.get('imbalance', 0)
                ob_raw += (imbalance * 40) # Imbalance يضيف أو يخصم بطريقة سلسة
                
                if depth_data['is_bid_spoof']:
                    ob_raw *= 0.2 # تدمير السكور لخطورته
                    tags.append("Flash_Spoofing_Manipulation")
                elif depth_data['is_ask_spoof'] and scores["cvd"] > 50:
                    ob_raw = min(ob_raw + 30, 100)
                    tags.append("Whale_Spoofing_Accumulation")
                elif imbalance > 0.3:
                    tags.append("OB_Buy")

            if global_ob_pressure > 0:
                # ضغط الأوردر بوك العالمي يرفع السكور بنعومة
                ob_raw += min((global_ob_pressure / 2.0) * 20, 20)
                
            scores["ob"] = max(0, min(ob_raw, 100))

            # ----------------------------------------------------------------
            # د. تقييم المشتقات والحيتان (Derivatives & Whales)
            # ----------------------------------------------------------------
                        # ----------------------------------------------------------------
            # د. تقييم المشتقات وتصيّد التصفية (Derivatives & Squeeze Mechanics)
            # ----------------------------------------------------------------
            deriv_raw = 50.0
            old_price_val = df["close"].iloc[-3] if len(df) > 3 else df["open"].iloc[0]
            
            # Fetching the upgraded Institutional Tick Data & OFI
            tick_delta, tick_buy, tick_sell, limit_abs_signal = await get_institutional_orderflow(f"{symbol}USDT", client)
            _, futures_signal, funding_val = await get_futures_liquidity(symbol, client, price, old_price_val)
            
            approx_24h_vol_usd = df["volume"].tail(24).sum() * price 
            whale_score = await detect_real_whale_trades(symbol, client, approx_24h_vol_usd)

            # THE SQUEEZE ENGINE (Spot Premium > Perp Premium + High OI)
            is_spot_premium = spot_lead_score > 3.0
            is_perp_dumping = futures_signal == "OI_Rising" and tick_delta < 0 and funding_val < -0.0005
            
            if limit_abs_signal == "Limit_Absorption":
                tags.append("Limit_Absorption")
                scores["cvd"] = min(scores["cvd"] + 30, 100) # Heavy boost for tick-level limit absorption
                
            if is_spot_premium and is_perp_dumping:
                tags.append("Short_Squeeze_Imminent")
                deriv_raw += 45.0 # Max out derivatives score
            elif futures_signal == "OI_Rising": 
                deriv_raw += 15.0; tags.append("OI_Rising")
            elif futures_signal == "Short_Covering": 
                deriv_raw -= 20.0; tags.append("Short_Covering")
                
            if whale_score > 0:
                deriv_raw += min((whale_score / 10.0) * 30, 30) # أقصاها 30 نقطة إضافية
            else:
                deriv_raw *= 0.6 # ضريبة غياب سيولة الحيتان الكبيرة
                
            # تطبيق متغير القوة النسبية للسبوت على السكور النهائي
            div_score = spot_lead_score 
            deriv_raw += (div_score * 2.5) 

            scores["deriv"] = max(0, min(deriv_raw, 100))

            # ----------------------------------------------------------------
            # هـ. تقييم الهيكلة الفنية (Technical Structure)
            # ----------------------------------------------------------------
            tech_raw = 50.0
            
            # 👇 تم استرجاع حسابات البولينجر باند المفقودة هنا 👇
            sma20_bb = df["close"].rolling(20).mean()
            std20_bb = df["close"].rolling(20).std(ddof=0)
            upper_band = sma20_bb + 2 * std20_bb
            lower_band = sma20_bb - 2 * std20_bb
            bb_width = (upper_band - lower_band) / sma20_bb
            
            # حماية من الأخطاء إذا كانت بيانات العملة أقل من 100 شمعة
            avg_bb_width = bb_width.rolling(100).mean().iloc[-1] if len(bb_width) >= 100 else float('nan')
            current_bb_width = bb_width.iloc[-1]
            # 👆 نهاية الحسابات 👆

            # انضغاط البولينجر باند (Squeeze)
            if not pd.isna(current_bb_width) and not pd.isna(avg_bb_width):
                if current_bb_width < (avg_bb_width * 0.5):
                    tech_raw += 20
                    tags.append("Squeeze")
                    
            ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            if price < ema200_val and last_rsi > 30 and df["rsi"].iloc[-10:-1].min() < 30:
                tech_raw += 20; tags.append("RSI_Div")
                
            # الفخاخ الفنية (Fakeouts & Candle Strength)
            candle_score = detect_candle_strength(df)
            fake_out_penalty = detect_fake_breakout(df)
            
            if candle_score > 0: tech_raw += 15; tags.append("Bullish_Hammer_Absorption")
            if fake_out_penalty < 0: 
                tech_raw *= 0.3 # عقاب قاسي فنياً
                tags.append("Fake_Breakout_Trap")

            # التمرد ضد البيتكوين (القوة النسبية)
            rs_score = await detect_btc_relative_strength(symbol, client)
            tech_raw += (rs_score * 3.0) 
                
            scores["tech"] = max(0, min(tech_raw, 100))

            # ====================================================================
            # ⚖️ الدمج النهائي بناءً على الأوزان المؤسساتية
            # ====================================================================
            final_weighted_score = (
                (scores["vol"]   * weights["vol"]) +
                (scores["cvd"]   * weights["cvd"]) +
                (scores["ob"]    * weights["ob"]) +
                (scores["deriv"] * weights["deriv"]) +
                (scores["tech"]  * weights["tech"])
            )

            # 🟢 ضبط السكور ليكون واقعياً (الكمال مستحيل)
            score = round(max(0.0, min(final_weighted_score, 98.5)), 1)

            # --- جدار الفيتو الإجباري والفلترة (تم نقله هنا ليعمل كحارس أخير) ---
            final_signal = "High Probability Setup 🎯"
            
            if "Fake_Pump" in tags or "Fake_Breakout_Trap" in tags or "Flash_Spoofing_Manipulation" in tags or "Short_Covering" in tags or "Late_FOMO" in tags or "Hidden_Distribution" in tags:
                return None 

            if score >= 95.0: final_signal = "Deep Liquidity Absorption 🏦"
            elif score >= 90.0: final_signal = "Institutional Orderflow 🐋"
            elif "Micro_Silent_Accumulation" in tags and "OB_Buy" in tags: final_signal = "Active Accumulation 🦈"
            elif "Short_Squeeze" in tags: final_signal = "(Short Squeeze) 🔥"
            elif "Z_Anom_Silent" in tags and "OI_Rising" in tags: final_signal = "(Derivatives Pump) 🚀"
            elif "Squeeze" in tags and "OB_Buy" in tags: final_signal = "(Liquidity Breakout) ⚡"
            elif "Micro_Silent_Accumulation" in tags: final_signal = "Silent Accumulation 🧲"
            elif "Smart_Accumulation" in tags: final_signal = "Smart Money Inflow 💸"

            # استدعاء المتغيرات للفيتو            # استدعاء المتغيرات للفيتو
            current_cvd = float(locals().get('micro_cvd_trend', 0.0)) * price
            current_imbalance = float(locals().get('depth_data', {}).get('imbalance', 0) if locals().get('depth_data') else 0.0)
            
            # ==========================================
            # 🛡️ الفيتو الإجباري المطور (Institutional Veto)
            # ==========================================
            
            # 1. كشف الفومو وجني الأرباح (Late FOMO / Mean Reversion)
            if current_vwap_z > 2.8:
                tags.append("Late_FOMO_Pump_VWAP")
                print(f"🗑️ {symbol} - مرفوض: السعر انحرف عن VWAP بأكثر من 2.8 (منطقة جني أرباح)")
                return None 
                
            # 2. كشف الفراغ السيولي والهشاشة (Liquidity Void)
            # 🟢 التعديل الأمني هنا لتجنب انهيار الكود مع عملات الـ DEX
            if locals().get('is_orderbook_hollow', False) and current_cvd < 0:
                tags.append("Liquidity_Void_Trap")
                print(f"🗑️ {symbol} - مرفوض: جدران شراء وهمية والعمق الداعم فارغ تماماً!")
                return None 

            # 3. فخ الجدران الوهمية (Spoofing Trap):
            if global_ob_pressure > 1.1 and current_cvd < 0:
                tags.append("Spoofing_Distribution_Trap")
                return None 


            # 2. انعدام الشراء الحقيقي:
            if current_cvd <= 0 and current_imbalance <= 0.1 and global_ob_pressure < 1.1:
                return None 

            # 3. فخ السكاكين الساقطة (Falling Knife / Pump & Dump Trap) - (GALA FIX):
            # إذا كان السعر في ترند هابط (تحت متوسط 200) وزخم السوق ميت تماماً (ADX < 20)،
            # فإن أي انفجار فوليوم هو مجرد "فخ" أو ارتداد مؤقت قبل انهيار جديد.
            ema200_veto = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            if price < ema200_veto and current_adx < 20.0 and current_z > 2.0:
                tags.append("Dead_Trend_Pump_Trap")
                return None 

            # ==========================================
            # 🛡️ استرجاع شروط القناص النهائي والمتغيرات المفقودة
            # ==========================================
            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
            current_vol_ratio = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0

            golden_tags = {"Z_Anom_Silent", "Smart_Accumulation", "Micro_Silent_Accumulation", "Institutional_Buy_Spike", "OB_Buy", "Squeeze"}
            confluence_count = sum(1 for tag in tags if tag in golden_tags)

            ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            is_macro_downtrend = price < ema200_val
            current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"

                        # 🛡️ رفع معايير القبول لمستوى صناديق التحوط (لن يمر سوى 3 إلى 5 عملات يومياً كحد أقصى)
            required_score = 75.0 if (current_regime_trend == "Trending_Bear" or is_macro_downtrend) else 70.0
            required_confluence = 2 if (current_regime_trend == "Trending_Bear" or is_macro_downtrend) else 2

            # إرجاع النتيجة فقط إذا تحقق السكور + الإجماع الفني + اجتياز الفيتو
            if score >= required_score and confluence_count >= required_confluence:    
                # 1. جلب بيانات السلسلة (On-Chain)
                whale_inflow = await get_whale_inflow_score()
                
                micro_volatility = df['close'].tail(20).pct_change().std() * 100
                
                # 🟢 الإصلاح: حساب انحراف مسار السيولة (CVD Divergence) باستخدام القيمة الحقيقية
                                # حساب انحراف مسار السيولة (CVD Divergence) - هل السعر يصعد بينما CVD يهبط؟
                cvd_divergence = 1.0 if (price > ema200_val and current_cvd < 0) else -1.0 if (price < ema200_val and current_cvd > 0) else 0.0

                # جلب معدل التمويل الحالي (Funding Rate) من مصفوفة الـ Futures التي حسبناها مسبقاً
                # إذا كان التمويل سالباً بقوة، الحيتان تضغط السعر صعوداً لتصفية البائعين

                ml_features = {
                    'z_score': float(current_z),
                    'cvd_usd': current_cvd, # 👈 سحبنا المتغير الجاهز والمصحح
                    'ofi_imbalance': float(current_imbalance),
                    'ob_skewness': float(locals().get('depth_data', {}).get('skewness', 1.0) if locals().get('depth_data') else 1.0),
                    'adx': float(current_adx),
                    'rsi': float(last_rsi),
                    'whale_inflow': float(whale_inflow),
                    'micro_volatility': float(micro_volatility) if not pd.isna(micro_volatility) else 0.0,
                    'cvd_divergence': float(cvd_divergence), # 👈 تم الإصلاح
                    'funding_rate': float(funding_val),
                    'volume_ratio': float(current_vol_ratio),
                    'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)),     # 🌍 حماية من أخطاء الذاكرة
                    'sentiment_score': float(MACRO_CACHE.get("sentiment_score", 50.0)) # 🧠 حماية من أخطاء الذاكرة
                }

                
                # 3. سؤال الذكاء الاصطناعي عن رأيه (في مسار خلفي)
                                # 3. سؤال الذكاء الاصطناعي (وضع التعليق والتعلم الصامت)
                # ai_probability = await asyncio.to_thread(predict_signal_sync, ml_features) 
                
                # إجبار البوت على تجاهل الـ AI حالياً والاعتماد على السكور الكلاسيكي المطور
                ai_probability = -1.0 
                
                # 4. قرار الفلترة (سيعتمد دائماً على السكور الكلاسيكي لأن ai_probability = -1)
                if ai_probability != -1.0:
                    if ai_probability < 75.0: return None
                    final_score = ai_probability
                    ai_status = "Active 🧠"
                else:
                    final_score = score # الاعتماد الكلي على معادلاتك الكمية الاحترافية
                    ai_status = "Training & Learning ⏳"

                
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
    async with pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT 1 FROM ml_training_data 
            WHERE symbol = $1 AND signal_time > CURRENT_TIMESTAMP - INTERVAL '24 hours'
        """, symbol)
        if exists: return 

        # إدخال البيانات الممتدة
        await conn.execute("""
            INSERT INTO ml_training_data 
            (symbol, entry_price, z_score, cvd, imbalance, adx, rsi, whale_inflow_score,
             ob_skewness, micro_volatility, cvd_divergence, funding_rate, volume_ratio,
             sp500_trend, sentiment_score)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """, 
        symbol, price, 
        features.get('z_score', 0.0), features.get('cvd_usd', 0.0), 
        features.get('ofi_imbalance', 0.0), features.get('adx', 0.0), 
        features.get('rsi', 0.0), features.get('whale_inflow', 0.0),
        features.get('ob_skewness', 1.0), features.get('micro_volatility', 0.0),
        features.get('cvd_divergence', 0.0), features.get('funding_rate', 0.0),
        features.get('volume_ratio', 1.0),
        features.get('sp500_trend', 0.0), features.get('sentiment_score', 50.0))

        
        print(f"🧠 [ML Logger] Data captured for {symbol} at ${price}")

async def ml_inspector_worker(pool):
    await asyncio.sleep(120)
    print("🕵️‍♂️ ML Inspector is running in the background...")
    
    while True:
        try:
            # 1. افتح الاتصال لجلب البيانات فقط ثم اغلقه فوراً
            async with pool.acquire() as conn:
                pending = await conn.fetch("""
                    SELECT id, symbol, entry_price, EXTRACT(EPOCH FROM signal_time) as sig_ts
                    FROM ml_training_data 
                    WHERE label = -1 AND signal_time <= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                """)
                
            if not pending:
                await asyncio.sleep(600)
                continue
                
            # 2. قم بالعمليات البطيئة (HTTP) خارج نطاق الاتصال بقاعدة البيانات
            async with httpx.AsyncClient(timeout=10) as client:
                for row in pending:
                    sym = f"{row['symbol']}USDT"
                    entry = row['entry_price']
                    start_time_ms = int(row['sig_ts'] * 1000)
                    
                    base_url = get_random_binance_base()
                    res = await client.get(
                        f"{base_url}/api/v3/klines",
                        params={"symbol": sym, "interval": "15m", "startTime": start_time_ms, "limit": 96}
                    )
                    
                    if res.status_code == 200:
                        klines = res.json()
                        if not klines: continue
                        
                        highest_high = max([float(k[2]) for k in klines])
                        max_profit_pct = ((highest_high - entry) / entry) * 100
                        label = 1 if max_profit_pct >= 5.0 else 0
                        
                        # 3. افتح اتصالاً سريعاً جداً لتحديث السطر الواحد واغلقه
                        async with pool.acquire() as conn:
                            await conn.execute("""
                                UPDATE ml_training_data 
                                SET max_profit_pct = $1, label = $2 
                                WHERE id = $3
                            """, max_profit_pct, label, row['id'])
                        
                        print(f"📊 [ML Labeling] {sym} evaluated. Max Profit: {max_profit_pct:.2f}%. Label: {label}")
                        
                    await asyncio.sleep(0.5) # حماية الـ API
                    
        except Exception as e:
            print(f"⚠️ ML Inspector Error: {e}")
            
        await asyncio.sleep(600)
# --- ذاكرة الماكرو والمشاعر اللحظية ---
MACRO_CACHE = {
    "sp500_trend": 0.0,      # نسبة تغير السوق الأمريكي اليوم
    "sentiment_score": 50.0, # من 0 إلى 100
}

async def macro_data_worker():
    """
    عامل خلفي (Quant Macro Engine) يعمل كل 30 دقيقة.
    يجلب مشاعر الكريبتو وحالة الأسهم الأمريكية ويخزنها في الذاكرة.
    لا يستهلك أي ليمت ولا يؤثر على سرعة الرادار.
    """
    await asyncio.sleep(10) # انتظر حتى يعمل البوت
    print("🌍 Macro Data Worker is live...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 1. جلب مؤشر المشاعر (Fear & Greed)
                try:
                    fng_res = await client.get("https://api.alternative.me/fng/?limit=1")
                    if fng_res.status_code == 200:
                        data = fng_res.json()
                        MACRO_CACHE["sentiment_score"] = float(data['data'][0]['value'])
                except Exception as e:
                    print(f"⚠️ Sentiment API Error: {e}")

                # 2. جلب حالة السوق الأمريكي (S&P 500 ETF - SPY)
                try:
                    spy_res = await client.get(
                        "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=2d",
                        headers=headers
                    )
                    if spy_res.status_code == 200:
                        chart_data = spy_res.json()['chart']['result'][0]['indicators']['quote'][0]
                        closes = chart_data['close']
                        # حساب نسبة التغير بين إغلاق أمس والسعر الحالي
                        if len(closes) >= 2 and closes[-1] and closes[-2]:
                            pct_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100
                            MACRO_CACHE["sp500_trend"] = round(pct_change, 2)
                except Exception as e:
                    print(f"⚠️ S&P 500 API Error: {e}")
                    
                print(f"🔄 [Macro Updated] Sentiment: {MACRO_CACHE['sentiment_score']} | S&P500 Trend: {MACRO_CACHE['sp500_trend']}%")
                
        except Exception as e:
            pass
            
        await asyncio.sleep(1800) # التحديث كل نصف ساعة

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
                        coins.append({
                            "symbol": clean_sym,
                            "quote": {"USD": {"price": float(t["lastPrice"])}},
                            "volume": vol_usd,
                            "priceChangePercent": price_change # 👈 أضفنا هذا السطر لكي تتعرف عليه دالة الترتيب
                        })
                
                # الفرز بناءً على أضيق نسبة تغير في السعر (من الأقرب للصفر) لاصطياد العملات المضغوطة
                                # الفرز بناءً على أعلى سيولة دولاريه (Volume) لاصطياد أهداف الحيتان الحقيقية
                # نترك كشف الانضغاط للتحليل العميق لكي لا ننخدع بالتذبذب الوهمي (Whipsaws)
                coins = sorted(coins, key=lambda x: float(x.get("volume", 0)), reverse=True)[:300]


                
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
أنت محلل بيانات كمية (Quant). المهمة: كتابة 4 نقاط تحليلية دقيقة ومباشرة فقط. لا تكتب أي مقدمات، أو تحيات، أو استنتاجات.
البيانات الخام لعملة {symbol}:
- شذوذ الفوليوم (Z-Score): {z_score_val}
- نسبة السيولة (Current/Avg Vol): {vol_ratio_val}
- ضغط الأوردر بوك (Bids/Asks): {ob_pressure_val}
- الإجماع الفني: {best_meta.get('confluence', 0)}
- الزخم: ADX: {best_meta['adx']} | RSI: {best_meta['rsi']}

قواعد التفسير المالي (التزم بها حرفياً):
1. ضغط الأوردر بوك: إذا كان < 1 (سيطرة بيعية/امتصاص). إذا > 1 (طلب هجومي).
2. نسبة السيولة: إذا كانت < 1 (جفاف سيولة/تجميع صامت مخفي). إذا > 1 (ضخ سيولة مؤسساتي نشط).
3. مؤشر Z-Score: الأرقام السلبية تعني انضغاطاً في القاع. الأرقام الإيجابية العالية (>2) تعني انفجاراً استثنائياً في الفوليوم.
4. الإجماع الفني: هو عدد صحيح من (1 إلى 6). وجود رقم (1 أو أعلى) يعني إجماعاً قوياً جداً وتأكيداً مؤكداً للفرصة.

شروط المخرجات (OUTPUT RULES):
- إياك أن تكتب عبارات مثل: "بناءً على البيانات"، "نلاحظ"، "مما يشير إلى". ادخل في التحليل فوراً.
- المخرج يجب أن يكون 3 نقاط فقط تبدأ برمز (•).
- اربط الأرقام بالواقع (مثال: "جفاف في السيولة مع نسبة فوليوم {vol_ratio_val} يعكس تجميعاً صامتاً من الحيتان").
- لغة جافة، خالية من العواطف والمبالغات.
- أسلوب الكتابة: اكتب بطريقة (Flash Notes). لا تشرح كل رقم في سطر منفصل، بل ادمجها بذكاء. مثال بدلاً من سرد الأرقام قل: (تكدس قوي في طلبات الشراء بضعف 1.84x يتزامن مع امتصاص صامت للسيولة 0.93x في قاع منضغط).
"""

                prompt_en = f"""
Act as a strict Quant Analyst. Task: Write EXACTLY 4 analytical bullet points. NO intro, NO outro, NO fluff.
Raw Data for {symbol}:
- Volume Anomaly (Z-Score): {z_score_val}
- Volume Ratio (Current/Avg): {vol_ratio_val}
- Orderbook Ratio (Bids/Asks): {ob_pressure_val}
- Confluence: {best_meta.get('confluence', 0)}
- Momentum: ADX: {best_meta['adx']} | RSI: {best_meta['rsi']}

Financial Interpretation Rules (Follow strictly):
1. Orderbook Ratio: If < 1 (Supply control/absorption). If > 1 (Aggressive demand).
2. Volume Ratio: If < 1 (Liquidity dry-up/silent hidden accumulation). If > 1 (Active institutional inflow).
3. Z-Score: Negative numbers mean bottom compression. High positive numbers (>2) mean exceptional volume explosion.
4. Confluence: It is an integer from (1 to 6). A number of (1 or higher) means very strong consensus and a confirmed setup.

Strict Output Format:
- NEVER use phrases like "Based on the data", "We can see", or "This indicates". Start the bullet points immediately.
- Output EXACTLY 3 points starting with (•).
- Weave numbers naturally (e.g., "Orderbook shows supply absorption with a ratio of {ob_pressure_val}").
- Keep the tone cold, dry, and institutional. Zero hype.
- Writing Style: Use 'Flash Notes' format. Do not explain metrics in isolation; fuse them into a cohesive narrative (e.g., 'Aggressive bid stacking at {ob_pressure_val}x aligns with silent liquidity absorption {vol_ratio_val}x at compressed lows').
"""




                insight_ar = await ask_groq(prompt_ar, lang="ar")
                insight_en = await ask_groq(prompt_en, lang="en")

                signal_id = str(uuid.uuid4())[:8] 
                radar_pending_approvals[signal_id] = {
                    "symbol": symbol, "price": price, "signal": signal, "score": best_score,
                    "insight_ar": insight_ar, "insight_en": insight_en
                }
                # تشغيل التسجيل في الخلفية لكي لا يؤخر إرسال الرسالة للأدمن
                                # 🧠 تسجيل البيانات للذكاء الاصطناعي (أخذنا البيانات الجاهزة من دالة التحليل مباشرة)
                asyncio.create_task(log_signal_for_ml(pool, symbol, price, best_meta.get('ml_features', {})))


                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ موافقة ونشر للمشتركين", callback_data=f"rad_app_{signal_id}")],
                    [InlineKeyboardButton(text="❌ إلغاء وتجاهل", callback_data=f"rad_rej_{signal_id}")]
                ])

                                # جلب حالة الذكاء الاصطناعي من النتائج (وإذا لم يجدها يضع Learning افتراضياً)
                current_ai_status = best_meta.get('ai_status', 'Learning ⏳')

                admin_text = (
                    f"⚠️ <b>تنبيه أدمن: قناص القيعان أنهى المسح 🎯</b>\n"
                    f"🏆 <b>أفضل عملة:</b> #{symbol}\n"
                    f"💵 السعر: ${format_price(price)}\n"
                    f"⚡ نوع التجميع: {signal}\n"
                    f"🤖 حالة الـ AI: <b>{current_ai_status}</b>\n"  # 👈 هذا هو السطر الذي سيظهر لك التغيير
                    f"📊 تقييم الفرصة: <b>{best_score}/100</b>\n\n"
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
    """
    محرك كشف الفخاخ (Fakeout Detection) المعتمد على السعر + الفوليوم.
    """
    if len(df) < 21: return 0
    
    recent_high = df["high"].iloc[-21:-1].max() # أعلى قمة في آخر 20 شمعة (تجاهل الحالية)
    avg_vol = df["volume"].iloc[-21:-1].mean()  # متوسط الفوليوم السابق
    
    last = df.iloc[-1]
    
    # حساب هندسة الشمعة الحالية
    candle_range = last["high"] - last["low"]
    if candle_range == 0: return 0
    upper_wick = last["high"] - max(last["open"], last["close"])
    
    # شروط الاختراق الكاذب القاتل (Bull Trap):
    # 1. السعر اخترق القمة السابقة
    # 2. السعر أغلق تحت القمة السابقة
    # 3. الفوليوم وقت الاختراق كان أقل من المتوسط (اختراق ضعيف السيولة)
    # 4. الذيل العلوي يمثل أكثر من 40% من حجم الشمعة (رفض سعري قوي)
    
    if (last["high"] > recent_high) and (last["close"] < recent_high):
        if (last["volume"] < avg_vol) and (upper_wick > candle_range * 0.4):
            return -25 # فخ بيعي مؤكد للمحترفين
            
    # شروط الكسر الكاذب (Bear Trap) - كسر قاع وهمي
    recent_low = df["low"].iloc[-21:-1].min()
    lower_wick = min(last["open"], last["close"]) - last["low"]
    
    if (last["low"] < recent_low) and (last["close"] > recent_low):
        if (last["volume"] < avg_vol) and (lower_wick > candle_range * 0.4):
            return 25 # فخ شرائي مؤكد (صيد قيعان)
            
    return 0

def detect_nearest_fvg(df, current_price, trend_direction):
    """
    محرك اكتشاف فجوات السيولة (FVG) المطور.
    يفلتر الفجوات الميتة (التي تم إغلاقها) ويتجاهل فجوات الفوليوم الضعيف.
    """
    recent = df.tail(30).reset_index(drop=True)
    avg_vol = recent['volume'].mean() # حساب متوسط الفوليوم
    fvgs = []
    
    for i in range(2, len(recent)):
        c1_high, c1_low = recent.loc[i-2, 'high'], recent.loc[i-2, 'low']
        c2_vol = recent.loc[i-1, 'volume'] # فوليوم الشمعة صانعة الفجوة
        c3_high, c3_low = recent.loc[i, 'high'], recent.loc[i, 'low']
        
        fvg_type = None
        top, bottom = 0.0, 0.0
        
        # 1. تحديد نوع الفجوة (يجب أن يكون فوليوم شمعة الاختراق أعلى من المتوسط)
        if c1_high < c3_low and c2_vol > (avg_vol * 0.8): # Bullish FVG
            fvg_type = "Bullish"
            top, bottom = c3_low, c1_high
        elif c1_low > c3_high and c2_vol > (avg_vol * 0.8): # Bearish FVG
            fvg_type = "Bearish"
            top, bottom = c1_low, c3_high
            
        # 2. 🛡️ الفحص الأهم: هل تم إغلاق هذه الفجوة في الشموع اللاحقة؟
        if fvg_type:
            is_filled = False
            for j in range(i+1, len(recent)):
                future_low, future_high = recent.loc[j, 'low'], recent.loc[j, 'high']
                
                # إذا نزل السعر لاحقاً وضرب قاع الفجوة الشرائية = تم إغلاقها
                if fvg_type == "Bullish" and future_low <= bottom:
                    is_filled = True; break
                # إذا صعد السعر لاحقاً وضرب قمة الفجوة البيعية = تم إغلاقها
                elif fvg_type == "Bearish" and future_high >= top:
                    is_filled = True; break
                    
            if not is_filled:
                fvgs.append({'top': top, 'bottom': bottom, 'type': fvg_type})

    if not fvgs: return None

    best_fvg_target = None
    min_dist = float('inf')

    # 3. اختيار أقرب فجوة مفتوحة كهدف مغناطيسي
    for fvg in fvgs:
        mid_fvg = (fvg['top'] + fvg['bottom']) / 2
        dist = abs(current_price - mid_fvg)
        
        if trend_direction == "Bullish" and current_price < fvg['bottom']:
            if dist < min_dist:
                min_dist = dist; best_fvg_target = fvg['bottom']
                
        elif trend_direction == "Bearish" and current_price > fvg['top']:
            if dist < min_dist:
                min_dist = dist; best_fvg_target = fvg['top']

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
        market_action = "تذبذب عرضي، سيولة ضعيفة حول الـ VWAP" if lang == "ar" else "Choppy ranging around VWAP"
    else: 
        if micro_bull:
            if macro_bull and vwap_bull:
                trend_strength = "قوي" if lang == "ar" else "Strong"
                market_action = "سيولة شرائية صحية تدعم الاتجاه" if lang == "ar" else "Healthy buying liquidity"
            elif not macro_bull and vwap_bull:
                trend_strength = "متوسط (ارتداد)" if lang == "ar" else "Moderate (Bounce)"
                market_action = "ضغط شرائي قصير الأمد يعاكس الترند الهابط" if lang == "ar" else "Short-term buying pressure countering downtrend"
            else:
                trend_strength = "مخادع" if lang == "ar" else "Fake"
                market_action = "صعود غير مدعوم بالسيولة (خطر الانعكاس)" if lang == "ar" else "Unconfirmed pump lacking liquidity (Reversal risk)"
        else: # Bearish
            if not macro_bull and not vwap_bull:
                trend_strength = "قوي" if lang == "ar" else "Strong"
                market_action = "سيطرة بيعية صريحة وتصريف للسيولة" if lang == "ar" else "Clear selling control and distribution"
            elif macro_bull and not vwap_bull:
                trend_strength = "متوسط (تصحيح)" if lang == "ar" else "Moderate (Correction)"
                market_action = "جني أرباح وتصحيح ضمن ترند صاعد" if lang == "ar" else "Profit-taking within a macro uptrend"
            else:
                trend_strength = "مخادع" if lang == "ar" else "Fake"
                market_action = "هبوط ضعيف السيولة (فخ بيعي محتمل)" if lang == "ar" else "Low-liquidity drop (Potential bear trap)"

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
        # إذا كان الدعم هو السعر الحالي بالضبط أو أعلى منه، نضع دعماً رياضياً تحته بـ 5%
        if pd.isna(support) or support >= current_price * 0.99:
            support = current_price * 0.95 
    except:
        support = current_price * 0.95

    try:
        resistance = df['high'].rolling(window=50, min_periods=1).max().iloc[-1]
        # إذا كانت المقاومة هي السعر الحالي أو أقل منه، نضع مقاومة رياضية فوقه بـ 5%
        if pd.isna(resistance) or resistance <= current_price * 1.01:
            resistance = current_price * 1.05 
    except:
        resistance = current_price * 1.05


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
                # 🛡️ إعدادات الحماية وإدارة المخاطر الجديدة
        MAX_SL_PCT = 0.05  # أقصى مسافة لوقف الخسارة (5%)
        MIN_TP_PCT = 0.015 # أقل مسافة للهدف الأول (1.5%)
        MAX_TP_PCT = 0.15  # أقصى مسافة للأهداف (15% لكي لا يعطي أهدافاً جنونية)

        if trend_direction == "Bullish":
            valid_targets = above_price[(above_price['price'] >= current_price * (1 + MIN_TP_PCT)) & 
                                        (above_price['price'] <= current_price * (1 + MAX_TP_PCT))]
            targets = valid_targets.nlargest(3, 'volume').sort_values('price')
            tps = targets['price'].tolist()

            support_node = below_price.nlargest(1, 'volume')
            support_price = support_node['price'].iloc[0] if not support_node.empty else current_price * 0.95

            lvns_below_support = below_price[below_price['price'] < support_price]
            if not lvns_below_support.empty:
                sl_price = lvns_below_support.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = support_price * 0.98

            if sl_price < current_price * (1 - MAX_SL_PCT):
                sl_price = current_price * (1 - MAX_SL_PCT)

        else: # Bearish
            valid_targets = below_price[(below_price['price'] <= current_price * (1 - MIN_TP_PCT)) & 
                                        (below_price['price'] >= current_price * (1 - MAX_TP_PCT))]
            targets = valid_targets.nlargest(3, 'volume').sort_values('price', ascending=False)
            tps = targets['price'].tolist()

            res_node = above_price.nlargest(1, 'volume')
            res_price = res_node['price'].iloc[0] if not res_node.empty else current_price * 1.05

            lvns_above_res = above_price[above_price['price'] > res_price]
            if not lvns_above_res.empty:
                sl_price = lvns_above_res.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = res_price * 1.02

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
                # 1. ⚡ جلب البيانات المؤسساتية أولاً لمعرفة النية المخفية (قبل وضع الأهداف)
                # 1. ⚡ جلب البيانات المؤسساتية (فقط لعملات المنصات المركزية CEX)
        delta_usd, funding_val = 0.0, 0.0
        cvd_sig, fut_sig = None, None
        buy_v, sell_v, z_score = 0, 0, 0
        
        if not is_dex: # 👈 حماية: لا تطلب بيانات مؤسساتية لعملات الديكس من بايننس
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    # 1. إجبار تحويل السعر الحالي إلى رقم عشري لتدمير أي نص قادم من الذاكرة
                    safe_price = float(price)
                    
                    # 2. إجبار السعر القديم ليكون رقماً عشرياً أيضاً
                    if len(df) > 3:
                        safe_old_price = float(df["close"].iloc[-3])
                    else:
                        safe_old_price = safe_price

                    cvd_task = get_micro_cvd_absorption(f"{clean_sym}USDT", client, gate_interval)
                    flow_task = get_institutional_orderflow(f"{clean_sym}USDT", client, minutes=15)
                    futures_task = get_futures_liquidity(clean_sym, client, safe_price, safe_old_price)
                    hollowness_task = measure_ob_hollowness(clean_sym, client, safe_price) # 👈 أضفنا هذا
                    
                    # نفذهم جميعاً في نفس اللحظة (صفر تأخير إضافي)
                    (cvd_boost, cvd_sig, cvd_trend_val), (delta_usd, buy_v, sell_v), (fut_boost, fut_sig, funding_val), is_orderbook_hollow = await asyncio.gather(
                        cvd_task, flow_task, futures_task, hollowness_task
                    )

                    z_score, _, _ = calculate_volume_zscore(df, window=720)
            except Exception as e:
                import traceback
                print(f"⚠️ Data Fetch Error in Manual Analysis: {e}")
                traceback.print_exc() # هذا السطر سيكشف لنا رقم السطر الخفي الذي يسبب المشكلة إذا ظهرت
                cvd_sig, buy_v, sell_v, fut_sig, z_score = None, 0, 0, None, 0
                delta_usd, funding_val = 0.0, 0.0

        # 2. كشف الفخاخ وتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        classic_trend = "Bullish" if ema20 > ema50 else "Bearish"
        
        final_trend_dir = classic_trend
        
        # إجبار تحويل الفوليوم إلى أرقام لتدمير أي نصوص قادمة من الـ API
        df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
        
        avg_vol_20 = df["volume"].tail(20).mean()
        current_vol = df["volume"].iloc[-1]

        
        # أ. شروط انعكاس الاتجاه من هابط إلى صاعد (اصطياد القاع الآمن)
        if classic_trend == "Bearish":
            # لا نعتمد على RSI وحده! نطلب إما دخول قوي للحيتان (CVD)، أو دايفرجنس إيجابي مع فوليوم أعلى من المتوسط
            vol_surge = current_vol > (avg_vol_20 * 1.5)
            if (cvd_sig == "Micro_Silent_Accumulation" or buy_v > sell_v * 1.5) or (last_rsi < 35 and last_macd > 0 and vol_surge):
                final_trend_dir = "Bullish" 
                
        # ب. شروط انعكاس الاتجاه من صاعد إلى هابط (الهروب من القمة المخادعة)
        elif classic_trend == "Bullish":
            # نطلب تصريف مخفي (CVD سلبي) أو تشبع بيعي مع فوليوم بيع عالي
            vol_surge = current_vol > (avg_vol_20 * 1.5)
            if (cvd_sig == "Hidden_Distribution" or sell_v > buy_v * 1.5) or (last_rsi > 75 and last_macd < 0 and vol_surge):
                final_trend_dir = "Bearish" 
        # 3. حساب الدعم والمقاومة والأهداف بناءً على الاتجاه "المُوحّد" لمنع التضارب
                # 3. حساب الدعم والمقاومة والأهداف في الخلفية لمنع التضارب والتعليق
        trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = await asyncio.to_thread(
            calculate_smart_trend_and_targets, df, price, db_vol_float, lang, final_trend_dir
        )


        # 4. تكييف النص ليطابق الاكتشاف المؤسساتي بدقة        # استخراج حالة الفوليوم والفجوة        # استخراج حالة الفوليوم والفجوة        # 4. تكييف النص ليطابق الاكتشاف المؤسساتي بدقة
        fvg_text = " [" + market_action.split("[")[1] if "[" in market_action else ""
        vol_text = " + فوليوم انفجاري" if "انفجاري" in market_action else (" + فوليوم ميت" if "ميت" in market_action else "")

        # 🟢 هندسة مصفوفة صندوق التحوط (Trend vs Flow Matrix)
        # نحدد نية الحيتان (Flow) أولاً بناءً على CVD والسيولة
        vol_surge = current_vol > (avg_vol_20 * 1.5)
        bullish_flow = (cvd_sig == "Micro_Silent_Accumulation" or buy_v > (sell_v * 1.2)) or (last_rsi < 40 and last_macd > 0 and vol_surge)
        bearish_flow = (cvd_sig == "Hidden_Distribution" or sell_v > (buy_v * 1.2)) or (last_rsi > 70 and last_macd < 0 and vol_surge)

        # دمج الرؤية الهيكلية (market_action) مع سلوك الحيتان الحالي
        if classic_trend == "Bullish":
            if bearish_flow:
                # السعر صاعد، لكن الحيتان تبيع (هنا فقط نطلق عليه تصريف أو مخادع)
                trend_str = "ضعيف (تصريف مخفي)" if lang == "ar" else "Weak (Hidden Dist.)"
                market_action_text = f"{market_action} | سيطرة بيعية خفية تعاكس الاتجاه الصاعد." if lang == "ar" else f"{market_action} | Hidden selling countering uptrend."
            else:
                # السعر صاعد، والحيتان تشتري
                trend_str = "قوي جداً" if lang == "ar" else "Very Strong"
                market_action_text = f"{market_action} | ضخ سيولة مؤسساتي يدعم استمرار الصعود." if lang == "ar" else f"{market_action} | Institutional inflow supporting uptrend."
                
        else: # classic_trend == "Bearish"
            if bullish_flow:
                # السعر هابط، لكن الحيتان تشتري (هذا هو قاع التجميع، مستحيل أن يكتب هنا تصريف قمة)
                trend_str = "ارتداد (تجميع قاع)" if lang == "ar" else "Bounce (Bottom Accum.)"
                market_action_text = f"{market_action} | امتصاص لضغط البيع ومحاولة صامتة لبناء قاع." if lang == "ar" else f"{market_action} | Supply absorption, attempting to build a bottom."
            else:
                # السعر هابط، والحيتان تبيع بقسوة
                trend_str = "هبوط قوي" if lang == "ar" else "Strong Downtrend"
                market_action_text = f"{market_action} | ضغط بيعي مستمر وجفاف في طلبات الشراء اللحظية." if lang == "ar" else f"{market_action} | Sustained selling pressure and lack of bids."

        # إضافة نصوص الفوليوم والفجوات (FVG) إلى النهاية
        market_action_text += vol_text + fvg_text

        # 🟢 حالات المشتقات الاستثنائية (تطغى على ما سبق لأنها حركات تصفية عنيفة)
        if fut_sig == "Short_Squeeze":
            trend_str = "انفجار شورت" if lang == "ar" else "Short Squeeze"
            market_action_text = ("ضغط تصفية مراكز بيع يدفع السعر بقوة عكسية." if lang == "ar" else "Liquidation pressure pushing price upwards.") + vol_text + fvg_text
        elif fut_sig == "Short_Covering":
            trend_str = "إغلاق شورت" if lang == "ar" else "Short Covering"
            market_action_text = ("ارتداد مؤقت بسبب جني أرباح البائعين وليس قوة شرائية حقيقية." if lang == "ar" else "Temporary bounce from short taking profit, no real demand.") + vol_text + fvg_text

        # 🟢 استعادة تعريف متغيرات RSI و MACD
        macd_fmt = format_price(last_macd) if 'last_macd' in locals() and last_macd is not None else "0.0"
        safe_rsi = f"{last_rsi:.2f}" if 'last_rsi' in locals() and last_rsi is not None else "N/A"
        
        # 🟢 تحديد الاتجاه بشكل صارم ومباشر لعنوان التقرير
        real_trend = "صاعد" if final_trend_dir == "Bullish" else "هابط"
        if lang != "ar": real_trend = "Bullish" if final_trend_dir == "Bullish" else "Bearish"
        
        # ربط المتغير وتنبيه الـ AI إذا كانت العملة لامركزية (DEX)
        trend_strength = trend_str 
        market_action = market_action_text if not is_dex else (f"(تحليل شبكة DEX) | {market_action_text}" if lang == "ar" else f"(DEX Network) | {market_action_text}")

        if lang == "ar":
            prompt = f"""
أنت محلل بيانات كمية (Quant) صارم في صندوق "NaiF CHarT". مهمتك صياغة التقرير الفني لعملة {clean_sym} بناءً على الأرقام فقط.
⚠️ تحذير شديد: يمنع منعاً باتاً تغيير أرقام الدعم، المقاومة، الأهداف، أو الوقف. الأرقام محسوبة رياضياً ويجب نسخها كما هي في القالب.

قواعد التفسير المالي (التزم بها حرفياً لصياغة النقاط):
1. RSI: إذا > 70 (تشبع شرائي/خطر تصحيح). إذا < 30 (تشبع بيعي/فرصة ارتداد). بينهما (حيادي).
2. ADX: إذا > 25 (ترند قوي). إذا < 25 (ترند ضعيف/مسار عرضي).
3. MACD: موجب (زخم شرائي). سالب (زخم بيعي).
4. السيولة: قم بإعادة صياغة الجملة المعطاة لك بأسلوب مؤسساتي جاف ومقنع.

⚠️ انسخ هذا القالب بدقة، واكتب تعليقاً فنياً من سطر واحد ومباشر لكل مؤشر (بدون مقدمات وبدون نصائح استثمارية):

📊 <b>التحليل لـ {clean_sym}</b> | {tf} | <code>{format_price(price)}$</code>
الاتجاه: {real_trend} ({trend_strength})

📉 <b>الدعم والمقاومة</b>
الدعم الأقرب: <code>{format_price(calc_sup)}$</code>
المقاومة الأقرب: <code>{format_price(calc_res)}$</code>

🎯 <b>الأهداف السعرية (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>وقف الخسارة (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>تحليل المؤشرات</b>
• السيولة: (أعد صياغة هذه الجملة باحترافية: {market_action})
• مؤشر(RSI) ({safe_rsi}): (اكتب سطر واحد يفسر الرقم بناءً على القواعد)
• مؤشر(MACD) ({macd_fmt}): (اكتب سطر واحد يفسر الزخم)
• مؤشر(ADX) ({adx_val:.1f}): (اكتب سطر واحد يفسر قوة الاتجاه)
"""
        else:
            real_trend = "Bullish" if trend_dir == "Bullish" else "Bearish"
            trend_strength = trend_str
            
            prompt = f"""
You are a strict Quant Analyst at "NaiF CHarT". Your task is to format the technical report for {clean_sym} based strictly on the provided data.
⚠️ CRITICAL WARNING: DO NOT alter the Support, Resistance, TP, or SL numbers. They are mathematically calculated.

Financial Interpretation Rules (Follow strictly):
1. RSI: > 70 (Overbought/Correction risk). < 30 (Oversold/Bounce opportunity). 30-70 (Neutral).
2. ADX: > 25 (Strong trend). < 25 (Weak/Ranging).
3. MACD: Positive (Bullish momentum). Negative (Bearish momentum).
4. Liquidity: Rephrase the provided sentence into a cold, institutional tone.

⚠️ Copy this exact HTML template, writing exactly ONE precise analytical line per indicator (No fluff, no financial advice):

📊 <b>Analysis: {clean_sym}</b> | {tf} | <code>{format_price(price)}$</code>
Trend: {real_trend} ({trend_strength})

📉 <b>Support & Resistance</b>
Nearest Support: <code>{format_price(calc_sup)}$</code>
Nearest Resistance: <code>{format_price(calc_res)}$</code>

🎯 <b>Price Targets (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>Stop Loss (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>Indicator Analysis</b>
• Liquidity: (Professionally rephrase this: {market_action})
• RSI ({safe_rsi}): (One line interpreting the number based on the rules)
• MACD ({macd_fmt}): (One line interpreting momentum)
• ADX ({adx_val:.1f}): (One line interpreting trend strength)
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
        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")
                # 🧠 ترقية جدول الذكاء الاصطناعي (إضافة كل الأعمدة المؤسساتية الجديدة إن لم تكن موجودة)
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS whale_inflow_score DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS ob_skewness DOUBLE PRECISION DEFAULT 1.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS micro_volatility DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS cvd_divergence DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS funding_rate DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS volume_ratio DOUBLE PRECISION DEFAULT 1.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS sp500_trend DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS sentiment_score DOUBLE PRECISION DEFAULT 50.0")

        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    asyncio.create_task(smart_radar_watchdog(pool))
    asyncio.create_task(macro_data_worker()) # 🌍 تشغيل عامل الماكرو
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