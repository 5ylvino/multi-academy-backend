FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

RUN pip install --no-cache-dir .

EXPOSE 8012

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012"]
