"""Who actually has a seat on a lift, and who is behind them.

Kept pure and separate because two callers need the same answer and must not
disagree: the public availability board names the waitlist, and the payments side
must never bill it. A second copy of this rule would eventually drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SeatCandidate:
    telegram_user_id: int
    label: str
    booked_at: datetime


@dataclass(frozen=True)
class SeatAllocation:
    holders: tuple[SeatCandidate, ...]
    waitlist: tuple[SeatCandidate, ...]


def allocate_seats(
    candidates: Iterable[SeatCandidate],
    *,
    capacity: int,
    reserved: int = 0,
) -> SeatAllocation:
    """Fill a lift: reserved seats first, then candidates in booking order.

    `reserved` covers manual bookings and guests. Those hold their seats outright —
    an admin took a manual rider personally, and a guest belongs to a rider who
    already declared them — so a poll vote cannot bump either.

    Booking order is tracked per lift. Adding or removing another lift leaves an
    existing seat alone; leaving this lift and later choosing it again is a new
    booking and moves the rider to the back here.
    """
    ordered = sorted(
        candidates, key=lambda candidate: (candidate.booked_at, candidate.telegram_user_id)
    )
    seats = max(capacity - reserved, 0)
    return SeatAllocation(holders=tuple(ordered[:seats]), waitlist=tuple(ordered[seats:]))
