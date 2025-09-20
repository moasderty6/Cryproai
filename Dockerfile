# 1. استخدم صورة بايثون رسمية خفيفة
FROM python:3.11-slim

# 2. تعيين مجلد العمل داخل الحاوية
WORKDIR /app

# 3. انسخ ملف المتطلبات أولاً للاستفادة من التخزين المؤقت
COPY requirements.txt .

# 4. تثبيت المكتبات بدون التخزين المؤقت
RUN pip install --no-cache-dir -r requirements.txt

# 5. نسخ باقي ملفات المشروع إلى مجلد العمل
COPY . .

# 6. تعيين Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

# 7. أمر تشغيل البوت باستخدام Gunicorn مع أخذ PORT من البيئة
CMD ["sh", "-c", "gunicorn main:app --worker-class aiohttp.GunicornWebWorker --bind 0.0.0.0:${PORT}"]
