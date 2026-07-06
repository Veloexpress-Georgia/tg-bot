set dotenv-load := true

setup:
    uv sync --dev

dev: up db-migrate
    uv run watchfiles "python -m veloexpress_bot" src alembic .env pyproject.toml

run:
    uv run python -m veloexpress_bot

register-admin-rights:
    uv run python -m veloexpress_bot.bot.admin_rights

webhook-delete:
    curl --fail --show-error "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook"

healthcheck:
    uv run python -m veloexpress_bot.healthcheck --skip-heartbeat

format:
    uv run ruff format .
    uv run ruff check --fix .

check:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright
    uv run pytest

up:
    docker compose -f docker-compose.local.yml up -d --wait db

down:
    docker compose -f docker-compose.local.yml down

db-migrate:
    uv run alembic upgrade head

test:
    uv run pytest

typecheck:
    uv run pyright
