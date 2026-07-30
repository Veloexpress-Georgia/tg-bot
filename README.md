# Veloexpress Bot

Telegram admin bot for Veloexpress lift polls.

The first MVP is intentionally narrow: an admin command creates weekend Telegram polls from a predefined English template. Admins can enable or disable Saturday/Sunday and choose the first and last lift times before posting; every time between those boundaries is included automatically.

Out of scope for the first MVP: payment tracking, balances, automatic waitlist management, scheduling, miniapp, website, and template editing.

## Product Principle

The bot should be helpful without becoming a maintenance workflow. Admins should
be able to keep using Telegram naturally: delete old polls manually, clean setup
messages, pin/unpin messages, and continue operating in the chat without running
sync or repair commands.

At decision points, the bot reconciles its stored state with Telegram. If an old
poll was deleted manually, the bot should catch up silently and allow a normal
create flow. If a stale poll still exists, the bot should offer recreate and
preserve tracked votes before cleanup.

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
TELEGRAM_PAYMENTS_THREAD_ID=
TELEGRAM_PIN_POLL=true
PAYMENT_PRICE_GEL=15
```

`TELEGRAM_ADMIN_IDS` must be numeric Telegram user IDs, not usernames.

`TELEGRAM_PAYMENTS_THREAD_ID` is the payments topic. Leave it empty to switch the payments board off entirely.

For a forum topic, set `TELEGRAM_TARGET_THREAD_ID` to the topic message thread id. Leave it empty for a normal group.

Start the local development environment:

```sh
just dev
```

`just dev` starts local Postgres with `docker-compose.local.yml`, runs Alembic
migrations, starts the bot in long-polling mode, and restarts it when Python
code, Alembic files, `.env`, or `pyproject.toml` change.

`just up` starts local Postgres and waits for the healthcheck before migrations run. If you
override `POSTGRES_PORT`, update the port in `DATABASE_URL` as well.

To stop local Docker infrastructure:

```sh
just down
```

Use `just run` only when Postgres is already running and you want a one-shot bot
process without hot reload.

All admin controls live in a private chat with the bot. Send `/start` and pick an action; `/create_lift_poll` jumps straight to the weekend plan. Polls are always posted to `TELEGRAM_TARGET_CHAT_ID` and, when configured, `TELEGRAM_TARGET_THREAD_ID`.

`📋 Weekend plan` is the single source of truth for what gets posted: it shows the upcoming weekend, the lift-time range (learned from recent weeks), which days are enabled, and when the polls will open automatically. Edits are saved as a plan — nothing posts until the scheduled time or an explicit `🚀 Post now`. Posted days are marked and protected; `♻️ Recreate polls` (with confirmation) replaces an already-posted weekend and reports tracked votes first. A `⏭ Skip weekend` toggle suppresses one auto run.

`⏰ Poll schedule` controls only the "when": the posting day (Monday–Saturday) and time in `SCHEDULE_TIMEZONE` (default `Asia/Tbilisi`), plus an optional group announcement 1–3 hours before posting. A background worker checks the schedule every 30 seconds and stores all state in Postgres, so restarts never lose or duplicate a run. The worker posts whatever the weekend plan says, skips days that already have active polls, and deletes the announcement message once the polls are up.

`➕ Extra lift day` covers mid-week lifts: pick a date within the next 7 days and a lift range, and the polls post to the group immediately (unpinned, so weekend polls stay pinned).

`📊 Booking monitor` keeps one reusable live monitor message per admin, with day tabs and `➖`/`➕` controls for people booked outside Telegram. Tapping a lift opens its detail (riders, manual count) with `🚫 Cancel lift` / `♻️ Restore lift` — a single lift is reversible. A `🚫 Cancel all` button retires the whole day: the polls close, the board shows all lifts cancelled, the day leaves the monitor, and the date is freed so an admin can post a fresh poll if it comes back (there is no in-place "restore day"). Cancelling a lift marks it `❌ cancelled` on the public board; cancelling the day marks the whole board cancelled. Either way the bot immediately tags the affected voters in the group thread so they know it is off. Public availability uses Telegram votes plus manual bookings and shows each lift's state in words (`needs N more`, `N left`, `full`, `waitlist +N`, `❌ cancelled`). All admin monitors refresh when votes, manual counts, or cancellations change. An admin must open the private bot chat before Telegram will allow the monitor to be delivered.

A lift runs once it reaches the rider minimum — five, the point where the trip covers the driver's cost. No admin taps anything to confirm it; the background worker watches bookings and posts one group message tagging that lift's riders when it crosses the line. Each lift announces this at most once, ever. If the count later falls back below the minimum the bot says so too, but only after the number has held for ten minutes: a drop is usually one rider re-picking slots, and a "running / short / running" burst is worse than silence. Recovering back above the minimum is silent. Cancelled lifts never signal. The day's earliest running lift also gets a single departure reminder shortly before it leaves — lateness is what kills the opening run, while later lifts self-correct because the van is already cycling. All of these are batched per worker tick, so a tick that trips several lifts still sends one message per kind. Because they ride on the same 30-second tick as auto-posting, a notice can lag a vote by up to half a minute, and they run even when no posting schedule was ever configured.

Once a day has a running lift, the bot posts one payments board for that day into `TELEGRAM_PAYMENTS_THREAD_ID` and keeps editing it: which lifts are running, the per-seat price, who has paid, and how many riders are still outstanding — as a count, never a name list. Nothing appears in the payments topic before a lift reaches the minimum.

Riders act on the board with inline buttons rather than a command, because a button in the group works for the many riders who never opened a private chat with the bot. `💸 I paid` records a claim priced as seats × `PAYMENT_PRICE_GEL`, defaulting to the number of running lifts that rider booked; `➕ Guest` and `➖ Seat` adjust the seat count, which is also how someone who booked two lifts but will only ride one corrects their total. `↩️ Undo` withdraws the claim. The labels are deliberately impersonal — one shared keyboard serves everyone — and each tap answers with a private toast.

When a rider claims, the bot posts their payment line to the payments topic on their behalf, tagged with amount and lifts, so Misho reads one format instead of a mix of screenshots and free text. If that rider already wrote in the payments topic since this weekend's polls were created, the bot stays silent and only records the claim: it checks that a message exists, never what it says. A later seat change edits the line the bot already posted instead of adding another. Payments are tracked per rider per day, not per lift, because a rider who booked two lifts may only ride one.

Riders can hold a booked seat without paying for it, and the bot does not chase them; the gap is visible on the board and in the admin monitor. Telegram poll votes cannot be withdrawn by a bot, so a rider who pays for fewer seats than they booked still occupies those slots — the availability count and the paid count can legitimately disagree.

The bot must be able to see ordinary messages in the payments topic for the "already wrote it themselves" check. Group admins receive all messages, which the bot is; if the check never triggers, disable privacy mode for the bot in BotFather.

The test bot needs permission to send polls. To fully test MVP behavior, also allow it to delete its setup messages and pin/unpin messages. Before deleting an old bot-managed poll, the bot unpins it; when a new weekend batch is pinned, older tracked poll pins are retired while both current service-day polls remain pinned. The bot also removes its own Telegram “pinned …” service notices from the configured lift topic so they do not later become “pinned Deleted message”; pin notices created manually by admins are preserved.

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
/start
/create_lift_poll
```

The bot registers these commands on startup. Only Telegram user IDs listed in `TELEGRAM_ADMIN_IDS` can use `/create_lift_poll`. The setup flow starts with upcoming Saturday and Sunday enabled and supports disabling either day. Lift times are configured as a continuous first-to-last range. Confirmed weekend schedules are remembered; a changed start or end time must be used for two consecutive weekends before it becomes the suggested default.

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

### Healthchecks

The Docker image and both Compose files define a container healthcheck:

```sh
python -m veloexpress_bot.healthcheck
```

It does not expose an HTTP port. The bot still runs in Telegram long-polling mode.
The healthcheck verifies two things:

- the bot event loop is alive by checking a heartbeat file updated by the running process;
- Postgres is reachable with a lightweight `select 1`.

Defaults are suitable for Coolify and Uptime Kuma Docker-container monitoring:

```env
APP_HEALTH_HEARTBEAT_FILE=/tmp/veloexpress-bot-heartbeat.json
APP_HEALTH_HEARTBEAT_INTERVAL_SECONDS=15
APP_HEALTH_MAX_AGE_SECONDS=90
```

In Uptime Kuma, use a Docker Container monitor for the Coolify bot container and treat Docker health status as the signal. To debug manually:

```sh
docker compose -f docker-compose.coolify.yml exec bot python -m veloexpress_bot.healthcheck
```

For a local DB-only check outside Docker, run:

```sh
just healthcheck
```

GitHub Actions contains:

- `CI`: lint, format check, tests, Docker build validation.
- `CD / Coolify / Dev`: triggers the Coolify dev deploy webhook after successful CI on `dev`, or manually via workflow dispatch.
- `CD / Coolify / Production`: triggers the Coolify production deploy webhook after successful CI on `main`, or manually via workflow dispatch.

Configure GitHub environments:

- `Development`: `COOLIFY_WEBHOOK`, `COOLIFY_TOKEN`
- `Production`: `COOLIFY_WEBHOOK`, `COOLIFY_TOKEN`
