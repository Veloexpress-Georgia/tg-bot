# One day ledger for every surface

Who owes what for a service day was computed in three places: the payments
service (group board, rider card, payment buttons, deadline roster), the admin
monitor and the web day page, and the lift detail behind the web lift page and
the booking order screen. They used different rules for guests, offline seats,
lifts still filling and money frozen at the deadline, so the same rider could be
paid on one screen and unpaid on another.

`veloexpress_core.daybook.load_day_bookings` is now the one loader for a day's
seats, payments and deadline roster, and `veloexpress_core.ledger.day_ledger`
the one pure function that turns it into money: per rider what is due now and
for the whole day, what is paid, outstanding, prepaid or unallocated, and per
rider and lift how many seats there are and how many are covered. Every surface
reads that ledger instead of recomputing.

The rules it encodes, taken from the group board, which was the most complete:

- A seat is owed once its lift is running: it reached the minimum live, or it
  had the minimum when booking closed. On a lift still filling a seat is
  `pending`, not unpaid.
- A rider owes for their own seat where they hold one, plus every guest they
  reserved on a running lift. This changes one case on purpose: a host who
  withdrew their own vote, or was moved to the waitlist, still owes for guests
  who hold seats. The monitor billed them before; the board did not.
- Money covers seats in order: what the deadline froze first, then running lifts
  by time, then lifts still filling. A seat with a guest is `partial` until both
  are covered, not `paid` as soon as one is.
- Offline bookings count as covered and are never owed, so a day with offline
  seats can show nothing missing.

Two related places now follow the same running rule: the availability card in
the lift topic and the lift signals (deadline reminder, departure ping) treat a
lift that had the minimum at the deadline as running after late drop-outs. The
admin monitor and the web show every current and upcoming day, not only the
next two, so an extra Friday no longer hides Sunday or its deadline reminder.
