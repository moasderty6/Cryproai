# 1. استخدم صورة بايثون رسمية خفيفة
FROM python:3.11-slim

# 2. تعيين مجلد العمل داخل الحاوية
WORKDIR /app

# 3. انسخ ملف المتطلبات أولاً للاستفادة من التخزين المؤقت
COPY requirements.txt .

# 4. قم بتثبيت المكتبات
RUN pip install --no-cache-dir -r requirements.txt

# 5. انسخ باقي ملفات المشروع إلى مجلد العمل
COPY . .

# 6. حدد المنفذ الذي سيعمل عليه التطبيق داخل الحاوية
EXPOSE 10000

# 7. أمر التشغيل
CMD ["gunicorn", "main:app", "--worker-class", "aiohttp.GunicornWebWorker", "--bind", "0.0.0.0:10000"]
