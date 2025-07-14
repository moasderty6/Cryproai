import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from dotenv import load_dotenv
import httpx

# تحميل متغيرات البيئة
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

GROQ_MODEL = "mixtral-8x7b-32768"
CHANNEL_USERNAME = "p2p_LRN"

# إعدادات البوت
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# كيبورد الاشتراك
subscribe_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ تم الاشتراك", callback_data="check_sub")]
])

# دالة سؤال Groq API
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
        response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        result = response.json()
        return result["choices"][0]["message"]["content"]

# /start
@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer(
        "👋 مرحبًا بك في بوت تحليل العملات الرقمية.\n\n"
        "🔐 لتحصل على تحليل لأي عملة رقمية، الرجاء الاشتراك أولًا في القناة ثم أرسل اسم العملة مثل: <b>BTC</b> أو <b>ETH</b>.",
        reply_markup=subscribe_keyboard
    )

# تحليل العملة
@dp.message(F.text)
async def handle_coin(message: types.Message):
    user_id = message.from_user.id
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
    if member.status not in ("member", "administrator", "creator"):
        await message.answer("⚠️ يرجى الاشتراك في القناة أولًا:", reply_markup=subscribe_keyboard)
        return

    coin = message.text.strip().upper()
    await message.answer(f"🔍 تحليل العملة {coin} قيد المعالجة...")
    prompt = f"""
قم بتحليل العملة الرقمية {coin}، واذكر وضعها الحالي في السوق، أهم نقاط الدعم والمقاومة، احتمالية صعودها خلال الأسبوعين القادمين، وهل يُنصح بشرائها الآن أم لا؟ بصيغة تقرير واضح ومختصر.
"""
    try:
        reply = await ask_groq(prompt)
        await message.answer(reply)
    except Exception as e:
        await message.answer(f"❌ حدث خطأ أثناء التحليل: {e}")

# التحقق من الاشتراك
@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
    if member.status in ("member", "administrator", "creator"):
        await callback.message.edit_text(
            "✅ تم التحقق من الاشتراك.\n\n"
            "📩 الآن أرسل اسم العملة الرقمية التي تريد تحليلها (مثال: BTC أو ETH):"
        )
    else:
        await callback.answer("❌ لم يتم الاشتراك بعد.", show_alert=True)

# Webhook handler
async def handle_webhook(request):
    data = await request.json()
    await dp.feed_webhook_update(bot=bot, update=data, headers=request.headers)
    return web.Response()

# Webhook startup/shutdown
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app):
    await bot.delete_webhook()

# Main loop
async def main():
    app = web.Application()
    app.router.add_post("/", handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    print(f"🚀 Running on port {PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
