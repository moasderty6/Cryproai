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

# 6. تعيين المنفذ
EXPOSE 10000

# 7. Healthcheck لفحص endpoint /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:10000/health || exit 1

# 8. أمر تشغيل البوت باستخدام Gunicorn
CMD ["gunicorn", "main:app", "--worker-class", "aiohttp.GunicornWebWorker", "--bind", "0.0.0.0:10000"]
