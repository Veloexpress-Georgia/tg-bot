"""Cancellation accounting, inside the caller's payment-day transaction.

A cancellation reverses the money a rider's cancelled seats had taken, receipt by
receipt and cash first. Each reversal against a transfer is owed back as a refund
obligation; cash was never handed over, so it only stops being expected. The
claim projection is lowered in the same transaction, so the board, coverage and
"already settled" agree with the ledger.
"""

import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.db.models import (
    CancelledLift,
    PaymentClaim,
    PaymentEntry,
    RefundObligation,
    RefundReport,
    ServiceDayTerms,
)

if TYPE_CHECKING:
    from veloexpress_bot.config import Settings
    from veloexpress_core.daybook import DayBookings

logger = logging.getLogger(__name__)

CASH = "cash"
# Money actually in: receipts net of reversals (re-credits are positive
# reversals). Refund payouts are reported beside it, never subtracted again.
NET_RECEIVED_KINDS = ("received", "reversal")


def net_received_filter():
    return PaymentEntry.kind.in_(NET_RECEIVED_KINDS)


def _stamp(moment: datetime) -> str:
    """The same text for a timestamp whether the driver returns it naive or aware."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat()


def as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def lift_prefix(cancelled: CancelledLift) -> str:
    # The creation time keeps a re-cancel after a restore a new event, even where
    # the database hands the deleted row's id out again.
    return f"cancel-lift:{cancelled.id}:{_stamp(cancelled.created_at)}:"


def seats_held(day: DayBookings, user_id: int, lift_time: str) -> int:
    return int(user_id in day.seat_holders_by_lift.get(lift_time, ())) + (
        day.guests_by_user_lift.get((user_id, lift_time), 0)
    )


def released_by_lift(
    day: DayBookings, lift_time: str, paid_by_user: Mapping[int, int]
) -> dict[int, int]:
    """GEL a lift's cancellation frees, per rider, from what they hold on it.

    Only the rider's own seat and guests on that lift count. The money they paid
    keeps covering the seats they still hold on the day's other lifts first, so a
    rider who owed for more than they paid gets nothing back, and a rider who was
    not on the lift (or dropped it themselves) is untouched.
    """
    released: dict[int, int] = {}
    for user_id, paid in paid_by_user.items():
        here = seats_held(day, user_id, lift_time)
        if not here or paid <= 0:
            continue
        elsewhere = sum(
            seats_held(day, user_id, other)
            for other in day.booked_lift_times_by_user.get(user_id, ())
            if other != lift_time
        )
        amount = min(here * day.price_gel, max(paid - elsewhere * day.price_gel, 0))
        if amount > 0:
            released[user_id] = amount
    return released


async def day_in_session(
    session: AsyncSession, *, settings: Settings, service_date: date
) -> DayBookings | None:
    from veloexpress_core.daybook import load_day_bookings

    if settings.telegram_target_chat_id is None:
        return None

    @asynccontextmanager
    async def current_session():
        yield session

    days = await load_day_bookings(
        settings=settings,
        session_factory=current_session,
        chat_id=settings.telegram_target_chat_id,
        today=service_date,
        until=service_date,
    )
    return next((day for day in days if day.service_date == service_date), None)


async def capture_lift_release(
    session: AsyncSession, *, settings: Settings, service_date: date, lift_time: str
) -> dict[str, int]:
    """Call under the day lock, before the lift is marked cancelled."""
    day = await day_in_session(session, settings=settings, service_date=service_date)
    if day is None:
        # Without the day nobody can say who held the lift. Releasing every
        # payment would refund riders who are still riding; record nothing.
        logger.warning(
            "lift_cancel_release_skipped service_date=%s lift_time=%s",
            service_date.isoformat(),
            lift_time,
        )
        return {}
    released = released_by_lift(day, lift_time, day.paid_amount_by_user)
    return {str(user_id): amount for user_id, amount in released.items()}


def _scope(model, settings: Settings, service_date: date):
    return (
        model.environment == settings.app_env,
        model.chat_id == settings.telegram_target_chat_id,
        model.thread_id == settings.telegram_target_thread_id,
        model.service_date == service_date,
    )


async def _claims(
    session: AsyncSession, settings: Settings, service_date: date
) -> dict[int, PaymentClaim]:
    rows = await session.scalars(
        select(PaymentClaim).where(*_scope(PaymentClaim, settings, service_date))
    )
    return {claim.telegram_user_id: claim for claim in rows}


async def _entries(
    session: AsyncSession, settings: Settings, service_date: date
) -> list[PaymentEntry]:
    return list(
        await session.scalars(
            select(PaymentEntry)
            .where(*_scope(PaymentEntry, settings, service_date))
            .order_by(PaymentEntry.id)
        )
    )


async def _seat_price(session: AsyncSession, claim: PaymentClaim) -> int:
    price = await session.scalar(
        select(ServiceDayTerms.price_gel).where(
            ServiceDayTerms.environment == claim.environment,
            ServiceDayTerms.chat_id == claim.chat_id,
            ServiceDayTerms.thread_id == claim.thread_id,
            ServiceDayTerms.service_date == claim.service_date,
        )
    )
    if price:
        return price
    return claim.amount_gel // claim.seats if claim.seats else 0


def _set_claim_money(claim: PaymentClaim, *, amount: int, cash: int, price: int) -> None:
    claim.amount_gel = max(amount, 0)
    claim.cash_amount_gel = min(max(cash, 0), claim.amount_gel)
    claim.seats = claim.amount_gel // price if price else 0
    if claim.amount_gel and claim.cash_amount_gel == claim.amount_gel:
        claim.method = CASH
    elif claim.cash_amount_gel:
        claim.method = "mixed"
    else:
        claim.method = "transfer"
    claim.updated_at = datetime.now(UTC)


async def _reverse(
    session: AsyncSession,
    claim: PaymentClaim,
    amount: int,
    *,
    entries: list[PaymentEntry],
    prefix: str,
    persist_claim: bool = True,
) -> None:
    """Reverse `amount` GEL of the rider's receipts, cash first."""
    own = [e for e in entries if e.telegram_user_id == claim.telegram_user_id]
    receipts = sorted(
        (e for e in own if e.kind == "received"), key=lambda e: (e.method != CASH, e.id)
    )
    cash_left = min(claim.cash_amount_gel or 0, claim.amount_gel or 0)
    cash_used = 0
    total = 0
    count = sum(1 for e in own if (e.reference_key or "").startswith(prefix))
    for receipt in receipts:
        if amount <= 0:
            break
        available = receipt.amount_gel + sum(
            e.amount_gel for e in own if e.kind == "reversal" and e.reversed_entry_id == receipt.id
        )
        take = min(amount, available)
        if take <= 0:
            continue
        reversal = PaymentEntry(
            environment=claim.environment,
            chat_id=claim.chat_id,
            thread_id=claim.thread_id,
            service_date=claim.service_date,
            telegram_user_id=claim.telegram_user_id,
            amount_gel=-take,
            method=receipt.method,
            kind="reversal",
            reversed_entry_id=receipt.id,
            reference_key=f"{prefix}{receipt.id}:{count}",
        )
        count += 1
        session.add(reversal)
        await session.flush()
        entries.append(reversal)
        own.append(reversal)
        cash_part = min(take, cash_left - cash_used)
        cash_used += cash_part
        refund = take - cash_part
        if refund:
            session.add(
                RefundObligation(
                    environment=claim.environment,
                    chat_id=claim.chat_id,
                    thread_id=claim.thread_id,
                    service_date=claim.service_date,
                    telegram_user_id=claim.telegram_user_id,
                    label=claim.full_name or claim.username or str(claim.telegram_user_id),
                    amount_gel=refund,
                    method="transfer",
                    reversal_entry_id=reversal.id,
                )
            )
        amount -= take
        total += take
    if total and persist_claim:
        _set_claim_money(
            claim,
            amount=claim.amount_gel - total,
            cash=claim.cash_amount_gel - cash_used,
            price=await _seat_price(session, claim),
        )


async def reverse_lift_cancellation(
    session: AsyncSession, *, settings: Settings, cancelled: CancelledLift
) -> None:
    """Reverse what the cancellation recorded as released. Safe to repeat."""
    if cancelled.released is None:
        # Cancelled before refunds were tracked: the old estimate report already
        # told the admin what to hand back. Reversing now would refund it twice.
        logger.info(
            "lift_cancel_legacy_skipped service_date=%s lift_time=%s",
            cancelled.service_date.isoformat(),
            cancelled.lift_time,
        )
        return
    claims = await _claims(session, settings, cancelled.service_date)
    entries = await _entries(session, settings, cancelled.service_date)
    prefix = lift_prefix(cancelled)
    for key, target in cancelled.released.items():
        claim = claims.get(int(key))
        if claim is None:
            continue
        already = -sum(
            e.amount_gel
            for e in entries
            if e.telegram_user_id == claim.telegram_user_id
            and e.kind == "reversal"
            and (e.reference_key or "").startswith(prefix)
        )
        if target - already > 0:
            await _reverse(session, claim, target - already, entries=entries, prefix=prefix)


async def restore_lift_cancellation(
    session: AsyncSession, *, settings: Settings, cancelled: CancelledLift
) -> None:
    """Undo a lift cancellation's money before its CancelledLift row is deleted.

    An unpaid refund is voided and the reversed money credited back, so the
    rider's payment covers the restored lift again. A refund already paid out
    stays paid: the rider owes for the restored lift like for any new booking.
    """
    if cancelled.released is None:
        return
    prefix = lift_prefix(cancelled)
    entries = await _entries(session, settings, cancelled.service_date)
    claims = await _claims(session, settings, cancelled.service_date)
    keys = {e.reference_key for e in entries}
    for reversal in [
        e for e in entries if e.kind == "reversal" and (e.reference_key or "").startswith(prefix)
    ]:
        if f"restore:{reversal.id}" in keys or f"undo:{reversal.reversed_entry_id}" in keys:
            # Credited back already, or the rider withdrew the receipt itself.
            continue
        claim = claims.get(reversal.telegram_user_id)
        receipt = next((e for e in entries if e.id == reversal.reversed_entry_id), None)
        if (
            claim is None
            or receipt is None
            or as_utc(reversal.recorded_at) < as_utc(claim.claimed_at)
        ):
            continue
        obligation = await session.scalar(
            select(RefundObligation).where(RefundObligation.reversal_entry_id == reversal.id)
        )
        if obligation is not None and obligation.paid_at is not None:
            continue
        corrections = [
            e
            for e in entries
            if e.telegram_user_id == reversal.telegram_user_id
            and e.kind == "method_change"
            and as_utc(e.recorded_at) <= as_utc(reversal.recorded_at)
        ]
        cash_release = reversal.method == CASH or bool(
            corrections and corrections[-1].method == CASH
        )
        if obligation is None and not cash_release:
            # A transfer is restorable only while its unpaid obligation exists.
            continue
        refund = obligation.amount_gel if obligation is not None else 0
        if obligation is not None:
            await session.delete(obligation)
        amount = -reversal.amount_gel
        session.add(
            PaymentEntry(
                environment=reversal.environment,
                chat_id=reversal.chat_id,
                thread_id=reversal.thread_id,
                service_date=reversal.service_date,
                telegram_user_id=reversal.telegram_user_id,
                amount_gel=amount,
                method=reversal.method,
                kind="reversal",
                reversed_entry_id=reversal.reversed_entry_id,
                reference_key=f"restore:{reversal.id}",
            )
        )
        _set_claim_money(
            claim,
            amount=claim.amount_gel + amount,
            cash=claim.cash_amount_gel + amount - refund,
            price=await _seat_price(session, claim),
        )
    await session.flush()


async def reverse_day_cancellation(
    session: AsyncSession, *, settings: Settings, service_date: date
) -> bool:
    """Reverse everything still received for the day. False when skipped as legacy."""
    legacy = await session.scalar(
        select(CancelledLift.id)
        .where(*_scope(CancelledLift, settings, service_date))
        .where(CancelledLift.released.is_(None))
        .limit(1)
    )
    if legacy is not None:
        # A lift on this day was cancelled before refunds were tracked and settled
        # from the cumulative estimate. Which part of each payment it covered was
        # never recorded, so the whole day stays on the old estimate.
        logger.warning("day_cancel_legacy_refunds_skipped service_date=%s", service_date)
        # Save the fallback before booking cleanup removes the legacy flag.
        # Claims already exclude tracked cancellations; those remain payable as
        # obligations and must not also appear in this read-only estimate.
        from veloexpress_bot.payments.render import RefundRow, render_cancellation_report
        from veloexpress_core.daybook import rider_label

        claims = await _claims(session, settings, service_date)
        # Tracked lift refunds on this day are payable in the cabinet and left
        # out of this estimate, so the admin would see fewer people here.
        tracked_refunds = await session.scalar(
            select(RefundObligation.id)
            .where(*_scope(RefundObligation, settings, service_date))
            .limit(1)
        )
        report = render_cancellation_report(
            service_date=service_date,
            cancelled_lift_time=None,
            tracked=False,
            earlier_refunds_elsewhere=tracked_refunds is not None,
            rows=tuple(
                RefundRow(
                    label=rider_label(claim.username, claim.full_name),
                    telegram_user_id=claim.telegram_user_id,
                    seats=claim.seats,
                    amount_gel=claim.amount_gel - claim.cash_amount_gel,
                    remaining_lift_times=(),
                    refund_gel=claim.amount_gel - claim.cash_amount_gel,
                )
                for claim in claims.values()
            ),
        )
        if report is not None:
            session.add(
                RefundReport(
                    environment=settings.app_env,
                    chat_id=settings.telegram_target_chat_id,
                    service_date=service_date,
                    lift_time=None,
                    text=report,
                    tracked=False,
                )
            )
        return False
    entries = await _entries(session, settings, service_date)
    claims = await _claims(session, settings, service_date)
    for user_id in {e.telegram_user_id for e in entries}:
        own = [e for e in entries if e.telegram_user_id == user_id]
        net = sum(e.amount_gel for e in own if e.kind in NET_RECEIVED_KINDS)
        if net <= 0:
            continue
        claim = claims.get(user_id)
        persist = claim is not None
        if claim is None:
            receipts = [e for e in own if e.kind == "received"]
            if not receipts:
                continue
            cash = sum(
                e.amount_gel for e in own if e.kind in NET_RECEIVED_KINDS and e.method == CASH
            )
            # A lost projection must not leave actual receipts in revenue. This
            # stand-in is never persisted.
            claim = PaymentClaim(
                environment=receipts[0].environment,
                chat_id=receipts[0].chat_id,
                thread_id=receipts[0].thread_id,
                service_date=service_date,
                telegram_user_id=user_id,
                full_name=f"Rider {user_id}",
                seats=0,
                amount_gel=net,
                cash_amount_gel=max(cash, 0),
            )
        await _reverse(
            session,
            claim,
            net,
            entries=entries,
            prefix=f"cancel-day:{service_date.isoformat()}:",
            persist_claim=persist,
        )
    return True
