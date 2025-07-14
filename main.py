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
MORALIS_KEY = os.getenv("MORALIS_API_KEY")
CMC_KEY = os.getenv("CMC_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))

CHANNEL_USERNAME = "p2p_LRN"
GROQ_MODEL = "deepseek-r1-distill-llama-70b"

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
user_lang = {}

symbol_to_contract = {
    "shiba": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    "pepe": "0x6982508145454ce325ddbe47a25d4ec3d2311933",
}

def clean_html(txt):
    return re.sub(r"<.*?>", "", txt)

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
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=json_data)
        try:
            result = res.json()
        except Exception as e:
            print(f"❌ Failed to parse Groq response: {e}\nRaw: {res.text}")
            return "❌ خطأ في تحليل الذكاء الاصطناعي."
        if "choices" in result and result["choices"]:
            return result["choices"][0]["message"]["content"]
        else:
            print(f"❌ Unexpected Groq output: {result}")
            return "❌ لم أستطع توليد التحليل من الذكاء الاصطناعي."

async def get_price_native(chain="eth"):
    url = f"https://deep-index.moralis.io/api/v2/native/prices?chain={chain}"
    headers = {"X-API-Key": MORALIS_KEY}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        if res.status_code != 200:
            return None
        try:
            data = res.json()
            return data.get("nativePrice", {}).get("usdPrice")
        except:
            return None

async def get_price_erc20(addr, chain="eth"):
    url = f"https://deep-index.moralis.io/api/v2/erc20/{addr}/price?chain={chain}"
    headers = {"X-API-Key": MORALIS_KEY}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        if res.status_code != 200:
            return None
        try:
            data = res.json()
            return data.get("usdPrice")
        except:
            return None

async def get_price_cmc(symbol):
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol={symbol.upper()}"
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        if res.status_code != 200:
            return None
        try:
            data = res.json()
            return data["data"][symbol.upper()]["quote"]["USD"]["price"]
        except:
            return None

async def fetch_price(symbol):
    symbol = symbol.lower()
    if symbol in ["eth", "ethereum", "إيثريوم"]:
        return await get_price_native("eth") or await get_price_cmc("ETH")
    elif symbol in ["btc", "bitcoin", "بتكوين"]:
        return await get_price_native("btc") or await get_price_cmc("BTC")
    elif symbol in symbol_to_contract:
        return await get_price_erc20(symbol_to_contract[symbol]) or await get_price_cmc(symbol.upper())
    else:
        return await get_price_cmc(symbol.upper())

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
    price = await fetch_price(sym)

    if not price:
        await m.answer("❌ لم أتمكن من جلب السعر الحالي للعملة." if lang == "ar"
                       else "❌ Couldn't fetch current price.")
        return

    await m.answer(f"💵 السعر الحالي: ${price:.6f}")

    prompt = (
        f"""الرجاء تحليل العملة التالية:
- الرمز: {sym}
- السعر الحالي: {price:.6f} دولار

1. ما هي الحالة الحالية؟
2. ما هي نقاط الدعم والمقاومة؟
3. هل من المتوقع أن يرتفع السعر؟
4. هل تنصح بالشراء؟
5. تنبيه عن المخاطر. رد باللغة العربية فقط."""
        if lang == "ar" else
        f"""Please analyze the following coin:
- Symbol: {sym}
- Current price: ${price:.6f}

1. What's the current situation?
2. Support/resistance levels?
3. Is it likely to go up?
4. Is it advisable to buy now?
5. Risk warning. Respond in English only."""
    )

    await m.answer("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing with AI...")
    try:
        analysis = await ask_groq(prompt)
        await m.answer(clean_html(analysis), parse_mode=None)
    except Exception as e:
        print("❌ Error:", e)
        await m.answer("❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Analysis failed.")

async def handle_webhook(req):
    update = await req.json()
    await dp.feed_webhook_update(bot=bot, update=update, headers=req.headers)
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
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print("✅ Webhook running...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
