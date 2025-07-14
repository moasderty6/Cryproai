import asyncio
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from dotenv import load_dotenv
import httpx

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

GROQ_MODEL = "llama3-70b-8192"
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# تخزين اللغة المختارة لكل مستخدم
user_lang = {}

# كيبورد اختيار اللغة
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")]
])

# كيبورد الاشتراك
subscribe_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("📢 اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton("✅ تم الاشتراك", callback_data="check_sub")]
])

# دالة استخدام Groq API
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
            return f"⚠️ API Error: {result['error'].get('message', 'Unknown error')}"
        else:
            return "⚠️ Unexpected response from AI."

# بدء البوت - اختيار اللغة
@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Welcome! Please select your language:\n\n👋 مرحبًا بك! اختر لغتك:",
        reply_markup=language_keyboard
    )

# تعيين اللغة المختارة
@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_lang[callback.from_user.id] = lang

    if lang == "ar":
        await callback.message.edit_text(
            "✅ تم اختيار اللغة العربية.\n\n"
            "📢 الرجاء الاشتراك في القناة أولًا لاستخدام البوت.",
            reply_markup=subscribe_keyboard
        )
    else:
        await callback.message.edit_text(
            "✅ English language selected.\n\n"
            "📢 Please subscribe to the channel before using the bot.",
            reply_markup=subscribe_keyboard
        )

# التحقق من الاشتراك
@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = user_lang.get(user_id, "ar")

    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status in ("member", "administrator", "creator"):
        if lang == "ar":
            await callback.message.edit_text("✅ تم التحقق من الاشتراك.\n\n📩 أرسل الآن اسم العملة الرقمية التي تريد تحليلها (مثل: BTC أو ETH):")
        else:
            await callback.message.edit_text("✅ Subscription verified.\n\n📩 Now send the name of the crypto coin you want to analyze (e.g., BTC or ETH):")
    else:
        if lang == "ar":
            await callback.answer("❌ لم يتم الاشتراك بعد.", show_alert=True)
        else:
            await callback.answer("❌ You are not subscribed yet.", show_alert=True)

# استقبال اسم العملة
@dp.message(F.text)
async def handle_coin(message: types.Message):
    user_id = message.from_user.id
    lang = user_lang.get(user_id, "ar")
    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)

    if member.status not in ("member", "administrator", "creator"):
        text = "⚠️ يرجى الاشتراك في القناة أولًا:" if lang == "ar" else "⚠️ Please subscribe to the channel first:"
        await message.answer(text, reply_markup=subscribe_keyboard)
        return

    coin = message.text.strip().upper()
    loading = f"🔍 جاري تحليل {coin}..." if lang == "ar" else f"🔍 Analyzing {coin}..."
    await message.answer(loading)

    if lang == "ar":
        prompt = f"""
قم بتحليل العملة الرقمية {coin} يشمل:
- الوضع الحالي
- الدعم والمقاومة
- احتمالية الصعود
- هل ينصح بالشراء الآن
باختصار وبشكل احترافي
"""
    else:
        prompt = f"""
Analyze the cryptocurrency {coin} including:
- Current market condition
- Support & resistance levels
- Potential for price growth
- Whether it's a good time to buy
Give a concise and professional summary.
"""

    try:
        result = await ask_groq(prompt)
        await message.answer(result)
    except Exception as e:
        err = "❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Error while analyzing."
        await message.answer(f"{err}\n{e}")

# Webhook handler
async def handle_webhook(request):
    data = await request.json()
    await dp.feed_webhook_update(bot=bot, update=data, headers=request.headers)
    return web.Response()

# Startup & shutdown
async def on_startup(app): await bot.set_webhook(WEBHOOK_URL)
async def on_shutdown(app): await bot.delete_webhook()

async def main():
    app = web.Application()
    app.router.add_post("/", handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"✅ Bot running on port {PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
