# Shared application modules and a web cabinet

The Telegram bot and FastAPI use `veloexpress_core` for lift, payment, history,
planning, scheduling and service-day-term behavior. Existing logic was moved,
not reimplemented. The Telegram adapter, rendering, configuration and persistence
remain in `veloexpress_bot`; the shared modules still use those existing injected
adapters and view types. Further separation of presentation can happen in small
steps without rewriting the money rules.

`runtime.build_runtime` is the composition root. Only the bot starts the scheduler
and command worker. FastAPI authenticates users and serves structured read views;
it does not start another Telegram poller or scheduler. Native Telegram polls
remain the booking interface. The rider cabinet shows those bookings and allows
payment reporting, undo (under the existing rules) and guest adjustments.

Web writes become durable `AdminCommand` records with a scope, actor, UUID,
validated payload and execution status. The bot executes them through the same
application modules. UUID retries return the existing operation. State-sensitive
cancellations/publications require a signed, expiring preview; the worker checks
that the relevant state still matches before executing. The audit includes rider
commands as well as admin actions. It covers web requests, not every historical
Telegram interaction.

There is one bot executor per environment. A command interrupted after starting
is marked `review` on restart and never automatically repeated: existing
application operations may commit money/state before an external Telegram call.
This is a durable request/audit mechanism, not an exactly-once transaction across
Postgres and Telegram. `pending` commands survive restart; their current rights
and domain preconditions are checked at execution.

Analytics read frozen `LiftDayResult`/`LiftDaySeat` rows and the append-only
`PaymentEntry` ledger independently. Money is grouped by service day, not the
receipt's posting date. Cash/transfer splits replay method corrections. The
current day is excluded from historical graphs; live operations have their own
screen. Expected amounts use the historical price and occupied seats. The
expected-minus-reported difference is not a debt or profit calculation, and
cancellation reports remain estimates, not refund transactions. Reconstructed
history is labelled; new riders mean first appearance in the available saved
history, not proof that they never rode before tracking began.

Telegram Mini App data are verified server-side with expiry and future-time
checks. Browser login uses Telegram OIDC, PKCE, state, nonce and verified RS256
ID tokens. Sessions are signed HttpOnly cookies. Mutations require a session CSRF
token and reject foreign browser origins. Admin rights are derived from the
server allowlist; personal read views are scoped to the authenticated user.
There is no development login bypass. `?demo=1` loads synthetic client-side
fixtures and never calls the mutation API.
