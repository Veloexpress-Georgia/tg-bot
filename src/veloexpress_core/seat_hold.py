"""Riders who must wait for a seat offer, as allocation needs to read them.

A waitlisted rider does not move into a freed seat on their own; they are held
until they accept an offer. The seat-offer scan in payments keeps that list, the
vote handler keeps it honest between scans, and everything that allocates seats
reads it from here, so the board, the bill and the admin monitor agree on who is
still waiting.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import LiftSeatState


def decode_ids(value: str | None) -> frozenset[int]:
    return frozenset(int(part) for part in (value or "").split(",") if part)


async def held_waitlists(
    session: AsyncSession,
    *,
    settings: Settings,
    service_dates: Iterable[date],
) -> dict[tuple[date, str], frozenset[int]]:
    """Frozen waitlist riders per (date, lift time); lifts not yet frozen are absent."""
    dates = tuple(service_dates)
    if not dates or settings.telegram_target_chat_id is None:
        return {}
    rows = (
        await session.scalars(
            select(LiftSeatState)
            .where(LiftSeatState.environment == settings.app_env)
            .where(LiftSeatState.chat_id == settings.telegram_target_chat_id)
            .where(LiftSeatState.thread_id == settings.telegram_target_thread_id)
            .where(LiftSeatState.service_date.in_(dates))
            .where(LiftSeatState.held_waitlist_ids.is_not(None))
        )
    ).all()
    return {(row.service_date, row.lift_time): decode_ids(row.held_waitlist_ids) for row in rows}


async def held_waitlist(
    session: AsyncSession,
    *,
    settings: Settings,
    service_date: date,
    lift_time: str,
) -> frozenset[int]:
    held = await held_waitlists(session, settings=settings, service_dates=(service_date,))
    return held.get((service_date, lift_time), frozenset())


def encode_ids(ids: Iterable[int]) -> str:
    return ",".join(str(user_id) for user_id in ids)


async def apply_vote_to_waitlists(
    session: AsyncSession,
    *,
    settings: Settings,
    service_date: date,
    telegram_user_id: int,
    joined: Iterable[str],
    left: Iterable[str],
    now: datetime,
) -> None:
    """Keep the held lists right at the moment a vote changes, not a tick later.

    Leaving a lift forgets the rider there, so voting again is a fresh booking.
    Joining a lift where riders are waiting for an offer queues behind them:
    otherwise a newcomer would take the seat that is being offered.
    """
    joined_times = set(joined)
    left_times = set(left)
    if not joined_times and not left_times:
        return
    rows = (
        await session.scalars(
            select(LiftSeatState)
            .where(LiftSeatState.environment == settings.app_env)
            .where(LiftSeatState.chat_id == settings.telegram_target_chat_id)
            .where(LiftSeatState.thread_id == settings.telegram_target_thread_id)
            .where(LiftSeatState.service_date == service_date)
            .where(LiftSeatState.lift_time.in_(joined_times | left_times))
        )
    ).all()
    for row in rows:
        held = set(decode_ids(row.held_waitlist_ids))
        declined = set(decode_ids(row.declined_ids))
        before = (frozenset(held), frozenset(declined))
        if row.lift_time in left_times:
            held.discard(telegram_user_id)
            declined.discard(telegram_user_id)
        elif held - declined - {telegram_user_id}:
            held.add(telegram_user_id)
        if (frozenset(held), frozenset(declined)) == before:
            continue
        row.held_waitlist_ids = encode_ids(sorted(held))
        row.declined_ids = encode_ids(sorted(declined))
        # Tells a scan that started before this vote to leave the row alone.
        row.updated_at = now
