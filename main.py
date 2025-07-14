import os
import asyncio
import logging
import json
import httpx
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "deepseek-chat"
CHANNEL_USERNAME = "p2p_LRN"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# ==== الحالة ====
class Form(StatesGroup):
    language = State()
    waiting_for_coin = State()

# ==== واجهة اختيار اللغة ====
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")]
])

# ==== زر الاشتراك ====
def join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 اشترك في القناة", url=f"https://t.me/{CHANNEL_USERNAME}")]
    ])

# ==== دالة الذكاء الاصطناعي ====
async def ask_groq(prompt):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    json_data = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
            result = res.json()
            raw_text = result["choices"][0]["message"]["content"]
        except Exception as e:
            return "❌ حدث خطأ أثناء التحليل."

        # تنظيف الرد
        for kw in ["###", "تحليل", "تقييم", "Ethereum", "Bitcoin"]:
            if kw in raw_text:
                raw_text = raw_text[raw_text.index(kw):]
                break

        raw_text = re.sub(r"[^\u0600-\u06FFa-zA-Z0-9\s.,:%$#@!?\-\n]", "", raw_text)
        return raw_text.strip()

# ==== جلب السعر من Moralis ====
async def get_price(symbol):
    headers = {"X-API-Key": MORALIS_API_KEY}
    url = f"https://deep-index.moralis.io/api/v2/erc20/metadata/search?search={symbol}"
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(url, headers=headers)
        try:
            data = res.json()
            if isinstance(data, list) and data:
                token = data[0]
                token_address = token["address"]
                chain = token["chainId"] if "chainId" in token else "eth"
                price_url = f"https://deep-index.moralis.io/api/v2/erc20/{token_address}/price?chain=eth"
                price_res = await client.get(price_url, headers=headers)
                price_data = price_res.json()
                return price_data.get("usdPrice")
            else:
                return None
        except:
            return None

# ==== التحقق من الاشتراك ====
async def check_subscription(user_id):
    try:
        member = await bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

# ==== بدء المحادثة ====
@dp.message(F.text == "/start")
async def start_cmd(message: types.Message, state: FSMContext):
    await message.answer("🌐 اختر اللغة / Choose Language:", reply_markup=language_keyboard)
    await state.set_state(Form.language)

# ==== استقبال اللغة ====
@dp.callback_query(F.data.startswith("lang_"))
async def language_chosen(callback: types.CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(language=lang)

    user_id = callback.from_user.id
    subscribed = await check_subscription(user_id)

    if not subscribed:
        msg = "👋 يجب الاشتراك في القناة أولاً!" if lang == "ar" else "👋 Please join our channel first!"
        await callback.message.edit_text(msg, reply_markup=join_keyboard())
    else:
        msg = "✅ مشترك. أرسل رمز العملة:" if lang == "ar" else "✅ Subscribed. Send the coin symbol:"
        await callback.message.edit_text(msg)
        await state.set_state(Form.waiting_for_coin)

# ==== استقبال رمز العملة ====
@dp.message(Form.waiting_for_coin)
async def receive_symbol(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    subscribed = await check_subscription(user_id)
    if not subscribed:
        await message.answer("❗ يجب الاشتراك أولاً", reply_markup=join_keyboard())
        return

    symbol = message.text.strip()
    data = await state.get_data()
    lang = data.get("language", "ar")

    await message.answer("⏳ جاري جلب السعر...")
    price = await get_price(symbol)

    if not price:
        msg = "❌ لم أتمكن من جلب السعر الحالي للعملة." if lang == "ar" else "❌ Couldn't fetch the current price."
        await message.answer(msg)
        return

    await message.answer(f"💵 السعر الحالي: ${price:.6f}")
    await message.answer("🤖 جاري التحليل...")

    if lang == "ar":
        prompt = f"""
محلل ذكي، أريد تحليل شامل للعملة {symbol.upper()}.

✅ السعر الحالي: {price:.6f} دولار.

قدم تحليلًا فنيًا ونفسيًا وماليًا.
- هل هي عملة واعدة؟
- احتمالية صعودها؟
- هل تُنصح بالشراء حاليًا أم لا؟

اجعل الإجابة بلغة عربية سهلة، واضحة ومباشرة.
        """.strip()
    else:
        prompt = f"""
Please analyze the coin {symbol.upper()}.

✅ Current price: ${price:.6f}

Provide a professional yet simple investment analysis covering:
- Current state
- Support & resistance
- Potential upside
- Buy recommendation

Use English only and make the result easy to understand.
        """.strip()

    ai_response = await ask_groq(prompt)
    await message.answer(ai_response)

# ==== ويب هوك ====
async def on_startup(app):
    webhook_info = await bot.set_webhook(WEBHOOK_URL)
    print(f"📡 Webhook set: {webhook_info.url}")

async def main():
    app = web.Application()
    app["bot"] = bot
    app["dispatcher"] = dp
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/")
    app.on_startup.append(on_startup)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print("✅ Bot is running...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
