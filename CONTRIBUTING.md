# Contributing

Keep changes focused on the existing Telegram workflow: planning, bookings, payments, and operational recovery.

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

- rider balances or fines
- miniapp or website
- template editor
