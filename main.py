import asyncio
import os
import re
import json
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
USERS_FILE = "users.json"

# === دعم تخزين المستخدمين في ملف JSON ===
def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

user_lang = load_users()

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

# === لوحة اللغة والاشتراك ===
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

# === لوحة اختيار الإطار الزمني صغيرة جنب بعض ===
timeframe_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="أسبوعي", callback_data="tf_weekly"),
        InlineKeyboardButton(text="يومي", callback_data="tf_daily"),
        InlineKeyboardButton(text="4 ساعات", callback_data="tf_4h")
    ]
])
timeframe_keyboard_en = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Weekly", callback_data="tf_weekly"),
        InlineKeyboardButton(text="Daily", callback_data="tf_daily"),
        InlineKeyboardButton(text="4H", callback_data="tf_4h")
    ]
])

# === أوامر البوت ===
@dp.message(F.text == "/start")
async def start(m: types.Message):
    uid = str(m.from_user.id)
    if uid not in user_lang:
        user_lang[uid] = "ar"
        save_users(user_lang)
    await m.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)

@dp.message(F.text == "/status")
async def status_handler(m: types.Message):
    lang = user_lang.get(str(m.from_user.id), "ar")
    count = len(user_lang)
    msg = f"📊 عدد المستخدمين: {count}" if lang == "ar" else f"📊 Total users: {count}"
    await m.answer(msg)

# === اختيار اللغة ===
@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = str(cb.from_user.id)
    user_lang[uid] = lang
    save_users(user_lang)
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", cb.from_user.id)
    if member.status in ("member", "administrator", "creator"):
        await cb.message.edit_text("✅ مشترك. أرسل رمز العملة:" if lang == "ar" else "✅ Subscribed. Send coin symbol:")
    else:
        kb = subscribe_ar if lang == "ar" else subscribe_en
        await cb.message.edit_text("❗ الرجاء الاشتراك أولاً" if lang == "ar" else "❗ Please subscribe first", reply_markup=kb)

@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_lang.get(uid, "ar")
    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", cb.from_user.id)
    if member.status in ("member", "administrator", "creator"):
        await cb.message.edit_text("✅ مشترك. أرسل رمز العملة:" if lang == "ar" else "✅ Subscribed. Send coin symbol:")
    else:
        kb = subscribe_ar if lang == "ar" else subscribe_en
        await cb.message.edit_text("❗ الرجاء الاشتراك أولاً" if lang == "ar" else "❗ Please subscribe first", reply_markup=kb)

# === التعامل مع رمز العملة ===
@dp.message(F.text)
async def handle_symbol(m: types.Message):
    uid = str(m.from_user.id)
    lang = user_lang.get(uid, "ar")
    sym = m.text.strip().lower()

    member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", m.from_user.id)
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

    # حفظ الرمز والسعر للمراحل القادمة
    user_lang[uid+"_symbol"] = sym
    user_lang[uid+"_price"] = price
    save_users(user_lang)

    # طلب اختيار الإطار الزمني
    kb = timeframe_keyboard if lang == "ar" else timeframe_keyboard_en
    await m.answer(
        "⏳ اختر الإطار الزمني للتحليل:" if lang == "ar" else "⏳ Select timeframe for analysis:",
        reply_markup=kb
    )

# === التعامل مع اختيار الإطار الزمني ===
@dp.callback_query(F.data.startswith("tf_"))
async def set_timeframe(cb: types.CallbackQuery):
    uid = str(cb.from_user.id)
    lang = user_lang.get(uid, "ar")
    tf_map = {
        "tf_weekly": "weekly",
        "tf_daily": "daily",
        "tf_4h": "4h"
    }
    timeframe = tf_map[cb.data]
    sym = user_lang.get(uid+"_symbol")
    price = user_lang.get(uid+"_price")

    prompt = ""
    if lang == "ar":
        prompt = (
            f"سعر العملة {sym.upper()} الآن هو {price:.6f}$.\n"
            f"قم بتحليل التشارت للإطار الزمني {timeframe} اعتمادًا على:\n"
            "- خطوط الدعم والمقاومة.\n"
            "- مؤشرات RSI و MACD و MA.\n"
            "- سلوك السعر السابق.\n"
            "ثم قدّم:\n"
            "1. تقييم عام (صعود أم هبوط؟)\n"
            "2. أقرب مقاومة ودعم.\n"
            "3. السعر المستهدف المتوقع.\n"
            "✅ استخدم العربية فقط.\n"
            "🚫 لا تكتب رموز أو كلمات بلغة أخرى.\n"
            "❌ لا تشرح المشروع، فقط تحليل التشارت."
        )
    else:
        prompt = (
            f"The current price of {sym.upper()} is ${price:.6f}.\n"
            f"Analyze the {timeframe} chart using:\n"
            "- Support and resistance levels.\n"
            "- RSI, MACD, MA indicators.\n"
            "- Previous price behavior.\n"
            "Then provide:\n"
            "1. General trend (up/down)\n"
            "2. Nearest resistance/support\n"
            "3. Target price\n"
            "✅ Answer in English only.\n"
            "❌ Don't explain the project, only chart analysis."
        )

    await cb.message.edit_text("🤖 جاري التحليل..." if lang == "ar" else "🤖 Analyzing...")
    analysis = await ask_groq(prompt, lang=lang)
    await cb.message.answer(analysis)

# === Webhook ===
async def handle_webhook(req):
    if req.method == "GET":
        return web.Response(text="✅ Bot is alive.")
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
    app.router.add_get("/", handle_webhook)
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
