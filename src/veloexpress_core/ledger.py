"""Who owes what on a service day: the one computation every surface reads.

The group board, the rider card, the payment buttons, the admin monitor, the
web cabinet and the booking order screen used to work this out separately, and
they drifted: the same rider could be paid on one screen and unpaid on another.
They now all read a `DayLedger`.

The rules, in one place:

- A seat is chargeable once its lift is running: it reached the minimum live,
  or it had the minimum when booking closed. A seat on a lift still short of it
  is booked, not owed.
- A rider's chargeable seats are their own seat where they hold one, plus every
  guest they reserved, whether or not they still hold a seat themselves.
- Money covers seats in order: what the deadline froze first, then running
  lifts by time, then lifts still filling. Money beyond what is owed now is
  prepaid while there are lifts still filling, unallocated after that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from veloexpress_bot.payments.coverage import CoverageTarget, reconcile_coverage
from veloexpress_bot.polls.defaults import MINIMUM_RIDERS
from veloexpress_bot.polls.liftsignals import lift_minutes
from veloexpress_core.daybook import DayBookings

SeatState = Literal["paid", "partial", "unpaid", "pending", "waitlist", "none"]


@dataclass(frozen=True)
class SeatMoney:
    """One rider's seats on one lift, and how many of them are paid for."""

    lift_time: str
    own: bool
    guests: int
    running: bool
    covered: int
    # Voted for the lift but holds no seat on it.
    waitlisted: bool = False

    @property
    def seats(self) -> int:
        return int(self.own) + self.guests

    @property
    def due(self) -> int:
        """Unpaid seats on this lift."""
        return max(self.seats - self.covered, 0)

    @property
    def state(self) -> SeatState:
        if not self.seats:
            return "waitlist" if self.waitlisted else "none"
        if self.covered >= self.seats:
            return "paid"
        if not self.running:
            # Nothing is owed before a lift reaches the minimum.
            return "pending"
        return "partial" if self.covered else "unpaid"


@dataclass(frozen=True)
class RiderMoney:
    telegram_user_id: int
    price_gel: int
    seats: tuple[SeatMoney, ...]
    paid_gel: int = 0
    method: str | None = None
    cash_gel: int = 0
    verified: bool = False

    def seat(self, lift_time: str) -> SeatMoney | None:
        return next((seat for seat in self.seats if seat.lift_time == lift_time), None)

    @property
    def running_seats(self) -> int:
        return sum(seat.seats for seat in self.seats if seat.running)

    @property
    def pending_seats(self) -> int:
        return sum(seat.seats for seat in self.seats if not seat.running)

    @property
    def due_now_gel(self) -> int:
        return self.running_seats * self.price_gel

    @property
    def due_all_gel(self) -> int:
        return (self.running_seats + self.pending_seats) * self.price_gel

    @property
    def outstanding_gel(self) -> int:
        return max(self.due_now_gel - self.paid_gel, 0)

    @property
    def credit_gel(self) -> int:
        return max(self.paid_gel - self.due_now_gel, 0)

    @property
    def prepaid_gel(self) -> int:
        """Credit that already covers a lift still filling."""
        return min(self.credit_gel, self.pending_seats * self.price_gel)

    @property
    def unallocated_gel(self) -> int:
        """Credit above every current booking. Not promised back: Misho decides."""
        return self.credit_gel - self.prepaid_gel

    @property
    def pays_cash(self) -> bool:
        """Some of the money is cash Misho still collects on site."""
        return self.cash_gel > 0 and not self.verified

    @property
    def holds_seat(self) -> bool:
        return any(seat.seats for seat in self.seats)


@dataclass(frozen=True)
class DayLedger:
    price_gel: int
    riders: dict[int, RiderMoney]
    running_lift_times: tuple[str, ...]
    # Paid seats per lift, offline bookings included: Misho settles those himself.
    covered_by_lift: dict[str, int]

    def rider(self, telegram_user_id: int) -> RiderMoney | None:
        return self.riders.get(telegram_user_id)

    def funded(self, lift_time: str) -> bool:
        return self.covered_by_lift.get(lift_time, 0) >= MINIMUM_RIDERS

    @property
    def owed_gel(self) -> int:
        """What riders owe for running lifts. Offline seats are not billed here."""
        return sum(rider.due_now_gel for rider in self.riders.values())

    @property
    def reported_gel(self) -> int:
        return sum(rider.paid_gel for rider in self.riders.values())

    @property
    def outstanding_gel(self) -> int:
        return sum(rider.outstanding_gel for rider in self.riders.values())

    @property
    def outstanding(self) -> tuple[RiderMoney, ...]:
        return tuple(rider for rider in self.riders.values() if rider.outstanding_gel > 0)


def day_ledger(day: DayBookings) -> DayLedger:
    running = set(day.running_lift_times)
    lifts = set(day.seats_by_lift) | running
    rider_ids = (
        set(day.booked_lift_times_by_user)
        | {
            user_id
            for (user_id, lift_time), count in day.guests_by_user_lift.items()
            if count > 0 and lift_time in lifts
        }
        | set(day.paid_amount_by_user)
    )
    riders: dict[int, RiderMoney] = {}
    covered_by_lift: dict[str, int] = {
        lift_time: day.manual_by_lift.get(lift_time, 0) for lift_time in lifts
    }
    for user_id in rider_ids:
        booked = set(day.booked_lift_times_by_user.get(user_id, ()))
        hosted = {
            lift_time
            for (host, lift_time), count in day.guests_by_user_lift.items()
            if host == user_id and count > 0 and lift_time in lifts
        }
        ordered = sorted(
            (booked | hosted) & lifts,
            key=lambda lift_time: (lift_time not in running, lift_minutes(lift_time)),
        )
        drafts = [
            SeatMoney(
                lift_time=lift_time,
                own=user_id in day.seat_holders_by_lift.get(lift_time, ()),
                guests=day.guests_by_user_lift.get((user_id, lift_time), 0),
                running=lift_time in running,
                covered=0,
                waitlisted=user_id in day.waitlist_by_lift.get(lift_time, ())
                and user_id not in day.seat_holders_by_lift.get(lift_time, ()),
            )
            for lift_time in ordered
        ]
        paid = day.paid_amount_by_user.get(user_id, 0)
        locked = {
            seat.lift_time: min(
                day.locked_covered_by_user_lift.get((user_id, seat.lift_time), 0), seat.seats
            )
            for seat in drafts
            if seat.running
        }
        coverage = reconcile_coverage(
            received_gel=max(paid - sum(locked.values()) * day.price_gel, 0),
            price_gel=day.price_gel,
            targets=tuple(
                CoverageTarget(seat.lift_time, seat.seats - locked.get(seat.lift_time, 0))
                for seat in drafts
            ),
        )
        seats = tuple(
            SeatMoney(
                lift_time=seat.lift_time,
                own=seat.own,
                guests=seat.guests,
                running=seat.running,
                covered=locked.get(seat.lift_time, 0) + target.covered_seats,
                waitlisted=seat.waitlisted,
            )
            for seat, target in zip(drafts, coverage.targets, strict=True)
        )
        for seat in seats:
            covered_by_lift[seat.lift_time] = covered_by_lift.get(seat.lift_time, 0) + seat.covered
        method = day.payment_method_by_user.get(user_id)
        riders[user_id] = RiderMoney(
            telegram_user_id=user_id,
            price_gel=day.price_gel,
            seats=seats,
            paid_gel=paid,
            method=method,
            cash_gel=day.cash_amount_by_user.get(user_id, 0),
            verified=user_id in day.verified_user_ids,
        )
    return DayLedger(
        price_gel=day.price_gel,
        riders=riders,
        running_lift_times=tuple(sorted(running, key=lift_minutes)),
        covered_by_lift=covered_by_lift,
    )
