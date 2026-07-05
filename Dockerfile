FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY app.py .
COPY assets/ assets/

RUN mkdir -p temp/job_matches temp/mp_inbox

ENV PORT=8765
EXPOSE 8765

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8765}"]
