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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, CommandStart
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pycoingecko import CoinGeckoAPI
import matplotlib.pyplot as plt

# --- إعدادات أولية ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- متغيرات البيئة (مهم جداً) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # -> https://your-app-name.onrender.com
PORT = int(os.getenv("PORT", 8000))
CHANNEL_USERNAME = "p2p_LRN" # غيّر هذا لمعرف قناتك

# --- تهيئة البوت و المكتبات ---
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
            amount DOUBLE PRECISION,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            symbol TEXT,
            target_price DOUBLE PRECISION,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    await conn.close()
    logging.info("Database initialized successfully.")

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
    
# ... (بقية دوال قاعدة البيانات هنا)

# --- دوال واجهات برمجة التطبيقات (APIs) ---
async def get_coin_data(symbol):
    symbol = symbol.lower()
    cache_key = f"price_{symbol}"
    cached_data = await get_from_cache(cache_key)
    if cached_data:
        return cached_data

    try:
        coins_list = cg.get_coins_list()
        coin_id = next((coin['id'] for coin in coins_list if coin['symbol'] == symbol), None)
        if not coin_id: return None
        
        data = cg.get_coin_by_id(coin_id)
        price = data['market_data']['current_price']['usd']
        price_change = data['market_data']['price_change_percentage_24h']
        
        result = {'id': coin_id, 'price': price, 'change': price_change}
        set_in_cache(cache_key, result)
        return result
    except Exception as e:
        logging.error(f"Error fetching coin data for {symbol}: {e}")
        return None

async def ask_groq(prompt, lang="ar"):
    # ... (نفس دالة Groq من الكود السابق)

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
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        
        buffer = BytesIO()
        plt.savefig(buffer, format='png', transparent=True)
        buffer.seek(0)
        plt.close(fig)
        return buffer
    except Exception as e:
        logging.error(f"Error generating chart for {coin_id}: {e}")
        return None

# --- لوحات المفاتيح (Keyboards) ---
# ... (تعريف لوحات المفاتيح هنا)
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
    welcome_text = "أهلاً بك في بوت تحليل العملات الرقمية المطور!" if lang == 'ar' else "Welcome to the Advanced Crypto Analysis Bot!"
    await message.answer(welcome_text, reply_markup=get_main_menu_keyboard(lang))
    
# ... (بقية الـ Handlers هنا)

# --- مهام الخلفية (Scheduler) ---
async def check_alerts():
    # ... (كود التحقق من التنبيهات وإرسالها)

# --- إعداد Webhook و تشغيل البوت ---
async def on_startup(app):
    await init_db()
    scheduler.add_job(check_alerts, 'interval', minutes=5)
    scheduler.start()
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.session.close()
    scheduler.shutdown()
    await http_client.aclose()

async def handle_webhook(request):
    url = str(request.url)
    index = url.rfind('/')
    token = url[index+1:]
    if token == BOT_TOKEN:
        update = await request.json()
        await dp.feed_update(bot, types.Update(**update))
        return web.Response()
    else:
        return web.Response(status=403)
        
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
        logging.info("Bot stopped.")

