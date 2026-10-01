# VeloExpress web cabinet

The app works in Telegram and in a regular browser. Routes are hash-based so a
shared link such as `/#analytics` survives a refresh. Node 24 and the Python
version in `.python-version` are the supported local/build runtimes.

## Local visual review

```sh
npm --prefix web ci
just web
```

Open http://localhost:5173/?demo=1. This is a fully navigable synthetic preview:
month/year/all-time/custom ranges, day details, admin forms and the rider view.
The banner always identifies demo data. Demo actions explain their result but do
not mutate the fixtures, write to Postgres or send Telegram messages. Desktop and
mobile screenshots from the initial review are under ignored `output/playwright`.

## Local API and bot

Copy `.env.example` to `.env`, configure a test bot/group, and set a random
`WEB_SESSION_SECRET` of at least 32 characters. Do not use the real group for
local UI checks. `WEB_PUBLIC_URL` must exactly match the browser origin, including
its port: use `http://localhost:5173`, or `http://127.0.0.1:5173` consistently.

```sh
just dev     # Postgres, migrations, Telegram bot and workers
just api     # another terminal: API on 127.0.0.1:8000
just web     # another terminal: frontend on localhost:5173
```

Vite proxies `/api` to FastAPI. Open the production HTTPS URL from the bot to test
real Telegram Mini App authentication. An ordinary local browser needs configured
Telegram OIDC (and an allowed redirect URI); the preview is available without
credentials. There is no fake login endpoint.

Checks:

```sh
just check
just web-check
npm --prefix web run format-check
```

Postgres concurrency tests also need `TEST_POSTGRES_URL`, pointing to a disposable
migrated database. API tests use synthetic identities and a fake Telegram adapter.

## Coolify deployment

1. Back up the existing database using `postgres-backup.md`. Retain the existing
   Coolify Compose resource/project so its `postgres-data` volume is reused.
2. Reload `docker-compose.coolify.yml` from Git. Keep existing bot/Postgres
   variables, and add:

   | Variable | Value |
   | --- | --- |
   | `WEB_PUBLIC_URL` | HTTPS origin, e.g. `https://app.example.com` |
   | `WEB_SESSION_SECRET` | Random secret, at least 32 characters |
   | `WEB_APP_URL` | Same HTTPS app URL; enables the bot's Mini App menu |
   | `WEB_TELEGRAM_CLIENT_ID` | BotFather Login Widget client ID, for browser login |
   | `WEB_TELEGRAM_CLIENT_SECRET` | Matching secret; server only |

   Generate a secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   Do not put credentials into Vite variables or Git.
3. Point the domain DNS to the Coolify server. Assign its HTTPS domain to **web**
   (container port 80). API and Postgres have no published host ports. Coolify
   terminates TLS; Nginx serves the app and proxies `/api` to the API container.
4. Deploy. `migrate` runs Alembic once, then bot/API start; web waits for API health.
   Run only one bot replica. API replicas must not start background workers.
5. In BotFather configure the Main Mini App HTTPS URL. The bot sets its menu
   button when `WEB_APP_URL` is configured.
6. For browser login, add the HTTPS origin and exact
   `https://app.example.com/api/auth/callback` URI in BotFather's Login Widget.
   Use RS256 and the `profile` scope. A user authenticated by Telegram becomes
   an admin only if their numeric Bot API ID is in `TELEGRAM_ADMIN_IDS`.
7. Verify `/api/health`, both login paths, an ordinary user's private history,
   read-only historical day details, and a reversible action in the test group.
   Live Telegram/OIDC acceptance checks require the configured domain and bot;
   local automated checks cannot replace them.

`CI` verifies Python (including Postgres concurrency), the frontend and Docker
builds. Its production deployment job depends on both checks and triggers only
for the current `main` commit after a push. Set `COOLIFY_WEBHOOK` and
`COOLIFY_TOKEN` in the GitHub `Production` environment. Keeping deployment in
the push workflow also works when the repository's default branch is `dev`;
a separate `workflow_run` workflow would need to exist on the default branch.
Keep Coolify's independent automatic Git deployment off if CI is the release
gate. No deployment is initiated by pull requests, local development or visual
preview. Development deployments retain their existing configuration.

A bot outage leaves commands pending. An interrupted or ambiguous execution
becomes **review**: inspect the day, ledger and Telegram before issuing a fresh
request. A transport retry must retain the same UUID. Refreshing the browser
continues watching an acknowledged pending command from that browser session.

## Rollback

Use the previous application image/commit with the same Postgres volume. Migration
0029 only adds the command audit table; leave it in place during an application
rollback so its audit survives. Do not run `docker compose down -v` on the existing
resource. The previous bot will not execute queued web commands; inspect pending
requests before re-enabling the new executor.

## Initial implementation verification (2026-10-01)

- 447 Python tests passed, including ten Postgres concurrency checks, plus four
  frontend period/formatting tests. Ruff, Pyright, TypeScript and Prettier passed.
- All migrations applied to a fresh disposable Postgres 18; the final suite used
  a temporary RAM-backed database after the local Docker disk filled up.
- Python and web Docker builds succeeded. The disposable Nginx/API/Postgres stack
  returned `{"status":"ok"}` through the public `/api/health` path.
- Browser checks covered month/year/all-time views, desktop and 390px mobile
  layouts, navigation, protected published plans, full-lift seat controls,
  cancellation preview/confirmation, and the personal rider cabinet.
- The real Telegram/OIDC deployment acceptance checks described above remain
  necessary once the HTTPS origin and BotFather credentials are configured.

## Mobile refinement (2026-10-01)

The Mini App now uses a mobile bottom navigation (overview, analytics, departures,
planning and More), with a separate three-item rider navigation. More opens a
bottom sheet for reports and settings. Theme choice (light/dark/automatic) is
saved locally; automatic follows Telegram in a Mini App and the device preference
in a regular browser. Telegram header/background colors follow the chosen theme.

Forms use styled Radix selects with keyboard navigation and viewport-aware menu
placement. Grid columns can shrink, long publishing buttons wrap, and the payment
donut uses percentage radii. Browser checks covered 320/360/390/430px layouts in
both themes, dropdown choices, day-dialog payment controls, theme persistence,
automatic theme changes and tab scroll reset. Bottom navigation reserves room for
Telegram/iOS safe areas and floating notifications.

## Compact Mini App workspaces

The rider view selects one service day at a time. Places and payment totals are
shown first; guest controls, payment details/undo and bank information are opened
on demand. A fully reported payment becomes a status line; reporting/correcting
it remains available in the payment details. The synthetic preview includes a
paid Saturday and a partially paid Sunday.

On mobile, planning shows one form (weekend/publication/terms/extra day), and
analytics shows one category (trend/loading/money/participation/history). Desktop
keeps the broader dashboard layout. Hidden charts are not mounted on mobile, so
switching categories does not leave zero-width chart measurements running.
Initial history shows five days with an explicit Show more action. The mobile
header, preview banner, spacing and repeated section introductions are reduced;
fonts and primary touch targets remain readable.

Browser checks covered date selection, both payment states, guest expansion,
payment undo access, all planning/analytics sections, history expansion, desktop
layout and widths 320/360/390/430px. At 390x640 the selected day's places, totals
and payment-report buttons were above the bottom navigation without scrolling.
