# Agent Guide

Use `.omx/plans/ralplan-veloexpress-bot-mvp.md` as the current source of truth.

## Commands

- Install: `uv sync --dev`
- Format: `just format`
- Check: `just check`
- Run bot: `just run`
- Run DB locally: `just db-up`
- Run migrations: `just db-migrate`

## Scope

First MVP is admin-only poll automation:

- aiogram command and setup flow
- predefined RU/EN poll template
- cancel default lifts
- toggle first lift location
- post one native poll per service day
- persist poll batch/message IDs

Do not implement payment tracking, balances, overbooking, scheduling, website, miniapp, or template editing unless a newer plan supersedes this file.

## Code Boundaries

- Keep Telegram API calls in `src/veloexpress_bot/telegram/`.
- Keep pure poll rendering in `src/veloexpress_bot/polls/`.
- Keep aiogram handlers thin; call services for business work.
- Add tests for pure logic and service behavior before changing live Telegram behavior.

## Git

Prefer small, reviewable commits. Use commit messages that explain intent, not only changed files.
