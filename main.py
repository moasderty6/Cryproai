import os
import asyncio
import logging
import json
import re
from datetime import datetime, timedelta
from io import BytesIO

# --- مكتبات أساسية ---
import httpx
import asyncpg
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardRemove
from aiogram.filters import Command, CommandStart
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pycoingecko import CoinGeckoAPI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- إعدادات أولية ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- متغيرات البيئة (مهم جداً) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "p2p_LRN") # يمكنك تغييره هنا أو في ملف .env

# --- تهيئة البوت والمكتبات ---
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
scheduler = AsyncIOScheduler()
cg = CoinGeckoAPI()
http_client = httpx.AsyncClient(timeout=40)

# --- نظام الكاش (لتقليل استهلاك الـ API) ---
cache = {}
CACHE_TTL = timedelta(minutes=5)

async def get_from_cache(key):
    if key in cache and datetime.now() < cache[key]['expiry']:
        return cache[key]['data']
    return None

def set_in_cache(key, data):
    cache[key] = {
        'data': data,
        'expiry': datetime.now() + CACHE_TTL
    }

# --- إعدادات قاعدة البيانات (PostgreSQL) ---
async def init_db():
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                lang TEXT DEFAULT 'ar',
                is_subscribed BOOLEAN DEFAULT FALSE
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                symbol TEXT,
                coin_id TEXT,
                amount DOUBLE PRECISION,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                symbol TEXT,
                coin_id TEXT,
                target_price DOUBLE PRECISION,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        await conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Database initialization failed: {e}")

# --- دوال مساعدة لقاعدة البيانات ---
async def get_user_lang(user_id):
    conn = await asyncpg.connect(DATABASE_URL)
    lang = await conn.fetchval("SELECT lang FROM users WHERE user_id = $1", user_id)
    if not lang:
        await conn.execute("INSERT INTO users (user_id, lang) VALUES ($1, 'ar') ON CONFLICT (user_id) DO NOTHING", user_id)
        lang = 'ar'
    await conn.close()
    return lang

async def set_user_lang(user_id, lang):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("UPDATE users SET lang = $1 WHERE user_id = $2", lang, user_id)
    await conn.close()

# --- دوال واجهات برمجة التطبيقات (APIs) ---
async def get_coin_data(symbol):
    symbol = symbol.lower().strip()
    cache_key = f"price_{symbol}"
    cached_data = await get_from_cache(cache_key)
    if cached_data:
        return cached_data
    try:
        coins_list = cg.get_coins_list()
        coin_id = next((coin['id'] for coin in coins_list if coin['symbol'] == symbol), None)
        if not coin_id: return None
        data = cg.get_coin_by_id(coin_id, market_data='true', community_data='false', developer_data='false', sparkline='false')
        price = data.get('market_data', {}).get('current_price', {}).get('usd')
        price_change = data.get('market_data', {}).get('price_change_percentage_24h')
        if price is None or price_change is None: return None
        result = {'id': coin_id, 'symbol': symbol, 'price': price, 'change': price_change}
        set_in_cache(cache_key, result)
        return result
    except Exception as e:
        logging.error(f"Error fetching coin data for {symbol}: {e}")
        return None

async def ask_groq(prompt, lang="ar"):
    cache_key = f"groq_{hash(prompt)}"
    cached_data = await get_from_cache(cache_key)
    if cached_data:
        return cached_data
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        res = await http_client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
        res.raise_for_status()
        result = res.json()
        content = result["choices"][0]["message"]["content"].strip()
        set_in_cache(cache_key, content)
        return content
    except Exception as e:
        logging.error(f"Error from Groq AI: {e}")
        return "❌ حدث خطأ أثناء الاتصال بالذكاء الاصطناعي." if lang == "ar" else "❌ Error contacting the AI."

# --- إنشاء الرسوم البيانية ---
async def generate_chart(coin_id, lang):
    try:
        days = 30
        data = cg.get_coin_market_chart_by_id(coin_id, vs_currency='usd', days=days)
        prices = [item[1] for item in data['prices']]
        timestamps = [datetime.fromtimestamp(item[0]/1000) for item in data['prices']]
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(timestamps, prices, color='#00FF00', linewidth=2)
        title = f"تحليل سعر {coin_id.upper()} آخر {days} يوم" if lang == 'ar' else f"{coin_id.upper()} Price Last {days} Days"
        ax.set_title(title, fontsize=16, color='white')
        ax.set_ylabel("السعر ($)" if lang == 'ar' else "Price (USD)", color='white')
        ax.grid(color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax.tick_params(axis='x', colors='white', rotation=25)
        ax.tick_params(axis='y', colors='white')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        fig.tight_layout()
        buffer = BytesIO()
        plt.savefig(buffer, format='png', transparent=True)
        buffer.seek(0)
        plt.close(fig)
        return buffer
    except Exception as e:
        logging.error(f"Error generating chart for {coin_id}: {e}")
        return None

# --- لوحات المفاتيح (Keyboards) ---
def get_main_menu_keyboard(lang):
    text = {
        'ar': ["تحليل عملة 📈", "محفظتي 💼", "تنبيهاتي 🔔", "لمحة عن السوق 🌍"],
        'en': ["Analyze Coin 📈", "My Portfolio 💼", "My Alerts 🔔", "Market Overview 🌍"]
    }
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text=text[lang][0]), types.KeyboardButton(text=text[lang][1])],
            [types.KeyboardButton(text=text[lang][2]), types.KeyboardButton(text=text[lang][3])]
        ],
        resize_keyboard=True
    )
    return keyboard

# --- معالجات الأوامر والرسائل (Handlers) ---
@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
    await conn.close()
    lang = await get_user_lang(user_id)
    welcome_text = "أهلاً بك في بوت تحليل العملات الرقمية المطور! 🤖\nاختر أحد الخيارات من القائمة بالأسفل للبدء." if lang == 'ar' else "Welcome to the Advanced Crypto Analysis Bot! 🤖\nChoose an option from the menu below to start."
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(lang))

@dp.message(F.text.contains("تحليل عملة"))
@dp.message(F.text.contains("Analyze Coin"))
async def ask_for_symbol(message: types.Message):
    lang = await get_user_lang(message.from_user.id)
    text = "يرجى إرسال رمز العملة التي تريد تحليلها (مثال: BTC)." if lang == 'ar' else "Please send the symbol of the coin you want to analyze (e.g., BTC)."
    await message.answer(text, reply_markup=ReplyKeyboardRemove())

# ... يمكنك إضافة المزيد من الـ Handlers هنا للتعامل مع المحفظة والتنبيهات وغيرها

# --- الـ Handler الرئيسي لمعالجة رموز العملات ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    # تحقق إذا كانت الرسالة هي أحد خيارات القائمة الرئيسية
    lang = await get_user_lang(message.from_user.id)
    main_menu_options_ar = ["تحليل عملة 📈", "محفظتي 💼", "تنبيهاتي 🔔", "لمحة عن السوق 🌍"]
    main_menu_options_en = ["Analyze Coin 📈", "My Portfolio 💼", "My Alerts 🔔", "Market Overview 🌍"]
    
    if message.text in main_menu_options_ar or message.text in main_menu_options_en:
        # إذا كانت الرسالة أمر من القائمة، قد ترغب في توجيهها إلى دالة أخرى
        # على سبيل المثال، إذا كانت "محفظتي"، استدع دالة عرض المحفظة
        return

    # إذا لم تكن من القائمة، افترض أنها رمز عملة
    symbol = message.text.upper()
    waiting_msg_text = f"جاري البحث عن بيانات {symbol}..." if lang == 'ar' else f"Searching for {symbol} data..."
    waiting_msg = await message.answer(waiting_msg_text)

    coin_data = await get_coin_data(symbol)
    if not coin_data:
        error_text = "لم أتمكن من العثور على العملة. يرجى التأكد من الرمز والمحاولة مرة أخرى." if lang == 'ar' else "Could not find the coin. Please check the symbol and try again."
        await waiting_msg.edit_text(error_text)
        return

    price = coin_data['price']
    change = coin_data['change']
    coin_id = coin_data['id']

    sign = "🟢" if change >= 0 else "🔴"
    price_info = (
        f"<b>{symbol} - ${price:,.4f}</b> {sign} ({change:,.2f}%)"
    )
    await waiting_msg.edit_text(price_info)

    # إنشاء وإرسال الرسم البياني
    chart_generating_text = "جاري إنشاء الرسم البياني..." if lang == 'ar' else "Generating chart..."
    chart_msg = await message.answer(chart_generating_text)
    chart_buffer = await generate_chart(coin_id, lang)
    if chart_buffer:
        await message.answer_photo(BufferedInputFile(chart_buffer.getvalue(), filename=f"{symbol}_chart.png"))
        await chart_msg.delete()
    else:
        await chart_msg.edit_text("لم أتمكن من إنشاء الرسم البياني." if lang == 'ar' else "Failed to generate chart.")

    # طلب التحليل من Groq
    analysis_generating_text = "الذكاء الاصطناعي يقوم بالتحليل الآن..." if lang == 'ar' else "AI is analyzing now..."
    analysis_msg = await message.answer(analysis_generating_text)
    
    prompt = f"Provide a concise technical analysis for the cryptocurrency {symbol} which is currently at ${price}. Focus on the daily chart. Mention key support and resistance levels, and give a short-term price prediction (next 1-3 days). Answer in {lang}."
    
    analysis = await ask_groq(prompt, lang)
    await analysis_msg.edit_text(analysis)


# --- مهام الخلفية (Scheduler) ---
async def check_alerts():
    # هذا مثال لكيفية عمله، يمكنك تطويره لاحقًا
    # logging.info("Scheduler is checking for alerts...")
    # conn = await asyncpg.connect(DATABASE_URL)
    # alerts = await conn.fetch("SELECT * FROM alerts")
    # ... logic to check prices and send notifications ...
    # await conn.close()
    pass


# --- إعداد Webhook و تشغيل البوت ---
async def on_startup(app):
    await init_db()
    scheduler.add_job(check_alerts, 'interval', minutes=5)
    scheduler.start()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != f"{WEBHOOK_URL}/{BOT_TOKEN}":
        await bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
        logging.info(f"Webhook set to {WEBHOOK_URL}/{BOT_TOKEN}")

async def on_shutdown(app):
    await bot.session.close()
    scheduler.shutdown()
    await http_client.aclose()
    logging.info("Bot is shutting down.")

async def handle_webhook(request):
    try:
        update_data = await request.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot=bot, update=update)
        return web.Response()
    except Exception as e:
        logging.error(f"Error in webhook: {e}")
        return web.Response(status=500)
        
async def main():
    app = web.Application()
    app.router.add_post(f'/{BOT_TOKEN}', handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Bot is running on port {PORT}...")
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped manually.")
