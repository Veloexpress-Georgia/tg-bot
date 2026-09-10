# Veloexpress Bot

Telegram bot for the Veloexpress shuttle group: it runs the weekend lift polls, shows which lifts reached the rider minimum and which are funded, and keeps an append-only record of reported money.

Admins work from a single private menu — `📋 Weekend` plans and schedules what gets posted, `📊 Booking monitor` is the live view during a weekend, `➕ Extra lift day` covers midweek rides. A background worker posts the polls on time, announces a lift once it reaches the rider minimum, reminds the group before the booking deadline, and pings the day's first lift before it leaves. Riders confirm payment with a button in the group or simply by writing in the payments topic.

Still out of scope: balances across service days, fines, automatic removal of Telegram votes, miniapp, website, and template editing.

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
PAYMENTS_VIA_PRIVATE_CHAT=false
```

`TELEGRAM_ADMIN_IDS` must be numeric Telegram user IDs, not usernames.

`TELEGRAM_PAYMENTS_THREAD_ID` is the payments topic. Leave it empty to switch the payments board off entirely.

`SCHEDULE_TIMEZONE` defaults to `Asia/Tbilisi`. `PAYMENT_PRICE_GEL=15` and `BOOKING_DEADLINE_TIME=20:00` are bootstrap defaults; admins can change the price and deadline at runtime from `⚙️ Service defaults`. Runtime changes apply only to days published afterwards.

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

All admin controls live in a private chat with the bot. There is no Close button on any card: `/start` posts a fresh card and then deletes the command echo along with whatever card an earlier `/start` left behind, so exactly one card is ever live and the bot tidies up instead of asking. `/start` answers rather than greets: it opens with each active day's state — how many lifts reached the minimum, how many are funded, how many riders reported payment — and when the next polls appear, with the menu underneath. `⚙️ Service defaults` changes the price in 5 GEL steps and the deadline in 30-minute steps without a redeploy. `/create_lift_poll` still jumps straight to the weekend card but is no longer suggested. Polls are always posted to `TELEGRAM_TARGET_CHAT_ID` and, when configured, `TELEGRAM_TARGET_THREAD_ID`.

`📋 Weekend` is the single source of truth for what gets posted: it shows the upcoming weekend, the lift-time range (learned from recent weeks), which days are enabled, and when the polls will open automatically. Edits are saved as a plan — nothing posts until the scheduled time or an explicit `🚀 Post now`. Posted days are marked and protected; `♻️ Recreate polls` (with confirmation) replaces an already-posted weekend and reports tracked votes first. A `⏭ Skip weekend` toggle suppresses one auto run.

`⏰ Opens` sits inside the weekend card rather than in the menu, because an admin thinks about one weekend and not about a standing setting — the card's `Opens` line always shows when polls appear, or `off`, so the auto-posting switch cannot hide. It controls the "when": the posting day (Monday–Saturday) and time in `SCHEDULE_TIMEZONE` (default `Asia/Tbilisi`), plus an optional group announcement 1–3 hours before posting. A background worker checks the schedule every 30 seconds and stores all state in Postgres, so restarts never lose or duplicate a run. The worker posts whatever the weekend plan says, skips days that already have active polls, and deletes the announcement message once the polls are up.

`➕ Extra lift day` covers mid-week lifts: pick a date within the next 7 days and a lift range, and the polls post to the group immediately (unpinned, so weekend polls stay pinned).

`📊 Booking monitor` opens with the day at a glance — how many lifts run out of those still active, total seats, how many riders have paid — counting only riders holding a seat on a lift that is still on, since the poll also carries a "👀 Check answers" option and people who only peeked at the results are not debtors — and the money as `paid of owed` — above the per-lift lines. The money is two figures rather than one because a bare total sat next to a seat count built from different rules (claims cover guests and lifts paid ahead; seats did not) and read as a contradiction. A day that has already run stays visible for late bookkeeping but loses its `🚫 Cancel all` button: cancelling reports every payment as a refund, which for a trip people actually took would hand back money that was earned. When a new weekend is posted, every admin's monitor is re-posted rather than edited, so it arrives at the bottom of the chat instead of staying buried where it was last opened; only admins who have opened it at least once can be reached, because Telegram refuses a private message to anyone who never started a chat with the bot.

The monitor keeps one reusable live message per admin, with day tabs and `➖`/`➕` controls for people booked outside Telegram. Tapping a lift opens its detail (riders, manual and guest counts) with `🚫 Cancel lift` / `♻️ Restore lift` — a single lift is reversible. A `🚫 Cancel all` button retires the whole day: the polls close, the board shows all lifts cancelled, the day leaves the monitor, and the date is freed so an admin can post a fresh poll if it comes back (there is no in-place "restore day"). The day takes its hand-added riders and its payments with it — nobody can tell whether a manually added rider still intends to come, and the money is going back, so a revived poll opens genuinely empty rather than with seats and cash the bot no longer holds. Cancelling a lift marks it `❌ cancelled` on the public board; cancelling the day marks the whole board cancelled. Either way the bot immediately tags the affected voters in the group thread so they know it is off. Public availability counts Telegram votes, manual bookings and guest seats, and shows each lift's state in words (`needs N more`, `N left`, `full`, `waitlist +N`, `❌ cancelled`). A lift with a waitlist names it on the next line, so `waitlist +2` stops being a riddle; the board is edited in place and a Telegram edit sends no notification, so naming informs without pinging. The seat order itself lives in one pure module used by both the board and the payments side, because two copies of that rule would drift and then disagree about who owes money. All admin monitors refresh when votes, manual counts, or cancellations change. An admin must open the private bot chat before Telegram will allow the monitor to be delivered.

A finished lift day is written down the morning after, into a record the bot never edits again. The day itself stays live throughout: riders put themselves on the morning van, and somebody who never showed up unbooks too late to matter. That is the day happening, not error — it is the next day that nothing can move any more, so that is when the worker records which lifts went, how many seats each held, and who was on them. History used to be recomputed from live votes on every read, which was wrong twice over: Telegram polls are never closed, so a vote changed weeks later silently rewrote a weekend the group had already lived through, and no count of votes could say that Misho had taken a van out with four riders. Money is not copied into the record, because `PaymentEntry` is already append-only with reversals as negative rows — the ledger, not the claim projection, is what the screens sum. Days that finished before the freeze existed are left blank rather than reconstructed from votes; days the bot catches up on late — a deploy, an outage over a weekend — are marked, and both the list and the day screen say so rather than presenting a guess as a record.

`📜 Lift history` hangs off the monitor on any week, not only a quiet one: the weeks an admin is actually in the bot are the weeks with lifts running, and history used to be unreachable on every one of them. It lists a page of days newest-first with `📅 Older` behind it, and the season line under the page is an aggregate over the whole record, so a truncated list never becomes a truncated total. Tapping a day opens it in full — which vans went, who held a seat, who had not paid, what came in and what went back out in refunds. That seat list is who held a place when the day closed rather than who paid, so somebody who turned up without paying is on it with a `🔴` beside them, which is the reason to open the screen at all. `📈 Trend` reads the same record sideways, by departure and by weekday: `15:30 — never ran in 14` is the answer to whether a lift earns its place in the template, and a weekend day counts once rather than once per van on it.

Riders get their own copy of it. `📜 My past rides` on the private card lists the lifts they actually rode and what they paid, and it is offered on the empty card too — midweek with nothing booked is exactly when somebody idly opens it and used to find a dead end. It follows the frozen day rather than the deadline roster, because the two disagree precisely where it matters to a rider: a van they joined on the morning counts, and a booking they dropped before it went does not.

`📊 Statistics` in admin Lift history opens a period overview (last 30 completed days, current calendar year, or all recorded history): days, lifts, registered riders, rider-days, seats, guests, manual seats, weighted occupancy, and reported payments with reversals. Riders are paginated and open into individual statistics. `📊 My statistics` on the private rider card exposes the same calculations for the current Telegram user only, even with no upcoming booking or when payment reporting is disabled. Money is aggregated separately from rides to avoid multiplying a day payment by the number of lifts. Reconstructed days are labelled; frozen seats are not an attendance check. The next history improvements are described in [the history design](docs/design/history-and-rider-statistics.md).

A lift opens payment once it reaches the rider minimum — five, the point where the trip can cover the driver's cost. The background worker adds payment controls to the existing availability message when the first lift crosses that line; subsequent lifts update that same booking status without extra messages or tags. The monitor then distinguishes `payment open`, `funded`, and `decision needed` instead of promising that the lift is already running. If the booking count later falls below five, the bot reports it after a ten-minute debounce. The day's earliest viable lift also gets one departure reminder shortly before it leaves.

One `PaymentTerms` object carries the money rules — price, deadline, link — so the notices and payments board state them consistently. The poll notice says payment opens at five riders. Instead of a separate tagged ✅ message or payment card in the lift topic, payment controls live under the existing booking statistics (`Availability`). `I paid` occupies a full keyboard row, with `Cash` and `Undo` underneath. The worker updates existing messages after deployment and removes controls after the service day. Previously posted standalone payment cards in the lift topic are unpinned and deleted automatically; failed cleanup is retried. Full transfer details, a small `Guests` text link, and detailed payment history remain in the payments topic and private rider card.

Opening polls on Thursday does not start a daily reminder cycle. A Saturday service day's payment deadline is Friday evening; Sunday's is Saturday evening. The payments board includes the deadline date as well as its time so a Thursday reader need not guess. Each day gets at most one pre-deadline reminder, linking back to its booking statistics in the lift topic.

Price, deadline and timezone are snapshotted when a service day is first published. A later deploy or default-price change cannot rewrite an amount riders already agreed to; new defaults apply to newly published days.

`BOOKING_DEADLINE_TIME` is when booking and paying closes, on the evening *before* a lift day. Two hours before it, each service day gets one reminder in the lift topic naming the deadline and what every lift still needs. It is sent once and then kept fresh in place: riders book after it lands, and a stale count is worse than none in the message people act on. It stays live past the deadline too, because nothing actually closes — the Telegram poll stays open and the bot enforces nothing — so only the call to action changes, to say the deadline passed and late changes are Misho's call. Editing costs nothing when the text has not moved. It is sent while an active lift needs more riders or has reached the rider minimum but still needs paid seats to fund it. The message distinguishes missing riders from missing paid seats and updates silently when either changes. Fully funded days and days with everything cancelled need no reminder. The reminder tags nobody: whoever is booked is already booked, and tagging the whole group is the spam this bot exists to avoid.

The board is pinned in the payments topic, because it is the payments menu and belongs at the top rather than wherever the day's chatter pushed it. On the next local day, the worker removes its buttons and unpins it, keeping the message as history. Older tracked boards are cleaned up automatically after a restart too; failed cleanup is retried, and late bookkeeping never restores the old controls. Once a lift reaches the rider minimum, the bot posts one payments board into `TELEGRAM_PAYMENTS_THREAD_ID` and keeps editing it: which lifts have payment open, the snapshotted price, reported payments, and the riders still outstanding. Nothing appears before a lift reaches the minimum.

`💸 I paid` and `💵 Cash` record the payment in place, as in-group callbacks. `PAYMENTS_VIA_PRIVATE_CHAT` turns them into deep links instead: paying is the most frequent thing anybody does here, so routing it through the private chat converts nearly the whole paying group within a weekend, and nothing else can reach the ~160 members Telegram will not let the bot message. Unlike a link that only opens the rider card, these two are the rider asserting that money has moved, so acting on arrival is exactly the promise the button already made.

It is off by default because the trade is real: a callback records the claim the instant it is tapped, while a link records it only once the rider completes Start, so somebody who backs out has told nobody. That is close to self-correcting — the board is right there and will still list them under `Waiting on` — and it stops mattering once they have the chat, but it wants watching on a test group before 166 people meet it. Without a bot username the links fall back to callbacks too, so the board is never left with no way to pay. The `Guests` text link is unaffected either way: it opens a private chat, because only a form can hold a row per lift.

When the setting is on, arriving by one of those links settles up immediately, unless something needs saying first. A partial booking or a waitlist place records nothing and opens the card instead: the board has to squeeze those warnings into a 200-character toast and a second tap, while the card has room to lay out which lifts filled, what is due now and what the whole day costs, with the buttons underneath. Explaining beats charging quietly, and it retires the two-tap gesture.

Riders act on the board with inline buttons rather than a command. `💸 I paid` and `💵 Cash` append only the outstanding amount to the rider's day ledger. `👤 Guests` opens the private rider card, where guest seats are adjusted per lift. Before the deadline, `↩️ Undo` appends a reversal rather than erasing financial history.

When a rider reports payment, the bot posts their day and amount to the payments topic on their behalf, without a fixed lift list: coverage follows their current bookings before the deadline. If that rider already wrote in the payments topic since the poll was created, the bot stays silent and records the report only once.

Cancelling something people already paid for sends the admin a compact refund estimate: each rider's total to return, amount received, and amount retained for rides. Riders with no refund are omitted. Each report is cumulative for the service day, including amounts shown in earlier reports; actual payouts are not tracked. Generating a report never records money as returned. Reports are saved as snapshots and available through `🧾 Past refunds`. Cancelling a whole day archives its payment projection so a fresh poll starts empty, while immutable received-payment entries remain. Nobody paid means no message.

A message in the payments topic counts as a payment report on its own: the group convention is that you post there once you have paid, so the bot marks the rider for the soonest running day they are booked on and adds no line of its own. It never reads the text — presence is the whole signal — which does mean a question asked in the topic counts too, and an admin clears that from the monitor. A post written before that day's poll existed is ignored.

The board tags whoever still owes rather than printing a count. Editing a message sends no Telegram notification, so the names show the gap without nagging anybody.

Tapping 💸 or 💵 while only part of a booking has filled warns first and charges on the second tap, naming what filled and what did not. Charging 15 GEL to somebody who booked three lifts reads as a bug rather than a rule, and cash makes it worse — money handed over twice means finding Misho twice. The warning fits Telegram's 200-character callback answer, so it stays terse. One warning shows at a time, waitlist first.

The private form can settle the whole day instead, unfilled lifts included. Nobody is pushed there — the rule stays that you need not pay before five riders — but one transfer beats two. Paying ahead then reads as `15 prepaid` on the board rather than `15 back`: the arithmetic is the same as an overpayment and the meaning is the opposite, so the two are told apart by whether the rider still holds an unfilled booking.

Behind every pay link is the rider's own card: their whole upcoming weekend, with days as tabs the same way the admin monitor works. It holds what a shared board cannot — where they stand in a waitlist queue, what they personally owe against what they have paid, and one row per lift for guests. Bringing someone for the whole day is one tap; the per-lift rows are for a guest who only rides some laps, and vanish when the host holds a single lift. A full lift offers no plus button. Guest seats are per lift because a seat is capacity, and capacity belongs to a lift.

The rider card offers named guest actions per lift, with no empty or inert counter buttons. Adding a guest to all lifts is offered only when every seated lift has space. Both the public payments board and the private rider card show the recipient and BoG/TBC accounts directly above the payment buttons, as copyable monospace text, without additional copy buttons. No separate screen is needed; copying details never records a payment.

Exactly one card exists per rider, replaced rather than repeated. A fresh message per tap would leave the chat stacked with old cards, each still carrying live buttons over amounts that had since moved. It is posted before the previous one is deleted, so there is never a moment with no card, and posted rather than edited because the rider has just sent `/start` — a silent edit far above their own message reads as nothing having happened. Taps on the card itself edit it in place.

A waitlisted rider is asked for nothing and shown no money buttons: they hold no seat, so there is nothing to settle. Position in the queue appears only here, because it is the part they actually want and the public board shows names without an order. The card also skips the board's two-tap warnings — those exist because a group button charges an amount whose working the rider cannot see, and the card *is* that working, printed above the button.

When a seat frees up, whoever was waiting is tagged in the lift topic. A cancellation is not bad news for them — it is a seat — but only if they hear about it, and the availability board that shows the new order is edited silently. Promotion means exactly one thing: was on the waitlist, now holds a seat. Somebody booking into an empty seat is not news to the person who just booked, and the first time the bot sees a lift it records the order and says nothing, because nobody moved. This keeps working past the booking deadline, which is when late cancellations actually happen.

Capacity is filled in a fixed order: manual bookings and guests hold their seats outright — an admin took them personally, and a guest belongs to a rider who declared them — and Telegram voters fill the rest in booking order, the same order the poll shows. Anyone booked beyond that is on the waitlist, holds no seat, and is therefore never billed or listed as owing. They may still pay for a waitlist place, but the bot warns once and takes the same tap again as consent; it is their money and their call, never a silent charge.

Cash has its own `💵 Cash` button beside `💸 I paid`. Telling riders to press the transfer button anyway is a rule most of a 166-person group will not follow, and the method is worth recording in its own right: Misho reconciles against his bank statement, so a cash line is the one he must *not* go hunting for. Cash is named on the board and in the posted line; a transfer is not, because its absence already means "expect it in the bank". An admin mark leaves the method unset — guessing "cash" there could send Misho looking for a transfer that never existed.

Some riders pay cash or write in the payments topic instead of using the transfer button. Those paths create the same immutable money entries, with the payment method kept for reconciliation. There is no separate verification workflow.

Paid and owed are two different numbers, because re-voting is free until the deadline. Payments are immutable money entries for a rider and service day; coverage is recalculated over the lifts and guests they currently hold. Changing one lift for another keeps the same coverage, while adding a seat creates a top-up. At the deadline the covered roster is frozen; later additional seats require additional payment.

Deriving from live votes is right up to the deadline and wrong after it. The group's rule is that once a lift is full by `BOOKING_DEADLINE_TIME` the trip is happening and the money is spent, so the first worker tick past the deadline freezes the day into a **deadline roster**: one row per rider per lift, own seat and guests, plus a row for the manual bookings that also count towards five. Written once, never updated — a live table cannot answer "who was on this at eight o'clock" — and marked on the service day even when it comes out empty, so "nobody booked" is not retried every thirty seconds as "not captured yet".

A missed deadline is never caught up. If the bot was down over 20:00, or a deploy lands mid-weekend, the moment has gone: a snapshot taken at Saturday lunchtime records who is booked at lunchtime, which is exactly the number the freeze exists to stop trusting, and it would chase people about a van that already left. So the freeze only happens while it is still the evening before, and missing it degrades to live behaviour — how the bot worked before any of this — rather than to a confident wrong answer.

The freeze is also when the bot notices a lift that closed underfunded. The group settles a lift at five paid seats, but somebody forgetting to tap a button is not a reason to call off a van — so instead of cancelling anything, one message in the payments topic names the shortfall (`8:30 — 2/5 paid`), tags the riders missing from it, and says the decision is Misho's. Cash counts exactly like a transfer, and manual bookings count as paid because Misho took them himself. Lifts that never reached the minimum are left out: nothing was due on them. Sent once, at the freeze, because the roster is captured once.

The roster then holds exactly two things still, and nothing else. A lift that reached the minimum by the deadline stays running, so the board cannot walk back a trip the group treats as settled — two people leaving no longer turns it into `needs 1 more`, and money paid for it stays spent instead of becoming `prepaid` credit towards a ride that already happened. And a rider who still holds their booking, or who paid, stays on the hook for it, so a late cancellation stops reading as `15 back`.

The one exemption is walking away unpaid. Booking and cancelling stay free after the deadline: the poll is open, the bot enforces nothing, and the group agreed that a prepayment is not refundable, not that forgetting to come is a debt. Somebody who leaves late is a question for Misho, so they surface in the admin monitor rather than on the public board as owing money. Staying is not leaving, though — a rider still booked on a lift that filled by the deadline owes for it however many others dropped out overnight. Seats freed by a cancellation are genuinely free and the waitlist moves up. The one refund the group does recognise is a lift the admin cancels after the deadline, so those rows drop out of the overlay entirely. A day whose poll was created after its own deadline is never frozen, since it never had one.

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

Concurrency tests in `tests/integration/test_postgres_concurrency.py` require
`TEST_POSTGRES_URL` pointing to a disposable PostgreSQL database already migrated
with Alembic. They are skipped without that variable locally; CI always runs them
on its Postgres service after migrations. They exercise concurrent first payments,
top-ups, undo, topic reports, and competing outbox dispatchers.

Money mutations acquire a transaction-scoped PostgreSQL advisory lock for the
service day before reading the payment projection. The lock covers missing first
payment rows and is released on commit, rollback or disconnect. Outbox dispatchers
hold a row lock through Telegram send and status persistence; other dispatchers
skip the in-flight row. An ambiguous external outcome (Telegram accepted a message
but the process crashed before storing its id) can still be retried: this does not
claim exactly-once delivery across an external API failure.

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
TELEGRAM_PAYMENTS_THREAD_ID=
TELEGRAM_PIN_POLL=true
SCHEDULE_TIMEZONE=Asia/Tbilisi
PAYMENT_PRICE_GEL=15
BOOKING_DEADLINE_TIME=20:00
PAYMENTS_VIA_PRIVATE_CHAT=false
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
The production healthcheck verifies three things:

- the bot event loop is alive by checking a heartbeat file updated by the running process;
- Postgres is reachable with a lightweight `select 1`.
- the required scheduler completed a fully successful tick recently; a live event loop with repeatedly failing business jobs is unhealthy.

Defaults are suitable for Coolify and Uptime Kuma Docker-container monitoring:

```env
APP_HEALTH_HEARTBEAT_FILE=/tmp/veloexpress-bot-heartbeat.json
APP_HEALTH_HEARTBEAT_INTERVAL_SECONDS=15
APP_HEALTH_MAX_AGE_SECONDS=90
APP_HEALTH_REQUIRE_WORKER=true
APP_HEALTH_WORKER_MAX_AGE_SECONDS=120
```

In Uptime Kuma, use a Docker Container monitor for the Coolify bot container and treat Docker health status as the signal. To debug manually:

```sh
docker compose -f docker-compose.coolify.yml exec bot python -m veloexpress_bot.healthcheck
```

Backups and restore drills are documented in [`docs/operations/postgres-backup.md`](docs/operations/postgres-backup.md).

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
