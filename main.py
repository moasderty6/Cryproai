import os
import asyncio
import re
import json
import httpx
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# --- تحميل الإعدادات الأولية ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_KEY = os.getenv("CMC_API_KEY") # ملاحظة: CoinMarketCap API لم يعد ضرورياً إذا اعتمدنا على CoinGecko
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "p2p_LRN") # غيّر هذا لمعرف قناتك

# --- تهيئة البوت ---
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# --- دوال مساعدة ---
# سنستخدم هذا كحل مؤقت بدلاً من ملف JSON لتجنب المشاكل على Render
user_lang_cache = {}

async def get_user_lang(user_id):
    return user_lang_cache.get(user_id, "ar")

async def set_user_lang(user_id, lang):
    user_lang_cache[user_id] = lang

async def get_coin_price(symbol):
    """جلب السعر باستخدام CoinGecko لأنه مجاني وأكثر مرونة"""
    symbol = symbol.lower().strip()
    try:
        async with httpx.AsyncClient() as client:
            # البحث عن معرّف العملة
            list_res = await client.get("https://api.coingecko.com/api/v3/coins/list")
            list_res.raise_for_status()
            coin_id = next((coin['id'] for coin in list_res.json() if coin['symbol'] == symbol), None)
            
            if not coin_id:
                return None
            
            # جلب السعر باستخدام المعرّف
            price_res = await client.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd")
            price_res.raise_for_status()
            return price_res.json()[coin_id]['usd']
    except Exception as e:
        print(f"Error fetching price from CoinGecko for {symbol}: {e}")
        return None

async def get_ai_analysis(symbol, price, timeframe, lang):
    """طلب التحليل من الذكاء الاصطناعي مع التركيز على الأهداف"""
    prompt_ar = f"""
محلل خبير، سعر عملة {symbol.upper()} حاليًا هو ${price}.
بناءً على الإطار الزمني **"{timeframe}"**، قم بتحليل فني دقيق ومختصر.
يجب أن يتضمن تحليلك النقاط التالية بوضوح:
1.  **الاتجاه العام:** (صاعد / هابط / عرضي).
2.  **أقرب دعم ومقاومة:** حدد أهم المستويات.
3.  **الأهداف المستقبلية الصاعدة:** اذكر 2-3 أهداف سعرية متوقعة في حالة الصعود.
4.  **الأهداف المستقبلية الهابطة:** اذكر 2-3 أهداف سعرية متوقعة في حالة الهبوط.

ركز على الأرقام والأهداف، وتجنب الشرح العام.
"""
    prompt_en = f"""
As an expert analyst, the current price of {symbol.upper()} is ${price}.
Based on the **"{timeframe}"** timeframe, provide a concise technical analysis.
Your analysis must clearly include these points:
1.  **General Trend:** (Bullish / Bearish / Sideways).
2.  **Nearest Support & Resistance:** Identify the key levels.
3.  **Future Bullish Targets:** State 2-3 expected price targets if the trend is upward.
4.  **Future Bearish Targets:** State 2-3 expected price targets if the trend is downward.

Focus on numbers and targets, avoid generic explanations.
"""
    prompt = prompt_ar if lang == "ar" else prompt_en
    
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {"model": "llama3-8b-8192", "messages": [{"role": "user", "content": prompt}]}
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
            res.raise_for_status()
            result = res.json()
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Error from AI: {e}")
        return "❌ حدث خطأ أثناء التحليل." if lang == "ar" else "❌ Analysis failed."

# --- لوحات المفاتيح (Keyboards) ---
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
    [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
])

def create_timeframe_keyboard(symbol, lang):
    texts = {"ar": "اختر الإطار الزمني", "en": "Select Timeframe"}
    timeframes = {
        "ar": ["4 ساعات", "يومي", "أسبوعي"],
        "en": ["4 Hours", "Daily", "Weekly"]
    }
    
    buttons = [
        InlineKeyboardButton(
            text=tf_text,
            callback_data=f"tf_{tf_en.lower().replace(' ', '')}_{symbol}"
        ) for tf_text, tf_en in zip(timeframes[lang], timeframes['en'])
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    return keyboard

# --- معالجات الرسائل (Handlers) ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer("👋 اختر لغتك:\nChoose your language:", reply_markup=language_keyboard)

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang_handler(query: types.CallbackQuery):
    lang = query.data.split("_")[1]
    await set_user_lang(query.from_user.id, lang)
    
    text = "تم تحديد اللغة. أرسل الآن رمز أي عملة تريد تحليلها (مثال: BTC)." if lang == 'ar' else "Language set. Now, send the symbol of any coin you want to analyze (e.g., BTC)."
    
    await query.message.edit_text(text)
    await query.answer()

@dp.message(F.text)
async def symbol_handler(message: types.Message):
    """المعالج الرئيسي الذي يستقبل رمز العملة"""
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)
    symbol = message.text.strip().upper()

    # التحقق من الاشتراك بالقناة
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        if member.status not in ("member", "administrator", "creator"):
            raise Exception("User not subscribed")
    except:
        subscribe_url = f"https://t.me/{CHANNEL_USERNAME}"
        text_ar = f"يجب عليك الاشتراك في القناة أولاً لاستخدام البوت.\n{subscribe_url}"
        text_en = f"You must subscribe to the channel first to use the bot.\n{subscribe_url}"
        await message.answer(text_ar if lang == 'ar' else text_en)
        return

    # جلب السعر وعرضه
    wait_msg = await message.answer("⏳" if lang == 'ar' else "⏳")
    price = await get_coin_price(symbol.lower())
    
    if price is None:
        error_text = f"لم أتمكن من العثور على العملة '{symbol}'. يرجى التأكد من الرمز." if lang == 'ar' else f"Could not find the coin '{symbol}'. Please check the symbol."
        await wait_msg.edit_text(error_text)
        return

    price_text = f"<b>{symbol}</b>: ${price:,.4f}\n\n"
    prompt_text = "الآن، يرجى اختيار الإطار الزمني للتحليل:" if lang == 'ar' else "Now, please select the timeframe for analysis:"
    
    await wait_msg.edit_text(
        price_text + prompt_text,
        reply_markup=create_timeframe_keyboard(symbol, lang)
    )

@dp.callback_query(F.data.startswith("tf_"))
async def timeframe_handler(query: types.CallbackQuery):
    """المعالج الذي يستقبل اختيار الإطار الزمني ويبدأ التحليل"""
    user_id = query.from_user.id
    lang = await get_user_lang(user_id)
    
    # استخراج البيانات من الزر
    _, timeframe, symbol = query.data.split("_")
    
    await query.message.edit_text(
        f"👍 تم اختيار الإطار الزمني ({timeframe}).\nجاري إعداد التحليل لعملة {symbol}...",
        reply_markup=None # إزالة الأزرار
    )
    
    # جلب السعر مرة أخرى لضمان حداثته
    price = await get_coin_price(symbol.lower())
    if price is None: # التحقق مرة أخرى
        await query.message.answer(f"حدث خطأ أثناء جلب سعر {symbol} مجدداً.")
        return
        
    analysis = await get_ai_analysis(symbol, price, timeframe, lang)
    
    header = f"<b>تحليل {symbol} - الإطار الزمني: {timeframe}</b>\n<b>السعر الحالي: ${price:,.4f}</b>\n{'-'*20}"
    await query.message.answer(f"{header}\n\n{analysis}")
    await query.answer()

# --- إعداد Webhook والتشغيل ---
async def handle_webhook(request):
    url_path = request.url.path
    if url_path != f"/{BOT_TOKEN}":
        return web.Response(status=404)
        
    update_data = await request.json()
    await dp.feed_update(bot=bot, update=types.Update(**update_data))
    return web.Response()

async def on_startup(app):
    await bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    print(f"✅ Webhook has been set to {WEBHOOK_URL}/{BOT_TOKEN}")

async def main():
    app = web.Application()
    app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Bot is running on port {PORT}...")
    
    # Set webhook on startup
    await on_startup(app)
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
