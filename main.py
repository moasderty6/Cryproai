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
import math

def quant_cdf_score(z_value, limit=100.0):
    """
    محرك التقييم الاحتمالي (Probability Engine):
    يحول قيمة Z-Score إلى نسبة مئوية سلسة جداً (CDF) باستخدام دالة الخطأ (erf).
    مثال: z=0 يعطي 50, z=2 يعطي 97.7
    """
    probability = 0.5 * (1.0 + math.erf(z_value / math.sqrt(2.0)))
    return probability * limit

def quant_sigmoid_score(value, sensitivity=1.0, limit=100.0):
    """
    محرك النعومة (Sigmoid Activation):
    يحول القيم المطلقة المفتوحة أو السلبية (مثل Imbalance أو Funding) إلى سكور بين 0 و 100 بسلاسة.
    """
    # حماية من الطفح الرياضي (Overflow) للقيم المتطرفة جداً
    safe_value = max(-20.0, min(20.0, sensitivity * value))
    sig = 1.0 / (1.0 + math.exp(-safe_value))
    return sig * limit
def quant_fat_tail_score(z_value, tail_weight=1.5, limit=100.0):
    """
    محرك التقييم للذيول السميكة (Fat-Tailed / Cauchy CDF Engine):
    مصمم خصيصاً لسوق الكريبتو. يستخدم توزيع كوشي لاستيعاب أحجام التداول المتطرفة (Black Swans)
    بدون سحق البيانات مبكراً كما تفعل دالة الخطأ (erf).
    - tail_weight (γ): معامل التحكم في سمك الذيل. 1.5 يعتبر قياسياً لأسواق الكريبتو.
    """
    # حماية من القيم السالبة جداً أو الصفر لتجنب أي أخطاء رياضية
    safe_z = max(0.0, float(z_value))
    
    # استخدام arctan لاستيعاب الأرقام المتطرفة جداً بمرونة
    probability = 0.5 + (math.atan(safe_z / tail_weight) / math.pi)
    
    return probability * limit
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
BLACKLISTED_COINS = {"TOMO", "EUR", "TVK", "OMNI", "GAL", "USD1", "COCOS", "LRC", "BUSD", "TUSD", "USDC", "USDE", "BFUSD", "RLUSD", "POLY", "XUSD", "U", "USDT", "DAI", "USDP", "FDUSD", "USDD", "PYUSD", "FRAX", "LUSD", "GUSD", "ZUSD", "VAI", "MAI", "DOLA", "EURC", "EURT", "EURS", "AEUR", "EURA", "TRY", "BRL", "ZAR"}
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
        "price_amount": 10.01,
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
    [Institutional Level] تدريب النموذج على التنبؤ بـ (Trade Quality Score)
    بدلاً من مجرد 0 أو 1، ليعرف البوت "مدى جودة" الإشارة.
    """
    global AI_QUANT_MODEL
    df = pd.DataFrame(records)
    
    # 1. تنظيف البيانات من القيم المفقودة
    df = df.dropna(subset=['trade_quality_score'])
    
    # 2. تحديد المدخلات المؤسساتية (15 بُعداً)
    X = df[['market_regime', 'sp500_trend_pct', 'sentiment_score', 
            'vol_z_score', 'cvd_to_vol_ratio', 'imbalance_ratio', 
            'ob_skewness', 'whale_dominance_pct', 'adx', 'rsi', 
            'micro_volatility_pct', 'cvd_divergence', 'funding_rate']]

    # 3. الهدف هو التنبؤ بجودة الصفقة (Regression)
    y = df['trade_quality_score']
    
    import xgboost as xgb
    # إعدادات متقدمة جداً لمنع الـ Overfitting (حفظ البيانات بدلاً من فهمها)
    model = xgb.XGBRegressor(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        objective='reg:squarederror' # التنبؤ برقم مستمر من -1 إلى 1
    )
    
    model.fit(X, y)
    AI_QUANT_MODEL = model
    return True

async def ai_trainer_worker(pool):
    """عامل التدريب: يستيقظ كل 12 ساعة لتطوير عقل البوت"""
    await asyncio.sleep(60) 
    while True:
        try:
            async with pool.acquire() as conn:
                # جلب البيانات المؤسساتية بالكامل
                records = await conn.fetch("""
                    SELECT market_regime, sp500_trend_pct, sentiment_score, 
                           vol_z_score, cvd_to_vol_ratio, imbalance_ratio, 
                           ob_skewness, whale_dominance_pct, adx, rsi, 
                           micro_volatility_pct, cvd_divergence, funding_rate,
                           trade_quality_score
                    FROM ml_training_data 
                    WHERE is_processed = 1
                """)
                
                if len(records) >= 100: # 🎯 عتبة الانطلاق (Critical Mass)
                    print(f"🧠 [AI Trainer] Mass training on {len(records)} samples...")
                    records_dict = [dict(r) for r in records]
                    await asyncio.to_thread(train_xgboost_sync, records_dict)
                    print("✅ [AI Trainer] Engine Optimized to Hedge Fund Level.")
                else:
                    print(f"⏳ [AI Trainer] Collecting data... ({len(records)}/100)")
                    
        except Exception as e:
            print(f"⚠️ AI Trainer Error: {e}")
        await asyncio.sleep(43200) # 12 ساعة

def predict_signal_sync(features: dict) -> float:
    """يتوقع جودة الصفقة بناءً على النموذج المدرب"""
    if AI_QUANT_MODEL is None:
        return -1.0 
        
    input_data = pd.DataFrame([{
        'market_regime': int(features.get('market_regime', 0)),
        'sp500_trend_pct': float(features.get('sp500_trend', 0.0)),
        'sentiment_score': float(features.get('sentiment_score', 50.0)),
        'vol_z_score': float(features.get('z_score', 0.0)),
        'cvd_to_vol_ratio': float(features.get('cvd_to_vol_ratio', 0.0)),
        'imbalance_ratio': float(features.get('ofi_imbalance', 0.0)),
        'ob_skewness': float(features.get('ob_skewness', 1.0)),
        'whale_dominance_pct': float(features.get('whale_inflow', 0.0)),
        'adx': float(features.get('adx', 0.0)),
        'rsi': float(features.get('rsi', 50.0)),
        'micro_volatility_pct': float(features.get('micro_volatility', 0.0)),
        'cvd_divergence': float(features.get('cvd_divergence', 0.0)),
        'funding_rate': float(features.get('funding_rate', 0.0))
    }])

    # التنبؤ بسكور الجودة المتوقع
    predicted_quality = AI_QUANT_MODEL.predict(input_data)[0]
    
    # تحويل السكور (من -1 إلى 1) إلى نسبة مئوية (0% إلى 100%) لسهولة القراءة
    confidence_pct = ((predicted_quality + 1) / 2) * 100
    return float(confidence_pct)

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
            try: # 🟢 إضافة حاجز الحماية لمنع انهيار الحلقة وإغلاق الـ Client
                # اسحب عملة من الطابور
                coin_mock_data = await radar_processing_queue.get()
                symbol = f"{coin_mock_data['symbol']}USDT"
                
                async with pool.acquire() as conn:
                    # فحص هل أرسلنا العملة في آخر 7 أيام؟
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

                        # جلب الماكرو وإرسالها للتحليل
                        market_regime = await detect_market_regime(client)
                        asyncio.create_task(analyze_radar_coin(coin_mock_data, client, market_regime, sem))
                
                # إخبار الطابور أن المهمة انتهت
                radar_processing_queue.task_done()
                await asyncio.sleep(1) # استراحة ثانية بين كل تحليل لحماية الـ API
                
            except Exception as e:
                print(f"⚠️ خطأ عابر في Worker الرادار، الاتصال مستمر: {e}")
                await asyncio.sleep(2)

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
async def get_micro_cvd_absorption(symbol, client, base_interval="1m", is_dex: bool = False):
    """
    يكتشف التجميع الصامت، يتأقلم مع الفريم الزمني المطلوب.
    """
    if is_dex:
        return 0.0, None, 0.0 # قيم صفرية آمنة لعملات الـ DEX

    cvd_trend = 0.0 
    try:
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
async def detect_btc_relative_strength(symbol: str, client: httpx.AsyncClient, is_dex: bool = False):
    """
    [UPGRADED] Statistical Beta Decoupling Engine
    """
    if is_dex:
        return 0.0 # إرجاع حيادي للديكس
        
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
async def analyze_orderbook_spoofing_instant(symbol: str, client: httpx.AsyncClient, current_price: float, is_dex: bool = False):
    """
    محرك كشف التلاعب اللحظي (Instant VWAD & Skewness)
    """
    if is_dex: # تخطي آمن لعملات الديكس
        return {"is_hollow": False, "imbalance": 0.0, "is_spoofed": False, "bid_pressure_ratio": 1.0}

    clean_sym = symbol.replace("USDT", "") + "USDT"
    url = f"{get_random_binance_base()}/api/v3/depth?symbol={clean_sym}&limit=500"
    
    try:
        await binance_rate_limit_event.wait()
        res = await client.get(url, timeout=3.0)
        if res.status_code != 200: 
            return {"is_hollow": False, "imbalance": 0.0, "is_spoofed": False}
        
        data = res.json()
        
        bids = np.array([[float(p), float(v)] for p, v in data.get('bids', [])])
        asks = np.array([[float(p), float(v)] for p, v in data.get('asks', [])])
        
        if len(bids) == 0 or len(asks) == 0:
            return {"is_hollow": False, "imbalance": 0.0, "is_spoofed": False}

        # 1. حساب السيولة المتراكمة في النطاقات الحساسة
        bid_vol_1pct = np.sum(bids[bids[:, 0] >= current_price * 0.99][:, 1] * bids[bids[:, 0] >= current_price * 0.99][:, 0])
        bid_vol_5pct = np.sum(bids[bids[:, 0] >= current_price * 0.95][:, 1] * bids[bids[:, 0] >= current_price * 0.95][:, 0])
        
        ask_vol_1pct = np.sum(asks[asks[:, 0] <= current_price * 1.01][:, 1] * asks[asks[:, 0] <= current_price * 1.01][:, 0])
        ask_vol_5pct = np.sum(asks[asks[:, 0] <= current_price * 1.05][:, 1] * asks[asks[:, 0] <= current_price * 1.05][:, 0])

        # 2. كشف الجدران الوهمية (Hollowness)
        # إذا كانت 60% من سيولة الشراء متمركزة في أول 1% فقط، فهذا جدار وهمي سيسحب فجأة
        is_bid_hollow = bid_vol_1pct > (bid_vol_5pct * 0.6) and bid_vol_5pct > 0
        
        # 3. كشف التلاعب الهجومي (Spoofing)
        # سيولة ضخمة جداً متكدسة على مسافة قريبة للضغط على السعر
        is_ask_spoofed = ask_vol_1pct > (bid_vol_1pct * 3.0)
        
        # 4. الخلل الكلي (Orderflow Imbalance)
        imbalance = (bid_vol_5pct - ask_vol_5pct) / (bid_vol_5pct + ask_vol_5pct + 1e-8)

        return {
            "is_hollow": is_bid_hollow,
            "imbalance": round(imbalance, 2),
            "is_spoofed": is_ask_spoofed,
            "bid_pressure_ratio": bid_vol_1pct / (ask_vol_1pct + 1e-8)
        }
        
    except Exception:
        return {"is_hollow": False, "imbalance": 0.0, "is_spoofed": False}

async def get_institutional_orderflow(symbol, client, minutes=15):
    """ 
    [ULTRA UPGRADED] Global Tick-Level Footprint Engine 🌍
    يجلب الصفقات اللحظية الحقيقية مع عزل ضجيج الأفراد وصناع السوق (Stratification)
    """
    import time
    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)
    
    clean_sym = symbol.replace("USDT", "")
    sym_binance = f"{clean_sym}USDT"
    sym_bybit = f"{clean_sym}USDT"
    sym_okx = f"{clean_sym}-USDT"

    # 🚀 فلتر الحيتان: تجاهل أي صفقة تقل عن 1000 دولار
    MIN_WHALE_TRADE_USD = 1000.0 
    
    # --- دالة فرعية 1: بايننس ---
    async def fetch_binance():
        try:
            base_url = get_random_binance_base()
            res = await client.get(f"{base_url}/api/v3/aggTrades", params={
                "symbol": sym_binance, "startTime": start_time, "endTime": end_time, "limit": 1000 
            }, timeout=4.0)
            
            if res.status_code == 200:
                trades = res.json()
                b_vol, s_vol = 0.0, 0.0
                prices = []
                for t in trades:
                    price = float(t['p'])
                    amount = float(t['q']) * price
                    if amount < MIN_WHALE_TRADE_USD: continue # 👈 (Trade Stratification)
                    prices.append(price)
                    if t['m']: s_vol += amount  
                    else: b_vol += amount       
                return b_vol, s_vol, prices
        except: pass
        return 0.0, 0.0, []

    # --- دالة فرعية 2: Bybit ---
    async def fetch_bybit():
        try:
            res = await client.get("https://api.bybit.com/v5/market/recent-trade", params={
                "category": "spot", "symbol": sym_bybit, "limit": 1000
            }, timeout=4.0)
            
            if res.status_code == 200:
                trades = res.json().get('result', {}).get('list', [])
                b_vol, s_vol = 0.0, 0.0
                for t in trades:
                    amount = float(t['v']) * float(t['p'])
                    if amount < MIN_WHALE_TRADE_USD: continue # 👈 (Trade Stratification)
                    if t['S'] == 'Buy': b_vol += amount
                    else: s_vol += amount
                return b_vol, s_vol
        except: pass
        return 0.0, 0.0

    # --- دالة فرعية 3: OKX ---
    async def fetch_okx():
        try:
            res = await client.get("https://www.okx.com/api/v5/market/trades", params={
                "instId": sym_okx, "limit": 500
            }, timeout=4.0)
            
            if res.status_code == 200:
                trades = res.json().get('data', [])
                b_vol, s_vol = 0.0, 0.0
                for t in trades:
                    amount = float(t['sz']) * float(t['px'])
                    if amount < MIN_WHALE_TRADE_USD: continue # 👈 (Trade Stratification)
                    if t['side'] == 'buy': b_vol += amount
                    else: s_vol += amount
                return b_vol, s_vol
        except: pass
        return 0.0, 0.0

    # ... (باقي كود الدالة كما هو تماماً بدون تغيير) ...

    # ==========================================
    # 🚀 الإطلاق المتزامن (Scatter-Gather)
    # ==========================================
    try:
        await binance_rate_limit_event.wait() # حماية بايننس
        
        # نرسل الـ 3 طلبات في نفس اللحظة
        binance_res, bybit_res, okx_res = await asyncio.gather(
            fetch_binance(), fetch_bybit(), fetch_okx()
        )
        
        # استخراج البيانات
        bin_buy, bin_sell, bin_prices = binance_res
        byb_buy, byb_sell = bybit_res
        okx_buy, okx_sell = okx_res
        
        # حساب السيولة العالمية (Global Flow)
        global_buy_vol = bin_buy + byb_buy + okx_buy
        global_sell_vol = bin_sell + byb_sell + okx_sell
        
        global_delta = global_buy_vol - global_sell_vol
        total_global_vol = global_buy_vol + global_sell_vol
        signal = None
        
        # --- محرك اكتشاف الامتصاص (Limit Absorption) ---
        # نعتمد على حركة أسعار بايننس كمؤشر قياسي لحركة السوق اللحظية
        if bin_prices and total_global_vol > 0:
            price_series = pd.Series(bin_prices)
            price_range_pct = (price_series.max() - price_series.min()) / (price_series.min() + 1e-8)
            
            # إذا كان السعر شبه ثابت (نطاق ضيق جداً < 0.5%) 
            # والدلتا الشرائية العالمية ضخمة جداً (تشكل أكثر من 25% من السيولة)
            if price_range_pct <= 0.005 and global_delta > (total_global_vol * 0.25):
                signal = "Limit_Absorption"
                
        return global_delta, global_buy_vol, global_sell_vol, signal

    except Exception as e:
        print(f"⚠️ Global Flow Error: {e}")
        
    return 0.0, 0.0, 0.0, None



async def detect_spot_perp_divergence(symbol: str, client: httpx.AsyncClient):
    """
    [Quant Upgrade] True CVD Correlation Engine
    يقيس الانحراف بين سيولة السبوت والعقود عبر الارتباط الإحصائي
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    spot_url = f"{get_random_binance_base()}/api/v3/klines?symbol={clean_sym}&interval=1m&limit=60"
    fapi_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean_sym}&interval=1m&limit=60"
    
    try:
        await binance_rate_limit_event.wait()
        spot_res, fapi_res = await asyncio.gather(
            client.get(spot_url, timeout=5.0),
            client.get(fapi_url, timeout=5.0)
        )
        
        if spot_res.status_code != 200 or fapi_res.status_code != 200: return 0.0

        spot_df = pd.DataFrame(spot_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        fapi_df = pd.DataFrame(fapi_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        
        # حساب CVD للسبوت
        spot_df['v'] = pd.to_numeric(spot_df['v'])
        spot_df['tbv'] = pd.to_numeric(spot_df['tbv'])
        spot_df['delta'] = spot_df['tbv'] - (spot_df['v'] - spot_df['tbv'])
        spot_cvd = spot_df['delta'].cumsum()
        
        # حساب CVD للفيوتشرز
        fapi_df['v'] = pd.to_numeric(fapi_df['v'])
        fapi_df['tbv'] = pd.to_numeric(fapi_df['tbv'])
        fapi_df['delta'] = fapi_df['tbv'] - (fapi_df['v'] - fapi_df['tbv'])
        fapi_cvd = fapi_df['delta'].cumsum()

        # حساب الارتباط (Correlation) بين المسارين
        correlation = spot_cvd.corr(fapi_cvd)
        spot_total_delta = spot_cvd.iloc[-1] - spot_cvd.iloc[0]

        if pd.isna(correlation): return 0.0

        # التقييم المستنبط رياضياً:
        # إذا كان الارتباط سلبياً (أقل من -0.5) والسبوت يشتري بقوة = تجميع مخفي وتحوط في العقود
        if correlation < -0.5 and spot_total_delta > 0:
            return 10.0 * abs(correlation) # سكور ديناميكي يصل لـ 10
        # إذا كان الارتباط سلبياً والسبوت يبيع = تصريف حقيقي
        elif correlation < -0.5 and spot_total_delta < 0:
            return -10.0 * abs(correlation)
            
        # إذا كانوا يتحركون معاً بشراسة (ارتباط > 0.8)
        elif correlation > 0.8 and spot_total_delta > 0:
            return 5.0
            
        return 0.0

    except Exception:
        return 0.0

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

            if len(oi_data) < 2: return 0.0, None, 0.0, 0.0



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
                        # ... (كودك الحالي) ...
            if funding_rate < -0.0005: 
                score_modifier += 12.0
                if not futures_signal: futures_signal = "Short_Squeeze"
            elif funding_rate > 0.0005:
                score_modifier -= 10.0

            # 🚀 التعديل: إرجاع oi_change_pct للمحرك الراداري
            return score_modifier, futures_signal, funding_rate, oi_change_pct 
            
    except Exception: pass
    # 🚀 التعديل: إرجاع 4 قيم أصفار في حال الخطأ
    return 0.0, None, 0.0, 0.0 

def calculate_volume_zscore(df, window=720):
    """
    محرك شذوذ الفوليوم المؤسساتي (Robust Z-Score) باستخدام MAD
    مضاد للتشوه: يتجاهل الشموع العملاقة السابقة تماماً.
    """
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    
    # 1. حساب الوسيط المتحرك (Median) بدلاً من المتوسط
    rolling_median = df["volume"].rolling(window=window, min_periods=100).median()
    
    # 2. دالة حساب الانحراف المطلق السريعة
    def calculate_mad(x):
        return np.median(np.abs(x - np.median(x)))
    
    # 3. تطبيق MAD على النافذة الزمنية (نستخدم raw=True لتسريع المعالجة)
    rolling_mad = df["volume"].rolling(window=window, min_periods=100).apply(calculate_mad, raw=True)
    
    # 4. تطبيق معادلة Robust Z-Score مع حماية من القسمة على صفر
    # المعامل 1.4826 لمعايرة النتيجة لتصبح مطابقة للـ Standard Deviation
    df["z_score"] = (df["volume"] - rolling_median) / ((rolling_mad * 1.4826) + 1e-8)
    
    current_z = df["z_score"].iloc[-1]
    last_median = rolling_median.iloc[-1]
    last_mad = rolling_mad.iloc[-1]
    
    # حماية من القسمة على صفر في العملات الميتة جداً
    if pd.isna(current_z) or current_z == float('inf'):
        current_z = 0.0

    # إرجاع 3 قيم تماماً كما يتوقع باقي الكود (Z-Score, Median كبديل لـ Mean, و MAD كبديل لـ Std)
    return float(current_z), float(last_median), float(last_mad)
async def silent_data_harvester_worker(pool):
    """
    عامل الحصاد الصامت: يعمل في الخلفية بهدوء، يحلل عملة واحدة كل دقيقة 
    لجمع البيانات اللحظية (بما فيها الأوردر بوك والتدفق) دون التأثير على البوت.
    """
    await asyncio.sleep(120) # ننتظر دقيقتين بعد تشغيل البوت ليستقر
    print("🌾 [Data Harvester] Engine is Online. Collecting ML Data silently...")

    while True:
        try:
            async with pool.acquire() as conn:
                records = await conn.fetch("SELECT symbol FROM radar_history")
                ignored_symbols = {r['symbol'] for r in records}

            async with httpx.AsyncClient(timeout=30) as client:
                await binance_rate_limit_event.wait()
                
                # جلب حالة الماكرو
                market_regime = await detect_market_regime(client)
                
                # جلب قائمة العملات (التيثر فقط)
                base_url = get_random_binance_base()
                res = await client.get(f"{base_url}/api/v3/ticker/24hr", timeout=10)
                
                if res.status_code != 200:
                    await asyncio.sleep(60)
                    continue
                
                all_tickers = res.json()
                coins = []
                
                for t in all_tickers:
                    symbol = t["symbol"]
                    if not symbol.endswith("USDT"): continue
                    clean_sym = symbol.replace("USDT", "")
                    if clean_sym in BLACKLISTED_COINS: continue
                    
                    vol_usd = float(t["quoteVolume"])
                    if vol_usd >= 400_000: # الفلتر المبدئي للسيولة
                        coins.append({"symbol": clean_sym, "price": float(t["lastPrice"]), "volume": vol_usd})
                
                # ترتيب العملات حسب السيولة وأخذ أعلى 350
                coins = sorted(coins, key=lambda x: x["volume"], reverse=True)[:350]
                
                print(f"🔄 [Harvester] Starting new cycle for {len(coins)} coins...")

                # ⏳ التقطير الصامت: معالجة عملة واحدة فقط كل 50 ثانية
                for c in coins:
                    await binance_rate_limit_event.wait()
                    sym = c["symbol"]
                    price = c["price"]
                    pair = f"{sym}USDT"
                    
                    try:
                        # 1. جلب الشموع (15 دقيقة للتدريب السريع والدقيق)
                        candles = await get_candles_binance(pair, "15m", limit=750)
                        if not candles: continue
                        
                        df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)
                        
                        # 2. جلب البيانات اللحظية (التي لا تحفظها بايننس تاريخياً)
                        cvd_boost, cvd_sig, cvd_trend = await get_micro_cvd_absorption(pair, client, "15m")
                        global_ob_pressure = await get_aggregated_orderbook(client, sym)
                        depth_data = await analyze_orderbook_spoofing_instant(sym, client, price)
                        tick_delta, tick_buy, tick_sell, limit_abs = await get_institutional_orderflow(pair, client)
                                                # 🚀 أضفنا الـ _ (الرابع) لاستقبال قيمة الـ OI وتجاهلها هنا
                        _, fut_sig, funding_val, _ = await get_futures_liquidity(sym, client, price, float(df["close"].iloc[-3]))
                        
                        avg_vol_20 = df["volume"].tail(20).mean()
                        avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
                        cvd_ratio_pct = (cvd_trend * price / avg_vol_usd) * 100 if avg_vol_usd > 0 else 0.0
                        
                        # حساب القوة النسبية للماكرو والتذبذب
                        ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
                        cvd_divergence = 1.0 if (price > ema200_val and cvd_trend < 0) else -1.0 if (price < ema200_val and cvd_trend > 0) else 0.0
                        micro_volatility = df['close'].tail(20).pct_change().std() * 100
                        
                        current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"
                        regime_map = {"Trending_Bull": 1, "Trending_Bear": 2, "Ranging": 3}
                        
                        # تجهيز الميزات (Features) وتسجيلها بصمت
                        ml_features = {
                            'market_regime': regime_map.get(current_regime_trend, 0),
                            'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)),
                            'sentiment_score': float(MACRO_CACHE.get("sentiment_score", 50.0)),
                            'z_score': float(current_z),
                            'cvd_to_vol_ratio': float(cvd_ratio_pct),
                            'ofi_imbalance': float(depth_data.get('imbalance', 0.0)),
                            'ob_skewness': float(depth_data.get('bid_pressure_ratio', 1.0)),
                            'whale_inflow': await get_whale_inflow_score(),
                            'adx': float(current_adx),
                            'rsi': float(last_rsi),
                            'micro_volatility': float(micro_volatility) if not pd.isna(micro_volatility) else 0.0,
                            'cvd_divergence': float(cvd_divergence),
                            'funding_rate': float(funding_val)
                        }
                        
                        # تسجيل البيانات (بدون فلتر، نريد الجيد والسيء)
                        await log_signal_for_ml(pool, sym, price, ml_features)

                    except Exception as e:
                        pass # صمت تام عند الأخطاء لتستمر الحلقة
                    
                    # 🛡️ الجدار السري لحماية السيرفر: استراحة 50 ثانية بين كل عملة وعملة
                    await asyncio.sleep(50) 
                    
        except Exception as e:
            print(f"⚠️ Harvester Error: {e}")
            await asyncio.sleep(300)

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
    """
    [Institutional Upgrade] Algorithmic Execution Detection (TWAP/VWAP & Iceberg)
    يبحث عن البصمة الإحصائية لخوارزميات المؤسسات التي تقوم بتقطيع الطلبات الكبيرة إلى 
    مئات الطلبات الصغيرة ذات الأحجام المتجانسة والكثافة الزمنية العالية.
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    # جلب آخر 1000 صفقة (تمثل دقائق أو ثواني في العملات النشطة)
    trades_url = f"{get_random_binance_base()}/api/v3/trades?symbol={clean_sym}&limit=1000"
    
    try:
        res = await client.get(trades_url, timeout=5.0)
        if res.status_code != 200:
            return 0.0
            
        trades = res.json()
        if not trades: return 0.0

        df = pd.DataFrame(trades)
        df['qty'] = df['qty'].astype(float)
        df['price'] = df['price'].astype(float)
        df['value'] = df['qty'] * df['price']
        
        # تقسيم الصفقات: من الذي يسحب السيولة (Taker)؟
        buy_trades = df[~df['isBuyerMaker'].astype(bool)]
        sell_trades = df[df['isBuyerMaker'].astype(bool)]
        
        buy_vol = buy_trades['value'].sum()
        sell_vol = sell_trades['value'].sum()
        total_vol = buy_vol + sell_vol
        
        if total_vol == 0: return 0.0

        # --- 🧠 Quantitative Algo Footprint Engine ---
        
        # 1. التكدس الزمني (Time-Execution Clustering)
        # كم عدد الصفقات التي نُفذت في نفس الثانية بالضبط؟ (دليل على HFT Sweeps)
        df['time_sec'] = df['time'] // 1000
        cluster_counts = df.groupby('time_sec')['value'].count()
        algo_clusters = cluster_counts[cluster_counts > 7] # 7 صفقات فأكثر في ثانية واحدة
        cluster_weight = min(len(algo_clusters) / 10.0, 4.0) # أقصى تعزيز 4 نقاط

        # 2. كشف تجانس الأحجام (Iceberg / TWAP Variance Detection)
        # تجاهل صفقات التجزئة البسيطة للأفراد (أقل من 200 دولار) للتركيز على مسار المؤسسات
        meaningful_buys = buy_trades[buy_trades['value'] > 200]
        meaningful_sells = sell_trades[sell_trades['value'] > 200]

        algo_buy_score = 0.0
        algo_sell_score = 0.0

        # Coefficient of Variation (CV) = Standard Deviation / Mean
        # الخوارزميات تترك CV منخفض جداً لأنها تقطع الأحجام بشكل رياضي متساوٍ
        if len(meaningful_buys) > 15:
            buy_cv = meaningful_buys['value'].std() / (meaningful_buys['value'].mean() + 1e-8)
            if buy_cv < 1.2: # تجانس رياضي غير طبيعي (روبوت تجميع)
                algo_buy_score += (1.2 - buy_cv) * 10

        if len(meaningful_sells) > 15:
            sell_cv = meaningful_sells['value'].std() / (meaningful_sells['value'].mean() + 1e-8)
            if sell_cv < 1.2: # تجانس رياضي (روبوت تصريف)
                algo_sell_score += (1.2 - sell_cv) * 10

        # 3. الهيمنة الاتجاهية (Directional Delta)
        delta_pct = (buy_vol - sell_vol) / total_vol
        
        # 4. دمج السكور النهائي بناءً على بصمة الـ Algo + هيمنة الاتجاه
        final_score = 0.0
        
        # إذا كان هناك اختلال شرائي مع بصمة خوارزميات التجميع
        if delta_pct > 0.05:
            final_score = 4.0 + algo_buy_score + cluster_weight + (delta_pct * 10)
        # إذا كان هناك اختلال بيعي مع بصمة خوارزميات التصريف
        elif delta_pct < -0.05:
            final_score = -4.0 - algo_sell_score - cluster_weight + (delta_pct * 10)
            
        # تحجيم النتيجة لتتناسب مع أوزان الرادار الأساسي (بين -12 و +12)
        return round(max(-12.0, min(12.0, final_score)), 2)

    except Exception as e:
        return 0.0


async def detect_phantom_liquidity_ws(symbol: str, client: httpx.AsyncClient, current_price: float, volume_24h: float, duration: float = 3.0):
    """
    [ULTRA INSTITUTIONAL] Phantom Liquidity & TWAP Rhythm Engine 🕸️
    يدمج بين Time-CV و Iceberg Regeneration لاصطياد نشاط الـ Dark Pools والـ OTC
    """
    clean_sym = symbol.replace("USDT", "").lower() + "usdt"
    # دمج بثين في اتصال واحد: الصفقات اللحظية + الأوردر بوك السريع
    ws_url = f"wss://stream.binance.com:9443/stream?streams={clean_sym}@aggTrade/{clean_sym}@depth5@100ms"
    
    taker_buy_vol, taker_sell_vol = 0.0, 0.0
    buy_times, sell_times = [], []
    depth_snapshots = []
    
    try:
        async with websockets.connect(ws_url, ping_interval=None, close_timeout=1) as ws:
            start_time = time.time()
            while time.time() - start_time < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    data = json.loads(msg)
                    stream = data.get('stream', '')
                    payload = data.get('data', {})
                    
                    # 1. التقاط إيقاع الصفقات (Execution Rhythm)
                    if 'aggTrade' in stream:
                        trade_vol = float(payload.get('p', 0)) * float(payload.get('q', 0))
                        trade_time = payload.get('T', 0)
                        is_buyer_maker = payload.get('m', False)
                        
                        if not is_buyer_maker: # Taker Buy (يضرب العروض)
                            taker_buy_vol += trade_vol
                            buy_times.append(trade_time)
                        else: # Taker Sell (يضرب الطلبات)
                            taker_sell_vol += trade_vol
                            sell_times.append(trade_time)
                            
                    # 2. التقاط عمق السوق (Liquidity State)
                    elif 'depth' in stream:
                        bids_vol = sum([float(p)*float(v) for p, v in payload.get('bids', [])])
                        asks_vol = sum([float(p)*float(v) for p, v in payload.get('asks', [])])
                        depth_snapshots.append({'bids': bids_vol, 'asks': asks_vol})
                        
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        # 🛡️ Fallback: إذا فشل الـ WebSocket، نعود فوراً لدالتك القديمة القوية
        return await detect_real_whale_trades(symbol, client, volume_24h), []

    # إذا لم نجمع بيانات كافية خلال 3 ثوانٍ، نستخدم الدالة القديمة
    if len(depth_snapshots) < 2:
        return await detect_real_whale_trades(symbol, client, volume_24h), []

    # ==========================================
    # 🧠 المحرك الرياضي (Quant Logic)
    # ==========================================
    score_boost = 0.0
    phantom_tags = []
    
    # 1. حساب معدل تجدد الجليد (Iceberg Regeneration Rate - IRR)
    start_bids = depth_snapshots[0]['bids']
    end_bids = depth_snapshots[-1]['bids']
    bid_depth_change = start_bids - end_bids 
    
    # المعادلة: السيولة المباعة - التغير في عمق الطلبات = السيولة المخفية التي تجددت
    regenerated_bids = taker_sell_vol - bid_depth_change
    
    # إذا باع الأفراد بقوة، لكن الطلبات لم تنقص بل تجددت (امتصاص الحيتان المخفي)
    if regenerated_bids > (taker_sell_vol * 0.6) and regenerated_bids > 15000:
        score_boost += 6.0
        phantom_tags.append("Iceberg_Bid_Absorption")

    # 2. حساب إيقاع التنفيذ الزمني (Time-CV) لكشف خوارزميات TWAP
    if len(buy_times) > 8:
        # حساب المسافة الزمنية بين كل صفقة شراء والتي تليها
        buy_intervals = np.diff(buy_times)
        buy_time_cv = np.std(buy_intervals) / (np.mean(buy_intervals) + 1e-8)
        
        # إذا كان الانحراف المعياري للزمن شبه معدوم، فهذا روبوت مؤسساتي يشتري بإيقاع ثابت
        if buy_time_cv < 0.4:
            score_boost += 6.0
            phantom_tags.append("TWAP_Algo_Accumulation")

    # تحجيم السكور ليتوافق مع نظامك (-12 إلى +12)
    final_whale_score = round(max(-12.0, min(12.0, score_boost)), 2)
    
    # إذا لم نجد بصمة شبحية، ندمج مع دالتك القديمة لتعزيز الدقة
    if final_whale_score == 0:
        rest_score = await detect_real_whale_trades(symbol, client, volume_24h)
        return rest_score, []
        
    return final_whale_score, phantom_tags
def get_dynamic_window(df, base_window=20, min_window=5, max_window=100):
    """
    محرك النوافذ الديناميكية (Volatility-Adjusted Lookback):
    يستخدم نسبة التذبذب الحالي مقارنة بالتذبذب التاريخي لضبط حجم النافذة.
    """
    if len(df) < max_window:
        return base_window

    # حساب التذبذب التاريخي (طويل الأمد) والتذبذب اللحظي
    returns = df['close'].pct_change().dropna()
    hist_vol = returns.tail(max_window).std()
    current_vol = returns.tail(min_window * 2).std()
    
    if hist_vol == 0 or pd.isna(hist_vol) or pd.isna(current_vol):
        return base_window

    # معامل كفاءة السوق: كلما زاد التذبذب اللحظي، صغرت النافذة
    volatility_ratio = hist_vol / current_vol
    
    # حساب النافذة الديناميكية مع حماية الحدود
    dynamic_window = int(base_window * volatility_ratio)
    return max(min_window, min(dynamic_window, max_window))
def detect_dark_pool_vca(df, current_cvd_usd, oi_change_pct):
    """
    [THE APEX ALPHA] Dark Pool Vacuum Coil Algorithm (DP-VCA)
    خوارزمية حصرية لاصطياد "كثافة الامتصاص" قبل الانفجارات السعرية (God Candles).
    تدمج بين خنق التذبذب، كثافة الفوليوم للنقطة الواحدة، والتهرب من سوق المشتقات.
    """
    if len(df) < 336: # نحتاج أسبوعين من البيانات على الأقل للمقارنة الإحصائية (فريم 1H)
        return 0.0, None

    # 1. عدسة الضغط الحالية (آخر 7 أيام) مقابل الخلفية التاريخية (آخر شهر)
    recent_window = df.tail(168)
    historic_window = df.iloc[-672:-168] # الـ 3 أسابيع التي سبقت الأسبوع الأخير
    
    avg_price = recent_window['close'].mean()
    
    # 2. حساب "خنق التذبذب" (Volatility Chokehold)
    # نحسب النطاق السعري الحقيقي كنسبة مئوية
    recent_range_pct = (recent_window['high'].max() - recent_window['low'].min()) / avg_price
    historic_range_pct = (historic_window['high'].max() - historic_window['low'].min()) / historic_window['close'].mean()
    
    # حماية رياضية من القسمة على صفر أو النطاقات الميتة تماماً
    recent_range_pct = max(recent_range_pct, 0.005) 
    historic_range_pct = max(historic_range_pct, 0.01)

    # 3. محرك "كثافة الامتصاص" (Absorption Density Engine) - [السر الحقيقي]
    # كم دولاراً تم تداوله لكل 1% من حركة السعر؟
    recent_vol = recent_window['volume'].sum()
    historic_vol_avg = historic_window['volume'].sum() / 3.0 # متوسط الفوليوم الأسبوعي
    
    recent_density = recent_vol / recent_range_pct
    historic_density = historic_vol_avg / historic_range_pct
    
    # الشروط المعقدة لانفجار الزنبرك:
    # أ. التذبذب تم خنقه (النطاق السعري الحالي أقل من نصف النطاق التاريخي)
    is_volatility_choked = recent_range_pct < (historic_range_pct * 0.5)
    
    # ب. كثافة الفوليوم تضاعفت بـ 3 مرات على الأقل (السعر محبوس، لكن الأموال تتدفق بعنف)
    is_heavy_absorption = recent_density > (historic_density * 3.0)
    
    if not (is_volatility_choked and is_heavy_absorption):
        return 0.0, None

    # 4. شرارة التخفي المؤسساتي (Stealth Ignition & Synthetic Delta)
    # صانع السوق المحترف يجمع في السبوت (Spot) ولا يرفع العقود المفتوحة (OI) لكي لا يكشف نفسه
    is_spot_driven = current_cvd_usd > (recent_window['volume'].mean() * avg_price * 0.3)
    is_stealth_derivatives = oi_change_pct < 0.015 # العقود المفتوحة هادئة وميتة
    
    # ==========================================
    # 🧠 التقييم السري (The Apex Score)
    # ==========================================
    vca_score = 0.0
    signal_tag = None

    if is_spot_driven and is_stealth_derivatives:
        # هذه بصمة حتمية لانفجار قادم (God Candle Prep)
        vca_score = 55.0
        signal_tag = "Dark_Pool_God_Candle_Prep"
        
    elif is_heavy_absorption:
        # السعر لا يزال في مرحلة الامتصاص العميق (مرحلة التجميع)
        vca_score = 30.0
        signal_tag = "MM_Deep_Absorption_Phase"

    return round(vca_score, 1), signal_tag


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
            
            # تعديل عتبة Z-Score ديناميكياً بناءً على تقلبات الماكرو
            current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"
            volatility_state = market_regime['volatility'] if isinstance(market_regime, dict) else "Normal"

            # عتبة مرنة (Dynamic Threshold)
            z_threshold = 2.0 if volatility_state == "Low_Vol" else (3.0 if volatility_state == "High_Vol" else 2.5)
            lar_threshold = 0.6 if current_regime_trend == "Trending_Bull" else 0.8

            # أ. الهروب من الفومو (Late FOMO Veto):
            if current_z > z_threshold and candle_spread_pct > 4.0:
                tags.append("Late_FOMO_Pump")
                return None 

            # ب. فلتر العملات الميتة (Dead Asset Veto):
            if lar_score < lar_threshold and current_z < (z_threshold - 1.0):
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
            # أ. تقييم الفوليوم (Fat-Tailed Institutional Mapping)
            # ----------------------------------------------------------------
            recent_pump = (df["close"].iloc[-1] - df["close"].iloc[-10]) / df["close"].iloc[-10]
            
            # 1. التقييم المؤسساتي: استيعاب سيولة الحيتان المتطرفة باستخدام توزيع كوشي
            vol_raw = quant_fat_tail_score(current_z, tail_weight=1.5, limit=100.0) 
            
            # 2. الخصم المستمر (Continuous Penalty) للفومو: خنق رياضي أسي وليس طرحاً عادياً
 
            
            # 2. الخصم المستمر (Continuous Penalty) للفومو: خنق رياضي أسي وليس طرحاً عادياً
            if current_z >= 2.5 and recent_pump > 0.02:
                penalty_factor = math.exp(-15.0 * recent_pump) # كلما زاد البمب، انهار السكور بقسوة
                vol_raw *= penalty_factor
                if vol_raw < 30: tags.append("Late_FOMO")
            elif 1.0 <= current_z <= 3.5 and recent_pump <= 0.02:
                tags.append("Smart_Accumulation")
            elif current_z > 3.5 and recent_pump <= 0.02:
                tags.append("Z_Anom_Silent")
                
            global_alt_volume = await verify_global_liquidity(symbol, client)
            if global_alt_volume > 100000: 
                # تعزيز لوغاريتمي سلس للسيولة العالمية
                vol_raw += math.log10(global_alt_volume / 10000.0) * 5.0

            scores["vol"] = min(vol_raw, 100.0)

            # ----------------------------------------------------------------
            # ب. تقييم السيولة اللحظية (Sigmoid CVD Mapping)
            # ----------------------------------------------------------------
            avg_vol_20 = df["volume"].tail(20).mean()
            micro_cvd_boost, micro_cvd_signal, micro_cvd_trend = await get_micro_cvd_absorption(f"{symbol}USDT", client, "1h")
            
            if avg_vol_20 > 0:
                cvd_ratio = micro_cvd_trend / avg_vol_20
                # تحويل النسبة إلى سكور سلس بحساسية 5.0 (0 يعطي 50 نقطة، 0.3 يعطي ~81 نقطة)
                cvd_raw = quant_sigmoid_score(cvd_ratio, sensitivity=5.0, limit=100.0)
                
                if cvd_ratio > 0.15 and abs(recent_pump) <= 0.015:
                    tags.append("Micro_Silent_Accumulation")
                elif cvd_ratio < -0.15 and recent_pump > 0.02:
                    tags.append("Hidden_Distribution")
                    
                scores["cvd"] = cvd_raw
            else:
                scores["cvd"] = 50.0 # حيادي تماماً

            # ----------------------------------------------------------------
            # ج. تقييم الأوردر بوك (Continuous Imbalance & Pressure)
            # ----------------------------------------------------------------
            global_ob_pressure = await get_aggregated_orderbook(client, symbol)
            depth_data = await analyze_orderbook_spoofing_instant(symbol, client, price)

            imbalance = depth_data.get('imbalance', 0.0)
            # تحويل الخلل (من -1 إلى 1) إلى سكور (0 إلى 100) بسلاسة
            ob_raw = quant_sigmoid_score(imbalance, sensitivity=3.0, limit=100.0)

            if depth_data.get('is_hollow', False):
                ob_raw *= 0.4 
                tags.append("Liquidity_Void_Trap")

            if depth_data.get('is_spoofed', False):
                ob_raw *= 0.2
                tags.append("Spoofing_Distribution_Trap")
            elif depth_data.get('bid_pressure_ratio', 0.0) > 2.0:
                tags.append("OB_Buy")
                
            # تعزيز سلس للضغط العالمي
            if global_ob_pressure > 1.0:
                ob_raw += math.log(global_ob_pressure) * 10.0
                
            scores["ob"] = max(0.0, min(ob_raw, 100.0))

            # ----------------------------------------------------------------
            # د. تقييم المشتقات (Derivatives Squeeze Mechanics)
            # ----------------------------------------------------------------
            deriv_raw = 50.0
            old_price_val = df["close"].iloc[-3] if len(df) > 3 else df["open"].iloc[0]
            
            tick_delta, tick_buy, tick_sell, limit_abs_signal = await get_institutional_orderflow(f"{symbol}USDT", client)
            _, futures_signal, funding_val, oi_change_pct = await get_futures_liquidity(symbol, client, price, old_price_val)

            approx_24h_vol_usd = df["volume"].tail(24).sum() * price 
            
            # 🟢 [التحديث المؤسساتي]: استدعاء محرك السيولة الشبحية بدلاً من الطريقة القديمة
            whale_score, phantom_tags = await detect_phantom_liquidity_ws(symbol, client, price, approx_24h_vol_usd)
            tags.extend(phantom_tags) # إضافة الإشارات الشبحية لقائمة التقييم

            # =========================================================
            # 🕸️ كشف تحوط الـ OTC (Synthetic Delta Trap)
            # =========================================================
            # إذا كان هناك شراء صامت في السبوت يقابله ارتفاع هائل في العقود 
            # مع تمويل سالب، والسعر شبه ثابت = صانع سوق يتحوط لصفقة OTC ضخمة!
            current_cvd_check = float(locals().get('micro_cvd_trend', 0.0))
            is_otc_hedging = (
                current_cvd_check > (approx_24h_vol_usd * 0.005) and # تجميع سبوت
                oi_change_pct > 0.03 and                             # قفزة جنونية في العقود
                funding_val < -0.0003 and                            # شورت للتحوط
                abs(recent_pump) <= 0.01                             # كتم السعر
            )
            
            if is_otc_hedging:
                tags.append("OTC_Hedging_Trap")
                deriv_raw = 100.0 # علامة كاملة لسكور المشتقات لأن الانفجار حتمي
                scores["cvd"] = min(scores["cvd"] + 30.0, 100.0)

            # 1. تقييم الفاندنج بسلاسة (السلبية العالية ترفع السكور للتصفية)


            # 1. تقييم الفاندنج بسلاسة (السلبية العالية ترفع السكور للتصفية)
            # حساسية -3000 تكبر الأرقام العشرية. (فاندنج -0.001 يعطي سكور قوي جداً)
            funding_score = quant_sigmoid_score(funding_val, sensitivity=-3000.0, limit=40.0) - 20.0
            deriv_raw += funding_score

            is_spot_premium = spot_lead_score > 3.0
            is_nuclear_squeeze = (futures_signal == "OI_Rising" and funding_val <= -0.0006 and float(locals().get('micro_cvd_trend', 0.0)) > 0)
            
            if limit_abs_signal == "Limit_Absorption":
                tags.append("Limit_Absorption")
                scores["cvd"] = min(scores["cvd"] + 30.0, 100.0)
                
            if is_nuclear_squeeze:
                tags.append("Nuclear_Short_Squeeze")
                deriv_raw = 100.0 
                scores["cvd"] = min(scores["cvd"] + 25.0, 100.0)
            elif is_spot_premium and futures_signal == "OI_Rising" and funding_val < -0.0003:
                tags.append("Short_Squeeze_Imminent")
                deriv_raw += 30.0 
            elif futures_signal == "OI_Rising": 
                deriv_raw += 15.0
                tags.append("OI_Rising")
                
            # 2. إضافة بصمة الحيتان (خطي ناعم)
            deriv_raw += quant_sigmoid_score(whale_score, sensitivity=0.5, limit=30.0) - 15.0
            deriv_raw += (spot_lead_score * 2.5) 

            scores["deriv"] = max(0.0, min(deriv_raw, 100.0))
            # ----------------------------------------------------------------
            # هـ. تقييم الهيكلة الفنية (Statistically Derived Scoring)
            # ----------------------------------------------------------------
            tech_raw = 50.0
            
            # 1. ديناميكية النوافذ (Dynamic Windows)
            dyn_window = get_dynamic_window(df, base_window=20)
            
            # 2. انضغاط البولينجر المطور (Statistically Scaled Squeeze)
            sma = df["close"].rolling(dyn_window).mean()
            std = df["close"].rolling(dyn_window).std(ddof=0)
            bb_width = (4 * std) / sma
            
            avg_bb_width = bb_width.rolling(dyn_window * 5).mean().iloc[-1]
            current_bb_width = bb_width.iloc[-1]

            if not pd.isna(current_bb_width) and not pd.isna(avg_bb_width) and avg_bb_width > 0:
                squeeze_ratio = current_bb_width / avg_bb_width
                # تحويل هندسي: كلما قل الـ ratio اقترب السكور من 30 نقطة إضافية بنعومة
                if squeeze_ratio < 1.0:
                    tech_boost = quant_sigmoid_score(1.0 - squeeze_ratio, sensitivity=5.0, limit=30.0)
                    tech_raw += tech_boost
                    if squeeze_ratio < 0.5: tags.append("Squeeze")

            # 3. دايفرجنس الـ RSI الموزون إحصائياً (Volatility Adjusted Divergence)
            ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            
            if price < ema200_val and last_rsi > 30:
                # قياس عمق القاع السابق في الـ RSI لتحديد قوة الارتداد
                min_rsi_recent = df["rsi"].iloc[-15:-1].min()
                if min_rsi_recent < 30:
                    # معادلة: كلما كان القاع أعمق والارتداد أقوى، زادت النقاط (بحد أقصى 20)
                    divergence_strength = (last_rsi - min_rsi_recent) / 10.0
                    tech_raw += quant_sigmoid_score(divergence_strength, sensitivity=2.0, limit=20.0)
                    tags.append("RSI_Div")

                
            recent_low_20 = df["low"].iloc[-21:-1].min()
            current_low = df["low"].iloc[-1]
            current_close = df["close"].iloc[-1]
            
            is_liquidity_sweep = (current_low < recent_low_20) and (current_close > recent_low_20)
            current_cvd_check = float(locals().get('micro_cvd_trend', 0.0))
            
            if is_liquidity_sweep and current_cvd_check > 0 and limit_abs_signal == "Limit_Absorption":
                tech_raw += 35.0 
                tags.append("Liquidity_Sweep_Absorption") 

            candle_score = detect_candle_strength(df)
            fake_out_penalty = detect_fake_breakout(df)
            
            if candle_score > 0: tech_raw += 10.0; tags.append("Bullish_Hammer_Absorption")
            if fake_out_penalty < 0: 
                tech_raw *= 0.3 
                tags.append("Fake_Breakout_Trap")

            rs_score = await detect_btc_relative_strength(symbol, client)
            # تمرير القوة النسبية بدالة سيجمويد
            tech_raw += quant_sigmoid_score(rs_score, sensitivity=0.5, limit=20.0) - 10.0
                
            scores["tech"] = max(0.0, min(tech_raw, 100.0))
                        # 🚀 استدعاء خوارزمية "الزنبرك المفرغ" (The Proprietary Alpha)
                        # ====================================================================
            # 🚀 استدعاء خوارزمية الثقب الأسود المؤسساتية (The Dark Pool VCA)
            # ====================================================================
            # تمرير البيانات الحالية المطلوبة (df, CVD بالدولار، والتغير في العقود)
            vca_bonus_score, vca_tag = detect_dark_pool_vca(
                df, 
                float(locals().get('current_cvd', 0.0)), 
                float(locals().get('oi_change_pct', 0.0))
            )
            
            if vca_tag:
                tags.append(vca_tag)
                print(f"☢️ [ALERT] {symbol} أظهر بصمة {vca_tag}! يتم تفعيل تجاوز السكور المتقدم.")
                
                # إعطاء أولوية قصوى واختراق التقييم الكلاسيكي
                scores["tech"] = 100.0 # التحليل الفني مخترق من قبل صانع السوق
                scores["vol"] = min(100.0, scores["vol"] + vca_bonus_score)
                scores["cvd"] = min(100.0, scores["cvd"] + (vca_bonus_score * 0.5))
                
                # حماية السكور لضمان ظهوره في أعلى القائمة
                weights["tech"] = 0.40 # نعطي الهيكلة الفنية وزن أعلى لأنها التقطت الـ VCA
                weights["vol"] = 0.30
                weights["cvd"] = 0.20
                weights["ob"] = 0.05
                weights["deriv"] = 0.05
            # ====================================================================


               # ====================================================================
            # ⚖️ الدمج النهائي وخصم السيولة (Institutional Haircut & Convexity)
            # ====================================================================
            final_weighted_score = (
                (scores["vol"]   * weights["vol"]) +
                (scores["cvd"]   * weights["cvd"]) +
                (scores["ob"]    * weights["ob"]) +
                (scores["deriv"] * weights["deriv"]) +
                (scores["tech"]  * weights["tech"])
            )

            # 🚀 محرك التحدب وعدم التكافؤ (Convexity Alpha Override)
            # يمنع ظاهرة "غسيل الإشارات". إذا كان هناك شذوذ متطرف في قطاع واحد، يفرض نفسه.
            max_category_score = max(scores["vol"], scores["cvd"], scores["ob"], scores["deriv"])
            
            # العتبة: إذا تجاوز أي مؤشر 88/100 (شذوذ حاد جداً)
            if max_category_score > 88.0:
                # حساب قوة الاختراق للعتبة
                override_power = max_category_score - 88.0 
                
                # المعادلة: إضافة أُسّية ترفع السكور النهائي بقوة تتناسب مع حجم الشذوذ
                # الصيغة: $Score_{new} = Score_{old} + (Override \times 1.5)$
                alpha_boost = override_power * 1.5
                final_weighted_score += alpha_boost
                tags.append("Alpha_Override_Triggered")

            # 🛡️ خنق المخاطرة (Liquidity Penalty Haircut)
            # إذا كان LAR أقل من 1 (سيولة سيئة أو تذبذب مجنون)، يتم قص السكور بنعومة
            if lar_score < 1.0:
                liquidity_penalty = 0.5 + (0.5 * quant_sigmoid_score(lar_score, sensitivity=3.0, limit=1.0))
                final_weighted_score *= liquidity_penalty

            import datetime
            current_hour_utc = datetime.datetime.utcnow().hour
            
            if 12 <= current_hour_utc <= 17:
                session_multiplier = 1.05 
            elif 1 <= current_hour_utc <= 6:
                session_multiplier = 0.90 
            else:
                session_multiplier = 1.0 
                
            final_weighted_score *= session_multiplier

            # السكور النهائي الموثوق
            score = round(max(0.0, min(final_weighted_score, 98.5)), 1)



            # --- جدار الفيتو الإجباري والفلترة (تم نقله هنا ليعمل كحارس أخير) ---
            final_signal = "High Probability Setup 🎯"
            
                        # --- جدار الفيتو الإجباري والفلترة ---
            final_signal = "High Probability Setup 🎯"
            
            if "Fake_Pump" in tags or "Fake_Breakout_Trap" in tags or "Flash_Spoofing_Manipulation" in tags or "Short_Covering" in tags or "Late_FOMO" in tags or "Hidden_Distribution" in tags:
                return None 

            # 👇 إضافة الإشارات المؤسساتية الجديدة للواجهة 👇
                        # 👇 إضافة الإشارات المؤسساتية الجديدة للواجهة 👇
            if "OTC_Hedging_Trap" in tags: final_signal = "OTC HEDGING / SYNTHETIC DELTA 🏦"
            elif "TWAP_Algo_Accumulation" in tags: final_signal = "TWAP ALGO ACCUMULATION 🤖"
            elif "Iceberg_Bid_Absorption" in tags: final_signal = "ICEBERG ABSORPTION (DARK POOL) 🧊"
            elif "Nuclear_Short_Squeeze" in tags: final_signal = "NUCLEAR SHORT SQUEEZE ☢️"
            elif "Liquidity_Sweep_Absorption" in tags: final_signal = "Stop-Loss Hunt / Reversal 🩸"
            elif score >= 95.0: final_signal = "Deep Liquidity Absorption 🏦"
            elif score >= 90.0: final_signal = "Institutional Orderflow 🐋"
            elif "Micro_Silent_Accumulation" in tags and "OB_Buy" in tags: final_signal = "Active Accumulation 🦈"
            elif "Short_Squeeze" in tags: final_signal = "(Short Squeeze) 🔥"
            elif "Z_Anom_Silent" in tags and "OI_Rising" in tags: final_signal = "(Derivatives Pump) 🚀"
            elif "Squeeze" in tags and "OB_Buy" in tags: final_signal = "(Liquidity Breakout) ⚡"
            elif "Micro_Silent_Accumulation" in tags: final_signal = "Silent Accumulation 🧲"
            elif "Smart_Accumulation" in tags: final_signal = "Smart Money Inflow 💸"
            # ==========================================
            # 🧠 محرك العتبات الديناميكية (Dynamic Thresholding Engine)
            # يمنع الـ Over-fitting عبر تكييف الأرقام مع حالة السوق
            # ==========================================
            # 1. استخراج نبض الماكرو الحالي
            volatility_state = market_regime['volatility'] if isinstance(market_regime, dict) else "Normal"
            macro_adx = market_regime['adx'] if isinstance(market_regime, dict) else 20.0
            
            # 2. ديناميكية الفومو (VWAP Z-Score)
            # في التذبذب العالي، السعر يبتعد كثيراً بشكل طبيعي. في الركود، الانحراف البسيط يعتبر خطراً.
            dyn_vwap_z = 2.5
            if volatility_state == "High_Vol": dyn_vwap_z = 3.2 
            elif volatility_state == "Low_Vol": dyn_vwap_z = 2.2 

            # 3. ديناميكية التوسع السعري (Price Expansion)
            # بدلاً من 0.003 ثابتة، نأخذ 35% من متوسط حركة الشموع الـ 14 الأخيرة للعملة نفسها
            recent_candle_spread = (abs(df['high'] - df['low']) / df['low']).tail(14).mean()
            dyn_expansion_threshold = max(0.0015, recent_candle_spread * 0.35) 

            # 4. ديناميكية اختلال الأوردر بوك (Imbalance & Pressure)
            # إذا كان السوق عرضياً (ADX ضعيف)، نطلب سيولة هجومية كاسحة لإقناعنا.
            # إذا كان الترند قوياً (ADX عالي)، أي خلل بسيط لصالح الترند يكفينا.
            dyn_imbalance_req = 0.1
            dyn_ob_req = 1.1
            if macro_adx < 25: 
                dyn_imbalance_req = 0.25 
                dyn_ob_req = 1.25
            elif macro_adx > 40:
                dyn_imbalance_req = 0.05 
                dyn_ob_req = 1.05

            # ==========================================
            # 🛡️ الفيتو الإجباري المتكيف (Adaptive Institutional Veto)
            # ==========================================
            
            # 1. كشف الفومو وجني الأرباح (Late FOMO / Mean Reversion)
            if current_vwap_z > dyn_vwap_z:
                tags.append("Late_FOMO_Pump_VWAP")
                print(f"🗑️ {symbol} - مرفوض: السعر انحرف عن VWAP بـ {current_vwap_z:.2f} (عتبة السوق الحالية {dyn_vwap_z:.2f})")
                return None 
                
            # 2. كشف الفراغ السيولي والهشاشة (Liquidity Void)
            if locals().get('is_orderbook_hollow', False) and current_cvd < 0:
                tags.append("Liquidity_Void_Trap")
                print(f"🗑️ {symbol} - مرفوض: جدران شراء وهمية والعمق الداعم فارغ تماماً!")
                return None 

            # 3. فخ الجدران الوهمية (Spoofing Trap) المتكيف:
            if global_ob_pressure > dyn_ob_req and current_cvd < 0:
                tags.append("Spoofing_Distribution_Trap")
                return None 

            # ==========================================
            avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
            is_strong_cvd = current_cvd > (avg_vol_usd * 0.15)

            if is_strong_cvd:
                # أ. كشف إعادة التوازن (MM Hedging)
                if oi_change_pct <= 0.005:
                    tags.append("Fake_MM_Hedging")
                    print(f"🗑️ {symbol} - مرفوض: CVD شراء ضخم ولكن العقود المفتوحة لم ترتفع.")
                    return None 

                # ب. كشف الإسفنجة (Limit Absorption Traps) المتكيف
                price_expansion = (current_high - current_low) / (current_low + 1e-8)
                if price_expansion < dyn_expansion_threshold: 
                    tags.append("Limit_Absorption_Sell_Trap")
                    print(f"🗑️ {symbol} - مرفوض: إسفنجة (التوسع {price_expansion:.4f} أقل من المطلوب للعملة {dyn_expansion_threshold:.4f})")
                    return None 
                    
            # 2. انعدام الشراء الحقيقي (معايير متكيفة):
            if current_cvd <= 0 and current_imbalance <= dyn_imbalance_req and global_ob_pressure < dyn_ob_req:
                return None 

            # 3. فخ السكاكين الساقطة (Falling Knife Trap):
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
                
                # 🛑 إضافة الحارس الأخير المتقدم للرادار هنا:
                # نقوم بفحص الـ WebSocket اللحظي (لمدة ثانيتين) لهذه العملة القوية فقط للتأكد أنها ليست فخاً
                ws_depth_check = await analyze_orderbook_advanced_manual(symbol, client, price)
                
                if ws_depth_check.get('is_spoofed', False) or ws_depth_check.get('is_hollow', False):
                    print(f"🗑️ {symbol} - تم الإلغاء في اللحظة الأخيرة! الرادار اكتشف جدران وهمية عبر الـ WebSocket.")
                    return None # نلغي الإشارة حتى لو كان سكورها 99
                
                # 1. جلب بيانات السلسلة (On-Chain)
                whale_inflow = await get_whale_inflow_score()
                
                micro_volatility = df['close'].tail(20).pct_change().std() * 100
                
                # 🟢 الإصلاح: حساب انحراف مسار السيولة (CVD Divergence) باستخدام القيمة الحقيقية
                                # حساب انحراف مسار السيولة (CVD Divergence) - هل السعر يصعد بينما CVD يهبط؟
                cvd_divergence = 1.0 if (price > ema200_val and current_cvd < 0) else -1.0 if (price < ema200_val and current_cvd > 0) else 0.0

                # جلب معدل التمويل الحالي (Funding Rate) من مصفوفة الـ Futures التي حسبناها مسبقاً
                # إذا كان التمويل سالباً بقوة، الحيتان تضغط السعر صعوداً لتصفية البائعين

                                # --- تحويل البيانات المطلقة إلى نسب مئوية لتناسب الـ AI ---
                avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
                cvd_ratio_pct = (current_cvd / avg_vol_usd) * 100 if avg_vol_usd > 0 else 0.0
                
                # ترميز حالة السوق للـ AI (0: مجهول، 1: صاعد، 2: هابط، 3: عرضي)
                regime_map = {"Trending_Bull": 1, "Trending_Bear": 2, "Ranging": 3}
                regime_code = regime_map.get(current_regime_trend, 0)

                ml_features = {
                    'market_regime': regime_code,
                    'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)),
                    'sentiment_score': float(MACRO_CACHE.get("sentiment_score", 50.0)),
                    'z_score': float(current_z),
                    'cvd_to_vol_ratio': float(cvd_ratio_pct), # 👈 الأهم: نسبة السيولة لحجم التداول بدلاً من دولار مطلق
                    'ofi_imbalance': float(current_imbalance),
                    'ob_skewness': float(locals().get('depth_data', {}).get('bid_pressure_ratio', 1.0) if locals().get('depth_data') else 1.0),
                    'whale_inflow': float(whale_inflow),
                    'adx': float(current_adx),
                    'rsi': float(last_rsi),
                    'micro_volatility': float(micro_volatility) if not pd.isna(micro_volatility) else 0.0,
                    'cvd_divergence': float(cvd_divergence), 
                    'funding_rate': float(funding_val)
                }
                # 3. استشارة الذكاء الاصطناعي (Quant AI Consultation)# 3. استشارة الذكاء الاصطناعي (Quant AI Consultation)
                ai_confidence = -1.0 # await asyncio.to_thread(predict_signal_sync, ml_features) 
                    
                if ai_confidence != -1.0:
    # 🛡️ الفلتر المؤسساتي: إذا كان الذكاء الاصطناعي يتوقع جودة أقل من 70%، نرفض الإشارة

                    # 🛡️ الفلتر المؤسساتي: إذا كان الذكاء الاصطناعي يتوقع جودة أقل من 70%، نرفض الإشارة
                    if ai_confidence < 70.0: 
                        print(f"🗑️ {symbol} - Rejected by AI (Confidence: {ai_confidence:.1f}%)")
                        return None
                    
                    final_score = (score * 0.4) + (ai_confidence * 0.6) # دمج الخبرة البرمجية مع ذكاء الآلة
                    ai_status = f"Active 🧠 (Conf: {ai_confidence:.1f}%)"
                else:
                    final_score = score 
                    ai_status = "Training & Learning ⏳"

                                # (في نهاية دالة analyze_radar_coin)
                return {
                    "symbol": symbol, "price": price, "score": final_score,
                    "rsi": round(last_rsi, 2), "adx": round(current_adx, 2),
                    "macd": current_z, 
                    "vol_ratio": round(current_vol_ratio, 2),
                    "ob_pressure": round(locals().get('global_ob_pressure', 1.0), 2),
                    "signal_type": final_signal,
                    "confluence": confluence_count,
                    "ml_features": ml_features, 
                    "ai_status": ai_status,
                    "cvd_usd": float(current_cvd) # 👈 أضفنا هذا السطر هنا لتمرير القيمة الدولارية للرسالة
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
        # منع تكرار الإشارة لنفس العملة خلال 24 ساعة لتجنب تضخم البيانات (Overfitting)
        exists = await conn.fetchval("""
            SELECT 1 FROM ml_training_data 
            WHERE symbol = $1 AND signal_time > CURRENT_TIMESTAMP - INTERVAL '5 hours'
        """, symbol)
        if exists: return 

        await conn.execute("""
            INSERT INTO ml_training_data 
            (symbol, entry_price, market_regime, sp500_trend_pct, sentiment_score, 
             vol_z_score, cvd_to_vol_ratio, imbalance_ratio, ob_skewness, whale_dominance_pct,
             adx, rsi, micro_volatility_pct, cvd_divergence, funding_rate)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """, 
        symbol, 
        price, 
        int(features.get('market_regime', 0)),
        float(features.get('sp500_trend', 0.0)), 
        float(features.get('sentiment_score', 50.0)),
        float(features.get('z_score', 0.0)), 
        float(features.get('cvd_to_vol_ratio', 0.0)), # 👈 القيمة النسبية الأهم
        float(features.get('ofi_imbalance', 0.0)), 
        float(features.get('ob_skewness', 1.0)), 
        float(features.get('whale_inflow', 0.0)),
        float(features.get('adx', 0.0)), 
        float(features.get('rsi', 50.0)), 
        float(features.get('micro_volatility', 0.0)),
        float(features.get('cvd_divergence', 0.0)), 
        float(features.get('funding_rate', 0.0)))
        
        print(f"🧠 [ML Logger] Institutional Data captured for {symbol} at ${price}")
import numpy as np

async def ml_inspector_worker(pool):
    """
    [Institutional Grade] Walk-Forward Evaluation Engine.
    يستيقظ لتقييم الصفقات المعلقة عبر قياس MFE/MAE والـ Alpha مقارنة بالبيتكوين.
    """
    await asyncio.sleep(120)
    print("🕵️‍♂️ [Quant Inspector] Institutional Labeling Engine is online...")
    
    while True:
        try:
            async with pool.acquire() as conn:
                # جلب الإشارات التي مر عليها 24 ساعة ولم يتم معالجتها
                pending = await conn.fetch("""
                    SELECT id, symbol, entry_price, EXTRACT(EPOCH FROM signal_time) as sig_ts
                    FROM ml_training_data 
                    WHERE is_processed = 0 AND signal_time <= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                """)
                
            if not pending:
                await asyncio.sleep(600)
                continue
                
            async with httpx.AsyncClient(timeout=15) as client:
                for row in pending:
                    sym = f"{row['symbol']}USDT"
                    entry = float(row['entry_price'])
                    start_time_ms = int(row['sig_ts'] * 1000)
                    
                    base_url = get_random_binance_base()
                    
                    # 1. جلب شموع العملة (15 دقيقة) لـ 24 ساعة (96 شمعة)
                    res_asset = await client.get(
                        f"{base_url}/api/v3/klines",
                        params={"symbol": sym, "interval": "15m", "startTime": start_time_ms, "limit": 96}
                    )
                    
                    # 2. جلب شموع البيتكوين لنفس الفترة لحساب الـ Alpha
                    res_btc = await client.get(
                        f"{base_url}/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "15m", "startTime": start_time_ms, "limit": 96}
                    )
                    
                    if res_asset.status_code == 200 and res_btc.status_code == 200:
                        klines = res_asset.json()
                        btc_klines = res_btc.json()
                        
                        if not klines or len(klines) < 96 or not btc_klines:
                            continue
                            
                        # --- حساب الأهداف الزمنية (Multi-Horizon Returns) ---
                        # الشمعة 4 = بعد ساعة، الشمعة 16 = بعد 4 ساعات، الشمعة 95 = بعد 24 ساعة
                        ret_1h = ((float(klines[3][4]) - entry) / entry) * 100 if len(klines) >= 4 else 0.0
                        ret_4h = ((float(klines[15][4]) - entry) / entry) * 100 if len(klines) >= 16 else 0.0
                        ret_24h = ((float(klines[-1][4]) - entry) / entry) * 100
                        
                        # --- حساب الـ Alpha مقارنة بالبيتكوين ---
                        btc_entry = float(btc_klines[0][1])
                        btc_exit = float(btc_klines[-1][4])
                        btc_return_24h = ((btc_exit - btc_entry) / btc_entry) * 100
                        alpha_24h = ret_24h - btc_return_24h
                        
                        # --- التقييم الزمني لمسار السعر (MFE & MAE) ---
                        mfe = 0.0
                        mae = 0.0
                        is_stopped_out = False
                        
                        # نعتبر أن هناك وقف خسارة وهمي عند -5% لتصحيح التقييم (Risk Penalty)
                        HARD_STOP_LOSS = 5.0 
                        
                        for k in klines:
                            high = float(k[2])
                            low = float(k[3])
                            
                            profit_pct = ((high - entry) / entry) * 100
                            drawdown_pct = ((entry - low) / entry) * 100
                            
                            if profit_pct > mfe: mfe = profit_pct
                            if drawdown_pct > mae: mae = drawdown_pct
                            
                            # إذا ضربت العملة الانعكاس قبل تحقيق ربح جيد، نوقف تحديث الـ MFE
                            if mae >= HARD_STOP_LOSS:
                                is_stopped_out = True
                                break 
                                
                        # --- The Hedge Fund Score (Trade Quality) ---
                        # معادلة تقييم تدمج بين الربح، الخسارة المحتملة، وسرعة الحركة
                        # تتراوح النتيجة بين -1.0 (تدمير للمحفظة) إلى +1.0 (صفقة قناص مثالية)
                        
                        if is_stopped_out and mfe < 3.0:
                            trade_quality = -1.0 # ضربت الوقف فوراً
                        else:
                            # Quality = (MFE - MAE) / (MFE + MAE + 0.1)
                            # الإضافة 0.1 لمنع القسمة على صفر
                            raw_quality = (mfe - mae) / (mfe + mae + 0.1)
                            
                            # نعزز التقييم إذا كانت الألفا إيجابية (العملة أقوى من البيتكوين)
                            alpha_bonus = min(0.2, max(-0.2, alpha_24h / 50.0))
                            trade_quality = max(-1.0, min(1.0, raw_quality + alpha_bonus))

                        # --- حفظ النتائج في قاعدة البيانات ---
                        async with pool.acquire() as conn:
                            await conn.execute("""
                                UPDATE ml_training_data 
                                SET ret_1h = $1, ret_4h = $2, ret_24h = $3,
                                    max_favorable_excursion = $4, max_adverse_excursion = $5,
                                    btc_return_24h = $6, alpha_24h = $7,
                                    trade_quality_score = $8, is_processed = 1
                                WHERE id = $9
                            """, ret_1h, ret_4h, ret_24h, mfe, mae, btc_return_24h, alpha_24h, float(trade_quality), row['id'])
                        
                        print(f"📊 [Quant Labeling] {sym} | MFE: +{mfe:.1f}% | MAE: -{mae:.1f}% | Quality Score: {trade_quality:.2f}")
                        
                    await asyncio.sleep(0.5) # الامتثال لقيود بايننس
                    
        except Exception as e:
            print(f"⚠️ Quant Inspector Error: {e}")
            
        await asyncio.sleep(600) # فحص كل 10 دقائق


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
async def check_btc_gravity_veto(client: httpx.AsyncClient):
    """
    مستشعر الجاذبية اللحظي للبيتكوين (Micro-Veto)
    يفحص آخر 5 دقائق. إذا كان البيتكوين ينزف بقوة، يتم تجميد الشراء.
    """
    try:
        base_url = get_random_binance_base()
        res = await client.get(f"{base_url}/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=2", timeout=3.0)
        if res.status_code == 200:
            data = res.json()
            current_close = float(data[-1][4])
            current_open = float(data[-1][1])
            prev_close = float(data[-2][4])
            
            # حساب نسبة الهبوط اللحظية
            drop_pct = (current_close - prev_close) / prev_close
            candle_drop = (current_close - current_open) / current_open
            
            # إذا هبط البيتكوين أكثر من 0.4% في 5 دقائق، هذا نزيف حاد (Flash Drop)
            if drop_pct < -0.004 or candle_drop < -0.004:
                return True # تفعيل الفيتو (خطر)
    except Exception as e:
        pass
    return False # الوضع آمن

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
                    if vol_usd >= 400_000 and -10.0 <= price_change <= 5.0:
                        coins.append({
                            "symbol": clean_sym,
                            "quote": {"USD": {"price": float(t["lastPrice"])}},
                            "volume": vol_usd,
                            "priceChangePercent": price_change # 👈 أضفنا هذا السطر لكي تتعرف عليه دالة الترتيب
                        })
                
                # الفرز بناءً على أضيق نسبة تغير في السعر (من الأقرب للصفر) لاصطياد العملات المضغوطة                # --- الكود القديم لديك ---
                coins = sorted(coins, key=lambda x: float(x.get("volume", 0)), reverse=True)[:350]

                # 👇👇 التعديل الجديد: تفعيل الفيتو اللحظي للبيتكوين 👇👇
                is_btc_dumping = await check_btc_gravity_veto(client)
                if is_btc_dumping:
                    print("🛑 [BTC Gravity Veto] البيتكوين ينزف لحظياً! تم تجميد الرادار لحماية المحفظة.")
                    await asyncio.sleep(120) # تجميد دقيقتين حتى يهدأ السوق
                    continue
                # 👆👆 نهاية التعديل 👆👆
                
                # --- الكود القديم لديك ---
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

                                # تنسيق الأرقام لضمان عدم ظهور أرقام طويلة جداً                # ====================================================================
                # ⚙️ محرك التحليل الكمي المباشر (Quant Notes)
                # ====================================================================
                ml = best_meta.get('ml_features', {})
                z_val = float(ml.get('z_score', best_meta.get('macd', 0.0)))
                
                # 👇 عدل السطرين التاليين 👇
                vol_ratio = float(best_meta.get('vol_ratio', 1.0)) 
                cvd_val = float(best_meta.get('cvd_usd', 0.0)) # 👈 التعديل هنا: يقرأ من best_meta وليس من ml
                
                ob_val = float(best_meta.get('ob_pressure', 1.0))
                funding = float(ml.get('funding_rate', 0.0))

                
                confluence = int(best_meta.get('confluence', 0))
                adx = float(best_meta.get('adx', 0.0))
                rsi = float(best_meta.get('rsi', 0.0))

                # ==========================================
                # 🇸🇦 بناء التحليل الكمي باللغة العربية
                # ==========================================
                vol_ar = f"شذوذ فوليوم مؤسساتي (Z-Score: {z_val:.2f}) مع ضخ سيولة حاد ({vol_ratio:.2f}x)." if z_val > 2 else f"انضغاط سيولة صامت (Z-Score: {z_val:.2f})."
                
                cvd_ar = f"امتصاص شرائي خفي (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"ضغط بيعي وتصريف (CVD: ${cvd_val:,.0f})"
                ob_ar = f"مع تكدس طلبات هجومي (OB: {ob_val:.2f}x)." if ob_val > 1 else f"مع سيطرة وتكدس لعروض البيع (OB: {ob_val:.2f}x)."
                
                if funding < -0.0005:
                    fund_ar = "تمركز بيعي قوي مع احتمالية لتصفية البائعين (Short Squeeze)."
                elif funding > 0.0005:
                    fund_ar = "طمع شرائي ومعدل تمويل إيجابي ينذر بخطر تصفية المشترين (Long Squeeze)."
                else:
                    fund_ar = "استقرار وتوازن في معدلات تمويل عقود المشتقات."
                
                tech_ar = f"إجماع فني ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"

                insight_ar = (
                    f"• <b>السيولة:</b> {vol_ar}\n"
                    f"• <b>التدفق:</b> {cvd_ar} {ob_ar}\n"
                    f"• <b>المشتقات:</b> {fund_ar}\n"
                    f"• <b>الهيكلة:</b> {tech_ar}"
                )

                # ==========================================
                # 🇺🇸 بناء التحليل الكمي باللغة الإنجليزية
                # ==========================================
                vol_en = f"Institutional volume anomaly (Z-Score: {z_val:.2f}) with aggressive inflow ({vol_ratio:.2f}x)." if z_val > 2 else f"Silent liquidity compression (Z-Score: {z_val:.2f})."
                
                cvd_en = f"Hidden buy absorption (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"Selling pressure & distribution (CVD: ${cvd_val:,.0f})"
                ob_en = f"with aggressive bid stacking (OB: {ob_val:.2f}x)." if ob_val > 1 else f"with heavy ask supply dominance (OB: {ob_val:.2f}x)."
                
                if funding < -0.0005:
                    fund_en = "Heavy short positioning with high (Short Squeeze) probability."
                elif funding > 0.0005:
                    fund_en = "Overleveraged longs with high (Long Squeeze/Correction) risk."
                else:
                    fund_en = "Stable futures open interest and neutral funding rates."
                
                tech_en = f"Technical confluence ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"

                insight_en = (
                    f"• <b>Liquidity:</b> {vol_en}\n"
                    f"• <b>Orderflow:</b> {cvd_en} {ob_en}\n"
                    f"• <b>Derivatives:</b> {fund_en}\n"
                    f"• <b>Structure:</b> {tech_en}"
                )

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
        # ---------- FREE ----------
        else:
            if lang == "ar":
                text = (
                    f"📡 <b>رادار الإنفجارات السعرية</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 العملة: •••• 🔒\n"
                    f"⚡ الإشارة: {data['signal']}\n"
                    f"📊 السكور: {data['score']}/100\n\n"
                    f"📈 <b>التحليل:</b>\n{data['insight_ar']}\n\n"
                    f"🔒 اشترك VIP لكشف اسم العملة والأهداف\n"
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
                    f"📈 <b>Insight:</b>\n{data['insight_en']}\n\n"
                    f"🔒 Subscribe VIP to unlock the coin and exact targets.\n"
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
    """تبحث عن العملة وتجلب السيولة وعنوان العقد الحقيقي لفحص الأمان"""
    url = f"https://api.dexscreener.com/latest/dex/search?q={symbol}"
    
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(retries):
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("pairs") and len(data["pairs"]) > 0:
                        # فلترة لجلب أفضل مجمع سيولة
                        pairs = sorted(data["pairs"], key=lambda x: float(x.get("liquidity", {}).get("usd", 0)), reverse=True)
                        best_pair = pairs[0]
                        return {
                            "network": best_pair["chainId"],
                            "pool_address": best_pair["pairAddress"],
                            "token_address": best_pair.get("baseToken", {}).get("address", ""), # 👈 مهم جداً
                            "price": float(best_pair.get("priceUsd", 0)),
                            "volume_24h": float(best_pair.get("volume", {}).get("h24", 0)),
                            "liquidity_usd": float(best_pair.get("liquidity", {}).get("usd", 0)), # 👈 حجم السيولة
                            "base_symbol": best_pair.get("baseToken", {}).get("symbol", symbol)
                        }
                    return None 
            except Exception: pass
            await asyncio.sleep(1)
    return None


# === كود جديد: ضعه فوق دوال التحليل ===
async def get_dex_klines(client: httpx.AsyncClient, chain_id: str, pair_address: str, tf: str):
    """
    جلب شموع عملات DEX اللامركزية من GeckoTerminal كبديل لشموع بايننس
    """
    try:
        # تحويل الإطار الزمني لمعيار GeckoTerminal
        resolution = "hour"
        aggregate = 1
        if tf in ["1m", "5m", "15m"]:
            resolution = "minute"
            aggregate = int(tf.replace("m", ""))
        elif tf == "1h":
            resolution = "hour"
            aggregate = 1
        elif tf == "4h":
            resolution = "hour"
            aggregate = 4
        elif tf in ["1d", "daily"]:
            resolution = "day"
            aggregate = 1
            
        url = f"https://api.geckoterminal.com/api/v2/networks/{chain_id}/pools/{pair_address}/ohlcv/{resolution}?aggregate={aggregate}&limit=100"
        
        res = await client.get(url, timeout=10.0)
        if res.status_code == 200:
            data = res.json()
            ohlcv_list = data['data']['attributes']['ohlcv_list']
            # GeckoTerminal يُرجع البيانات: [timestamp, open, high, low, close, volume]
            ohlcv_list.reverse() # ترتيب من الأقدم للأحدث ليطابق بايننس
            df = pd.DataFrame(ohlcv_list, columns=["t", "open", "high", "low", "close", "volume"])
            df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric)
            return df
    except Exception as e:
        print(f"⚠️ خطأ في جلب شموع DEX: {e}")
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
                "liquidity_usd": dex_data.get("liquidity_usd", 0.0), # 👈 هذا هو السطر المضاف
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

def calculate_smart_trend_and_targets(df, current_price, current_z, lang="ar", override_trend=None):
    # --- [بداية الدالة كما هي لتجهيز البيانات و ATR] ---
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
    # 🌟 1. توحيد الاتجاه الصارم (Strict Trend Unification)
    if override_trend:
        trend_direction = override_trend
    else:
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        trend_direction = "Bullish" if ema20 > ema50 else "Bearish"

    # 🌟 2. حساب الأهداف الأساسية بالـ VPVR
    sl, tp1, tp2, tp3 = calculate_vpvr_levels(df, current_price, trend_direction)

    # 🧲 3. دمج فجوات السيولة (FVG) كأهداف حتمية ووقف خسارة ذكي
    fvg_target = detect_nearest_fvg(df, current_price, trend_direction)
    
    if fvg_target:
        if trend_direction == "Bullish":
            if fvg_target < current_price:
                # الفجوة بالأسفل: قد ينزل السعر لملئها، لذا نضع الوقف تحتها بأمان
                sl = min(sl, fvg_target * 0.985) 
            else:
                # الفجوة بالأعلى: تصبح هي الهدف الأول المغناطيسي
                tp1 = fvg_target 
        else: # Bearish
            if fvg_target > current_price:
                # الفجوة بالأعلى في ترند هابط: نضع الوقف فوقها
                sl = max(sl, fvg_target * 1.015) 
            else:
                # الفجوة بالأسفل: تصبح هدفاً أول للبيع
                tp1 = fvg_target

    # ترتيب الأهداف منطقياً بعد تعديل الـ FVG لضمان التسلسل الصحيح
    if trend_direction == "Bullish":
        tp1, tp2, tp3 = sorted([tp1, tp2, tp3])
    else:
        tp1, tp2, tp3 = sorted([tp1, tp2, tp3], reverse=True)

    # 🌟 4. هندسة السيولة بناءً على Z-Score
    try:
        real_adx_value = float(ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14, fillna=True).adx().iloc[-1])
    except:
        real_adx_value = 0.0

    trend_strength = "غير محدد"
    market_action = ""

    # لا نحتاج لتعقيد النصوص هنا، لأننا سنبنيها باحترافية في دالة `run_analysis`
    
    # 🌟 5. حساب الدعم والمقاومة الكلاسيكي (كما هو في كودك)
    try:
        support = df['low'].rolling(window=50, min_periods=1).min().iloc[-1]
        if pd.isna(support) or support >= current_price * 0.99:
            support = current_price * 0.95 
    except: support = current_price * 0.95

    try:
        resistance = df['high'].rolling(window=50, min_periods=1).max().iloc[-1]
        if pd.isna(resistance) or resistance <= current_price * 1.01:
            resistance = current_price * 1.05 
    except: resistance = current_price * 1.05

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
        # ==========================================
        # 🛡️ التعديل المؤسساتي: عزل دورة السوق الحقيقية (Market Cycle Slicing)
        # ==========================================
        recent_df = df.tail(300).reset_index(drop=True) # نأخذ آخر 300 شمعة كحد أقصى للبحث
        
        # تحديد موقع القمة والقاع الرئيسي في هذه الفترة
        max_idx = recent_df['high'].idxmax()
        min_idx = recent_df['low'].idxmin()

        # دورة السوق الحالية تبدأ من أحدث نقطة تطرف (أيهما أقرب للوقت الحالي)
        cycle_start_idx = max(max_idx, min_idx)
        
        # قص البيانات لأخذ الشموع من نقطة التطرف وحتى اللحظة الحالية فقط
        cycle_df = recent_df.iloc[cycle_start_idx:].copy()

        # حماية رياضية: إذا كانت نقطة التطرف قريبة جداً (حدثت في آخر 20 شمعة)،
        # فهذا يعني أن الدورة لم تتشكل بعد، فنأخذ الـ 100 شمعة الأخيرة كبديل آمن.
        if len(cycle_df) < 20:
            cycle_df = recent_df.tail(100).copy()

        # الآن نستخدم الدورة المعزولة (cycle_df) لحساب مناطق الارتداد الحقيقية
        min_price = cycle_df['low'].min()
        max_price = cycle_df['high'].max()
        price_bins = np.linspace(min_price, max_price, num_bins)
        
        cycle_df['typical_price'] = (cycle_df['high'] + cycle_df['low'] + cycle_df['close']) / 3
        cycle_df['bin_index'] = np.digitize(cycle_df['typical_price'], price_bins) - 1
        vol_profile = cycle_df.groupby('bin_index')['volume'].sum()

        profile = []
        for idx, vol in vol_profile.items():
            if 0 <= idx < len(price_bins):
                profile.append({'price': price_bins[idx], 'volume': vol})

        profile_df = pd.DataFrame(profile)
        if profile_df.empty:
            raise ValueError("Empty Profile")

        above_price = profile_df[profile_df['price'] > current_price]
        below_price = profile_df[profile_df['price'] < current_price]
        # ... (باقي الكود أسفل هذا السطر يبقى كما هو تماماً) ...


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
async def analyze_orderbook_advanced_manual(symbol: str, client: httpx.AsyncClient, current_price: float):
    """
    [Institutional Upgrade] True Order Flow Imbalance (OFI) & Flash Spoofing Detection
    يستخدم WebSocket لالتقاط 20 إطاراً في ثانيتين (100ms interval) لكشف الخوارزميات، 
    مع وجود Fallback لـ REST API في حال فشل الاتصال لضمان استقرار البوت.
    """
    clean_symbol = symbol.replace("USDT", "").lower() + "usdt"
    ws_url = f"wss://stream.binance.com:9443/ws/{clean_symbol}@depth10@100ms"

    frames = []
    try:
        # فتح اتصال سريع لالتقاط التلاعب اللحظي (Microseconds manipulation)
        async with websockets.connect(ws_url, ping_interval=None, close_timeout=1) as ws:
            start_time = time.time()
            while time.time() - start_time < 2.0: # ثانيتين تكفي لاستخراج الـ OFI
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    data = json.loads(msg)
                    if data.get('bids') and data.get('asks'):
                        frames.append(data)
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        # Fallback أمني: إذا فشل الـ WS (بسبب ليمت أو جدار حماية)، نعود للنسخة الأصلية
        return await analyze_orderbook_spoofing_instant(symbol, client, current_price)

    # إذا لم نجمع بيانات كافية، نلجأ للطريقة الآمنة
    if len(frames) < 5:
        return await analyze_orderbook_spoofing_instant(symbol, client, current_price)

    # --- 🧠 Advanced OFI Engine ---
    ofi_scores = []
    bid_vols = []
    ask_vols = []

    for i in range(1, len(frames)):
        prev_bids = {float(p): float(v) for p, v in frames[i-1].get('bids', [])}
        curr_bids = {float(p): float(v) for p, v in frames[i].get('bids', [])}
        prev_asks = {float(p): float(v) for p, v in frames[i-1].get('asks', [])}
        curr_asks = {float(p): float(v) for p, v in frames[i].get('asks', [])}

        # Delta Calculation: من يضخ سيولة ومن يسحبها؟
        bid_vol_change = sum(curr_bids.values()) - sum(prev_bids.values())
        ask_vol_change = sum(curr_asks.values()) - sum(prev_asks.values())

        ofi_scores.append(bid_vol_change - ask_vol_change)
        bid_vols.append(sum(curr_bids.values()))
        ask_vols.append(sum(curr_asks.values()))

    # معامل الاختلاف (Coefficient of Variation) لكشف الـ Flash Spoofing
    bid_cv = np.std(bid_vols) / (np.mean(bid_vols) + 1e-8)
    ask_cv = np.std(ask_vols) / (np.mean(ask_vols) + 1e-8)

    # شروط التلاعب المؤسساتي (الجدران تظهر وتختفي بتذبذب عالي)
    is_spoofed = (bid_cv > 0.6 or ask_cv > 0.6)
    
    # الفراغ السيولي (Hollowness): العمق الحقيقي هش جداً
    is_hollow = (np.mean(bid_vols) * current_price) < 15000 
    
    avg_ofi = np.mean(ofi_scores)
    total_vol_mean = np.mean(bid_vols) + np.mean(ask_vols) + 1e-8
    
    # تطبيع الخلل ليكون قيمة بين -1 و 1
    imbalance = avg_ofi / total_vol_mean

    return {
        "is_hollow": bool(is_hollow),
        "imbalance": round(float(max(-1.0, min(1.0, imbalance))), 2),
        "is_spoofed": bool(is_spoofed),
        "bid_pressure_ratio": float(np.mean(bid_vols) / (np.mean(ask_vols) + 1e-8))
    }

# --- دالة التحليل المعدلة ---
async def evaluate_dex_risk(liquidity_usd: float, vol_24h: float):
    """محرك تقييم مخاطر السيولة في الـ DEX"""
    risk_warnings_ar = []
    risk_warnings_en = []
    risk_score = 0
    
    # 1. فحص فقر السيولة (Liquidity Void)
    if liquidity_usd < 50000:
        risk_warnings_ar.append("🚨 خطر عالي: سيولة المجمع (LP) أقل من 50 ألف دولار! (سهلة التلاعب/السحب).")
        risk_warnings_en.append("🚨 HIGH RISK: Liquidity Pool < $50k! (Rug-pull/Manipulation risk).")
        risk_score -= 5
    elif liquidity_usd < 200000:
        risk_warnings_ar.append("⚠️ تنبيه: سيولة المجمع ضعيفة، توقع انزلاق سعري (Slippage) عالي.")
        risk_warnings_en.append("⚠️ WARNING: Low Liquidity, expect high slippage.")
        risk_score -= 2
        
    # 2. فحص نسبة الفوليوم للسيولة (Volume/Liquidity Ratio)
    # إذا كان الفوليوم اليومي أعلى من السيولة بـ 10 أضعاف، هذا تدوير وهمي (Wash Trading)
    if liquidity_usd > 0 and (vol_24h / liquidity_usd) > 10:
         risk_warnings_ar.append("🤖 تحذير: الفوليوم أعلى من السيولة بشكل غير منطقي (احتمال Wash Trading).")
         risk_warnings_en.append("🤖 WARNING: Abnormal Vol/Liq ratio (Possible Wash Trading).")
         risk_score -= 3

    return risk_warnings_ar, risk_warnings_en, risk_score

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
    
    # --- التعديل الجديد: جلب السيولة وفحص الأمان للـ DEX ---
    is_dex = data.get('is_dex', False)
    dex_liquidity = data.get('liquidity_usd', 0.0) 
    dex_vol = data.get('volume_24h', 0.0)
    
    dex_warnings_ar, dex_warnings_en = [], []
    
    if is_dex:
        # تقييم المخاطر أولاً
        dex_warnings_ar, dex_warnings_en, dex_risk = await evaluate_dex_risk(dex_liquidity, dex_vol)
        
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
    
    # 🟢 الحل الجذري لمنع التعليق: التمييز بين شكل شموع بايننس وشموع الـ DEX
    if len(df.columns) >= 7:
        # مسار بايننس (يوجد 7 أعمدة فأكثر)
        df = df.iloc[:, :7]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open", "taker_buy_vol"]
    else:
        # مسار الـ DEX (الشموع تأتي بـ 6 أعمدة فقط)
        df = df.iloc[:, :6]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
        # إضافة عمود وهمي بأصفار للـ DEX حتى لا تنهار الحسابات السفلية التي تطلبه
        df["taker_buy_vol"] = 0.0

    for col in ["close", "high", "low", "open", "volume", "taker_buy_vol"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')

        
    db_vol_float = 0.0
    try:
        avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
        avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
        if avg_vol_20 > 0:
            db_vol_float = ((avg_vol_5 / avg_vol_20) - 1) * 100 
    except: pass

    # 🌟 خريطة التوافق الزمني المؤسساتية (Timeframe Alignment Map)    # 1. تحديث خريطة التوافق الزمني لإضافة مفتاح (use_ob)
    tf_settings = {
        "4h": {"cvd_tf": "15m", "oi_period": "4h", "macro_flow": False, "use_ob": True},
        "daily": {"cvd_tf": "1h", "oi_period": "1d", "macro_flow": True, "use_ob": False}, # 👈 تعطيل الأوردر بوك
        "weekly": {"cvd_tf": "4h", "oi_period": "1d", "macro_flow": True, "use_ob": False}  # 👈 تعطيل الأوردر بوك
    }
    current_tf = tf_settings.get(tf, tf_settings["4h"])

    delta_usd, funding_val = 0.0, 0.0
    cvd_sig, fut_sig = None, None
    buy_v, sell_v, z_score = 0, 0, 0
    is_orderbook_hollow = False 
    is_spoofed = False
    
    # 🟢 الحل: نقل حساب Z-Score هنا ليعمل على CEX و DEX معاً (الفوليوم هو سلاحك الوحيد في الديكس)
    z_score, _, _ = calculate_volume_zscore(df, window=720)
    # 2. انزل للأسفل عند قسم (تنفيذ المهام المتزامنة tasks_to_run) وقم بتعديل استدعاء depth_task
    if not is_dex:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                safe_price = float(price)
                safe_old_price = float(df["close"].iloc[-3]) if len(df) > 3 else safe_price

                # 🟢 التعديل الجذري: لا تطلب الأوردر بوك إذا كان الفريم كبير!
                if current_tf["use_ob"]:
                    depth_task = analyze_orderbook_advanced_manual(clean_sym, client, safe_price)
                else:
                    # إرجاع بيانات محايدة للفريمات الكبيرة لمنع تلوث التحليل
                    async def mock_depth():
                        return {"is_hollow": False, "imbalance": 0.0, "is_spoofed": False, "bid_pressure_ratio": 1.0}
                    depth_task = mock_depth()
                
                # ... (باقي الكود cvd_task و flow_task و futures_task يبقى كما هو) ...
                # 2. توافق فريم السيولة الصامتة
                cvd_task = get_micro_cvd_absorption(f"{clean_sym}USDT", client, current_tf["cvd_tf"])
                
                # 3. توجيه ذكي لتدفق الأوامر (Macro vs Micro)
                if current_tf["macro_flow"]:
                    flow_task = None # يتم حسابه محلياً من بيانات الشموع الطويلة
                else:
                    # التدفق اللحظي للفريمات الصغيرة (Limit_Absorption)
                    flow_task = get_institutional_orderflow(f"{clean_sym}USDT", client, minutes=240)
                    
                # 4. توافق الفريم لعقود المشتقات
                # دالة get_futures_liquidity الحالية تقبل period=15m ثابتة، سنرسل الطلب يدوياً هنا للسرعة
                await binance_rate_limit_event.wait()
                futures_task = client.get(
                    f"https://fapi.binance.com/futures/data/openInterestHist?symbol={clean_sym}USDT&period={current_tf['oi_period']}&limit=2", 
                    timeout=3.0
                )

                # تنفيذ المهام المتزامنة
                tasks_to_run = [cvd_task, depth_task, futures_task]
                if flow_task: tasks_to_run.append(flow_task)
                
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                
                # استخراج CVD و Depth
                cvd_data = results[0] if not isinstance(results[0], Exception) else (0.0, None, 0.0)
                cvd_boost, cvd_sig, cvd_trend_val = cvd_data
                
                depth_data = results[1] if not isinstance(results[1], Exception) else {}
                is_orderbook_hollow = depth_data.get('is_hollow', False)
                is_spoofed = depth_data.get('is_spoofed', False)

                # استخراج Futures
                oi_res = results[2]
                if not isinstance(oi_res, Exception) and oi_res.status_code == 200:
                    oi_data = oi_res.json()
                    if len(oi_data) >= 2:
                        old_oi = float(oi_data[0]["sumOpenInterest"])
                        current_oi = float(oi_data[-1]["sumOpenInterest"])
                        oi_change = (current_oi - old_oi) / old_oi
                        price_change = (safe_price - safe_old_price) / safe_old_price
                        if price_change > 0.01 and oi_change > 0.02: fut_sig = "OI_Rising"
                        elif price_change > 0.01 and oi_change < -0.02: fut_sig = "Short_Covering"

                # استخراج Flow
                if current_tf["macro_flow"]:
                    # الحساب الهندسي الدقيق للترندات الكبيرة دون تجاوز حدود الـ API
                    buy_v = df['taker_buy_vol'].tail(30).sum() if tf == "daily" else df['taker_buy_vol'].sum()
                    sell_v = (df['volume'].tail(30).sum() - buy_v) if tf == "daily" else (df['volume'].sum() - buy_v)
                    delta_usd = buy_v - sell_v
                    limit_abs_signal = None
                else:
                    flow_data = results[3] if not isinstance(results[3], Exception) else (0.0, 0.0, 0.0, None)
                    delta_usd, buy_v, sell_v, limit_abs_signal = flow_data

            z_score, _, _ = calculate_volume_zscore(df, window=720)
            
        except Exception as e:
            import traceback
            print(f"⚠️ Data Fetch Error in Manual Analysis: {e}")
            cvd_sig, buy_v, sell_v, fut_sig, z_score = None, 0, 0, None, 0
            delta_usd, funding_val = 0.0, 0.0

    # 2. كشف الفخاخ وتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)    # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)
    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    classic_trend = "Bullish" if ema20 > ema50 else "Bearish"
    
    final_trend_dir = classic_trend
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    avg_vol_20 = df["volume"].tail(20).mean()
    current_vol = df["volume"].iloc[-1]

    # أ. شروط انعكاس الاتجاه من هابط إلى صاعد (اصطياد القاع الآمن)
    if classic_trend == "Bearish":
        vol_surge = current_vol > (avg_vol_20 * 1.5)
        
        # 🛡️ الفلتر المؤسساتي القاتل: هل السيولة حقيقية أم مجرد إغلاق شورت؟
        # fut_sig يأتي من تحليل العقود الآجلة ويخبرنا بانخفاض الـ Open Interest 
        is_short_covering = (fut_sig == "Short_Covering")

        # نسمح بانعكاس الاتجاه فقط إذا لم يكن الهبوط مجرد سكاكين ساقطة يتم تغطيتها
        if not is_short_covering:
            if (cvd_sig == "Micro_Silent_Accumulation" or buy_v > (sell_v * 1.5)) or (last_rsi < 35 and last_macd > 0 and vol_surge):
                final_trend_dir = "Bullish"

    # ب. شروط انعكاس الاتجاه من صاعد إلى هابط (الهروب من القمة المخادعة)
    elif classic_trend == "Bullish":
        vol_surge = current_vol > (avg_vol_20 * 1.5)
        
        # 🛡️ حماية عكسية: هل هو تصريف أم تصفية مراكز شراء مفرطة الرافعة (Long Squeeze)؟
        is_long_squeeze = (fut_sig == "OI_Rising" and delta_usd < 0 and funding_val > 0.001)

        if not is_long_squeeze:
            if (cvd_sig == "Hidden_Distribution" or sell_v > (buy_v * 1.5)) or (last_rsi > 75 and last_macd < 0 and vol_surge):
                final_trend_dir = "Bearish"
 
    # 3. حساب الدعم والمقاومة والأهداف بناءً على الاتجاه "المُوحّد" لمنع التضارب
            # 3. حساب الدعم والمقاومة والأهداف في الخلفية لمنع التضارب والتعليق
    trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = await asyncio.to_thread(
        calculate_smart_trend_and_targets, df, price, db_vol_float, lang, final_trend_dir
    )

    # --- تعريف متغيرات التدفق (Fixing NameError) ---
    try:
        # 💡 1. المؤسساتي: حساب الـ CVD عبر ميل متسارع (Slope) لتجنب ضوضاء الشمعة الواحدة
        if 'cvd' in df.columns and len(df) >= 10:
            cvd_slope = df['cvd'].diff(5).iloc[-1] # قياس ميل تدفق السيولة في آخر 5 شموع
            cvd_ma = df['cvd'].rolling(10).mean().iloc[-1] # المتوسط المتحرك للسيولة
            current_cvd = df['cvd'].iloc[-1]
            
            # السيولة الحقيقية إيجابية فقط إذا كان الاتجاه صاعد والسعر فوق متوسط السيولة
            bullish_flow = (cvd_slope > 0) and (current_cvd > cvd_ma)
            bearish_flow = (cvd_slope < 0) and (current_cvd < cvd_ma)
        else:
            # خط دفاعي في حال عدم توفر بيانات كافية
            cvd_diff = df['cvd'].diff().iloc[-1] if 'cvd' in df.columns else 0
            bullish_flow = cvd_diff > 0
            bearish_flow = cvd_diff < 0
    except:
        bullish_flow = False
        bearish_flow = False

    # 💡 2. المؤسساتي: حساب الحدود الديناميكية للـ RSI (Dynamic Thresholds)
    try:
        if 'rsi' in df.columns and len(df) >= 14:
            rsi_ma = df['rsi'].rolling(14).mean().iloc[-1]
        else:
            rsi_ma = 50.0
        
        # النطاق يتسع ويضيق حسب حالة السوق (بدل 70 و 30 الثابتة)
        rsi_upper = rsi_ma + 15  
        rsi_lower = rsi_ma - 15  
    except:
        rsi_ma, rsi_upper, rsi_lower = 50.0, 65.0, 35.0

    # ==========================================
    # 🤖 المولد النصي الكمي المؤسساتي (Quant Text Generator)
    # ==========================================
    
            # ==========================================
    # 🤖 المولد النصي الكمي المؤسساتي (Quant Text Generator)
    # ==========================================
    
    # 💡 التعديل الأول: تقريب الـ MACD والـ RSI لمرتبتين عشريتين من الجذور (Formatting Fix)
            # إرجاع القيم لدقتها الأصلية لحماية العملات ذات الكسور العميقة
    safe_rsi = float(last_rsi) if not pd.isna(last_rsi) else 50.0
    safe_macd = float(last_macd) if not pd.isna(last_macd) else 0.0
            # 💡 التعديل: إخفاء Z-Score تماماً لعملات الـ DEX
    vol_state = "" if is_dex else f"(Z-Score: {z_score:.1f})"

    # ====================================================================
    # 🧠 المحرك الكمي المطور (Institutional Conviction & Mean Reversion Engine)
    # يقضي على التحيز التأكيدي (Confirmation Bias) للفريمات الكبيرة والصغيرة
    # ====================================================================
    
    # 1. حساب الانحراف المعياري لحركة السعر (Price Z-Score / Mean Reversion Risk)
    # هل المستخدم يسأل عن عملة طارت بالفعل وصارت خطرة؟ أم عملة في القاع؟
    recent_returns = df['close'].pct_change().dropna().tail(20)
    price_z_score = (recent_returns.iloc[-1] - recent_returns.mean()) / (recent_returns.std() + 1e-8)
    
    # 2. حساب حافة الفوليوم (Volume Edge) باستخدام CDF (احتمالية من 0 إلى 100)
    vol_edge = quant_cdf_score(z_score, limit=100.0)
    
    # 3. حساب حافة التدفق (Orderflow Edge) للتغلب على نقص الأوردر بوك في الفريمات الكبيرة
    # نستخدم delta_usd مقارنة بمتوسط السيولة
    avg_vol_usd = df["volume"].tail(20).mean() * price
    flow_ratio = (delta_usd / avg_vol_usd) if avg_vol_usd > 0 else 0.0
    
    # إذا كانت DEX، التدفق اللحظي غير متاح، فنعتمد على ميل السيولة الكلية
    if is_dex and 'cvd' in df.columns and len(df) >= 10:
        cvd_slope = df['cvd'].diff(5).iloc[-1]
        flow_ratio = (cvd_slope / avg_vol_usd) if avg_vol_usd > 0 else 0.0
        
    flow_edge = quant_sigmoid_score(flow_ratio, sensitivity=5.0, limit=100.0)
    # 4. بناء السكور المؤسساتي النهائي (Absolute Confidence Score)
    # نحول التدفق والمؤشرات لتكون إيجابية دائماً "باتجاه الترند" لحساب نسبة الثقة
    if final_trend_dir == "Bullish":
        directional_flow = flow_edge
        directional_rsi = safe_rsi
    else:
        directional_flow = 100.0 - flow_edge # في الهبوط، التدفق الصفر يعني 100% ثقة بالهبوط
        directional_rsi = 100.0 - safe_rsi   # في الهبوط، الـ RSI المنخفض يعني زخم بيعي قوي

    # دمج الفوليوم مع التدفق الاتجاهي (نسبة الثقة النهائية)
    conviction_score = (vol_edge * 0.30) + (directional_flow * 0.50) + (directional_rsi * 0.20)
    
    # عقاب الفومو (Late FOMO Penalty): للسوق الصاعد
    if final_trend_dir == "Bullish" and price_z_score > 2.0 and flow_edge < 50.0:
        conviction_score *= math.exp(-0.5 * price_z_score) 
        
    # عقاب الانهيار الكاذب (Fake Dump Penalty): للسوق الهابط
    elif final_trend_dir == "Bearish" and price_z_score < -2.0 and flow_edge > 50.0:
        conviction_score *= math.exp(0.5 * price_z_score) 
        
    # 5. تحليل التباين الهيكلي (Macro Divergence)
    is_fomo_trap = price_z_score > 2.0 and flow_edge < 40.0 and vol_edge < 60.0
    is_capitulation_absorption = price_z_score < -2.0 and flow_edge > 60.0 and vol_edge > 70.0
    is_trend_backed_by_flow = (final_trend_dir == "Bullish" and flow_edge > 60.0) or (final_trend_dir == "Bearish" and flow_edge < 40.0)


    # إرجاع القيم لدقتها الأصلية
    safe_rsi = float(last_rsi) if not pd.isna(last_rsi) else 50.0
    safe_macd = float(last_macd) if not pd.isna(last_macd) else 0.0
    vol_state = "" if is_dex else f"(Z-Score: {z_score:.1f})"

    # ==========================================
    # 🤖 المولد النصي الكمي المؤسساتي (Quant Text Generator)
    # ==========================================
    if lang == "ar":
        # 1. نصوص المؤشرات المبنية على الانحرافات الإحصائية
        if safe_rsi >= rsi_upper: rsi_txt = f"انحراف شرائي حاد. السعر خارج اعلى من قيمته العادلة، خطر الانعكاس (Mean Reversion) مرتفع."
        elif safe_rsi <= rsi_lower: rsi_txt = f"تشبع بيعي. السعر يتداول دون قيمته العادلة (Undervalued)، احتمالية تكوين قاع."
        else: rsi_txt = f"زخم متوازن. السعر يتمركز بثبات حول مناطق القيمة العادلة (Fair Value)."
        
        if adx_val >= 25: adx_txt = "ترند صريح وقوي. المؤسسات تدعم هذا المسار بزخم واضح."
        else: adx_txt = "انخفاض حاد في التقلبات. تذبذب عرضي وانتظار لضخ سيولة جديدة."
        
        if safe_macd > 0: macd_txt = "تمركز سيولة إيجابي على المدى القصير (سيطرة مشترين)."
        else: macd_txt = "ضغوط تسييل بيعية على المدى القصير (سيطرة بائعين)."
        
        # 2. توليد حالة السوق (Market Action) بناءً على قناعة الذكاء الاصطناعي
        if final_trend_dir == "Bullish":
            real_trend = "صاعد"
            if is_fomo_trap:
                market_action = f"فخ تحيز تأكيدي (FOMO Trap)! السعر صعد بقوة لكن التدفق المالي ضعيف جداً {vol_state}. احذر من تصريف مفاجئ."
                trend_strength = "مخادع (احتمال انعكاس قاسي)"
            elif is_trend_backed_by_flow:
                market_action = f"ترند صحي ومدعوم بتدفق أموال مؤسساتي (Orderflow Backed) {vol_state}. المشترون يمتصون العروض."
                trend_strength = f"قوي (درجة الثقة {conviction_score:.0f}%)"
            else:
                market_action = f"صعود باهت بسبب ضعف السيولة (Low Vol Markup) {vol_state}. الحركة تفتقر للزخم المؤسساتي."
                trend_strength = "ضعيف (سيولة هشة)"
                
        else: # Bearish
            real_trend = "هابط"
            if is_capitulation_absorption:
                market_action = f"استسلام بيعي (Capitulation) يقابله امتصاص شرائي ضخم {vol_state}! الحيتان تشتري الذعر اللحظي لبناء قاع."
                trend_strength = "ارتداد محتمل (بناء قاع)"
            elif is_trend_backed_by_flow:
                market_action = f"سيطرة بيعية حقيقية وتفريغ مستمر للسيولة (Liquidity Drain) {vol_state}. الهبوط مدعوم بتدفق سلبي."
                trend_strength = f"قوي (درجة الثقة {conviction_score:.0f}%)"
            else:
                market_action = f"هبوط بطيء بلا زخم حقيقي (Low Vol Markdown) {vol_state}. غياب للمشترين أكثر من كونه قوة بيعية."
                trend_strength = "متذبذب (هبوط صامت)"

        if is_spoofed: market_action += " [رصدنا خوارزميات تضع جدران وهمية للتلاعب بالسعر]"
        if is_orderbook_hollow: market_action += " [رصدنا فراغ سيولي، السعر قد ينزلق بعنف]"
        if funding_val < -0.001 and not is_dex: market_action += " [تمويل سالب بشدة: خطر تصفية البائعين Short Squeeze]"

    else: # English Version (Institutional Grade)
        if safe_rsi >= rsi_upper: rsi_txt = f"Statistical deviation ({safe_rsi:.1f}). Overextended beyond Fair Value, extreme Mean Reversion risk."
        elif safe_rsi <= rsi_lower: rsi_txt = f"Deep Oversold ({safe_rsi:.1f}). Trading at a severe discount, bottom formation probable."
        else: rsi_txt = f"Balanced Momentum ({safe_rsi:.1f}). Price respecting Fair Value boundaries."
        
        if adx_val >= 25: adx_txt = "Strong Directional Conviction. Trend backed by institutional momentum."
        else: adx_txt = "Volatility Contraction. Range-bound chop, awaiting liquidity injection."
        
        if safe_macd > 0: macd_txt = "Positive short-term capital deployment."
        else: macd_txt = "Negative short-term capital extraction."
        
        if final_trend_dir == "Bullish":
            real_trend = "Bullish"
            if is_fomo_trap:
                market_action = f"FOMO TRAP! Price rallied but Orderflow is severely disconnected {vol_state}. High risk of sudden distribution."
                trend_strength = "Fake (High Reversion Risk)"
            elif is_trend_backed_by_flow:
                market_action = f"Healthy trend fully backed by positive Institutional Orderflow {vol_state}. Bids absorbing ask liquidity."
                trend_strength = f"Strong ({conviction_score:.0f}% Quant Conviction)"
            else:
                market_action = f"Low Volume Markup {vol_state}. Uptrend lacks genuine institutional footprint."
                trend_strength = "Weak (Fragile Liquidity)"
                
        else: # Bearish
            real_trend = "Bearish"
            if is_capitulation_absorption:
                market_action = f"Capitulation Event! Massive panic selling met with hidden limit buy absorption {vol_state}. Bottom forming."
                trend_strength = "Reversal (Bottoming)"
            elif is_trend_backed_by_flow:
                market_action = f"Genuine distribution and continuous liquidity drain {vol_state}. Sellers in full control."
                trend_strength = f"Strong ({conviction_score:.0f}% Quant Conviction)"
            else:
                market_action = f"Low Volume Markdown {vol_state}. Dropping due to lack of bids rather than aggressive selling."
                trend_strength = "Choppy (Silent Drop)"

        if is_spoofed: market_action += " [Algorithmic Spoofing Detected in Orderbook]"
        if is_orderbook_hollow: market_action += " [Liquidity Void: High slippage risk]"
        if funding_val < -0.001 and not is_dex: market_action += " [Deep Negative Funding: Short Squeeze Imminent]"

    # بناء التقرير النهائي
    macd_fmt = format_price(safe_macd)
    if is_dex:
        market_action = f"(تحليل شبكة DEX) | {market_action}" if lang == "ar" else f"(DEX Network) | {market_action}"

    # 👇👇 السطور الجديدة التي ستضيفها هنا 👇👇
    dex_alert_str = ""
    if is_dex and dex_warnings_ar:
        if lang == "ar":
            dex_alert_str = "\n🛡️ <b>تدقيق أمان اللامركزية (DEX Audit):</b>\n" + "\n".join(dex_warnings_ar) + "\n"
        else:
            dex_alert_str = "\n🛡️ <b>DEX Security Audit:</b>\n" + "\n".join(dex_warnings_en) + "\n"
    # 👆👆 نهاية السطور الجديدة 👆👆

    if lang == "ar":
        final_report = f"""
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

📈 <b>تحليل المؤشرات والسيولة</b>
• <b>التدفق:</b> {market_action}
• <b>مؤشر (RSI) ({safe_rsi:.1f}):</b> {rsi_txt}
• <b>مؤشر (MACD) ({macd_fmt}):</b> {macd_txt}
• <b>مؤشر (ADX) ({adx_val:.1f}):</b> {adx_txt}
{dex_alert_str}
"""
    else:
        final_report = f"""
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

📈 <b>Liquidity & Indicators</b>
• <b>Flow:</b> {market_action}
• <b>RSI ({safe_rsi:.1f}):</b> {rsi_txt}
• <b>MACD ({macd_fmt}):</b> {macd_txt}
• <b>ADX ({adx_val:.1f}):</b> {adx_txt}
{dex_alert_str}
"""
    # 3. إرسال النتيجة فوراً للمستخدم (بدون انتظار أي سيرفر خارجي)
    try:
        await cb.message.edit_text(final_report, parse_mode=ParseMode.HTML)
    except Exception as e:
        if "message is not modified" not in str(e):
            await cb.message.answer(final_report, parse_mode=ParseMode.HTML)


    
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
                # 🧠 الجديد: إنشاء جدول تدريب الذكاء الاصطناعي المؤسساتي (ML Data)        # 🧠 إنشاء جدول تدريب الذكاء الاصطناعي المؤسساتي (Hedge Fund Schema)
                # 🧠 The Ultimate Hedge Fund Data Schema
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ml_training_data (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                signal_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                entry_price DOUBLE PRECISION,
                
                -- Features (المدخلات - مقيسة كنسب مئوية)
                market_regime INTEGER,          
                sp500_trend_pct DOUBLE PRECISION, 
                sentiment_score DOUBLE PRECISION,
                vol_z_score DOUBLE PRECISION,   
                cvd_to_vol_ratio DOUBLE PRECISION, 
                imbalance_ratio DOUBLE PRECISION,  
                ob_skewness DOUBLE PRECISION,
                whale_dominance_pct DOUBLE PRECISION, 
                adx DOUBLE PRECISION,
                rsi DOUBLE PRECISION,
                micro_volatility_pct DOUBLE PRECISION, 
                cvd_divergence DOUBLE PRECISION,
                funding_rate DOUBLE PRECISION,
                
                -- Multi-Horizon Targets (النتائج عبر آفاق زمنية مختلفة)
                ret_1h DOUBLE PRECISION DEFAULT NULL, -- العائد بعد ساعة
                ret_4h DOUBLE PRECISION DEFAULT NULL, -- العائد بعد 4 ساعات
                ret_24h DOUBLE PRECISION DEFAULT NULL, -- العائد بعد 24 ساعة
                
                -- Path Metrics (جودة مسار السعر)
                max_favorable_excursion DOUBLE PRECISION DEFAULT NULL, -- MFE
                max_adverse_excursion DOUBLE PRECISION DEFAULT NULL,   -- MAE
                
                -- Alpha Metric (العائد مقارنة بالبيتكوين)
                btc_return_24h DOUBLE PRECISION DEFAULT NULL,
                alpha_24h DOUBLE PRECISION DEFAULT NULL,
                
                -- The Ultimate Label (تقييم الجودة من -1.0 إلى 1.0)
                trade_quality_score DOUBLE PRECISION DEFAULT NULL,
                
                -- Processing Status (0: قيد الانتظار, 1: تم التقييم)
                is_processed INTEGER DEFAULT 0                              
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_pending ON ml_training_data(is_processed, signal_time)")

        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")
                # 🧠 ترقية جدول الذكاء الاصطناعي (إضافة كل الأعمدة المؤسساتية الجديدة إن لم تكن موجودة)
        
        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    #asyncio.create_task(smart_radar_watchdog(pool))
    asyncio.create_task(silent_data_harvester_worker(pool))
    asyncio.create_task(macro_data_worker()) # 🌍 تشغيل عامل الماكرو
    #asyncio.create_task(radar_worker_process(pool))
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