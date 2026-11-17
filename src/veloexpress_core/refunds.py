"""Cancellation accounting, inside the caller's payment-day transaction."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.db.models import PaymentClaim, PaymentEntry, RefundObligation


async def reverse_cancellation(
    session: AsyncSession, claims: list[PaymentClaim], released: dict[int, int] | None = None
) -> None:
    for claim in claims:
        entries = list(
            await session.scalars(
                select(PaymentEntry)
                .where(
                    PaymentEntry.environment == claim.environment,
                    PaymentEntry.chat_id == claim.chat_id,
                    PaymentEntry.thread_id == claim.thread_id,
                    PaymentEntry.service_date == claim.service_date,
                    PaymentEntry.telegram_user_id == claim.telegram_user_id,
                )
                .order_by(PaymentEntry.id)
            )
        )
        # Claim ids distinguish a new booking cycle on a reposted day.
        prefix = f"cancel:{claim.id}:{claim.claimed_at.isoformat()}:"
        already = -sum(
            e.amount_gel
            for e in entries
            if e.kind == "reversal" and (e.reference_key or "").startswith(prefix)
        )
        target = claim.amount_gel if released is None else released.get(claim.telegram_user_id, 0)
        delta = max(target - already, 0)
        cash_left = max((claim.cash_amount_gel or 0) - already, 0)
        for receipt in (e for e in entries if e.kind == "received"):
            available = receipt.amount_gel + sum(
                e.amount_gel
                for e in entries
                if e.kind == "reversal" and e.reversed_entry_id == receipt.id
            )
            amount = min(delta, available)
            if amount <= 0:
                continue
            reversal = PaymentEntry(
                environment=claim.environment,
                chat_id=claim.chat_id,
                thread_id=claim.thread_id,
                service_date=claim.service_date,
                telegram_user_id=claim.telegram_user_id,
                amount_gel=-amount,
                method=claim.method,
                kind="reversal",
                reversed_entry_id=receipt.id,
                reference_key=f"{prefix}{receipt.id}:{already}",
            )
            session.add(reversal)
            await session.flush()
            refund = max(amount - cash_left, 0)
            cash_left = max(cash_left - amount, 0)
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
            delta -= amount


async def reverse_lift_cancellation(
    session: AsyncSession, *, settings, service_date, lift_time
) -> None:
    from contextlib import asynccontextmanager

    from veloexpress_core.daybook import load_day_bookings
    from veloexpress_core.payments import PaymentsService

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
    day = next(iter(days), None)
    claims = list(
        await session.scalars(
            select(PaymentClaim).where(
                PaymentClaim.environment == settings.app_env,
                PaymentClaim.chat_id == settings.telegram_target_chat_id,
                PaymentClaim.thread_id == settings.telegram_target_thread_id,
                PaymentClaim.service_date == service_date,
            )
        )
    )
    released = {}
    for claim in claims:
        row = PaymentsService._refund_row(claim, day=day, cancelled_lift_time=lift_time)
        held = (
            sum(
                int(claim.telegram_user_id in day.seat_holders_by_lift.get(t, ()))
                + day.guests_by_user_lift.get((claim.telegram_user_id, t), 0)
                for t in row.remaining_lift_times
            )
            if day is not None
            else 0
        )
        released[claim.telegram_user_id] = min(
            claim.amount_gel, max(claim.seats - held, 0) * (claim.amount_gel // max(claim.seats, 1))
        )
    await reverse_cancellation(session, claims, released)


async def reverse_day_cancellation(session: AsyncSession, *, settings, service_date) -> None:
    entries = list(
        await session.scalars(
            select(PaymentEntry)
            .where(
                PaymentEntry.environment == settings.app_env,
                PaymentEntry.chat_id == settings.telegram_target_chat_id,
                PaymentEntry.thread_id == settings.telegram_target_thread_id,
                PaymentEntry.service_date == service_date,
            )
            .order_by(PaymentEntry.id)
        )
    )
    claims = list(
        await session.scalars(
            select(PaymentClaim).where(
                PaymentClaim.environment == settings.app_env,
                PaymentClaim.chat_id == settings.telegram_target_chat_id,
                PaymentClaim.thread_id == settings.telegram_target_thread_id,
                PaymentClaim.service_date == service_date,
            )
        )
    )
    known = {c.telegram_user_id for c in claims}
    for uid in {e.telegram_user_id for e in entries} - known:
        own = [e for e in entries if e.telegram_user_id == uid]
        receipts = [e for e in own if e.kind == "received"]
        if not receipts:
            continue
        net = sum(e.amount_gel for e in own if e.kind in {"received", "reversal"})
        cash = sum(
            e.amount_gel for e in own if e.kind in {"received", "reversal"} and e.method == "cash"
        )
        first = receipts[0]
        # A lost/deleted projection must not leave actual receipts in revenue.
        # Use a stable synthetic cycle key; this object is never persisted.
        claims.append(
            PaymentClaim(
                id=-first.id,
                environment=first.environment,
                chat_id=first.chat_id,
                thread_id=first.thread_id,
                service_date=service_date,
                telegram_user_id=uid,
                full_name=f"Rider {uid}",
                seats=0,
                amount_gel=max(net, 0),
                cash_amount_gel=max(cash, 0),
                claimed_at=first.recorded_at,
                method=first.method,
            )
        )
    targets = {}
    for claim in claims:
        prefix = f"cancel:{claim.id}:{claim.claimed_at.isoformat()}:"
        own = [e for e in entries if e.telegram_user_id == claim.telegram_user_id]
        net = sum(e.amount_gel for e in own if e.kind in {"received", "reversal"})
        already = -sum(
            e.amount_gel
            for e in own
            if e.kind == "reversal" and (e.reference_key or "").startswith(prefix)
        )
        targets[claim.telegram_user_id] = max(net, 0) + already
    await reverse_cancellation(session, claims, targets)
