FROM python:3.11-slim

# تثبيت curl للتأكد من الـ Healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# تعديل الـ Healthcheck ليتناسب مع كودنا (سيرفر aiohttp)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:${PORT}/ || exit 1

# تشغيل البوت مباشرة باستخدام python وليس gunicorn
CMD ["sh", "-c", "python main.py"]
