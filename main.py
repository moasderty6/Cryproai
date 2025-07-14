import asyncio
import os
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

GROQ_MODEL = "llama3-70b-8192"
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
user_lang = {}

# لوحات التحكم
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])

subscribe_keyboard_ar = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 اشترك الآن في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ تحققت من الاشتراك", callback_data="check_sub")]
])

subscribe_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Subscribe to channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ I have subscribed", callback_data="check_sub")]
])

# Groq API
async def ask_groq(prompt: str) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    json_data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        result = res.json()
        if "choices" in result and result["choices"]:
            return result["choices"][0]["message"]["content"]
        elif "error" in result:
            return f"❌ API Error: {result['error'].get('message', 'Unknown')}"
        else:
            return "❌ Unexpected response."

# بدء البوت
@dp.message(F.text == "/start")
async def start_handler(message: types.Message):
    await message.answer("👋 Please select your language:\n👋 الرجاء اختيار لغتك:", reply_markup=language_keyboard)

# تعيين اللغة والتحقق من الاشتراك
@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    user_lang[user_id] = lang

    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)

    if member.status in ("member", "administrator", "creator"):
        if lang == "ar":
            await callback.message.edit_text("✅ تم التحقق من الاشتراك.\n\n✍️ أرسل اسم العملة الرقمية (مثل: BTC أو ETH):")
        else:
            await callback.message.edit_text("✅ Subscription verified.\n\n✍️ Send the cryptocurrency name (e.g., BTC or ETH):")
    else:
        if lang == "ar":
            await callback.message.edit_text(
                "❗ لم يتم التحقق من الاشتراك.\n\nيرجى الاشتراك أولاً في القناة:",
                reply_markup=subscribe_keyboard_ar
            )
        else:
            await callback.message.edit_text(
                "❗ Subscription not verified.\n\nPlease subscribe to the channel first:",
                reply_markup=subscribe_keyboard_en
            )

# زر التحقق اليدوي من الاشتراك
@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = user_lang.get(user_id, "ar")
    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)

    if member.status in ("member", "administrator", "creator"):
        text = "✅ تم التحقق من الاشتراك.\n\n✍️ أرسل اسم العملة الرقمية (مثل: BTC أو ETH):" if lang == "ar" \
            else "✅ Subscription verified.\n\n✍️ Send the cryptocurrency name (e.g., BTC or ETH):"
        await callback.message.edit_text(text)
    else:
        keyboard = subscribe_keyboard_ar if lang == "ar" else subscribe_keyboard_en
        text = "❗ لم يتم التحقق من الاشتراك. الرجاء الاشتراك في القناة:" if lang == "ar" \
            else "❗ Subscription not verified. Please subscribe to the channel:"
        await callback.message.edit_text(text, reply_markup=keyboard)

# تحليل العملة
@dp.message(F.text)
async def handle_coin(message: types.Message):
    user_id = message.from_user.id
    lang = user_lang.get(user_id, "ar")
    coin = message.text.strip().upper()

    member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
    if member.status not in ("member", "administrator", "creator"):
        kb = subscribe_keyboard_ar if lang == "ar" else subscribe_keyboard_en
        txt = "⚠️ يرجى الاشتراك أولاً." if lang == "ar" else "⚠️ Please subscribe first."
        await message.answer(txt, reply_markup=kb)
        return

    loading = f"🔍 جاري تحليل {coin}..." if lang == "ar" else f"🔍 Analyzing {coin}..."
    await message.answer(loading)

    prompt_ar = f"""قم بتحليل العملة الرقمية {coin} يشمل:
- الحالة الحالية
- نقاط الدعم والمقاومة
- احتمالية الصعود
- هل يُنصح بالشراء الآن
قدم تقريرًا احترافيًا مختصرًا."""

    prompt_en = f"""Analyze the cryptocurrency {coin} including:
- Current status
- Support and resistance levels
- Growth potential
- Is it a good time to buy?
Provide a brief, expert summary."""

    prompt = prompt_ar if lang == "ar" else prompt_en

    try:
        result = await ask_groq(prompt)
        await message.answer(result)
    except Exception as e:
        error_msg = "❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Error during analysis."
        await message.answer(f"{error_msg}\n{str(e)}")

# Webhook
async def handle_webhook(request):
    data = await request.json()
    await dp.feed_webhook_update(bot=bot, update=data, headers=request.headers)
    return web.Response()

async def on_startup(app): await bot.set_webhook(WEBHOOK_URL)
async def on_shutdown(app): await bot.delete_webhook()

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
