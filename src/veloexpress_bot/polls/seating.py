"""Who actually has a seat on a lift, and who is behind them.

Kept pure and separate because two callers need the same answer and must not
disagree: the public availability board names the waitlist, and the payments side
must never bill it. A second copy of this rule would eventually drift.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

# How long before departure the waitlist stops moving. A seat that frees up later
# than this stays empty: a tag eight minutes before the van leaves reaches nobody
# who can still make it, yet would have put that rider on the bill.
WAITLIST_FREEZE_LEAD = timedelta(minutes=60)


@dataclass(frozen=True)
class SeatCandidate:
    telegram_user_id: int
    label: str
    booked_at: datetime
    queue_rank: int | None = None


@dataclass(frozen=True)
class SeatAllocation:
    holders: tuple[SeatCandidate, ...]
    waitlist: tuple[SeatCandidate, ...]


def allocate_seats(
    candidates: Iterable[SeatCandidate],
    *,
    capacity: int,
    reserved: int = 0,
    held_back: Collection[int] = frozenset(),
) -> SeatAllocation:
    """Fill a lift: reserved seats first, then candidates in booking order.

    `reserved` covers manual bookings and guests. Those hold their seats outright —
    an admin took a manual rider personally, and a guest belongs to a rider who
    already declared them — so a poll vote cannot bump either.

    `held_back` are riders whose waitlist froze close to departure. They stay on
    the waitlist, behind everyone else, even when a seat frees up in front of them.

    Admin ranks precede chronological bookings; unranked new bookings append.
    Booking order is tracked per lift. Adding or removing another lift leaves an
    existing seat alone; leaving this lift and later choosing it again is a new
    booking and moves the rider to the back here.
    """
    ordered = sorted(candidates, key=booking_order_key)
    seats = max(capacity - reserved, 0)
    eligible = [c for c in ordered if c.telegram_user_id not in held_back]
    frozen = [c for c in ordered if c.telegram_user_id in held_back]
    return SeatAllocation(
        holders=tuple(eligible[:seats]),
        waitlist=(*eligible[seats:], *frozen),
    )


def booking_order_key(candidate: SeatCandidate) -> tuple[bool, int, datetime, int]:
    return (
        candidate.queue_rank is None,
        candidate.queue_rank or 0,
        candidate.booked_at,
        candidate.telegram_user_id,
    )


def queue_rank(value: str | None, option_id: int) -> int | None:
    rank = json.loads(value or "{}").get(str(option_id))
    return rank if isinstance(rank, int) else None
