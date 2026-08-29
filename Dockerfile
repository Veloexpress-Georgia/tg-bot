FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_HEALTH_HEARTBEAT_FILE=/tmp/veloexpress-bot-heartbeat.json \
    APP_HEALTH_HEARTBEAT_INTERVAL_SECONDS=15 \
    APP_HEALTH_MAX_AGE_SECONDS=90 \
    APP_HEALTH_REQUIRE_WORKER=true \
    APP_HEALTH_WORKER_MAX_AGE_SECONDS=120 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

COPY alembic.ini ./
COPY alembic ./alembic

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -m veloexpress_bot.healthcheck

CMD ["sh", "-c", "alembic upgrade head && python -m veloexpress_bot"]
