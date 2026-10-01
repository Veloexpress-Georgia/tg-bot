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

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

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

# Start beside `just dev`; API never starts the Telegram scheduler.
api:
    uv run uvicorn veloexpress_api.app:create_app --factory --reload --host 127.0.0.1 --port 8000

web:
    npm --prefix web run dev

web-build:
    npm --prefix web run build

web-check:
    npm --prefix web run test
    npm --prefix web run build
