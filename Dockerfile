FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/lol-coach-Backend/src

WORKDIR /app

COPY lol-coach-Backend/requirements.txt lol-coach-Backend/requirements-rag.txt ./lol-coach-Backend/
RUN pip install --no-cache-dir --requirement lol-coach-Backend/requirements.txt \
    && useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser lol-coach-Backend ./lol-coach-Backend
COPY --chown=appuser:appuser lol-coach-Frontend ./lol-coach-Frontend

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn main:app --app-dir lol-coach-Backend --host 0.0.0.0 --port \"${PORT:-8080}\""]
