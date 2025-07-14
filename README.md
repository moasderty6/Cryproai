# Telegram Crypto AI Webhook Bot

بوت تيليغرام يعتمد على Groq API لتحليل العملات الرقمية، ويعمل عبر Webhook داخل Docker. يشترط الاشتراك في قناة @p2p_LRN قبل استخدامه.

## التشغيل:
1. ضع القيم في ملف .env
2. بناء الصورة:
   docker build -t crypto-ai-bot .
3. تشغيل الحاوية:
   docker run -d -p 8000:8000 --env-file .env crypto-ai-bot