# syntax=docker/dockerfile:1.7
# One image, several roles: api (default), worker, migrate, cli.

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir "uv==0.11.32"
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY alembic.ini README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 masms && useradd --system --uid 10001 --gid masms --no-create-home masms
WORKDIR /app
COPY --from=build --chown=masms:masms /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MASMS_ENVIRONMENT=production
USER masms
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"
# Workers per container: scale with replicas first; raise --workers only up to the CPU count.
CMD ["uvicorn", "app.presentation.http.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--http", "httptools", "--proxy-headers", \
     "--forwarded-allow-ips", "*", "--no-access-log", "--timeout-graceful-shutdown", "20"]
