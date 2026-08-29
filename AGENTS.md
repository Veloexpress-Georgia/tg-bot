# Agent Guide

Use `README.md`, `CONTEXT.md`, and accepted decisions under `docs/adr/` as the current source of truth. The original `.omx` MVP plan is historical context only.

## Commands

- Install: `uv sync --dev`
- Format: `just format`
- Check: `just check`
- Run bot: `just run`
- Run local Docker infrastructure: `just up`
- Stop local Docker infrastructure: `just down`
- Run migrations: `just db-migrate`

## Scope

The current product includes:

- weekend and extra-day poll planning
- booking, capacity and waitlist monitoring
- service-day payment tracking and deadline rosters
- automatic scheduling, notices and Telegram-message recovery

Balances across service days, fines, a website, miniapp, and template editing remain out of scope unless explicitly approved.

## Code Boundaries

- Keep Telegram API calls in `src/veloexpress_bot/telegram/`.
- Keep pure poll rendering in `src/veloexpress_bot/polls/`.
- Keep aiogram handlers thin; call services for business work.
- Add tests for pure logic and service behavior before changing live Telegram behavior.

## Product Design

- Design for minimal interaction with the bot. Admins should not need sync,
  repair, or maintenance commands for normal operations.
- Treat manual Telegram admin actions as first-class behavior. Admins may delete
  old polls or setup messages manually; the bot should reconcile at the next
  decision point and continue naturally.
- Prefer automatic, silent recovery over asking admins to learn bot workflows.
  Surface warnings only when the bot genuinely needs human attention.
- Keep native Telegram polls as the main user/admin surface. The database stores
  observed state and audit history, not absolute truth.

## Git

Prefer small, reviewable commits. Use commit messages that explain intent, not only changed files.
