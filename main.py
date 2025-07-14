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
MORALIS_KEY = os.getenv("MORALIS_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

GROQ_MODEL = "deepseek-llm-7b-instruct"
CHANNEL_USERNAME = "p2p_LRN"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
user_lang = {}

symbol_to_contract = {
    # مثال لخريطة للعملة الرمزية إلى عنوان العقد
    "eth": "0x0000000000000000000000000000000000000000",  # ETH placeholder
    "harrypotterobamasonic10inu": "0xABCDEF1234567890ABCDEF1234567890ABCDEF12"
    # أضف رموزًا وعناوين العقود حسب الحاجة
}

def clean_html(txt):
    return re.sub(r"<.*?>", "", txt)

async def ask_groq(prompt):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    json_data = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        result = res.json()
        if "choices" in result and result["choices"]:
            return result["choices"][0]["message"]["content"]
        return f"❌ API Error: {result.get('error', {}).get('message', 'Unknown')}"

async def get_price_moralis(addr, chain="eth"):
    url = f"https://deep-index.moralis.io/api/v2/erc20/{addr}/price?chain={chain}"
    headers = {"X-API-Key": MORALIS_KEY}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        data = res.json()
        print(f"🔍 Moralis response for {addr}: {data}")
        return data.get("usdPrice")

language_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar")],
                                          [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")]])
subscribe_ar = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك بالقناة", url=f"https://t.me/{CHANNEL_USERNAME}")],
                                     [InlineKeyboardButton("✅ تحققت", callback_data="check_sub")]])
subscribe_en = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Subscribe channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
                                     [InlineKeyboardButton("✅ I subscribed", callback_data="check_sub")]])

@dp.message(F.text == "/start")
async def start(m: types.Message):
    await m.answer("👋 اختر لغتك:\n👋 Please select your language:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    user_lang[cb.from_user.id] = lang
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", cb.from_user.id)
    if member.status in ("member","administrator","creator"):
        await cb.message.edit_text("✅ مشترك. أرسل رمز العملة:" if lang=="ar" else "✅ Subscribed. Send coin symbol:")
    else:
        kb = subscribe_ar if lang=="ar" else subscribe_en
        await cb.message.edit_text("❗ اشترك أولاً" if lang=="ar" else "❗ Subscribe first", reply_markup=kb)

@dp.callback_query(F.data=="check_sub")
async def check(cb: types.CallbackQuery):
    await set_lang(cb)

@dp.message(F.text)
async def anal(m: types.Message):
    uid = m.from_user.id
    lang = user_lang.get(uid, "ar")
    sym = m.text.strip().lower()
    print("🟡 Input:", sym)
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", uid)
    if member.status not in ("member","administrator","creator"):
        await m.answer("⚠️ اشترك بقناتنا." if lang=="ar" else "⚠️ Please subscribe.", reply_markup=(subscribe_ar if lang=="ar" else subscribe_en))
        return

    contract = symbol_to_contract.get(sym)
    if not contract:
        await m.answer("❌ لم أجد العقد لهذه العملة." if lang=="ar" else "❌ Token contract not found.")
        return

    price = await get_price_moralis(contract)
    if not price:
        await m.answer("❌ لم أتمكن من جلب السعر من Moralis." if lang=="ar" else "❌ Can't fetch price from Moralis.")
        return

    await m.answer(f"🔍 السعر الحالي: ${price:.6f}")
    await m.answer("📊 جاري التحليل..." if lang=="ar" else "📊 Analyzing...")

    prompt_ar = f"""الرجاء تحليل التالي:
- اسم الرمز: {sym}
- السعر الحالي: {price:.6f} دولار
1. الحالة الراهنة.
2. الدعم والمقاومة.
3. احتمالات الصعود.
4. هل يُنصح بالشراء؟
5. تنويه المخاطر.
رد بالعربية فقط."""
    prompt_en = f"""Please analyze:
- Symbol: {sym}
- Price: {price:.6f} USD
1. Situation.
2. Support/Resistance.
3. Upside potential.
4. Should one buy now?
5. Risk warning.
Reply in English only."""

    prompt = prompt_ar if lang=="ar" else prompt_en

    try:
        resp = await ask_groq(prompt)
        await m.answer(clean_html(resp), parse_mode=None)
    except Exception as e:
        print("❌ AI error:", e)
        await m.answer("❌ خطأ أثناء التحليل" if lang=="ar" else "❌ Analysis error")

async def webhook(req):
    d = await req.json()
    await dp.feed_webhook_update(bot=bot, update=d, headers=req.headers)
    return web.Response()

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    app = web.Application()
    app.router.add_post("/", webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("🚀 Running")
    while True:
        await asyncio.sleep(3600)

if __name__=="__main__":
    asyncio.run(main())
