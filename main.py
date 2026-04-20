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
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_HEADERS = {"X-MBX-APIKEY": BINANCE_API_KEY}
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
# طابور معالجة العملات المنفجرة (لحماية الـ API من الضغط)
radar_processing_queue = asyncio.Queue()

# ذاكرة لتسجيل العملات التي تم إرسالها اليوم لمنع تكرارها
daily_signaled_coins = {}
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
async def get_recent_orderflow_delta(symbol, client, limit=500):
    """
    بديل سريع وآمن للـ WebSocket: يقرأ آخر 500 صفقة تمت لتحديد الشراء/البيع العدواني
    """
    try:
        res = await client.get(f"{BINANCE_BASE}/trades", params={"symbol": symbol, "limit": limit})
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
                
                # هنا نحسب الـ Absorption (امتصاص البيع)
                # إذا كان السعر ثابتاً أو يرتفع رغم وجود Delta بيعي ضخم = الحيتان تمتص العروض

# ذاكرة مؤقتة لتتبع الفوليوم والسعر
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

                        current_vol = float(ticker['q']) 
                        current_price = float(ticker['c'])

                        if symbol in live_market_memory:
                            old_data = live_market_memory[symbol]
                            old_vol = old_data['volume']
                            old_price = old_data['price']
                            time_diff = current_time - old_data['last_update']

                            # فحص الانفجار السعري
                            if time_diff >= 60 and old_vol > 0:
                                vol_change = (current_vol - old_vol) / old_vol
                                price_change = (current_price - old_price) / old_price

                                if current_vol >= MIN_VOLUME_USD and vol_change >= VOLUME_SPIKE_THRESHOLD and price_change >= PRICE_SPIKE_THRESHOLD:
                                    
                                    # إرسال العملة إلى الطابور فوراً للتحليل العميق (بدون انتظار)
                                    coin_mock_data = {
                                        "symbol": symbol.replace("USDT", ""),
                                        "quote": {"USD": {"price": current_price}}
                                    }
                                    # نضعها في الطابور ليقوم العامل (Worker) بمعالجتها بهدوء
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
        res = await client.get(f"{BINANCE_BASE}/klines", params={
            "symbol": "BTCUSDT", "interval": "1d", "limit": 25
        }, headers=BINANCE_HEADERS)
        if res.status_code == 200:
            data = res.json()
            # في بايننس الإغلاق هو المؤشر رقم 4
            close_prices = [float(c[4]) for c in data]
            sma20 = sum(close_prices[-20:]) / 20
            return close_prices[-1] > sma20
    except:
        pass
    return True # افتراضي في حال فشل الـ API
import ta
import pandas as pd

async def detect_market_regime(client):
    """
    تحليل حالة السوق العامة (الماكرو) بناءً على حركة البيتكوين.
    """
    # جلب شمعة الـ 4 ساعات للبيتكوين لتحديد الاتجاه العام
    res = await client.get(f"https://api.binance.com/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "4h", "limit": 100})
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

async def analyze_orderbook_depth(symbol, client):
    """
    تحليل أعمق لـ 500 مستوى في الأوردر بوك لكشف الجدران المخفية (Iceberg) والتلاعب (Spoofing)
    """
    pair = f"{symbol.upper()}USDT"
    # غيرنا الليمت إلى 500 لكشف الأعماق الحقيقية
    res = await client.get(f"https://api.binance.com/api/v3/depth", params={"symbol": pair, "limit": 500})
    
    if res.status_code == 200:
        data = res.json()
        bids = data['bids'] # طلبات الشراء (الدعم)
        asks = data['asks'] # طلبات البيع (المقاومة)
        
        # تقسيم العمق إلى 3 مناطق: قريبة (1-50)، متوسطة (51-200)، بعيدة (201-500)
        # المنطقة القريبة (السيولة اللحظية الحقيقية التي تحرك السعر الآن)
        near_bids_vol = sum(float(b[0]) * float(b[1]) for b in bids[:50])
        near_asks_vol = sum(float(a[0]) * float(a[1]) for a in asks[:50])
        
        # المنطقة البعيدة (هنا يضع الحيتان الأوامر الوهمية Spoofing)
        far_bids_vol = sum(float(b[0]) * float(b[1]) for b in bids[200:500])
        far_asks_vol = sum(float(a[0]) * float(a[1]) for a in asks[200:500])

        # 1. حساب خلل التوازن الحقيقي (Order Book Imbalance) للمنطقة القريبة
        total_near = near_bids_vol + near_asks_vol
        imbalance = (near_bids_vol - near_asks_vol) / total_near if total_near > 0 else 0
        
        # 2. كشف الخداع (Spoofing Detection)
        # إذا كانت الطلبات البعيدة ضخمة جداً (أكبر بـ 5 أضعاف من القريبة) فهذا فخ وهمي لدفع السعر للأسفل ثم سحبها!
        spoofing_risk = far_bids_vol > (near_bids_vol * 5.0)
        
        # 3. كشف جدار البيع الخفي (Iceberg Distribution)
        # إذا كان الدعم القريب قوي جداً، لكن يوجد جدار بيع بعيد أضخم منه، الحوت يصرف بصمت
        hidden_wall_risk = far_asks_vol > (near_asks_vol * 8.0) and far_asks_vol > (near_bids_vol * 3.0)

        return {
            "imbalance": round(imbalance, 2), # من 1.0 (شراء كاسح) إلى -1.0 (بيع كاسح)
            "spoofing_risk": spoofing_risk,
            "hidden_wall": hidden_wall_risk,
            "real_support": near_bids_vol
        }
    return None


async def get_aggregated_orderbook(client: httpx.AsyncClient, symbol: str):
    """
    جلب ودمج الأوردر بوك من 6 منصات لقرائة ضغط الحيتان
    Binance, Bybit, Gate.io, KuCoin, OKX, MEXC
    """
    sym_binance_mexc = f"{symbol}USDT"
    sym_gate = f"{symbol}_USDT"
    sym_kucoin_okx = f"{symbol}-USDT"

    urls = {
        "binance": f"https://api.binance.com/api/v3/depth?symbol={sym_binance_mexc}&limit=50",
        "bybit": f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={sym_binance_mexc}&limit=50",
        "gate": f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={sym_gate}&limit=50",
        "kucoin": f"https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol={sym_kucoin_okx}",
        "okx": f"https://www.okx.com/api/v5/market/books?instId={sym_kucoin_okx}&sz=50",
        "mexc": f"https://api.mexc.com/api/v3/depth?symbol={sym_binance_mexc}&limit=50"
    }

    async def fetch_ob(exchange, url):
        try:
            res = await client.get(url, timeout=3.0) 
            if res.status_code == 200:
                data = res.json()
                bids_vol, asks_vol = 0.0, 0.0
                
                if exchange in ["binance", "mexc", "gate"]:
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in data.get("bids", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in data.get("asks", []))
                elif exchange == "bybit":
                    result = data.get("result", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in result.get("b", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in result.get("a", []))
                elif exchange == "kucoin":
                    d = data.get("data", {})
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", [])[:50])
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", [])[:50])
                elif exchange == "okx":
                    d = data.get("data", [{}])[0]
                    bids_vol = sum(float(b[0]) * float(b[1]) for b in d.get("bids", []))
                    asks_vol = sum(float(a[0]) * float(a[1]) for a in d.get("asks", []))

                return exchange, bids_vol, asks_vol
        except:
            pass 
        return exchange, 0.0, 0.0

    tasks = [fetch_ob(ex, url) for ex, url in urls.items()]
    results = await asyncio.gather(*tasks)

    total_bids_usd = 0.0
    total_asks_usd = 0.0

    # ----- الطباعة في اللوغ -----
        # ----- الطباعة في اللوغ -----
    print(f"\n📊 --- تفاصيل الأوردر بوك لعملة {symbol} ---", flush=True)
    for exchange, bids, asks in results:
        total_bids_usd += bids
        total_asks_usd += asks
        # طباعة المنصات التي تحتوي على بيانات فقط لتجنب الإزعاج
        if bids > 0 or asks > 0:
            print(f"🔹 {exchange.upper():<8}: Bids = ${bids:,.0f} | Asks = ${asks:,.0f}", flush=True)
            
    print(f"🌍 الإجمالي: Bids = ${total_bids_usd:,.0f} | Asks = ${total_asks_usd:,.0f}", flush=True)
    print("------------------------------------------\n", flush=True)

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
    if price_change_pct <= 0.02 and cvd_change > (recent["volume"].mean() * 0.5):
        score_boost = 15.0 
        signal_upgrade = "Whale_CVD"
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

async def analyze_radar_coin(c, client, market_regime, sem):
    async with sem:  
        try:
            symbol = c["symbol"]
            price = float(c["quote"]["USD"]["price"])
            
            candles = await get_candles_binance(f"{symbol}USDT", "1h", limit=750)
            if not candles: return None

            df = pd.DataFrame(candles)
            df = df.iloc[:, :7] 
            df.columns = ["timestamp", "volume", "close", "high", "low", "open", "taker_buy_vol"]
            for col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')

            delta = df["close"].diff()
            gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            loss = (-1 * delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            df["rsi"] = 100 - (100 / (1 + (gain / loss)))
            last_rsi = df["rsi"].iloc[-1]
            
            try: current_adx = float(ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14, fillna=True).adx().iloc[-1])
            except: current_adx = 0.0

            # 🟢 البداية من سكور 20 لتوزيع النسب باحترافية
            score = 20.0
            tags = [] # قائمة لتجميع نوع الحركات

            current_z, vol_mean, vol_std = calculate_volume_zscore(df, window=720)
            
            pool = dp['db_pool']
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO market_memory (symbol, vol_mean, vol_stddev, z_score, last_updated)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                    ON CONFLICT (symbol) DO UPDATE 
                    SET vol_mean = EXCLUDED.vol_mean, vol_stddev = EXCLUDED.vol_stddev, z_score = EXCLUDED.z_score, last_updated = CURRENT_TIMESTAMP
                """, symbol, vol_mean, vol_std, current_z)

            # 1. فلتر الفوليوم
            if current_z >= 4.0:
                score += 20.0
                tags.append("Z_Anom")
            elif current_z >= 3.0:
                score += 12.0
                tags.append("Z_High")
            elif current_z >= 2.0:
                score += 5.0
            elif current_z < 0: return None 

            # 2. فلتر CVD
            cvd_boost, cvd_signal = detect_smart_money_absorption(df)
            score += cvd_boost
            if cvd_signal: tags.append(cvd_signal)

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

            # 5. الفحص العميق (Order Flow + Global)            # 5. الفحص العميق (Order Flow + Global)            # 5. الفحص العميق (Order Flow + Global)
            if score >= 50.0: 
                depth_data = await analyze_orderbook_depth(symbol, client)
                if depth_data:
                    if depth_data['spoofing_risk'] or depth_data['hidden_wall']:
                        score -= 15.0 # تعديل: تخفيف العقوبة لأن الـ Spoofing طبيعي في العملات الصغيرة
                        tags.append("Spoofing_or_Wall")
                    elif depth_data['imbalance'] > 0.4:
                        score += 15.0 
                        tags.append("OB_Buy")

                # تعديل: استخدام دالة الـ REST السريعة بدلاً من إيقاف الكود بـ WebSocket
                delta_usd = await get_recent_orderflow_delta(symbol, client)
                if delta_usd > (price * 1000): 
                    score += 15.0
                    tags.append("Aggressive_Buy")
                elif delta_usd < -(price * 1000): 
                    score -= 15.0 # تخفيف العقوبة السلبية هنا أيضاً

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
            final_signal = "High Probability Setup 🎯"
            if "Fake_Pump" in tags or "Spoofing_or_Wall" in tags or "Short_Covering" in tags:
                return None 
            elif "Whale_CVD" in tags and "Aggressive_Buy" in tags: final_signal = "Aggressive Whale Accumulation 🐋"
            elif "Short_Squeeze" in tags: final_signal = "(Short Squeeze) 🔥"
            elif "Z_Anom" in tags and "OI_Rising" in tags: final_signal = "(Derivatives Pump) 🚀"
            elif "Squeeze" in tags and "OB_Buy" in tags: final_signal = "(Liquidity Breakout) ⚡"
            elif "Whale_CVD" in tags: final_signal = "Silent Institutional Accumulation 🧲"
            elif "Z_Anom" in tags or "Z_High" in tags: final_signal = "Smart Money Inflow 💸"

            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
            current_vol_ratio = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0

            # 🟢 1. تحديد الإشارات الحيوية (المفاتيح الذهبية)
            golden_tags = {"Z_Anom", "Z_High", "Whale_CVD", "Aggressive_Buy", "OB_Buy", "Squeeze"}
            # كم إشارة ذهبية اجتمعت في هذه العملة؟
            confluence_count = sum(1 for tag in tags if tag in golden_tags)

            # 🟢 2. فلتر الاتجاه المعاكس (لا تشتري سكين تسقط)
            is_macro_downtrend = price < ema200_val

            # 🟢 3. شروط القناص النهائي:
            required_score = 85.0 if (market_regime['trend'] == "Trending_Bear" or is_macro_downtrend) else 75.0
            required_confluence = 3 if (market_regime['trend'] == "Trending_Bear" or is_macro_downtrend) else 2

            # إرجاع النتيجة فقط إذا تحقق السكور + الإجماع الفني
            if score >= required_score and confluence_count >= required_confluence:  
                return {
                    "symbol": symbol, "price": price, "score": score,
                    "rsi": round(last_rsi, 2), "adx": round(current_adx, 2),
                    "macd": current_z, 
                    "vol_ratio": round(current_vol_ratio, 2),
                    "ob_pressure": round(locals().get('global_ob_pressure', 1.0), 2),
                    "signal_type": final_signal,
                    "confluence": confluence_count
                }
            return None  
        except Exception:
            return None


async def ai_opportunity_radar(pool):
    print("🚀 تم تشغيل الرادار الشامل (وضع صيد القيعان)...")
    sem = asyncio.Semaphore(5)
    
    while True:
        try:
            print("🔍 جاري جلب 1000 عملة للبحث عن الجواهر المنسية...")
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
            STABLE_COINS = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD"}

            async with pool.acquire() as conn:
                records = await conn.fetch("""
                    SELECT symbol FROM radar_history 
                    WHERE last_signaled > CURRENT_TIMESTAMP - INTERVAL '7 days'
                """)
                ignored_symbols = {r['symbol'] for r in records}

            async with httpx.AsyncClient(timeout=30) as client:
                # 🟢 التعديل هنا: جلب بيانات الماكرو الجديدة بدل البوليان القديم
                market_regime = await detect_market_regime(client)
                
                res = await client.get(
                    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                    headers=headers, params={"limit": "1000"} 
                )
                
                if res.status_code != 200:
                    await bot.send_message(ADMIN_USER_ID, "❌ فشل الاتصال بـ CoinMarketCap. سيتم إعادة المحاولة...")
                    await asyncio.sleep(60)
                    continue
                
                coins = [
                    c for c in res.json()["data"] 
                    if c["symbol"] not in STABLE_COINS 
                    and c["symbol"] not in ignored_symbols
                    and c["quote"]["USD"]["volume_24h"] >= 1_000_000 
                    and abs(c["quote"]["USD"]["percent_change_24h"]) >= 0.2
                ]

                tasks = [analyze_radar_coin(c, client, market_regime, sem) for c in coins]
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

                prompt_ar = f"""
أنت كبير المحللين الفنيين. رادار السوق الذكي التقط فرصة لعملة {symbol} بسكور {best_score}/100.
الإشارة: {signal} | ADX: {best_meta['adx']} | RSI: {best_meta['rsi']} | سيولة أعلى بـ {best_meta['vol_ratio']} ضعف.
ضغط الشراء (Orderbook): طلبات الشراء تتفوق بـ {best_meta.get('ob_pressure', 1.0)} ضعف.
اكتب تحليلاً احترافياً (3 أسطر قصار) يدمج هذه الأرقام مباشرة ويوضح سبب احتمال الصعود بدون ذكر قوي وقوة وتهويل.
"""
                prompt_en = f"""
You are Lead Technical Analyst. Smart market caught a bottom opportunity for {symbol} with score {best_score}/100.
Signal: {signal} | ADX: {best_meta['adx']} | RSI: {best_meta['rsi']} | Liquidity {best_meta['vol_ratio']}x higher.
Buy Pressure (Orderbook): Buy bids are {best_meta.get('ob_pressure', 1.0)}x stronger.
Write a 3-short line professional analysis integrating these metrics and explaining the current accumulation Without mentioning strength, power, or exaggeration.
"""

                insight_ar = await ask_groq(prompt_ar, lang="ar")
                insight_en = await ask_groq(prompt_en, lang="en")

                signal_id = str(uuid.uuid4())[:8] 
                radar_pending_approvals[signal_id] = {
                    "symbol": symbol, "price": price, "signal": signal, "score": best_score,
                    "insight_ar": insight_ar, "insight_en": insight_en
                }

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
                
                break

        except Exception as e:
            print(f"Radar Error: {e}")
            await asyncio.sleep(60)
            continue
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

async def get_candles_dex(network: str, pool_address: str, interval: str, limit: int = 500):
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
            # بايننس تستخدم الرمز متصل بدون شرطة سفلية، مثلاً BTCUSDT
            pair = f"{sym}USDT" 
            
            # تمرير مفتاحك الخاص في الهيدر
            binance_headers = {
                "X-MBX-APIKEY": "rvApoDI6XRYcki1r2QTnPUBs3QwESzrpTVKohgjbK1zxSzlvrFPxAbZKr94xA2Lx"
            }
            
            # جلب السعر والفوليوم من بايننس باستخدام حسابك
            res_binance = await client.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": pair},
                headers=binance_headers,
                timeout=10
            )
            
            if res_binance.status_code == 200:
                data_binance = res_binance.json()
                price = float(data_binance["lastPrice"])
                volume_24h = float(data_binance["quoteVolume"]) # الفوليوم بـ USDT

                # محاولة جلب نسبة تغير الفوليوم من CMC
                try:
                    res_cmc = await client.get(
                        f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={sym}",
                        headers={"X-CMC_PRO_API_KEY": CMC_KEY},
                        timeout=5
                    )
                    data_cmc = res_cmc.json()
                    if res_cmc.status_code == 200 and sym in data_cmc.get("data", {}):
                        quote_data = data_cmc["data"][sym]["quote"]["USD"]
                        vol_change_cmc = float(quote_data.get("volume_change_24h", 0))
                        
                        # تحديث قاعدة البيانات
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
                raise ValueError("Symbol not found in Binance")

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
async def get_candles_binance(symbol: str, interval: str, limit: int = 500, retries: int = 3):
    clean_symbol = symbol.replace("_", "") 
    
    async with httpx.AsyncClient() as client:
        for attempt in range(retries):
            try:
                res = await client.get(
                    f"{BINANCE_BASE}/klines",
                    params={"symbol": clean_symbol, "interval": interval, "limit": limit},
                    headers=BINANCE_HEADERS,
                    timeout=10
                )
                if res.status_code == 200:
                    data = res.json()
                    formatted_candles = []
                    for c in data:
                        formatted_candles.append([
                            str(int(c[0] / 1000)), # 0: Timestamp
                            c[5], # 1: Total Volume
                            c[4], # 2: Close
                            c[2], # 3: High
                            c[3], # 4: Low
                            c[1], # 5: Open
                            c[9]  # 6: Taker Buy Base Asset Volume (هنا السر: أموال الشراء العدواني)
                        ])
                    return formatted_candles
                elif res.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    return None
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

def calculate_smart_trend_and_targets(df, current_price, db_vol_change, lang="ar"):
    
    # 🟢 الحل الجذري: إجبار تحويل الأعمدة إلى أرقام (Floats) قبل أي عملية حسابية
    for col in ['high', 'low', 'close', 'open', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # هنا بيبدأ كودك القديم طبيعي جداً
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

    micro_bull = ema20 > ema50
    trend_direction = "Bullish" if micro_bull else "Bearish"

    if real_adx_value < 20:
        trend_strength = "ضعيف" if lang == "ar" else "Weak"
        market_action = "صراع سيولة وتجميع حول VWAP اليومي في نطاق عرضي" if lang == "ar" else "Liquidity struggle and accumulation around daily VWAP in a ranging market"
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
                trend_strength = "ضعيف" if lang == "ar" else "Weak"
                market_action = "الحيتان تشتري الهبوط سرًا" if lang == "ar" else "Whales are silently buying the dip"
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
        market_action += f" [هدف مغناطيسي FVG عند: {fvg_display}$]" if lang == "ar" else f" [Magnetic FVG Target at: {fvg_display}$]"


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
    """
    محرك Volume Profile لحساب الأهداف ووقف الخسارة بناءً على العقد السعرية الفعلية.
    """
    try:
        # 1. تحديد النطاق السعري لآخر فترة
        min_price = df['low'].min()
        max_price = df['high'].max()

        # 2. تقسيم النطاق إلى 50 مستوى سعري (Bins)
        price_bins = np.linspace(min_price, max_price, num_bins)
        
        # 3. حساب السعر النموذجي (Typical Price) لكل شمعة
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3

        # 4. توزيع الفوليوم على المستويات السعرية
        df['bin_index'] = np.digitize(df['typical_price'], price_bins) - 1
        vol_profile = df.groupby('bin_index')['volume'].sum()

        # تجهيز القائمة النهائية للبروفايل
        profile = []
        for idx, vol in vol_profile.items():
            if 0 <= idx < len(price_bins):
                profile.append({'price': price_bins[idx], 'volume': vol})

        profile_df = pd.DataFrame(profile)
        if profile_df.empty:
            raise ValueError("Empty Profile")

        # 5. فصل المستويات السعرية أعلى وأسفل السعر الحالي
        above_price = profile_df[profile_df['price'] > current_price]
        below_price = profile_df[profile_df['price'] < current_price]

        # 6. استخراج الأهداف (TP) والستوب (SL)
        if trend_direction == "Bullish":
            # الأهداف (TP) هي أكبر 3 عقد سيولة (HVN) فوق السعر الحالي
            targets = above_price.nlargest(3, 'volume').sort_values('price')
            tps = targets['price'].tolist()

            # الدعم الرئيسي هو أكبر عقدة تحت السعر
            support_node = below_price.nlargest(1, 'volume')
            support_price = support_node['price'].iloc[0] if not support_node.empty else current_price * 0.95

            # وقف الخسارة (SL): نبحث عن منطقة فراغ سيولة (LVN) تحت الدعم مباشرة
            # حتى يكون الستوب محمي بجدار سيولة (الدعم)
            lvns_below_support = below_price[below_price['price'] < support_price]
            if not lvns_below_support.empty:
                sl_price = lvns_below_support.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = support_price * 0.98

        else: # Bearish
            # الأهداف هي أكبر 3 عقد سيولة تحت السعر
            targets = below_price.nlargest(3, 'volume').sort_values('price', ascending=False)
            tps = targets['price'].tolist()

            # المقاومة هي أكبر عقدة فوق السعر
            res_node = above_price.nlargest(1, 'volume')
            res_price = res_node['price'].iloc[0] if not res_node.empty else current_price * 1.05

            # وقف الخسارة (SL): منطقة فراغ (LVN) فوق المقاومة
            lvns_above_res = above_price[above_price['price'] > res_price]
            if not lvns_above_res.empty:
                sl_price = lvns_above_res.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = res_price * 1.02

        # تأمين النتائج في حال لم نجد 3 أهداف
        tp1 = tps[0] if len(tps) > 0 else current_price * (1.02 if trend_direction == "Bullish" else 0.98)
        tp2 = tps[1] if len(tps) > 1 else tp1 * (1.02 if trend_direction == "Bullish" else 0.98)
        tp3 = tps[2] if len(tps) > 2 else tp2 * (1.02 if trend_direction == "Bullish" else 0.98)

        # ترتيب الأهداف منطقياً (تصاعدي للشراء، تنازلي للبيع)
        if trend_direction == "Bullish":
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3])
            sl_price = min(sl_price, current_price * 0.99) # حماية أخيرة للستوب
        else:
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3], reverse=True)
            sl_price = max(sl_price, current_price * 1.01)

        return sl_price, tp1, tp2, tp3

    except Exception as e:
        print(f"VPVR Error: {e}")
        # أهداف احتياطية كلاسيكية في حال فشل الحساب
        return current_price*0.9, current_price*1.05, current_price*1.1, current_price*1.15

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
    last_rsi, last_macd, last_bb, last_vol, _, _ = compute_indicators(candles) 
        
    import pandas as pd 
    df = pd.DataFrame(candles)
    df = df.iloc[:, :6]
    df.columns = ["timestamp", "volume", "close", "high", "low", "open"]

    for col in ["close", "high", "low", "open", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    # ... (كمل باقي الكود من هنا: سحب الفوليوم من الداتا بيز، وتعريف الـ prompt بدون ما تخليهم جوا if) ...


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
        trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = calculate_smart_trend_and_targets(df, price, db_vol_float, lang)

        # 🟢 استعادة تعريف متغيرات RSI و MACD لتجنب خطأ NameError
        macd_fmt = format_price(last_macd) if 'last_macd' in locals() and last_macd is not None else "0.0"
        safe_rsi = f"{last_rsi:.2f}" if 'last_rsi' in locals() and last_rsi is not None else "N/A"

        # تجهيز المتغيرات للغتين وبناء البرومبت (محمية داخل شرط الشموع لتجنب أي خطأ)
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
                # ترقية جدول الذاكرة ليعمل بنظام الشذوذ الإحصائي
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS vol_mean DOUBLE PRECISION DEFAULT 0")
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS vol_stddev DOUBLE PRECISION DEFAULT 0")
        await conn.execute("ALTER TABLE market_memory ADD COLUMN IF NOT EXISTS z_score DOUBLE PRECISION DEFAULT 0")
        # 2. إجبار تحديث الجداول القديمة (للمشتركين الحاليين)
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS last_active DATE")
        await conn.execute("ALTER TABLE paid_users ADD COLUMN IF NOT EXISTS expiry_date TIMESTAMP")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS invited_by BIGINT")
        await conn.execute("ALTER TABLE users_info ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0")

        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)

    asyncio.create_task(smart_radar_watchdog(pool))
    asyncio.create_task(radar_worker_process(pool))
    #asyncio.create_task(daily_channel_post())
    asyncio.create_task(update_market_memory_loop(pool)) # 🔥 تشغيل ذاكرة السوق
    await bot.set_webhook(f"{WEBHOOK_URL}/")


app = web.Application()
app.router.add_post("/", handle_webhook)
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)