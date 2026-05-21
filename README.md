# Veloexpress Bot

Telegram admin bot for Veloexpress lift polls.

The first MVP is intentionally narrow: an admin command creates weekend Telegram polls from a predefined RU/EN template. Admins can enable or disable Saturday/Sunday, cancel default lifts, and toggle the first lift location between Justice Hall and Vake before posting.

Out of scope for the first MVP: payment tracking, balances, overbooking, scheduling, miniapp, website, and template editing.

## Stack

- Python 3.14
- aiogram 3
- SQLAlchemy async + Alembic
- Postgres
- uv
- ruff
- pyright
- pytest
- just
- Lefthook
- Docker Compose / Coolify

## Local Testing

```sh
cp .env.example .env
uv sync --dev
```

Edit `.env` for your test bot and test Telegram group:

```env
DATABASE_URL=postgresql+asyncpg://veloexpress:veloexpress@localhost:5432/veloexpress
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ADMIN_IDS=123456789
TELEGRAM_TARGET_CHAT_ID=-1001234567890
TELEGRAM_TARGET_THREAD_ID=
TELEGRAM_PIN_POLL=true
```

`TELEGRAM_ADMIN_IDS` must be numeric Telegram user IDs, not usernames.

For a forum topic, set `TELEGRAM_TARGET_THREAD_ID` to the topic message thread id. Leave it empty for a normal group.

Start the local development environment:

```sh
just dev
```

`just dev` starts local Postgres with `docker-compose.local.yml`, runs Alembic
migrations, starts the bot in long-polling mode, and restarts it when Python
code, Alembic files, `.env`, or `pyproject.toml` change.

`just db-up` waits for the Postgres healthcheck before migrations run. If you
override `POSTGRES_PORT`, update the port in `DATABASE_URL` as well.

Use `just run` only when Postgres is already running and you want a one-shot bot
process without hot reload.

In the test Telegram group/topic, or in a direct message with the bot, send:

```text
/create_lift_poll
```

The setup menu can be used from DM. Polls are still posted to `TELEGRAM_TARGET_CHAT_ID`
and, when configured, `TELEGRAM_TARGET_THREAD_ID`.

The bot should show setup buttons for:

- enabling/disabling Saturday and Sunday
- first lift location
- enabling/disabling default lift times
- creating or cancelling the weekend polls

The test bot needs permission to send polls. To fully test MVP behavior, also allow it to delete its setup messages and pin messages.

The bot currently uses Telegram long polling. If the same bot token previously had a webhook registered, polling will not receive updates until the webhook is deleted. Run this once if updates are not arriving:

```sh
just webhook-delete
```

Webhook serving is intentionally not part of the first MVP.

## Local Quality Checks

```sh
just check
```

This runs:

- Ruff format check
- Ruff lint
- pyright type check
- pytest

For only the Pylance-compatible type check:

```sh
just typecheck
```

If you use Lefthook locally:

```sh
lefthook install
lefthook run pre-commit
```

## Bot Command

```text
/help
/create_lift_poll
```

The bot registers these commands on startup. Only Telegram user IDs listed in `TELEGRAM_ADMIN_IDS` can use `/create_lift_poll`. The setup flow starts with upcoming Saturday and Sunday enabled, and supports disabling either day, first-location toggling, plus canceling default lift times.

The bot also registers its suggested default group admin rights on startup. Telegram will preselect pin/delete permissions when adding the bot as an admin, but the person adding it can still change the permissions before confirming.

To register those suggested admin rights manually:

```sh
just register-admin-rights
```

The command also prints an admin invite link with explicit Telegram deep-link
permissions. Use that link if Telegram's generic "add/promote admin" screen
preselects broader rights than expected.

## Development Workflow

```sh
just setup
just format
just check
```

Use the test bot and test Telegram group/topic before touching the real Veloexpress chat.

## Deployment

`docker-compose.local.yml` starts Postgres and the bot for local development. `docker-compose.coolify.yml` is intended for a Coolify Git-based Docker Compose application and includes a bundled Postgres service with a persistent volume.

For Coolify, set these environment variables on the application:

```env
POSTGRES_DB=veloexpress
POSTGRES_USER=veloexpress
POSTGRES_PASSWORD=change-me
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ADMIN_IDS=123456789
TELEGRAM_TARGET_CHAT_ID=-1001234567890
TELEGRAM_TARGET_THREAD_ID=
TELEGRAM_PIN_POLL=true
```

By default the bot connects to the bundled `db` service. Later, if you move Postgres to a separate managed/write database, set `DATABASE_URL` explicitly:

```env
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/DATABASE
```

`DATABASE_URL` must point to a Postgres host reachable from the bot container. Do not use `localhost` or `127.0.0.1` in Coolify unless Postgres runs inside the same container, which it does not. The Docker image runs `alembic upgrade head` before starting the bot.

If `DATABASE_URL` is empty in production, the bot derives it from `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_HOST` with `POSTGRES_HOST=db` by default.

GitHub Actions contains:

- `CI`: lint, format check, tests, Docker build validation.
- `CD / Coolify / Dev`: triggers the Coolify dev deploy webhook after successful CI on `dev`, or manually via workflow dispatch.
- `CD / Coolify / Production`: triggers the Coolify production deploy webhook after successful CI on `main`, or manually via workflow dispatch.

Configure GitHub environments:

- `Development`: `COOLIFY_WEBHOOK`, `COOLIFY_TOKEN`
- `Production`: `COOLIFY_WEBHOOK`, `COOLIFY_TOKEN`
