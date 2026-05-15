# Contributing

This project is early and intentionally small. Keep first-MVP changes focused on Telegram poll automation.

## Setup

```sh
uv sync --dev
lefthook install
just check
```

## Before Opening a PR

```sh
just check
```

Document any Telegram smoke-test result in the PR. If you could not run a live Telegram smoke test, state why.

## Scope Guard

Do not add these features without a new approved plan:

- payment tracking
- rider balances or fines
- overbooking/waitlists
- scheduling
- miniapp or website
- template editor
