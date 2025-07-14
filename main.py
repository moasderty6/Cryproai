import asyncio
import os
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from dotenv import load_dotenv
import httpx

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

GROQ_MODEL = "deepseek-r1-distill-llama-70b"
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
user_lang = {}

# سنقوم بتحميل قائمة العملات هنا عند بدء البوت
symbol_to_id_map = {}
name_to_id_map = {}

language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])

subscribe_keyboard_ar = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ تحققت من الاشتراك", callback_data="check_sub")]
])

subscribe_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Subscribe to the channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ I have subscribed", callback_data="check_sub")]
])

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)

async def ask_groq(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    json_data = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        result = res.json()
        if "choices" in result and result["choices"]:
            return result["choices"][0]["message"]["content"]
        elif "error" in result:
            return f"❌ API Error: {result['error'].get('message', 'Unknown')}"
        else:
            return "❌ Unexpected response."

async def get_price_from_id(coin_id):
    price_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(price_url)
            data = res.json()
            return data.get(coin_id, {}).get("usd")
        except Exception:
            return None

async def load_coin_list():
    global symbol_to_id_map, name_to_id_map
    print("🔁 Loading coin list from CoinGecko...")
    async with httpx.AsyncClient() as client:
        res = await client.get("https://api.coingecko.com/api/v3/coins/list")
        coin_list = res.json()
        for coin in coin_list:
            symbol_to_id_map[coin["symbol"].lower()] = (coin["id"], coin["name"])
            name_to_id_map[coin["name"].lower()] = (coin["id"], coin["symbol"])
    print(f"✅ Loaded {len(coin_list)} coins.")

@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer("👋 Please select your language:\n👋 الرجاء اختيار لغتك:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    user_lang[user_id] = lang

    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status in ("member", "administrator", "creator"):
        msg = "✅ تم التحقق من الاشتراك.\n\n✍️ أرسل اسم العملة الرقمية (مثل: BTC أو ETH):" if lang == "ar" else \
              "✅ Subscription verified.\n\n✍️ Send the cryptocurrency name (e.g., BTC or ETH):"
        await callback.message.edit_text(msg)
    else:
        kb = subscribe_keyboard_ar if lang == "ar" else subscribe_keyboard_en
        msg = "❗ لم يتم التحقق من الاشتراك. يرجى الاشتراك أولاً:" if lang == "ar" else \
              "❗ Subscription not verified. Please subscribe first:"
        await callback.message.edit_text(msg, reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = user_lang.get(user_id, "ar")
    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status in ("member", "administrator", "creator"):
        msg = "✅ تم التحقق من الاشتراك.\n\n✍️ أرسل اسم العملة الرقمية (مثل: BTC أو ETH):" if lang == "ar" else \
              "✅ Subscription verified.\n\n✍️ Send the cryptocurrency name (e.g., BTC or ETH):"
        await callback.message.edit_text(msg)
    else:
        kb = subscribe_keyboard_ar if lang == "ar" else subscribe_keyboard_en
        msg = "❗ لم يتم التحقق من الاشتراك. يرجى الاشتراك أولاً:" if lang == "ar" else \
              "❗ Subscription not verified. Please subscribe first:"
        await callback.message.edit_text(msg, reply_markup=kb)

@dp.message(F.text)
async def handle_coin(message: types.Message):
    user_id = message.from_user.id
    lang = user_lang.get(user_id, "ar")
    coin_input = message.text.strip().lower()

    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status not in ("member", "administrator", "creator"):
        kb = subscribe_keyboard_ar if lang == "ar" else subscribe_keyboard_en
        await message.answer("⚠️ اشترك في القناة أولاً." if lang == "ar" else "⚠️ Please subscribe first.", reply_markup=kb)
        return

    # البحث باستخدام الرمز أو الاسم
    coin_data = symbol_to_id_map.get(coin_input) or name_to_id_map.get(coin_input)
    if not coin_data:
        await message.answer("❌ لم أتمكن من العثور على هذه العملة." if lang == "ar" else "❌ Coin not found.")
        return

    coin_id, coin_name = coin_data
    price = await get_price_from_id(coin_id)
    if not price:
        await message.answer("❌ لم أتمكن من جلب السعر الحالي للعملة.")
        return

    await message.answer(f"🔍 تم العثور على العملة: {coin_name}" if lang == "ar" else f"🔍 Found coin: {coin_name}")
    await message.answer("📊 جاري التحليل..." if lang == "ar" else "📊 Analyzing...")

    prompt_ar = f"""الرجاء تحليل العملة التالية بشكل مختصر واحترافي:
- الاسم: {coin_name}
- السعر الحالي: {price} دولار

المطلوب:
1. الوضع الحالي للعملة.
2. نقاط الدعم والمقاومة.
3. احتمالية الصعود.
4. هل يُنصح بالشراء الآن؟
5. تحذير من المخاطر إن وُجد.
الرجاء الرد باللغة العربية فقط، وبدون مقدمات عامة."""

    prompt_en = f"""Please analyze the following cryptocurrency concisely and professionally:
- Name: {coin_name}
- Current Price: {price} USD

Requirements:
1. Current situation.
2. Support and resistance levels.
3. Upside potential.
4. Is it a good time to buy?
5. Warn about risks if needed.
Please reply only in English and avoid any generic introduction."""

    prompt = prompt_ar if lang == "ar" else prompt_en

    try:
        response = await ask_groq(prompt)
        clean_response = clean_html(response)
        await message.answer(clean_response, parse_mode=None)
    except Exception as e:
        await message.answer("❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Error during analysis.")
        print("❌ ERROR:", e)

# Webhook
async def handle_webhook(request):
    data = await request.json()
    await dp.feed_webhook_update(bot=bot, update=data, headers=request.headers)
    return web.Response()

async def on_startup(app):
    await load_coin_list()
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    app = web.Application()
    app.router.add_post("/", handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🚀 Running on port {PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
