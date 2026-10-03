"""Explicit admin corrections; native votes and payment history stay intact."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.db.locking import transaction_lock
from veloexpress_bot.db.models import (
    BookingOrderChange,
    DeadlineRoster,
    GuestSeat,
    LiftDayResult,
    LiftSeatState,
    PollOptionSnapshot,
    PollVote,
    TelegramOutbox,
)
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS
from veloexpress_bot.polls.seating import SeatCandidate, allocate_seats, queue_rank

if TYPE_CHECKING:
    from veloexpress_core.lifts import PollPostingService


def candidates(votes: list[PollVote], option_id: int) -> list[SeatCandidate]:
    from veloexpress_core.lifts import decode_option_ids, vote_booked_at

    return [
        SeatCandidate(
            v.telegram_user_id,
            (v.username and f"@{v.username}") or v.full_name,
            vote_booked_at(v, option_id),
            queue_rank(v.option_queue_ranks, option_id),
        )
        for v in votes
        if option_id in decode_option_ids(v.option_ids)
    ]


def remember_retractions(
    vote: PollVote, votes: list[PollVote], option_ids: tuple[int, ...]
) -> None:
    from veloexpress_core.lifts import decode_option_ids

    ranks = json.loads(vote.option_queue_ranks or "{}")
    previous = json.loads(vote.option_previous_orders or "{}")
    for option_id in set(decode_option_ids(vote.option_ids)) - set(option_ids):
        ordered = allocate_seats(candidates(votes, option_id), capacity=len(votes)).holders
        previous[str(option_id)] = [r.telegram_user_id for r in ordered]
        ranks.pop(str(option_id), None)
    vote.option_queue_ranks = json.dumps(ranks)
    vote.option_previous_orders = json.dumps(previous)


@dataclass
class OrderState:
    snapshot: PollOptionSnapshot
    votes: list[PollVote]
    ordered: list[SeatCandidate]
    available: int
    deadline_closed: bool
    digest: str
    previous_positions: dict[str, int]
    previous_orders: dict[int, list[int]]
    payments: dict[int, dict[str, Any]]

    def view(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "available_seats": self.available,
            "riders": [
                {
                    "user_id": r.telegram_user_id,
                    "label": r.label,
                    "waitlisted": i >= self.available,
                    **self.payments[r.telegram_user_id],
                }
                for i, r in enumerate(self.ordered)
            ],
            "previous_positions": self.previous_positions,
            "deadline_closed": self.deadline_closed,
        }


async def load_order(
    service: PollPostingService, session: AsyncSession, *, service_date: date, lift_time: str
) -> OrderState:
    if service_date < datetime.now(UTC).astimezone(service._zone).date():
        raise ValueError("Finished service days cannot be edited")
    snapshot = await service._active_snapshot_for_lift(
        session=session, service_date=service_date, lift_time=lift_time
    )
    if snapshot is None:
        raise ValueError("This lift is no longer active")
    if lift_time in await service._cancelled_lift_times(session=session, service_date=service_date):
        raise ValueError("This lift is cancelled")
    if await session.scalar(
        select(LiftDayResult.id)
        .where(
            LiftDayResult.environment == service._settings.app_env,
            LiftDayResult.chat_id == service._settings.telegram_target_chat_id,
            LiftDayResult.thread_id == service._settings.telegram_target_thread_id,
            LiftDayResult.service_date == service_date,
        )
        .limit(1)
    ):
        raise ValueError("Finished service days cannot be edited")
    votes = list(
        (await session.scalars(select(PollVote).where(PollVote.poll_id == snapshot.poll_id))).all()
    )
    ordered = list(
        allocate_seats(candidates(votes, snapshot.option_index), capacity=len(votes)).holders
    )
    manual = await service._manual_booking_count(
        session=session, service_date=service_date, lift_time=lift_time
    )
    guests = list(
        (
            await session.scalars(
                select(GuestSeat).where(
                    GuestSeat.environment == service._settings.app_env,
                    GuestSeat.chat_id == service._settings.telegram_target_chat_id,
                    GuestSeat.thread_id == service._settings.telegram_target_thread_id,
                    GuestSeat.service_date == service_date,
                    GuestSeat.lift_time == lift_time,
                )
            )
        ).all()
    )
    capacity = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}[lift_time]
    available = max(capacity - manual - sum(g.count for g in guests), 0)
    deadline_closed = bool(
        await session.scalar(
            select(DeadlineRoster.id)
            .where(
                DeadlineRoster.environment == service._settings.app_env,
                DeadlineRoster.chat_id == service._settings.telegram_target_chat_id,
                DeadlineRoster.service_date == service_date,
            )
            .limit(1)
        )
    )
    previous_positions = {}
    previous_orders = {}
    for vote in votes:
        previous = json.loads(vote.option_previous_orders or "{}").get(
            str(snapshot.option_index), []
        )
        if vote.telegram_user_id in previous and any(
            r.telegram_user_id == vote.telegram_user_id for r in ordered
        ):
            previous_positions[str(vote.telegram_user_id)] = (
                previous.index(vote.telegram_user_id) + 1
            )
            previous_orders[vote.telegram_user_id] = previous
    state = {
        "snapshot": snapshot.id,
        "manual": manual,
        "guests": sorted((g.host_user_id, g.count) for g in guests),
        "bookings": [(r.telegram_user_id, r.booked_at.isoformat(), r.queue_rank) for r in ordered],
        "previous_positions": previous_positions,
        "deadline_closed": deadline_closed,
    }
    detail = await service.lift_detail(service_date=service_date, lift_time=lift_time)
    if detail is None:
        raise ValueError("This lift is no longer active")
    payments = {
        r.telegram_user_id: {
            "paid": r.paid,
            "paid_gel": r.paid_gel,
            "cash_on_site": r.cash_on_site,
        }
        for r in detail[1]
    }
    state["payments"] = payments
    digest = sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    return OrderState(
        snapshot,
        votes,
        ordered,
        available,
        deadline_closed,
        digest,
        previous_positions,
        previous_orders,
        payments,
    )


def proposal(
    state: OrderState,
    *,
    ordered_user_ids: list[int] | None = None,
    restore_user_id: int | None = None,
) -> dict[str, Any]:
    current = [r.telegram_user_id for r in state.ordered]
    if (ordered_user_ids is None) == (restore_user_id is None):
        raise ValueError("Choose a new order or one rider to restore")
    if restore_user_id is not None:
        position = state.previous_positions.get(str(restore_user_id))
        if position is None:
            raise ValueError("No previous position is recorded for this rider")
        ordered_user_ids = [uid for uid in current if uid != restore_user_id]
        previous = state.previous_orders[restore_user_id]
        following = next((uid for uid in previous[position:] if uid in ordered_user_ids), None)
        preceding = next(
            (uid for uid in reversed(previous[: position - 1]) if uid in ordered_user_ids), None
        )
        insertion = (
            ordered_user_ids.index(following)
            if following is not None
            else ordered_user_ids.index(preceding) + 1
            if preceding is not None
            else min(position - 1, len(ordered_user_ids))
        )
        ordered_user_ids.insert(insertion, restore_user_id)
    assert ordered_user_ids is not None
    if len(ordered_user_ids) != len(current) or set(ordered_user_ids) != set(current):
        raise ValueError("Bookings changed. Reopen the booking order")
    holders_before = set(current[: state.available])
    holders_after = set(ordered_user_ids[: state.available])
    promoted = [uid for uid in ordered_user_ids if uid in holders_after - holders_before]
    demoted = [uid for uid in current if uid in holders_before - holders_after]
    paid_demoted = [uid for uid in demoted if state.payments[uid]["paid"]]
    labels = {r.telegram_user_id: r.label for r in state.ordered}
    lines = ["Confirm booking order", ""]
    if paid_demoted:
        lines.append(
            "⚠️ Already-paid bookings will move to the waitlist: "
            + ", ".join(labels[uid] for uid in paid_demoted)
        )
    for title, ids in (("Will get a seat", promoted), ("Will move to the waitlist", demoted)):
        if ids:
            lines.append(f"{title}: {', '.join(labels[uid] for uid in ids)}")
    if not promoted and not demoted:
        lines.append("Seat holders stay the same; the order changes for future vacancies.")
    if state.deadline_closed:
        lines.extend(
            [
                "",
                "The deadline roster stays intact. "
                "This does not transfer payments or promise refunds.",
            ]
        )
    lines.extend(
        [
            "",
            "New order:",
            *[
                f"{i + 1}. {labels[uid]} · {'seat' if i < state.available else 'waitlist'}"
                f" · {payment_label(state.payments[uid])}"
                for i, uid in enumerate(ordered_user_ids)
            ],
        ]
    )
    return {
        **state.view(),
        "ordered_user_ids": ordered_user_ids,
        "promoted": promoted,
        "demoted": demoted,
        "paid_demoted": paid_demoted,
        "details": "\n".join(lines),
    }


def payment_label(payment: dict[str, Any]) -> str:
    if payment["paid"]:
        return "payment reported"
    amount = payment["paid_gel"]
    note = f" · {amount} GEL reported for the day" if amount else ""
    if payment["cash_on_site"]:
        return f"cash on site promised{note}"
    return f"payment not reported{note}"


async def view(
    service: PollPostingService, *, service_date: date, lift_time: str
) -> dict[str, Any]:
    async with service._session_factory() as session:
        await lock_day(service, session, service_date)
        return (
            await load_order(service, session, service_date=service_date, lift_time=lift_time)
        ).view()


async def preview(
    service: PollPostingService,
    *,
    service_date: date,
    lift_time: str,
    ordered_user_ids: list[int] | None = None,
    restore_user_id: int | None = None,
) -> dict[str, Any]:
    async with service._session_factory() as session:
        await lock_day(service, session, service_date)
        state = await load_order(service, session, service_date=service_date, lift_time=lift_time)
        return proposal(state, ordered_user_ids=ordered_user_ids, restore_user_id=restore_user_id)


async def lock_day(service: PollPostingService, session: AsyncSession, service_date: date) -> None:
    await transaction_lock(
        session,
        f"payment-day:{service._settings.app_env}:{service._settings.telegram_target_chat_id}:{service_date}",
    )


async def save(
    service: PollPostingService,
    *,
    service_date: date,
    lift_time: str,
    expected_digest: str,
    admin_user_id: int,
    ordered_user_ids: list[int] | None = None,
    restore_user_id: int | None = None,
) -> dict[str, Any]:
    if admin_user_id not in service._settings.telegram_admin_ids:
        raise PermissionError("Administrator access required")
    async with service._session_factory() as session:
        await lock_day(service, session, service_date)
        state = await load_order(service, session, service_date=service_date, lift_time=lift_time)
        if state.digest != expected_digest:
            raise ValueError("Data changed. Reopen the booking order and confirm again")
        result = proposal(state, ordered_user_ids=ordered_user_ids, restore_user_id=restore_user_id)
        ids = result["ordered_user_ids"]
        current = [r.telegram_user_id for r in state.ordered]
        if ids == current:
            return {"message": "Booking order is unchanged"}
        by_user = {v.telegram_user_id: v for v in state.votes}
        for rank, uid in enumerate(ids):
            vote = by_user[uid]
            ranks = json.loads(vote.option_queue_ranks or "{}")
            ranks[str(state.snapshot.option_index)] = rank
            vote.option_queue_ranks = json.dumps(ranks)
        audit = BookingOrderChange(
            snapshot_id=state.snapshot.id,
            environment=service._settings.app_env,
            chat_id=service._settings.telegram_target_chat_id,
            thread_id=service._settings.telegram_target_thread_id,
            service_date=service_date,
            lift_time=lift_time,
            poll_id=state.snapshot.poll_id,
            admin_user_id=admin_user_id,
            action="restore" if restore_user_id is not None else "reorder",
            before_order=json.dumps(current),
            after_order=json.dumps(ids),
            promoted_ids=json.dumps(result["promoted"]),
            demoted_ids=json.dumps(result["demoted"]),
        )
        session.add(audit)
        await session.flush()
        # Record the final distribution alongside the correction. The background
        # promotion scanner must not announce a second, intermediate change.
        seat_state = await session.scalar(
            select(LiftSeatState).where(
                LiftSeatState.environment == service._settings.app_env,
                LiftSeatState.chat_id == service._settings.telegram_target_chat_id,
                LiftSeatState.thread_id == service._settings.telegram_target_thread_id,
                LiftSeatState.service_date == service_date,
                LiftSeatState.lift_time == lift_time,
            )
        )
        if seat_state is None:
            seat_state = LiftSeatState(
                environment=service._settings.app_env,
                chat_id=service._settings.telegram_target_chat_id,
                thread_id=service._settings.telegram_target_thread_id,
                service_date=service_date,
                lift_time=lift_time,
            )
            session.add(seat_state)
        seat_state.holder_ids = ",".join(map(str, ids[: state.available]))
        seat_state.waitlist_ids = ",".join(map(str, ids[state.available :]))
        seat_state.updated_at = datetime.now(UTC)
        labels = {r.telegram_user_id: r.label for r in state.ordered}
        lines = [f"🎟 Booking order updated · {lift_time} · {service_date:%d %b}"]
        for title, changed in (
            ("Now holds a seat", result["promoted"]),
            ("Now on the waitlist", result["demoted"]),
        ):
            if changed:
                mentions = " ".join(
                    f'<a href="tg://user?id={uid}">{html.escape(labels[uid])}</a>'
                    for uid in changed
                )
                lines.append(f"{title}: {mentions}")
        if result["promoted"] or result["demoted"]:
            session.add(
                TelegramOutbox(
                    environment=service._settings.app_env,
                    operation_key=f"booking-order:{audit.id}",
                    chat_id=service._settings.telegram_target_chat_id,
                    thread_id=service._settings.telegram_target_thread_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                    status="pending",
                    attempts=0,
                )
            )
        await session.commit()
    # A display/network failure does not undo a successfully committed correction.
    await service._outbox.deliver_pending()
    await service._refresh_availability(state.snapshot.poll_id)
    await service._refresh_booking_monitors()
    return {
        "message": "Booking order updated",
        "promoted": result["promoted"],
        "demoted": result["demoted"],
    }
