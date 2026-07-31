from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import (
    CancelledLift,
    ManualBookingCount,
    PaymentClaim,
    PaymentsBoard,
    PaymentsTopicPost,
    PollBatch,
    PollOptionSnapshot,
    PollVote,
)
from veloexpress_bot.payments.render import (
    PAYMENTS_PARSE_MODE,
    OutstandingRider,
    PaymentsBoardView,
    RefundRow,
    RiderPayment,
    render_cancellation_report,
    render_payment_post,
    render_payments_board,
)
from veloexpress_bot.polls.defaults import MINIMUM_RIDERS
from veloexpress_bot.polls.service import SessionFactory, TelegramPollClient, decode_option_ids

logger = logging.getLogger(__name__)

NOT_BOOKED_TEXT = "You are not booked for this day."
ALREADY_CLAIMED_TEXT = "Already marked as paid — ↩️ Undo to start over."
NOTHING_TO_UNDO_TEXT = "You have not marked a payment for this day."
NOT_CLAIMED_YET_TEXT = "Tap 💸 I paid first."
MIN_SEATS_TEXT = "At least one seat."
BOARD_GONE_TEXT = "This payments board is no longer active."
PAYMENTS_DISABLED_TEXT = "Payments are not set up for this chat."

CASH_METHOD = "cash"
TRANSFER_METHOD = "transfer"


@dataclass
class DayBookings:
    service_date: date
    running_lift_times: tuple[str, ...]
    cancelled: bool
    polls_created_at: datetime
    lift_times_by_user: dict[int, tuple[str, ...]] = field(default_factory=dict)
    # Every non-cancelled lift a rider booked, including ones still short of the
    # minimum: a refund question must not treat "not full yet" as "gone".
    booked_lift_times_by_user: dict[int, tuple[str, ...]] = field(default_factory=dict)
    labels_by_user: dict[int, tuple[str | None, str]] = field(default_factory=dict)

    @property
    def booked_rider_count(self) -> int:
        # Manual bookings are counted on the availability board but not here:
        # they have no Telegram identity, so nobody can tap a button for them.
        return len(self.lift_times_by_user)


class PaymentsService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        telegram_client: TelegramPollClient,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._telegram_client = telegram_client
        self._zone = ZoneInfo(settings.schedule_timezone)

    @property
    def enabled(self) -> bool:
        return (
            self._settings.telegram_target_chat_id is not None
            and self._settings.telegram_payments_thread_id is not None
        )

    async def sync_boards(self, *, now: datetime | None = None) -> None:
        """Post or refresh one payments board per day that has a running lift.

        Idempotent and driven by the worker tick rather than by threshold events,
        so a restart mid-weekend simply catches up on the next tick.
        """
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        for day in await self._active_days(today=moment.date()):
            await self._refresh_board(day)

    async def claim(
        self,
        *,
        service_date: date,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        method: str = TRANSFER_METHOD,
    ) -> str:
        if not self.enabled:
            return PAYMENTS_DISABLED_TEXT
        day = await self._day(service_date)
        if day is None:
            return BOARD_GONE_TEXT
        lift_times = day.lift_times_by_user.get(telegram_user_id)
        if not lift_times:
            return NOT_BOOKED_TEXT

        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is not None:
                return ALREADY_CLAIMED_TEXT
            session.add(
                PaymentClaim(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=service_date,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    seats=len(lift_times),
                    method=method,
                )
            )
            await session.commit()

        await self._announce_claim(day, telegram_user_id)
        await self._refresh_board(day)
        # Name the lifts: the amount only makes sense once you can see that lifts
        # still short of the minimum are not charged for.
        how = " in cash" if method == CASH_METHOD else ""
        return f"Thanks! {', '.join(lift_times)} · {self._amount(len(lift_times))} GEL{how}."

    async def adjust_seats(
        self,
        *,
        service_date: date,
        telegram_user_id: int,
        delta: int,
    ) -> str:
        if not self.enabled:
            return PAYMENTS_DISABLED_TEXT
        day = await self._day(service_date)
        if day is None:
            return BOARD_GONE_TEXT

        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is None:
                return NOT_CLAIMED_YET_TEXT
            seats = claim.seats + delta
            if seats < 1:
                return MIN_SEATS_TEXT
            claim.seats = seats
            claim.updated_at = datetime.now(UTC)
            await session.commit()

        await self._announce_claim(day, telegram_user_id)
        await self._refresh_board(day)
        return f"{seats} seats · {self._amount(seats)} GEL."

    async def undo(self, *, service_date: date, telegram_user_id: int) -> str:
        if not self.enabled:
            return PAYMENTS_DISABLED_TEXT
        day = await self._day(service_date)
        posted_message_id: int | None = None
        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is None:
                return NOTHING_TO_UNDO_TEXT
            posted_message_id = claim.posted_message_id
            await session.delete(claim)
            await session.commit()

        if posted_message_id is not None:
            await self._telegram_client.delete_message(
                chat_id=self._require_chat_id(),
                message_id=posted_message_id,
            )
        if day is not None:
            await self._refresh_board(day)
        return "Removed."

    async def toggle_admin_payment(
        self,
        *,
        service_date: date,
        telegram_user_id: int,
        admin_user_id: int,
    ) -> str:
        """Record a payment made outside Telegram — cash, or a direct message to Misho.

        Nothing is posted to the payments topic: whoever took the cash already
        knows, and the board is where the group reads the result.
        """
        if not self.enabled:
            # Reachable from the booking monitor even with no payments topic set,
            # and without this the board would land in the group's root topic.
            return PAYMENTS_DISABLED_TEXT
        day = await self._day(service_date)
        if day is None:
            return BOARD_GONE_TEXT
        username, full_name = day.labels_by_user.get(telegram_user_id, (None, "Rider"))
        seats = max(len(day.lift_times_by_user.get(telegram_user_id, ())), 1)

        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is not None:
                posted_message_id = claim.posted_message_id
                await session.delete(claim)
                await session.commit()
                notice = f"{_rider_label(username, full_name)}: not paid."
            else:
                posted_message_id = None
                session.add(
                    PaymentClaim(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=service_date,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        seats=seats,
                        # Left unset: an admin records money the bot never saw, and
                        # guessing "cash" could send Misho hunting for a transfer.
                        verified_by_user_id=admin_user_id,
                        verified_at=datetime.now(UTC),
                    )
                )
                await session.commit()
                notice = f"{_rider_label(username, full_name)}: paid {self._amount(seats)} GEL."

        if posted_message_id is not None:
            await self._telegram_client.delete_message(
                chat_id=self._require_chat_id(),
                message_id=posted_message_id,
            )
        await self._refresh_board(day)
        return notice

    async def cancellation_report(
        self,
        *,
        service_date: date,
        cancelled_lift_time: str | None = None,
    ) -> str | None:
        """What the admin owes back after cancelling. None when nobody had paid.

        Call this after the cancellation is recorded, so a rider's remaining lifts
        already exclude what was just cancelled.
        """
        if not self.enabled:
            return None
        day = await self._day(service_date)
        async with self._session_factory() as session:
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._require_chat_id())
                    .where(PaymentClaim.service_date == service_date)
                    .order_by(PaymentClaim.claimed_at, PaymentClaim.id)
                )
            ).all()
        if not claims:
            return None

        rows = tuple(
            RefundRow(
                label=_rider_label(claim.username, claim.full_name),
                telegram_user_id=claim.telegram_user_id,
                seats=claim.seats,
                amount_gel=self._amount(claim.seats),
                remaining_lift_times=(
                    ()
                    if day is None or cancelled_lift_time is None
                    else day.booked_lift_times_by_user.get(claim.telegram_user_id, ())
                ),
            )
            for claim in claims
        )
        return render_cancellation_report(
            service_date=service_date,
            cancelled_lift_time=cancelled_lift_time,
            rows=rows,
        )

    async def forget_day(self, *, service_date: date) -> None:
        """Drop the day's payments once the refunds have been reported.

        A retired day is being paid back, so leaving the claims behind would make a
        revived poll open with money the bot no longer holds. Call this after
        cancellation_report — that report is the record.
        """
        if not self.enabled:
            return
        async with self._session_factory() as session:
            await session.execute(
                delete(PaymentClaim)
                .where(PaymentClaim.environment == self._settings.app_env)
                .where(PaymentClaim.chat_id == self._require_chat_id())
                .where(PaymentClaim.service_date == service_date)
            )
            await session.commit()
        logger.info(
            "payments_day_forgotten service_date=%s",
            service_date.isoformat(),
            extra={"service_date": service_date.isoformat()},
        )

    async def record_topic_post(self, *, telegram_user_id: int, posted_at: datetime) -> None:
        """Treat a rider writing in the payments topic as their payment report.

        The bot never reads what they wrote — the group convention is that you
        post there when you have paid, so presence is the whole signal. The
        trade-off is deliberate: a question posted in the topic also counts, and
        an admin can clear it from the monitor.
        """
        if not self.enabled:
            return
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PaymentsTopicPost)
                .where(PaymentsTopicPost.environment == self._settings.app_env)
                .where(PaymentsTopicPost.chat_id == self._require_chat_id())
                .where(PaymentsTopicPost.telegram_user_id == telegram_user_id)
            )
            if row is None:
                session.add(
                    PaymentsTopicPost(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        telegram_user_id=telegram_user_id,
                        last_posted_at=posted_at,
                    )
                )
            else:
                row.last_posted_at = posted_at
            await session.commit()

        await self._claim_from_topic_post(telegram_user_id=telegram_user_id, posted_at=posted_at)

    async def _claim_from_topic_post(self, *, telegram_user_id: int, posted_at: datetime) -> None:
        """Mark the rider paid for the soonest running day they are booked on.

        Only one day, and only the nearest: a single message cannot be read as
        paying for a whole weekend, and guessing wide would overstate what Misho
        has received.
        """
        for day in await self._active_days(today=posted_at.astimezone(self._zone).date()):
            if day.cancelled or telegram_user_id not in day.lift_times_by_user:
                continue
            if posted_at < day.polls_created_at:
                # Written before this day's poll existed, so it cannot be about it.
                continue
            async with self._session_factory() as session:
                if (
                    await self._claim_row(
                        session=session,
                        service_date=day.service_date,
                        telegram_user_id=telegram_user_id,
                    )
                    is not None
                ):
                    return
                username, full_name = day.labels_by_user.get(telegram_user_id, (None, "Rider"))
                session.add(
                    PaymentClaim(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        seats=len(day.lift_times_by_user[telegram_user_id]),
                    )
                )
                await session.commit()
            await self._refresh_board(day)
            return

    async def _announce_claim(self, day: DayBookings, telegram_user_id: int) -> None:
        """Post the rider's payment line, unless they already wrote it themselves."""
        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=day.service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is None:
                return
            seats = claim.seats
            label = _rider_label(claim.username, claim.full_name)
            cash = claim.method == CASH_METHOD
            posted_message_id = claim.posted_message_id
            self_posted = await self._posted_in_topic_since(
                session=session,
                telegram_user_id=telegram_user_id,
                since=day.polls_created_at,
            )

        if self_posted and posted_message_id is None:
            return

        text = render_payment_post(
            label=label,
            service_date=day.service_date,
            lift_times=day.lift_times_by_user.get(telegram_user_id, ()),
            seats=seats,
            amount_gel=self._amount(seats),
            user_id=telegram_user_id,
            cash=cash,
        )
        if posted_message_id is not None:
            await self._telegram_client.edit_text(
                chat_id=self._require_chat_id(),
                message_id=posted_message_id,
                text=text,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
            return

        sent = await self._telegram_client.send_text(
            chat_id=self._require_chat_id(),
            message_thread_id=self._settings.telegram_payments_thread_id,
            text=text,
            parse_mode=PAYMENTS_PARSE_MODE,
        )
        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=day.service_date,
                telegram_user_id=telegram_user_id,
            )
            if claim is not None:
                claim.posted_message_id = sent.message_id
                await session.commit()

    async def _refresh_board(self, day: DayBookings) -> None:
        view = await self._board_view(day)
        draft = render_payments_board(view)
        async with self._session_factory() as session:
            board = await session.scalar(
                select(PaymentsBoard)
                .where(PaymentsBoard.environment == self._settings.app_env)
                .where(PaymentsBoard.chat_id == self._require_chat_id())
                .where(PaymentsBoard.service_date == day.service_date)
            )
            board_message_id = board.telegram_message_id if board is not None else None

        if board_message_id is not None:
            await self._telegram_client.edit_text(
                chat_id=self._require_chat_id(),
                message_id=board_message_id,
                text=draft.text,
                reply_markup=draft.reply_markup,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
            return
        if draft.reply_markup is None:
            # Nothing actionable yet: stay out of the payments topic entirely.
            return

        sent = await self._telegram_client.send_text(
            chat_id=self._require_chat_id(),
            message_thread_id=self._settings.telegram_payments_thread_id,
            text=draft.text,
            reply_markup=draft.reply_markup,
            parse_mode=PAYMENTS_PARSE_MODE,
        )
        async with self._session_factory() as session:
            session.add(
                PaymentsBoard(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    service_date=day.service_date,
                    telegram_message_id=sent.message_id,
                )
            )
            await session.commit()
        logger.info(
            "payments_board_posted service_date=%s message_id=%s",
            day.service_date.isoformat(),
            sent.message_id,
            extra={"service_date": day.service_date.isoformat(), "message_id": sent.message_id},
        )

    async def _board_view(self, day: DayBookings) -> PaymentsBoardView:
        async with self._session_factory() as session:
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._require_chat_id())
                    .where(PaymentClaim.service_date == day.service_date)
                    .order_by(PaymentClaim.claimed_at, PaymentClaim.id)
                )
            ).all()
        paid_user_ids = {claim.telegram_user_id for claim in claims}
        return PaymentsBoardView(
            service_date=day.service_date,
            running_lift_times=day.running_lift_times,
            price_gel=self._settings.payment_price_gel,
            payments=tuple(
                RiderPayment(
                    label=_rider_label(claim.username, claim.full_name),
                    seats=claim.seats,
                    amount_gel=self._amount(claim.seats),
                    cash=claim.method == CASH_METHOD,
                )
                for claim in claims
            ),
            outstanding=tuple(
                OutstandingRider(
                    telegram_user_id=user_id,
                    label=_rider_label(*day.labels_by_user.get(user_id, (None, "Rider"))),
                )
                for user_id in day.lift_times_by_user
                if user_id not in paid_user_ids
            ),
            deadline_time=self._settings.booking_deadline_time,
            cancelled=day.cancelled,
        )

    async def _day(self, service_date: date) -> DayBookings | None:
        days = await self._active_days(today=service_date)
        return next((day for day in days if day.service_date == service_date), None)

    async def _active_days(self, *, today: date) -> list[DayBookings]:
        chat_id = self._require_chat_id()
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.status.in_(("posted", "cancelled")))
                    .where(PollBatch.service_date >= today)
                    .order_by(PollBatch.service_date, PollBatch.id)
                )
            ).all()
            latest_by_date = {batch.service_date: batch for batch in batches}
            if not latest_by_date:
                return []

            batch_ids = tuple(batch.id for batch in latest_by_date.values())
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot)
                    .where(PollOptionSnapshot.batch_id.in_(batch_ids))
                    .where(PollOptionSnapshot.lift_time.is_not(None))
                    .order_by(PollOptionSnapshot.option_index)
                )
            ).all()
            poll_ids = tuple({snapshot.poll_id for snapshot in snapshots})
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))
            ).all()
            service_dates = tuple(latest_by_date)
            manual = (
                await session.scalars(
                    select(ManualBookingCount)
                    .where(ManualBookingCount.environment == self._settings.app_env)
                    .where(ManualBookingCount.chat_id == chat_id)
                    .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
                    .where(ManualBookingCount.service_date.in_(service_dates))
                )
            ).all()
            cancelled_lifts = (
                await session.scalars(
                    select(CancelledLift)
                    .where(CancelledLift.environment == self._settings.app_env)
                    .where(CancelledLift.chat_id == chat_id)
                    .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                    .where(CancelledLift.service_date.in_(service_dates))
                )
            ).all()

        votes_by_poll: dict[str, list[PollVote]] = {}
        for vote in votes:
            votes_by_poll.setdefault(vote.poll_id, []).append(vote)
        manual_by_date_time = {(row.service_date, row.lift_time): row.count for row in manual}
        cancelled_date_time = {(row.service_date, row.lift_time) for row in cancelled_lifts}

        days: list[DayBookings] = []
        for service_date, batch in latest_by_date.items():
            day = DayBookings(
                service_date=service_date,
                running_lift_times=(),
                cancelled=batch.status == "cancelled",
                polls_created_at=_as_utc(batch.created_at),
            )
            running: list[str] = []
            for snapshot in (s for s in snapshots if s.batch_id == batch.id):
                lift_time = snapshot.lift_time
                if lift_time is None or (service_date, lift_time) in cancelled_date_time:
                    continue
                voters = [
                    vote
                    for vote in votes_by_poll.get(snapshot.poll_id, [])
                    if snapshot.option_index in decode_option_ids(vote.option_ids)
                ]
                seats = len(voters) + manual_by_date_time.get((service_date, lift_time), 0)
                for vote in voters:
                    day.booked_lift_times_by_user[vote.telegram_user_id] = (
                        *day.booked_lift_times_by_user.get(vote.telegram_user_id, ()),
                        lift_time,
                    )
                    day.labels_by_user[vote.telegram_user_id] = (vote.username, vote.full_name)
                if seats < MINIMUM_RIDERS:
                    continue
                running.append(lift_time)
                for vote in voters:
                    day.lift_times_by_user[vote.telegram_user_id] = (
                        *day.lift_times_by_user.get(vote.telegram_user_id, ()),
                        lift_time,
                    )
                    day.labels_by_user[vote.telegram_user_id] = (vote.username, vote.full_name)
            day.running_lift_times = tuple(running)
            days.append(day)
        return days

    async def _claim_row(
        self,
        *,
        session: AsyncSession,
        service_date: date,
        telegram_user_id: int,
    ) -> PaymentClaim | None:
        return await session.scalar(
            select(PaymentClaim)
            .where(PaymentClaim.environment == self._settings.app_env)
            .where(PaymentClaim.chat_id == self._require_chat_id())
            .where(PaymentClaim.service_date == service_date)
            .where(PaymentClaim.telegram_user_id == telegram_user_id)
        )

    async def _posted_in_topic_since(
        self,
        *,
        session: AsyncSession,
        telegram_user_id: int,
        since: datetime,
    ) -> bool:
        last_posted_at = await session.scalar(
            select(PaymentsTopicPost.last_posted_at)
            .where(PaymentsTopicPost.environment == self._settings.app_env)
            .where(PaymentsTopicPost.chat_id == self._require_chat_id())
            .where(PaymentsTopicPost.telegram_user_id == telegram_user_id)
        )
        if last_posted_at is None:
            return False
        return _as_utc(last_posted_at) >= since

    def _amount(self, seats: int) -> int:
        return seats * self._settings.payment_price_gel

    def _require_chat_id(self) -> int:
        chat_id = self._settings.telegram_target_chat_id
        if chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required for payments."
            raise ValueError(msg)
        return chat_id


def _rider_label(username: str | None, full_name: str) -> str:
    return f"@{username}" if username else full_name


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
