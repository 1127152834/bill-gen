FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-liberation2 fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/assets.py hydrate && python scripts/assets.py check
ENV HOST=0.0.0.0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app BILL_GEN_DATA_DIR=/data
EXPOSE 8800
CMD ["python", "run.py"]
