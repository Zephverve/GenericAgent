FROM python:3.12-slim

WORKDIR /app

COPY requirements-cloud.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY assets/ assets/
COPY data/ data/

RUN mkdir -p temp/job_matches temp/mp_inbox

ENV PORT=8765
EXPOSE 8765

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8765}"]
