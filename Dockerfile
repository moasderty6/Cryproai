FROM python:3.11-slim

# تثبيت curl ضروري لعمل فحص الصحة
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# الغاء الـ Healthcheck القديم تماماً لتجنب تعليق ريندر
HEALTHCHECK NONE

# تشغيل البوت مباشرة
CMD ["python", "main.py"]
