import asyncio
import os
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from dotenv import load_dotenv
import httpx

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
user_lang = {}

def clean_response(text, lang="ar"):
    if lang == "ar":
        return re.sub(r'[^\u0600-\u06FF0-9A-Za-z.,:%$؟! \n\-]+', '', text)
    else:
        return re.sub(r'[^\w\s.,:%$!?$-]+', '', text)

async def ask_groq(prompt, lang="ar"):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            result = res.json()
            content = result["choices"][0]["message"]["content"]
            return clean_response(content, lang=lang).strip()
    except Exception as e:
        print("❌ Error from AI:", e)
        return "❌ حدث خطأ أثناء تحليل التشارت." if lang == "ar" else "❌ Analysis failed."

async def get_price_cmc(symbol):
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                return None
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
    except:
        return None

language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])
subscribe_ar = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 اشترك بالقناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ تحققت", callback_data="check_sub")]
])
subscribe_en = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Subscribe", url=f"https://t.me/{CHANNEL_USERNAME}")],
    [InlineKeyboardButton(text="✅ I've joined", callback_data="check_sub")]
])

@dp.message(F.text == "/start")
async def start(m: types.Message):
    await m.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    user_lang[cb.from_user.id] = lang
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", cb.from_user.id)
    if member.status in ("member", "administrator", "creator"):
        await cb.message.edit_text("✅ مشترك. أرسل رمز العملة:" if lang == "ar" else "✅ Subscribed. Send coin symbol:")
    else:
        kb = subscribe_ar if lang == "ar" else subscribe_en
        await cb.message.edit_text("❗ الرجاء الاشتراك أولاً" if lang == "ar" else "❗ Please subscribe first", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: types.CallbackQuery):
    await set_lang(cb)

@dp.message(F.text)
async def handle_symbol(m: types.Message):
    uid = m.from_user.id
    lang = user_lang.get(uid, "ar")
    sym = m.text.strip().lower()

    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", uid)
    if member.status not in ("member", "administrator", "creator"):
        await m.answer("⚠️ اشترك بالقناة أولاً." if lang == "ar" else "⚠️ Please join the channel first.",
                       reply_markup=subscribe_ar if lang == "ar" else subscribe_en)
        return

    await m.answer("⏳ جاري جلب السعر..." if lang == "ar" else "⏳ Fetching price...")
    price = await get_price_cmc(sym)
    if not price:
        await m.answer("❌ لم أتمكن من جلب السعر الحالي للعملة." if lang == "ar"
                       else "❌ Couldn't fetch current price.")
        return

    await m.answer(f"💵 السعر الحالي: ${price:.6f}" if lang == "ar" else f"💵 Current price: ${price:.6f}")

    prompt = (
        f"""سعر العملة {sym.upper()} الآن هو {price:.6f}$.
قم بتحليل التشارت الأسبوعي فقط للعملة اعتمادًا على:
- خطوط الدعم والمقاومة.
- مؤشرات RSI و MACD و MA.
- سلوك السعر السابق خلال الأسابيع الماضية.
ثم قدّم:
1. تقييم عام (صعود أم هبوط؟).
2. أقرب مقاومة ودعم.
3. السعر المستهدف المتوقع.
✅ استخدم العربية فقط.
🚫 لا تكتب رموز أو كلمات بلغة أخرى.
❌ لا تشرح المشروع، فقط تحليل التشارت."""
        if lang == "ar" else
        f"""The current price of {sym.upper()} is ${price:.6f}.
Analyze only the weekly chart using:
- Support and resistance levels.
- RSI, MACD, MA indicators.
- Weekly price behavior.
Then provide:
1. General trend (up/down).
2. Nearest resistance/support.
3. Target price.
✅ Answer in English only.
❌ Don't explain the project, only chart analysis."""
    )

    await m.answer("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    analysis = await ask_groq(prompt, lang=lang)
    await m.answer(analysis)

async def handle_webhook(req):
    update = await req.json()
    await dp.feed_update(bot=bot, update=types.Update(**update))
    return web.Response()

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()

async def main():
    app = web.Application()
    app.router.add_post("/", handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("✅ Bot is running...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
