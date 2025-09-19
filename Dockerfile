# 1. استخدم صورة بايثون رسمية خفيفة
FROM python:3.11-slim

# 2. تعيين مجلد العمل داخل الحاوية
WORKDIR /app

# 3. انسخ ملف المتطلبات أولاً للاستفادة من التخزين المؤقت (Caching)
COPY requirements.txt .

# 4. قم بتثبيت المكتبات
# استخدام --no-cache-dir يجعل حجم الصورة أصغر
RUN pip install --no-cache-dir -r requirements.txt

# 5. انسخ باقي ملفات المشروع إلى مجلد العمل
COPY . .

# 6. حدد المنفذ الذي سيعمل عليه التطبيق داخل الحاوية
# Render سيتصل بهذا المنفذ. 10000 هو خيار شائع.
ENV PORT=10000
EXPOSE 10000

# 7. أمر التشغيل (هذا هو الـ "Start Command" الفعلي)
# استخدم Gunicorn لتشغيل تطبيق aiohttp بشكل احترافي
# مهم جداً: استخدم 0.0.0.0 ليكون الخادم متاحاً خارج الحاوية
CMD ["gunicorn", "main:app", "--worker-class", "aiohttp.GunicornWebWorker", "--bind", "0.0.0.0:10000"]
