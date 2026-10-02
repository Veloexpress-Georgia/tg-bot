# Cash on site is an intention, not received money

The rider's cash button means “I will pay Misho on site on the lift day”. Store
that choice in a day-scoped `CashPromise`, separate from `PaymentClaim` and the
immutable `PaymentEntry` ledger. Choosing or cancelling cash never records income,
paid seats or a refund. An admin records the amount actually collected, through
the existing authenticated, idempotent payment action.

Preserve posted callback and deep-link wire formats. The shared core interprets
cash from rider actions as an intention; admin payment actions still mean received
cash. The Mini App and bot distinguish these two states. A cash choice prevents a
topic message from inferring a transfer for another day of the rider's weekend.

For planning reminders, confirmed seats backed by cash intentions are separate
from already received payments. Do not chase those intentions as overdue bank
transfers. Financial statistics and funded-lift status use received money only.
Cash collected after the deadline contributes to live coverage in addition to
money already spent at the deadline, without rewriting the deadline roster.

The migration adds an empty intention table. Existing historical money and frozen
prices remain intact. Cancellation clears intentions along with the retired day
projection; interrupted cleanup checks the latest booking cycle, protecting a
freshly posted day from an earlier cancellation.
