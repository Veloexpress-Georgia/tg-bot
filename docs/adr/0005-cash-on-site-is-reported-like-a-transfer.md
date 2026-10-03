# Cash on site is reported like a transfer

Supersedes [0004](0004-cash-on-site-is-an-intention.md).

The rider's cash button means “I will pay Misho in cash on site”. Record it in the
day ledger exactly like a transfer report: the `PaymentClaim` projection and an
immutable `PaymentEntry` with method `cash`. It counts toward paid seats, funded
lifts, the deadline roster, payment reminders and income statistics. A transfer
report is not verified either, so a separate intention only added a second state,
an admin recording step and special cases on every screen without making the
numbers more reliable. A rider who no longer means to pay leaves the poll; the
seat goes, and so does what they owe for it.

Refunds are the one difference. Cash is handed over on site, so a cancelled lift
or day never refunds it. Released seats use up a rider's cash part first, and only
money transferred in advance appears in refund estimates.

Admins do not record received money; the cabinet shows who reported, by which
method, and who still owes. Choosing the other method after reporting corrects the
method without moving money, and `Undo` follows the existing reversal rules.

Choices stored under 0004 are converted once. On each tick, before the deadline
roster is captured, the scheduler turns every remaining `CashPromise` into a cash
report for the seats the rider holds — riders already covered or holding no seat
keep their state — and deletes it. Promises for finished or cancelled days are
dropped. Nothing writes to `cash_promise` any more; the table can be dropped once
it is empty in every environment.
