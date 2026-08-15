"""What the booking deadline froze, and how it overrides live votes afterwards.

Until the deadline every number the bot shows is derived from live poll votes,
which is right: people re-vote, and the bill must follow. After it, the group's
rule is that a lift with five riders is happening and the money is spent — so a
late drop-out must not turn into `15 back`, and a lift that filled up must not
still read `needs 1 more`.

The override is one-directional: seats can be added after the deadline, never
removed. Adding is a rider choosing to pay more; removing would be the bot
handing back money the group agreed is no longer refundable.
"""

from __future__ import annotations

from dataclasses import dataclass

from veloexpress_bot.polls.defaults import MINIMUM_RIDERS

# Manual bookings hold seats but have no Telegram identity, so they get one row
# per lift under this id rather than being lost from the frozen head count.
MANUAL_USER_ID = 0


@dataclass(frozen=True)
class RosterSeat:
    """One rider's seats on one lift at the moment booking closed."""

    lift_time: str
    telegram_user_id: int
    label: str
    # Own seat plus guests: a rider who rode alone holds seats=1, guests=0; one
    # who brought somebody holds seats=2, guests=1.
    seats: int
    guests: int = 0


@dataclass(frozen=True)
class DeadlineSnapshot:
    rows: tuple[RosterSeat, ...]

    def lift_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for row in self.rows:
            totals[row.lift_time] = totals.get(row.lift_time, 0) + row.seats
        return totals

    def running_lift_times(self) -> tuple[str, ...]:
        """Lifts that had the minimum when booking closed, so they are happening."""
        totals = self.lift_totals()
        return tuple(sorted(time for time, seats in totals.items() if seats >= MINIMUM_RIDERS))

    def running_rows(self) -> tuple[RosterSeat, ...]:
        """Rows on lifts that are happening. The rest were never owed for."""
        running = set(self.running_lift_times())
        return tuple(row for row in self.rows if row.lift_time in running)

    def without_lifts(self, lift_times: frozenset[str]) -> DeadlineSnapshot:
        """Drop lifts cancelled after the deadline — that is the one real refund."""
        if not lift_times:
            return self
        return DeadlineSnapshot(
            rows=tuple(row for row in self.rows if row.lift_time not in lift_times)
        )
