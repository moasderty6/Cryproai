FROM python:3.11-slim

# تثبيت curl ضروري لعمل فحص الصحة الداخلي
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ ملف المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# فحص الصحة الداخلي يشير الآن إلى المسار الجديد /health
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT}/health || exit 1

# تشغيل البوت مباشرة باستخدام python
CMD ["sh", "-c", "python main.py"]
