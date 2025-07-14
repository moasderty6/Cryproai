import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
import httpx

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "mixtral-8x7b-32768"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PORT = int(os.getenv("PORT", 8000))
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

subscribe_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📢 اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton(text="✅ تم الاشتراك", callback_data="check_sub")]
    ]
)

async def ask_groq(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    json_data = {
        "messages": [{"role": "user", "content": prompt}],
        "model": GROQ_MODEL
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        result = response.json()
        return result["choices"][0]["message"]["content"]

@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer(
        "👋 مرحبًا بك في بوت تحليل العملات الرقمية.\n\n"
        "🔐 لتحصل على تحليل لأي عملة رقمية، الرجاء الاشتراك أولًا في القناة ثم أرسل اسم العملة مثل: <b>BTC</b> أو <b>ETH</b>.",
        reply_markup=subscribe_keyboard
    )

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status not in ("member", "administrator", "creator"):
        await message.answer("⚠️ يرجى الاشتراك في القناة أولًا:", reply_markup=subscribe_keyboard)
        return

    coin = message.text.strip().upper()
    await message.answer(f"🔍 تحليل العملة {coin} قيد المعالجة...")
    prompt = f"""
قم بتحليل العملة الرقمية {coin}، واذكر وضعها الحالي في السوق، أهم نقاط الدعم والمقاومة، احتمالية صعودها خلال الأسبوعين القادمين، وهل يُنصح بشرائها الآن أم لا؟ بصيغة تقرير واضح ومختصر.
"""
    try:
        result = await ask_groq(prompt)
        await message.answer(result)
    except Exception as e:
        await message.answer(f"❌ خطأ أثناء التحليل: {e}")

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status in ("member", "administrator", "creator"):
        await callback.message.edit_text(
            "✅ تم التحقق من الاشتراك.\n\n"
            "📩 الآن أرسل اسم العملة الرقمية التي تريد تحليلها (مثال: BTC أو ETH):"
        )
    else:
        await callback.answer("❌ لم يتم الاشتراك بعد.", show_alert=True)

# ---------------- WEBHOOK HANDLING ----------------

async def handle_webhook(request):
    body = await request.read()
    await dp.feed_webhook_update(bot=bot, update=body, headers=request.headers)
    return web.Response()

async def on_startup(app):
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
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"🚀 Webhook bot running on port {WEBHOOK_PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
