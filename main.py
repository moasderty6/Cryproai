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
import httpx
import random
from urllib.parse import urlparse, urlunparse

# 🚀 قائمة الووركرز أو سيرفرات بايننس البديلة
BINANCE_BASES = [
    "https://binance-sain.mo-dahoh.workers.dev",
    "https://binance.mor-aghyad6.workers.dev",
    "https://binani.gsmyr800.workers.dev",
    "https://holy-king-a16d.moooody-18-18-18.workers.dev",
    "https://old-recipe-c34d.m-aldahooh.workers.dev",
    "https://steep-art-7164.dahoohh1.workers.dev",
    "https://lucky-base-6c70.abedelqader02.workers.dev"
]
# 1. نحفظ النسخة الأصلية والأساسية من دالة get الخاصة بمكتبة httpx
_original_httpx_get = httpx.AsyncClient.get

# 2. نصنع دالة "الفلتر الذكي"
async def _patched_binance_get(self, url, *args, **kwargs):
    url_str = str(url)
    parsed_url = urlparse(url_str)
    
    # 📝 [اللوغ] طباعة الطلب الأصلي قبل أي تعديل
    # print(f"🔄 [Network] اعتراض طلب GET موجه إلى: {parsed_url.netloc}{parsed_url.path}")

    # 🛑 فلتر الأمان 1: إذا الطلب لـ Bybit، أو منصات أخرى، مشّيه فوراً
    if "binance" not in parsed_url.netloc and "workers.dev" not in parsed_url.netloc:
        print(f"⏩ [Network] تمرير طلب خارجي مباشرة إلى: {parsed_url.netloc}")
        return await _original_httpx_get(self, url, *args, **kwargs)

    # 🟢 المعالجة الذكية لطلبات Binance Spot:
    bases = BINANCE_BASES.copy()
    random.shuffle(bases) # توزيع الحمل العشوائي

    last_response = None
    for base in bases:
        try:
            base_parsed = urlparse(base)
            
            # 🛠️ دمج الرابط الصحيح: نحافظ على المسار (path) والمتغيرات (query) كما هي تماماً!
            new_url = urlunparse((
                base_parsed.scheme,
                base_parsed.netloc,
                parsed_url.path,
                parsed_url.params,
                parsed_url.query,  # 👈 هذا السطر يعيد (symbol=BTCUSDT) ويحل مشكلة 400
                parsed_url.fragment
            ))
            
            print(f"🔀 [Network] جاري محاولة الاتصال عبر: {new_url}")
            print(f"DEBUG URL: {new_url}")
            print(f"DEBUG PARAMS: {kwargs.get('params')}") 
            # إرسال الطلب
            res = await _original_httpx_get(self, new_url, *args, **kwargs)
            
            if res.status_code == 200:
                print(f"✅ [Network] نجاح الاتصال! (الرد: 200) من السيرفر: {base_parsed.netloc}")
                return res
                
            print(f"⚠️ [Network] السيرفر {base_parsed.netloc} رفض الطلب بخطأ {res.status_code}، جاري التبديل...")
            last_response = res
            
        except Exception as e:
            print(f"❌ [Network] خطأ انقطاع اتصال/شبكة في {base_parsed.netloc}: {str(e)}")
            continue
            
    # 🚨 إذا وصلنا هنا، يعني أن جميع الروابط الـ 4 فشلت!
    print(f"🚨 [Network-Critical] فشلت جميع سيرفرات Binance الأساسية في الاستجابة للمسار: {parsed_url.path}")
    
    # 🛡️ جدار حماية أخير لمنع كراش البوت (AttributeError: 'NoneType' has no attribute 'status_code')
    if last_response is None:
        print("🛡️ [Network] إرجاع استجابة وهمية (500) لمنع انهيار الرادار.")
        # نعيد كائن استجابة وهمي يحتوي على كود 500 لكي تتخطاه دوال التحليل لديك بأمان
        return httpx.Response(500, request=httpx.Request("GET", url_str))
        
    return last_response 

# 3. 💉 الحقن السحري: نستبدل دالة get في المكتبة بالدالة الذكية الخاصة بنا
httpx.AsyncClient.get = _patched_binance_get

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
    import math # لضمان عمل الدالة في حال لم تكن مستدعاة في الأعلى
    
    # 🧠 التصحيح الكمي: إزالة قيد التصفير (max 0.0) للسماح باكتشاف "فراغ السيولة"
    # دالة atan تتعامل مع الأرقام السالبة بأمان رياضي تام دون أي أخطاء (Errors)
    # حماية إضافية من الـ NaN في حال تمرير بيانات فاسدة
    try:
        safe_z = float(z_value)
        if math.isnan(safe_z): safe_z = 0.0
    except (ValueError, TypeError):
        safe_z = 0.0
    
    # استخدام arctan لاستيعاب الأرقام المتطرفة جداً بمرونة
    probability = 0.5 + (math.atan(safe_z / tail_weight) / math.pi)
    
    return probability * limit

from aiohttp import web
from dotenv import load_dotenv
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

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
# 🚀 استبدال القائمة المحظورة بالرابط المحمي عبر Cloudflare
# 🚀 توزيع الحمل بين اثنين Workers لتفادي حدود كلاود فلير
# 🚀 توزيع الحمل (Load Balancing) بين 3 سيرفرات لتفادي حدود كلاود فلير (300 ألف طلب يومياً)

def get_random_binance_base():
    return random.choice(BINANCE_BASES)
BINANCE_HEADERS = {"X-MBX-APIKEY": BINANCE_API_KEY}
GATE_API_KEY = "a3f6a57b42f6106011e6890049e57b2e"
GATE_API_SECRET = "1ac18e0a690ce782f6854137908a6b16eb910cf02f5b95fa3c43b670758f79bc"
GATE_BASE = "https://api.gateio.ws/api/v4/spot/candlesticks"
# 🔄 مصفوفة المفاتيح المجانية (API Pool) لضمان عدم توقف المحرك
CRYPTORANK_API_KEYS = [
    "6ba0b029bf0c28de3f3b9fc73b518d21b965498724e32ae31015be0da48b",
    "f39724a2c07164b0e1021801673ef8e0b8b8c1b60878f801372cf6b2df21",
    "f9210c5b3b9e1fbf23ce8f839c35e3ae0c731c3b6193d05ede9013049248",
    "7788730c52567724dab6d175c97f121540fab95557a549f47ea07782e833",
    "94131717582a30a14e0e951139f2a5c3638054e53094af9a48d56d0af197"
]

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
# ====================================================================
# 🐋 THE ELITE INVESTORS CLUB 
# ====================================================================
INVESTOR_IDS = [7146339698, 565965404] # أرقام المستثمرين النخبة
investor_pending_approvals = {} # ذاكرة مؤقتة لانتظار موافقة الأدمن على الانفجارات الكبرى
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
# الذاكرة الاستباقية للمحرك المؤسساتي (مفصولة تماماً عن الرادار)
VANGUARD_MEMORY = {}
from collections import deque
import json
import websockets

# ====================================================================
# 🏦 THE INSTITUTIONAL LOCAL ORDERBOOK (LOB) MEMORY
# ====================================================================
# يخزن حالة الأوردر بوك اللحظية والـ OFI التراكمي لآخر 60 ثانية (600 إطار)
INSTITUTIONAL_LOB = {}

def update_dynamic_ofi(symbol, new_bids, new_asks):
    """
    [Tier-1 Quant] Multi-Level Order Flow Imbalance (ML-OFI)
    يتتبع أول 5 مستويات سعرية محددة بوزن أسّي (Exponential Decay) لامتصاص 
    تلاعب صناع السوق (Flickering/Spoofing) في المستوى الأول، واكتشاف السيولة الملتزمة.
    """
    if symbol not in INSTITUTIONAL_LOB:
        # تهيئة الذاكرة المتقدمة لـ 5 مستويات بدلاً من مستوى واحد
        INSTITUTIONAL_LOB[symbol] = {
            "prev_bids": {}, # {price: volume}
            "prev_asks": {},
            "ofi_window": deque(maxlen=600), 
            "bid_vols": deque(maxlen=60),    
            "ask_vols": deque(maxlen=60),
            # 🛡️ الحفاظ على المتغيرات التوافقية لدالة analyze_orderbook_advanced_manual
            "best_bid": 0.0, "best_bid_vol": 0.0,
            "best_ask": float('inf'), "best_ask_vol": 0.0,
            "prev_mid": 0.0
        }
    
    mem = INSTITUTIONAL_LOB[symbol]
    
    # تحديث المتغيرات الأساسية للحفاظ على استقرار باقي الكود
    curr_best_bid = float(new_bids[0][0]) if new_bids else 0.0
    curr_best_bid_vol = float(new_bids[0][1]) if new_bids else 0.0
    curr_total_bid_vol = sum(float(v) for p, v in new_bids)
    
    curr_best_ask = float(new_asks[0][0]) if new_asks else float('inf')
    curr_best_ask_vol = float(new_asks[0][1]) if new_asks else 0.0
    curr_total_ask_vol = sum(float(v) for p, v in new_asks)
    
    mem["best_bid"] = curr_best_bid
    mem["best_bid_vol"] = curr_best_bid_vol
    mem["best_ask"] = curr_best_ask
    mem["best_ask_vol"] = curr_best_ask_vol
    mem["bid_vols"].append(curr_total_bid_vol)
    mem["ask_vols"].append(curr_total_ask_vol)
    # ==========================================================
    # 🧠 المحرك المؤسساتي: ML-OFI (Price-Grid Anchoring & Boundary Filtration)
    # ==========================================================
    total_ofi = 0.0

    # نوسع العدسة لـ 10 مستويات لامتصاص الصدمات السعرية دون فقدان البيانات
    curr_bids_dict = {float(p): float(v) for p, v in new_bids[:10]}
    curr_asks_dict = {float(p): float(v) for p, v in new_asks[:10]}

    prev_bids_dict = mem.get("prev_bids", {})
    prev_asks_dict = mem.get("prev_asks", {})

    curr_mid = (curr_best_bid + curr_best_ask) / 2.0
    prev_mid = mem.get("prev_mid", curr_mid)
    if prev_mid == 0: prev_mid = curr_mid

    # 🛡️ فلتر حدود الأوردر بوك (Boundary Effect Filter):
    # لمنع وهم "دخول سيولة" ناتج فقط عن تحرك السعر وإدخال مستويات جديدة لعدسة الرؤية
    prev_worst_bid = min(prev_bids_dict.keys()) if prev_bids_dict else 0.0
    prev_worst_ask = max(prev_asks_dict.keys()) if prev_asks_dict else float('inf')

    # 1. معالجة طلبات الشراء (Bids) بدقة السعر المطلق (Price-Tick)
    all_bid_prices = set(curr_bids_dict.keys()).union(set(prev_bids_dict.keys()))
    for price in all_bid_prices:
        # إعدام الضجيج: تجاهل أي سعر ظهر فجأة أسفل أضعف طلب قديم (هذا انزياح للرؤية وليس ضخ سيولة حقيقي)
        if price < prev_worst_bid and price not in prev_bids_dict:
            continue

        curr_v = curr_bids_dict.get(price, 0.0)
        prev_v = prev_bids_dict.get(price, 0.0)

        # الوزن الديناميكي: المستويات القريبة من السعر العادل (Mid-Price) تمتلك تأثيراً أقوى
        distance_pct = abs(curr_mid - price) / curr_mid if curr_mid > 0 else 0
        weight = math.exp(-distance_pct * 1000)

        total_ofi += weight * (curr_v - prev_v)

    # 2. معالجة عروض البيع (Asks)
    all_ask_prices = set(curr_asks_dict.keys()).union(set(prev_asks_dict.keys()))
    for price in all_ask_prices:
        # إعدام الضجيج للعروض: تجاهل الأسعار التي تقع فوق أعلى عرض قديم
        if price > prev_worst_ask and price not in prev_asks_dict:
            continue

        curr_v = curr_asks_dict.get(price, 0.0)
        prev_v = prev_asks_dict.get(price, 0.0)

        distance_pct = abs(price - curr_mid) / curr_mid if curr_mid > 0 else 0
        weight = math.exp(-distance_pct * 1000)

        # العروض علاقتها عكسية: زيادة العرض تضغط السعر للأسفل (OFI سالب)
        total_ofi -= weight * (curr_v - prev_v)

    # 3. إدراج الـ OFI النقي في الذاكرة (تم الاستغناء عن Shift Filter القديم لأن الكود الجديد يمتص الانزياح طبيعياً)
    mem["ofi_window"].append(total_ofi)

    # 4. تحديث الذاكرة للدورة القادمة
    mem["prev_bids"] = curr_bids_dict
    mem["prev_asks"] = curr_asks_dict
    mem["prev_mid"] = curr_mid


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
from sklearn.multioutput import MultiOutputRegressor
import pandas as pd
import numpy as np
import math

# تخزين النموذج في الذاكرة الحية
AI_QUANT_MODEL = None
MIN_TRAINING_SAMPLES = 100 # أقل عدد صفقات مطلوب لتدريب الذكاء الاصطناعي

def train_xgboost_sync(records):
    """
    [Institutional Level] تدريب النموذج ليتوقع 4 أهداف: 
    الجودة، نسبة الانعكاس للدخول، وقت الانفجار، وأقصى صعود (Pump).
    """
    global AI_QUANT_MODEL
    df = pd.DataFrame(records)
    
    df = df.dropna(subset=['trade_quality_score'])
    
    for col in ['max_adverse_excursion', 'max_favorable_excursion']:
        if col in df.columns:
            df[col] = df[col].fillna(0.0)
        else:
            df[col] = 0.0

    def derive_time(row):
        mfe = row.get('max_favorable_excursion', 0)
        if pd.isna(mfe) or mfe <= 0: return 336.0 
        
        thresh = mfe * 0.5
        time_points = [1.0, 4.0, 24.0, 72.0, 168.0, 336.0]
        return_cols = ['ret_1h', 'ret_4h', 'ret_24h', 'ret_3d', 'ret_7d', 'ret_14d']
        
        prev_t, prev_r = 0.0, 0.0 
        
        for t, col in zip(time_points, return_cols):
            r = row.get(col, 0)
            if pd.isna(r): r = prev_r
            if r >= thresh:
                if r == prev_r: return t
                fraction = (thresh - prev_r) / (r - prev_r + 1e-8)
                exact_time = prev_t + (fraction * (t - prev_t))
                return max(0.1, min(exact_time, t))
            prev_t, prev_r = t, r
            
        return 336.0

        
    df['time_to_surge'] = df.apply(derive_time, axis=1)

    X = df[['market_regime', 'sp500_trend_pct', 'sentiment_score', 
            'vol_z_score', 'cvd_to_vol_ratio', 'imbalance_ratio', 
            'ob_skewness', 'whale_dominance_pct', 'adx', 'rsi', 
            'micro_volatility_pct', 'cvd_divergence', 'funding_rate',
            'weekly_liquidity_void', 'macro_z_score_30d', 
            'htf_whale_accumulation', 'days_since_last_expansion']]

    # 🎯 الأهداف الأربعة
    from sklearn.preprocessing import StandardScaler # أضف هذا الاستدعاء في أعلى الملف أو داخل الدالة
# 🎯 تحديد الأهداف الأربعة بصورتها الخام
    Y = df[['trade_quality_score', 'max_adverse_excursion', 'time_to_surge', 'max_favorable_excursion']].copy()
    
    # 🧠 الـ Winsorization الديناميكي: حصر مخرجات السعر والوقت المتطرفة (الذيول السميكة الفاسدة) 
    # عند المئين 99.5 لقتل الأخطاء الناتجة عن الـ Glitches، دون قتل الـ Real Alpha.
    for col in ['max_adverse_excursion', 'time_to_surge', 'max_favorable_excursion']:
        upper_limit = Y[col].quantile(0.995)
        Y[col] = Y[col].clip(upper=upper_limit)
    
    # صياغة القيود الرتيبة (Monotone Constraints) لكل مخرج بشكل مستقل لمنع الـ Overfitting
    # (1 يعني علاقة طردية حتمية مع الميزات، 0 يعني علاقة حرة يكتشفها النموذج بنفسه)
    # وبما أن MultiOutputRegressor يدرب شجرة منفصلة لكل هدف، نمرر القيد المناسب لكل شجرة
    base_model = xgb.XGBRegressor(
        n_estimators=500,          # رفع الكفاءة الاستيعابية لعشرات آلاف الصفقات
        max_depth=6,               # عمق مثالي لامتصاص العلاقات غير الخطية في السلاسل الضخمة
        learning_rate=0.02,        # تقليل الخطوة لمنع التذبذب والهروب أثناء النزول الاشتقاقي
        subsample=0.85, 
        colsample_bytree=0.8, 
        objective='reg:pseudohubererror', # Huber loss هو الأقوى عالمياً للتعامل مع الـ Outliers
        tree_method='hist'
    )
    
    # تدريب محرك المخرجات المتعددة مباشرة على القيم الحقيقية المعالجة
    multi_model = MultiOutputRegressor(base_model)
    multi_model.fit(X, Y) 
    
    global AI_QUANT_MODEL
    # نحفظ النموذج الخام فقط بشكل نقي (Thread-Safe)، تخلصنا من الـ Scaler الملوث كلياً!
    AI_QUANT_MODEL = multi_model
    return True



async def ai_trainer_worker(pool):
    """عامل التدريب: يستيقظ كل 12 ساعة لتطوير عقل البوت"""
    await asyncio.sleep(60) 
    while True:
        try:
            async with pool.acquire() as conn:
                # 🟢 التعديل الجراحي الأهم: جلب أعمدة العوائد والانعكاس من الداتابيز لكي لا يحدث خطأ (KeyError)
                records = await conn.fetch("""
                    SELECT market_regime, sp500_trend_pct, sentiment_score, 
                           vol_z_score, cvd_to_vol_ratio, imbalance_ratio, 
                           ob_skewness, whale_dominance_pct, adx, rsi, 
                           micro_volatility_pct, cvd_divergence, funding_rate,
                           weekly_liquidity_void, macro_z_score_30d, 
                           htf_whale_accumulation, days_since_last_expansion,
                           trade_quality_score,
                           max_adverse_excursion, max_favorable_excursion,
                           ret_1h, ret_4h, ret_24h, ret_3d, ret_7d
                    FROM ml_training_data 
                    WHERE is_processed = 1
                """)
                
                if len(records) >= 100: # 🎯 عتبة الانطلاق (Critical Mass)
                    print(f"🧠 [AI Trainer] Mass training on {len(records)} samples...")
                    records_dict = [dict(r) for r in records]
                    await asyncio.to_thread(train_xgboost_sync, records_dict)
                    print("✅ [AI Trainer] Engine Optimized to Hedge Fund Level (Multi-Target).")
                else:
                    print(f"⏳ [AI Trainer] Collecting data... ({len(records)}/100)")
                    
        except Exception as e:
            print(f"⚠️ AI Trainer Error: {e}")
        await asyncio.sleep(43200) # 12 ساعة

def predict_signal_sync(features: dict):
    """يتوقع 4 قيم: جودة، هبوط، وقت، أقصى صعود"""
    if AI_QUANT_MODEL is None:
        return -1.0, 0.0, 0.0, 0.0 
        
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
        'funding_rate': float(features.get('funding_rate', 0.0)),
        'weekly_liquidity_void': float(features.get('weekly_liquidity_void', 0.0)),
        'macro_z_score_30d': float(features.get('macro_z_score_30d', 0.0)),
        'htf_whale_accumulation': float(features.get('htf_whale_accumulation', 0.0)),
        'days_since_last_expansion': float(features.get('days_since_last_expansion', 0.0))
    }])
    if AI_QUANT_MODEL is None:
        return -1.0, 0.0, 0.0, 0.0 
    
    # دالة حماية إضافية لمدخلات الـ Live Features قبل التنبؤ لضمان عدم تمرير NaN أو أرقام فلكية من الـ API
    input_data = input_data.fillna(0.0)
    
    # التنبؤ المباشر بالقيمة الحقيقية دون أي عمليات تحويل مشوهة للبيانات
    prediction = AI_QUANT_MODEL.predict(input_data)[0]
    
    # استخراج المخرجات الأربعة بدقتها المطلقة مباشرة من عقل الشجرة
    predicted_quality = float(prediction[0])
    
    # إذا كانت الجودة مسجلة في قاعدة بياناتك كـ Trade Quality Score (بين -1 و +1)
    # نحولها لمعيار مئوي نقي ومحمي من الخروج عن الحدود (Bounded 0-100)
    confidence_pct = max(0.0, min(100.0, ((predicted_quality + 1.0) / 2.0) * 100.0))
    confidence_pct = round(confidence_pct, 1)
    
    # استخراج النسب المئوية والساعات الحقيقية مباشرة (Clean, Non-linear Splits)
    entry_drop_pct = max(0.0, float(prediction[1]))        # لا يمكن أن يكون الارتداد المتوقع سالباً
    time_to_surge_hours = max(0.1, float(prediction[2]))   # لا يمكن أن يكون الزمن سالباً أو صفراً
    expected_pump_pct = max(0.0, float(prediction[3]))     # لا يمكن أن يكون الصعود سالباً
    
    return float(confidence_pct), entry_drop_pct, time_to_surge_hours, expected_pump_pct
    
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
                        if not clean_sym.isalnum(): continue
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
    عامل القنص اللحظي (Live AI Executioner):
    يستلم العملات من الرادار اللحظي فور دخول سيولة الحيتان، ويقيّمها بالـ AI في نفس الثانية.
    """
    sem = asyncio.Semaphore(5) 
    await asyncio.sleep(10)
    print("👷‍♂️ [Live AI Sniper] جاهز لاصطياد السيولة اللحظية والانفجارات...")
    
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                # 1. المستشعر التقط سيولة حية ورماها هنا
                coin_mock_data = await radar_processing_queue.get()
                clean_sym = coin_mock_data['symbol']
                
                async with pool.acquire() as conn:
                    # نمنع تكرار الإرسال المزعج لنفس العملة خلال 24 ساعة
                    is_signaled = await conn.fetchval("""
                        SELECT 1 FROM radar_history 
                        WHERE symbol = $1 AND last_signaled > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                    """, clean_sym)

                if not is_signaled:
                    await binance_rate_limit_event.wait()
                    market_regime = await detect_market_regime(client)

                    # 2. تشغيل التحليل العميق فوراً
                    meta = await analyze_radar_coin(coin_mock_data, client, market_regime, sem)
                    
                    if meta:
                        ai_confidence = meta.get('ai_raw_score', -1.0)
                        
                        # 🎯 The Apex Trigger اللحظي: إذا كان تقييم الـ AI الخفي 80% فما فوق
                        if ai_confidence >= 80.0:
                            
                            # تسجيل العملة لمنع التكرار
                            async with pool.acquire() as conn:
                                await conn.execute("""
                                    INSERT INTO radar_history (symbol, last_signaled)
                                    VALUES ($1, CURRENT_TIMESTAMP)
                                    ON CONFLICT (symbol) DO UPDATE SET last_signaled = CURRENT_TIMESTAMP
                                """, clean_sym)

                            price = meta['price']
                            z_val = float(meta.get('macd', 0.0))
                            vol_ratio = float(meta.get('vol_ratio', 1.0))
                            cvd_val = float(meta.get('cvd_usd', 0.0))
                            ob_val = float(meta.get('ob_pressure', 1.0))
                            adx = float(meta.get('adx', 0.0))
                            rsi = float(meta.get('rsi', 0.0))

                            ml = meta.get('ml_features', {})
                            funding = float(ml.get('funding_rate', 0.0))
                            confluence = int(meta.get('confluence', 0))

                            # صياغة التحليل
                            vol_ar = f"شذوذ فوليوم (Z-Score: {z_val:.2f}) مع ضخ سيولة حاد ({vol_ratio:.2f}x)."
                            cvd_ar = f"امتصاص شرائي خفي (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"ضغط بيعي وتصريف (CVD: ${cvd_val:,.0f})"
                            ob_ar = f"مع تكدس طلبات هجومي (OB: {ob_val:.2f}x)." if ob_val > 1 else f"سيطرة عروض بيع (OB: {ob_val:.2f}x)."
                            fund_ar = "تمركز بيعي (خطر Short Squeeze)." if funding < -0.0005 else "استقرار معدلات التمويل."
                            tech_ar = f"إجماع فني ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"

                            insight_ar = (
                                f"• <b>السيولة:</b> {vol_ar}\n"
                                f"• <b>التدفق:</b> {cvd_ar} {ob_ar}\n"
                                f"• <b>المشتقات:</b> {fund_ar}\n"
                                f"• <b>الهيكلة:</b> {tech_ar}"
                            )

                            # صياغة الإنجليزي للحفاظ على التوافق مع المستخدمين الأجانب
                            insight_en = (
                                f"• <b>Liquidity:</b> Volume Anomaly (Z-Score: {z_val:.2f}), Inflow ({vol_ratio:.2f}x).\n"
                                f"• <b>Flow:</b> Buy Absorption (CVD: +${cvd_val:,.0f}), Orderbook Bid Pressure ({ob_val:.2f}x).\n"
                                f"• <b>Derivatives:</b> {fund_ar.replace('تمركز بيعي', 'Short bias').replace('استقرار', 'Stable')}\n"
                                f"• <b>Structure:</b> Confluence ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"
                            )

                            signal_id = str(uuid.uuid4())[:8]
                            radar_pending_approvals[signal_id] = {
                                "symbol": clean_sym, "price": price, "signal": "⚡ LIVE AI APEX Pick", "score": ai_confidence,
                                "insight_ar": insight_ar, "insight_en": insight_en
                            }

                            admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="✅ موافقة ونشر لحظي", callback_data=f"rad_app_{signal_id}")],
                                [InlineKeyboardButton(text="❌ تجاهل", callback_data=f"rad_rej_{signal_id}")]
                            ])

                            admin_text = (
                                f"⚡ <b>انفجار لحظي: قناص الذكاء الاصطناعي التقط سيولة حية للتو!</b>\n"
                                f"🏆 <b>العملة:</b> #{clean_sym}\n"
                                f"💵 السعر: ${format_price(price)}\n"
                                f"🤖 تقييم الذكاء الاصطناعي: <b>{ai_confidence:.1f}%</b>\n\n"
                                f"📝 <b>التحليل:</b>\n{insight_ar}\n\n"
                                f"هل تريد الموافقة؟"
                            )
                            
                            await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                            print(f"🚀 [Live Sniper] Caught {clean_sym} exactly as the whale entered with AI Score {ai_confidence:.1f}%!")

                # إخبار الطابور بانتهاء المهمة
                radar_processing_queue.task_done()
                await asyncio.sleep(1) # استراحة ثانية لحماية الـ API
                
            except Exception as e:
                print(f"⚠️ خطأ عابر في Live Sniper Worker: {e}")
                await asyncio.sleep(2)
# طابور مستقل لمعالجة الشورت
short_processing_queue = asyncio.Queue()

async def apex_short_watchdog(pool):
    """
    مستشعر ذروة الفومو (Euphoria Producer): 
    يصطاد اللحظة التي يضخ فيها الأفراد سيولة جنونية لكن السعر يتوقف عن الصعود (تفريغ الحيتان).
    """
    url = "wss://stream.binance.com:9443/ws/!miniTicker@arr"
    print("🔴 [Short Engine] Euphoria Watchdog is Online. Hunting market tops...")

    short_market_memory = {}

    while True:
        current_time = time.time()
        keys_to_delete = [k for k, v in short_market_memory.items() if current_time - v['last_update'] > 3600]
        for k in keys_to_delete: del short_market_memory[k]

        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                async for message in ws:
                    data = json.loads(message)
                    current_time = time.time()

                    for ticker in data:
                        symbol = ticker['s']
                        if not symbol.endswith("USDT"): continue

                        clean_sym = symbol.replace("USDT", "")
                        if not clean_sym.isalnum(): continue
                        if clean_sym in BLACKLISTED_COINS: continue
                        

                        current_vol = float(ticker['q']) 
                        current_price = float(ticker['c'])

                        if symbol in short_market_memory:
                            old_data = short_market_memory[symbol]
                            old_vol = old_data['volume']
                            old_price = old_data['price']
                            time_diff = current_time - old_data['last_update']

                            # مراقبة الدقيقة الواحدة
                            if time_diff >= 60 and old_vol > 0:
                                traded_usd_minute = current_vol - old_vol 
                                price_change = (current_price - old_price) / old_price
                                
                                # 🧠 شروط قمة التصريف (Distribution Top):
                                # 1. سيولة ضخمة دخلت فجأة (أكثر من 50 ألف دولار في دقيقة)
                                # 2. السعر إما صعد بحدة شديدة (FOMO Spike > 1.5%)
                                # 3. أو السعر لم يتحرك رغم السيولة الضخمة (Limit Sell Wall Absorption)
                                MIN_EXHAUSTION_VOL = max(50_000.0, old_vol * 0.003)

                                if traded_usd_minute >= MIN_EXHAUSTION_VOL and (price_change >= 0.015 or (price_change <= 0.002 and traded_usd_minute > MIN_EXHAUSTION_VOL * 2)):
                                    print(f"🩸 [Euphoria Alert] {symbol} | Injected: ${traded_usd_minute:,.0f} | Price Shift: {price_change*100:.2f}%") 
                                    
                                    await short_processing_queue.put({"symbol": clean_sym, "quote": {"USD": {"price": current_price}}})
                                
                                short_market_memory[symbol] = {'volume': current_vol, 'price': current_price, 'last_update': current_time}
                        else:
                            short_market_memory[symbol] = {'volume': current_vol, 'price': current_price, 'last_update': current_time}

        except Exception as e:
            print(f"⚠️ Short Watchdog Error: {e} - Reconnecting...")
            await asyncio.sleep(3)
async def analyze_short_radar_coin(c, client, market_regime, sem):
    """
    [Institutional Grade Short Radar]
    النسخة الهجومية المعكوسة من رادار التحليل. تعمل بنفس القوة والتعقيد لكن باحثة عن فخاخ السيولة الشرائية.
    """
    async with sem:  
        try:
            symbol = c["symbol"]
            price = float(c["quote"]["USD"]["price"])
            
            candles = await get_candles_binance(f"{symbol}USDT", "1h", limit=750)
            if not candles: return None

            df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)
            tags = []
            
            # 🛡️ 1. فلتر قوة الاندفاع (Exhaustion Filter)
            current_vwap_z, current_vwap_price = calculate_vwap_zscore(df, window=24)
            current_high, current_low = df["high"].iloc[-1], df["low"].iloc[-1]
            candle_spread_pct = ((current_high - current_low) / current_low) * 100
            
            # إذا كان السعر تحت الـ VWAP، لا يوجد شورت (لا تشورت عملة تنزف أصلاً، نحن نبحث عن قمم)
            if current_vwap_z < 1.0: 
                return None
                
            # 🟢 2. جلب كل البيانات الميكرو-سوقية (Data Fetching)
            spot_lead_score = await detect_spot_perp_divergence(symbol, client)
            old_price_val = df["close"].iloc[-3] if len(df) > 3 else df["open"].iloc[0]
            approx_24h_vol_usd = df["volume"].tail(24).sum() * price 

            # جلب اللحظي
            micro_cvd_boost, micro_cvd_signal, micro_cvd_trend = await get_micro_cvd_absorption(f"{symbol}USDT", client, "1h")
            global_ob_pressure = await get_aggregated_orderbook(client, symbol)
            depth_data = await analyze_orderbook_spoofing_instant(symbol, client, price)
            tick_delta, tick_buy, tick_sell, limit_abs_signal = await get_institutional_orderflow(f"{symbol}USDT", client)
            _, futures_signal, funding_val, oi_change_pct, _ = await get_futures_liquidity(symbol, client, price, old_price_val)
            whale_score, phantom_tags = await detect_phantom_liquidity_ws(symbol, client, price, approx_24h_vol_usd)
            rs_score = await detect_btc_relative_strength(symbol, client)
            vpin_score = await get_institutional_vpin(symbol, client, approx_24h_vol_usd)
            
            tags.extend(phantom_tags)

            # 🧠 3. جلب بيانات الماكرو والـ MTFA (Timeframe Alignment)
            candles_1d_macro = await get_candles_binance(f"{symbol}USDT", "1d", limit=40)
            candles_4h = await get_candles_binance(f"{symbol}USDT", "4h", limit=100)
            candles_1w_simulated = [] # (اختصاراً هنا نضع كود تجميع الشموع الأسبوعية الخاص بك كما هو في الدالة الأصلية)
            # ... (كود تجميع df_daily للشموع الأسبوعية) ...

            mtfa_context = await asyncio.to_thread(calculate_mtfa_context_sync, candles_4h, candles_1d_macro, candles_1w_simulated)
            
            # ⚙️ 4. الهيكلة الفنية والبولينجر
            dyn_window = get_dynamic_window(df, base_window=20)
            sma = df["close"].rolling(dyn_window).mean()
            std = df["close"].rolling(dyn_window).std(ddof=0)
            bb_width = (4 * std) / sma
            avg_bb_width = bb_width.rolling(dyn_window * 5).mean().iloc[-1]
            current_bb_width = bb_width.iloc[-1]
            squeeze_ratio = current_bb_width / (avg_bb_width + 1e-8) if not pd.isna(avg_bb_width) and avg_bb_width > 0 else 1.0
            # ====================================================================
            # 🩸 5. محرك التسعير الكمي الهجومي (The Inverse Quant Scoring & Alpha Fusion)
            # ====================================================================
            df["vol_usd"] = df["volume"] * df["close"]
            avg_vol_usd_20 = max(df["vol_usd"].tail(20).mean(), 1.0)
            
            # أ. تقييم السيولة (CVD) للشورت:
            # نبحث عن CVD إيجابي جداً يقابله ضعف في حركة السعر (تفريغ المؤسسات على الأفراد)
            real_cvd_usd_eval = float(micro_cvd_trend) * price 
            cvd_ratio = real_cvd_usd_eval / avg_vol_usd_20
            
            # كلما زاد التفاعل الشرائي اللحظي للأفراد (CVD عالي)، زادت جودة الشورت!
            dir_cvd = quant_sigmoid_score(cvd_ratio, sensitivity=6.0, limit=100.0) 
            # ب. تقييم المشتقات، الانحراف، وإنهاك السيولة (Derivatives, Spot Divergence & OI Exhaustion):
            
            # 1. حساسية معدل التمويل (Funding Sensitivity - لسيناريو مصيدة المشترين)
            funding_sensitivity = 3000.0 if (futures_signal == "OI_Rising" and oi_change_pct > 0.02) else 1000.0
            base_deriv = quant_sigmoid_score(funding_val, sensitivity=funding_sensitivity, limit=100.0)
            
            # 2. استعادة الألفا المهدرة (Spot Divergence Bonus - لسيناريو تفريغ السبوت):
            spot_divergence_bonus = min(20.0, abs(spot_lead_score) * 2.0) if spot_lead_score < 0 else 0.0
            
            # 3. 🧠 محرك إنهاك السيولة المفتوحة (OI Exhaustion Engine - لسيناريو الهروب الصامت):
            # القمة الحقيقية تتشكل عندما يبدأ الـ OI بالنزيف بينما الأفراد لا زالوا يطمعون (Funding إيجابي)
            oi_exhaustion_bonus = 0.0
            if oi_change_pct < -0.005 and funding_val > 0.0001:
                # سحب مؤسساتي صامت للسيولة: نكافئ الشورت تصاعدياً بناءً على حجم انسحاب الحيتان
                # نضيف 10 نقاط كحد أدنى، وتزيد مع قوة النزيف (بحد أقصى 25 نقطة)
                oi_exhaustion_bonus = min(25.0, 10.0 + (abs(oi_change_pct) * 500.0))
                
            # ⚖️ الدمج المؤسساتي: جمع كافة محفزات المشتقات لتكوين القناعة النهائية (Capped at 100)
            dir_deriv = min(100.0, base_deriv + spot_divergence_bonus + oi_exhaustion_bonus)

            # ج. تقييم الأوردر بوك اللحظي:
            # نبحث عن ضغط بيعي في الأوردر بوك (Imbalance سالب) وفراغ في طلبات الشراء
            imbalance = depth_data.get('imbalance', 0.0)
            # كلما قل Imbalance (اتجه للسالب)، زاد سكور الشورت
            ob_base = 100.0 - quant_sigmoid_score(imbalance, sensitivity=4.0, limit=100.0)
            timing_score = ob_base
            
            if depth_data.get('is_hollow', False): timing_score *= 1.4 # مكافأة: لا يوجد قاع يحمي السعر!
            
            # د. دمج الاتجاه المؤسساتي (Inverse Fusion)
            base_short_conviction = (dir_cvd * 0.40) + (dir_deriv * 0.40) + (timing_score * 0.20)
            
            # 🧠 استعادة الألفا المهدرة 2 (VPIN Toxicity Multiplier):
            # نقيس "سمية السيولة". إذا كانت السيولة سامة جداً (VPIN > 0.5)، نضاعف ثقة الشورت
            # لأن من يصرف الكميات هم الحيتان (Informed Traders) وليس صغار المتداولين.
            vpin_multiplier = 1.0 + (max(0.0, vpin_score - 0.5) * 0.60) # يضيف حتى 30% قوة إضافية للفرصة
            
            # القناعة النهائية الهجومية
            short_conviction = min(100.0, base_short_conviction * vpin_multiplier)

            # ====================================================================
            # 🛑 الفيتو الإجباري الديناميكي للشورت (Dynamic Institutional Vetoes)
            # ====================================================================
            # 1. فيتو اكتشاف السعر (Price Discovery Veto):
            # لا تشورت عملة أقوى من البيتكوين (RS عالي) وتتلقى سيولة مؤسساتية هجومية (VPIN سام)
            if rs_score > 8.0 and vpin_score > 0.7: 
                return None # العملة أقوى من البيتكوين والسيولة سامة للشراء، اهرب!
            
            # 2. فيتو مصيدة الشورت (Smart Short Squeeze Veto):
            # بدلاً من الرقم الثابت الساذج (-0.001)، نعتمد على الانحراف المعياري وسياق المراكز المفتوحة (OI).
            
            # أ. الإشارة الإحصائية: هل أبلغنا المحرك عن انحراف معياري خطير (Z-Score < -1.5)؟
            is_statistical_squeeze = (futures_signal == "Short_Squeeze")
            
            # ب. فخ التكدس (Trapped Shorts): إذا كان الـ OI يرتفع بقوة بينما التمويل يميل للسلبية
            # فهذا يعني أن الأفراد يفتحون شورتات بهلع، وصانع السوق يجهز لضربهم صعوداً.
            is_trapped_shorts = (futures_signal == "OI_Rising" and funding_val < -0.0004)
            
            # ج. الجدار المطلق (Absolute Hard Floor): للحماية من الانهيارات التاريخية المفتوحة
            is_extreme_funding = (funding_val < -0.0020) # عتبة واسعة جداً (-2% يومياً) تُستخدم فقط كصمام أمان أخير
            
            if is_statistical_squeeze or is_trapped_shorts or is_extreme_funding:
                return None 
# ✅ الكود الجديد (الاندماج مع الماكرو):
# 1. سيولة الماكرو الكلية: هل البيتكوين يمتص السيولة؟ (Altcoin Bleed)
            alt_regime = MACRO_CACHE.get("alt_regime_score", 50.0)
            macro_tailwinds = 1.0
            
            if alt_regime < 45.0:
                # البيتكوين ينزف البديلة، بيئة ممتازة للشورت
                macro_tailwinds += 0.15 
            elif alt_regime > 60.0:
                # موسم عملات بديلة! خطر جداً عمل شورت، نعاقب السكور
                macro_tailwinds -= 0.20
            
            # 2. صحة التمويل الكلي (Global Over-leverage)
            global_funding = MACRO_CACHE.get("global_funding_health", 50.0)
            if global_funding > 75.0: 
                # طمع شديد في السوق بالكامل = اقتراب الانهيار
                macro_tailwinds += 0.10
            
            final_raw_score = short_conviction * macro_tailwinds
            
            # 🛑 الفيتو المزدوج للشورت:
            score = round(max(0.0, min(final_raw_score, 99.5)), 1)
            if score < 75.0: return None 
            # ====================================================================
            # 🤖 6. محرك الانعكاس الاصطناعي للذكاء الاصطناعي (Synthetic Feature Inversion - SFI)
            # ====================================================================
            # بدلاً من استنتاج الشورت عبر حساب نسبة الخطأ (Drop) لنماذج الشراء،
            # سنقوم بقلب كافة المتغيرات الاتجاهية رياضياً لتكوين "عالم مرايا".
            # إذا أعطانا النموذج ثقة شراء 90% في عالم المرايا، فهذا يعني ثقة شورت 90% في العالم الحقيقي!
            
            # 🧠 دمج المتغيرات المهدرة (VPIN & Spot Divergence) لتغذية الشورت قبل الإرسال للـ AI
            enhanced_cvd_ratio = float((tick_delta / avg_vol_usd_20) * 100)
            if spot_lead_score < -5.0: enhanced_cvd_ratio -= 15.0 # السبوت يفرّغ والفيوتشرز عالق

            whale_ratio = await get_whale_inflow_score()
            
            inverse_ml_features = {
                'market_regime': 2 if int(MACRO_CACHE.get("market_regime", 0)) == 1 else (1 if int(MACRO_CACHE.get("market_regime", 0)) == 2 else 0), 
                'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)) * -1.0, 
                'sentiment_score': 100.0 - float(MACRO_CACHE.get("sentiment_score", 50.0)),
                
                # 📊 الفوليوم يظل موجباً لأننا نحتاج سيولة عالية وتذبذب للانفجار سواء كان صعوداً أو هبوطاً
                'z_score': float(current_z), 
                
                # 🔄 قلب التدفق: تفريغ السبوت (-10%) سيراه النموذج كشراء مؤسساتي (+10%)
                'cvd_to_vol_ratio': enhanced_cvd_ratio * -1.0, 
                
                # 🔄 قلب الأوردر بوك: العروض الكثيفة تصبح طلبات كثيفة
                'ofi_imbalance': float(imbalance) * -1.0, 
                'ob_skewness': 1.0 / max(float(depth_data.get('bid_pressure_ratio', 1.0)), 1e-4), 
                
                # 🔄 قلب تدفق الحيتان (Long/Short Ratio)
                'whale_inflow': 1.0 / max(whale_ratio, 1e-4), 
                
                'adx': float(current_adx), # زخم الترند يظل موجباً لأنه يقيس القوة وليس الاتجاه
                
                # 🔄 قلب الـ RSI: ذروة الشراء 80 تصبح ذروة بيع 20 ليقتنصها النموذج كفرصة ممتازة!
                'rsi': 100.0 - float(last_rsi), 
                
                'micro_volatility': float(df['close'].tail(20).pct_change().std() * 100),
                'cvd_divergence': float(MACRO_CACHE.get("cvd_divergence", 0.0)) * -1.0, 
                
                # 🔄 قلب الفاندنج: فاندنج إيجابي عالي (طمع) يصبح سلبياً جداً ليحاكي Short Squeeze في عقل النموذج
                'funding_rate': float(funding_val) * -1.0, 
                
                'weekly_liquidity_void': float(MACRO_CACHE.get("weekly_liquidity_void", 0.0)) * -1.0,
                'macro_z_score_30d': float(MACRO_CACHE.get("macro_z_score_30d", 0.0)),
                'htf_whale_accumulation': float(MACRO_CACHE.get("htf_whale_accumulation", 0.0)) * -1.0,
                'days_since_last_expansion': float(MACRO_CACHE.get("days_since_last_expansion", 0.0))
            }
            
            # 🚀 استدعاء النماذج وتمرير "البيانات المعكوسة"
            ai_conf_short_xgb, xgb_synthetic_drop, xgb_time, xgb_synthetic_pump = await asyncio.to_thread(predict_signal_sync, inverse_ml_features)
            ai_conf_short_deep, deep_synthetic_drop, deep_time, deep_synthetic_pump = await asyncio.to_thread(predict_deep_moe, inverse_ml_features)

            # 🧠 فك التشفير العكسي (Inverse Translation)
            # بما أننا عكسنا المتغيرات، فإن الصعود المتوقع (Pump) في عقل النموذج، هو في الحقيقة الانهيار السعري (Drop)!
            # والـ Drop في عقل النموذج هو الارتداد المعاكس (Risk) الذي سيضرب وقف الخسارة.
            best_short_profit = max(xgb_synthetic_pump, deep_synthetic_pump) 
            worst_short_risk = max(xgb_synthetic_drop, deep_synthetic_drop) 
            
            # 🛑 الفيتو الإلزامي: إذا كان النموذج (في العالم المعكوس) يرى خطراً كبيراً للانهيار
            # فهذا يعني أن السعر سيرتد صعوداً ويضرب الشورت الخاص بنا.
            if worst_short_risk > 10.0: return None
            
            true_short_ai_conf = max(ai_conf_short_xgb, ai_conf_short_deep)

            # ====================================================================
            # 🩸 تعزيز قناعة الشورت بناءً على محفزات اقتصاديات التوكن (Tokenomics Exit-Liquidity)
            # تم دمجها هنا لتعزيز التقييم النهائي قبل إخراجه
            # ====================================================================
            tokenomics_risk = await evaluate_tokenomics_overhang(symbol, client)
            
            if tokenomics_risk['is_vetoed']:
                # إذا كان هناك فك توكنات ضخم قادم، نعتبره إشارة قنص ممتازة لتفريغ المؤسسات!
                true_short_ai_conf = min(99.9, true_short_ai_conf * 1.30)
                score = min(99.9, score * 1.30)
                tags.append("Tokenomics_Exit_Liquidity")
            elif tokenomics_risk['penalty_multiplier'] < 1.0:
                # تضخم عالي (Toxic FDV) يعطي دفعة للشورت
                true_short_ai_conf = min(99.9, true_short_ai_conf * 1.15)
                score = min(99.9, score * 1.15)

# ✅ الكود الجديد (الاستهداف المغناطيسي للتصفيات):
            conf_sl, conf_tp1, conf_tp2, conf_tp3 = await asyncio.to_thread(
                calculate_institutional_vpvr_confluence, candles_4h, candles_1d_macro, price, "Bearish"
            )
            
            # 🩸 جلب خريطة التصفيات اللحظية
            liq_data = await build_liquidation_heatmap(symbol, client)
            
            sl = conf_sl if conf_sl else price * (1 + (worst_short_risk / 100))
            tp1 = conf_tp1 if conf_tp1 else price * (1 - 0.02)
            tp2 = conf_tp2 if conf_tp2 else price * (1 - (best_short_profit / 100))
            
            # 🧠 الاندماج المغناطيسي (Heatmap Fusion):
            # إذا اكتشفنا أن هناك Longs محاصرين، نستخدم نقطة تصفيتهم كهدف أول أو ثاني مؤكد!
            if liq_data and "Long Liquidation" in liq_data['type']:
                # إذا كانت التصفية قريبة نضعها كهدف أول، وإذا كانت بعيدة نضعها كهدف ثاني
                if liq_data['distance_pct'] <= 0.03:
                    tp1 = liq_data['target']
                else:
                    tp2 = liq_data['target']
                    tp1 = price * (1 - (liq_data['distance_pct'] * 0.5)) # هدف أولي في منتصف الطريق
                    
            tp3 = conf_tp3 if conf_tp3 else tp2 * 0.95
            
            return {
                "symbol": symbol, "price": price, "score": score,
                "rsi": round(last_rsi, 2), "adx": round(current_adx, 2),
                "macd": current_z, "vol_ratio": float((df["volume"].iloc[-1] / vol_mean) if vol_mean > 0 else 1.0),
                "ob_pressure": float(depth_data.get('bid_pressure_ratio', 1.0)),
                "signal_type": "🩸 قمة تصريف (Short / Exit)",
                "confluence": 5, "ml_features": ml_features, 
                "cvd_usd": float(real_cvd_usd_eval),
                "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "ai_conf": true_short_ai_conf # ✅ التعديل الصحيح
            }

        except Exception as e:
            print(f"Error in analyze_short_radar_coin: {e}")
            return None  

async def short_radar_worker_process(pool):
    sem = asyncio.Semaphore(5) 
    await asyncio.sleep(15)
    print("🩸 [Live Short Sniper] Ready for execution...")
    
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                coin_mock_data = await short_processing_queue.get()
                clean_sym = coin_mock_data['symbol']
                
                async with pool.acquire() as conn:
                    is_signaled = await conn.fetchval("SELECT 1 FROM radar_history WHERE symbol = $1 AND last_signaled > CURRENT_TIMESTAMP - INTERVAL '12 hours'", f"{clean_sym}_SHORT")

                if not is_signaled:
                    await binance_rate_limit_event.wait()
                    market_regime = await detect_market_regime(client)
                    meta = await analyze_short_radar_coin(coin_mock_data, client, market_regime, sem)
                    
                    if meta and meta['score'] >= 80.0:
                        async with pool.acquire() as conn:
                            await conn.execute("INSERT INTO radar_history (symbol, last_signaled) VALUES ($1, CURRENT_TIMESTAMP) ON CONFLICT (symbol) DO UPDATE SET last_signaled = CURRENT_TIMESTAMP", f"{clean_sym}_SHORT")

                        price = meta['price']
                        insight_ar = (
                            f"🩸 <b>إشارة بيع مكشوف (SHORT) / جني أرباح</b> 🩸\n"
                            f"🧲 <b>منطقة الدخول:</b> <code>{format_price(price)}$</code>\n"
                            f"🎯 <b>أهداف الهبوط:</b> <code>{format_price(meta['tp1'])}$</code> - <code>{format_price(meta['tp2'])}$</code>\n"
                            f"🛑 <b>وقف الخسارة:</b> <code>{format_price(meta['sl'])}$</code>\n"
                            f"• <b>التدفق:</b> تصريف مؤسساتي مخفي، ومخاطر Long Squeeze عالية.\n"
                            f"• <b>الذكاء الاصطناعي:</b> ثقة {meta['ai_conf']:.1f}% بالانهيار السعري."
                        )

                        signal_id = str(uuid.uuid4())[:8]
                        radar_pending_approvals[f"sh_{signal_id}"] = {
                            "symbol": clean_sym, "price": price, "signal": meta['signal_type'], "score": meta['score'],
                            "insight_ar": insight_ar, "insight_en": insight_ar.replace("إشارة بيع مكشوف", "SHORT Signal").replace("أهداف الهبوط", "Drop Targets").replace("وقف الخسارة", "Stop Loss")
                        }

                        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="✅ نشر إشارة (Short)", callback_data=f"rad_app_sh_{signal_id}")],
                            [InlineKeyboardButton(text="❌ تجاهل", callback_data=f"rad_rej_sh_{signal_id}")]
                        ])

                        admin_text = f"🩸 <b>تنبيه شورت: قمة تفريغ مكتشفة!</b>\n🏆 <b>العملة:</b> #{clean_sym}\n💵 السعر: ${format_price(price)}\n📊 السكور: <b>{meta['score']:.1f}/100</b>\n\n📝 <b>التحليل:</b>\n{insight_ar}\n\nنشر الإشارة؟"
                        
                        await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                        
                short_processing_queue.task_done()
                await asyncio.sleep(1)
            except Exception as e:
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
            
            price_change = (df["c"].iloc[-1] - df["c"].iloc[0]) / (df["c"].iloc[0] + 1e-8)
            cvd_trend = df["cvd"].iloc[-1] - df["cvd"].iloc[0]
            total_vol = df["v"].sum()
            
            # 🧠 المحرك الكمي: الجهد مقابل النتيجة (Effort vs. Result / Institutional Absorption)
            # CVD يقيس أوامر الماركت (Takers - عادةً الأفراد أو خوارزميات الزخم). 
            # السعر يقيس أوامر الليمت (Makers - الحيتان وصناع السوق).
            
            delta_pct = cvd_trend / (total_vol + 1e-8)
            
            # 1. الامتصاص الشرائي الحقيقي (True Limit Buy Absorption / Bottom Squeezing)
            # الأفراد يبيعون بهلع ماركت (CVD سلبي)، لكن السعر يرفض الهبوط (امتصاص مؤسساتي بطلبات ليمت).
            if delta_pct < -0.06 and price_change >= -0.005:
                return 30.0, "Micro_Silent_Accumulation", cvd_trend 

            # 2. الامتصاص البيعي الخفي (True Limit Sell Distribution / Top Trapping)
            # الأفراد يشترون بفومو ماركت (CVD إيجابي)، لكن السعر يرفض الصعود (تفريغ مؤسساتي بعروض ليمت).
            elif delta_pct > 0.06 and price_change <= 0.005:
                return -30.0, "Hidden_Distribution", cvd_trend 
                
            # 3. كفاءة السيولة (High Efficiency Mark-up)
            # سيولة ماركت خفيفة تنجح في رفع السعر بقوة (دليل على انعدام عروض البيع وجفاف العرض).
            elif delta_pct > 0.02 and price_change > 0.03:
                return 15.0, "Efficient_Markup", cvd_trend
            
            # 🟢 إرجاع قيمة الـ CVD الحقيقية للذكاء الاصطناعي للتحليل العميق
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

    # 🧠 التصحيح المؤسساتي (Microstructure Fix):
    MIN_WHALE_TRADE_USD = 50.0 
    
    # --- دالة فرعية 1: بايننس ---    # --- دالة فرعية 1: بايننس (محدثة كمياً - Smart Money & Algo Detection) ---
    async def fetch_binance():
        try:
            base_url = get_random_binance_base()
            res = await client.get(f"{base_url}/api/v3/aggTrades", params={
                "symbol": sym_binance, "startTime": start_time, "endTime": end_time, "limit": 1000 
            }, timeout=4.0)
            
            if res.status_code == 200:
                trades = res.json()
                if not trades: return 0.0, 0.0, []
                
                import numpy as np
                trade_values = np.array([float(t['p']) * float(t['q']) for t in trades])
                
                # 🧠 1. عزل السيولة (Volume Stratification):
                # حساب العتبة الديناميكية لـ "الحيتان" (أعلى 15% من حجم الصفقات في هذه العينة)
                dynamic_whale_threshold = np.percentile(trade_values, 85)
                # نضع حداً أدنى 2000 دولار لكي لا نلتقط الأفراد في العملات الميتة
                min_smart_money = max(2000.0, dynamic_whale_threshold)

                b_vol, s_vol = 0.0, 0.0
                prices = []
                
                # 🧠 2. اكتشاف روبوتات التقسيم (Iceberg/TWAP Variance Detection):
                # التجميع بناءً على الزمن (الثانية) لاكتشاف أوامر الـ HFT المقطعة
                time_clusters = {}
                for t in trades:
                    sec = t['T'] // 1000
                    vol = float(t['p']) * float(t['q'])
                    is_sell = t['m']
                    time_clusters.setdefault(sec, []).append((vol, is_sell))
                
                algo_multiplier_buy = 1.0
                algo_multiplier_sell = 1.0
                
                # تحليل التباين (Variance) للثواني التي تحتوي أكثر من 5 صفقات
                for sec, cluster in time_clusters.items():
                    if len(cluster) >= 5:
                        vols = [x[0] for x in cluster]
                        cv = np.std(vols) / (np.mean(vols) + 1e-8)
                        if cv < 0.2: # تجانس رياضي عالي جداً = Algo (روبوت يقسم الأوامر بالتساوي)
                            if cluster[0][1]: algo_multiplier_sell = 1.5
                            else: algo_multiplier_buy = 1.5

                for t in trades:
                    price = float(t['p'])
                    amount = float(t['q']) * price
                    
                    # فلترة صغار المتداولين
                    if amount < min_smart_money: continue 
                    
                    prices.append(price)
                    # ضرب الحجم بمعامل الخوارزمية (الروبوتات أثقل وزناً من التداول العادي)
                    if t['m']: s_vol += (amount * algo_multiplier_sell)
                    else: b_vol += (amount * algo_multiplier_buy)
                    
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
        
        # 🧠 [التحديث المؤسساتي]: مصفوفة خصم أثر السيولة (Liquidity Impact Discounting)
        # 1 دولار في Binance يتمتع بوزن 1.0 (تأثير كامل).
        # المنصات ذات الدفاتر الضحلة (Shallow Orderbooks) تُعاقب بخصم وزنها 
        # لمنع تأثير التدوير الوهمي (Wash Trading) الذي يخدع مؤشرات السيولة.
        BINANCE_WEIGHT = 1.00
        BYBIT_WEIGHT   = 0.65
        OKX_WEIGHT     = 0.40
        
        adj_bin_buy = bin_buy * BINANCE_WEIGHT
        adj_bin_sell = bin_sell * BINANCE_WEIGHT
        
        adj_byb_buy = byb_buy * BYBIT_WEIGHT
        adj_byb_sell = byb_sell * BYBIT_WEIGHT
        
        adj_okx_buy = okx_buy * OKX_WEIGHT
        adj_okx_sell = okx_sell * OKX_WEIGHT
        
        # 🌍 حساب التدفق العالمي الموزون (Depth-Adjusted Global Flow)
        global_buy_vol = adj_bin_buy + adj_byb_buy + adj_okx_buy
        global_sell_vol = adj_bin_sell + adj_byb_sell + adj_okx_sell
        
        global_delta = global_buy_vol - global_sell_vol
        total_global_vol = global_buy_vol + global_sell_vol
        signal = None
        
        # --- 🛡️ محرك اكتشاف الامتصاص المتقاطع (Cross-Exchange Absorption) ---
        if bin_prices and total_global_vol > 0:
            import pandas as pd # ضمان التوفر اللحظي
            price_series = pd.Series(bin_prices)
            price_range_pct = (price_series.max() - price_series.min()) / (price_series.min() + 1e-8)
            
            # 1. الامتصاص التقليدي (السعر ثابت والسيولة تدخل)
            if price_range_pct <= 0.005 and global_delta > (total_global_vol * 0.25):
                signal = "Limit_Absorption"
                
            # 2. الموازنة المؤسساتية (Arbitrage & Panic Absorption)
            # الألفا الحقيقية: متداولو التجزئة يفرغون عملاتهم في OKX و Bybit بهلع (دلتا سلبية)،
            # بينما حيتان Binance يقومون بابتلاع كل هذا البيع وتثبيت السعر (دلتا إيجابية ضخمة)!
            bin_delta = adj_bin_buy - adj_bin_sell
            alt_delta = (adj_byb_buy + adj_okx_buy) - (adj_byb_sell + adj_okx_sell)
            
            if price_range_pct <= 0.008 and bin_delta > 0 and alt_delta < 0:
                if bin_delta > abs(alt_delta) * 1.5: # بايننس تبتلع الهلع بـ 1.5 ضعف
                    signal = "Limit_Absorption" 
                    # مكافأة رياضية: رفع قيمة الدلتا لأن امتصاص بايننس هو المحرك الحقيقي للسوق
                    global_delta += bin_delta * 0.30 
                    
        return float(global_delta), float(global_buy_vol), float(global_sell_vol), signal

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
        
        if spot_res.status_code != 200 or fapi_res.status_code != 200: 
            if fapi_res.status_code != 200:
                print(f"⚠️ [Binance FAPI Direct] {clean_sym} | رفض كود {fapi_res.status_code} في Spot-Perp Divergence")
            return 0.0

        spot_df = pd.DataFrame(spot_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        fapi_df = pd.DataFrame(fapi_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        
        # حساب CVD للسبوت
        spot_df['v'] = pd.to_numeric(spot_df['v'])
        spot_df['tbv'] = pd.to_numeric(spot_df['tbv'])
        spot_df['delta'] = spot_df['tbv'] - (spot_df['v'] - spot_df['tbv'])
        spot_cvd = spot_df['delta'].cumsum()
        
        # حساب CVD للفيوتشرز
                # حساب CVD للفيوتشرز
        fapi_df['v'] = pd.to_numeric(fapi_df['v'])
        fapi_df['tbv'] = pd.to_numeric(fapi_df['tbv'])
        fapi_df['delta'] = fapi_df['tbv'] - (fapi_df['v'] - fapi_df['tbv'])
        fapi_cvd = fapi_df['delta'].cumsum()
        # 🛡️ الحماية المؤسساتية (Zero Variance Protection):
        # يجب فحص الانحراف المعياري للتدفق اللحظي (Delta) وليس التراكمي
        if spot_df['delta'].std() == 0 or fapi_df['delta'].std() == 0:
            return 0.0

        # 🧠 التصحيح الكمّي: حساب الارتباط (Correlation) على التغير اللحظي (Deltas) 
        # لمنع الارتباط الزائف للسلاسل الزمنية غير المستقرة (Non-Stationary)
        correlation = spot_df['delta'].corr(fapi_df['delta'])
        
        # نحتفظ بحساب الإجمالي لمعرفة اتجاه السيولة العام في التقييم السفلي
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

    except Exception as e:
        print(f"🚨 [Binance FAPI Direct] خطأ في detect_spot_perp_divergence لـ {clean_sym}: {str(e)}")
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
import numpy as np
import pandas as pd

async def build_liquidation_heatmap(symbol: str, client: httpx.AsyncClient):
    """
    [Tier-1 Quant] Cumulative Liquidation Heatmap Engine 🧲
    تم تحديث المحرك من الداخل ليعمل بـ 3D Fusion (الشموع، OI، وعدوانية المتداولين)
    مع الحفاظ التام على التوافق الرجعي (Backward Compatibility) لباقي دوال البوت.
    """
    import numpy as np
    import pandas as pd
    
    fapi_base = "https://fapi.binance.com"
    clean_sym = symbol.replace("USDT", "") + "USDT"
    
    try:
        oi_url = f"{fapi_base}/futures/data/openInterestHist?symbol={clean_sym}&period=1h&limit=72"
        klines_url = f"{get_random_binance_base()}/api/v3/klines?symbol={clean_sym}&interval=1h&limit=72"
        taker_ls_url = f"{fapi_base}/futures/data/takerlongshortRatio?symbol={clean_sym}&period=1h&limit=72"
        
        await binance_rate_limit_event.wait()
        res_oi, res_klines, res_taker = await asyncio.gather(
            client.get(oi_url, timeout=5.0),
            client.get(klines_url, timeout=5.0),
            client.get(taker_ls_url, timeout=5.0)
        )
        
        if res_oi.status_code != 200 or res_klines.status_code != 200 or res_taker.status_code != 200:
            return None
            
        oi_data = res_oi.json()
        klines_data = res_klines.json()
        taker_data = res_taker.json()
        
        if len(oi_data) < 20 or len(klines_data) < 20: return None

        df = pd.DataFrame({
            'high': [float(k[2]) for k in klines_data],
            'low': [float(k[3]) for k in klines_data],
            'close': [float(k[4]) for k in klines_data],
            'vwap': [(float(k[2]) + float(k[3]) + float(k[4])) / 3 for k in klines_data],
            'oi': [float(o['sumOpenInterestValue']) for o in oi_data],
            'taker_buy_vol': [float(t['buyVol']) for t in taker_data],
            'taker_sell_vol': [float(t['sellVol']) for t in taker_data]
        })
        
        df['oi_delta'] = df['oi'].diff().fillna(0)
        current_price = df['vwap'].iloc[-1]
        
        build_ups = df[df['oi_delta'] > 0].copy()
        short_liq_levels = []
        long_liq_levels = []
        
        for _, row in build_ups.iterrows():
            oi_added = row['oi_delta']
            vwap = row['vwap']
            if row['taker_buy_vol'] > row['taker_sell_vol'] * 1.1:
                long_liq_levels.append({'price': vwap * 0.985, 'weight': oi_added * 0.4})
                long_liq_levels.append({'price': vwap * 0.965, 'weight': oi_added * 0.6})
            elif row['taker_sell_vol'] > row['taker_buy_vol'] * 1.1:
                short_liq_levels.append({'price': vwap * 1.015, 'weight': oi_added * 0.4})
                short_liq_levels.append({'price': vwap * 1.035, 'weight': oi_added * 0.6})

        def extract_thickest_cluster(levels, is_upper):
            if not levels: return None, 0.0
            cdf = pd.DataFrame(levels)
            if is_upper: cdf = cdf[cdf['price'] > current_price]
            else: cdf = cdf[cdf['price'] < current_price]
            
            if cdf.empty: return None, 0.0
            bin_size = current_price * 0.005
            min_p, max_p = cdf['price'].min(), cdf['price'].max()
            bins = np.arange(min_p, max_p + bin_size, bin_size)
            
            if len(bins) < 2: return [min_p, max_p], cdf['weight'].sum()
            cdf['bin'] = pd.cut(cdf['price'], bins)
            cluster = cdf.groupby('bin', observed=False)['weight'].sum()
            if cluster.empty: return None, 0.0
            
            thickest_bin = cluster.idxmax()
            return [thickest_bin.left, thickest_bin.right], cluster.max()

        upper_pool, upper_weight = extract_thickest_cluster(short_liq_levels, is_upper=True)
        lower_pool, lower_weight = extract_thickest_cluster(long_liq_levels, is_upper=False)
        total_weight = upper_weight + lower_weight + 1e-8
        
        def format_pool(pool, weight):
            if not pool: return "غير متاح", "⚪ ضعيف"
            intensity = "🔥 مرعب" if weight / total_weight > 0.6 else ("🩸 عالي" if weight / total_weight > 0.4 else "🟡 متوسط")
            return f"${format_price(pool[0])} - ${format_price(pool[1])}", intensity

        up_range, up_int = format_pool(upper_pool, upper_weight)
        low_range, low_int = format_pool(lower_pool, lower_weight)
        
        # 🧠 التوافق الرجعي (Backward Compatibility):
        # توليد المتغيرات القديمة التي تحتاجها باقي دوال البوت لكي لا ينكسر الكود
        pain_node = current_price # كقيمة افتراضية للمسار الآمن
        if upper_weight > lower_weight * 1.5:
            magnetic_bias = "استهداف علوي (Short Squeeze Magnet) 🚀"
            legacy_target = upper_pool[0] if upper_pool else current_price * 1.05
            legacy_type = "Short Liquidation Magnet (Squeeze) 🧲"
        elif lower_weight > upper_weight * 1.5:
            magnetic_bias = "استهداف سفلي (Long Flush Magnet) 🩸"
            legacy_target = lower_pool[1] if lower_pool else current_price * 0.95
            legacy_type = "Long Liquidation Magnet (Flush) 🧲"
        else:
            magnetic_bias = "تجاذب سيولي (Two-Way Liquidity) ⚖️"
            legacy_target = current_price
            legacy_type = "Dynamic Target (No Stack)"

        legacy_distance_pct = abs(current_price - legacy_target) / current_price

        # نرجع القاموس يضم المفاتيح القديمة (للرادارات) والمفاتيح الجديدة (لتقرير /btc)
        return {
            # ⬅️ المفاتيح القديمة لحماية النظام من الكراش
            "pain_node": pain_node,
            "target": legacy_target,
            "type": legacy_type,
            "distance_pct": legacy_distance_pct,
            
            # ⬅️ المفاتيح الجديدة العميقة لتقرير /btc
            "upper_pool": up_range, 
            "upper_intensity": up_int,
            "lower_pool": low_range, 
            "lower_intensity": low_int,
            "magnetic_bias": magnetic_bias
        }

    except Exception as e:
        print(f"🚨 [Cumulative Liquidity Engine] Error for {clean_sym}: {str(e)}")
        return None


async def track_orderbook_center_of_mass(symbol: str, client: httpx.AsyncClient, current_price: float):
    """
    [Tier-1 Quant Upgrade] Deep Liquidity Center of Mass
    يعزل ضجيج خوارزميات الـ HFT ويركز على سيولة "حزام الالتزام" (Commitment Band) 
    لاكتشاف الزحف الحقيقي لأرضية صانع السوق (Dark Accumulation).
    """
    sym = symbol.replace("USDT", "") + "USDT"
    current_time = time.time()
    
    try:
        # 1000 مستوى لكشف السيولة العميقة حقاً وليس القشور السطحية
        url = f"{get_random_binance_base()}/api/v3/depth?symbol={sym}&limit=1000"
        await binance_rate_limit_event.wait()
        res = await client.get(url, timeout=3.0)
        
        if res.status_code != 200: return None
        bids = np.array([[float(p), float(v)] for p, v in res.json().get('bids', [])])
        if len(bids) == 0: return None
        
        # 🧠 [التحديث المؤسساتي]: هندسة عزل الطبقات (Liquidity Stratification)
        # استبعاد أول 0.5% (جدران وهمية سريعة السحب للترهيب)
        # استبعاد ما هو أدنى من 7% (طلبات ميتة لن تتنفذ قريباً وتكسر الدقة)
        upper_bound = current_price * 0.995
        lower_bound = current_price * 0.930
        
        commitment_bids = bids[(bids[:, 0] <= upper_bound) & (bids[:, 0] >= lower_bound)]
        if len(commitment_bids) == 0: return None
        
        total_vol = np.sum(commitment_bids[:, 1])
        com_price = np.sum(commitment_bids[:, 0] * commitment_bids[:, 1]) / total_vol
        
        if symbol not in VANGUARD_MEMORY:
            VANGUARD_MEMORY[symbol] = {"com_history": []}
            
        history = VANGUARD_MEMORY[symbol]["com_history"]
        
        # تسجيل قراءة جديدة كل ساعة (3600 ثانية) لتكوين مسار دقيق للاختراقات
        if not history or (current_time - history[-1]['ts']) > 3600:
            history.append({"ts": current_time, "com": com_price, "price": current_price})
            VANGUARD_MEMORY[symbol]["com_history"] = history[-72:] # حفظ 3 أيام من البيانات
            
        # 🧠 التقييم المؤسساتي للزحف السعري (استخدام تمليس المتوسطات لمنع التشوه اللحظي)
        if len(history) > 12: # نحتاج 12 ساعة كحد أدنى للحكم
            # استخدام متوسط 3 ساعات للبدء والنهاية لقتل شذوذ اللقطات المفردة
            old_com = sum(h['com'] for h in history[:3]) / 3
            new_com = sum(h['com'] for h in history[-3:]) / 3
            
            old_price = sum(h['price'] for h in history[:3]) / 3
            new_price = sum(h['price'] for h in history[-3:]) / 3
            
            com_growth = (new_com - old_com) / old_com
            price_growth = (new_price - old_price) / old_price
            
            # 🚀 الألفا: أرضية الأوردر بوك العميقة زحفت صعوداً بأكثر من 1.5% بينما السعر محتجز أو ينزف!
            if com_growth > 0.015 and price_growth < 0.005:
                return {"com_growth_pct": com_growth * 100, "days_tracked": len(history) / 24.0}
                
        return None
    except Exception:
        return None

async def detect_global_derivatives_frontrunning(symbol: str, client: httpx.AsyncClient):
    """
    [Tier-1 Quant Upgrade] Microstructure Derivatives Front-Running
    يقيس "تسارع" الانحراف في تدفق الأوامر (Order Flow Acceleration) للفريمات الصغيرة (15m)،
    لأن التحوط المؤسساتي يحدث كصدمة استباقية قبل ساعات قليلة من انفجار السبوت.
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    try:
        # النزول بالعدسة إلى 15 دقيقة لالتقاط البصمة الدقيقة للتدفق لآخر 6 ساعات
        spot_url = f"{get_random_binance_base()}/api/v3/klines?symbol={clean_sym}&interval=15m&limit=24"
        fapi_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean_sym}&interval=15m&limit=24"
        
        await binance_rate_limit_event.wait()
        spot_res, fapi_res = await asyncio.gather(
            client.get(spot_url, timeout=5.0),
            client.get(fapi_url, timeout=5.0)
        )
        
        if spot_res.status_code != 200 or fapi_res.status_code != 200: 
            if fapi_res.status_code != 200:
                print(f"⚠️ [Binance FAPI Direct] {clean_sym} | كود {fapi_res.status_code} في Frontrunning")
            return None
        
        spot_df = pd.DataFrame(spot_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        fapi_df = pd.DataFrame(fapi_res.json(), columns=["t","o","h","l","c","v","ct","qv","trades","tbv","tqav","ignore"])
        
        spot_df['tbv'], spot_df['v'] = pd.to_numeric(spot_df['tbv']), pd.to_numeric(spot_df['v'])
        fapi_df['tbv'], fapi_df['v'] = pd.to_numeric(fapi_df['tbv']), pd.to_numeric(fapi_df['v'])
        
        # حساب التدفق اللحظي للسيولة العدوانية (Delta) لكل شمعة
        spot_df['delta'] = spot_df['tbv'] - (spot_df['v'] - spot_df['tbv'])
        fapi_df['delta'] = fapi_df['tbv'] - (fapi_df['v'] - fapi_df['tbv'])
        
        # 🧠 [التحديث المؤسساتي]: التركيز على "التسارع" في آخر ساعتين (8 شموع)
        recent_spot_cvd = spot_df['delta'].tail(8).sum()
        recent_fapi_cvd = fapi_df['delta'].tail(8).sum()
        
        recent_spot_vol = spot_df['v'].tail(8).sum()
        recent_fapi_vol = fapi_df['v'].tail(8).sum()
        
        if recent_spot_vol == 0 or recent_fapi_vol == 0: return None
        
        # كثافة الشراء الماركت نسبة إلى إجمالي الحجم
        spot_inflow_ratio = recent_spot_cvd / recent_spot_vol
        fapi_inflow_ratio = recent_fapi_cvd / recent_fapi_vol
        
        divergence = fapi_inflow_ratio - spot_inflow_ratio
        
        # 🚀 الألفا: المشتقات تقود بتدفق شرائي عنيف (فارق > 20%) بينما السبوت نائم أو يصحح
        if divergence > 0.20 and spot_inflow_ratio <= 0.05:
            return {"type": "Derivatives Leading 📈", "divergence": divergence * 100}
        # توزيع صامت (Distribution): بيع قوي في المشتقات بينما السبوت مستقر
        elif divergence < -0.20 and spot_inflow_ratio >= -0.05:
            return {"type": "Derivatives Distribution 📉", "divergence": divergence * 100}
            
        return None
    except Exception as e:
        print(f"🚨 [Binance FAPI Direct] خطأ في Frontrunning لـ {clean_sym}: {str(e)}")
        return None

async def institutional_vanguard_worker():
    """
    غرفة العمليات الاستباقية (Vanguard Worker)
    يعمل بشكل معزول تماماً، يبحث في أعلى 150 عملة ليرسل تقارير للأدمن فقط.
    """
    await asyncio.sleep(300) # انتظار 5 دقائق بعد تشغيل البوت
    print("🦅 [Vanguard Engine] Institutional Pre-cognition is Online.")
    
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await binance_rate_limit_event.wait()
                base_url = get_random_binance_base()
                res = await client.get(f"{base_url}/api/v3/ticker/24hr")
                
                if res.status_code == 200:
                    tickers = [t for t in res.json() if t['symbol'].endswith("USDT") and float(t['quoteVolume']) > 5_000_000]
                    # نأخذ أعلى 150 عملة سيولة فقط
                    tickers = sorted(tickers, key=lambda x: float(x['quoteVolume']), reverse=True)[:150]
                    
                    for t in tickers:
                        sym = t['symbol'].replace("USDT", "")
                        if not sym.isalnum(): continue
                        if sym in BLACKLISTED_COINS: continue
                        
                        
                        price = float(t['lastPrice'])
                        
                        # تشغيل المحركات الثلاثة معاً
                        liq_data = await build_liquidation_heatmap(sym, client)
                        com_data = await track_orderbook_center_of_mass(sym, client, price)
                        lead_lag_data = await detect_global_derivatives_frontrunning(sym, client)
                        
                        # 🧠 شرط إطلاق الإنذار للأدمن: 
                        # يجب أن نكتشف (زحف الأوردر بوك) أو (استباق المشتقات) مقترناً بمغناطيس تصفية
                        
                        if (com_data or lead_lag_data) and liq_data:
                            # فلتر إضافي: لا ترسل إذا كانت مسافة التصفية بعيدة جداً (أكثر من 15%)
                            if liq_data['distance_pct'] > 0.15: continue
                            
                            report = f"🦅 <b>استخبارات الطليعة المؤسساتية (Vanguard)</b> 🦅\n"
                            report += f"━━━━━━━━━━━━━━\n"
                            report += f"💎 <b>العملة:</b> #{sym}\n"
                            report += f"💵 <b>السعر الحالي:</b> ${format_price(price)}\n\n"
                            
                            if com_data:
                                report += f"🧱 <b>زحف السيولة (Dark Accumulation):</b>\n"
                                report += f"أرضية طلبات الشراء ارتفعت بنسبة <b>{com_data['com_growth_pct']:.1f}%</b> خلال <b>{com_data['days_tracked']:.1f} أيام</b> والسعر لا يزال مضغوطاً.\n\n"
                            
                            if lead_lag_data:
                                report += f"⚡ <b>استباق المشتقات (Front-running):</b>\n"
                                report += f"حيتان الفيوتشرز يسبقون السبوت ({lead_lag_data['type']}) بانحراف <b>{lead_lag_data['divergence']:.1f}%</b>.\n\n"
                                
                            report += f"🧲 <b>مغناطيس التصفية القادم:</b>\n"
                            report += f"النوع: {liq_data['type']}\n"
                            report += f"الهدف المؤسساتي المتوقع: <b>${format_price(liq_data['target'])}</b>\n"
                            report += f"━━━━━━━━━━━━━━\n"
                            report += f"<i>* هذه رسالة مشفرة للأدمن فقط تكشف نوايا صانع السوق قبل الانفجار.</i>"
                            
                            # إرسال للأدمن فقط
                            await bot.send_message(ADMIN_USER_ID, report, parse_mode=ParseMode.HTML)
                            
                        await asyncio.sleep(1) # استراحة بين العملات لتجنب الحظر
                        
        except Exception as e:
            print(f"⚠️ [Vanguard Engine] Error: {e}")
            
        await asyncio.sleep(3600) # يمسح السوق مرة واحدة كل ساعة بهدوء تام

import websockets
import json
import asyncio
import time

async def detect_flash_spoofing_ws(symbol: str, duration: float = 12.0):
    """
    [Tier-1 HFT Upgrade] True OFI via AggTrade + Depth Fusion
    يصطاد السيولة الحقيقية ويتجاهل الجدران الوهمية (Ghost Spoofing) التي تُسحب قبل التنفيذ.
    """
    clean_symbol = symbol.replace("USDT", "").lower() + "usdt"
    # دمج تيار الأوردر بوك السريع مع تيار التنفيذ الفعلي
    ws_url = f"wss://stream.binance.com:9443/stream?streams={clean_symbol}@depth5@100ms/{clean_symbol}@aggTrade"
    
    bid_vols, ask_vols = [], []
    taker_buy_vol, taker_sell_vol = 0.0, 0.0
    ofi_trend = 0.0

    try:
        async with websockets.connect(ws_url, ping_interval=None, close_timeout=1) as ws:
            start_time = time.time()
            while time.time() - start_time < duration:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(msg)
                    stream = data.get('stream', '')
                    payload = data.get('data', {})

                    if 'aggTrade' in stream:
                        trade_vol = float(payload.get('p', 0)) * float(payload.get('q', 0))
                        if payload.get('m', False): # Taker Sell
                            taker_sell_vol += trade_vol
                        else: # Taker Buy
                            taker_buy_vol += trade_vol

                    elif 'depth' in stream:
                        if not payload.get('bids') or not payload.get('asks'): continue
                        current_bids = sum(float(p) * float(v) for p, v in payload['bids'])
                        current_asks = sum(float(p) * float(v) for p, v in payload['asks'])
                        
                        bid_vols.append(current_bids)
                        ask_vols.append(current_asks)

                        if len(bid_vols) > 1:
                            delta_bid = bid_vols[-1] - bid_vols[-2]
                            delta_ask = ask_vols[-1] - ask_vols[-2]
                            
                            # 🧠 True OFI Logic: السيولة تضاف + تنفيذ حقيقي في نفس اللحظة = حوت
                            if delta_bid > 0 and taker_buy_vol > 0: ofi_trend += 1.0
                            if delta_ask > 0 and taker_sell_vol > 0: ofi_trend -= 1.0
                            
                            # تصفير عداد التنفيذ اللحظي للدورة القادمة
                            taker_buy_vol, taker_sell_vol = 0.0, 0.0

                except asyncio.TimeoutError:
                    continue
    except Exception:
        return None

    if len(bid_vols) < 5: return None

    mean_bids = np.mean(bid_vols)
    mean_asks = np.mean(ask_vols)
    bid_std = np.std(bid_vols) / (mean_bids + 1e-6)
    ask_std = np.std(ask_vols) / (mean_asks + 1e-6)

    return {
        "imbalance": round((mean_bids - mean_asks) / (mean_bids + mean_asks + 1e-6), 2),
        "ofi_trend": ofi_trend,
        "is_bid_spoof": bid_std > 0.8,
        "is_ask_spoof": ask_std > 0.8,
        "is_iceberg_buying": bid_std < 0.15 and mean_bids > mean_asks and ofi_trend > 0
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
                
                # 🧠 محرك الفلترة المكانية (Proximity Filter)
                # تجاهل أي سيولة تبعد أكثر من 5% عن السعر الحالي لقتل الجدران الوهمية (Spoof Walls)
                def calc_real_depth(orders_list, is_bid):
                    if not orders_list: return 0.0
                    best_price = float(orders_list[0][0])
                    limit_price = best_price * 0.95 if is_bid else best_price * 1.05
                    total = 0.0
                    for p, v in orders_list:
                        price, vol = float(p), float(v)
                        if (is_bid and price >= limit_price) or (not is_bid and price <= limit_price):
                            total += price * vol
                    return total

                # 1. Binance, MEXC, Gate
                if exchange in ["binance", "mexc", "gate"]:
                    bids_vol = calc_real_depth(data.get("bids", []), True)
                    asks_vol = calc_real_depth(data.get("asks", []), False)
                # 2. Bybit
                elif exchange == "bybit":
                    result = data.get("result", {})
                    bids_vol = calc_real_depth(result.get("b", []), True)
                    asks_vol = calc_real_depth(result.get("a", []), False)
                # 3. KuCoin
                elif exchange == "kucoin":
                    d = data.get("data", {})
                    bids_vol = calc_real_depth(d.get("bids", [])[:50], True)
                    asks_vol = calc_real_depth(d.get("asks", [])[:50], False)
                # 4. OKX
                elif exchange == "okx":
                    d = data.get("data", [{}])[0]
                    bids_vol = calc_real_depth(d.get("bids", []), True)
                    asks_vol = calc_real_depth(d.get("asks", []), False)
                # 5. Bitget (الجديدة)
                elif exchange == "bitget":
                    d = data.get("data", {})
                    bids_vol = calc_real_depth(d.get("bids", []), True)
                    asks_vol = calc_real_depth(d.get("asks", []), False)
                # 6. HTX (الجديدة)
                elif exchange == "htx":
                    d = data.get("tick", {})
                    bids_vol = calc_real_depth(d.get("bids", [])[:50], True)
                    asks_vol = calc_real_depth(d.get("asks", [])[:50], False)

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
    """
    [The True Funding Z-Score Engine]
    يحسب الانحراف المعياري الحقيقي لآخر 14 يوماً (42 فترة تمويل) بدون أي أرقام وهمية.
    """
    fapi_base = "https://fapi.binance.com"
    pair = f"{symbol}USDT"

    try:
        # 1. جلب التغير اللحظي للـ OI
        oi_url = f"{fapi_base}/futures/data/openInterestHist?symbol={pair}&period=15m&limit=2"
        # 2. جلب التمويل اللحظي القادم
        live_fund_url = f"{fapi_base}/fapi/v1/premiumIndex?symbol={pair}"
        # 3. جلب تاريخ التمويل لآخر 14 يوم (42 فترة، كل فترة 8 ساعات)
        hist_fund_url = f"{fapi_base}/fapi/v1/fundingRate?symbol={pair}&limit=42"

        # 🛑 حماية من حظر بايننس
        await binance_rate_limit_event.wait()

        oi_res, live_fund_res, hist_fund_res = await asyncio.gather(
            client.get(oi_url, timeout=5.0),
            client.get(live_fund_url, timeout=5.0),
            client.get(hist_fund_url, timeout=5.0)
        )

        if oi_res.status_code == 200 and live_fund_res.status_code == 200 and hist_fund_res.status_code == 200:
            oi_data = oi_res.json()
            live_fund_data = live_fund_res.json()
            hist_fund_data = hist_fund_res.json()

            if len(oi_data) < 2 or not hist_fund_data: 
                return 0.0, None, 0.0, 0.0, 0.0

            old_oi = float(oi_data[0]["sumOpenInterest"])
            current_oi = float(oi_data[-1]["sumOpenInterest"])
            oi_change_pct = (current_oi - old_oi) / (old_oi + 1e-8)
            price_change_pct = (float(current_price) - float(old_price)) / (float(old_price) + 1e-8)
            
            # التمويل اللحظي
            current_funding_rate = float(live_fund_data.get("lastFundingRate", 0.0))
            
            # 🧠 الحساب الكمي للانحراف المعياري الحقيقي (True Z-Score)
            import numpy as np
            hist_rates = [float(item["fundingRate"]) for item in hist_fund_data]
            
            mean_funding = np.mean(hist_rates)
            std_funding = np.std(hist_rates, ddof=0)
            
            # 🛡️ الجدار المؤسساتي (Volatility Floor) لمنع انفجار الأرقام
            MIN_FUNDING_STD = 0.00005
            safe_std = max(std_funding, MIN_FUNDING_STD)
            
            funding_z_score = float((current_funding_rate - mean_funding) / safe_std)
            
            # ⚙️ إشارات الرادار الكلاسيكية الخاصة بك
            score_modifier = 0.0
            futures_signal = None

            if price_change_pct > 0.01 and oi_change_pct > 0.02: 
                score_modifier += 15.0
                futures_signal = "OI_Rising"
            elif price_change_pct > 0.01 and oi_change_pct < -0.02:
                score_modifier -= 25.0
                futures_signal = "Short_Covering"
            
            if funding_z_score < -1.5: 
                score_modifier += 12.0
                if not futures_signal: futures_signal = "Short_Squeeze"
            elif funding_z_score > 1.5:
                score_modifier -= 10.0

            # 🚀 إرجاع 5 متغيرات (تمت إضافة Z-Score في النهاية)
            return score_modifier, futures_signal, current_funding_rate, oi_change_pct, funding_z_score

    except Exception as e: 
        print(f"🚨 [Funding Engine] Error for {pair}: {str(e)}")
    
    # في حال الفشل، نعيد أصفاراً آمنة
    return 0.0, None, 0.0, 0.0, 0.0


def calculate_volume_zscore(df, window=720):
    """
    [Tier-1 Quant Upgrade] Time-of-Day Seasonality Z-Score
    يقارن فوليوم الشمعة الحالية بمتوسط الفوليوم لنفس التوقيت من الأيام السابقة، 
    مما يعزل ضجيج افتتاح الجلسات العالمية (الأمريكية والآسيوية) عن نشاط الحيتان الحقيقي.
    """
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    
    # التحويل اللوغاريتمي الآمن
    log_vol = np.log1p(df["volume"])
    
    # 🧠 الحل المؤسساتي: عزل الموسمية الزمنية (Time-of-Day Grouping)
    if "timestamp" in df.columns:
        try:
            # 1. استخراج "الوقت" من الشمعة (مثلاً 14:00 أو 14:15)
            dt = pd.to_datetime(pd.to_numeric(df["timestamp"]), unit='s')
            time_of_day = dt.dt.time
            
            # 2. إنشاء جدول مؤقت لجمع الفوليوم حسب الوقت
            temp_df = pd.DataFrame({'log_vol': log_vol, 'tod': time_of_day})
            
            # 3. حساب المتوسط والانحراف المعياري لكل "وقت محدد" عبر التاريخ المتاح
            tod_stats = temp_df.groupby('tod')['log_vol'].agg(['mean', 'std'])
            
            # 4. سحب وقت الشمعة الحالية
            current_tod = time_of_day.iloc[-1]
            
            # 5. جلب المعدلات التاريخية الخاصة بهذا التوقيت تحديداً
            current_mean = tod_stats.loc[current_tod, 'mean']
            current_std = tod_stats.loc[current_tod, 'std']
            
            if pd.isna(current_std) or current_std == 0:
                current_std = 1e-8
                
            # 6. حساب الـ Z-Score الدقيق الخالي من ضجيج الجلسات
            current_z = float((log_vol.iloc[-1] - current_mean) / current_std)
            
        except Exception as e:
            # 🛡️ الجدار الآمن (Fallback): إذا فشلت الحسابات، نعود لنظام Rolling الكلاسيكي
            rolling_mean_log = log_vol.rolling(window=window, min_periods=100).mean()
            rolling_std_log = log_vol.rolling(window=window, min_periods=100).std(ddof=0)
            current_z = float((log_vol.iloc[-1] - rolling_mean_log.iloc[-1]) / (rolling_std_log.iloc[-1] + 1e-8))
    else:
        # Fallback في حال عدم تمرير عمود timestamp
        rolling_mean_log = log_vol.rolling(window=window, min_periods=100).mean()
        rolling_std_log = log_vol.rolling(window=window, min_periods=100).std(ddof=0)
        current_z = float((log_vol.iloc[-1] - rolling_mean_log.iloc[-1]) / (rolling_std_log.iloc[-1] + 1e-8))

    # حسابات توافقية للحفاظ على استقرار الكود القديم دون كسر الدوال الأخرى
    last_median = float(df["volume"].rolling(window=window, min_periods=100).median().iloc[-1])
    last_mad = float(df["volume"].rolling(window=window, min_periods=100).std().iloc[-1])
    
    if pd.isna(current_z) or current_z == float('inf'):
        current_z = 0.0

    return current_z, last_median, last_mad

async def silent_data_harvester_worker(pool):
    """
    عامل الحصاد الصامت (The Apex Sniper): 
    يعمل في الخلفية بهدوء، يحلل عملة واحدة كل دقيقة لجمع البيانات اللحظية.
    إذا وجد فرصة بتقييم ذكاء اصطناعي (AI) أعلى من 80%، يكسر الصمت ويرسل إشعار طوارئ.
    """
    await asyncio.sleep(120) # ننتظر دقيقتين بعد تشغيل البوت ليستقر
    print("🌾 [Data Harvester & Apex Sniper] Engine is Online. Hunting silently...")

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
                    if not clean_sym.isalnum(): continue
                    if clean_sym in BLACKLISTED_COINS: continue
                    
                    vol_usd = float(t["quoteVolume"])
                    if vol_usd >= 200_000: # الفلتر المبدئي للسيولة
                        coins.append({"symbol": clean_sym, "price": float(t["lastPrice"]), "volume": vol_usd})
                
                # ترتيب العملات حسب السيولة وأخذ أعلى 350
                coins = sorted(coins, key=lambda x: x["volume"], reverse=True)[:350]
                
                print(f"🔄 [Apex Sniper] Starting new silent cycle for {len(coins)} coins...")

                # ⏳ التقطير الصامت: معالجة عملة واحدة فقط كل 50 ثانية
                for c in coins:
                    await binance_rate_limit_event.wait()
                    sym = c["symbol"]
                    price = c["price"]
                    pair = f"{sym}USDT"
                    
                    try:
                        # 1. جلب الشموع (15 دقيقة للتدريب السريع والدقيق)                        # 1. جلب الشموع (15m للتحليل + 1d للماكرو) بخفة تامة
                        candles_15m, candles_1d = await asyncio.gather(
                            get_candles_binance(pair, "15m", limit=750),
                            get_candles_binance(pair, "1d", limit=40), # 40 يوم تكفي للـ Z-score و الـ FVG
                            return_exceptions=True
                        )
                        
                        if isinstance(candles_15m, Exception) or not candles_15m: continue
                        if isinstance(candles_1d, Exception): candles_1d = []

                        # تحويل شموع اليوم إلى أسبوع برمجياً دون الحاجة لطلب API إضافي                        # تحويل شموع اليوم إلى أسبوع برمجياً دون الحاجة لطلب API إضافي
                        candles_1w_simulated = []
                        if candles_1d and len(candles_1d) >= 14:
                            df_daily = pd.DataFrame(candles_1d).iloc[:, :6]
                            df_daily.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                            
                            # 🟢 التعديل الجراحي: تحويل النصوص إلى أرقام قبل التجميع
                            df_daily[["open", "high", "low", "close", "volume"]] = df_daily[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors='coerce')
                            
                            df_daily['datetime'] = pd.to_datetime(df_daily['timestamp'].astype(float), unit='s')
                            df_daily.set_index('datetime', inplace=True)
                            
                            weekly_df = df_daily.resample('W-MON').agg({
                                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 
                                'volume': 'sum', 'timestamp': 'first'
                            }).dropna()
                            candles_1w_simulated = weekly_df.values.tolist()

                        # حساب الماكرو الحقيقي بصمت
                        w_void, m_z30, htf_accum, days_exp = await asyncio.to_thread(
                            calculate_macro_htf_features, candles_1d, candles_1w_simulated
                        )

                        
                        candles = candles_15m # نمررها للدوال التي تعتمد على 15m

                        
                        df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)
                                                # 🎯 التحديث اللحظي للسعر لحل مشكلة السعر القديم والأوردر بوك المجنون
                        price = float(df["close"].iloc[-1])

                        # 2. جلب البيانات اللحظية (التي لا تحفظها بايننس تاريخياً)
                        cvd_boost, cvd_sig, cvd_trend = await get_micro_cvd_absorption(pair, client, "15m")
                        global_ob_pressure = await get_aggregated_orderbook(client, sym)
                        depth_data = await analyze_orderbook_spoofing_instant(sym, client, price)
                        tick_delta, tick_buy, tick_sell, limit_abs = await get_institutional_orderflow(pair, client)
                        _, fut_sig, funding_val, _, _ = await get_futures_liquidity(sym, client, price, float(df["close"].iloc[-3]))
                        
                        avg_vol_20 = df["volume"].tail(20).mean()
                        avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
                        cvd_ratio_pct = (cvd_trend * price / avg_vol_usd) * 100 if avg_vol_usd > 0 else 0.0
                        
                        # حساب القوة النسبية للماكرو والتذبذب
                        ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
                        cvd_divergence = 1.0 if (price > ema200_val and cvd_trend < 0) else -1.0 if (price < ema200_val and cvd_trend > 0) else 0.0
                        micro_volatility = df['close'].tail(20).pct_change().std() * 100
                        # ====================================================================
                        # 🧬 محرك مصفوفة التجميع الصامت (Stealth Accumulation Matrix)
                        # ====================================================================
                        # استخدام tick_delta (تدفق حقيقي) وإذا لم يتوفر نستخدم CVD لضمان عدم التكرار الوهمي
                        current_cvd_usd = tick_delta if tick_delta != 0 else (cvd_trend * price)
                        
                        # حساب متوسط السيولة اليومية لآخر 30 يوم من الشموع المتوفرة
                        df_1d_macro = pd.DataFrame(candles_1d).iloc[:, :6]
                        df_1d_macro.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                        df_1d_macro['volume'] = pd.to_numeric(df_1d_macro['volume'])
                        adv_30d_usd = df_1d_macro['volume'].tail(30).mean() * price
                        
                        async with pool.acquire() as conn:
                            matrix_row = await conn.fetchrow("""
                                SELECT start_price, adv_30d_usd, accumulated_cvd_usd, 
                                EXTRACT(EPOCH FROM start_time) as start_ts 
                                FROM stealth_accumulation_matrix WHERE symbol = $1
                            """, sym)
                            
                            if matrix_row:
                                # 🔄 المرحلة الثانية: التحديث اللحظي (بدون جمع وهمي)
                                new_accum = current_cvd_usd
                                start_price_db = matrix_row['start_price']
                                days_elapsed = (time.time() - matrix_row['start_ts']) / 86400.0
                                
                                # 🗑️ التدمير الذاتي: الحيتان تقوم بـ Shakeouts حتى 15%، وإعطاء مهلة 14 يوماً للانفجار
                                if price < (start_price_db * 0.85) or days_elapsed > 14.0:
                                    await conn.execute("DELETE FROM stealth_accumulation_matrix WHERE symbol = $1", sym)
                                else:
                                    # تحديث المخزون الحقيقي
                                    await conn.execute("UPDATE stealth_accumulation_matrix SET accumulated_cvd_usd = $1, last_updated = CURRENT_TIMESTAMP WHERE symbol = $2", new_accum, sym)
                            else:
                                # 🎯 المرحلة الأولى: نقطة الصفر (Node Zero Trigger)
                                # البحث عن تصحيح 10% من أعلى قمة في آخر 7 أيام (وليست آخر 5 ساعات)
                                recent_high = df['high'].max() 
                                
                                # شروط الدخول: شذوذ مقبول (Z>1.2) + سيولة إيجابية + السعر صحح 10%
                                if current_z > 1.2 and current_cvd_usd > 0 and price < (recent_high * 0.90):
                                    await conn.execute("""
                                        INSERT INTO stealth_accumulation_matrix 
                                        (symbol, start_price, adv_30d_usd, accumulated_cvd_usd) 
                                        VALUES ($1, $2, $3, $4)
                                    """, sym, price, adv_30d_usd, current_cvd_usd)
                                    print(f"🧬 [Matrix] Node Zero Triggered for {sym}. Tracking stealth buildup...")
                        # ====================================================================

                        current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"
                        regime_map = {"Trending_Bull": 1, "Trending_Bear": 2, "Ranging": 3}
                        # تجهيز الميزات (Features) وتسجيلها
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
                            'funding_rate': float(funding_val),
                            # 🟢 حقن البيانات المؤسساتية الحقيقية للماكرو
                            'weekly_liquidity_void': float(w_void),
                            'macro_z_score_30d': float(m_z30),
                            'htf_whale_accumulation': float(htf_accum),
                            'days_since_last_expansion': float(days_exp)
                        }

                        
                        # تسجيل البيانات بصمت
                        await log_signal_for_ml(pool, sym, price, ml_features)
                        # ==========================================
                        ai_confidence, xgb_drop, xgb_time, xgb_pump = await asyncio.to_thread(predict_signal_sync, ml_features)
                        ai_confidence = round(ai_confidence, 1)

                        # 🧠 تقييم الذكاء العميق (MoE)
                        ai_confidence_deep, deep_drop, deep_time, deep_pump = await asyncio.to_thread(predict_deep_moe, ml_features)
                        ai_confidence_deep = round(ai_confidence_deep, 1) if ai_confidence_deep != -1.0 else -1.0
              
                        best_pump = max(xgb_pump, deep_pump)
                        model_name = "العميق (MoE) 🧠" if deep_pump >= xgb_pump else "الكلاسيكي (XGB) ⚙️"
                        best_confidence = ai_confidence_deep if deep_pump >= xgb_pump else ai_confidence
                        
                        if best_pump >= 50.0 and best_confidence >= 20.0:
                            
                            # نمنع تكرار إرسال نفس الجوهرة الاستثمارية خلال 48 ساعة
                            async with pool.acquire() as conn:
                                is_investor_signaled = await conn.fetchval("""
                                    SELECT 1 FROM radar_history 
                                    WHERE symbol = $1 AND last_signaled > CURRENT_TIMESTAMP - INTERVAL '48 hours'
                                """, sym)
                                
                            if not is_investor_signaled:
                                signal_id = str(uuid.uuid4())[:8]
                                
                                # حساب سعر الدخول والهدف
                                target_drop = deep_drop if deep_pump >= xgb_pump else xgb_drop
                                target_time = deep_time if deep_pump >= xgb_pump else xgb_time
                                opt_entry = price * (1 - (target_drop / 100))
                                target_price = price * (1 + (best_pump / 100))
                                
                                # حفظ البيانات في الذاكرة المؤقتة للأدمن
                                investor_pending_approvals[signal_id] = {
                                    "symbol": sym, 
                                    "price": price, 
                                    "entry": opt_entry,
                                    "target": target_price,
                                    "pump_pct": best_pump,
                                    "time_hrs": target_time,
                                    "model": model_name,
                                    "score": best_confidence
                                }

                                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="💎 إرسال كـ (استثمار نخبة) 🐋", callback_data=f"inv_app_{signal_id}")],
                                    [InlineKeyboardButton(text="❌ تجاهل", callback_data=f"inv_rej_{signal_id}")]
                                ])

                                admin_text = (
                                    f"🐋 <b>رادار المستثمرين النخبة | إنذار انفجار!</b> 🐋\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"💎 <b>العملة:</b> #{sym}\n"
                                    f"💵 السعر الحالي: ${format_price(price)}\n"
                                    f"🚀 <b>الصعود المتوقع: +{best_pump:.1f}%</b>\n"
                                    f"🎯 الهدف: ${format_price(target_price)}\n"
                                    f"🧲 أفضل دخول: ${format_price(opt_entry)}\n"
                                    f"🤖 النموذج المكتشف: {model_name} (دقة {best_confidence:.1f}%)\n"
                                    f"⏱️ الزمن المقدر: {target_time:.1f} ساعة\n\n"
                                    f"هل توافق على إرسالها كإشارة استثمارية كبرى؟"
                                )
                                
                                await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                                print(f"🐋 [Investor Radar] Caught {sym} for a {best_pump:.1f}% pump!")
                        # ====================================================================

                        # 🛑 الشرط الصارم
                        if ai_confidence >= 80.0 and ai_confidence_deep >= 70.0:


                            # التحقق مما إذا تم إرسال هذه العملة مؤخراً لتجنب الإزعاج
                            async with pool.acquire() as conn:
                                is_signaled = await conn.fetchval("""
                                    SELECT 1 FROM radar_history 
                                    WHERE symbol = $1 AND last_signaled > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                                """, sym)
                                
                            if not is_signaled:
                                # تسجيل العملة كمرسلة
                                async with pool.acquire() as conn:
                                    await conn.execute("""
                                        INSERT INTO radar_history (symbol, last_signaled)
                                        VALUES ($1, CURRENT_TIMESTAMP)
                                        ON CONFLICT (symbol) DO UPDATE SET last_signaled = CURRENT_TIMESTAMP
                                    """, sym)

                                # تجهيز البيانات للعرض
                                z_val = float(current_z)
                                avg_vol_5 = df["volume"].tail(5).mean()
                                vol_ratio = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0
                                cvd_val = float(cvd_trend * price)
                                ob_val = float(depth_data.get('bid_pressure_ratio', 1.0))
                                funding = float(funding_val)
                                adx = float(current_adx)
                                rsi = float(last_rsi)

                                # صياغة التحليل (باستخدام مصطلحات السيولة والتدفق)
                                vol_ar = f"شذوذ فوليوم مؤسساتي (Z-Score: {z_val:.2f}) مع ضخ سيولة حاد ({vol_ratio:.2f}x)." if z_val > 2 else f"انضغاط سيولة صامت (Z-Score: {z_val:.2f})."
                                cvd_ar = f"امتصاص شرائي خفي (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"ضغط بيعي وتصريف (CVD: ${cvd_val:,.0f})"
                                ob_ar = f"مع تكدس طلبات هجومي (OB: {ob_val:.2f}x)." if ob_val > 1 else f"مع سيطرة وتكدس لعروض البيع (OB: {ob_val:.2f}x)."
                                
                                if funding < -0.0005:
                                    fund_ar = "تمركز بيعي قوي مع احتمالية لتصفية البائعين (Short Squeeze)."
                                elif funding > 0.0005:
                                    fund_ar = "طمع شرائي ومعدل تمويل إيجابي ينذر بخطر تصفية المشترين (Long Squeeze)."
                                else:
                                    fund_ar = "استقرار وتوازن في معدلات تمويل عقود المشتقات."
                                
                                tech_ar = f"ADX: {adx:.1f} | RSI: {rsi:.1f}"

                                # 🧲 حساب النطاقات من الأصغر للأكبر
                                # 🧲 حساب النطاقات من الأصغر للأكبر (دخول وخروج)
                                xgb_opt_entry = price * (1 - (xgb_drop / 100))
                                deep_opt_entry = price * (1 - (deep_drop / 100))
                                min_entry = min(xgb_opt_entry, deep_opt_entry)
                                max_entry = max(xgb_opt_entry, deep_opt_entry)
                                
                                xgb_target = price * (1 + (xgb_pump / 100))
                                deep_target = price * (1 + (deep_pump / 100))
                                min_target = min(xgb_target, deep_target)
                                max_target = max(xgb_target, deep_target)

                                min_time = min(xgb_time, deep_time)
                                max_time = max(xgb_time, deep_time)

                                insight_ar = (
                                    f"🧲 <b>نطاق الشراء:</b> <code>{format_price(min_entry)}$</code> - <code>{format_price(max_entry)}$</code>\n"
                                    f"🎯 <b>نطاق الهدف:</b> <code>{format_price(min_target)}$</code> - <code>{format_price(max_target)}$</code>\n"
                                    f"⏱️ <b>الزمن المقدر:</b> <code>{min_time:.1f}h</code> - <code>{max_time:.1f}h</code>\n"
                                    f"• <b>السيولة:</b> {vol_ar}\n"
                                    f"• <b>التدفق:</b> {cvd_ar} {ob_ar}\n"
                                    f"• <b>المشتقات:</b> {fund_ar}\n"
                                    f"• <b>الهيكلة:</b> {tech_ar}"
                                )

                                vol_en = f"Institutional volume anomaly (Z-Score: {z_val:.2f}) with aggressive inflow ({vol_ratio:.2f}x)." if z_val > 2 else f"Silent liquidity compression (Z-Score: {z_val:.2f})."
                                cvd_en = f"Hidden buy absorption (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"Selling pressure & distribution (CVD: ${cvd_val:,.0f})"
                                ob_en = f"with aggressive bid stacking (OB: {ob_val:.2f}x)." if ob_val > 1 else f"with heavy ask supply dominance (OB: {ob_val:.2f}x)."
                                
                                if funding < -0.0005:
                                    fund_en = "Heavy short positioning with high (Short Squeeze) probability."
                                elif funding > 0.0005:
                                    fund_en = "Overleveraged longs with high (Long Squeeze/Correction) risk."
                                else:
                                    fund_en = "Stable futures open interest and neutral funding rates."
                                    
                                tech_en = f"ADX: {adx:.1f} | RSI: {rsi:.1f}"

                                insight_en = (
                                    f"🧲 <b>Buying Range:</b> <code>${format_price(min_entry)}</code> - <code>${format_price(max_entry)}</code>\n"
                                    f"🎯 <b>Target Range:</b> <code>${format_price(min_target)}</code> - <code>${format_price(max_target)}</code>\n"
                                    f"⏱️ <b>Est. Surge Time:</b> <code>{min_time:.1f}h</code> - <code>{max_time:.1f}h</code>\n"
                                    f"• <b>Liquidity:</b> {vol_en}\n"
                                    f"• <b>Orderflow:</b> {cvd_en} {ob_en}\n"
                                    f"• <b>Derivatives:</b> {fund_en}\n"
                                    f"• <b>Structure:</b> {tech_en}"
                                )


                                # ====================================================================
                                # 🚨 صدمة العرض المؤسساتية (للأدمن فقط - لا تحفظ في إشارة المستخدمين)
                                # ====================================================================
                                supply_shock_msg_ar = ""
                                
                                async with pool.acquire() as conn:
                                    matrix_data = await conn.fetchrow("""
                                        SELECT accumulated_cvd_usd, adv_30d_usd, EXTRACT(EPOCH FROM start_time) as start_ts 
                                        FROM stealth_accumulation_matrix WHERE symbol = $1
                                    """, sym)
                                
                                if matrix_data and matrix_data['adv_30d_usd'] > 0:
                                    shock_ratio = (matrix_data['accumulated_cvd_usd'] / matrix_data['adv_30d_usd']) * 100
                                    if shock_ratio > 5.0:
                                        days_active = (time.time() - matrix_data['start_ts']) / 86400.0
                                        supply_shock_msg_ar = f"🚨 <b>صدمة عرض مؤسساتية:</b> تم سحب {shock_ratio:.1f}% من السيولة الشهرية بهدوء منذ {days_active:.1f} أيام!\n\n"
                                # ====================================================================

                                signal_id = str(uuid.uuid4())[:8] 
                                signal_type = f"🤖 AI APEX Pick"
                                
                                # 🛡️ إدراج التحليل العادي للمستخدمين بدون بيانات صدمة العرض!
                                radar_pending_approvals[signal_id] = {
                                    "symbol": sym, "price": price, "signal": signal_type, "score": ai_confidence,
                                    "insight_ar": insight_ar, "insight_en": insight_en,
                                    "score_deep": ai_confidence_deep
                                }

                                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="✅ موافقة ونشر للمشتركين", callback_data=f"rad_app_{signal_id}")],
                                    [InlineKeyboardButton(text="❌ إلغاء وتجاهل", callback_data=f"rad_rej_{signal_id}")]
                                ])

                                # 🎯 حقن رسالة الحوت هنا فقط، لكي تظهر في شاشة الأدمن ولا تصل للمستخدم
                                # 🎯 حساب أسعار الأهداف للأدمن
                                xgb_opt_entry = price * (1 - (xgb_drop / 100))
                                xgb_target = price * (1 + (xgb_pump / 100))
                                deep_opt_entry = price * (1 - (deep_drop / 100))
                                deep_target = price * (1 + (deep_pump / 100))

                                admin_text = (
                                    f"🦅 <b>تنبيه طوارئ: قناص الذكاء الاصطناعي التقط جوهرة!</b>\n"
                                    f"🏆 <b>العملة:</b> #{sym}\n"
                                    f"💵 السعر الحالي: ${format_price(price)}\n"
                                    f"⚡ نوع التجميع: {signal_type}\n"
                                    f"🤖 <b>الذكاء الكلاسيكي (XGB):</b>\n"
                                    f"📊 جودة: <b>{ai_confidence:.1f}%</b> | ⏱️ {xgb_time:.1f}h\n"
                                    f"🧲 دخول: ${format_price(xgb_opt_entry)} | 🚀 <b>صعود: +{xgb_pump:.1f}% (${format_price(xgb_target)})</b>\n"
                                    f"🧠 <b>الذكاء العميق (MoE):</b>\n"
                                    f"📊 جودة: <b>{ai_confidence_deep:.1f}%</b> | ⏱️ {deep_time:.1f}h\n"
                                    f"🧲 دخول: ${format_price(deep_opt_entry)} | 🚀 <b>صعود: +{deep_pump:.1f}% (${format_price(deep_target)})</b>\n\n"
                                    f"{supply_shock_msg_ar}"  
                                    f"📝 <b>التحليل:</b>\n{insight_ar}\n\n"
                                    f"هل تريد الموافقة على نشرها؟"
                                )

                                await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                                print(f"🎯 [Apex Sniper] {sym} fired with AI score {ai_confidence:.1f}%!")

                    except Exception as e:
                        pass # صمت تام عند الأخطاء لتستمر الحلقة
                    
                    # 🛡️ الجدار السري لحماية السيرفر: استراحة 50 ثانية بين كل عملة وعملة
                    await asyncio.sleep(3) 
                    
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
def detect_dark_pool_vca(df, current_cvd_usd, oi_change_pct, funding_rate=0.0, on_chain_proxy_score=0.0):
    """
    [THE APEX ALPHA] Dark Pool Vacuum Coil Algorithm (DP-VCA) + Kinetic Potential
    مدمجة الآن مع طاقة السحب الشبكية أو الاحتضان.
    """
    if len(df) < 336: 
        return 0.0, None

    recent_window = df.tail(168)
    historic_window = df.iloc[-672:-168] 
    
    avg_price = recent_window['close'].mean()
    
    recent_range_pct = (recent_window['high'].max() - recent_window['low'].min()) / avg_price
    historic_range_pct = (historic_window['high'].max() - historic_window['low'].min()) / historic_window['close'].mean()
    
    recent_range_pct = max(recent_range_pct, 0.005) 
    historic_range_pct = max(historic_range_pct, 0.01)

    recent_vol = recent_window['volume'].sum()
    historic_vol_avg = historic_window['volume'].sum() / 3.0 
    
    recent_density = recent_vol / recent_range_pct
    historic_density = historic_vol_avg / historic_range_pct
    
    is_volatility_choked = recent_range_pct < (historic_range_pct * 0.6)
    is_heavy_absorption = recent_density > (historic_density * 1.8)
    
    if not (is_volatility_choked and is_heavy_absorption):
        return 0.0, None

    is_spot_driven = current_cvd_usd > (recent_window['volume'].mean() * avg_price * 0.25)
    is_stealth_derivatives = (oi_change_pct < 0.02) or (oi_change_pct >= 0.02 and funding_rate <= 0.0001)
    
    # 🧠 حساب المضاعفات الأساسية
    density_multiplier = recent_density / (historic_density + 1e-8)
    choke_ratio = historic_range_pct / (recent_range_pct + 1e-8)
    
    dynamic_base_score = (density_multiplier * 6.0) + (choke_ratio * 4.0)

    # 🧬 دمج الطاقة الكامنة (On-Chain / Incubation)
    # إذا كانت العملة محتضنة، يتم ضرب قوة الدارك بول بـ 1.5 لأن الضغط حقيقي
    kinetic_multiplier = 1.0 + (on_chain_proxy_score / 100.0) 

    vca_score = 0.0
    signal_tag = None

    if is_spot_driven and is_stealth_derivatives:
        # السكور الديناميكي يتضخم بالـ Kinetic Energy بحد أقصى 50 نقطة
        vca_score = min(50.0, (dynamic_base_score * 1.5) * kinetic_multiplier)
        signal_tag = "DARK_POOL_COIL"
        
    elif is_heavy_absorption:
        vca_score = min(30.0, dynamic_base_score * kinetic_multiplier)
        signal_tag = "DEEP_ABSORPTION"

    return round(vca_score, 1), signal_tag
async def get_institutional_vpin(symbol: str, client: httpx.AsyncClient, volume_24h: float):
    """
    [Tier-1 Quant] Dynamic Volume-Synchronized Probability of Informed Trading (VPIN)
    تطبيق أصلي لساعة الفوليوم (Volume-Clock Slicing): 
    يعتمد حجم الدلو كنسبة ثابتة من الـ ADV، ويقطع أوامر الحيتان بدقة لتوزيعها على الدلاء.
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    trades = []
    
    # 1. 🧠 تثبيت سعة الدلو (Anchored Bucket Size):
    # الدلو الصارم يمثل 2% من السيولة اليومية (Average Daily Volume). 
    # هذا يضمن أن تقييم BTC يعادل تقييم PEPE إحصائياً.
    bucket_size_usd = max(volume_24h * 0.02, 5000.0)
    
    # نهدف لجمع سيولة تكفي لملء 3 دلاء على الأقل (6% من سيولة اليوم) לקراءة اختلال حقيقي
    target_volume_usd = bucket_size_usd * 3.0
    
    try:
        base_url = get_random_binance_base()
        last_id = None
        accumulated_vol = 0.0
        
        # 🛡️ سحب بيانات التنفيذ: نطلب صفقات متتالية من الأحدث للأقدم، ثم نعكسها زمنياً
        for _ in range(5):
            await binance_rate_limit_event.wait()
            params = {"symbol": clean_sym, "limit": 1000}
            if last_id:
                params["fromId"] = last_id - 1000 
                
            res = await client.get(f"{base_url}/api/v3/aggTrades", params=params, timeout=5.0)
            if res.status_code != 200: break
            
            batch = res.json()
            if not batch: break
            
            trades = batch + trades # الترتيب الزمني الصحيح (الأقدم فالأحدث)
            last_id = batch[0]['a']
            
            batch_vol = sum(float(t['q']) * float(t['p']) for t in batch)
            accumulated_vol += batch_vol
            
            if accumulated_vol >= target_volume_usd:
                break
                
        if not trades: return 0.5 
        
        import numpy as np
        
        # 2. 🧠 خوارزمية التقطيع الفوليومي (Volume Slicing Engine)
        completed_buckets_imbalance = []
        
        current_buy_vol = 0.0
        current_sell_vol = 0.0
        
        for t in trades:
            vol = float(t['q']) * float(t['p'])
            is_sell = t['m'] # True = Taker Sell (بيع ماركت)
            
            # حلقة while هنا هي (قلب المحرك): إذا كانت الصفقة أكبر من مساحة الدلو، 
            # سيتم قصها، ملء الدلو، حفظه، ثم وضع الباقي في الدلو التالي!
            while vol > 0:
                current_total = current_buy_vol + current_sell_vol
                space_left = bucket_size_usd - current_total
                
                # نأخذ الحجم الأصغر: إما كل الصفقة، أو ما يملأ الدلو الحالي فقط
                fill_vol = min(vol, space_left)
                
                if is_sell:
                    current_sell_vol += fill_vol
                else:
                    current_buy_vol += fill_vol
                
                vol -= fill_vol # خصم ما تم وضعه في الدلو
                
                # إذا امتلأ الدلو (مع التسامح مع الكسور العشرية)
                if (current_buy_vol + current_sell_vol) >= bucket_size_usd - 1e-4:
                    imbalance = abs(current_buy_vol - current_sell_vol)
                    completed_buckets_imbalance.append(imbalance)
                    # تصفير الدلو للبدء من جديد
                    current_buy_vol = 0.0
                    current_sell_vol = 0.0

        # 3. ⚖️ حساب مؤشر VPIN النهائي
        if not completed_buckets_imbalance:
            # حالة استثنائية: إذا كانت العملة ميتة جداً ولم يمتلئ حتى دلو واحد
            total_collected = current_buy_vol + current_sell_vol
            if total_collected < 1000.0: return 0.5 # حيادي
            return float(abs(current_buy_vol - current_sell_vol) / total_collected)
            
        # VPIN = مجموع الاختلالات / إجمالي الحجم في كل الدلاء المكتملة
        vpin = np.sum(completed_buckets_imbalance) / (len(completed_buckets_imbalance) * bucket_size_usd)
        return float(vpin)
        
    except Exception as e:
        print(f"VPIN Quant Engine Error: {e}")
        return 0.5



import numpy as np
import onnxruntime as ort
import os

# ====================================================================
# 🧠 THE DEEP LEARNING ENGINE (MoE - ONNX) - COMPLETELY SEPARATED
# ====================================================================
DEEP_EXPERTS = {}

def load_deep_experts():
    global DEEP_EXPERTS
    try:
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 1
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        if os.path.exists("model_bull.onnx"):
            DEEP_EXPERTS[1] = ort.InferenceSession("model_bull.onnx", sess_options)
        if os.path.exists("model_bear.onnx"):
            DEEP_EXPERTS[2] = ort.InferenceSession("model_bear.onnx", sess_options)
        if os.path.exists("model_chop.onnx"):
            DEEP_EXPERTS[3] = ort.InferenceSession("model_chop.onnx", sess_options)
            
    except Exception:
        pass # صمت تام لكي لا يزعج سيرفرك إذا لم تكن الملفات موجودة بعد

load_deep_experts()

def predict_deep_moe(features: dict):
    if not DEEP_EXPERTS: return -1.0, 0.0, 0.0, 0.0 
    
    try:
        regime = int(features.get('market_regime', 0))
        expert_id = regime if regime in DEEP_EXPERTS else 3 
        if expert_id not in DEEP_EXPERTS: return -1.0, 0.0, 0.0, 0.0

        expert_model = DEEP_EXPERTS[expert_id]
        
        # 🧠 [Quant Scaling]: تثبيت البيانات وإجبارها على أن تكون Stationary & Bounded
        # الدالة tanh تحصر الأرقام المفتوحة بين -1 و 1 بمرونة.
        # القسمة على 100 تحصر النسب المئوية بين 0 و 1.
        
        # 🧠 الحل المؤسساتي (Winsorization & Safe Activation):
        # حصر القيم المتطرفة (Clipping) قبل إدخالها لدالة tanh لمنع العمى الرياضي للنموذج
        scaled_features = [
            np.tanh(np.clip(float(features.get('sp500_trend', 0.0)), -15.0, 15.0) / 5.0), 
            float(features.get('sentiment_score', 50.0)) / 100.0,
            np.tanh(np.clip(float(features.get('z_score', 0.0)), -15.0, 15.0) / 3.0),               # 👈 حماية من الـ Wash Trading
            np.tanh(np.clip(float(features.get('cvd_to_vol_ratio', 0.0)), -100.0, 100.0) / 20.0),   # 👈 حماية من طفرات السيولة الوهمية
            np.clip(float(features.get('ofi_imbalance', 0.0)), -1.0, 1.0),
            np.tanh(np.clip(float(features.get('ob_skewness', 1.0)) - 1.0, -5.0, 5.0)),
            np.clip(float(features.get('whale_inflow', 0.0)) / 5.0, 0.0, 2.0),                      # 👈 حماية من أرقام الحيتان الفلكية
            np.clip(float(features.get('adx', 0.0)) / 100.0, 0.0, 1.0),
            np.clip(float(features.get('rsi', 50.0)) / 100.0, 0.0, 1.0),
            np.tanh(np.clip(float(features.get('micro_volatility', 0.0)), 0.0, 50.0) / 10.0),
            np.clip(float(features.get('cvd_divergence', 0.0)), -1.0, 1.0),
            np.tanh(np.clip(float(features.get('funding_rate', 0.0)), -0.05, 0.05) * 1000.0),       # 👈 حماية من أخطاء عقود المشتقات
            np.tanh(np.clip(float(features.get('weekly_liquidity_void', 0.0)), -50.0, 50.0) / 10.0),
            np.tanh(np.clip(float(features.get('macro_z_score_30d', 0.0)), -15.0, 15.0) / 3.0),
            np.clip(float(features.get('htf_whale_accumulation', 0.0)) / 100.0, -1.0, 1.0),
            np.tanh(np.clip(float(features.get('days_since_last_expansion', 0.0)), 0.0, 100.0) / 30.0)
        ]

        input_vector = np.array([scaled_features], dtype=np.float32)

        input_name = expert_model.get_inputs()[0].name
        output_name = expert_model.get_outputs()[0].name
        
        prediction = expert_model.run([output_name], {input_name: input_vector})[0]
        # 🧠 استخراج الرؤوس الأربعة
        if prediction.ndim > 1:
            raw_score = float(prediction[0][0])
            entry_drop_pct = float(prediction[0][1])
            time_to_surge_hours = float(prediction[0][2])
            expected_pump_pct = float(prediction[0][3]) # 🚀 الصعود العميق
        else:
            raw_score = float(prediction[0])
            entry_drop_pct = float(prediction[1])
            time_to_surge_hours = float(prediction[2])
            expected_pump_pct = float(prediction[3]) # 🚀 الصعود العميق
            
        quality_score = round(max(0.0, min(100.0, ((raw_score + 1.0) / 2.0) * 100.0)), 1)
        
        return quality_score, entry_drop_pct, time_to_surge_hours, expected_pump_pct
    except:
        return -1.0, 0.0, 0.0, 0.0

import os
import asyncio
import onnxruntime as ort
from huggingface_hub import hf_hub_download

# ====================================================================
# 🦅 HEDGE FUND GRADE: Atomic Zero-Downtime Model Hot-Swapping
# ====================================================================
DEEP_EXPERTS = {}

# 🚨 ضع هنا (نفس التي استخدمتها في مصنع التدريب)import os

# السحب الآمن للتوكن من بيئة السيرفر بدلاً من كتابته صراحة
HF_TOKEN = os.getenv("HF_TOKEN")  
HF_REPO_ID = "Djdhdhdh827237/quant-moe-models" # اسم المستودع عادي يكون مكشوف


async def moe_hot_swap_worker():
    """
    عامل الاستبدال الحي: يجلب الأوزان الجديدة من السحابة ويركبها في الذاكرة 
    بدون أي انقطاع في تيار بيانات بايننس (Zero Slippage/Downtime).
    """
    global DEEP_EXPERTS
    await asyncio.sleep(20) # ننتظر استقرار السيرفر عند الإقلاع
    print("☁️ [MoE Ghost Sync] Hedge Fund Model Synchronization is Online...")

    while True:
        try:
            print("🔄 [MoE Ghost Sync] Checking Hugging Face for fresh Brain Weights...")
            new_experts = {}
            
            # إعدادات الـ ONNX بأعلى كفاءة للمعالجات (CPUs)
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 1
            sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

            models_to_fetch = {1: "model_bull.onnx", 2: "model_bear.onnx", 3: "model_chop.onnx"}

            # جلب النماذج في خيط خلفي (Background Thread) لمنع تجميد البوت اللحظي
            for regime_id, model_name in models_to_fetch.items():
                file_path = await asyncio.to_thread(
                    hf_hub_download,
                    repo_id=HF_REPO_ID,
                    filename=model_name,
                    token=HF_TOKEN,
                    force_download=True # إجبار السيرفر على جلب أحدث نسخة دائماً
                )
                # تحميل النموذج الجديد في الذاكرة المعزولة أولاً
                new_experts[regime_id] = ort.InferenceSession(file_path, sess_options)

            # ⚛️ الاستبدال الذرّي (Atomic Swap): 
            # نستبدل القاموس القديم بالجديد في عملية واحدة لا تتجزأ (Thread-Safe)
            if len(new_experts) == 3:
                DEEP_EXPERTS = new_experts
                print("✅ [MoE Ghost Sync] Atomic Swap Complete! New Brains injected successfully.")

        except Exception as e:
            # صمت مؤسساتي: إذا فشل الاتصال، لا نوقف البوت، بل نستمر باستخدام الأوزان القديمة
            print(f"⚠️ [MoE Ghost Sync] Connection failed, keeping old models. Error: {e}")

        # ينام لمدة 12 ساعة، ثم يستيقظ لجلب أي تحديثات جديدة من مصنع Kaggle
        await asyncio.sleep(43200) 
def calculate_macro_htf_features(candles_1d, candles_1w):
    """
    [Institutional Grade] HTF Macro Features Engine
    يحسب 4 أبعاد معقدة للسيولة باستخدام الفريم اليومي والأسبوعي.
    """
    # قيم افتراضية آمنة في حال فشل جلب البيانات
    macro_z_30d = 0.0
    days_since_expansion = 0.0
    htf_accumulation = 0.0
    weekly_void_score = 0.0

    try:
        # --- معالجة الفريم اليومي ---
        if candles_1d and len(candles_1d) >= 30:
            df_1d = pd.DataFrame(candles_1d).iloc[:, :6]
            df_1d.columns = ["timestamp", "volume", "close", "high", "low", "open"]
            df_1d[["close", "high", "low", "volume"]] = df_1d[["close", "high", "low", "volume"]].apply(pd.to_numeric)
            
            # 1. حساب الانحراف المعياري لـ 30 يوم (macro_z_score_30d)
            sma_30 = df_1d['close'].rolling(30).mean().iloc[-1]
            std_30 = df_1d['close'].rolling(30).std(ddof=0).iloc[-1]
            if std_30 > 0:
                macro_z_30d = float((df_1d['close'].iloc[-1] - sma_30) / std_30)
                macro_z_30d = max(-10.0, min(10.0, macro_z_30d)) # حماية من القيم الشاذة

            # 2. حساب أيام الانضغاط (days_since_last_expansion)
            df_1d['tr'] = df_1d['high'] - df_1d['low']
            df_1d['atr'] = df_1d['tr'].rolling(14).mean()
            # نعرف الانفجار بأنه شمعة مداها أكبر من 2.5 ضعف متوسط المدى
            df_1d['is_expansion'] = df_1d['tr'] > (df_1d['atr'] * 2.5)
            
            expansion_indices = np.where(df_1d['is_expansion'])[0]
            if len(expansion_indices) > 0:
                last_exp_idx = expansion_indices[-1]
                days_since_expansion = float(len(df_1d) - 1 - last_exp_idx)
            else:
                days_since_expansion = 30.0 # الحد الأقصى إذا لم يحدث انفجار منذ شهر
            
            # 3. تجميع الحيتان الكلي (htf_whale_accumulation) باستخدام Chaikin Money Flow (CMF) المطور
            # يعطي وزناً للفوليوم بناءً على إغلاق الشمعة (بالقرب من القمة = شراء قوي)
            money_flow_mult = ((df_1d['close'] - df_1d['low']) - (df_1d['high'] - df_1d['close'])) / (df_1d['high'] - df_1d['low'] + 1e-8)
            df_1d['cmf_vol'] = money_flow_mult * df_1d['volume']
            htf_accumulation = float((df_1d['cmf_vol'].tail(14).sum() / (df_1d['volume'].tail(14).sum() + 1e-8)) * 100.0)

        # --- معالجة الفريم الأسبوعي ---
        if candles_1w and len(candles_1w) >= 3:
            df_1w = pd.DataFrame(candles_1w).iloc[:, :6]
            df_1w.columns = ["timestamp", "volume", "close", "high", "low", "open"]
            df_1w[["high", "low"]] = df_1w[["high", "low"]].apply(pd.to_numeric)
            
            # 4. حساب الفجوات الأسبوعية (weekly_liquidity_void) - SMC Fair Value Gaps
            # نبحث في آخر 10 أسابيع عن فجوات لم يتم إغلاقها
            void_intensity = 0.0
            lookback = min(10, len(df_1w) - 2)
            
            for i in range(len(df_1w)-1, len(df_1w)-lookback-1, -1):
                c1_high = df_1w['high'].iloc[i-2]
                c3_low = df_1w['low'].iloc[i]
                c1_low = df_1w['low'].iloc[i-2]
                c3_high = df_1w['high'].iloc[i]

                if c1_high < c3_low: # Bullish FVG (فجوة شرائية مفتوحة)
                    gap_pct = (c3_low - c1_high) / c1_high
                    void_intensity += (gap_pct * 100)
                elif c1_low > c3_high: # Bearish FVG (فجوة بيعية مفتوحة)
                    gap_pct = (c1_low - c3_high) / c3_high
                    void_intensity -= (gap_pct * 100)
            
            weekly_void_score = float(void_intensity)

    except Exception as e:
        print(f"⚠️ [Macro Engine] Error calculating HTF features: {e}")

    return weekly_void_score, macro_z_30d, htf_accumulation, days_since_expansion
# ====================================================================
# 🐋 THE INSTITUTIONAL TOKENOMICS ENGINE (Emission Overhang)
# ====================================================================
# ====================================================================
# 🐋 THE INSTITUTIONAL TOKENOMICS ENGINE (Emission Overhang) [THE REAL FIX]
# ====================================================================
TOKENOMICS_CACHE = {}

async def evaluate_tokenomics_overhang(symbol: str, client: httpx.AsyncClient):
    clean_sym = symbol.replace("USDT", "")
    current_time = time.time()
    
    # الذاكرة المؤقتة لمنع استنزاف حدود الـ API (كل 24 ساعة للعملة)
    if clean_sym in TOKENOMICS_CACHE and (current_time - TOKENOMICS_CACHE[clean_sym]['ts']) < 86400:
        return TOKENOMICS_CACHE[clean_sym]['data']
        
    result = {"is_vetoed": False, "penalty_multiplier": 1.0, "reason": "", "tag": ""}

    # ==========================================================
    # 🛡️ المحرك الأول: التضخم الكلي (Macro FDV) باستخدام CoinMarketCap
    # ==========================================
    if CMC_KEY:
        try:
            headers = {"X-CMC_PRO_API_KEY": CMC_KEY, "Accept": "application/json"}
            cmc_url = f"https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest?symbol={clean_sym}"
            # Time out قصير لمنع تعليق الرادار
            cmc_res = await client.get(cmc_url, headers=headers, timeout=3.0)
            
            if cmc_res.status_code == 200:
                data = cmc_res.json()
                if 'data' in data and clean_sym in data['data']:
                    coin_info = data['data'][clean_sym][0]
                    circulating = float(coin_info.get('circulating_supply') or 1.0)
                    total_supply = float(coin_info.get('total_supply') or circulating)
                    
                    fdv_ratio = total_supply / circulating
                    
                    # العقاب الأساسي: إذا كانت العملة لم تفك سوى جزء بسيط من معروضها
                    if fdv_ratio > 6.0:
                        result["penalty_multiplier"] = 0.6 
                        result["tag"] = "Toxic_FDV_Overhang"
        except Exception:
            pass # عزل الخطأ بصمت تام لكي لا يتعطل المحرك الثاني
    # ==========================================================
    # ⏱️ المحرك الثاني: مؤقت الانفجار (The Cliff Timer) باستخدام CryptoRank
    # هذا هو الحل الحقيقي لمعرفة "متى" سيحدث فك الارتباط بدقة
    # ==========================================
    if CRYPTORANK_API_KEYS:
        # 🔄 محرك التبديل الذكي (API Key Rotation Engine)
        for api_key in CRYPTORANK_API_KEYS:
            try:
                # 1. جلب ID العملة الداخلي في CryptoRank
                cr_url = f"https://api.cryptorank.io/v1/currencies?symbol={clean_sym}&api_key={api_key}"
                cr_res = await client.get(cr_url, timeout=4.0)
                
                # إذا استنفدنا الرصيد اليومي أو كان المفتاح معطلاً، ننتقل للمفتاح التالي فوراً
                if cr_res.status_code in [429, 403, 401]:
                    print(f"⚠️ [CryptoRank] المفتاح {api_key[:8]}... استنفد الرصيد. التبديل للذي يليه...")
                    continue
                
                if cr_res.status_code == 200:
                    cr_data = cr_res.json()
                    if cr_data.get('data') and len(cr_data['data']) > 0:
                        coin_id = cr_data['data'][0]['id']
                        
                        # 2. جلب جدول فك الارتباط اللحظي (Vesting/Unlocks)
                        unlocks_url = f"https://api.cryptorank.io/v1/vesting/{coin_id}?api_key={api_key}"
                        unlocks_res = await client.get(unlocks_url, timeout=4.0)
                        
                        if unlocks_res.status_code in [429, 403, 401]:
                            print(f"⚠️ [CryptoRank] المفتاح {api_key[:8]}... استنفد الرصيد أثناء جلب الجدول. التبديل...")
                            continue
                        
                        if unlocks_res.status_code == 200:
                            vesting_data = unlocks_res.json().get('data', {})
                            next_unlock = vesting_data.get('nextUnlock', {})
                            
                            if next_unlock:
                                # حساب الأيام المتبقية وحجم الفك بدقة
                                unlock_date = pd.to_datetime(next_unlock.get('date'))
                                days_until_unlock = (unlock_date - pd.Timestamp.utcnow()).days
                                unlock_pct = float(next_unlock.get('percentOfCirculatingSupply', 0.0))
                                
                                # 🛑 جدار الإعدام المؤسساتي (The True Veto): 
                                # إعدام الإشارة فوراً إذا كان هناك فك لأكثر من 3% خلال الـ 14 يوم القادمة
                                if 0 <= days_until_unlock <= 14.0 and unlock_pct >= 3.0:
                                    result["is_vetoed"] = True
                                    result["reason"] = f"Massive Unlock ({unlock_pct:.1f}%) in {days_until_unlock} Days"
                                    result["tag"] = "Exit_Liquidity_Trap"
                    
                    # 🎯 كسر الحلقة (Break): 
                    # إذا وصلنا هنا، يعني أن الاتصال نجح وتمت قراءة البيانات (أو العملة لا تملك فك ارتباط)
                    # لذلك لا داعي لاستهلاك باقي المفاتيح عبثاً.
                    break 
                    
            except Exception as e:
                # خطأ في الاتصال بالشبكة (TimeOut)، نتخطاه ونجرب المفتاح التالي لعل السيرفر يستجيب
                print(f"⚠️ [CryptoRank] خطأ اتصال/شبكة في المفتاح {api_key[:8]}... : {str(e)}")
                continue 

    # حفظ النتيجة في الذاكرة لمنع تكرار الاتصال العبثي للـ API
    TOKENOMICS_CACHE[clean_sym] = {'ts': current_time, 'data': result}
    return result

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
            tags = [] 
            toxicity_score = 0.0  # 🧠 المحرك الجديد: تجميع سمية السيولة

            # ====================================================================
            # 🛡️ THE RUTHLESS FILTER: Liquidity Absorption Ratio (LAR)
            # ====================================================================
            current_vwap_z, current_vwap_price = calculate_vwap_zscore(df, window=24)
            current_high = df["high"].iloc[-1]
            current_low = df["low"].iloc[-1]
            candle_spread_pct = ((current_high - current_low) / current_low) * 100
            
            avg_spread = (abs(df["high"] - df["low"]) / df["low"]).tail(5).mean() * 100
            safe_spread = max(candle_spread_pct, avg_spread, 0.15) 
            lar_score = current_z / safe_spread
            
            # ==========================================================
            # 🛑 المرحلة الأولى: تراكم المخاطر الأساسية (Soft Penalty Accumulation)
            # ==========================================================
            
            # 🧬 1. فحص اقتصاديات التوكن (Tokenomics Exit-Liquidity Check)
            tokenomics_risk = await evaluate_tokenomics_overhang(symbol, client)
            if tokenomics_risk['is_vetoed']:
                tags.append(tokenomics_risk['tag'])
                toxicity_score += 45.0 # عقاب شديد بدلاً من القتل
            
            current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"
            volatility_state = market_regime['volatility'] if isinstance(market_regime, dict) else "Normal"

            z_threshold = 2.0 if volatility_state == "Low_Vol" else (3.0 if volatility_state == "High_Vol" else 2.5)
            lar_threshold = 0.6 if current_regime_trend == "Trending_Bull" else 0.8

            # أ. الهروب من الفومو (Late FOMO Penalty):
            if current_z > z_threshold and candle_spread_pct > 4.0:
                tags.append("Late_FOMO_Pump")
                toxicity_score += 30.0 

            # ب. فلتر العملات الميتة (Dead Asset Penalty):
            if lar_score < lar_threshold and current_z < (z_threshold - 1.0):
                tags.append("Dead_Asset_Risk")
                toxicity_score += 40.0 

            # إضافة الـ Tag للعملات القوية وتخفيف السمية
            if lar_score >= 2.0 and current_z > 1.5:
                tags.append("High_Liquidity_Absorption")
                toxicity_score = max(0.0, toxicity_score - 15.0) 

            # ==========================================================
            # 🟢 المرحلة الثانية: فحص المشتقات وجلب البيانات المؤسساتية
            # ==========================================================
            spot_lead_score = await detect_spot_perp_divergence(symbol, client)
            if spot_lead_score < -3.0:
                tags.append("Spot_Dumping_Fakeout")
                toxicity_score += 35.0 

            old_price_val = df["close"].iloc[-3] if len(df) > 3 else df["open"].iloc[0]
            approx_24h_vol_usd = df["volume"].tail(24).sum() * price 
            # ==========================================================
            # 🚀 جلب البيانات المؤسساتية لصفقات السوينغ (Swing-Grade Data Fetching)
            # تم توسيع العدسة لتتجاهل ضجيج الدقائق وتركز على الساعات لتنقية عقل الـ AI
            # ==========================================================
            
            # 1. إجبار الـ CVD على قراءة الفريم الأكبر (بتمرير "1d" ستعمل الدالة على 15 دقيقة بدلاً من 1m)
            micro_cvd_boost, micro_cvd_signal, micro_cvd_trend = await get_micro_cvd_absorption(f"{symbol}USDT", client, "1d")
            
            global_ob_pressure = await get_aggregated_orderbook(client, symbol)
            depth_data = await analyze_orderbook_spoofing_instant(symbol, client, price)
            
            # 2. 🧠 التعديل الأهم: قراءة تدفق الأوامر (Orderflow) لآخر 4 ساعات (240 دقيقة) بدلاً من 15 دقيقة!
            tick_delta, tick_buy, tick_sell, limit_abs_signal = await get_institutional_orderflow(f"{symbol}USDT", client, minutes=240)
            
            _, futures_signal, funding_val, oi_change_pct, _ = await get_futures_liquidity(symbol, client, price, old_price_val)
            whale_score, phantom_tags = await detect_phantom_liquidity_ws(symbol, client, price, approx_24h_vol_usd)
            rs_score = await detect_btc_relative_strength(symbol, client)
                        # 🚀 استدعاء فلتر الحقيقة VPIN (قراءة 10,000 صفقة)            # 🚀 استدعاء فلتر الحقيقة VPIN (مثبت بـ ADV لضمان الدقة)
            vpin_score = await get_institutional_vpin(symbol, client, approx_24h_vol_usd)
            print(f"🔬 [VPIN Engine] {symbol} | Toxicity Score: {vpin_score:.2f}")
            tags.extend(phantom_tags)
            if limit_abs_signal == "Limit_Absorption": tags.append("Limit_Absorption")
            if micro_cvd_signal == "Micro_Silent_Accumulation": tags.append("Whale_CVD")
            if futures_signal: tags.append(futures_signal)
                        # 🧠 جلب شموع 1D السريعة (40 شمعة فقط) لحساب متغيرات الماكرو            # ==========================================================
            # 🧠 جلب بيانات الماكرو للذكاء الاصطناعي (أقل استهلاك للـ API)
            # ==========================================================
            candles_1d_macro = await get_candles_binance(f"{symbol}USDT", "1d", limit=40)
            candles_1w_simulated = []
            
            if candles_1d_macro and len(candles_1d_macro) >= 14:
                df_daily = pd.DataFrame(candles_1d_macro).iloc[:, :6]
                df_daily.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                
                # 🟢 التعديل الجراحي: تحويل النصوص إلى أرقام قبل التجميع لمنع لصق النصوص
                df_daily[["open", "high", "low", "close", "volume"]] = df_daily[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors='coerce')
                
                df_daily['datetime'] = pd.to_datetime(df_daily['timestamp'].astype(float), unit='s')
                df_daily.set_index('datetime', inplace=True)
                
                weekly_df = df_daily.resample('W-MON').agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 
                    'volume': 'sum', 'timestamp': 'first'
                }).dropna()
                candles_1w_simulated = weekly_df.values.tolist()

            w_void, m_z30, htf_accum, days_exp = await asyncio.to_thread(
                calculate_macro_htf_features, candles_1d_macro, candles_1w_simulated
            )
            # ==========================================================


            # 🚀 استدعاء خوارزمية الدارك بول (VCA) وغرفة الاحتضان
            # 🚀 استدعاء خوارزمية الدارك بول (VCA) وغرفة الاحتضان
            # ----------------------------------------------------
            # 🧬 1. فحص الانتماء لغرفة الاحتضان (The Incubation Synergy)
            is_incubated = f"{symbol}USDT" in INCUBATION_MATRIX
            # 🛡️ حاجز الأمان: لا نمنح علاوة الاحتضان والماكرو إلا إذا كانت السيولة اللحظية (CVD) للعملة نفسها إيجابية
            incubation_bonus = 20.0 if (is_incubated and micro_cvd_trend > 0) else 0.0

            
            if is_incubated:
                tags.append("Incubated_Macro_Coil")
                print(f"🧬 [Synergy] {symbol} triggered from Incubation Room! Enhancing VCA...")

            # 🚀 نمرر incubation_bonus كـ On-chain/Kinetic proxy
            vca_bonus_score, vca_tag = detect_dark_pool_vca(df, micro_cvd_trend * price, oi_change_pct, funding_val, on_chain_proxy_score=incubation_bonus)
            if vca_tag: tags.append(vca_tag)

            # ⚙️ تجهيز الهيكلة الفنية (Bollinger & Sweeps) للتقييم
            dyn_window = get_dynamic_window(df, base_window=20)
            sma = df["close"].rolling(dyn_window).mean()
            std = df["close"].rolling(dyn_window).std(ddof=0)
            bb_width = (4 * std) / sma
            avg_bb_width = bb_width.rolling(dyn_window * 5).mean().iloc[-1]
            current_bb_width = bb_width.iloc[-1]
            
            recent_low_20 = df["low"].iloc[-21:-1].min()
            current_low = df["low"].iloc[-1]
            current_close = df["close"].iloc[-1]
            is_liquidity_sweep = (current_low < recent_low_20) and (current_close > recent_low_20)
            # ====================================================================
            # 🧠 المرحلة الثالثة: محرك التسعير الكمي المضاعف (True Multiplicative Quant Engine)
            # ====================================================================
            # 1. حساب الفوليوم الدولاري الدقيق لكل شمعة لتجنب تضخم السعر الراجع (Retroactive Inflation)
            df["vol_usd"] = df["volume"] * df["close"]
            avg_vol_usd_20 = df["vol_usd"].tail(20).mean()
            avg_vol_usd_20 = max(avg_vol_usd_20, 1.0) # حماية من القسمة على صفر
            
            # --- البُعد الأول: الاتجاه (Directional Conviction) ---
            # أ. تقييم السيولة (CVD)
            real_cvd_usd_eval = float(micro_cvd_trend) * price 
            cvd_ratio = real_cvd_usd_eval / avg_vol_usd_20
            
            cvd_score = quant_sigmoid_score(cvd_ratio, sensitivity=6.0, limit=100.0)
            spot_lead_bonus = max(0.0, min(20.0, spot_lead_score * 2.0))
            dir_cvd = min(100.0, cvd_score + spot_lead_bonus)
            
            # ب. تقييم المشتقات (Derivatives & Phantom Liquidity)
            # تحويل سيولة الحيتان المخفية (Phantom) إلى نسبة مئوية تضاعف قوة المشتقات
            phantom_multiplier = 1.0 + (quant_sigmoid_score(whale_score, sensitivity=0.5, limit=50.0) / 100.0)
            funding_sensitivity = -3000.0 if (futures_signal == "OI_Rising" and oi_change_pct > 0.02) else -1000.0
            base_deriv = quant_sigmoid_score(funding_val, sensitivity=funding_sensitivity, limit=100.0)
            dir_deriv = min(100.0, base_deriv * phantom_multiplier)
            # 🛡️ ج. تقييم الهيكلة الفنية (المحرك المصحح لاكتشاف السكاكين الساقطة)
            tech_base = 50.0
            squeeze_ratio = current_bb_width / (avg_bb_width + 1e-8) if not pd.isna(avg_bb_width) and avg_bb_width > 0 else 1.0
            
            if squeeze_ratio < 0.8: 
                # مكافأة التجميع والانضغاط (هنا تتألق العملات الميتة قبل الانفجار)
                tech_base += quant_sigmoid_score(1.0 - squeeze_ratio, sensitivity=5.0, limit=30.0)
            elif squeeze_ratio > 1.3:
                # 🛑 عقاب التوسع العنيف (سحق العملات المنهارة مثل ORCA)
                penalty = quant_sigmoid_score(squeeze_ratio - 1.0, sensitivity=5.0, limit=30.0)
                tech_base -= penalty
            
            if is_liquidity_sweep and real_cvd_usd_eval > 0: 
                sweep_strength = min(1.0, real_cvd_usd_eval / (avg_vol_usd_20 * 0.5)) 
                tech_base += (20.0 * sweep_strength)
                
            tech_base += quant_sigmoid_score(rs_score, sensitivity=0.5, limit=20.0)
            dir_tech = max(0.0, min(100.0, tech_base)) # حماية من النزول تحت الصفر

            # 🧠 محرك الأوزان الديناميكية (Dynamic Information-Theoretic Weighting)
            total_strength = dir_cvd + dir_deriv + dir_tech + 1e-8
            
            # حساب الأوزان ذاتياً: المؤشر الأقوى يأخذ الوزن الأكبر
            w_cvd = dir_cvd / total_strength
            w_deriv = dir_deriv / total_strength
            w_tech = dir_tech / total_strength
            
            # دمج التقييم بناءً على الأوزان الذكية الجديدة
            directional_score = (dir_cvd * w_cvd) + (dir_deriv * w_deriv) + (dir_tech * w_tech)
            
            # 🎯 مكافأة الإجماع: إذا اتفقت السيولة مع المشتقات بقوة، نرفع التقييم
            if dir_cvd > 65.0 and dir_deriv > 65.0:
                directional_score = min(100.0, directional_score * 1.15)


            # --- البُعد الثاني: التوقيت (Timing & Execution) ---
            imbalance = depth_data.get('imbalance', 0.0)
            is_spoofed_flag = depth_data.get('is_spoofed', False)
            is_hollow_flag = depth_data.get('is_hollow', False)

            ob_base = quant_sigmoid_score(imbalance, sensitivity=4.0, limit=100.0)
            global_ob_bonus = min(20.0, math.log1p(max(0, global_ob_pressure - 1.0)) * 10.0)
            timing_score = min(100.0, ob_base + global_ob_bonus)
            
            # 🧠 التعامل الذكي مع التلاعب (Smart Spoofing Logic)
            if is_hollow_flag:
                timing_score *= 0.4 # عقاب صارم لفراغ السيولة (خطر الانزلاق)
            elif is_spoofed_flag:
                # إذا كان التلاعب يدعم الاتجاه (تجميع صانع سوق)، العقاب خفيف
                if (imbalance > 0.1 and current_regime_trend == "Trending_Bull") or (imbalance < -0.1 and current_regime_trend == "Trending_Bear"):
                    timing_score *= 0.8 
                else:
                    # تلاعب عكس الاتجاه (فخ سيولة حقيقي)
                    timing_score *= 0.3 

            # --- البُعد الثالث: الفوليوم كبوابة حتمية (The Pure Volume Gatekeeper) ---
            # 🛡️ الحفاظ على نزاهة Z-Score وعدم تلوثه بأي إضافات
            safe_z_calc = max(-10.0, min(10.0, 2.0 * (current_z - 1.5)))
            raw_vol_multiplier = 1.0 / (1.0 + math.exp(-safe_z_calc))
            
            # 🚨 إصلاح ثغرة (Negative Exponent Underflow) وحماية القسمة
            safe_lar = max(lar_score, 0.05) 
            if safe_lar < 0.1:
                vol_penalty_factor = 0.1 # خنق تام إذا كان الامتصاص معدوماً
            elif safe_lar < 1.0:
                vol_penalty_factor = math.exp(-2.0 / safe_lar)
            else:
                vol_penalty_factor = 1.0
                
            volume_multiplier = raw_vol_multiplier * vol_penalty_factor
            # --- البُعد الرابع: محفزات الألفا الهيكلية (Structural Alpha Multipliers) ---
            # --- البُعد الرابع: محفزات الألفا الهيكلية ووقود الماكرو (Structural Alpha & Macro Fuel) ---
            vca_multiplier = 1.0 + (vca_bonus_score / 100.0) 
            incubation_multiplier = 1.0 + (incubation_bonus / 100.0)
            
            # 🌍 1. حقن سيولة الماكرو (Global Liquidity Injection):
            onchain_macro_fuel = 1.0
            if is_incubated and MACRO_CACHE.get("onchain_liquidity_score", 0.0) > 15.0 and micro_cvd_trend > 0:
                onchain_macro_fuel = 1.15 # 15% دفعة قوية للعملة
            # 🪐 2. المحرك المؤسساتي لتوزيع السيولة القطاعية (Dynamic Altcoin Regime Flow):
            # تم استبدال الكابح القاطع بدالة نعومة أسّية (Exponential Smoothing) لمنع التذبذب الحاد للسكور
            alt_regime = MACRO_CACHE.get("alt_regime_score", 50.0)
            alt_flow_multiplier = 1.0
            
            if symbol != "BTC": # لا نطبق هذا الفلتر على البيتكوين نفسه
                if alt_regime > 50.0:
                    # 🚀 منحنى تصاعدي مرن: يبدأ بهدوء من 50 ويصل لأقصى مكافأة (+15%) عند 80 فأكثر
                    boost_ratio = min((alt_regime - 50.0) / 30.0, 1.0)
                    # القوة 1.5 تضمن أن التغيير حول مستوى 50 يكون بطيئاً جداً، ويتسارع كلما اقتربنا من 80
                    alt_flow_multiplier = 1.0 + (0.15 * (boost_ratio ** 1.5))
                    
                    # لا نعطي إشارة بوجود "موسم عملات بديلة" إلا إذا كان المحفز مؤثراً فعلاً (> 8%)
                    if alt_flow_multiplier >= 1.08: 
                        tags.append("Alt_Regime_Tailwind")
                        
                elif alt_regime < 50.0:
                    # 🩸 منحنى هبوطي مرن: يبدأ بهدوء من 50 ويصل لأقصى عقاب (-25%) عند 20 فأقل
                    drop_ratio = min((50.0 - alt_regime) / 30.0, 1.0)
                    alt_flow_multiplier = 1.0 - (0.25 * (drop_ratio ** 1.5))
                    
                    # لا نطلق تحذير "ابتلاع البيتكوين للسيولة" إلا إذا كان العقاب مؤثراً فعلاً (> 12%)
                    if alt_flow_multiplier <= 0.88: 
                        tags.append("BTC_Liquidity_Vacuum")

            # 🛑 كابح الدارك بول النهائي: 
            if squeeze_ratio < 1.2:
                # نضرب المحفزات الذاتية بوقود الماكرو وتدفق القطاع (Alt Flow Multiplier)
                structural_alpha_boost = min(1.60, vca_multiplier * incubation_multiplier * onchain_macro_fuel * alt_flow_multiplier)
            else:
                # إذا كانت العملة تنزف، نلغي المحفزات الإيجابية، لكن نُبقي على عقاب امتصاص البيتكوين إن وُجد
                structural_alpha_boost = min(1.0, alt_flow_multiplier)
            # --- ⚖️ الدمج الهندسي المؤسساتي (Weighted Geometric Fusion) ---
            # استخدام الضرب التبادلي: إذا كان التوقيت سيئاً (يقترب من الصفر)، السكور ينهار لحماية المشترك
            # --- ⚖️ الدمج الهندسي المؤسساتي (Weighted Geometric Fusion) ---
            base_conviction = (directional_score ** 0.70) * (timing_score ** 0.30)
            
            # 🧠 تفعيل تأثير السمية الناعم (Toxicity Discount Multiplier)
            # خصم تصاعدي للفرص المشبوهة التي نجت من الإعدام
            toxicity_discount = max(0.1, 1.0 - (toxicity_score / 100.0))
            
            # 🛡️ تطبيق عقاب التوكنوميكس
            tokenomics_multiplier = tokenomics_risk.get("penalty_multiplier", 1.0)
            if tokenomics_multiplier < 1.0:
                tags.append(tokenomics_risk.get("tag", "High_Inflation"))
            
            # تطبيق محفزات الدارك بول (VCA)، وغرفة الاحتضان، وخصم السمية التراكمي
            enhanced_conviction = base_conviction * structural_alpha_boost * tokenomics_multiplier * toxicity_discount
            
            # البوابة النهائية (Volume Multiplier) تصفي الفرصة
            final_raw_score = enhanced_conviction * volume_multiplier
            
            # 🚀 محفز الإجماع الأسّي (Exponential Confluence Boost) للمراكز المثالية
            if directional_score >= 75.0 and timing_score >= 75.0 and current_z >= 2.0:
                # نضع سقفاً لـ Z-Score هنا لمنع تضخم الأرقام اللانهائي
                boost_factor = 1.05 + (min(current_z, 5.0) * 0.01)
                final_raw_score = final_raw_score * boost_factor
            
            score = round(max(0.0, min(final_raw_score, 99.5)), 1)
            
            # 🔄 الحفاظ على التوافق التام مع المتغيرات اللاحقة (Dict compatibility)
            scores = {
                "cvd": dir_cvd, 
                "deriv": dir_deriv, 
                "tech": dir_tech, 
                "ob": timing_score, 
                "vol": volume_multiplier * 100.0
            }

            # --- 🏷️ تحديد نوع الإشارة بدقة ---
                        # --- 🏷️ تحديد نوع الإشارة بدقة ---
            dominant_pillar = max(scores, key=scores.get)
            if score >= 80.0:
                if dominant_pillar == "vol" and scores["vol"] >= 85.0: final_signal = "Deep MM Absorption 🏦"
                elif dominant_pillar == "cvd" and scores["cvd"] >= 85.0: final_signal = "Stealth Accumulation 🦈"
                elif dominant_pillar == "deriv" and scores["deriv"] >= 85.0: final_signal = "Derivatives Trapping 🔥"
                elif dominant_pillar == "ob" and scores["ob"] >= 85.0: final_signal = "Orderflow Dominance 💸"
                elif dominant_pillar == "tech" and scores["tech"] >= 85.0: final_signal = "Pre-Breakout Squeeze ⚡"
                else: final_signal = "High Probability Setup 🎯"
            
            # التعديل الموضعي: تقسيم سكورات السبعينيات بناءً على محرك السوق الفعلي
            elif score >= 70.0:
                if dominant_pillar in ["cvd", "vol"]:
                    final_signal = "Smart Money Inflow 🐋" # تدفق أموال ذكية (السيولة والفوليوم هي السبب)
                elif dominant_pillar == "ob":
                    final_signal = "Orderbook Pressure 🧱" # ضغط دفتر الأوامر (تكدس طلبات هجومية)
                else:
                    final_signal = "Structural Compression 🗜️" # انضغاط هيكلي (المشتقات والتحليل الفني هي السبب)
            
            else:
                final_signal = "Active Accumulation 🧲" # لما دون السبعين (الوضع الافتراضي)


            # ==========================================
            # 🌉 جسر توحيد المتغيرات (Variable Unification Bridge)
            # ==========================================
            current_cvd = micro_cvd_trend * price  
            current_imbalance = depth_data.get('imbalance', 0.0)
            is_orderbook_hollow_flag = depth_data.get('is_hollow', False)

            df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            avg_vol_5 = df["volume"].rolling(5).mean().iloc[-1]
            current_vol_ratio = (avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0

            volatility_state = market_regime['volatility'] if isinstance(market_regime, dict) else "Normal"
            macro_adx = market_regime['adx'] if isinstance(market_regime, dict) else 20.0
            
            # 2. ديناميكية الفومو (VWAP Z-Score)
            dyn_vwap_z = 2.5
            if volatility_state == "High_Vol": dyn_vwap_z = 3.2 
            elif volatility_state == "Low_Vol": dyn_vwap_z = 2.2 

            # 3. ديناميكية التوسع السعري (Price Expansion)
            recent_candle_spread = (abs(df['high'] - df['low']) / df['low']).tail(14).mean()
            dyn_expansion_threshold = max(0.0015, recent_candle_spread * 0.35) 
            
            # 4. ديناميكية اختلال الأوردر بوك (Imbalance & Pressure)
            dyn_imbalance_req = 0.1
            dyn_ob_req = 1.15 
            if macro_adx < 25: 
                dyn_imbalance_req = 0.25 
                dyn_ob_req = 1.30 
            elif macro_adx > 40:
                dyn_imbalance_req = 0.08 
                dyn_ob_req = 1.10 
            # ==========================================
            # 🛡️ الفيتو الناعم المتكيف (Adaptive Soft Penalties)
            # ==========================================
            veto_tolerance = 1.3 if is_incubated else 1.0 
            
            # 🛑 فلتر VPIN: ضجيج الأفراد
            if vpin_score < 0.25 and micro_cvd_trend > 0:
                tags.append("VPIN_Retail_Noise_Trap")
                toxicity_score += 25.0
            elif vpin_score > 0.65:
                tags.append("VPIN_Toxic_Inflow")
                toxicity_score = max(0.0, toxicity_score - 10.0) 

            # انحراف VWAP
            if current_vwap_z > (dyn_vwap_z * veto_tolerance):
                tags.append("Late_FOMO_Pump_VWAP")
                toxicity_score += min(40.0, (current_vwap_z - dyn_vwap_z) * 15.0) 
                
            if is_orderbook_hollow_flag and current_cvd < 0:
                tags.append("Liquidity_Void_Trap")
                toxicity_score += 30.0 

            if global_ob_pressure > dyn_ob_req and current_cvd < 0:
                tags.append("Spoofing_Distribution_Trap")
                toxicity_score += 20.0 

            # ==========================================================
            # 🛑 جدار الإعدام المؤسساتي (The Ultimate Sieve)
            # ==========================================================
            max_allowed_toxicity = 90.0 if is_incubated else 70.0
            
            if toxicity_score > max_allowed_toxicity:
                print(f"🗑️ {symbol} - تم الاستبعاد: تراكمت مخاطر التلاعب والسيولة (Toxicity: {toxicity_score:.1f}/{max_allowed_toxicity}).")
                return None 


            # ==========================================
            avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
            is_strong_cvd = current_cvd > (avg_vol_usd * 0.15)
            if is_strong_cvd:
                if oi_change_pct < -0.015:  
                    tags.append("Short_Cover_Illusion")
                    return None 

                price_expansion = (current_high - current_low) / (current_low + 1e-8)
                if price_expansion < dyn_expansion_threshold and limit_abs_signal != "Limit_Absorption" and "MM_Deep_Absorption_Phase" not in tags: 
                    tags.append("Limit_Absorption_Sell_Trap")
                    return None 

            if current_cvd <= 0 and current_imbalance <= dyn_imbalance_req and global_ob_pressure < dyn_ob_req:
                return None 

            ema200_veto = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            if price < ema200_veto and current_adx < 20.0 and current_z > 2.0:
                tags.append("Dead_Trend_Pump_Trap")
                return None  

                        # 1. تفعيل التاجات المخفية بناءً على الحسابات الموجودة مسبقاً
            if depth_data.get('imbalance', 0.0) > 0.15 or global_ob_pressure > 1.2:
                tags.append("OB_Buy_Pressure")
            
            squeeze_ratio_check = current_bb_width / (avg_bb_width + 1e-8) if not pd.isna(avg_bb_width) and avg_bb_width > 0 else 1.0
            if squeeze_ratio_check < 0.8:
                tags.append("Volatility_Squeeze")
                
            if is_liquidity_sweep:
                tags.append("Liquidity_Sweep_Absorption")
                
            if rs_score > 5.0:
                tags.append("Relative_Strength_Alpha")

            # 2. مصفوفة الإجماع المؤسساتية المحدثة
            confluence_axes = [
                any(t in tags for t in ["DARK_POOL_COIL", "Incubated_Macro_Coil", "Whale_CVD"]), # المحرك الكلي والدارك بول
                any(t in tags for t in ["High_Liquidity_Absorption", "DEEP_ABSORPTION"]),        # محرك امتصاص السيولة
                any(t in tags for t in ["OB_Buy_Pressure", "Limit_Absorption"]),                 # محرك الأوردر بوك والطلبات المخفية
                any(t in tags for t in ["Volatility_Squeeze"]),                                  # محرك الانضغاط السعري
                any(t in tags for t in ["Liquidity_Sweep_Absorption"]),                          # محرك اختطاف السيولة (صيد القيعان)
                any(t in tags for t in ["Relative_Strength_Alpha", "OI_Rising"])                 # محرك القوة النسبية والمشتقات
            ]

            
            confluence_count = sum(1 for axis in confluence_axes if axis)

            ema200_val = df["close"].ewm(span=200).mean().iloc[-1] if len(df) >= 200 else df["close"].ewm(span=50).mean().iloc[-1]
            is_macro_downtrend = price < ema200_val
            current_regime_trend = market_regime['trend'] if isinstance(market_regime, dict) else "Unknown"

            required_score = 60.0 if (current_regime_trend == "Trending_Bear" or is_macro_downtrend) else 60.0
            
            # 🌟 تخفيض سكور القبول 5 نقاط كاملة إذا كانت العملة تُطبخ في غرفة الاحتضان!
            if is_incubated: 
                required_score -= 5.0
                print(f"🎯 [Incubator Bypass] {symbol} threshold lowered to {required_score} due to Macro Coiling!")

            required_confluence = 2 if (current_regime_trend == "Trending_Bear" or is_macro_downtrend) else 2

            if score >= required_score and confluence_count >= required_confluence:    
                avg_vol_usd_for_depth = avg_vol_20 * price if avg_vol_20 > 0 else 15000.0
                ws_depth_check = await analyze_orderbook_advanced_manual(symbol, client, price, avg_vol_usd_for_depth)

                # 🧠 محرك السوينغ (Swing Execution Logic):
                # في الصفقات التي تستمر لأيام، التلاعب اللحظي (Spoofing) هو ضجيج لا يجب أن يقتل الصفقة الكلية.
                ob_skewness_val = float(depth_data.get('bid_pressure_ratio', 1.0))
                
                if ws_depth_check.get('is_spoofed', False) or ws_depth_check.get('is_hollow', False):
                    print(f"⚠️ {symbol} - تلاعب لحظي (HFT Noise). الصفقة السوينغ مستمرة لكن سيتم تحييد تأثير الأوردر بوك للذكاء الاصطناعي.")
                    # نحيّد تأثير الأوردر بوك لكي لا يخدع الذكاء الاصطناعي (نجعله محايداً = 1.0)
                    ob_skewness_val = 1.0 
                
                whale_inflow = await get_whale_inflow_score()
                micro_volatility = df['close'].tail(20).pct_change().std() * 100
                cvd_divergence = 1.0 if (price > ema200_val and current_cvd < 0) else -1.0 if (price < ema200_val and current_cvd > 0) else 0.0

                avg_vol_usd = avg_vol_20 * price if avg_vol_20 > 0 else 1.0
                cvd_ratio_pct = (current_cvd / avg_vol_usd) * 100 if avg_vol_usd > 0 else 0.0 # 👈 أصبحت دولار / دولار صحيحة للذكاء الاصطناعي
                
                regime_map = {"Trending_Bull": 1, "Trending_Bear": 2, "Ranging": 3}
                regime_code = regime_map.get(current_regime_trend, 0)

                ml_features = {
                    'market_regime': regime_code,
                    'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)),
                    'sentiment_score': float(MACRO_CACHE.get("sentiment_score", 50.0)),
                    'z_score': float(current_z),
                    'cvd_to_vol_ratio': float(cvd_ratio_pct), 
                    'ofi_imbalance': float(current_imbalance),
                    'ob_skewness': float(ob_skewness_val),
                    'whale_inflow': float(whale_inflow),
                    'adx': float(current_adx),
                    'rsi': float(last_rsi),
                    'micro_volatility': float(micro_volatility) if not pd.isna(micro_volatility) else 0.0,
                    'cvd_divergence': float(cvd_divergence), 
                    'funding_rate': float(funding_val),
                    # 🟢 البيانات المؤسساتية الحقيقية
                    'weekly_liquidity_void': float(w_void),
                    'macro_z_score_30d': float(m_z30),
                    'htf_whale_accumulation': float(htf_accum),
                    'days_since_last_expansion': float(days_exp)
                }
                # 1. الذكاء الكلاسيكي (XGBoost) - يعمل كما كان تماماً                # 1. الذكاء الكلاسيكي (XGBoost) - 4 مخرجات
                ai_confidence, xgb_drop, xgb_time, xgb_pump = await asyncio.to_thread(predict_signal_sync, ml_features)

                # 2. الذكاء العميق (MoE) - 4 مخرجات
                ai_confidence_deep, deep_drop, deep_time, deep_pump = await asyncio.to_thread(predict_deep_moe, ml_features)

                # صياغة النتائج للأدمن
                if ai_confidence != -1.0:
                    xgb_opt_entry = price * (1 - (xgb_drop / 100))
                    xgb_target = price * (1 + (xgb_pump / 100))
                    xgb_status = f"الكلاسيكي (XGB): {ai_confidence:.1f}% | ⏱️ {xgb_time:.1f}h\n 🧲 دخول: ${format_price(xgb_opt_entry)} | 🚀 صعود: +{xgb_pump:.1f}%"
                else:
                    xgb_status = "الكلاسيكي (XGB): قيد التدريب ⏳"

                if ai_confidence_deep != -1.0:
                    deep_opt_entry = price * (1 - (deep_drop / 100))
                    deep_target = price * (1 + (deep_pump / 100))
                    regime_names = {1: "Bull 🐂", 2: "Bear 🐻", 3: "Chop ⚖️"}
                    active_expert = regime_names.get(ml_features.get('market_regime', 0), "Unknown")
                    deep_status = f"العميق (MoE): {ai_confidence_deep:.1f}% | خبير: {active_expert} | ⏱️ {deep_time:.1f}h\n 🧲 دخول: ${format_price(deep_opt_entry)} | 🚀 صعود: +{deep_pump:.1f}%"
                else:
                    deep_status = "العميق (MoE): لم يتم رفع النماذج بعد 📥"

                # نمرر كلا الحالتين لطباعتهما
                ai_status = f"{xgb_status}\n🤖 {deep_status}"
                # 🧠 تسليم القيادة للذكاء الاصطناعي (Ensemble AI Takeover)
                valid_ai_scores = []
                if ai_confidence != -1.0: valid_ai_scores.append(ai_confidence)
                if ai_confidence_deep != -1.0: valid_ai_scores.append(ai_confidence_deep)
                
                if valid_ai_scores:
                    # نأخذ أعلى تقييم من نماذج الذكاء الاصطناعي
                    best_ai_score = max(valid_ai_scores)
                    
                    # ⚖️ القرار النهائي: 75% للذكاء الاصطناعي و 25% للمحرك الكلاسيكي كشبكة أمان
                    final_score = (best_ai_score * 0.75) + (score * 0.25)
                else:
                    # في حال فشل نماذج AI أو عدم تحميلها، نعود للمحرك الكلاسيكي كخطة طوارئ
                    final_score = score 
                
                # تنعيم الرقم النهائي وحمايته
                final_score = round(max(0.0, min(final_score, 99.5)), 1)
                
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
                    "cvd_usd": float(current_cvd),
                    # 🟢 تمرير النطاقات الزمنية والسعرية للرادار
                    "xgb_drop": xgb_drop, "xgb_time": xgb_time, "xgb_pump": xgb_pump,
                    "deep_drop": deep_drop, "deep_time": deep_time, "deep_pump": deep_pump
                }
            return None    

  
        except Exception as e:
            print(f"Error in analyze_radar_coin: {e}")
            return None  


async def apex_btc_tape_worker(pool):
    """
    [Institutional BTC Tape Recorder] 🦅
    يسجل حالة النظام البيئي للبيتكوين بالكامل كل 5 دقائق لتدريب الذكاء الاصطناعي.
    """
    await asyncio.sleep(60) # انتظار استقرار السيرفر
    print("📼 [BTC Tape] Continuous Institutional Data Recording is Online...")

    while True:
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                await binance_rate_limit_event.wait()
                
                # 1. جلب السعر والشموع الفنية (تم الرفع إلى 750 شمعة لإشباع الـ Z-Score)
                bin_klines = await get_candles_binance("BTCUSDT", "15m", limit=750)
                if not bin_klines or len(bin_klines) < 100:
                    await asyncio.sleep(60)
                    continue
                
                current_spot = float(bin_klines[-1][2])
                old_spot = float(bin_klines[-3][2]) # السعر قبل 45 دقيقة (ليتطابق مع قراءة الفيوتشرز)
                
                # حساب المؤشرات الكلاسيكية البسيطة
                df = pd.DataFrame(bin_klines).iloc[:, :6]
                df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
                df[["high", "low", "close", "volume"]] = df[["high", "low", "close", "volume"]].apply(pd.to_numeric)
                
                # إزالة window=24 ليعمل المحرك على 720 شمعة (أسبوع) لاكتشاف الشذوذ الحقيقي
                vol_z, _, _ = calculate_volume_zscore(df)
                
                rsi_15m = 50.0 # افتراضي
                adx_15m = 20.0
                try:
                    delta = df["close"].diff()
                    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                    loss = (-1 * delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                    rsi_15m = float((100 - (100 / (1 + (gain / loss)))).iloc[-1])
                    adx_15m = float(ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx().iloc[-1])
                except: pass

                # 2. الإطلاق المتزامن للمحركات المؤسساتية العظمى
                live_data, funding_data, ws_data, macro_data, liq_pools = await asyncio.gather(
                    get_live_zscores(client),
                    get_futures_liquidity("BTC", client, current_spot, old_spot),
                    get_wall_street_macro_flows(client, current_spot),
                    get_isolated_macro_for_btc_report(client),
                    build_liquidation_heatmap("BTC", client)
                )

                _, _, _, premium_z, basis_z = live_data
                _, _, _, _, funding_z = funding_data
                cme_premium_pct, _, ibit_vol_surge, _ = ws_data
                spy_trend, _, dxy_trend, _, us10y_trend, _ = macro_data

                # 3. هندسة المسافات لمجمعات السيولة (لجعلها قابلة للفهم للذكاء الاصطناعي)
                up_dist_pct = 0.0
                dn_dist_pct = 0.0
                mag_code = 0
                
                if liq_pools:
                    up_str = liq_pools.get('upper_pool', '')
                    dn_str = liq_pools.get('lower_pool', '')
                    mag_bias = liq_pools.get('magnetic_bias', '')
                    
                    try:
                        if " - " in up_str:
                            up_price = float(up_str.split(" - ")[0].replace("$", "").replace(",", ""))
                            up_dist_pct = ((up_price - current_spot) / current_spot) * 100
                        if " - " in dn_str:
                            dn_price = float(dn_str.split(" - ")[1].replace("$", "").replace(",", "")) 
                            dn_dist_pct = ((current_spot - dn_price) / current_spot) * 100
                    except: pass
                    
                    if "Short Squeeze" in mag_bias: mag_code = 1
                    elif "Long Flush" in mag_bias: mag_code = -1

                # 4. التخزين الصامت في قاعدة البيانات
                async with pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO apex_btc_tape (
                            spot_price, premium_z, basis_z, funding_z, cme_premium_pct, ibit_vol_surge,
                            dxy_trend_pct, us10y_trend_pct, spy_trend_pct,
                            upper_pool_dist_pct, lower_pool_dist_pct, magnetic_bias_code,
                            vol_z_score, rsi_15m, adx_15m
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """, 
                    current_spot, float(premium_z), float(basis_z), float(funding_z), float(cme_premium_pct), float(ibit_vol_surge),
                    float(dxy_trend), float(us10y_trend), float(spy_trend),
                    float(up_dist_pct), float(dn_dist_pct), int(mag_code),
                    float(vol_z), float(rsi_15m), float(adx_15m))

        except Exception as e:
            import traceback
            print(f"⚠️ [BTC Tape Error]: {e}")
            
        # أخذ لقطة دقيقة كل 5 دقائق (300 ثانية) وهو التردد الذهبي في الصناديق الكمية
        await asyncio.sleep(300)

async def apex_btc_inspector_worker(pool):
    """
    [Tier-1 Labeling Engine] 🕵️‍♂️
    يقوم بمراجعة الشريط المسجل بعد مرور 24 ساعة، ويحسب العوائد وجودة مسار السعر
    لتكوين التقييم النهائي (trade_quality_score) الجاهز لتدريب نماذج MoE و XGBoost.
    """
    await asyncio.sleep(200)
    print("🕵️‍♂️ [Tape Inspector] BTC Historical Labeler is online...")
    
    while True:
        try:
            async with pool.acquire() as conn:
                # نجلب اللقطات التي مر عليها 24 ساعة ولم يتم تقييمها
                pending = await conn.fetch("""
                    SELECT id, spot_price, EXTRACT(EPOCH FROM snapshot_timestamp) as sig_ts
                    FROM apex_btc_tape 
                    WHERE is_processed = 0 AND snapshot_timestamp <= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                    LIMIT 100
                """)
                
            if not pending:
                await asyncio.sleep(1800) # ينام نصف ساعة إذا لم يجد شيئاً
                continue
                
            async with httpx.AsyncClient(timeout=20) as client:
                for row in pending:
                    entry = float(row['spot_price'])
                    start_time_ms = int(row['sig_ts'] * 1000)
                    
                    # جلب 96 شمعة (15 دقيقة) = 24 ساعة بالضبط
                    res = await client.get(
                        f"{get_random_binance_base()}/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "15m", "startTime": start_time_ms, "limit": 96}
                    )
                    
                    if res.status_code == 200:
                        klines = res.json()
                        if len(klines) < 90: continue # تجاوز إذا البيانات ناقصة
                        
                        # حساب العوائد الزمنية المستهدفة (Labels)
                        ret_1h = ((float(klines[3][4]) - entry) / entry) * 100 
                        ret_4h = ((float(klines[15][4]) - entry) / entry) * 100 
                        ret_24h = ((float(klines[-1][4]) - entry) / entry) * 100
                        
                        # حساب التذبذب الكامل للمسار (MFE & MAE) لـ 24 ساعة
                        mfe, mae = 0.0, 0.0
                        safe_list = klines[1:] # تجاهل شمعة الدخول لمنع انحياز النظرة المستقبلية
                        for k in safe_list:
                            high, low = float(k[2]), float(k[3])
                            profit = ((high - entry) / entry) * 100
                            drawdown = ((entry - low) / entry) * 100
                            if profit > mfe: mfe = profit
                            if drawdown > mae: mae = drawdown
                                
                        # 🧠 معادلة شارب المعدلة (Modified Sharpe) لتقييم جودة الصفقة
                        # تجمع بين أقصى صعود ممكن وبين العقاب على الانهيارات السعرية (Drawdowns)
                        drawdown_penalty = mae * (0.2 if mfe > 3.0 else 0.8)
                        trade_quality = (mfe - drawdown_penalty) / (mfe + drawdown_penalty + 0.1)
                        trade_quality = max(-1.0, min(1.0, trade_quality))

                        async with pool.acquire() as conn:
                            await conn.execute("""
                                UPDATE apex_btc_tape 
                                SET ret_1h = $1, ret_4h = $2, ret_24h = $3, 
                                    mfe_24h = $4, mae_24h = $5,
                                    trade_quality_score = $6, is_processed = 1
                                WHERE id = $7
                            """, ret_1h, ret_4h, ret_24h, mfe, mae, float(trade_quality), row['id'])
                            
                    await asyncio.sleep(0.2) # استراحة بين الطلبات
                    
        except Exception as e:
            print(f"⚠️ [Tape Inspector Error]: {e}")
        await asyncio.sleep(60)

async def institutional_incubator_worker(pool):
    """
    [The Incubation Matrix] 🧬
    يبحث عن التجميع المؤسساتي البطيء على فريمات 4H/1D ويجهزها للرادار اللحظي.
    """
    await asyncio.sleep(60) # انتظر استقرار السيرفر
    print("🧪 [Incubator] The Matrix is Online. Scanning Macro Coils...")

    while True:
        try:
            current_time = time.time()
            # تنظيف الغرفة من العملات التي تعفنت (مر عليها أكثر من 48 ساعة دون انفجار)
            stale_coins = [k for k, v in INCUBATION_MATRIX.items() if current_time - v['incubation_start'] > 172800]
            for k in stale_coins:
                del INCUBATION_MATRIX[k]
                print(f"🧹 [Incubator] Removed {k} from matrix (Time Expired).")

            async with httpx.AsyncClient(timeout=30) as client:
                await binance_rate_limit_event.wait()
                base_url = get_random_binance_base()
                res = await client.get(f"{base_url}/api/v3/ticker/24hr")
                
                if res.status_code == 200:
                    tickers = [t for t in res.json() if t['symbol'].endswith("USDT") and float(t['quoteVolume']) > 2_000_000]
                    
                    for t in tickers:
                        sym = t['symbol']
                        clean_sym = symbol.replace("USDT", "")
                        if not clean_sym.isalnum(): continue
                        if clean_sym in BLACKLISTED_COINS: continue
                        

                        await binance_rate_limit_event.wait()
                        # جلب شموع 4 ساعات للاحتضان
                        candles = await get_candles_binance(sym, "4h", limit=100)
                        if not candles: continue

                        df, last_rsi, current_adx, current_z, vol_mean, vol_std = await asyncio.to_thread(process_dataframe_sync, candles)
                        dyn_window = get_dynamic_window(df, base_window=20)
                        sma = df["close"].rolling(dyn_window).mean()
                        std = df["close"].rolling(dyn_window).std(ddof=0)
                        bb_width = (4 * std) / sma
                        current_bb_width = bb_width.iloc[-1]
                        # 🧠 شروط الاحتضان الصارمة (Idiosyncratic Compression):
                        # العملة يجب أن تثبت انضغاطها الهيكلي بقوتها الذاتية دون الاعتماد على الماكرو.
                        # الماكرو سيعمل لاحقاً كوقود للانفجار (Multiplier) وليس كعذر للدخول المبكر.
                        
                        onchain_boost = MACRO_CACHE.get("onchain_liquidity_score", 0.0)
                        
                        # عتبة البولينجر تصبح صارمة وخاصة بالعملة فقط
                        # 0.08 تعني انضغاط قاتل (Volatility Squeeze) حقيقي
                        strict_bb_threshold = 0.08
                        
                        # 🛡️ فلتر الانضغاط الذاتي (Self-Sustained Coiling)
                        if current_bb_width < strict_bb_threshold and current_adx < 25.0 and current_z > 1.0:
                            if sym not in INCUBATION_MATRIX:
                                INCUBATION_MATRIX[sym] = {
                                    "incubation_start": time.time(),
                                    "macro_z": current_z,
                                    "bb_width": current_bb_width,
                                    "onchain_fueled": True if onchain_boost > 15.0 else False # 👈 نحتفظ ببصمة البلوكتشين لاستخدامها كمحفز
                                }
                                print(f"🧬 [Incubator] Added {sym} (Strict Squeeze Confirmed). On-Chain Fueled: {INCUBATION_MATRIX[sym]['onchain_fueled']}")
                        
                        await asyncio.sleep(2) # راحة لبايننس

        except Exception as e:
            print(f"⚠️ [Incubator] Error: {e}")
            
        await asyncio.sleep(14400) # يمسح السوق كل 4 ساعات

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
        # منع تكرار الإشارة لنفس العملة خلال 24 ساعة
        exists = await conn.fetchval("""
            SELECT 1 FROM ml_training_data 
            WHERE symbol = $1 AND signal_time > CURRENT_TIMESTAMP - INTERVAL '5 hours'
        """, symbol)
        if exists: return 

        await conn.execute("""
            INSERT INTO ml_training_data 
            (symbol, entry_price, market_regime, sp500_trend_pct, sentiment_score, 
             vol_z_score, cvd_to_vol_ratio, imbalance_ratio, ob_skewness, whale_dominance_pct,
             adx, rsi, micro_volatility_pct, cvd_divergence, funding_rate,
             weekly_liquidity_void, macro_z_score_30d, htf_whale_accumulation, days_since_last_expansion)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
        """, 
        symbol, price, 
        int(features.get('market_regime', 0)), float(features.get('sp500_trend', 0.0)), 
        float(features.get('sentiment_score', 50.0)), float(features.get('z_score', 0.0)), 
        float(features.get('cvd_to_vol_ratio', 0.0)), float(features.get('ofi_imbalance', 0.0)), 
        float(features.get('ob_skewness', 1.0)), float(features.get('whale_inflow', 0.0)),
        float(features.get('adx', 0.0)), float(features.get('rsi', 50.0)), 
        float(features.get('micro_volatility', 0.0)), float(features.get('cvd_divergence', 0.0)), 
        float(features.get('funding_rate', 0.0)),
        
        # 🟢 متغيرات الماكرو الجديدة المحقونة
        float(features.get('weekly_liquidity_void', 0.0)),
        float(features.get('macro_z_score_30d', 0.0)),
        float(features.get('htf_whale_accumulation', 0.0)),
        float(features.get('days_since_last_expansion', 0.0)))

        print(f"🧠 [ML Logger] Institutional Data captured for {symbol} at ${price}")
import numpy as np

async def ml_inspector_worker(pool):
    """
    [Hedge Fund Grade] Multi-Horizon Evaluation Engine.
    يقيم الصفقات على مدار 3، 7، و 14 يوماً ويتسامح مع الانعكاسات الاستباقية.
    """
    await asyncio.sleep(120)
    print("🕵️‍♂️ [Quant Inspector] 14-Day Horizon Engine is online...")
    
    while True:
        try:
            async with pool.acquire() as conn:
                # نطلب فقط الصفقات التي مر عليها 14 يوماً كاملة لتنضج!
                pending = await conn.fetch("""
                    SELECT id, symbol, entry_price, EXTRACT(EPOCH FROM signal_time) as sig_ts
                    FROM ml_training_data 
                    WHERE is_processed = 0 AND signal_time <= CURRENT_TIMESTAMP - INTERVAL '14 days'
                """)
                
            if not pending:
                await asyncio.sleep(3600) # ينام ساعة
                continue
                
            async with httpx.AsyncClient(timeout=20) as client:
                for row in pending:
                    sym_raw = row['symbol']
                    
                    # 🛡️ فلتر الحماية: تخطي أي صف تالف في الداتابيز يحتوي على علامات استفهام أو رموز
                    if not sym_raw.isalnum():
                        print(f"🗑️ [Inspector] تم تخطي عملة تالفة في الداتابيز: {sym_raw}")
                        continue
                        
                    sym = f"{sym_raw}USDT"
                    entry = float(row['entry_price'])
                    start_time_ms = int(row['sig_ts'] * 1000)
                    
                    base_url = get_random_binance_base()
                    
                    # جلب 336 شمعة (ساعة واحدة) = 14 يوماً
                    res_asset = await client.get(
                        f"{base_url}/api/v3/klines",
                        params={"symbol": sym, "interval": "1h", "startTime": start_time_ms, "limit": 336}
                    )
                    
                    res_btc = await client.get(
                        f"{base_url}/api/v3/klines",
                        params={"symbol": "BTCUSDT", "interval": "1h", "startTime": start_time_ms, "limit": 336}
                    )
                    
                    if res_asset.status_code == 200 and res_btc.status_code == 200:
                        klines = res_asset.json()
                        btc_klines = res_btc.json()
                        
                        if not klines or len(klines) < 300 or not btc_klines:
                            continue
                        # --- 1. حساب العوائد الزمنية (Time-Horizon Returns) ---
                        # فريم الساعة = الشمعة 1، 4 ساعات = الشمعة 4، 24 ساعة = الشمعة 24
                        ret_1h = ((float(klines[1][4]) - entry) / entry) * 100 if len(klines) > 1 else 0.0
                        ret_4h = ((float(klines[4][4]) - entry) / entry) * 100 if len(klines) > 4 else 0.0
                        ret_24h = ((float(klines[24][4]) - entry) / entry) * 100 if len(klines) > 24 else 0.0
                        
                        ret_3d = ((float(klines[71][4]) - entry) / entry) * 100 if len(klines) > 71 else 0.0
                        ret_7d = ((float(klines[167][4]) - entry) / entry) * 100 if len(klines) > 167 else 0.0
                        ret_14d = ((float(klines[-1][4]) - entry) / entry) * 100
                        
                        btc_entry = float(btc_klines[0][1])
                        btc_exit = float(btc_klines[-1][4])
                        btc_return_14d = ((btc_exit - btc_entry) / btc_entry) * 100
                        alpha_14d = ret_14d - btc_return_14d

                        # --- 2. محرك التسامح والانفجار متعدد الآفاق (Multi-Horizon MFE/MAE) ---
                        klines_3d = klines[:72]   
                        klines_7d = klines[:168]  
                        klines_14d = klines       

                        def calculate_excursions(k_list, entry_price):
                            mfe, mae = 0.0, 0.0
                            # 🧠 الحل المؤسساتي (Execution Bar Discarding & Forward Slicing):
                            # نتجاهل الشمعة الأولى (شمعة التنفيذ) لأن القمة/القاع فيها ربما حدثت قبل لحظة الإشارة!
                            # استخدامها يعتبر "تسريب للمستقبل" (Look-Ahead Bias) ويدمر واقعية التدريب.
                            safe_list = k_list[1:] if len(k_list) > 1 else []
                            
                            for k in safe_list:
                                high, low = float(k[2]), float(k[3])
                                profit = ((high - entry_price) / entry_price) * 100
                                drawdown = ((entry_price - low) / entry_price) * 100
                                if profit > mfe: mfe = profit
                                if drawdown > mae: mae = drawdown
                            return mfe, mae

                        mfe_3d, mae_3d = calculate_excursions(klines_3d, entry)

                        mfe_7d, mae_7d = calculate_excursions(klines_7d, entry)
                        mfe_14d, mae_14d = calculate_excursions(klines_14d, entry)

                        # 🧠 التقييم المبني على السرعة (Velocity) والتسامح مع الانعكاس
                        score_3d = (mfe_3d - (mae_3d * 1.2)) / (mfe_3d + mae_3d + 0.1)
                        score_7d = (mfe_7d - mae_7d) / (mfe_7d + mae_7d + 0.1)
                        
                        drawdown_penalty_14d = mae_14d * (0.2 if mfe_14d > 50.0 else 0.5)
                        score_14d = (mfe_14d - drawdown_penalty_14d) / (mfe_14d + drawdown_penalty_14d + 0.1)

                        # ⚖️ الدمج الهندسي بالأوزان 
                        raw_quality = (score_3d * 0.40) + (score_7d * 0.30) + (score_14d * 0.30)
                        
                        alpha_bonus = min(0.3, max(-0.3, alpha_14d / 50.0))
                        trade_quality = max(-1.0, min(1.0, raw_quality + alpha_bonus))
                        # --- 3. الحفظ في قاعدة البيانات ---
                        async with pool.acquire() as conn:
                            await conn.execute("""
                                UPDATE ml_training_data 
                                SET ret_1h = $1, ret_4h = $2, ret_24h = $3, 
                                    ret_3d = $4, ret_7d = $5, ret_14d = $6,
                                    max_favorable_excursion = $7, max_adverse_excursion = $8,
                                    btc_return_24h = $9, alpha_24h = $10,
                                    trade_quality_score = $11, is_processed = 1
                                WHERE id = $12
                            """, 
                            ret_1h, ret_4h, ret_24h, # الآن نمررهم بشكل صحيح!
                            ret_3d, ret_7d, ret_14d, 
                            mfe_14d, mae_14d, btc_return_14d, alpha_14d, float(trade_quality), row['id'])
                        
                        print(f"📊 [14D Labeling] {sym} | 14D Return: {ret_14d:.1f}% | Quality: {trade_quality:.2f}")
                        
                    await asyncio.sleep(0.5) 
                    
        except Exception as e:
            print(f"⚠️ Quant Inspector Error: {e}")
            
        await asyncio.sleep(600)

MACRO_CACHE = {
    "sp500_trend": 0.0,
    "sentiment_score": 50.0, 
    "global_funding_health": 0.0, 
    "btc_liquidity_health": 0.0, 
    "onchain_liquidity_score": 0.0, 
    "onchain_net_flow_usd": 0.0,
    "alt_regime_score": 50.0      # 👈 المحرك الجديد: يقيس تدفق السيولة للعملات البديلة مقابل البيتكوين
}


# غرفة الاحتضان (الزنبرك): { "BTCUSDT": {"incubation_start": 1712000000, "score": 85} }
INCUBATION_MATRIX = {} 
async def get_onchain_stablecoin_flow(client: httpx.AsyncClient):
    """
    [On-Chain Macro Engine] 🐋
    يراقب طباعة العملات المستقرة (USDT, USDC) على البلوكتشين كبديل مجاني لبيانات On-Chain.
    دخول مليارات الدولارات للنظام البيئي يسبق الانفجارات السعرية بـ 24-48 ساعة.
    """
    try:
        # استخدام واجهة DefiLlama المجانية (لا تحتاج API Key)
        res = await client.get("https://stablecoins.llama.fi/stablecoincharts/all", timeout=10.0)
        
        if res.status_code == 200:
            data = res.json()
            # نحتاج بيانات آخر 3 أيام للمقارنة
            if len(data) >= 3:
                today_mcap = float(data[-1]['totalCirculatingUSD']['peggedUSD'])
                two_days_ago_mcap = float(data[-3]['totalCirculatingUSD']['peggedUSD'])

                # حساب التغير في السيولة (الدولارات المطبوعة حديثاً)
                net_flow_usd = today_mcap - two_days_ago_mcap
                flow_pct = (net_flow_usd / two_days_ago_mcap) * 100

                # تحويل النسبة إلى سكور مؤسساتي (Risk Premium Score) بين -25 و +50
                # طباعة 0.3% فقط تعني دخول مليارات للسوق
                if flow_pct > 0.05:
                    # أموال جديدة تدخل (Bullish)
                    score = min(50.0, (flow_pct / 0.5) * 50.0)
                elif flow_pct < -0.05:
                    # سحب وحرق للأموال (Bearish)
                    score = max(-25.0, (flow_pct / 0.5) * 25.0)
                else:
                    score = 0.0

                return net_flow_usd, round(score, 2)
    except Exception as e:
        print(f"⚠️ [On-Chain] DefiLlama API Error: {e}")
        
    return 0.0, 0.0

async def macro_data_worker():
    """
    [Institutional Macro Engine]
    يعمل كل 30 دقيقة. يدمج الأسواق التقليدية (S&P500) مع صحة مشتقات الكريبتو الكلية.
    """
    await asyncio.sleep(10)
    print("🌍 [Macro Engine] Quant Macro Worker is live...")
    
    headers = {"User-Agent": "Mozilla/5.0"}

    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # 1. حالة السوق الأمريكي (SPY)
                try:
                    spy_res = await client.get("https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=2d", headers=headers)
                    if spy_res.status_code == 200:
                        chart_data = spy_res.json()['chart']['result'][0]['indicators']['quote'][0]
                        closes = chart_data['close']
                        if len(closes) >= 2 and closes[-1] and closes[-2]:
                            MACRO_CACHE["sp500_trend"] = round(((closes[-1] - closes[-2]) / closes[-2]) * 100, 2)
                except Exception as e:
                    print(f"⚠️ [Macro] S&P 500 API Error (Silent Fallback): {e}")

                # 2. قياس صحة المشتقات الكلية (Global Funding Health)
                # بدلاً من المشاعر، نسحب معدلات التمويل لأكبر العملات
                try:
                    fapi_url = "https://fapi.binance.com/fapi/v1/premiumIndex"
                    fund_res = await client.get(fapi_url)
                    if fund_res.status_code == 200:
                        fund_data = fund_res.json()
                        # تصفية العملات وأخذ المتوسط المرجح
                        rates = [float(item['lastFundingRate']) for item in fund_data if 'USDT' in item['symbol']]
                        if rates:
                            avg_funding = sum(rates) / len(rates)
                            # تحويلها لسكور من 0 إلى 100 (50 هو التعادل)
                            # إذا كان التمويل سالباً جداً (شورت)، السكور يرتفع (فرصة قنص)
                            MACRO_CACHE["global_funding_health"] = quant_sigmoid_score(avg_funding, sensitivity=-5000.0, limit=100.0)
                except Exception as e:
                    print(f"⚠️ [Macro] Funding API Error: {e}")

                # --- الحقن هنا ---
                # 3. محرك البلوكتشين (On-Chain Liquidity) للتنبؤ قبل 48 ساعة
                net_usd, onchain_score = await get_onchain_stablecoin_flow(client)
                MACRO_CACHE["onchain_net_flow_usd"] = net_usd
                MACRO_CACHE["onchain_liquidity_score"] = onchain_score
                # ----------------
                # 4. 🧠 محرك السيولة القطاعية (Altcoin Regime Engine)
                # يقيس من يمتلك زمام المبادرة: البيتكوين أم العملات البديلة؟
                try:
                    tickers_res = await client.get(f"{get_random_binance_base()}/api/v3/ticker/24hr")
                    if tickers_res.status_code == 200:
                        tickers = tickers_res.json()
                        btc_ticker = next((t for t in tickers if t['symbol'] == 'BTCUSDT'), None)
                        if btc_ticker:
                            btc_change = float(btc_ticker['priceChangePercent'])
                            
                            # أخذ أعلى 100 عملة بديلة سيولة (Top 100 Alts)
                            alts = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] != 'BTCUSDT']
                            alts = sorted(alts, key=lambda x: float(x['quoteVolume']), reverse=True)[:100]
                            
                            alts_changes = [float(a['priceChangePercent']) for a in alts]
                            if alts_changes:
                                # حساب أداء "الكتلة الصلبة" للعملات البديلة (Median لتجاهل الشذوذ)
                                median_alt_change = sorted(alts_changes)[len(alts_changes)//2]
                                
                                # حساب الفارق (Spread) بين البديلة والبيتكوين
                                alt_spread = median_alt_change - btc_change
                                
                                # تحويل الفارق لسكور مؤسساتي من 0 لـ 100 (50 هو التعادل)
                                # إيجابي بقوة = Altseason | سلبي بقوة = BTC Vacuum
                                MACRO_CACHE["alt_regime_score"] = quant_sigmoid_score(alt_spread, sensitivity=0.5, limit=100.0)
                except Exception as e:
                    print(f"⚠️ [Macro] Alt-Regime Engine Error: {e}")

                # 5. دمج الماكرو النهائي كـ "علاوة مخاطرة" (Risk Premium)
                base_score = 50.0
                if MACRO_CACHE["sp500_trend"] > 0.5: base_score += 10
                elif MACRO_CACHE["sp500_trend"] < -0.5: base_score -= 10
                
                # ⚖️ الدمج المؤسساتي المتوازن للأسواق الكلية:
                # 25% SPY, 25% Funding, 25% On-Chain, 25% Alt-Regime
                final_macro_score = (base_score * 0.25) + \
                                    (MACRO_CACHE["global_funding_health"] * 0.25) + \
                                    (MACRO_CACHE["onchain_liquidity_score"] * 0.25) + \
                                    (MACRO_CACHE["alt_regime_score"] * 0.25)
                
                # إضافة علاوة (Bonus) قوية جداً إذا تم طباعة أموال والسيولة تتجه للبديلة بقوة
                if MACRO_CACHE["onchain_liquidity_score"] > 20.0 and MACRO_CACHE["alt_regime_score"] > 60.0:
                    final_macro_score += 15.0 

                MACRO_CACHE["sentiment_score"] = round(max(0.0, min(100.0, final_macro_score)), 1)

                print(f"🔄 [Macro Updated] Inst_Risk: {MACRO_CACHE['sentiment_score']} | S&P500: {MACRO_CACHE['sp500_trend']}% | On-Chain Flow: ${net_usd:,.0f} | Funding: {MACRO_CACHE['global_funding_health']:.1f}")
                
        except Exception as e:
            print(f"⚠️ [Macro Engine] Critical Background Error: {e}")
            
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
            if drop_pct < -0.2 or candle_drop < -0.2:
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
                    
                    # 🛡️ فلتر حماية: تجاهل أي عملة تحتوي على علامات استفهام أو رموز غريبة
                    if not clean_sym.isalnum():
                        continue
                    
                    if clean_sym in STABLE_COINS or clean_sym in ignored_symbols or clean_sym in BLACKLISTED_COINS: 
                        continue

                    
                    vol_usd = float(t["quoteVolume"])
                    price_change = float(t["priceChangePercent"])
                    
                    # 🟢 الفلترة السحرية: 
                    if vol_usd >= 400_000 and -25.0 <= price_change <= 5.0:
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
                    await asyncio.sleep(60)
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
                # 🧲 استخراج وترتيب النطاقات من الأصغر للأكبر
                # ==========================================
                # 🇸🇦 بناء التحليل الكمي باللغة العربية
                # ==========================================
                # 🧲 استخراج وترتيب النطاقات من الأصغر للأكبر
                xgb_drop = float(best_meta.get('xgb_drop', 0.0))
                xgb_time = float(best_meta.get('xgb_time', 0.0))
                xgb_pump = float(best_meta.get('xgb_pump', 0.0))
                
                deep_drop = float(best_meta.get('deep_drop', 0.0))
                deep_time = float(best_meta.get('deep_time', 0.0))
                deep_pump = float(best_meta.get('deep_pump', 0.0))

                # حساب مناطق الدخول
                xgb_opt_entry = price * (1 - (xgb_drop / 100))
                deep_opt_entry = price * (1 - (deep_drop / 100))
                min_entry = min(xgb_opt_entry, deep_opt_entry)
                max_entry = max(xgb_opt_entry, deep_opt_entry)
                
                # حساب مناطق الخروج (الأهداف)
                xgb_target = price * (1 + (xgb_pump / 100))
                deep_target = price * (1 + (deep_pump / 100))
                min_target = min(xgb_target, deep_target)
                max_target = max(xgb_target, deep_target)
                
                min_time = min(xgb_time, deep_time)
                max_time = max(xgb_time, deep_time)

                vol_ar = f"شذوذ فوليوم مؤسساتي (Z-Score: {z_val:.2f}) مع ضخ سيولة حاد ({vol_ratio:.2f}x)." if z_val > 2 else f"انضغاط سيولة صامت (Z-Score: {z_val:.2f})."
                cvd_ar = f"امتصاص شرائي خفي (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"ضغط بيعي وتصريف (CVD: ${cvd_val:,.0f})"
                ob_ar = f"مع تكدس طلبات هجومي (OB: {ob_val:.2f}x)." if ob_val > 1 else f"مع سيطرة وتكدس لعروض البيع (OB: {ob_val:.2f}x)."
                
                if funding < -0.0005: fund_ar = "تمركز بيعي قوي مع احتمالية لتصفية البائعين (Short Squeeze)."
                elif funding > 0.0005: fund_ar = "طمع شرائي ومعدل تمويل إيجابي ينذر بخطر تصفية المشترين (Long Squeeze)."
                else: fund_ar = "استقرار وتوازن في معدلات تمويل عقود المشتقات."
                
                tech_ar = f"إجماع فني ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"

                insight_ar = (
                    f"🧲 <b>نطاق الشراء:</b> <code>{format_price(min_entry)}$</code> - <code>{format_price(max_entry)}$</code>\n"
                    f"🎯 <b>نطاق الهدف:</b> <code>{format_price(min_target)}$</code> - <code>{format_price(max_target)}$</code>\n"
                    f"⏱️ <b>الزمن المقدر:</b> <code>{min_time:.1f}h</code> - <code>{max_time:.1f}h</code>\n"
                    f"• <b>السيولة:</b> {vol_ar}\n"
                    f"• <b>التدفق:</b> {cvd_ar} {ob_ar}\n"
                    f"• <b>المشتقات:</b> {fund_ar}\n"
                    f"• <b>الهيكلة:</b> {tech_ar}"
                )

                # ==========================================
                # 🇺🇸 بناء التحليل الكمي باللغة الإنجليزية
                # ==========================================
                vol_en = f"Institutional anomaly (Z-Score: {z_val:.2f}) with aggressive inflow ({vol_ratio:.2f}x)." if z_val > 2 else f"Silent liquidity compression (Z-Score: {z_val:.2f})."
                cvd_en = f"Hidden buy absorption (CVD: +${cvd_val:,.0f})" if cvd_val > 0 else f"Selling pressure & distribution (CVD: ${cvd_val:,.0f})"
                ob_en = f"with aggressive bid stacking (OB: {ob_val:.2f}x)." if ob_val > 1 else f"with heavy ask supply dominance (OB: {ob_val:.2f}x)."
                
                if funding < -0.0005: fund_en = "Heavy short positioning with high (Short Squeeze) probability."
                elif funding > 0.0005: fund_en = "Overleveraged longs with high (Long Squeeze/Correction) risk."
                else: fund_en = "Stable futures open interest and neutral funding rates."
                
                tech_en = f"Technical confluence ({confluence}/6) | ADX: {adx:.1f} | RSI: {rsi:.1f}"

                insight_en = (
                    f"🧲 <b>Buying Range:</b> <code>${format_price(min_entry)}</code> - <code>${format_price(max_entry)}</code>\n"
                    f"🎯 <b>Target Range:</b> <code>${format_price(min_target)}</code> - <code>${format_price(max_target)}</code>\n"
                    f"⏱️ <b>Est. Surge Time:</b> <code>{min_time:.1f}h</code> - <code>{max_time:.1f}h</code>\n"
                    f"• <b>Liquidity:</b> {vol_en}\n"
                    f"• <b>Orderflow:</b> {cvd_en} {ob_en}\n"
                    f"• <b>Derivatives:</b> {fund_en}\n"
                    f"• <b>Structure:</b> {tech_en}"
                )


                # ====================================================================
                # 🚨 صدمة العرض المؤسساتية (للأدمن فقط - لا تحفظ في إشارة المستخدمين)
                # ====================================================================
                supply_shock_msg_ar = ""
                
                # استخدام pool مباشرة لأنه متاح في الدالة
                async with pool.acquire() as conn:
                    matrix_data = await conn.fetchrow("""
                        SELECT accumulated_cvd_usd, adv_30d_usd, EXTRACT(EPOCH FROM start_time) as start_ts 
                        FROM stealth_accumulation_matrix WHERE symbol = $1
                    """, symbol)
                
                if matrix_data and matrix_data['adv_30d_usd'] > 0:
                    shock_ratio = (matrix_data['accumulated_cvd_usd'] / matrix_data['adv_30d_usd']) * 100
                    if shock_ratio > 5.0:
                        days_active = (time.time() - matrix_data['start_ts']) / 86400.0
                        supply_shock_msg_ar = f"🚨 <b>صدمة عرض مؤسساتية:</b> تم سحب {shock_ratio:.1f}% من السيولة الشهرية بهدوء منذ {days_active:.1f} أيام!\n"
                # ====================================================================

                signal_id = str(uuid.uuid4())[:8] 
                # 🛡️ إدراج التحليل العادي للمستخدمين بدون بيانات صدمة العرض!
                radar_pending_approvals[signal_id] = {
                    "symbol": symbol, "price": price, "signal": signal, "score": best_score,
                    "insight_ar": insight_ar, "insight_en": insight_en
                }
                
                # تشغيل التسجيل في الخلفية لكي لا يؤخر إرسال الرسالة للأدمن
                asyncio.create_task(log_signal_for_ml(pool, symbol, price, best_meta.get('ml_features', {})))

                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ موافقة ونشر للمشتركين", callback_data=f"rad_app_{signal_id}")],
                    [InlineKeyboardButton(text="❌ إلغاء وتجاهل", callback_data=f"rad_rej_{signal_id}")]
                ])

                current_ai_status = best_meta.get('ai_status', 'Learning ⏳')

                # 🎯 حقن رسالة الحوت هنا فقط، لكي تظهر في شاشة الأدمن ولا تصل للمستخدم
                admin_text = (
                    f"⚠️ <b>تنبيه أدمن: قناص القيعان أنهى المسح 🎯</b>\n"
                    f"🏆 <b>أفضل عملة:</b> #{symbol}\n"
                    f"💵 السعر: ${format_price(price)}\n"
                    f"⚡ نوع التجميع: {signal}\n"
                    f"🤖 حالة الـ AI: <b>{current_ai_status}</b>\n"
                    f"📊 تقييم الفرصة: <b>{best_score}/100</b>\n\n"
                    f"{supply_shock_msg_ar}"  # <--- ستظهر لك هنا فقط إذا تجاوز الحوت 5%
                    f"📝 <b>التحليل:</b>\n{insight_ar}\n\n"
                    f"هل تريد الموافقة على نشرها؟"
                )

                await bot.send_message(ADMIN_USER_ID, admin_text, reply_markup=admin_kb, parse_mode=ParseMode.HTML)
                print(f"✅ تم اصطياد قاع {symbol} بسكور {best_score}!")
                
                # 🟢 التعديل هنا: حذفنا break ووضعنا استراحة 5 دقائق (300 ثانية)
                print("⏱️ الرادار يدخل في استراحة لمدة 5 دقائق قبل بدء البحث التالي...")
                await asyncio.sleep(120) 


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

    await cb.answer() # 🟢 أضف هذا السطر هنا لمنع تجميد شاشتك كأدمن

    signal_id = cb.data.replace("rad_app_", "")
    # ... باقي الكود ...
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
# ====================================================================
# 🐋 أزرار موافقة رادار المستثمرين (Investor Radar Callbacks)
# ====================================================================
@dp.callback_query(F.data.startswith("inv_app_"))
async def approve_investor_signal(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)

    await cb.answer() 
    signal_id = cb.data.replace("inv_app_", "")
    data = investor_pending_approvals.get(signal_id)

    if not data:
        return await cb.message.edit_text("❌ انتهت صلاحية هذه الإشارة أو تم اتخاذ قرار مسبقاً.")

    await cb.message.edit_text(f"✅ تمت الموافقة! جاري إرسال الإشارة {data['symbol']} لجميع المستخدمين...")

    # توثيق الإرسال لمنع التكرار
    pool = dp['db_pool']
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO radar_history (symbol, last_signaled)
            VALUES ($1, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol) DO UPDATE SET last_signaled = CURRENT_TIMESTAMP
        """, data['symbol'])

        # جلب جميع المستخدمين من قاعدة البيانات
        users_data = await conn.fetch("SELECT user_id, lang FROM users_info")

    # إرسال الرسالة للجميع (مكشوفة للمستثمرين، ومخفية للبقية)
    for row in users_data:
        uid = row["user_id"]
        lang = row["lang"] or "ar"
        
        # الفحص: هل هذا المستخدم ضمن قائمة المستثمرين؟
        is_investor = uid in INVESTOR_IDS

        # ---------- رسالة المستثمرين (مكشوفة بالكامل) ----------
        if is_investor:
            if lang == "ar":
                text = (
                    f"📡 <b>رادار المستثمرين</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 <b>العملة:</b> #{data['symbol']}\n"
                    f"💵 <b>السعر الحالي:</b> ${format_price(data['price'])}\n"
                    f"🧲 <b>منطقة الدخول:</b> ${format_price(data['entry'])}\n\n"
                    f"🚀 <b>نسبة الصعود المتوقعة: +{data['pump_pct']:.1f}%</b>\n"
                    f"🎯 <b>الهدف:</b> ${format_price(data['target'])}\n\n"
                    f"🤖 <b>المحرك:</b> {data['model']}\n"
                    f"⏱️ <b>الزمن المقدر:</b> {data['time_hrs']:.1f} ساعة\n"
                    f"━━━━━━━━━━━━━━"
                )
            else:
                text = (
                    f"📡 <b>Investors Radar</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 <b>Asset:</b> #{data['symbol']}\n"
                    f"💵 <b>Current Price:</b> ${format_price(data['price'])}\n"
                    f"🧲 <b>Entry Zone:</b> ${format_price(data['entry'])}\n\n"
                    f"🚀 <b>Expected Surge: +{data['pump_pct']:.1f}%</b>\n"
                    f"🎯 <b>Target:</b> ${format_price(data['target'])}\n\n"
                    f"🤖 <b>Engine:</b> {data['model']}\n"
                    f"⏱️ <b>Est. Time:</b> {data['time_hrs']:.1f} Hours\n"
                    f"━━━━━━━━━━━━━━"
                )
        # ---------- رسالة باقي المستخدمين (مخفية) ----------
        else:
            if lang == "ar":
                text = (
                    f"📡 <b>رادار المستثمرين</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 <b>العملة:</b> •••• 🔒\n"
                    f"💵 <b>السعر الحالي:</b> •••• 🔒\n"
                    f"🧲 <b>منطقة الدخول:</b> •••• 🔒\n\n"
                    f"🚀 <b>نسبة الصعود المتوقعة: +{data['pump_pct']:.1f}%</b>\n"
                    f"🎯 <b>الهدف:</b> •••• 🔒\n\n"
                    f"🤖 <b>المحرك:</b> {data['model']}\n"
                    f"⏱️ <b>الزمن المقدر:</b> {data['time_hrs']:.1f} ساعة\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📌 <i>هذه الإشارة مخصصة وحصرية للمستثمرين.</i>"
                )
            else:
                text = (
                    f"📡 <b>Investors Radar</b>\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"💎 <b>Asset:</b> •••• 🔒\n"
                    f"💵 <b>Current Price:</b> •••• 🔒\n"
                    f"🧲 <b>Entry Zone:</b> •••• 🔒\n\n"
                    f"🚀 <b>Expected Surge: +{data['pump_pct']:.1f}%</b>\n"
                    f"🎯 <b>Target:</b> •••• 🔒\n\n"
                    f"🤖 <b>Engine:</b> {data['model']}\n"
                    f"⏱️ <b>Est. Time:</b> {data['time_hrs']:.1f} Hours\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📌 <i>This signal is exclusively for elite investors.</i>"
                )

        try:
            # إرسال بدون أي أزرار (reply_markup=None) للجميع
            await bot.send_message(
                uid,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
            await asyncio.sleep(0.05) # حماية من الحظر
        except Exception:
            continue
            
    # تنظيف الذاكرة بعد الإرسال
    del investor_pending_approvals[signal_id]

@dp.callback_query(F.data.startswith("inv_rej_"))
async def reject_investor_signal(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)

    signal_id = cb.data.replace("inv_rej_", "")
    if signal_id in investor_pending_approvals:
        del investor_pending_approvals[signal_id]

    await cb.message.edit_text("❌ تم تجاهل الإشارة ولن يتم إرسالها.")

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
# 3. معالجة زر الإضافة
@dp.callback_query(F.data.startswith("sub_add_"))
async def add_month_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ للأدمن فقط", show_alert=True)
    
    await cb.answer("⏳ جاري الإضافة...") # 🟢 أضف هذا السطر هنا ليفك تعليق الزر فوراً
    
    target_id = int(cb.data.replace("sub_add_", ""))
    pool = dp['db_pool']
    # ... باقي الكود كما هو ...
    
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO paid_users (user_id, expiry_date) 
            VALUES ($1, CURRENT_TIMESTAMP + INTERVAL '30 days') 
            ON CONFLICT (user_id) DO UPDATE 
            SET expiry_date = GREATEST(COALESCE(paid_users.expiry_date, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP) + INTERVAL '30 days'
        """, target_id)
        new_date = await conn.fetchval("SELECT expiry_date FROM paid_users WHERE user_id = $1", target_id)
        
    try:
        await cb.message.edit_text(f"✅ <b>تمت الإضافة!</b>\nتم إضافة 30 يوم بنجاح للمستخدم: <code>{target_id}</code>\n📅 تاريخ الانتهاء الجديد: {new_date.strftime('%Y-%m-%d')}")
    except Exception:
        pass
    await cb.answer("✅ تمت الإضافة بنجاح")

# 4. معالجة زر الخصم
@dp.callback_query(F.data.startswith("sub_min_"))
async def minus_month_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_USER_ID:
        return await cb.answer("❌ للأدمن فقط", show_alert=True)
    
    await cb.answer("⏳ جاري الخصم...") # 🟢 أضف هذا السطر هنا
    
    target_id = int(cb.data.replace("sub_min_", ""))
    pool = dp['db_pool']
    # ... باقي الكود كما هو ...
    
    async with pool.acquire() as conn:
        res = await conn.execute("""
            UPDATE paid_users 
            SET expiry_date = expiry_date - INTERVAL '30 days' 
            WHERE user_id = $1
        """, target_id)
        
        try:
            if res == "UPDATE 1":
                new_date = await conn.fetchval("SELECT expiry_date FROM paid_users WHERE user_id = $1", target_id)
                await cb.message.edit_text(f"✅ <b>تم الخصم!</b>\nتم خصم 30 يوم بنجاح من المستخدم: <code>{target_id}</code>\n📅 تاريخ الانتهاء الجديد: {new_date.strftime('%Y-%m-%d')}")
            else:
                await cb.message.edit_text(f"❌ المستخدم <code>{target_id}</code> غير موجود في جدول المشتركين!")
        except Exception:
            pass
            
    await cb.answer("✅ تمت العملية")

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
import time
import pandas as pd
import numpy as np
import httpx
import asyncio
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram import types

# ذاكرة مؤقتة لمنع استنزاف الـ API عند الحسابات التاريخية المتكررة
VANGUARD_STATS_CACHE = {
    "premium_mean": 0.0, "premium_std": 0.001,
    "basis_mean": 0.0, "basis_std": 0.001,
    "last_update": 0
}

async def fetch_historical_zscore_baselines(client: httpx.AsyncClient):
    """
    [Baseline Engine] محرك المحاذاة الزمنية
    يسحب تاريخ بايننس وكوينبيس، يدمج الشموع بناءً على الزمن، ويستخرج الانحراف المعياري.
    """
    current_time = time.time()
    
    # تحديث الذاكرة كل 4 ساعات فقط
    if current_time - VANGUARD_STATS_CACHE["last_update"] < 14400 and VANGUARD_STATS_CACHE["last_update"] != 0:
        return VANGUARD_STATS_CACHE["premium_mean"], VANGUARD_STATS_CACHE["premium_std"], \
               VANGUARD_STATS_CACHE["basis_mean"], VANGUARD_STATS_CACHE["basis_std"]

    try:
        base_url = get_random_binance_base()
        
        bin_usdc_url = f"{base_url}/api/v3/klines?symbol=BTCUSDC&interval=1h&limit=100"
        cb_usd_url = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600"
        
        # جلب العقد الديناميكي للفيوتشرز (عقود التسليم)
        dapi_info = await client.get("https://dapi.binance.com/dapi/v1/exchangeInfo", timeout=5.0)
        active_contract = "BTCUSD_PERP" 
        if dapi_info.status_code == 200:
            symbols = dapi_info.json().get('symbols', [])
            delivery = [s['symbol'] for s in symbols if "BTCUSD_" in s['symbol'] and "PERP" not in s['symbol'] and s['contractStatus'] == "TRADING"]
            if delivery: active_contract = delivery[0] 
            
        bin_delivery_url = f"https://dapi.binance.com/dapi/v1/klines?symbol={active_contract}&interval=1h&limit=100"

        res_bin, res_cb, res_del = await asyncio.gather(
            client.get(bin_usdc_url, timeout=5.0),
            client.get(cb_usd_url, timeout=5.0),
            client.get(bin_delivery_url, timeout=5.0)
        )

        if res_bin.status_code == 200 and res_cb.status_code == 200 and res_del.status_code == 200:
            df_bin = pd.DataFrame(res_bin.json(), columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "tr", "tbv", "tqav", "i"])
            df_bin["t"] = pd.to_datetime(df_bin["t"].astype(float), unit='ms')
            df_bin["close_bin"] = df_bin["c"].astype(float)
            
            cb_data = res_cb.json()
            df_cb = pd.DataFrame(cb_data, columns=["t", "low", "high", "open", "close", "volume"])
            df_cb["t"] = pd.to_datetime(df_cb["t"].astype(float), unit='s')
            df_cb["close_cb"] = df_cb["close"].astype(float)
            
            df_del = pd.DataFrame(res_del.json(), columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "tr", "tbv", "tqav", "i"])
            df_del["t"] = pd.to_datetime(df_del["t"].astype(float), unit='ms')
            df_del["close_del"] = df_del["c"].astype(float)

            # دمج الجداول
            merged = pd.merge(df_bin[["t", "close_bin"]], df_cb[["t", "close_cb"]], on="t", how="inner")
            merged = pd.merge(merged, df_del[["t", "close_del"]], on="t", how="inner")

            merged["premium_pct"] = ((merged["close_cb"] - merged["close_bin"]) / merged["close_bin"]) * 100
            merged["basis_pct"] = ((merged["close_del"] - merged["close_bin"]) / merged["close_bin"]) * 100

            VANGUARD_STATS_CACHE["premium_mean"] = merged["premium_pct"].mean()
            VANGUARD_STATS_CACHE["premium_std"] = max(merged["premium_pct"].std(), 0.001) 
            VANGUARD_STATS_CACHE["basis_mean"] = merged["basis_pct"].mean()
            VANGUARD_STATS_CACHE["basis_std"] = max(merged["basis_pct"].std(), 0.001)
            VANGUARD_STATS_CACHE["last_update"] = current_time

            return VANGUARD_STATS_CACHE["premium_mean"], VANGUARD_STATS_CACHE["premium_std"], \
                   VANGUARD_STATS_CACHE["basis_mean"], VANGUARD_STATS_CACHE["basis_std"]

    except Exception as e:
        print(f"⚠️ Z-Score Baseline Error: {e}")
        
    return 0.0, 0.001, 0.0, 0.001

async def get_live_zscores(client: httpx.AsyncClient):
    """
    [Live Engine] يسحب الـ Mid-Price اللحظي ويحوله لـ Z-Score.
    """
    try:
        base_url = get_random_binance_base()
        
        bin_usdc_url = f"{base_url}/api/v3/ticker/bookTicker?symbol=BTCUSDC"
        cb_usd_url = "https://api.exchange.coinbase.com/products/BTC-USD/book"
        dapi_book_url = "https://dapi.binance.com/dapi/v1/ticker/bookTicker"

        res_bin, res_cb, res_dapi = await asyncio.gather(
            client.get(bin_usdc_url, timeout=3.0),
            client.get(cb_usd_url, timeout=3.0),
            client.get(dapi_book_url, timeout=3.0)
        )

        bin_mid, cb_mid, del_mid = 0.0, 0.0, 0.0

        if res_bin.status_code == 200:
            data = res_bin.json()
            bin_mid = (float(data["bidPrice"]) + float(data["askPrice"])) / 2.0
            
        if res_cb.status_code == 200:
            data = res_cb.json()
            cb_mid = (float(data["bids"][0][0]) + float(data["asks"][0][0])) / 2.0

        if res_dapi.status_code == 200:
            data = res_dapi.json()
            delivery_contracts = [item for item in data if "BTCUSD_" in item['symbol'] and "PERP" not in item['symbol']]
            if delivery_contracts:
                best_bid = float(delivery_contracts[0]['bidPrice'])
                best_ask = float(delivery_contracts[0]['askPrice'])
                del_mid = (best_bid + best_ask) / 2.0

        if bin_mid > 0 and cb_mid > 0 and del_mid > 0:
            current_premium_pct = ((cb_mid - bin_mid) / bin_mid) * 100
            current_basis_pct = ((del_mid - bin_mid) / bin_mid) * 100

            p_mean, p_std, b_mean, b_std = await fetch_historical_zscore_baselines(client)

            premium_z = (current_premium_pct - p_mean) / p_std
            basis_z = (current_basis_pct - b_mean) / b_std

            return cb_mid, bin_mid, current_premium_pct, premium_z, basis_z

    except Exception as e:
        print(f"⚠️ Live Z-Score Fetch Error: {e}")
        
    return 0.0, 0.0, 0.0, 0.0, 0.0

def evaluate_apex_matrix(premium_z: float, basis_z: float, funding_z: float, cme_premium: float, ibit_surge: float):
    """
    [The Apex Matrix] مصفوفة النوايا المندمجة (Fusion Intent)
    تدمج تدفقات الـ ETF وشيكاغو مع انحرافات منصات التجزئة.
    """
    intent, verdict = "توازن سيولي (Market Equilibrium)", "⚖️ تذبذب هيكلي. غياب الشذوذ الإحصائي، الحيتان في وضع الانتظار."
    
    p_z = max(-5.0, min(5.0, premium_z))
    f_z = max(-5.0, min(5.0, funding_z))

    # 1. حالة الاندفاع المؤسساتي المطلق (The Wall Street God Mode)
    if p_z > 1.5 and cme_premium > 0.4 and ibit_surge > 1.5:
        intent = "استحواذ أمريكي كلي (US Hegemony Accumulation)"
        verdict = "🟢 وجهة نهائية. وول ستريت (CME + BlackRock + Coinbase) تبتلع المعروض بتوافق تام. الشراء هنا إجباري."
        
    # 2. حالة التفريغ المؤسساتي (Wall Street Distribution)
    elif p_z < -1.5 and cme_premium < -0.1 and ibit_surge > 1.5:
        intent = "تصريف مؤسساتي عنيف (Wall St. Distribution)"
        verdict = "🩸 قمة مرحلية. صناديق ETF تفرّغ شحناتها، و Coinbase تقود الهبوط. خطر انهيار سريع."

    # 3. فخ سيولة التجزئة (Retail Trap)
    elif p_z < -1.0 and f_z > 1.5 and cme_premium <= 0: 
        intent = "فخ سيولة التجزئة (Retail Liquidity Trap)"
        verdict = "🔴 ارتداد وهمي. الأفراد يدفعون السعر بالرافعة المالية، ومؤسسات أمريكا لا تشارك. (فرصة شورت)."

    # 4. تصفية الخصوم (Squeeze)
    elif f_z < -2.0 and (p_z > 0.5 or cme_premium > 0.2):
        intent = "تصفية الخصوم (Aggressive Short Squeeze)"
        verdict = "⚡ صعود تصفية. المؤسسات تدفع السعر لضرب مراكز الشورت المتكدسة. هروب سريع بعد القمة."

    # 5. تجميع صامت خارج أوقات الدوام (Off-Hours Accumulation)
    elif p_z > 1.5 and ibit_surge < 0.5:
        intent = "تجميع أوفشور (Offshore/OTC Accumulation)"
        verdict = "🟡 شراء خفي بعيداً عن ساعات عمل الـ ETFs. سيولة تتجهز لانفجار قادم."

    return intent, verdict

async def get_futures_liquidity(symbol: str, client: httpx.AsyncClient, current_price: float, old_price: float):
    """
    [The True Funding Z-Score Engine] (يُرجع 5 متغيرات)
    """
    fapi_base = "https://fapi.binance.com"
    pair = f"{symbol}USDT"

    try:
        oi_url = f"{fapi_base}/futures/data/openInterestHist?symbol={pair}&period=15m&limit=2"
        live_fund_url = f"{fapi_base}/fapi/v1/premiumIndex?symbol={pair}"
        hist_fund_url = f"{fapi_base}/fapi/v1/fundingRate?symbol={pair}&limit=42"

        await binance_rate_limit_event.wait()

        oi_res, live_fund_res, hist_fund_res = await asyncio.gather(
            client.get(oi_url, timeout=5.0),
            client.get(live_fund_url, timeout=5.0),
            client.get(hist_fund_url, timeout=5.0)
        )

        if oi_res.status_code == 200 and live_fund_res.status_code == 200 and hist_fund_res.status_code == 200:
            oi_data = oi_res.json()
            live_fund_data = live_fund_res.json()
            hist_fund_data = hist_fund_res.json()

            if len(oi_data) < 2 or not hist_fund_data: 
                return 0.0, None, 0.0, 0.0, 0.0

            old_oi = float(oi_data[0]["sumOpenInterest"])
            current_oi = float(oi_data[-1]["sumOpenInterest"])
            oi_change_pct = (current_oi - old_oi) / (old_oi + 1e-8)
            price_change_pct = (float(current_price) - float(old_price)) / (float(old_price) + 1e-8)
            
            current_funding_rate = float(live_fund_data.get("lastFundingRate", 0.0))
            
            hist_rates = [float(item["fundingRate"]) for item in hist_fund_data]
            mean_funding = np.mean(hist_rates)
            std_funding = np.std(hist_rates, ddof=0)
            
            MIN_FUNDING_STD = 0.00005
            safe_std = max(std_funding, MIN_FUNDING_STD)
            funding_z_score = float((current_funding_rate - mean_funding) / safe_std)
            
            score_modifier = 0.0
            futures_signal = None

            if price_change_pct > 0.01 and oi_change_pct > 0.02: 
                score_modifier += 15.0
                futures_signal = "OI_Rising"
            elif price_change_pct > 0.01 and oi_change_pct < -0.02:
                score_modifier -= 25.0
                futures_signal = "Short_Covering"
            
            if funding_z_score < -1.5: 
                score_modifier += 12.0
                if not futures_signal: futures_signal = "Short_Squeeze"
            elif funding_z_score > 1.5:
                score_modifier -= 10.0

            return score_modifier, futures_signal, current_funding_rate, oi_change_pct, funding_z_score

    except Exception as e: 
        print(f"🚨 [Funding Engine] Error for {pair}: {str(e)}")
    
    return 0.0, None, 0.0, 0.0, 0.0
async def get_wall_street_macro_flows(client: httpx.AsyncClient, spot_price: float):
    """
    [Tier-1 Wall Street Engine] 🦅
    يجلب بيانات بورصة شيكاغو (CME) وصناديق الاستثمار المتداولة (BlackRock IBIT)
    باستخدام Yahoo Finance كبديل مجاني ومستقر جداً ولا يحتاج API Key.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        # 1. جلب بيانات عقود شيكاغو (CME Bitcoin Futures: BTC=F)
        cme_url = "https://query1.finance.yahoo.com/v8/finance/chart/BTC=F?interval=15m&range=1d"
        
        # 2. جلب بيانات أضخم ETF (BlackRock IBIT)
        ibit_url = "https://query1.finance.yahoo.com/v8/finance/chart/IBIT?interval=15m&range=2d"
        
        res_cme, res_ibit = await asyncio.gather(
            client.get(cme_url, headers=headers, timeout=5.0),
            client.get(ibit_url, headers=headers, timeout=5.0),
            return_exceptions=True
        )
        
        cme_premium_pct = 0.0
        cme_trend = "Neutral ⚪"
        ibit_action = "Market Closed / Quiet 💤"
        ibit_vol_surge = 0.0
        
        # --- تحليل CME (Institutional Futures) ---
        if not isinstance(res_cme, Exception) and res_cme.status_code == 200:
            cme_data = res_cme.json().get('chart', {}).get('result', [{}])[0]
            if 'indicators' in cme_data and 'quote' in cme_data['indicators']:
                cme_closes = cme_data['indicators']['quote'][0].get('close', [])
                valid_cme = [c for c in cme_closes if c is not None]
                if valid_cme:
                    cme_price = float(valid_cme[-1])
                    # حساب الفارق بين سعر شيكاغو والسعر الفوري (Premium/Discount)
                    cme_premium_pct = ((cme_price - spot_price) / spot_price) * 100
                    
                    if cme_premium_pct > 0.4: cme_trend = "Bullish Contango 🟢"
                    elif cme_premium_pct < -0.1: cme_trend = "Bearish Backwardation 🔴"
                    else: cme_trend = "Neutral Basis ⚪"

        # --- تحليل BlackRock ETF (IBIT) ---
        if not isinstance(res_ibit, Exception) and res_ibit.status_code == 200:
            ibit_data = res_ibit.json().get('chart', {}).get('result', [{}])[0]
            if 'indicators' in ibit_data and 'quote' in ibit_data['indicators']:
                vols = ibit_data['indicators']['quote'][0].get('volume', [])
                valid_vols = [v for v in vols if v is not None and v > 0]
                
                if len(valid_vols) >= 10:
                    # مقارنة فوليوم آخر 45 دقيقة بالمتوسط لاكتشاف "صدمة التدفق"
                    recent_vol = sum(valid_vols[-3:]) 
                    avg_vol = sum(valid_vols) / len(valid_vols)
                    
                    ibit_vol_surge = (recent_vol / (avg_vol * 3)) if avg_vol > 0 else 1.0
                    
                    if ibit_vol_surge > 2.0:
                        ibit_action = "Aggressive Wall St. Inflow 🔥"
                    elif ibit_vol_surge > 1.2:
                        ibit_action = "Steady Accumulation 📈"
                    elif ibit_vol_surge < 0.5:
                        ibit_action = "Low Institutional Interest 💤"
                        
        return cme_premium_pct, cme_trend, ibit_vol_surge, ibit_action
        
    except Exception as e:
        print(f"⚠️ Wall Street Data Error: {e}")
        return 0.0, "Unknown", 0.0, "Unknown"
async def get_isolated_macro_for_btc_report(client: httpx.AsyncClient):
    """
    [Isolated Macro Correlation Engine] 🌍
    دالة معزولة تعمل عند طلب /btc فقط. تسحب حالة الاقتصاد الكلي 
    (الدولار، السندات، الأسهم) دون التأثير على رادارات الذكاء الاصطناعي الأساسية.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        spy_url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=5d"
        dxy_url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d"
        us10y_url = "https://query1.finance.yahoo.com/v8/finance/chart/^TNX?interval=1d&range=5d"

        res_spy, res_dxy, res_us10y = await asyncio.gather(
            client.get(spy_url, headers=headers, timeout=5.0),
            client.get(dxy_url, headers=headers, timeout=5.0),
            client.get(us10y_url, headers=headers, timeout=5.0),
            return_exceptions=True
        )

        def extract_trend(res):
            if not isinstance(res, Exception) and res.status_code == 200:
                chart_data = res.json().get('chart', {}).get('result', [{}])[0]
                closes = chart_data.get('indicators', {}).get('quote', [{}])[0].get('close', [])
                valid_closes = [c for c in closes if c is not None]
                if len(valid_closes) >= 2:
                    return ((valid_closes[-1] - valid_closes[-2]) / valid_closes[-2]) * 100
            return 0.0

        spy_trend = extract_trend(res_spy)
        dxy_trend = extract_trend(res_dxy)
        us10y_trend = extract_trend(res_us10y)
        
        # تحليل التأثير على البيتكوين (عكسي مع الدولار والسندات، طردي مع الأسهم)
        dxy_impact = "🔴 ضغط سلبي" if dxy_trend > 0.15 else ("🟢 داعم للسيولة" if dxy_trend < -0.15 else "⚪ حيادي")
        us10y_impact = "🔴 يسحب السيولة" if us10y_trend > 1.0 else ("🟢 يسهل الاقتراض" if us10y_trend < -1.0 else "⚪ حيادي")
        spy_impact = "🟢 شهية مخاطر عالية" if spy_trend > 0.3 else ("🔴 هروب للملاذات" if spy_trend < -0.3 else "⚪ حيادي")

        return spy_trend, spy_impact, dxy_trend, dxy_impact, us10y_trend, us10y_impact

    except Exception as e:
        print(f"⚠️ [Isolated Macro] Fetch Error: {e}")
        return 0.0, "⚪", 0.0, "⚪", 0.0, "⚪"
import xgboost as xgb
import onnxruntime as ort

# ====================================================================
# 📥 تهيئة نماذج البيتكوين المعزولة (Global Scope)
# ====================================================================
BTC_XGB_MODELS = {}
BTC_DEEP_MODEL = None

def load_btc_models():
    global BTC_XGB_MODELS, BTC_DEEP_MODEL
    try:
        targets = ['quality', 'drop', 'time', 'pump']
        for t in targets:
            model_path = f"btc_xgb_{t}.json"
            if os.path.exists(model_path):
                m = xgb.XGBRegressor()
                m.load_model(model_path)
                BTC_XGB_MODELS[t] = m
                
        if os.path.exists("btc_moe_model.onnx"):
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 1
            BTC_DEEP_MODEL = ort.InferenceSession("btc_moe_model.onnx", sess_options)
            print("✅ [BTC AI] Native Models Loaded Successfully.")
    except Exception as e:
        print(f"⚠️ [BTC AI] Failed to load models: {e}")

load_btc_models()

async def predict_btc_specific_ai(features: dict):
    """
    [Isolated BTC Inference]
    يقوم بالمعالجة بـ Scaling مطابق 100% لبيئة التدريب
    ويستخرج القيم الحقيقية من النماذج المدربة للبيتكوين حصراً.
    """
    if not BTC_XGB_MODELS or not BTC_DEEP_MODEL:
        # حماية في حال لم يتم تحميل النماذج
        return -1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0

    try:
        X_scaled = np.zeros((1, 14), dtype=np.float32)
        X_scaled[0, 0] = np.tanh(np.clip(float(features.get('premium_z', 0)), -5.0, 5.0) / 2.0)
        X_scaled[0, 1] = np.tanh(np.clip(float(features.get('basis_z', 0)), -5.0, 5.0) / 2.0)
        X_scaled[0, 2] = np.tanh(np.clip(float(features.get('funding_z', 0)), -5.0, 5.0) / 2.0)
        X_scaled[0, 3] = np.clip(float(features.get('cme_premium_pct', 0)), -2.0, 2.0) / 2.0
        X_scaled[0, 4] = np.tanh(np.clip(float(features.get('ibit_vol_surge', 0)), 0.0, 10.0) / 3.0)
        X_scaled[0, 5] = np.tanh(np.clip(float(features.get('dxy_trend_pct', 0)), -2.0, 2.0) / 0.5)
        X_scaled[0, 6] = np.tanh(np.clip(float(features.get('us10y_trend_pct', 0)), -5.0, 5.0) / 1.5)
        X_scaled[0, 7] = np.tanh(np.clip(float(features.get('spy_trend_pct', 0)), -5.0, 5.0) / 1.5)
        X_scaled[0, 8] = np.clip(float(features.get('upper_pool_dist_pct', 0)), 0.0, 15.0) / 15.0
        X_scaled[0, 9] = np.clip(float(features.get('lower_pool_dist_pct', 0)), 0.0, 15.0) / 15.0
        X_scaled[0, 10] = float(features.get('magnetic_bias_code', 0))
        X_scaled[0, 11] = np.tanh(np.clip(float(features.get('vol_z_score', 0)), -5.0, 5.0) / 2.0)
        X_scaled[0, 12] = np.clip(float(features.get('rsi_15m', 50.0)) / 100.0, 0.0, 1.0)
        X_scaled[0, 13] = np.clip(float(features.get('adx_15m', 20.0)) / 100.0, 0.0, 1.0)

        # 1. نتائج الكلاسيكي (XGBoost)
        raw_q = float(BTC_XGB_MODELS['quality'].predict(X_scaled)[0])
        xgb_conf = max(0.0, min(100.0, ((raw_q + 1.0) / 2.0) * 100.0))
        xgb_drop = max(0.0, float(BTC_XGB_MODELS['drop'].predict(X_scaled)[0]))
        xgb_time = max(0.1, float(BTC_XGB_MODELS['time'].predict(X_scaled)[0]))
        xgb_pump = max(0.0, float(BTC_XGB_MODELS['pump'].predict(X_scaled)[0]))

        # 2. نتائج الذكاء العميق (ONNX MoE)
        input_name = BTC_DEEP_MODEL.get_inputs()[0].name
        output_name = BTC_DEEP_MODEL.get_outputs()[0].name
        pred = BTC_DEEP_MODEL.run([output_name], {input_name: X_scaled})[0][0]
        
        raw_q_moe = float(pred[0])
        moe_conf = max(0.0, min(100.0, ((raw_q_moe + 1.0) / 2.0) * 100.0))
        moe_drop = max(0.0, float(pred[1]))
        moe_time = max(0.1, float(pred[2]))
        moe_pump = max(0.0, float(pred[3]))

        return xgb_conf, xgb_drop, xgb_time, xgb_pump, moe_conf, moe_drop, moe_time, moe_pump

    except Exception as e:
        print(f"⚠️ [BTC AI Execution Error]: {e}")
        return -1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0
@dp.message(Command("btc_admin"))
async def btc_admin_supreme_command(message: types.Message):
    ALLOWED_IDS = [565965404, 7146339698, ADMIN_USER_ID]
    if message.from_user.id not in ALLOWED_IDS:
        return await message.reply("🚫 <b>Access Denied. Tier-1 Clearance Required.</b>", parse_mode=ParseMode.HTML)

    loading_text = (
        "📡 <i>Initiating Apex Vanguard Supreme Matrix...</i>\n"
        "<i>1️⃣ Synchronizing Wall Street Flows (CME & IBIT)...</i>\n"
        "<i>2️⃣ Constructing 3D Cumulative Liquidation Pools...</i>\n"
        "<i>3️⃣ Executing Isolated BTC Dual-AI Inference...</i> 🧠"
    )
    processing_msg = await message.reply(loading_text, parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # ==========================================================
            # 1. 🌐 البنية التحتية والمحركات العظمى
            # ==========================================================
            bin_klines_15m = await get_candles_binance("BTCUSDT", "15m", limit=30)
            if not bin_klines_15m:
                return await processing_msg.edit_text("⚠️ <b>خطأ في جلب بيانات البنية التحتية من Binance.</b>")
                
            current_spot = float(bin_klines_15m[-1][2])
            old_spot = float(bin_klines_15m[0][2])
            
            df_15m = pd.DataFrame(bin_klines_15m).iloc[:, :6]
            df_15m.columns = ["timestamp", "volume", "close", "high", "low", "open"]
            df_15m[["high", "low", "close", "volume"]] = df_15m[["high", "low", "close", "volume"]].apply(pd.to_numeric)
            
            vol_z, _, _ = calculate_volume_zscore(df_15m, window=24)
            try:
                adx_val = float(ta.trend.ADXIndicator(df_15m['high'], df_15m['low'], df_15m['close'], window=14).adx().iloc[-1])
                delta = df_15m["close"].diff()
                gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                loss = (-1 * delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                rsi_val = float((100 - (100 / (1 + (gain / loss)))).iloc[-1])
            except:
                adx_val, rsi_val = 20.0, 50.0

            tasks = [
                get_live_zscores(client),
                get_futures_liquidity("BTC", client, current_spot, old_spot),
                get_wall_street_macro_flows(client, current_spot),
                get_isolated_macro_for_btc_report(client),
                build_liquidation_heatmap("BTC", client) 
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            live_data = results[0] if not isinstance(results[0], Exception) else (current_spot, current_spot, 0, 0, 0)
            funding_data = results[1] if not isinstance(results[1], Exception) else (0, None, 0, 0, 0)
            ws_data = results[2] if not isinstance(results[2], Exception) else (0, "⚪", 0, "⚪")
            macro_data = results[3] if not isinstance(results[3], Exception) else (0,"⚪", 0,"⚪", 0,"⚪")
            liq_pools = results[4] if not isinstance(results[4], Exception) else {}
            
            cb_price, binance_price, premium_pct, premium_z, basis_z = live_data
            _, fut_sig, funding_val, oi_change, funding_z = funding_data
            cme_premium_pct, cme_trend, ibit_vol_surge, ibit_action = ws_data
            spy_trend, spy_impact, dxy_trend, dxy_impact, us10y_trend, us10y_impact = macro_data

            # ==========================================================
            # 2. 🧠 عزل وتغذية الذكاء الاصطناعي الخاص بالبيتكوين (Features)
            # ==========================================================
            up_dist_pct, dn_dist_pct, mag_code = 0.0, 0.0, 0
            if liq_pools and isinstance(liq_pools, dict):
                pool_up = f"{liq_pools.get('upper_pool', 'غير متاح')} ({liq_pools.get('upper_intensity', '⚪')})"
                pool_dn = f"{liq_pools.get('lower_pool', 'غير متاح')} ({liq_pools.get('lower_intensity', '⚪')})"
                mag_bias = liq_pools.get('magnetic_bias', "تجاذب سيولي ⚖️")
                
                try:
                    up_str = liq_pools.get('upper_pool', '')
                    dn_str = liq_pools.get('lower_pool', '')
                    if " - " in up_str: up_dist_pct = ((float(up_str.split(" - ")[0].replace("$", "").replace(",", "")) - current_spot) / current_spot) * 100
                    if " - " in dn_str: dn_dist_pct = ((current_spot - float(dn_str.split(" - ")[1].replace("$", "").replace(",", ""))) / current_spot) * 100
                except: pass
                
                if "Short Squeeze" in mag_bias: mag_code = 1
                elif "Long Flush" in mag_bias: mag_code = -1
            else:
                pool_up, pool_dn, mag_bias = "غير متاح", "غير متاح", "غير متاح"

            apex_intent, apex_verdict = evaluate_apex_matrix(premium_z, basis_z, funding_z, cme_premium_pct, ibit_vol_surge)

            btc_specific_features = {
                'premium_z': premium_z, 'basis_z': basis_z, 'funding_z': funding_z,
                'cme_premium_pct': cme_premium_pct, 'ibit_vol_surge': ibit_vol_surge,
                'dxy_trend_pct': dxy_trend, 'us10y_trend_pct': us10y_trend, 'spy_trend_pct': spy_trend,
                'upper_pool_dist_pct': up_dist_pct, 'lower_pool_dist_pct': dn_dist_pct,
                'magnetic_bias_code': mag_code, 'vol_z_score': vol_z,
                'rsi_15m': rsi_val, 'adx_15m': adx_val
            }

            # الإطلاق للمحرك المعزول
            xgb_conf, xgb_drop, xgb_time, xgb_pump, moe_conf, moe_drop, moe_time, moe_pump = await predict_btc_specific_ai(btc_specific_features)

            # ==========================================================
            # 3. 📊 التقرير التنفيذي النهائي (The Ultimate Brief)
            # ==========================================================
            cbp_icon = "🟢" if premium_z > 1.0 else ("🔴" if premium_z < -1.0 else "⚪")
            basis_icon = "📈" if basis_z > 1.0 else ("📉" if basis_z < -1.0 else "⚪")
            fund_icon = "🔥" if funding_z > 1.0 else ("🩸" if funding_z < -1.0 else "⚪")

            # صياغة النطاقات بصرامة (رقم كلاسيكي - رقم عميق)
            if xgb_conf != -1.0 and moe_conf != -1.0:
                min_entry = current_spot * (1 - (max(xgb_drop, moe_drop) / 100))
                max_entry = current_spot * (1 - (min(xgb_drop, moe_drop) / 100))
                
                min_target = current_spot * (1 + (min(xgb_pump, moe_pump) / 100))
                max_target = current_spot * (1 + (max(xgb_pump, moe_pump) / 100))
                
                ai_status_text = f"""
🤖 <b>إجماع الذكاء الاصطناعي (BTC Isolated Models):</b>
• <b>الكلاسيكي (XGB):</b> الجودة <b>{xgb_conf:.1f}%</b>
• <b>العميق (MoE):</b> الجودة <b>{moe_conf:.1f}%</b>
• <b>منطقة الشراء (Limit):</b> <code>${format_price(min_entry)}</code> ➖ <code>${format_price(max_entry)}</code>
• <b>نطاق جني الأرباح (TP):</b> <code>${format_price(min_target)}</code> ➖ <code>${format_price(max_target)}</code>
• <b>الزمن المقدر:</b> <code>{min(xgb_time, moe_time):.1f} - {max(xgb_time, moe_time):.1f} ساعة</code>
"""
            else:
                ai_status_text = "🤖 <b>إجماع الذكاء الاصطناعي:</b> النماذج المخصصة قيد التجميع والتدريب ⏳"

            final_report = f"""
🏛 <b>APEX VANGUARD SUPREME | محطة الاستخبارات الكميّة (BTC)</b> 🏛
━━━━━━━━━━━━━━━━━━
💰 <b>الأسعار العادلة (Mid-Price):</b>
• <b>Binance (التجزئة):</b> <code>${format_price(binance_price)}</code>
• <b>Coinbase (أمريكا):</b> <code>${format_price(cb_price)}</code>

🗽 <b>تدفقات الماكرو الأمريكية (US Macro Flows):</b>
• <b>عقود شيكاغو (CME):</b> <code>{cme_trend} ({cme_premium_pct:+.2f}%)</code>
• <b>صناديق ETF (BlackRock):</b> <b>{ibit_action}</b>

🌍 <b>مصفوفة الارتباط الكلي (Macro Correlation):</b>
• <b>مؤشر الدولار (DXY):</b> <code>{dxy_trend:+.2f}%</code> ({dxy_impact})
• <b>عوائد السندات (US10Y):</b> <code>{us10y_trend:+.2f}%</code> ({us10y_impact})

📊 <b>بصمة الشذوذ الإحصائي للصناع (Market Maker Z-Scores):</b>
• <b>علاوة الشراء (Coinbase Z):</b> <code>{premium_z:+.2f}σ</code> {cbp_icon}
• <b>علاوة التحوط (Basis Z):</b> <code>{basis_z:+.2f}σ</code> {basis_icon}
• <b>ضغط المشتقات (Funding Z):</b> <code>{funding_z:+.2f}σ</code> {fund_icon}

🧲 <b>مجمعات السيولة التراكمية (Cumulative 3D Heatmap):</b>
• <b>الاتجاه المغناطيسي الحرج:</b> <b>{mag_bias}</b>
• 📈 <b>حوض تصفية الشورت:</b> <code>{pool_up}</code>
• 📉 <b>حوض تصفية اللونج:</b> <code>{pool_dn}</code>

{ai_status_text}

📝 <b>تحليل نوايا صانع السوق (MM Intent):</b>
{apex_verdict}
━━━━━━━━━━━━━━━━━━
<i>* الدالة تعمل الآن بشكل معزول تماماً ببيانات مخصصة للبيتكوين.</i>
"""
            await processing_msg.edit_text(final_report, parse_mode=ParseMode.HTML)
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Apex Vanguard Error Details:\n{error_details}")
        error_msg = f"⚠️ <b>حدث خطأ في محرك البيانات الفائقة:</b>\n<code>{str(e)}</code>"
        await processing_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)

@dp.message(Command("btc"))
async def btc_apex_vanguard_command(message: types.Message):
    # 🛡️ حماية الغرفة المغلقة
    ALLOWED_IDS = [565965404, 7146339698, ADMIN_USER_ID]
    if message.from_user.id not in ALLOWED_IDS:
        return await message.reply("🚫 <b>عذراً، هذه الاستخبارات مخصصة لغرف التداول المغلقة (Tier-1).</b>", parse_mode=ParseMode.HTML)

    loading_text = (
        "📡 <i>تهيئة مصفوفة (Apex Vanguard) العظمى...</i>\n"
        "<i>1️⃣ جلب بيانات BlackRock (IBIT) و CME...</i>\n"
        "<i>2️⃣ مسح الارتباط الكلي (DXY, US10Y)...</i>\n"
        "<i>3️⃣ بناء خريطة مجمعات التصفية التراكمية (3D Heatmap)...</i> 🦅"
    )
    processing_msg = await message.reply(loading_text, parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            # 1. جلب بيانات بايننس اللحظية (السبوت)
            bin_klines = await get_candles_binance("BTCUSDT", "15m", limit=3)
            current_spot = float(bin_klines[-1][2]) if bin_klines else 60000.0
            old_spot = float(bin_klines[0][2]) if bin_klines else current_spot
            
            # 2. 🚀 الإطلاق المتزامن للأسطول الكمّي (Simultaneous Quant Fleet Execution)
            live_data_task = get_live_zscores(client)
            funding_task = get_futures_liquidity("BTC", client, current_spot, old_spot)
            wall_street_task = get_wall_street_macro_flows(client, current_spot)
            macro_corr_task = get_isolated_macro_for_btc_report(client) 
            liq_pool_task = build_liquidation_heatmap("BTC", client) # المحرك التراكمي الهجين

            # انتظار عودة جميع البيانات معاً
            live_data, funding_data, ws_data, macro_data, liq_pools = await asyncio.gather(
                live_data_task, funding_task, wall_street_task, macro_corr_task, liq_pool_task
            )

            # 3. فك تشفير البيانات (Unpacking)
            cb_price, binance_price, premium_pct, premium_z, basis_z = live_data
            _, _, funding_val, _, funding_z = funding_data
            cme_premium_pct, cme_trend, ibit_vol_surge, ibit_action = ws_data
            spy_trend, spy_impact, dxy_trend, dxy_impact, us10y_trend, us10y_impact = macro_data

            # 4. 🧠 تغذية مصفوفة النوايا (Fusion Matrix)
            apex_intent, apex_verdict = evaluate_apex_matrix(
                premium_z, basis_z, funding_z, cme_premium_pct, ibit_vol_surge
            )

            # 5. 🧲 هندسة المغناطيس والهدف (Target & Liquidity Architecture)
            if liq_pools:
                target_price = liq_pools.get('target', binance_price * 1.05)
                target_type = liq_pools.get('type', "ديناميكي (غياب التكدس)")
                pool_up = f"{liq_pools.get('upper_pool', 'غير متاح')} ({liq_pools.get('upper_intensity', '⚪')})"
                pool_dn = f"{liq_pools.get('lower_pool', 'غير متاح')} ({liq_pools.get('lower_intensity', '⚪')})"
                mag_bias = liq_pools.get('magnetic_bias', "تجاذب سيولي ⚖️")
            else:
                target_price, target_type = binance_price, "غير متاح حالياً"
                pool_up, pool_dn, mag_bias = "غير متاح", "غير متاح", "غير متاح"

            # أيقونات الحالة البصرية لتسهيل القراءة السريعة
            cbp_icon = "🟢" if premium_z > 1.0 else ("🔴" if premium_z < -1.0 else "⚪")
            basis_icon = "📈" if basis_z > 1.0 else ("📉" if basis_z < -1.0 else "⚪")
            fund_icon = "🔥" if funding_z > 1.0 else ("🩸" if funding_z < -1.0 else "⚪")
            
            # 6. 📊 طباعة التقرير التنفيذي (The Executive Wall Street Brief)
            final_report = f"""
🏛 <b>APEX VANGUARD | تقرير وول ستريت التنفيذي (BTC)</b> 🏛
━━━━━━━━━━━━━━━━━━
💰 <b>الأسعار العادلة (Institutional Mid-Price):</b>
• <b>Binance (التجزئة العالمية):</b> <code>${format_price(binance_price)}</code>
• <b>Coinbase (السيولة الأمريكية):</b> <code>${format_price(cb_price)}</code>

🗽 <b>تدفقات وول ستريت (Wall St. Macro Flows):</b>
• <b>عقود شيكاغو (CME):</b> <code>{cme_trend} ({cme_premium_pct:+.2f}%)</code>
• <b>صناديق ETF (BlackRock IBIT):</b> <b>{ibit_action}</b>

🌍 <b>الارتباط الكلي (Macro Correlation Matrix):</b>
• <b>مؤشر الدولار (DXY):</b> <code>{dxy_trend:+.2f}%</code> ({dxy_impact})
• <b>عوائد السندات (US10Y):</b> <code>{us10y_trend:+.2f}%</code> ({us10y_impact})
• <b>مؤشر S&P500 (SPY):</b> <code>{spy_trend:+.2f}%</code> ({spy_impact})

📊 <b>البصمة الإحصائية (Z-Scores Signatures):</b>
• <b>علاوة المؤسسات (Coinbase Z):</b> <code>{premium_z:+.2f}σ</code> {cbp_icon}
• <b>علاوة التحوط (Basis Z):</b> <code>{basis_z:+.2f}σ</code> {basis_icon}
• <b>ضغط المشتقات (Funding Z):</b> <code>{funding_z:+.2f}σ</code> {fund_icon}

🧠 <b>قرار صانع السوق (Market Maker Verdict):</b>
• <b>التكتيك اللحظي:</b> {apex_intent}
• <b>الخلاصة:</b> {apex_verdict}

🧲 <b>خريطة السيولة العميقة (Liquidation 3D Heatmap):</b>
• <b>الاتجاه المغناطيسي الحرج:</b> <b>{mag_bias}</b>
• 🎯 <b>نقطة الاصطدام الدقيقة:</b> <code>${format_price(target_price)}</code>
• ⚠️ <b>طبيعة الهدف:</b> {target_type}
• 📈 <b>حوض تصفية الشورت (Upper Pool):</b> <code>{pool_up}</code>
• 📉 <b>حوض تصفية اللونج (Lower Pool):</b> <code>{pool_dn}</code>
━━━━━━━━━━━━━━━━━━
<i>* تم دمج بيانات CME و ETFs وخريطة التصفية التراكمية لتوفير رؤية 3D معزولة.</i>
"""
            await processing_msg.edit_text(final_report, parse_mode=ParseMode.HTML)
            
    except Exception as e:
        error_msg = f"⚠️ <b>حدث خطأ في محرك البيانات الفائقة:</b>\n<code>{str(e)}</code>\n<i>يرجى المحاولة بعد قليل.</i>"
        await processing_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
 
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
    # التأكد أن الأمر للأدمن فقط
    if m.from_user.id != ADMIN_USER_ID:
        return
        
    pool = dp['db_pool']
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM users_info")
            
            # 🟢 جلب المشتركين النشطين فقط (تاريخهم في المستقبل)
            active_vips = await conn.fetchval("SELECT count(*) FROM paid_users WHERE expiry_date > CURRENT_TIMESTAMP OR expiry_date IS NULL")
            
            # 🔴 جلب المشتركين المنتهية اشتراكاتهم (تاريخهم في الماضي)
            expired_vips = await conn.fetchval("SELECT count(*) FROM paid_users WHERE expiry_date <= CURRENT_TIMESTAMP")
            
            total_trials = await conn.fetchval("SELECT count(*) FROM trial_users")
            active_today = await conn.fetchval("SELECT count(*) FROM users_info WHERE last_active = CURRENT_DATE")
        
        msg = (f"📊 **إحصائيات البوت المتقدمة:**\n"
               f"───────────────────\n"
               f"👥 **إجمالي القاعدة:** `{total}` مستخدم\n"
               f"🔥 **النشاط اليومي:** `{active_today}` مستخدم نشط\n"
               f"🎁 **مستخدمي التجربة:** `{total_trials}` شخص\n"
               f"💎 **VIP النشطين:** `{active_vips}` مشترك\n"
               f"⏳ **VIP المنتهيين:** `{expired_vips}` مشترك")
               
        await m.answer(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Status Error: {e}")
    
@dp.message(Command("send"))
async def broadcast_investment_cmd(m: types.Message):
    # التأكد من أن الأمر للأدمن فقط
    if m.from_user.id != ADMIN_USER_ID:
        return await m.answer("❌ لا تملك صلاحية استخدام هذا الأمر.")

    # النص المطلوب إرساله محاط بوسوم <b> لجعله بخط غامق بالكامل
    broadcast_text = (
        "<b>تحديث هام للمستثمرين\n"
        "تم أضافة الأمر /btc للبوت لتتبع حركة سعر البتكوين خطوة بخطوة والسعر المستهدف له بدقة لمعرفة حالة السوق قبل الدخول فيه.\n\n"
        "⚠️ ملاحظة : هذا الميزة خاصة للمستثمرين فقط.</b>"
    )

    pool = dp['db_pool']
    sent_count = 0
    failed_count = 0

    # إشعار الأدمن ببدء العملية
    status_msg = await m.answer("⏳ جاري إرسال الإعلان لجميع المستخدمين، يرجى الانتظار...")

    try:
        async with pool.acquire() as conn:
            # جلب جميع المستخدمين المسجلين في البوت
            users = await conn.fetch("SELECT user_id FROM users_info")
            
        for row in users:
            uid = row["user_id"]
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=broadcast_text,
                    parse_mode=ParseMode.HTML
                )
                sent_count += 1
                # استراحة 0.05 ثانية لتجنب تجاوز حدود تيليجرام (Flood Control)
                await asyncio.sleep(0.05) 
            except Exception:
                # إذا قام المستخدم بحظر البوت أو حذف حسابه
                failed_count += 1
                continue

        # تحديث الرسالة بالنتيجة النهائية
        await status_msg.edit_text(
            f"✅ <b>اكتمل الإرسال!</b>\n\n"
            f"📨 تم الإرسال بنجاح إلى: <code>{sent_count}</code> مستخدم.\n"
            f"❌ فشل الإرسال لـ: <code>{failed_count}</code> مستخدم (حظروا البوت)."
        )

    except Exception as e:
        print(f"Broadcast Error: {e}")
        await status_msg.edit_text("⚠️ حدث خطأ أثناء محاولة جلب المستخدمين من قاعدة البيانات.")

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
    # 1. إرسال تأكيد فوري لتيليجرام لقتل أيقونة التحميل المزعجة (The Fix)
    await cb.answer() 
    
    lang = cb.data.split("_")[1]

    try:
        # 2. تحديث قاعدة البيانات
        async with dp['db_pool'].acquire() as conn:
            await conn.execute(
                "UPDATE users_info SET lang = $1 WHERE user_id = $2",
                lang,
                cb.from_user.id
            )
    except Exception as e:
        print(f"DB Error in set_lang: {e}")
        # إذا حدث خطأ، نظهر رسالة منبثقة للمستخدم
        return await cb.answer("Server busy, try again...", show_alert=True)
    
    # 3. فحص حالة السيولة والاشتراك للمستخدم
    is_paid = await is_user_paid(dp['db_pool'], cb.from_user.id)
    has_tr = await has_trial(dp['db_pool'], cb.from_user.id)

    # 4. توجيه المستخدم (Routing)
    # ... الكود السابق ...
    if is_paid:
        msg = "✅ أهلاً بك مجدداً! اشتراكك مفعل.\nأرسل رمز العملة للتحليل." if lang == "ar" else "✅ Welcome back! Your subscription is active.\nSend a coin symbol to analyze."
    elif has_tr:
        msg = "🎁 لديك تجربة مجانية واحدة! أرسل رمز العملة للتحليل." if lang == "ar" else "🎁 You have one free trial! Send a coin symbol for analysis."
    else:
        msg = "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك مقابل 10 USDT أو 500 ⭐ شهرياً." if lang == "ar" else "⚠️ Your free trial has ended. For full access, please subscribe."
    
    try:
        await cb.message.edit_text(msg, reply_markup=None if (is_paid or has_tr) else get_payment_kb(lang))
    except Exception:
        pass
    
    await cb.answer() # إنهاء دوران زر اللغة
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
        # 🧠 توحيد الاتجاه باستخدام (Anchored VWAP + Price Acceleration)
        vwap_val = df['anchored_vwap'].iloc[-1]
        
        # حساب التسارع السعري (Momentum) لآخر 3 شموع لضمان عدم وجود فخ
        price_momentum = df['close'].diff(3).iloc[-1]
        
        if current_price > vwap_val and price_momentum >= 0:
            trend_direction = "Bullish"
        elif current_price < vwap_val and price_momentum <= 0:
            trend_direction = "Bearish"
        else:
            # عند التذبذب، السيولة (VWAP) هي الحكم القاطع للذكاء الاصطناعي
            trend_direction = "Bullish" if current_price > vwap_val else "Bearish"

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
        # ==========================================================
        # 🧠 المحرك الكمّي الجديد: تسعير اكتشاف السعر (Kinetic Price Discovery)
        # ==========================================================
        # 1. حساب التذبذب الحقيقي (ATR) لاستخدامه كمسطرة ديناميكية للانفجارات
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        recent_atr = df['tr'].tail(14).mean()
        
        # حماية رياضية من العملات الميتة أو البيانات الناقصة
        if pd.isna(recent_atr) or recent_atr == 0:
            recent_atr = current_price * 0.02 

        # 2. حدود الحماية الديناميكية (Dynamic Risk Bounds)
        # نتخلى عن النسب الثابتة الغبية ونربطها بشخصية تذبذب العملة
        DYNAMIC_MIN_TP = max(current_price * 0.015, recent_atr * 1.5)
        
        if trend_direction == "Bullish":
            # 🚀 الكشف عن منطقة (Price Discovery)
            is_price_discovery = above_price.empty or (above_price['price'].max() < current_price * 1.01)

            tps = []
            if not is_price_discovery:
                # استخراج الأهداف من عقد السيولة التاريخية (HVN)
                valid_targets = above_price[above_price['price'] >= current_price + DYNAMIC_MIN_TP]
                if not valid_targets.empty:
                    targets = valid_targets.nlargest(3, 'volume').sort_values('price')
                    tps = targets['price'].tolist()

            # 🚀 التدخل المؤسساتي: الإسقاط الحركي (Kinetic Projection)
            # إذا اخترقنا القمم ولا يوجد أهداف كافية، نسقط الأهداف بناءً على زخم التذبذب (ATR Fibs)
            if len(tps) < 1: tps.append(current_price + (recent_atr * 1.618))
            if len(tps) < 2: tps.append(tps[-1] + (recent_atr * 1.0))
            if len(tps) < 3: tps.append(tps[-1] + (recent_atr * 1.618))

            # 🛡️ تحديد الوقف (SL) بناءً على فجوات السيولة (LVN) أسفل الدعم
            support_node = below_price.nlargest(1, 'volume')
            support_price = support_node['price'].iloc[0] if not support_node.empty else current_price - recent_atr

            lvns_below_support = below_price[below_price['price'] < support_price]
            if not lvns_below_support.empty:
                sl_price = lvns_below_support.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = support_price - (recent_atr * 0.5)

            # حماية قصوى: الوقف الديناميكي لا يجب أن يتجاوز 8% لمنع التصفية
            sl_price = max(sl_price, current_price * (1 - 0.08))

        else: # Bearish
            # 🚀 الكشف عن الانهيار السحيق (Bottom Discovery)
            is_bottom_discovery = below_price.empty or (below_price['price'].min() > current_price * 0.99)
            
            tps = []
            if not is_bottom_discovery:
                valid_targets = below_price[below_price['price'] <= current_price - DYNAMIC_MIN_TP]
                if not valid_targets.empty:
                    targets = valid_targets.nlargest(3, 'volume').sort_values('price', ascending=False)
                    tps = targets['price'].tolist()

            # 🚀 الإسقاط الحركي لانهيارات الشورت
            if len(tps) < 1: tps.append(current_price - (recent_atr * 1.618))
            if len(tps) < 2: tps.append(tps[-1] - (recent_atr * 1.0))
            if len(tps) < 3: tps.append(tps[-1] - (recent_atr * 1.618))

            res_node = above_price.nlargest(1, 'volume')
            res_price = res_node['price'].iloc[0] if not res_node.empty else current_price + recent_atr

            lvns_above_res = above_price[above_price['price'] > res_price]
            if not lvns_above_res.empty:
                sl_price = lvns_above_res.nsmallest(1, 'volume')['price'].iloc[0]
            else:
                sl_price = res_price + (recent_atr * 0.5)

            sl_price = min(sl_price, current_price * (1 + 0.08))

        # ترتيب الأهداف منطقياً
        tp1, tp2, tp3 = tps[0], tps[1], tps[2]
        
        if trend_direction == "Bullish":
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3])
            sl_price = min(sl_price, current_price * 0.99) # الوقف أسفل السعر الحالي دائماً
        else:
            tp1, tp2, tp3 = sorted([tp1, tp2, tp3], reverse=True)
            sl_price = max(sl_price, current_price * 1.01)

        return float(sl_price), float(tp1), float(tp2), float(tp3)

    except Exception as e:
        print(f"VPVR Kinetic Engine Error: {e}")
        # أهداف احتياطية ديناميكية بدلاً من الثابتة
        fallback_atr = current_price * 0.02
        if trend_direction == "Bullish":
            return current_price - fallback_atr*2, current_price + fallback_atr*1.5, current_price + fallback_atr*2.5, current_price + fallback_atr*4
        else:
            return current_price + fallback_atr*2, current_price - fallback_atr*1.5, current_price - fallback_atr*2.5, current_price - fallback_atr*4

async def analyze_macro_derivatives_divergence(symbol: str, client: httpx.AsyncClient, spot_df: pd.DataFrame, tf: str):
    """
    [Macro Derivatives Engine] - محرك المشتقات الكلي للفريمات الكبيرة
    يستبدل الأوردر بوك اللحظي بقراءة بناء المراكز (Short/Long) على المدى الطويل.
    """
    clean_sym = symbol.replace("USDT", "") + "USDT"
    
    # تحديد نطاق جلب البيانات بناءً على الفريم
    period = "1d" if tf in ["1w", "weekly", "1d", "daily"] else "4h"
    limit = 30 if tf in ["1w", "weekly"] else 14 # 30 يوم للأسبوعي، 14 يوماً لليومي
    
    fapi_base = "https://fapi.binance.com"
    oi_url = f"{fapi_base}/futures/data/openInterestHist?symbol={clean_sym}&period={period}&limit={limit}"
    
    try:
        res = await client.get(oi_url, timeout=5.0)
        if res.status_code != 200:
            print(f"⚠️ [Binance FAPI Direct] {clean_sym} | رفض كود {res.status_code} في Macro Derivatives")
            return None
            
        oi_data = res.json()
        if len(oi_data) < 5:
            return None
            
        oi_df = pd.DataFrame(oi_data)
        oi_df['sumOpenInterestValue'] = pd.to_numeric(oi_df['sumOpenInterestValue'])
        
        # 1. التغير الكلي في العقود المفتوحة (OI)
        old_oi = oi_df['sumOpenInterestValue'].iloc[0]
        current_oi = oi_df['sumOpenInterestValue'].iloc[-1]
        oi_change_pct = (current_oi - old_oi) / (old_oi + 1e-8)
        
        # 2. التغير في السعر لنفس الفترة
        recent_spot = spot_df.tail(len(oi_df))
        old_price = recent_spot['close'].iloc[0]
        current_price = recent_spot['close'].iloc[-1]
        price_change_pct = (current_price - old_price) / (old_price + 1e-8)
        
        # 3. ميل السيولة الكلية (CVD Flow)
        if 'cvd' in spot_df.columns:
            old_cvd = recent_spot['cvd'].iloc[0]
            current_cvd = recent_spot['cvd'].iloc[-1]
            cvd_change = current_cvd - old_cvd
        else:
            cvd_change = 0.0
         # ==========================================================
        # 🧠 المنطق المؤسساتي: اكتشاف فخاخ السيولة والانحرافات
        # ==========================================================
        if price_change_pct < -0.05 and oi_change_pct > 0.15 and cvd_change < 0:
            return {
                "ar": "<b>بناء عنيف لمراكز البيع (Aggressive Shorts):</b> السعر ينزف والـ OI يرتفع بشدة. خطر انفجار سعري (Short Squeeze) مرتفع جداً لتصفيتهم!",
                "en": "<b>Aggressive Shorts Build-up:</b> Price is bleeding while Open Interest spikes. Extremely high risk of a Short Squeeze!"
            }
        elif price_change_pct > 0.05 and oi_change_pct > 0.15 and cvd_change > 0:
            return {
                "ar": "<b>تضخم مراكز الشراء (Overleveraged Longs):</b> صعود مدعوم بالمشتقات أكثر من السبوت. تصحيح قاسي محتمل لتصفيتهم.",
                "en": "<b>Overleveraged Longs:</b> Rally driven by derivatives rather than Spot. High risk of a long squeeze/flush."
            }
        elif price_change_pct > -0.05 and price_change_pct < 0.05 and oi_change_pct < -0.10 and cvd_change > 0:
            return {
                "ar": "<b>تجميع سبوت صامت (Spot Accumulation):</b> إغلاق لمراكز الفيوتشرز وشراء حقيقي وامتصاص من السوق.",
                "en": "<b>Silent Spot Accumulation:</b> Futures OI is dropping while Spot CVD shows real buying absorption."
            }
        elif price_change_pct < 0 and oi_change_pct < -0.10:
            return {
                "ar": "<b>استسلام كلي (Capitulation):</b> تصفية قسرية للمراكز وخروج تدريجي للسيولة من الأصل.",
                "en": "<b>Capitulation:</b> Forced liquidations and gradual liquidity exit from the asset."
            }
        else:
            return {
                "ar": "<b>تمركز اعتيادي (Neutral Positioning):</b> لا توجد انحرافات خطيرة في سوق المشتقات الكلي.",
                "en": "<b>Neutral Positioning:</b> No severe deviations detected in macro derivatives."
            }

    except Exception as e:
        print(f"🚨 [Binance FAPI Direct] خطأ في analyze_macro_derivatives_divergence لـ {clean_sym}: {str(e)}")
        return None
async def institutional_lob_worker(pool):
    """
    [The Market Maker Matrix] 🕸️
    يفتح تياراً مجمعاً (Combined Stream) لأهم 300 عملة.
    يحدث الأوردر بوك المحلي كل 100 ملي ثانية بدون أي API Call.
    """
    await asyncio.sleep(30) # انتظر السيرفر حتى يستقر
    print("🕸️ [LOB Engine] Local Orderbook Shadowing is Online...")

    while True:
        try:
            # 1. جلب العملات النشطة من الرادار (لمراقبتها لحظياً)
            async with pool.acquire() as conn:
                records = await conn.fetch("SELECT symbol FROM radar_history ORDER BY last_signaled DESC LIMIT 300")
                symbols = [r['symbol'].lower() for r in records]
            
            # إذا كانت الداتابيز فارغة في البداية، راقب البيتكوين والإيثيريوم
            if not symbols: symbols = ["btc", "eth"]

            # تجهيز رابط الـ Combined Stream (عمق 10 مستويات يكفي جداً للـ OFI وخفيف على الـ RAM)
            streams = "/".join([f"{sym}usdt@depth10@100ms" for sym in symbols[:300]])
            ws_url = f"wss://stream.binance.com:9443/stream?streams={streams}"
            
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20, max_size=10_000_000) as ws:
                print(f"✅ [LOB Engine] Connected to {len(symbols)} coins. Absorbing microstructure...")
                
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    
                    if "data" in data and "stream" in data:
                        sym = data["stream"].split("@")[0].upper() # استخراج اسم العملة (مثال: BTCUSDT)
                        payload = data["data"]
                        
                        # تحديث الـ OFI والذاكرة بسرعة البرق
                        if "bids" in payload and "asks" in payload:
                            update_dynamic_ofi(sym, payload["bids"], payload["asks"])
                            
        except Exception as e:
            print(f"⚠️ [LOB Engine] Stream disconnected. Reconnecting in 5s... Error: {e}")
            await asyncio.sleep(5)
async def analyze_orderbook_advanced_manual(symbol: str, client: httpx.AsyncClient, current_price: float, recent_vol_usd: float = 15000.0):
    """
    [Institutional Upgrade] True Order Flow Imbalance (OFI) & Flash Spoofing Detection
    (Zero Latency Memory Read) - تقرأ من ذاكرة الـ LOB اللحظية لـ 60 ثانية بدلاً من الانتظار.
    """
    import numpy as np # لضمان عدم وجود أخطاء في الـ Math
    
    # تجهيز مفتاح الذاكرة ليطابق ما يتم تخزينه في INSTITUTIONAL_LOB (مثال: BTCUSDT)
    mem_key = symbol.replace("USDT", "").upper() + "USDT"

    # ====================================================================
    # 🛡️ نظام الحماية (Fallback Security)
    # إذا كانت العملة جديدة جداً ولم يجمع لها الـ Worker بيانات تكفي (أقل من 10 لقطات)
    # أو كانت عملة DEX، نعود فوراً لدالتك الاحتياطية لضمان عدم توقف البوت.
    # ====================================================================
    if mem_key not in INSTITUTIONAL_LOB or len(INSTITUTIONAL_LOB[mem_key]["ofi_window"]) < 10:
        return await analyze_orderbook_spoofing_instant(symbol, client, current_price)

    # ====================================================================
    # ⚡ سحب البيانات من الذاكرة اللحظية (Microsecond Extraction)
    # ====================================================================
    mem = INSTITUTIONAL_LOB[mem_key]
    
    # تحويل قوائم الـ Deque إلى List لتسهيل العمليات الحسابية
    ofi_scores = list(mem["ofi_window"])
    bid_vols = list(mem["bid_vols"])
    ask_vols = list(mem["ask_vols"])
    
    mean_bid_vol = np.mean(bid_vols)
    mean_ask_vol = np.mean(ask_vols)
    avg_ofi = np.mean(ofi_scores)

    # ====================================================================
    # 🧠 1. محرك التلاعب وإعادة التعبئة (Spoofing & Iceberg Replenishment)
    # ====================================================================
    # معامل الاختلاف (Coefficient of Variation) لكشف الـ Flash Spoofing
    bid_cv = np.std(bid_vols) / (mean_bid_vol + 1e-8)
    ask_cv = np.std(ask_vols) / (mean_ask_vol + 1e-8)

    # محرك إعادة التعبئة (Iceberg Replenishment Rate):
    # نختبر ما إذا كانت السيولة تُسحب ثم تتجدد فجأة في غضون أجزاء من الثانية
    is_ask_replenished = False
    is_bid_replenished = False
    for i in range(1, len(ask_vols)-1):
        if ask_vols[i] < ask_vols[i-1] * 0.9 and ask_vols[i+1] > ask_vols[i] * 1.05:
            is_ask_replenished = True
        if bid_vols[i] < bid_vols[i-1] * 0.9 and bid_vols[i+1] > bid_vols[i] * 1.05:
            is_bid_replenished = True

    # القرار: هل يوجد تلاعب خوارزمي؟
    is_spoofed = bool((bid_cv > 0.6 or ask_cv > 0.6) or is_ask_replenished or is_bid_replenished)

    # ====================================================================
    # 🧠 2. محرك "هشاشة السيولة" (Orderbook Slippage Vulnerability - OSV)
    # ====================================================================
    mean_bid_vol_usd = mean_bid_vol * current_price
    
    # أ. اختبار الصدمة (Shock Absorption Test):
    is_fragile_vs_volume = mean_bid_vol_usd < (recent_vol_usd * 0.02)

    # ب. اختبار تشتت السيولة (Spread Risk):
    best_bid = mem["best_bid"]
    best_ask = mem["best_ask"]
    spread_pct = (best_ask - best_bid) / (best_bid + 1e-8)
    
    # الجدار وهمي ومثقوب إذا كان السبريد واسعاً والسيولة أقل من 5% من الفوليوم المعتاد
    is_hollow_spread = spread_pct > 0.002 and mean_bid_vol_usd < (recent_vol_usd * 0.05)

    # القرار: هل الأوردر بوك فارغ وسينزلق؟
    is_hollow = bool(is_fragile_vs_volume or is_hollow_spread)

    # ====================================================================
    # 🧠 3. محرك الاتجاه اللحظي (OFI & Skewness Regime)
    # ====================================================================
    # تطبيع الخلل ليكون قيمة بين -1 و 1
    total_vol_mean = mean_bid_vol + mean_ask_vol + 1e-8
    imbalance = avg_ofi / total_vol_mean

    # ضغط الأوردر بوك الحقيقي (النسبة بين متوسط الطلبات الحية والعروض)
    bid_pressure = float(mean_bid_vol / (mean_ask_vol + 1e-8))
    
    # النظام الصاعد يتطلب أن تكون الطلبات أثقل بـ 1.5 مرة من العروض
    is_bull_regime = bid_pressure > 1.5

    # إرجاع نفس هيكل البيانات القديم بالضبط لكي لا ينكسر الرادار
    return {
        "is_hollow": is_hollow,
        "imbalance": round(float(max(-1.0, min(1.0, imbalance))), 2),
        "is_spoofed": is_spoofed,
        "bid_pressure_ratio": bid_pressure,
        "is_bull_regime": is_bull_regime
    }


# --- دالة التحليل المعدلة ---
async def evaluate_dex_risk(liquidity_usd: float, vol_24h: float):
    """محرك تقييم مخاطر السيولة في الـ DEX"""
    risk_warnings_ar = []
    risk_warnings_en = []
    risk_score = 0
    
    # 1. فحص فقر السيولة (Liquidity Void)
    if liquidity_usd < 50000:
        risk_warnings_ar.append(" خطر عالي: سيولة المجمع (LP) أقل من 50 ألف دولار! (سهلة التلاعب/السحب).")
        risk_warnings_en.append(" HIGH RISK: Liquidity Pool < $50k! (Rug-pull/Manipulation risk).")
        risk_score -= 5
    elif liquidity_usd < 200000:
        risk_warnings_ar.append(" تنبيه: سيولة المجمع ضعيفة، توقع انزلاق سعري (Slippage) عالي.")
        risk_warnings_en.append(" WARNING: Low Liquidity, expect high slippage.")
        risk_score -= 2
        
    # 2. فحص نسبة الفوليوم للسيولة (Volume/Liquidity Ratio)
    # إذا كان الفوليوم اليومي أعلى من السيولة بـ 10 أضعاف، هذا تدوير وهمي (Wash Trading)
    if liquidity_usd > 0 and (vol_24h / liquidity_usd) > 10:
         risk_warnings_ar.append(" تحذير: الفوليوم أعلى من السيولة بشكل غير منطقي (احتمال Wash Trading).")
         risk_warnings_en.append(" WARNING: Abnormal Vol/Liq ratio (Possible Wash Trading).")
         risk_score -= 3

    return risk_warnings_ar, risk_warnings_en, risk_score
def calculate_mtfa_context_sync(candles_4h, candles_1d, candles_1w):
    """
    [Tier-1 Quant] Volatility Compression MTFA Engine (Macro Coil)
    يعالج "تأخر المؤشرات" بالبحث عن الانضغاط السعري (Compression) على الفريمات الكبيرة
    بدلاً من انتظار اتجاه صريح قد يكون متأخراً (Lagging Trend).
    """
    import numpy as np
    import pandas as pd

    def get_tf_state(candles, drop_unclosed=False):
        if not candles or isinstance(candles, Exception) or len(candles) < 50:
            return "Unknown", False
        
        df = pd.DataFrame(candles).iloc[:, :6]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
        
        if drop_unclosed and len(df) > 1:
            df = df.iloc[:-1].copy()
            
        df[["high", "low", "close", "volume"]] = df[["high", "low", "close", "volume"]].apply(pd.to_numeric, errors='coerce')
        
        # 1. تحديد الاتجاه (Trend) عبر VWAP المرتكز لضمان عدم الانحياز السعري
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3.0
        df['vwap'] = (df['typical_price'] * df['volume']).cumsum() / (df['volume'].cumsum() + 1e-8)
        current_close = df['close'].iloc[-1]
        current_vwap = df['vwap'].iloc[-1]
        
        trend = "Bullish" if current_close > current_vwap else "Bearish"
        
        # 2. 🧠 تحديد الانضغاط (Compression/Squeeze) عبر عرض البولينجر باندز
        sma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std(ddof=0)
        bb_width = ((4 * std20) / sma20).iloc[-1]
        
        # إذا كان عرض الباند أقل من 15% (وهي عتبة مؤسساتية لسوق الكريبتو)، فهناك تجميع هادئ
        is_compressing = bb_width < 0.15 
        
        return trend, is_compressing

    # استخراج حالة الاتجاه والانضغاط لكل فريم
    trend_4h, comp_4h = get_tf_state(candles_4h, drop_unclosed=False)
    trend_1d, comp_1d = get_tf_state(candles_1d, drop_unclosed=True)
    trend_1w, comp_1w = get_tf_state(candles_1w, drop_unclosed=True)

    # 🧠 المنطق المؤسساتي المتقدم (Macro Coil & Kinetic Expansion)
    tp_modifier = 1.0 
    status_ar = ""
    status_en = ""

    # 1. 🚀 الانفجار السعري من الانضغاط (The Ultimate Macro Squeeze Breakout)
    # الفريم الأسبوعي أو اليومي مضغوط (تجميع هادئ) + الفريم اللحظي (4H) بدأ بالصعود
    if (comp_1w or comp_1d) and trend_4h == "Bullish":
        tp_modifier = 1.50 # نوسع الأهداف جداً لأن الانفجار سيكون عنيفاً
        status_ar = "<b>انفجار انضغاطي (Macro Coil Breakout):</b> الفريمات الكبيرة في مرحلة تجميع صامت والسيولة اللحظية بدأت بالدخول. أفضل فرصة لاصطياد قاع الانفجار."
        status_en = "<b>Macro Coil Breakout:</b> HTF is in a tight volatility squeeze while 4H is expanding upwards. Optimal bottom-catching setup."

    # 2. التوافق الذهبي (Trend Continuation)
    elif trend_4h == "Bullish" and trend_1d == "Bullish" and trend_1w == "Bullish":
        tp_modifier = 1.20 
        status_ar = "<b>استمرار الاتجاه (Golden Continuation):</b> جميع الفريمات في ترند صاعد. السعر يواصل رحلة الاكتشاف."
        status_en = "<b>Golden Continuation:</b> All timeframes aligned bullishly. Asset is in steady markup phase."

    # 3. صيد التراجعات (Dip Buying)
    elif trend_4h == "Bearish" and trend_1d == "Bullish":
        tp_modifier = 1.0 
        status_ar = "<b>تراجع صحي (Pullback / Dip Buy):</b> الماكرو صاعد لكن اللحظي يصحح. منطقة ممتازة للشراء مع الاتجاه العام."
        status_en = "<b>Healthy Pullback:</b> Macro is Bullish, execution timeframe is retracing. Prime Dip Buying zone."

    # 4. ارتداد عكس الاتجاه (Counter-Trend Scalp)
    elif trend_4h == "Bullish" and trend_1d == "Bearish":
        tp_modifier = 0.50 # نخنق الأهداف لتجنب الفخ والرجوع بسرعة للأسفل
        status_ar = "<b>ارتداد عكس الاتجاه (Counter-Trend):</b> الماكرو هابط بقوة والـ 4H يرتد. <b>تم تقريب الأهداف للهروب السريع (Hit & Run).</b>"
        status_en = "<b>Counter-Trend Scalp:</b> Daily is Bearish while 4H is bouncing. <b>Targets tightened for a Hit & Run.</b>"

    # 5. الانهيار المتزامن (Death Spiral)
    elif trend_4h == "Bearish" and trend_1d == "Bearish" and trend_1w == "Bearish":
        tp_modifier = 1.20 # توسيع لأهداف الشورت
        status_ar = "<b>انهيار متزامن (Death Spiral):</b> توافق هبوطي تام. السعر في مرحلة تفريغ مؤسساتي كامل."
        status_en = "<b>Death Spiral:</b> Perfect bearish alignment. Asset is in full structural capitulation."

    else:
        status_ar = "<b>تذبذب هيكلي (Mixed Flow):</b> لا يوجد إجماع هيكلي واضح بين الفريمات."
        status_en = "<b>Mixed Flow:</b> Timeframes lack structural consensus."

    # 🛡️ الحفاظ على المخرجات تماماً كما يتوقعها باقي الكود لضمان عدم توقف دوال الفيتو
    return {
        "macro_1w": trend_1w, "swing_1d": trend_1d, "exec_4h": trend_4h,
        "tp_modifier": tp_modifier, "ar_text": status_ar, "en_text": status_en
    }

import time

def extract_institutional_memory(symbol: str):
    """
    [Institutional Memory Bridge] - جسر الذاكرة المؤسساتية
    يقرأ بصمت من غرفة الاحتضان والماكرو دون أي تداخل مع مهام الرادار اللحظية.
    """
    # الرادار يخزن العملات مع USDT، لذا نجهز الرمز ليتطابق مع المصفوفة
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol
    
    # 1. قراءة الماكرو (Macro Context)
    macro_score = MACRO_CACHE.get("sentiment_score", 50.0)
    onchain_fueled_macro = MACRO_CACHE.get("onchain_liquidity_score", 0.0) > 15.0
    
    # 2. فحص غرفة الاحتضان (Incubation Check)
    is_incubated = pair in INCUBATION_MATRIX
    
    alert_ar = ""
    alert_en = ""
    
    if is_incubated:
        inc_data = INCUBATION_MATRIX[pair]
        # حساب كم يوم مضى على طبخ هذه العملة في غرفة الاحتضان
        days_in_matrix = (time.time() - inc_data.get('incubation_start', time.time())) / 86400
        days_str = f"{days_in_matrix:.1f}" if days_in_matrix >= 0.1 else "أقل من يوم"
        days_str_en = f"{days_in_matrix:.1f} days" if days_in_matrix >= 0.1 else "less than a day"
        
        # 🧠 صياغة تنبيه الألفا (Alpha Alert) بناءً على قوة السيولة
        alert_ar += f"\n <b>تنبيه ألفا (Alpha Alert):</b>\n"
        alert_ar += f" هذه العملة تخضع حالياً للتجميع المخفي وموجودة في <b>غرفة الاحتضان المؤسساتية</b> منذ {days_str}.\n"
        
        alert_en += f"\n <b>ALPHA ALERT:</b>\n"
        alert_en += f" This asset is undergoing stealth accumulation and has been in the <b>Incubation Matrix</b> for {days_str_en}.\n"
        
        if inc_data.get('onchain_fueled') or onchain_fueled_macro:
            alert_ar += " <b>عامل محفز (Catalyst):</b> العملة تتلقى دعماً هائلاً من سيولة الـ On-Chain اللحظية!\n"
            alert_en += " <b>Macro Catalyst:</b> Asset is experiencing massive On-Chain liquidity inflows!\n"
            
    # 3. صياغة تقييم الماكرو الكلي لربطه مع التحليل
    macro_state_ar = "إيجابية" if macro_score > 60 else "سلبية" if macro_score < 40 else "حيادية"
    macro_state_en = "Bullish" if macro_score > 60 else "Bearish" if macro_score < 40 else "Neutral"
            
    return {
        "is_incubated": is_incubated,
        "text_ar": alert_ar,
        "text_en": alert_en,
        "macro_state_ar": macro_state_ar,
        "macro_state_en": macro_state_en
    }
import numpy as np
import pandas as pd

def calculate_institutional_vpvr_confluence(candles_4h, candles_1d, current_price, trend_direction):
    """
    [VPVR Confluence Engine] - محرك التقاء السيولة المؤسساتي
    يقارن الفوليوم التراكمي لـ 4 ساعات مع اليومي لاستخراج (نواة السيولة / المغناطيس)
    """
    # 🛡️ حماية: التأكد من سلامة البيانات
    if not candles_4h or not candles_1d or isinstance(candles_4h, Exception) or isinstance(candles_1d, Exception):
        return None, None, None, None

    def get_hvn_profile(candles, num_bins=60):
        df = pd.DataFrame(candles).iloc[:, :6]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
        df[["high", "low", "close", "volume"]] = df[["high", "low", "close", "volume"]].apply(pd.to_numeric)
        
        # نأخذ آخر 300 شمعة لرسم دورة السوق الحالية
        recent_df = df.tail(300).copy()
        min_p, max_p = recent_df['low'].min(), recent_df['high'].max()
        if min_p == max_p: return pd.DataFrame()
        
        bins = np.linspace(min_p, max_p, num_bins)
        recent_df['typical_price'] = (recent_df['high'] + recent_df['low'] + recent_df['close']) / 3
        recent_df['bin'] = np.digitize(recent_df['typical_price'], bins) - 1
        
        profile = recent_df.groupby('bin')['volume'].sum().reset_index()
        profile['price'] = profile['bin'].apply(lambda x: bins[x] if x < len(bins) else bins[-1])
        
        # تحويل الفوليوم لنسبة مئوية (Normalize) لاكتشاف الذروة
        profile['vol_norm'] = profile['volume'] / (profile['volume'].max() + 1e-8)
        return profile

    prof_4h = get_hvn_profile(candles_4h)
    prof_1d = get_hvn_profile(candles_1d)
    
    if prof_4h.empty or prof_1d.empty:
        return None, None, None, None

    # استخراج العقد ذات السيولة العالية (High Volume Nodes - HVN)
    hvns_4h = prof_4h[prof_4h['vol_norm'] > 0.6].copy()
    hvns_1d = prof_1d[prof_1d['vol_norm'] > 0.6].copy()
    
    magnets = []
    tolerance = current_price * 0.015 # 1.5% نسبة تفاوت مقبولة بين الفريمين (Overlap Zone)
    
    # 🧠 البحث عن "التقاء السيولة" (Confluence)
    for _, r4 in hvns_4h.iterrows():
        p4 = r4['price']
        # هل توجد عقدة سيولة يومية في نفس هذه المنطقة؟
        nearby_1d = hvns_1d[(hvns_1d['price'] >= p4 - tolerance) & (hvns_1d['price'] <= p4 + tolerance)]
        
        if not nearby_1d.empty:
            # تم العثور على مغناطيس مؤسساتي! (تقاطع الـ 4H مع اليومي)
            score = r4['vol_norm'] + nearby_1d['vol_norm'].max()
            magnets.append({'price': p4, 'score': score, 'is_macro': True})
        else:
            # عقدة سيولة لحظية (4H فقط)
            magnets.append({'price': p4, 'score': r4['vol_norm'], 'is_macro': False})
            
    magnets_df = pd.DataFrame(magnets)
    if magnets_df.empty: return None, None, None, None
        
    # فصل مناطق السيولة العلوية (مقاومات/أهداف) والسفلية (دعوم/وقف)
    above = magnets_df[magnets_df['price'] > current_price * 1.005].sort_values('price')
    below = magnets_df[magnets_df['price'] < current_price * 0.995].sort_values('price', ascending=False)
    
    MAX_SL_PCT = 0.08 # أقصى وقف خسارة 8%
    
    if trend_direction == "Bullish":
        targets = above.nlargest(3, 'score').sort_values('price')['price'].tolist()
        sl_nodes = below.nlargest(2, 'score')
        sl = sl_nodes['price'].iloc[0] if not sl_nodes.empty else current_price * 0.95
        sl = max(sl, current_price * (1 - MAX_SL_PCT))
    else: # Bearish
        targets = below.nlargest(3, 'score').sort_values('price', ascending=False)['price'].tolist()
        sl_nodes = above.nlargest(2, 'score')
        sl = sl_nodes['price'].iloc[0] if not sl_nodes.empty else current_price * 1.05
        sl = min(sl, current_price * (1 + MAX_SL_PCT))

    # تعويض الأهداف الناقصة إن وجدت
    while len(targets) < 3:
        last_t = targets[-1] if targets else current_price
        multiplier = 1.03 if trend_direction == "Bullish" else 0.97
        targets.append(last_t * multiplier)
        
    return sl, targets[0], targets[1], targets[2]

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
        try:
            await cb.message.edit_text(
                "⚠️ انتهت تجربتك المجانية. للوصول الكامل، يرجى الاشتراك." if lang=="ar" else "⚠️ Trial ended. Please subscribe.",
                reply_markup=get_payment_kb(lang)
            )
        except Exception:
            pass # تجاهل خطأ عدم تعديل الرسالة
        return await cb.answer("⚠️ انتهى الاشتراك / Subscription Ended", show_alert=True)


    try:
        await cb.message.edit_text("🤖 جاري التحليل..." if lang=="ar" else "🤖 Analyzing...")
    except Exception as e:
        if "message is not modified" in str(e):
            pass  
        else:
            print(f"Edit msg error in analysis: {e}")

    clean_sym = sym.replace("USDT", "").strip().upper()
    
    # ====================================================================
    # 🧠 حقن الذاكرة المؤسساتية (Incubation & Macro Check)
    # ====================================================================
    inst_memory = extract_institutional_memory(clean_sym)

    
    # --- التعديل الجديد: جلب السيولة وفحص الأمان للـ DEX ---
    is_dex = data.get('is_dex', False)
    dex_liquidity = data.get('liquidity_usd', 0.0) 
    dex_vol = data.get('volume_24h', 0.0)
    
    # 🚨 السطران المفقودان: استخراج الشبكة وعنوان المجمع من ذاكرة المستخدم!
    network = data.get('network', '')
    pool_address = data.get('pool_address', '')
    
    depth_data = {} # 🛡️ الجدار الآمن: تعريف المتغير فارغاً لمنع كراش الديكس في محرك الـ AI

    if is_dex:
        # 🚨 تفعيل محرك فحص أمان الديكس (الذي كان صامتاً في الكود القديم)
        dex_warnings_ar, dex_warnings_en, _ = await evaluate_dex_risk(dex_liquidity, dex_vol)
    else:
        dex_warnings_ar, dex_warnings_en = [], []

        # ====================================================================
    # 🌐 1. جلب الفريمات الثلاثة المتزامنة (3D MTFA Fetching)
    # نستخدم asyncio.gather لجلب الثلاثة معاً في نفس الوقت بدون تأخير
    # ====================================================================
    if is_dex:
        tasks = [
            get_candles_dex(network, pool_address, "4h", limit=500),
            get_candles_dex(network, pool_address, "1d", limit=200),
            get_candles_dex(network, pool_address, "1w", limit=100)
        ]
        candles_4h, candles_1d, candles_1w = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        tasks = [
            get_candles_binance(f"{clean_sym}USDT", "4h", limit=500),
            get_candles_binance(f"{clean_sym}USDT", "1d", limit=200),
            get_candles_binance(f"{clean_sym}USDT", "1w", limit=100)
        ]
        candles_4h, candles_1d, candles_1w = await asyncio.gather(*tasks, return_exceptions=True)

    # 🛡️ تحديد الفريم الأساسي الذي طلبه المستخدم لتشغيل باقي الدوال (لكي لا يتعطل الكود القديم)
    target_tf_index = {"4h": 0, "daily": 1, "weekly": 2}.get(tf, 0)
    candles = [candles_4h, candles_1d, candles_1w][target_tf_index]

    if isinstance(candles, Exception) or not candles or len(candles) < 3:
        if lang == "ar":
            error_msg = f"⚠️ <b>عذراً، بيانات الإطار الزمني غير كافية لعملة {clean_sym} حالياً.</b>\n🔄 يرجى اختيار إطار زمني أقل (مثل 4 ساعات)."
        else:
            error_msg = f"⚠️ <b>Sorry, insufficient data for {clean_sym} on this timeframe.</b>\n🔄 Please choose a lower timeframe (like 4H)."
        
        try:
            return await cb.message.edit_text(error_msg, parse_mode=ParseMode.HTML)
        except Exception:
            return await cb.message.answer(error_msg, parse_mode=ParseMode.HTML)

    # 🧠 تشغيل محرك التوافق الزمني في الخلفية
    mtfa_context = await asyncio.to_thread(calculate_mtfa_context_sync, candles_4h, candles_1d, candles_1w)
    # ====================================================================


    # 🟢 لاحظ هنا: شلنا (if candles:) وكل الأسطر اللي تحتها رجعناها لورا مسافة عشان تصير أساسية بالدالة
    # 🟢 التعديل الأول: نقل حساب المؤشرات إلى الخلفية لمنع تعليق البوت
    last_rsi, last_macd, last_bb, last_vol, _, _ = await asyncio.to_thread(compute_indicators, candles)
        
    import pandas as pd 
    df = pd.DataFrame(candles)
    if len(df.columns) >= 7:
        df = df.iloc[:, :7]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open", "taker_buy_vol"]
        for col in ["close", "high", "low", "open", "volume", "taker_buy_vol"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    else:
        df = df.iloc[:, :6]
        df.columns = ["timestamp", "volume", "close", "high", "low", "open"]
        for col in ["close", "high", "low", "open", "volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        # 🧠 الحل الكمّي لسد ثغرة الديكس (Tick Volume Proxy)
        # استنتاج التدفق الشرائي رياضياً من ضغط الإغلاق لتغذية الـ AI والـ CVD ببيانات منطقية
        price_range = df["high"] - df["low"]
        price_range = price_range.replace(0, 1e-8) # حماية من القسمة على صفر
        buy_pressure = (df["close"] - df["low"]) / price_range
        df["taker_buy_vol"] = df["volume"] * buy_pressure


        
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
    macro_deriv_action = None # 👈 السطر الجديد

    
    # [إصلاح صانع السوق]: إعطاء قيم افتراضية للمتغيرات لمنع الكراش في عملات الديكس
    cvd_trend_val = 0.0
    limit_abs_signal = None
    
    # 🟢 الحل: نقل حساب Z-Score هنا ليعمل على CEX و DEX معاً (الفوليوم هو سلاحك الوحيد في الديكس)
    z_score, _, _ = calculate_volume_zscore(df, window=720)

    # 2. انزل للأسفل عند قسم (تنفيذ المهام المتزامنة tasks_to_run) وقم بتعديل استدعاء depth_task
    if not is_dex:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                safe_price = float(price)
                safe_old_price = float(df["close"].iloc[-3]) if len(df) > 3 else safe_price

                # 🟢 التعديل الجذري: لا تطلب الأوردر بوك إذا كان الفريم كبير!
                                # 🟢 التعديل الجذري: لا تطلب الأوردر بوك إذا كان الفريم كبير!
                if current_tf["use_ob"]:
                    # نحسب الفوليوم اللحظي لآخر 5 شموع ليكون معياراً لقوة الأوردر بوك (للـ 4 ساعات فقط)
                    recent_vol_usd = df["volume"].tail(5).mean() * safe_price if len(df) >= 5 else 15000.0
                    depth_task = analyze_orderbook_advanced_manual(clean_sym, client, safe_price, recent_vol_usd)
                else:
                    # 🚀 إذا كنا في فريم يومي أو أسبوعي، نشغل محرك المشتقات الكلي بدل الأوردر بوك
                    macro_deriv_action = await analyze_macro_derivatives_divergence(clean_sym, client, df, tf)
                    
                    # إرجاع بيانات محايدة للأوردر بوك لمنع تلوث التحليل اللحظي
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
                    flow_task = get_institutional_orderflow(f"{clean_sym}USDT", client, minutes=240)
                # 4. توافق الفريم لعقود المشتقات
                # [إصلاح صانع السوق]: استدعاء الدالة الكاملة لضمان جلب الـ OI والـ Funding معاً
                await binance_rate_limit_event.wait()
                futures_task = get_futures_liquidity(clean_sym, client, safe_price, safe_old_price)

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

                # استخراج Futures الصحيح (بما في ذلك التمويل)
                futures_data = results[2] if not isinstance(results[2], Exception) else (0.0, None, 0.0, 0.0, 0.0)
                _, fut_sig, funding_val, oi_change, _ = futures_data



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
            # [إصلاح صانع السوق]: حماية إضافية لو تعطل الاتصال
            cvd_trend_val = 0.0
            limit_abs_signal = None


    # 2. كشف الفخاخ وتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه        # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)    # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)
    import time
    # 2. كشف الفخاخ والارتدادات لتوحيد الاتجاه بطريقة مؤسساتية (Quant Trend Unification)
    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
    classic_trend = "Bullish" if ema20 > ema50 else "Bearish"
    
    final_trend_dir = classic_trend
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    avg_vol_20 = df["volume"].tail(20).mean()
    current_vol = df["volume"].iloc[-1]

    # ====================================================================
    # 🧠 محرك "إسقاط الفوليوم التنبؤي" (Predictive Volume Projection Engine)
    # يسبق صانع السوق بخطوة عبر قياس "سرعة تدفق السيولة" مع كابح الخداع اللحظي
    # ====================================================================
    # 1. تحديد الفريم الزمني بدقة (طول الشمعة بالثواني)
    # نعمل هكذا لكي يكون الكود مرناً لأي فريم (ساعة، 4 ساعات، 15 دقيقة)
    candle_open_ts = float(df["timestamp"].iloc[-1])
    prev_candle_ts = float(df["timestamp"].iloc[-2])
    candle_duration = max(60.0, candle_open_ts - prev_candle_ts) 
    
    # 2. حساب الوقت المنقضي من الشمعة الحالية
    current_ts = time.time()
    elapsed_time = max(1.0, current_ts - candle_open_ts)
    elapsed_time = min(elapsed_time, candle_duration) # لا يتجاوز طول الشمعة
    
    # 3. حساب سرعة السيولة (Volume Velocity - Vol/Sec)
    current_velocity = current_vol / elapsed_time
    avg_velocity = avg_vol_20 / candle_duration
    
    # 4. الإسقاط الخطي مع كابح الضجيج (Linear Projection with Anti-Spoof Dampener)
    time_progress_pct = elapsed_time / candle_duration
    
    # استدعاء قيمة الـ CVD الحالية بشكل آمن
        # استدعاء قيمة الـ CVD الحالية بشكل آمن
    current_cvd_check = cvd_trend_val

    if time_progress_pct < 0.15: # أول 15% من عمر الشمعة (مرحلة فخاخ الحيتان)
 # أول 15% من عمر الشمعة (مرحلة فخاخ الحيتان)
        # [تعديل صانع السوق]: لا نقبل الفوليوم العنيف في البداية إلا إذا كان مدعوماً بتدفق شرائي (Taker Buy) حقيقي
        if current_vol > (avg_vol_20 * 0.3) and current_cvd_check > 0:
            projected_vol = current_velocity * candle_duration
        else:
            projected_vol = current_vol # الفوليوم وهمي أو بيعي، أوقف التنبؤ!
 # تجاهل الإسقاط، اعتبر الفوليوم كما هو لتجنب الفخ
    else:
        # الإسقاط الطبيعي لما بعد مرحلة الخطر
        projected_vol = current_velocity * candle_duration

    # 5. اتخاذ قرار الانفجار (Surge) بناءً على "المستقبل" و"السرعة"
    # الانفجار يتحقق إذا كان الفوليوم الفعلي ضخم (شمعة أغلقت)، أو الفوليوم التنبؤي مرعب، أو سرعة التدفق أضعاف المعتاد
    vol_surge = (current_vol > (avg_vol_20 * 1.5)) or (projected_vol > (avg_vol_20 * 1.8)) or (current_velocity > (avg_velocity * 2.2))
    # ====================================================================
    # أ. شروط انعكاس الاتجاه من هابط إلى صاعد (اصطياد القاع الاستباقي)
    if classic_trend == "Bearish":
        is_short_covering = (fut_sig == "Short_Covering")
        if not is_short_covering:
            if (cvd_sig == "Micro_Silent_Accumulation" or buy_v > (sell_v * 1.5)) or (last_rsi < 35 and last_macd > 0 and vol_surge):
                final_trend_dir = "Bullish"

    # ب. شروط انعكاس الاتجاه من صاعد إلى هابط (الهروب المبكر من القمة)
    elif classic_trend == "Bullish":
        is_long_squeeze = (fut_sig == "OI_Rising" and delta_usd < 0 and funding_val > 0.001)
        if not is_long_squeeze:
            if (cvd_sig == "Hidden_Distribution" or sell_v > (buy_v * 1.5)) or (last_rsi > 75 and last_macd < 0 and vol_surge):
                final_trend_dir = "Bearish"

    # 🛡️ حق النقض المؤسساتي (Macro Veto) لمنع انفصام الاتجاه
    is_1w_bear, is_1d_bear, is_4h_bear = mtfa_context['macro_1w'] == "Bearish", mtfa_context['swing_1d'] == "Bearish", mtfa_context['exec_4h'] == "Bearish"
    is_1w_bull, is_1d_bull, is_4h_bull = mtfa_context['macro_1w'] == "Bullish", mtfa_context['swing_1d'] == "Bullish", mtfa_context['exec_4h'] == "Bullish"
    
    if is_1w_bear and is_1d_bear and is_4h_bear:
        final_trend_dir = "Bearish" # إجبار الاتجاه ليكون هابطاً في حالة Death Spiral
    elif is_1w_bull and is_1d_bull and is_4h_bull:
        final_trend_dir = "Bullish" # إجبار الاتجاه ليكون صاعداً في التوافق الذهبي
 
    # 3. حساب الدعم والمقاومة والأهداف بناءً على الاتجاه "المُوحّد" لمنع التضارب
            # 3. حساب الدعم والمقاومة والأهداف في الخلفية لمنع التضارب والتعليق
    trend_dir, trend_str, market_action, adx_val, calc_sl, calc_tp1, calc_tp2, calc_tp3, calc_sup, calc_res = await asyncio.to_thread(
        calculate_smart_trend_and_targets, df, price, db_vol_float, lang, final_trend_dir
    )
    # ====================================================================
    # 🧲 محرك خريطة السيولة المؤسساتية (VPVR Confluence Overwrite)
    # ====================================================================
    conf_sl, conf_tp1, conf_tp2, conf_tp3 = await asyncio.to_thread(
        calculate_institutional_vpvr_confluence, candles_4h, candles_1d, price, final_trend_dir
    )
    if conf_sl is not None:
        calc_sl, calc_tp1, calc_tp2, calc_tp3 = conf_sl, conf_tp1, conf_tp2, conf_tp3
    # ====================================================================
    # 🎯 محرك كبح المخاطر المؤسساتي (TP & SL Dynamic Risk Modifier)
    # ====================================================================
    dist_tp1, dist_tp2, dist_tp3 = calc_tp1 - price, calc_tp2 - price, calc_tp3 - price
    dist_sl = calc_sl - price
    
    # 🧠 التعديل الجراحي: ربط كابح المخاطر بالاتجاه النهائي الفعلي (Final Trend)
    dynamic_tp_mod = 1.0
    if final_trend_dir == "Bullish":
        if mtfa_context['swing_1d'] == "Bearish": 
            dynamic_tp_mod = 0.5 # 🛡️ ارتداد صعودي عكس انهيار = خنق الأهداف للهروب
        elif mtfa_context['exec_4h'] == "Bullish" and mtfa_context['swing_1d'] == "Bullish" and mtfa_context['macro_1w'] == "Bullish": 
            dynamic_tp_mod = 1.3 # 🚀 توافق صاعد كلي = توسيع الأهداف
    else: # Bearish
        if mtfa_context['swing_1d'] == "Bullish": 
            dynamic_tp_mod = 0.5 # 🛡️ تصحيح هبوطي عكس ترند صاعد = خنق الأهداف
        elif mtfa_context['exec_4h'] == "Bearish" and mtfa_context['swing_1d'] == "Bearish" and mtfa_context['macro_1w'] == "Bearish": 
            dynamic_tp_mod = 1.3 # 🚀 توافق هابط كلي (Death Spiral) = توسيع أهداف الشورت

    # تعديل الأهداف حسب الماكرو الجديد
    calc_tp1 = price + (dist_tp1 * dynamic_tp_mod)
    calc_tp2 = price + (dist_tp2 * dynamic_tp_mod)
    calc_tp3 = price + (dist_tp3 * dynamic_tp_mod)
    # 🛡️ تضييق الوقف (SL) مع حماية مؤسساتية (Hard Floor)
    sl_modifier = dynamic_tp_mod if dynamic_tp_mod < 1.0 else 1.0
    raw_sl = price + (dist_sl * sl_modifier)
    
    min_sl_distance_pct = 0.015 # حد أدنى 1.5% لمسافة وقف الخسارة
    
    if final_trend_dir == "Bullish":
        max_allowed_sl = price * (1.0 - min_sl_distance_pct)
        # نأخذ الرقم الأبعد عن السعر الحالي لحماية الوقف من ذيول الشموع
        calc_sl = min(raw_sl, max_allowed_sl) 
    else: # Bearish
        min_allowed_sl = price * (1.0 + min_sl_distance_pct)
        calc_sl = max(raw_sl, min_allowed_sl)

    # ====================================================================
    # 🧠 المحرك الكمي المطور (العمليات الحسابية يجب أن تسبق الـ AI)
    # ====================================================================
    recent_returns = df['close'].pct_change().dropna().tail(20)
    price_z_score = (recent_returns.iloc[-1] - recent_returns.mean()) / (recent_returns.std() + 1e-8)
    
    vol_edge = quant_cdf_score(z_score, limit=100.0)
    avg_vol_usd_recent = df["volume"].tail(20).mean() * price
    flow_ratio = (delta_usd / avg_vol_usd_recent) if avg_vol_usd_recent > 0 else 0.0
    if is_dex and 'cvd' in df.columns and len(df) >= 10:
        flow_ratio = (df['cvd'].diff(5).iloc[-1] / avg_vol_usd_recent) if avg_vol_usd_recent > 0 else 0.0
        
    flow_edge = quant_sigmoid_score(flow_ratio, sensitivity=5.0, limit=100.0)
    safe_rsi = float(last_rsi) if not pd.isna(last_rsi) else 50.0
    safe_macd = float(last_macd) if not pd.isna(last_macd) else 0.0

    if final_trend_dir == "Bullish":
        conviction_score = (vol_edge * 0.30) + (flow_edge * 0.50) + (safe_rsi * 0.20)
        if price_z_score > 2.0 and flow_edge < 50.0: conviction_score *= math.exp(-0.5 * price_z_score) 
    else:
        conviction_score = (vol_edge * 0.30) + ((100.0 - flow_edge) * 0.50) + ((100.0 - safe_rsi) * 0.20)
        if price_z_score < -2.0 and flow_edge > 50.0: conviction_score *= math.exp(0.5 * price_z_score) 

    # ====================================================================
    # 🤖 محرك تقييم الثقة التنبؤي (AI Call & Justification)
    # ====================================================================
    try:
        micro_vol = float(df['close'].tail(20).pct_change().std() * 100) if len(df) > 20 else 0.0
        ml_features = {
            'market_regime': 0,
            'sp500_trend': float(MACRO_CACHE.get("sp500_trend", 0.0)),
            'sentiment_score': float(MACRO_CACHE.get("sentiment_score", 50.0)),
            'z_score': float(z_score),
            'cvd_to_vol_ratio': float((delta_usd / (avg_vol_usd_recent + 1e-8)) * 100),
            'ofi_imbalance': float(depth_data.get('imbalance', 0.0)) if not is_dex else 0.0,
            'ob_skewness': float(depth_data.get('bid_pressure_ratio', 1.0)) if not is_dex else 1.0,
            'whale_inflow': float(MACRO_CACHE.get("global_funding_health", 50.0) / 50.0),
            'adx': float(adx_val),
            'rsi': float(safe_rsi),
            'micro_volatility': micro_vol,
            'cvd_divergence': 1.0 if (price > df['close'].ewm(span=200).mean().iloc[-1] and delta_usd < 0) else 0.0,
            'funding_rate': float(funding_val)
        }
        ai_conviction, _, _, _ = await asyncio.to_thread(predict_signal_sync, ml_features)
    except: ai_conviction = -1.0

    final_conviction_score = ai_conviction if ai_conviction != -1.0 else conviction_score
    ai_badge = "🤖 ML Model" if ai_conviction != -1.0 else "📐 Quant Algo"

    justification_ar, justification_en = [], []
    
    # 1. المزامنة المطلقة مع محرك MTFA (فحص الفريمات الثلاثة بدقة)
    is_4h_bull = mtfa_context['exec_4h'] == "Bullish"
    is_1d_bull = mtfa_context['swing_1d'] == "Bullish"
    is_1w_bull = mtfa_context['macro_1w'] == "Bullish"
    
    is_4h_bear = mtfa_context['exec_4h'] == "Bearish"
    is_1d_bear = mtfa_context['swing_1d'] == "Bearish"
    is_1w_bear = mtfa_context['macro_1w'] == "Bearish"

    if final_trend_dir == "Bullish":
        if is_4h_bull and is_1d_bull and is_1w_bull:
            justification_ar.append("توافق صاعد كلي"); justification_en.append("Bullish Macro Alignment")
        elif is_1d_bear:
            justification_ar.append("ارتداد عكس الاتجاه"); justification_en.append("Counter-Trend Bounce")
        else:
            justification_ar.append("تذبذب هيكلي"); justification_en.append("Mixed Flow")
    else: # Bearish
        if is_4h_bear and is_1d_bear and is_1w_bear:
            justification_ar.append("توافق هابط كلي"); justification_en.append("Bearish Macro Alignment")
        elif is_1d_bull:
            justification_ar.append("تصحيح هبوطي"); justification_en.append("Bearish Correction")
        else:
            justification_ar.append("تذبذب هيكلي"); justification_en.append("Mixed Flow")

    # 2. إصلاح التناقض بين التدفق (CVD) والنص
    if delta_usd > 0 and final_trend_dir == "Bullish" and flow_edge > 50.0: 
        justification_ar.append("تدفق إيجابي"); justification_en.append("Positive Flow")
    elif delta_usd < 0 and final_trend_dir == "Bearish" and flow_edge < 50.0: 
        justification_ar.append("تفريغ حقيقي"); justification_en.append("Genuine Drain")



    just_text_ar = " و ".join(justification_ar) if justification_ar else "زخم هيكلي"
    just_text_en = " & ".join(justification_en) if justification_en else "Structural Momentum"
    
    trend_strength_ar = f"<b>{final_conviction_score:.1f}%</b> (مدعوم بـ: {just_text_ar})"
    trend_strength_en = f"<b>{final_conviction_score:.1f}%</b> (Backed by: {just_text_en})"
    # ====================================================================
    # 💬 المولد النصي المؤسساتي (Quant Text Generator)
    # ====================================================================
    is_fomo_trap = price_z_score > 2.0 and flow_edge < 40.0 and vol_edge < 60.0
    is_capitulation_absorption = price_z_score < -2.0 and flow_edge > 60.0 and vol_edge > 70.0
    # 🛡️ إضافة شرط z_score > 1.0 لضمان عدم مدح سيولة وهمية في أسواق ميتة
    is_trend_backed_by_flow = (final_trend_dir == "Bullish" and flow_edge > 60.0 and z_score > 1.0) or (final_trend_dir == "Bearish" and flow_edge < 40.0 and z_score > 1.0)
    vol_state = "" if is_dex else f"(Z-Score: {z_score:.1f})"

    if lang == "ar":
        trend_strength_display = trend_strength_ar
        real_trend = "صاعد" if final_trend_dir == "Bullish" else "هابط"
        
        if final_trend_dir == "Bullish":
            if is_fomo_trap: market_action = f"فخ تحيز تأكيدي (FOMO Trap)! صعود بلا تدفق مالي {vol_state}."
            elif is_trend_backed_by_flow: market_action = f"ترند صحي ومدعوم بتدفق أموال مؤسساتي {vol_state}."
            else: market_action = f"صعود باهت بسبب ضعف السيولة {vol_state}."
        else:
            if is_capitulation_absorption: market_action = f"استسلام بيعي يقابله امتصاص شرائي ضخم {vol_state}! الحيتان تبني قاعاً."
            elif is_trend_backed_by_flow: market_action = f"سيطرة بيعية وتفريغ مستمر للسيولة {vol_state}."
            else: market_action = f"هبوط بطيء بلا زخم حقيقي {vol_state}."

        if current_tf["use_ob"]: 
            if is_spoofed: market_action += " [رصدنا جدران وهمية للتلاعب]"
            if is_orderbook_hollow: market_action += " [فراغ سيولي، السعر قد ينزلق]"
            if funding_val < -0.001 and not is_dex: market_action += " [خطر تصفية Short Squeeze]"
        elif macro_deriv_action and not is_dex:
            market_action += f" | {macro_deriv_action['ar']}"
            
        dex_alert_str = ("\n🛡️ <b>تدقيق أمان DEX:</b>\n" + "\n".join(dex_warnings_ar) + "\n") if (is_dex and dex_warnings_ar) else ""

    else:
        trend_strength_display = trend_strength_en
        real_trend = "Bullish" if final_trend_dir == "Bullish" else "Bearish"
        
        if final_trend_dir == "Bullish":
            if is_fomo_trap: market_action = f"FOMO TRAP! Price rallied but Orderflow is disconnected {vol_state}."
            elif is_trend_backed_by_flow: market_action = f"Healthy trend backed by Institutional Orderflow {vol_state}."
            else: market_action = f"Low Volume Markup {vol_state}."
        else:
            if is_capitulation_absorption: market_action = f"Capitulation Event! Panic selling met with absorption {vol_state}."
            elif is_trend_backed_by_flow: market_action = f"Genuine distribution and liquidity drain {vol_state}."
            else: market_action = f"Low Volume Markdown {vol_state}."

        if current_tf["use_ob"]: 
            if is_spoofed: market_action += " [Algorithmic Spoofing Detected]"
            if is_orderbook_hollow: market_action += " [Liquidity Void: High slippage risk]"
            if funding_val < -0.001 and not is_dex: market_action += " [Deep Negative Funding: Short Squeeze]"
        elif macro_deriv_action and not is_dex:
            market_action += f" | {macro_deriv_action['en']}"

        dex_alert_str = ("\n🛡️ <b>DEX Security Audit:</b>\n" + "\n".join(dex_warnings_en) + "\n") if (is_dex and dex_warnings_en) else ""

    if is_dex:
        market_action = f"(شبكة DEX) | {market_action}" if lang == "ar" else f"(DEX Network) | {market_action}"

    # ====================================================================
    # 📊 التقرير النهائي (The Final Output)
    # ====================================================================
    macd_fmt = format_price(safe_macd)
    inst_txt = inst_memory['text_ar'] if lang == "ar" else inst_memory['text_en']
    macro_state = inst_memory['macro_state_ar'] if lang == "ar" else inst_memory['macro_state_en']
    mtfa_txt = mtfa_context['ar_text'] if lang == "ar" else mtfa_context['en_text']
    
    if lang == "ar":
        final_report = f"""
📊 <b>التحليل لـ {clean_sym}</b> | {tf} | <code>{format_price(price)}$</code>
الاتجاه: <b>{real_trend}</b>
درجة الثقة: {trend_strength_display}

📉 <b>الدعم والمقاومة</b>
الدعم الأقرب: <code>{format_price(calc_sup)}$</code>
المقاومة الأقرب: <code>{format_price(calc_res)}$</code>

🎯 <b>الأهداف السعرية (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>وقف الخسارة (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>تحليل التدفق والسيولة</b>
• <b>حالة الماكرو:</b> {macro_state}
• <b>التوافق الزمني:</b> {mtfa_txt}
• <b>التدفق:</b> {market_action}
• <b>المؤشرات الفنية:</b>
RSI: {safe_rsi:.1f} | MACD: {macd_fmt} | ADX: {adx_val:.1f}
{dex_alert_str}"""
    else:
        final_report = f"""
📊 <b>Analysis: {clean_sym}</b> | {tf} | <code>{format_price(price)}$</code>
Trend: <b>{real_trend}</b>
Quant Conviction: {trend_strength_display}

📉 <b>Support & Resistance</b>
Nearest Support: <code>{format_price(calc_sup)}$</code>
Nearest Resistance: <code>{format_price(calc_res)}$</code>

🎯 <b>Price Targets (TP)</b>
TP1: <code>{format_price(calc_tp1)}</code>
TP2: <code>{format_price(calc_tp2)}</code>
TP3: <code>{format_price(calc_tp3)}</code>

🛑 <b>Stop Loss (SL)</b>
Stop Loss: <code>{format_price(calc_sl)}</code>

📈 <b>Flow & Liquidity</b>
• <b>Macro State:</b> {macro_state}
• <b>Timeframe Alignment:</b> {mtfa_txt}
• <b>Flow:</b> {market_action}
• <b>Quant Indicators:</b>
RSI: {safe_rsi:.1f} | MACD: {macd_fmt} | ADX: {adx_val:.1f}
{dex_alert_str}"""

    # 3. إرسال النتيجة فوراً للمستخدم
    # 3. إرسال النتيجة فوراً للمستخدم
    try:
        await cb.message.edit_text(final_report, parse_mode=ParseMode.HTML)
    except Exception as e:
        if "message is not modified" not in str(e):
            await cb.message.answer(final_report, parse_mode=ParseMode.HTML)

    # 🟢 أضف هذا السطر لإنهاء حالة التحميل للزر
    await cb.answer() 



    
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
# 1. 🟢 أضف هذا المتغير خارج الدالة لحفظ المهام ومنع بايثون من قتلها
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
        # 🧬 The Stealth Accumulation Matrix (ذاكرة الحيتان التراكمية)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS stealth_accumulation_matrix (
                symbol TEXT PRIMARY KEY,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                start_price DOUBLE PRECISION,
                adv_30d_usd DOUBLE PRECISION,
                accumulated_cvd_usd DOUBLE PRECISION DEFAULT 0.0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 🧠 The Ultimate BTC Quant Tape Schema
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_btc_tape (
                id SERIAL PRIMARY KEY,
                snapshot_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                spot_price DOUBLE PRECISION NOT NULL,
                
                -- 1. بصمة الشذوذ الإحصائي (Z-Scores)
                premium_z DOUBLE PRECISION,
                basis_z DOUBLE PRECISION,
                funding_z DOUBLE PRECISION,
                cme_premium_pct DOUBLE PRECISION,
                ibit_vol_surge DOUBLE PRECISION,
                
                -- 2. الارتباط الكلي (Macro Correlation)
                dxy_trend_pct DOUBLE PRECISION,
                us10y_trend_pct DOUBLE PRECISION,
                spy_trend_pct DOUBLE PRECISION,
                
                -- 3. مجمعات السيولة (Liquidation Heatmap Distances)
                upper_pool_dist_pct DOUBLE PRECISION,
                lower_pool_dist_pct DOUBLE PRECISION,
                magnetic_bias_code INTEGER, -- 1 للعلوي، -1 للسفلي، 0 للتوازن
                
                -- 4. البنية المجهرية للسبوت (Microstructure)
                vol_z_score DOUBLE PRECISION,
                rsi_15m DOUBLE PRECISION,
                adx_15m DOUBLE PRECISION,
                
                -- ==========================================================
                -- 🎯 آفاق المستقبل (Labels for AI Training)
                -- ==========================================================
                ret_1h DOUBLE PRECISION DEFAULT NULL,
                ret_4h DOUBLE PRECISION DEFAULT NULL,
                ret_24h DOUBLE PRECISION DEFAULT NULL,
                
                mfe_24h DOUBLE PRECISION DEFAULT NULL, -- أقصى صعود 
                mae_24h DOUBLE PRECISION DEFAULT NULL, -- أقصى هبوط
                
                trade_quality_score DOUBLE PRECISION DEFAULT NULL,
                is_processed INTEGER DEFAULT 0
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_apex_tape_process ON apex_btc_tape(is_processed, snapshot_timestamp)")

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
                # 1. إضافة أعمدة التقييم الزمني الجديد
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS ret_3d DOUBLE PRECISION DEFAULT NULL")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS ret_7d DOUBLE PRECISION DEFAULT NULL")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS ret_14d DOUBLE PRECISION DEFAULT NULL")
        
        # 2. إضافة أعمدة الماكرو (Macro Features)
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS weekly_liquidity_void DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS macro_z_score_30d DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS htf_whale_accumulation DOUBLE PRECISION DEFAULT 0.0")
        await conn.execute("ALTER TABLE ml_training_data ADD COLUMN IF NOT EXISTS days_since_last_expansion DOUBLE PRECISION DEFAULT 0.0")

        # 3. 🎯 التكتيك الذهبي: إعادة الصفقات القديمة إلى محكمة التفتيش
        # هذا السطر سيجعل البوت يعيد تقييم كل الصفقات القديمة بالمنطق الجديد!
        await conn.execute("""
            UPDATE ml_training_data 
            SET is_processed = 0 
            WHERE ret_14d IS NULL AND signal_time <= CURRENT_TIMESTAMP - INTERVAL '14 days'
        """)

        # 3. تفعيل حسابات الأدمن بشكل دائم
        initial_paid_users = {1317225334, 5527572646}
        for uid in initial_paid_users:
            await conn.execute("INSERT INTO paid_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
    asyncio.create_task(apex_btc_tape_worker(pool))
    asyncio.create_task(apex_btc_inspector_worker(pool))
    asyncio.create_task(apex_short_watchdog(pool))
    asyncio.create_task(short_radar_worker_process(pool))
    asyncio.create_task(smart_radar_watchdog(pool))
    asyncio.create_task(institutional_lob_worker(pool))
    asyncio.create_task(silent_data_harvester_worker(pool))
    asyncio.create_task(macro_data_worker()) # 🌍 تشغيل عامل الماكرو
    asyncio.create_task(radar_worker_process(pool))
    asyncio.create_task(institutional_incubator_worker(pool))
    asyncio.create_task(institutional_vanguard_worker())
    asyncio.create_task(moe_hot_swap_worker())
    asyncio.create_task(ai_trainer_worker(pool)) # 🧠 تشغيل مدرب الذكاء الاصطناعي
    asyncio.create_task(ml_inspector_worker(pool)) # 🧠 تشغيل محقق الذكاء الاصطناعي
        # مسح أي تحديثات معلقة تسبب تعليق السيرفر
    await bot.delete_webhook(drop_pending_updates=True)
    # تعيين الويب هوك مع طلب صريح بقبول الأزرار
    await bot.set_webhook(f"{WEBHOOK_URL}/", allowed_updates=["message", "callback_query", "pre_checkout_query", "successful_payment"])


# 🗑️ احذف دالة handle_webhook القديمة بالكامل

app = web.Application()

# 🚀 المحرك الرسمي من Aiogram (يمنع تعليق الأزرار ويظهر الأخطاء المخفية)
webhook_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
)
# توجيه الطلبات القادمة من تيليجرام إلى المحرك الرسمي
webhook_handler.register(app, path="/")

# مساراتك الأخرى تبقى كما هي
app.router.add_post("/webhook/nowpayments", nowpayments_ipn)
app.router.add_get("/health", lambda r: web.Response(text="ok"))
app.on_startup.append(on_startup)

# 🛡️ إعداد التطبيق لربط دورة حياة البوت بالسيرفر بشكل سليم
setup_application(app, dp, bot=bot)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)