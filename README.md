# Veloexpress Bot

Telegram bot for the Veloexpress shuttle group: it runs the weekend lift polls, tells the group which lifts actually happen, and keeps track of who has paid.

Admins work from a single private menu — `📋 Weekend` plans and schedules what gets posted, `📊 Booking monitor` is the live view during a weekend, `➕ Extra lift day` covers midweek rides. A background worker posts the polls on time, announces a lift once it reaches the rider minimum, reminds the group before the booking deadline, and pings the day's first lift before it leaves. Riders confirm payment with a button in the group or simply by writing in the payments topic.

Still out of scope: balances, automatic waitlist management, miniapp, website, and template editing.

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
BOOKING_DEADLINE_TIME=20:00
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

All admin controls live in a private chat with the bot. There is no Close button on any card: `/start` posts a fresh card and then deletes the command echo along with whatever card an earlier `/start` left behind, so exactly one card is ever live and the bot tidies up instead of asking. `/start` answers rather than greets: it opens with each active day's state — how many lifts are running, how many riders have paid — and when the next polls appear, with the menu underneath. `/create_lift_poll` still jumps straight to the weekend card but is no longer suggested, so the menu stays the single way in instead of a second, drifting copy of it. Polls are always posted to `TELEGRAM_TARGET_CHAT_ID` and, when configured, `TELEGRAM_TARGET_THREAD_ID`.

`📋 Weekend` is the single source of truth for what gets posted: it shows the upcoming weekend, the lift-time range (learned from recent weeks), which days are enabled, and when the polls will open automatically. Edits are saved as a plan — nothing posts until the scheduled time or an explicit `🚀 Post now`. Posted days are marked and protected; `♻️ Recreate polls` (with confirmation) replaces an already-posted weekend and reports tracked votes first. A `⏭ Skip weekend` toggle suppresses one auto run.

`⏰ Opens` sits inside the weekend card rather than in the menu, because an admin thinks about one weekend and not about a standing setting — the card's `Opens` line always shows when polls appear, or `off`, so the auto-posting switch cannot hide. It controls the "when": the posting day (Monday–Saturday) and time in `SCHEDULE_TIMEZONE` (default `Asia/Tbilisi`), plus an optional group announcement 1–3 hours before posting. A background worker checks the schedule every 30 seconds and stores all state in Postgres, so restarts never lose or duplicate a run. The worker posts whatever the weekend plan says, skips days that already have active polls, and deletes the announcement message once the polls are up.

`➕ Extra lift day` covers mid-week lifts: pick a date within the next 7 days and a lift range, and the polls post to the group immediately (unpinned, so weekend polls stay pinned).

`📊 Booking monitor` opens with the day at a glance — how many lifts run out of those still active, total seats, how many riders have paid, and the money in — above the per-lift lines. When a new weekend is posted, every admin's monitor is re-posted rather than edited, so it arrives at the bottom of the chat instead of staying buried where it was last opened; only admins who have opened it at least once can be reached, because Telegram refuses a private message to anyone who never started a chat with the bot.

The monitor keeps one reusable live message per admin, with day tabs and `➖`/`➕` controls for people booked outside Telegram. Tapping a lift opens its detail (riders, manual count) with `🚫 Cancel lift` / `♻️ Restore lift` — a single lift is reversible. A `🚫 Cancel all` button retires the whole day: the polls close, the board shows all lifts cancelled, the day leaves the monitor, and the date is freed so an admin can post a fresh poll if it comes back (there is no in-place "restore day"). The day takes its hand-added riders and its payments with it — nobody can tell whether a manually added rider still intends to come, and the money is going back, so a revived poll opens genuinely empty rather than with seats and cash the bot no longer holds. Cancelling a lift marks it `❌ cancelled` on the public board; cancelling the day marks the whole board cancelled. Either way the bot immediately tags the affected voters in the group thread so they know it is off. Public availability counts Telegram votes, manual bookings and guest seats, and shows each lift's state in words (`needs N more`, `N left`, `full`, `waitlist +N`, `❌ cancelled`). A lift with a waitlist names it on the next line, so `waitlist +2` stops being a riddle; the board is edited in place and a Telegram edit sends no notification, so naming informs without pinging. The seat order itself lives in one pure module used by both the board and the payments side, because two copies of that rule would drift and then disagree about who owes money. All admin monitors refresh when votes, manual counts, or cancellations change. An admin must open the private bot chat before Telegram will allow the monitor to be delivered.

A lift runs once it reaches the rider minimum — five, the point where the trip covers the driver's cost. No admin taps anything to confirm it; the background worker watches bookings and posts one group message tagging that lift's seat holders when it crosses the line — never the waitlist, who have no seat and so are not being asked to pay. The message is one line with a link to the day's payments board rather than a restatement of the rules, which live in the pinned notice; three lines of money talk per confirmed lift read as spam in the lift topic. Each lift announces this at most once, ever. If the count later falls back below the minimum the bot says so too, but only after the number has held for ten minutes: a drop is usually one rider re-picking slots, and a "running / short / running" burst is worse than silence. Recovering back above the minimum is silent. Cancelled lifts never signal. The day's earliest running lift also gets a single departure reminder shortly before it leaves — lateness is what kills the opening run, while later lifts self-correct because the van is already cycling. All of these are batched per worker tick, so a tick that trips several lifts still sends one message per kind. Because they ride on the same 30-second tick as auto-posting, a notice can lag a vote by up to half a minute, and they run even when no posting schedule was ever configured.

One `PaymentTerms` object carries the money rules — price, deadline, link — so the poll notice, the ✅ message and the payments board all state them identically instead of drifting apart. The poll notice says a lift runs from five riders and that there is nothing to pay before the ✅ message; the ✅ message itself asks for the money and links to the board, without repeating the rulebook. It says *pay to lock it in* rather than *is running*, because five bookings only make the money due — what the group agreed actually settles a lift is five prepayments by the deadline, and promising a van nobody has paid for is the kind of over-promise that gets the bot ignored. The link points at the payments topic itself, derived from the chat and thread ids, so there is nothing to configure and nothing to keep in sync; with no payments topic set the link line is simply omitted.

The notice is re-rendered in place once per process for every upcoming day, so a changed price or rule reaches notices that were already posted. Recreating the polls would also carry the new text but would throw away live votes; a Telegram edit is silent and costs nothing.

`BOOKING_DEADLINE_TIME` is when booking and paying closes, on the evening *before* a lift day. Two hours before it, each service day gets one reminder in the lift topic naming the deadline and what every lift still needs. It is sent once and then kept fresh in place: riders book after it lands, and a stale count is worse than none in the message people act on. It stays live past the deadline too, because nothing actually closes — the Telegram poll stays open and the bot enforces nothing — so only the call to action changes, to say the deadline passed and late changes are Misho's call. Editing costs nothing when the text has not moved. It is sent only while at least one lift is short of the minimum — when everything already runs there is nothing to ask the group for, and when everything is cancelled there is nothing to save. The reminder tags nobody: whoever is booked is already booked, and tagging the whole group is the spam this bot exists to avoid.

The board is pinned in the payments topic, because it is the payments menu and belongs at the top rather than wherever the day's chatter pushed it. Once a day has a running lift, the bot posts one payments board for that day into `TELEGRAM_PAYMENTS_THREAD_ID` and keeps editing it: which lifts are running, the per-seat price, who has paid, and how many riders are still outstanding — as a count, never a name list. Nothing appears in the payments topic before a lift reaches the minimum.

Riders act on the board with inline buttons rather than a command, because a button in the group works for the many riders who never opened a private chat with the bot. `💸 I paid` records a claim priced as seats × `PAYMENT_PRICE_GEL`, defaulting to the number of running lifts that rider booked; `➕ Guest` and `➖ Seat` adjust the seat count, which is also how someone who booked two lifts but will only ride one corrects their total. `↩️ Undo` withdraws the claim. The labels are deliberately impersonal — one shared keyboard serves everyone — and each tap answers with a private toast.

When a rider claims, the bot posts their payment line to the payments topic on their behalf, tagged with amount and lifts, so Misho reads one format instead of a mix of screenshots and free text. If that rider already wrote in the payments topic since this weekend's polls were created, the bot stays silent and only records the claim: it checks that a message exists, never what it says. A later seat change edits the line the bot already posted instead of adding another. Payments are tracked per rider per day, not per lift, because a rider who booked two lifts may only ride one.

Cancelling something people already paid for sends the admin a refund list in their private chat: who paid, how much, and the total to return. Because payment covers the day rather than a single lift, cancelling one lift is not automatically a refund — riders still booked on another lift that day are listed as staying, and only riders left with nothing appear as refunds. Cancelling the whole day makes every payment a refund. Nobody paid means no message.

A message in the payments topic counts as a payment report on its own: the group convention is that you post there once you have paid, so the bot marks the rider for the soonest running day they are booked on and adds no line of its own. It never reads the text — presence is the whole signal — which does mean a question asked in the topic counts too, and an admin clears that from the monitor. A post written before that day's poll existed is ignored.

The board tags whoever still owes rather than printing a count. Editing a message sends no Telegram notification, so the names show the gap without nagging anybody.

Tapping 💸 or 💵 while only part of a booking has filled warns first and charges on the second tap, naming what filled and what did not. Charging 15 GEL to somebody who booked three lifts reads as a bug rather than a rule, and cash makes it worse — money handed over twice means finding Misho twice. The warning fits Telegram's 200-character callback answer, so it stays terse. One warning shows at a time, waitlist first.

The private form can settle the whole day instead, unfilled lifts included. Nobody is pushed there — the rule stays that you need not pay before five riders — but one transfer beats two. Paying ahead then reads as `15 prepaid` on the board rather than `15 back`: the arithmetic is the same as an overpayment and the meaning is the opposite, so the two are told apart by whether the rider still holds an unfilled booking.

That form is the rider's own day rather than a guest sheet: guests open there too, because only a form can say how many seats a lift has left and what this rider owes, and only a private chat has room for one row per lift. The board links there, and the link is a deep link — tapping it *is* pressing Start, which is how a rider who never opened the bot ends up with a private chat the bot may write to. Bringing someone for the whole day is one tap; the per-lift rows are for a guest who only rides some laps, and vanish when the host holds a single lift. A full lift offers no plus button. Guest seats are per lift because a seat is capacity, and capacity belongs to a lift.

Capacity is filled in a fixed order: manual bookings and guests hold their seats outright — an admin took them personally, and a guest belongs to a rider who declared them — and Telegram voters fill the rest in booking order, the same order the poll shows. Anyone booked beyond that is on the waitlist, holds no seat, and is therefore never billed or listed as owing. They may still pay for a waitlist place, but the bot warns once and takes the same tap again as consent; it is their money and their call, never a silent charge.

Cash has its own `💵 Cash` button beside `💸 I paid`. Telling riders to press the transfer button anyway is a rule most of a 166-person group will not follow, and the method is worth recording in its own right: Misho reconciles against his bank statement, so a cash line is the one he must *not* go hunting for. Cash is named on the board and in the posted line; a transfer is not, because its absence already means "expect it in the bank". An admin mark leaves the method unset — guessing "cash" there could send Misho looking for a transfer that never existed.

Some riders pay cash or message Misho directly and never touch the bot at all. Tapping a rider in the monitor's lift detail records their payment; tapping again clears it. That admin mark posts nothing to the payments topic — whoever took the cash already knows — and the board shows it exactly like a rider's own claim, because who recorded a payment is bookkeeping the group does not need to read. There is no separate "verify a claim" step: with no enforcement behind it, a tap per rider would be work without a consequence.

Paid and owed are two different numbers, because re-voting is free until the deadline. A claim stores what the rider has settled for; what they owe is derived from the lifts they currently hold plus the guests they declared. When the two differ the board says so — `+15 due` or `15 back` — rather than restating the paid figure, which would make the bot lie about money. Tapping 💸 or 💵 again settles the difference; tapping with nothing outstanding just says you are already settled. ➕ Guest raises the bill rather than claiming the extra seat is paid.

Deriving from live votes is right up to the deadline and wrong after it. The group's rule is that once a lift is full by `BOOKING_DEADLINE_TIME` the trip is happening and the money is spent, so the first worker tick past the deadline freezes the day into a **deadline roster**: one row per rider per lift, own seat and guests, plus a row for the manual bookings that also count towards five. Written once, never updated — a live table cannot answer "who was on this at eight o'clock" — and marked on the service day even when it comes out empty, so "nobody booked" is not retried every thirty seconds as "not captured yet".

The roster then holds exactly two things still, and nothing else. A lift that reached the minimum by the deadline stays running, so the board cannot walk back a trip the group treats as settled — two people leaving no longer turns it into `needs 1 more`. And a rider who **paid** stays on the hook for what they paid for, so a late cancellation stops reading as `15 back`.

Booking and cancelling stay free after the deadline. The poll is open, the bot enforces nothing, and a rider who never paid owes nothing by leaving: the group agreed that a prepayment is not refundable, not that forgetting to come is a debt. Somebody who walks out late is a question for Misho, so they surface in the admin monitor rather than on the public board as owing money. Seats freed that way are genuinely free and the waitlist moves up. The one refund the group does recognise is a lift the admin cancels after the deadline, so those rows drop out of the overlay entirely. A day whose poll was created after its own deadline is never frozen, since it never had one.

The receipt the bot posts in the payments topic names no lifts, only the day and the amount. Payment covers the day, and a receipt listing lifts would go stale the moment the rider changed slots; the live breakdown belongs on the board.

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
```

`/start` is the only command the bot suggests; everything else is a menu button. `/create_lift_poll` still works as an unlisted shortcut to the weekend card. Only Telegram user IDs listed in `TELEGRAM_ADMIN_IDS` can use either. The setup flow starts with upcoming Saturday and Sunday enabled and supports disabling either day. Lift times are configured as a continuous first-to-last range. Confirmed weekend schedules are remembered; a changed start or end time must be used for two consecutive weekends before it becomes the suggested default.

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
