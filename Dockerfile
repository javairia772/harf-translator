FROM python:3.11-slim
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt && useradd --create-home pilot
COPY app/ app/
COPY static/ static/
COPY docs/translation-glossary-draft.md docs/translation-glossary-draft.md
USER pilot
ENV PORT=8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
