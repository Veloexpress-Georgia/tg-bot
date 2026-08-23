import html
import logging
from asyncio import Lock
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    BookingMonitorDraft,
    LiftHistory,
    LiftHistoryDay,
    LiftRider,
    MonitorGuest,
    MonitorLateExit,
    MonitorRider,
    MonitorWaitlistRider,
    render_all_riders,
    render_booking_management,
    render_booking_monitor,
    render_cancel_day_confirmation,
    render_lift_history,
)
from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import (
    ACTIVE_POLL_BATCH_STATUSES,
    AdminBookingMonitor,
    CancelledLift,
    DeadlineRoster,
    GuestSeat,
    LiftSignalState,
    ManualBookingCount,
    PaymentClaim,
    PaymentsBoard,
    PollAutoSchedule,
    PollBatch,
    PollMessage,
    PollOptionSnapshot,
    PollScheduleHistory,
    PollVote,
    PollVoteEvent,
    RefundReport,
    ServiceDayNotice,
)
from veloexpress_bot.deeplinks import message_link, topic_link

# Safe despite payments importing polls: `myday` reaches only `polls.render`, and
# `payments/__init__` pulls in `render`, never `service`. Kept next to its parser
# rather than moved into `deeplinks`, which is about t.me/c group links.
from veloexpress_bot.payments.myday import deep_link
from veloexpress_bot.polls.autoschedule import render_schedule_summary, state_from_row
from veloexpress_bot.polls.defaults import (
    DEFAULT_CANCELLED_LIFT_TIMES,
    DEFAULT_LIFTS,
    MINIMUM_RIDERS,
    PAYMENT_TERMS_PARSE_MODE,
    PaymentTerms,
    StartLocation,
)
from veloexpress_bot.polls.liftsignals import (
    LiftEvent,
    LiftEventKind,
    LiftMemory,
    LiftSignal,
    booking_deadline_at,
    day_opener_time,
    decide_deadline_reminder,
    decide_lift_signal,
    lift_departure_at,
    lift_minutes,
    render_deadline_reminder,
    render_lift_signal_notice,
)
from veloexpress_bot.polls.render import (
    AVAILABILITY_PARSE_MODE,
    GuestParty,
    LiftAvailability,
    PollDraft,
    PollRenderInput,
    WaitlistRider,
    render_availability_status,
    render_poll,
    render_poll_notice,
)
from veloexpress_bot.polls.schedule import (
    lift_range_from_cancelled,
    normalize_cancelled_lift_times,
    suggested_cancelled_lift_times,
)
from veloexpress_bot.polls.seating import SeatCandidate, allocate_seats


class DuplicatePollError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

ACTIVE_BATCH_STATUSES = (*ACTIVE_POLL_BATCH_STATUSES, "cleanup_pending", "cleanup_failed")
REUSABLE_BATCH_STATUSES = {"deleted", "failed", "recreated", "cancelled"}

# Good news before bad news, and the departure ping last so it reads as the
# closing word when a tick happens to produce all three.
LIFT_SIGNAL_KIND_ORDER: tuple[LiftEventKind, ...] = ("confirmed", "undershoot", "departure")


@dataclass(frozen=True)
class SentPollMessage:
    message_id: int
    poll_id: str | None = None


@dataclass(frozen=True)
class SentTextMessage:
    message_id: int


class TelegramPollClient(Protocol):
    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> SentTextMessage: ...

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage: ...

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> bool: ...

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def unpin_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool: ...


@dataclass(frozen=True)
class PollSetup:
    service_date: date
    created_by_user_id: int
    # Rustaveli is closed, so Justice Hall departures are off; all lifts start at Vake.
    first_lift_location: StartLocation = StartLocation.VAKE
    cancelled_lift_times: tuple[str, ...] = ()

    def idempotency_key(self, settings: Settings) -> str:
        cancelled = ",".join(sorted(self.cancelled_lift_times))
        return (
            f"{settings.app_env}:{settings.telegram_target_chat_id}:"
            f"{settings.telegram_target_thread_id}:{self.service_date.isoformat()}:"
            f"{self.first_lift_location}:{cancelled}"
        )


@dataclass(frozen=True)
class PollCreationResult:
    batch_id: int
    message_id: int
    availability_message_id: int
    poll_id: str | None
    pinned: bool
    notice_message_id: int | None = None


@dataclass(frozen=True)
class CleanupResult:
    deleted_count: int
    failed_count: int


@dataclass(frozen=True)
class BumpedRider:
    """A rider a manual seat pushed off the lift, and whether their money is in."""

    telegram_user_id: int
    label: str
    paid: bool


@dataclass(frozen=True)
class ManualBookingAdjustmentResult:
    service_date: date
    lift_time: str
    manual_count: int
    # Named, not counted: an admin who takes a rider personally has to know whose
    # seat paid for it, and who is now owed money back.
    bumped: tuple[BumpedRider, ...] = ()


@dataclass(frozen=True)
class ActivePollConflict:
    batch_id: int
    service_date: date
    status: str


@dataclass(frozen=True)
class RecreatePollsResult:
    created: tuple[PollCreationResult, ...]
    report_text: str
    old_batch_ids: tuple[int, ...]
    replacement_by_date: dict[date, int]
    replaced_dates: tuple[date, ...]


# Early enough to be in hand before the first van at 8:30, late enough not to be
# a notification in the middle of the night.
MONITOR_REPOST_AT = time(8, 0)


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PollPostingService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        telegram_client: TelegramPollClient,
        bot_username: str = "",
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._telegram_client = telegram_client
        self._bot_username = bot_username
        self._availability_locks: dict[str, Lock] = {}
        # Notices already reconciled in this process; see refresh_poll_notices.
        self._reconciled_notice_batch_ids: set[int] = set()
        self._deadline_reminder_texts: dict[date, str] = {}
        self._zone = ZoneInfo(settings.schedule_timezone)

    async def _payment_terms(self, *, service_date: date | None = None) -> PaymentTerms:
        return PaymentTerms(
            price_gel=self._settings.payment_price_gel,
            deadline_time=self._settings.booking_deadline_time,
            link=await self._payments_link(service_date),
        )

    async def _payments_link(self, service_date: date | None) -> str:
        """Where "pay" sends a rider: their own private card, when that is possible.

        This is the most-tapped link the bot has, and pointing it at a group
        message wastes it. A deep link is pressing Start, so tapping it both
        shows the rider their own day — their lifts, their amount, no scrolling
        a shared board looking for themselves — and leaves the bot able to
        message them ever after. Most of the 166 members never opened the bot,
        and nothing else in the group converts them.

        It never settles anything by itself: tapping a link is not paying.

        Falls back to the day's board, then the payments topic, when the bot's
        username is unknown or the link has no day to point at.
        """
        if self._bot_username and service_date is not None:
            return deep_link(bot_username=self._bot_username, service_date=service_date)
        chat_id = self._settings.telegram_target_chat_id
        thread_id = self._settings.telegram_payments_thread_id
        if chat_id is None or thread_id is None:
            return ""
        board_message_id = None
        if service_date is not None:
            async with self._session_factory() as session:
                board_message_id = await session.scalar(
                    select(PaymentsBoard.telegram_message_id)
                    .where(PaymentsBoard.environment == self._settings.app_env)
                    .where(PaymentsBoard.chat_id == chat_id)
                    .where(PaymentsBoard.service_date == service_date)
                )
        if board_message_id is not None:
            return (
                message_link(chat_id=chat_id, thread_id=thread_id, message_id=board_message_id)
                or ""
            )
        return topic_link(chat_id=chat_id, thread_id=thread_id) or ""

    def _require_target_chat_id(self) -> int:
        chat_id = self._settings.telegram_target_chat_id
        if chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to post to the group."
            raise ValueError(msg)
        return chat_id

    async def suggested_cancelled_lift_times(self) -> tuple[str, ...]:
        if self._settings.telegram_target_chat_id is None:
            return DEFAULT_CANCELLED_LIFT_TIMES
        try:
            async with self._session_factory() as session:
                history = (
                    await session.scalars(
                        select(PollScheduleHistory)
                        .where(PollScheduleHistory.environment == self._settings.app_env)
                        .where(
                            PollScheduleHistory.chat_id == self._settings.telegram_target_chat_id
                        )
                        .where(
                            PollScheduleHistory.thread_id
                            == self._settings.telegram_target_thread_id
                        )
                        .order_by(
                            PollScheduleHistory.service_week_start,
                            PollScheduleHistory.id,
                        )
                    )
                ).all()
        except Exception:
            logger.exception("poll_schedule_suggestion_failed")
            return DEFAULT_CANCELLED_LIFT_TIMES

        return suggested_cancelled_lift_times(
            ((row.first_lift_time, row.last_lift_time) for row in history),
            default_cancelled_lift_times=DEFAULT_CANCELLED_LIFT_TIMES,
        )

    async def record_schedule_selection(
        self,
        *,
        service_dates: tuple[date, ...],
        cancelled_lift_times: tuple[str, ...],
        created_by_user_id: int,
    ) -> bool:
        if self._settings.telegram_target_chat_id is None or not service_dates:
            return False

        normalized = normalize_cancelled_lift_times(cancelled_lift_times)
        first_time, last_time = lift_range_from_cancelled(normalized)
        service_week_start = _service_week_start(min(service_dates))
        now = datetime.now(UTC)
        try:
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(PollScheduleHistory)
                    .where(PollScheduleHistory.environment == self._settings.app_env)
                    .where(PollScheduleHistory.chat_id == self._settings.telegram_target_chat_id)
                    .where(
                        PollScheduleHistory.thread_id == self._settings.telegram_target_thread_id
                    )
                    .where(PollScheduleHistory.service_week_start == service_week_start)
                )
                if row is None:
                    session.add(
                        PollScheduleHistory(
                            environment=self._settings.app_env,
                            chat_id=self._settings.telegram_target_chat_id,
                            thread_id=self._settings.telegram_target_thread_id,
                            service_week_start=service_week_start,
                            first_lift_time=first_time,
                            last_lift_time=last_time,
                            created_by_user_id=created_by_user_id,
                            updated_at=now,
                        )
                    )
                else:
                    row.first_lift_time = first_time
                    row.last_lift_time = last_time
                    row.created_by_user_id = created_by_user_id
                    row.updated_at = now
                await session.commit()
        except Exception:
            logger.exception(
                "poll_schedule_history_write_failed service_week_start=%s",
                service_week_start,
            )
            return False
        return True

    async def open_booking_monitor(
        self,
        *,
        admin_user_id: int,
        private_chat_id: int,
        selected_service_date: date | None = None,
    ) -> int:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to open the booking monitor."
            raise ValueError(msg)

        async with self._session_factory() as session:
            monitor = await self._booking_monitor_row(
                session=session,
                admin_user_id=admin_user_id,
            )
            existing_message_id = monitor.telegram_message_id if monitor is not None else None
            stored_date = monitor.selected_service_date if monitor is not None else None

        days = await self._booking_monitor_days()
        selected_date = selected_service_date or stored_date
        available_dates = {day.service_date for day in days}
        if selected_date not in available_dates:
            selected_date = days[0].service_date if days else None
        draft = await self._monitor_draft(days, selected_service_date=selected_date)

        # Opening is an explicit action, so retire any prior monitor message and
        # post a fresh one at the bottom instead of editing one buried up the chat.
        if existing_message_id is not None:
            await self._telegram_client.delete_message(
                chat_id=private_chat_id,
                message_id=existing_message_id,
            )
        sent = await self._telegram_client.send_text(
            chat_id=private_chat_id,
            message_thread_id=None,
            text=draft.text,
            reply_markup=draft.reply_markup,
        )
        message_id = sent.message_id

        await self._store_booking_monitor(
            admin_user_id=admin_user_id,
            private_chat_id=private_chat_id,
            telegram_message_id=message_id,
            selected_service_date=selected_date,
        )
        return message_id

    async def status_days(self) -> tuple[BookingMonitorDay, ...]:
        """Current and upcoming lift days, for the start card."""
        return await self._booking_monitor_days()

    async def booking_monitor_view(
        self,
        *,
        admin_user_id: int,
        selected_service_date: date | None = None,
    ) -> BookingMonitorDraft:
        """Render the monitor for in-place edits (day tabs, detail back) without reposting."""
        days = await self._booking_monitor_days()
        available_dates = {day.service_date for day in days}
        selected_date = selected_service_date
        if selected_date not in available_dates:
            selected_date = days[0].service_date if days else None
        draft = await self._monitor_draft(days, selected_service_date=selected_date)

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            monitor = await self._booking_monitor_row(session=session, admin_user_id=admin_user_id)
            if monitor is not None:
                monitor.selected_service_date = selected_date
                monitor.updated_at = now
                await session.commit()
        return draft

    async def booking_management_view(
        self,
        *,
        admin_user_id: int,
        selected_service_date: date | None = None,
    ) -> BookingMonitorDraft:
        """Rare seat mutations live on their own card, away from daily monitoring."""
        days = await self._booking_monitor_days()
        available_dates = {day.service_date for day in days}
        selected_date = selected_service_date
        if selected_date not in available_dates:
            selected_date = days[0].service_date if days else None
        draft = render_booking_management(days, selected_service_date=selected_date)

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            monitor = await self._booking_monitor_row(session=session, admin_user_id=admin_user_id)
            if monitor is not None:
                monitor.selected_service_date = selected_date
                monitor.updated_at = now
                await session.commit()
        return draft

    async def _monitor_draft(
        self,
        days: tuple[BookingMonitorDay, ...],
        *,
        selected_service_date: date | None,
    ) -> BookingMonitorDraft:
        """The monitor card. With no live days it becomes the midweek way in, so
        it needs the schedule line and the last weekend's figures."""
        if days:
            return render_booking_monitor(days, selected_service_date=selected_service_date)
        return render_booking_monitor(
            (),
            selected_service_date=None,
            schedule_line=await self._schedule_line(),
            history=await self.lift_history(),
        )

    async def _schedule_line(self) -> str:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(PollAutoSchedule)
                .where(PollAutoSchedule.environment == self._settings.app_env)
                .where(PollAutoSchedule.chat_id == self._settings.telegram_target_chat_id)
                .where(PollAutoSchedule.thread_id == self._settings.telegram_target_thread_id)
            )
        return render_schedule_summary(
            state_from_row(row),
            now=datetime.now(UTC).astimezone(self._zone),
            zone=self._zone,
        )

    async def lift_history(self, *, limit: int = 8) -> LiftHistory:
        """What the finished lift days came to, newest first.

        Built from the deadline roster rather than live votes: the roster is who
        held a seat when booking closed, which is what actually rode. Days that
        finished without a roster — the bot was down at 20:00, or they predate the
        freeze — fall back to the votes that are still on file, which is the best
        record there is for them.
        """
        if self._settings.telegram_target_chat_id is None:
            return LiftHistory(days=(), total_days=0, total_ran=0, total_seats=0, total_gel=0)
        today = datetime.now(UTC).astimezone(self._zone).date()
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.status == "posted")
                    .where(PollBatch.service_date < today)
                    .order_by(PollBatch.service_date.desc(), PollBatch.id.desc())
                )
            ).all()
            past_batches: dict[date, PollBatch] = {}
            for batch in batches:
                past_batches.setdefault(batch.service_date, batch)
            if not past_batches:
                return LiftHistory(days=(), total_days=0, total_ran=0, total_seats=0, total_gel=0)

            service_dates = tuple(past_batches)
            roster_rows = (
                await session.scalars(
                    select(DeadlineRoster)
                    .where(DeadlineRoster.environment == self._settings.app_env)
                    .where(DeadlineRoster.chat_id == self._settings.telegram_target_chat_id)
                    .where(DeadlineRoster.thread_id == self._settings.telegram_target_thread_id)
                    .where(DeadlineRoster.service_date.in_(service_dates))
                )
            ).all()
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot)
                    .where(
                        PollOptionSnapshot.batch_id.in_(
                            tuple(batch.id for batch in past_batches.values())
                        )
                    )
                    .where(PollOptionSnapshot.lift_time.is_not(None))
                )
            ).all()
            votes = (
                await session.scalars(
                    select(PollVote).where(
                        PollVote.poll_id.in_(tuple({snapshot.poll_id for snapshot in snapshots}))
                    )
                )
            ).all()
            cancelled = (
                await session.scalars(
                    select(CancelledLift)
                    .where(CancelledLift.environment == self._settings.app_env)
                    .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                    .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                    .where(CancelledLift.service_date.in_(service_dates))
                )
            ).all()
            claims = (
                await session.execute(
                    select(PaymentClaim.service_date, PaymentClaim.seats)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._settings.telegram_target_chat_id)
                    .where(PaymentClaim.service_date.in_(service_dates))
                )
            ).all()

        seats_by_date_time: dict[tuple[date, str], int] = {}
        for row in roster_rows:
            key = (row.service_date, row.lift_time)
            seats_by_date_time[key] = seats_by_date_time.get(key, 0) + row.seats
        votes_by_poll_option: dict[tuple[str, int], int] = {}
        for vote in votes:
            for option_index in decode_option_ids(vote.option_ids):
                key = (vote.poll_id, option_index)
                votes_by_poll_option[key] = votes_by_poll_option.get(key, 0) + 1
        cancelled_date_time = {(row.service_date, row.lift_time) for row in cancelled}
        paid_seats_by_date: dict[date, int] = {}
        for claim_date, claim_seats in claims:
            paid_seats_by_date[claim_date] = paid_seats_by_date.get(claim_date, 0) + claim_seats

        history_days: list[LiftHistoryDay] = []
        for service_date, batch in past_batches.items():
            day_snapshots = [
                snapshot
                for snapshot in snapshots
                if snapshot.batch_id == batch.id and snapshot.lift_time is not None
            ]
            ran = 0
            seats = 0
            lifts = 0
            for snapshot in day_snapshots:
                lift_time = snapshot.lift_time
                if lift_time is None or (service_date, lift_time) in cancelled_date_time:
                    continue
                lifts += 1
                lift_seats = seats_by_date_time.get(
                    (service_date, lift_time),
                    votes_by_poll_option.get((snapshot.poll_id, snapshot.option_index), 0),
                )
                if lift_seats >= MINIMUM_RIDERS:
                    ran += 1
                    seats += lift_seats
            history_days.append(
                LiftHistoryDay(
                    service_date=service_date,
                    ran_count=ran,
                    lift_count=lifts,
                    seat_count=seats,
                    paid_gel=paid_seats_by_date.get(service_date, 0)
                    * self._settings.payment_price_gel,
                )
            )

        return LiftHistory(
            days=tuple(history_days[:limit]),
            total_days=sum(day.ran_count > 0 for day in history_days),
            total_ran=sum(day.ran_count for day in history_days),
            total_seats=sum(day.seat_count for day in history_days),
            total_gel=sum(day.paid_gel for day in history_days),
        )

    async def lift_history_view(self) -> BookingMonitorDraft:
        return render_lift_history(await self.lift_history())

    async def all_riders_view(
        self,
        *,
        selected_service_date: date,
    ) -> BookingMonitorDraft:
        days = await self._booking_monitor_days()
        selected_day = next(
            (day for day in days if day.service_date == selected_service_date),
            days[0] if days else None,
        )
        if selected_day is None:
            return await self._monitor_draft((), selected_service_date=None)
        rosters: list[tuple[BookingLiftStatus, tuple[LiftRider, ...]]] = []
        for lift in selected_day.lifts:
            detail = await self.lift_detail(
                service_date=selected_day.service_date,
                lift_time=lift.time,
            )
            if detail is not None:
                rosters.append((lift, detail[1]))
        return render_all_riders(
            days,
            selected_service_date=selected_day.service_date,
            rosters=tuple(rosters),
        )

    async def cancel_day_confirmation_view(
        self,
        *,
        selected_service_date: date,
    ) -> BookingMonitorDraft | None:
        days = await self._booking_monitor_days()
        day = next(
            (day for day in days if day.service_date == selected_service_date),
            None,
        )
        return render_cancel_day_confirmation(day) if day is not None else None

    async def adjust_manual_booking(
        self,
        *,
        service_date: date,
        lift_time: str,
        delta: int,
        admin_user_id: int,
    ) -> ManualBookingAdjustmentResult:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to adjust bookings."
            raise ValueError(msg)
        if delta not in {-1, 1}:
            msg = "Manual booking adjustments must be +1 or -1."
            raise ValueError(msg)

        now = datetime.now(UTC)
        poll_id: str | None = None
        async with self._session_factory() as session:
            snapshot = await self._active_snapshot_for_lift(
                session=session,
                service_date=service_date,
                lift_time=lift_time,
            )
            if snapshot is None:
                msg = "This lift is not active."
                raise ValueError(msg)
            poll_id = snapshot.poll_id
            booking = await session.scalar(
                select(ManualBookingCount)
                .where(ManualBookingCount.environment == self._settings.app_env)
                .where(ManualBookingCount.chat_id == self._settings.telegram_target_chat_id)
                .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
                .where(ManualBookingCount.service_date == service_date)
                .where(ManualBookingCount.lift_time == lift_time)
                .with_for_update()
            )
            current_count = booking.count if booking is not None else 0
            new_count = current_count + delta
            if new_count < 0:
                msg = "No manual bookings to remove."
                raise ValueError(msg)
            if delta > 0:
                cancelled = await session.scalar(
                    select(CancelledLift.id)
                    .where(CancelledLift.environment == self._settings.app_env)
                    .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                    .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                    .where(CancelledLift.service_date == service_date)
                    .where(CancelledLift.lift_time == lift_time)
                )
                if cancelled is not None:
                    msg = "This lift is cancelled."
                    raise ValueError(msg)
                votes = (
                    await session.scalars(
                        select(PollVote).where(PollVote.poll_id == snapshot.poll_id)
                    )
                ).all()
                vote_count = sum(
                    snapshot.option_index in decode_option_ids(vote.option_ids) for vote in votes
                )
                guest_count = (
                    await session.scalar(
                        select(func.coalesce(func.sum(GuestSeat.count), 0))
                        .where(GuestSeat.environment == self._settings.app_env)
                        .where(GuestSeat.chat_id == self._settings.telegram_target_chat_id)
                        .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                        .where(GuestSeat.service_date == service_date)
                        .where(GuestSeat.lift_time == lift_time)
                    )
                ) or 0
                capacity = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}.get(lift_time, 10)
                if vote_count + current_count + guest_count >= capacity:
                    msg = "No seats left on this lift."
                    raise ValueError(msg)
            if booking is None:
                session.add(
                    ManualBookingCount(
                        environment=self._settings.app_env,
                        chat_id=self._settings.telegram_target_chat_id,
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=service_date,
                        lift_time=lift_time,
                        count=new_count,
                        updated_by_user_id=admin_user_id,
                        updated_at=now,
                    )
                )
            else:
                booking.count = new_count
                booking.updated_by_user_id = admin_user_id
                booking.updated_at = now
            # Re-check after staging the change. A poll vote can land between the
            # capacity read above and this query; even then the manual seat must
            # fail instead of silently displacing the new holder.
            bumped = (
                await self._bumped_by_manual(
                    session=session,
                    snapshot=snapshot,
                    service_date=service_date,
                    lift_time=lift_time,
                    manual_before=current_count,
                    manual_after=new_count,
                )
                if delta > 0
                else ()
            )
            if bumped:
                msg = "No seats left on this lift."
                raise ValueError(msg)
            await session.commit()

        if poll_id is not None:
            await self._refresh_availability(poll_id)
        await self._refresh_booking_monitors()
        return ManualBookingAdjustmentResult(
            service_date=service_date,
            lift_time=lift_time,
            manual_count=new_count,
            bumped=bumped,
        )

    async def _bumped_by_manual(
        self,
        *,
        session: AsyncSession,
        snapshot: PollOptionSnapshot,
        service_date: date,
        lift_time: str,
        manual_before: int,
        manual_after: int,
    ) -> tuple[BumpedRider, ...]:
        """Riders who held a seat at the old manual count and lost it at the new one.

        A manual booking outranks a poll vote in allocation. This detects a rider
        who would lose a seat so the caller can reject the manual booking before
        commit, including when a vote raced the earlier capacity check.
        """
        votes = [
            vote
            for vote in (
                await session.scalars(select(PollVote).where(PollVote.poll_id == snapshot.poll_id))
            ).all()
            if snapshot.option_index in decode_option_ids(vote.option_ids)
        ]
        if not votes:
            return ()
        capacity = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}.get(lift_time, 10)
        guests = (
            await session.scalar(
                select(func.coalesce(func.sum(GuestSeat.count), 0))
                .where(GuestSeat.environment == self._settings.app_env)
                .where(GuestSeat.chat_id == self._settings.telegram_target_chat_id)
                .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                .where(GuestSeat.service_date == service_date)
                .where(GuestSeat.lift_time == lift_time)
            )
        ) or 0
        candidates = [
            SeatCandidate(
                telegram_user_id=vote.telegram_user_id,
                label=_rider_label(vote),
                booked_at=vote_booked_at(vote, snapshot.option_index),
            )
            for vote in votes
        ]
        before = allocate_seats(candidates, capacity=capacity, reserved=manual_before + guests)
        after = allocate_seats(candidates, capacity=capacity, reserved=manual_after + guests)
        held_after = {candidate.telegram_user_id for candidate in after.holders}
        lost = [
            candidate
            for candidate in before.holders
            if candidate.telegram_user_id not in held_after
        ]
        if not lost:
            return ()
        paid_user_ids = set(
            (
                await session.scalars(
                    select(PaymentClaim.telegram_user_id)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._settings.telegram_target_chat_id)
                    .where(PaymentClaim.service_date == service_date)
                )
            ).all()
        )
        return tuple(
            BumpedRider(
                telegram_user_id=candidate.telegram_user_id,
                label=candidate.label,
                paid=candidate.telegram_user_id in paid_user_ids,
            )
            for candidate in lost
        )

    async def lift_detail(
        self,
        *,
        service_date: date,
        lift_time: str,
    ) -> tuple[BookingLiftStatus, tuple[LiftRider, ...]] | None:
        if self._settings.telegram_target_chat_id is None:
            return None
        capacity_by_time = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}
        async with self._session_factory() as session:
            snapshot = await self._active_snapshot_for_lift(
                session=session,
                service_date=service_date,
                lift_time=lift_time,
            )
            if snapshot is None:
                return None
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id == snapshot.poll_id))
            ).all()
            lift_votes = [
                vote
                for vote in votes
                if snapshot.option_index in decode_option_ids(vote.option_ids)
            ]
            # Payments are per rider per day, so the same claim covers every lift
            # that rider booked on this date.
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._settings.telegram_target_chat_id)
                    .where(PaymentClaim.service_date == service_date)
                )
            ).all()
            claim_by_user = {claim.telegram_user_id: claim for claim in claims}
            guest_rows = (
                await session.scalars(
                    select(GuestSeat)
                    .where(GuestSeat.environment == self._settings.app_env)
                    .where(GuestSeat.chat_id == self._settings.telegram_target_chat_id)
                    .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                    .where(GuestSeat.service_date == service_date)
                    .where(GuestSeat.lift_time == lift_time)
                )
            ).all()
            guests_by_host = {row.host_user_id: row.count for row in guest_rows if row.count > 0}
            guest_count = sum(guests_by_host.values())
            manual_count = await self._manual_booking_count(
                session=session,
                service_date=service_date,
                lift_time=lift_time,
            )
            capacity = capacity_by_time.get(lift_time, 10)
            allocation = allocate_seats(
                (
                    SeatCandidate(
                        telegram_user_id=vote.telegram_user_id,
                        label=_rider_label(vote),
                        booked_at=vote_booked_at(vote, snapshot.option_index),
                    )
                    for vote in lift_votes
                ),
                capacity=capacity,
                reserved=manual_count + guest_count,
            )
            waitlisted_ids = {rider.telegram_user_id for rider in allocation.waitlist}
            riders = tuple(
                sorted(
                    (
                        LiftRider(
                            telegram_user_id=vote.telegram_user_id,
                            label=_rider_label(vote),
                            paid=vote.telegram_user_id in claim_by_user,
                            cash=(claim := claim_by_user.get(vote.telegram_user_id)) is not None
                            and claim.method == "cash"
                            and claim.verified_by_user_id is None,
                            guests=guests_by_host.get(vote.telegram_user_id, 0),
                            waitlisted=vote.telegram_user_id in waitlisted_ids,
                        )
                        for vote in lift_votes
                    ),
                    key=lambda rider: (rider.waitlisted, rider.label),
                )
            )
            cancelled_times = await self._cancelled_lift_times(
                session=session,
                service_date=service_date,
            )
        status = BookingLiftStatus(
            time=lift_time,
            vote_count=len(riders),
            manual_count=manual_count,
            guest_count=guest_count,
            capacity=capacity,
            cancelled=lift_time in cancelled_times,
        )
        return status, riders

    async def cancel_lift(
        self,
        *,
        service_date: date,
        lift_time: str,
        admin_user_id: int,
    ) -> None:
        await self._set_lift_cancelled(
            service_dates_times=((service_date, lift_time),),
            notice=f"❌ {lift_time} · {_service_day_label(service_date)} is cancelled.",
            admin_user_id=admin_user_id,
        )

    async def cancel_day(self, *, service_date: date, admin_user_id: int) -> None:
        """Retire the whole day: the polls close and the day leaves the monitor.

        Unlike a single-lift cancel this is not reversible in place — the date is
        freed so an admin can post a fresh poll if the day comes back.
        """
        if self._settings.telegram_target_chat_id is None:
            return
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.service_date == service_date)
                    .where(PollBatch.status == "posted")
                )
            ).all()
            if not batches:
                return
            batch_ids = [batch.id for batch in batches]
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot).where(PollOptionSnapshot.batch_id.in_(batch_ids))
                )
            ).all()
            poll_ids = tuple({snapshot.poll_id for snapshot in snapshots})
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))
            ).all()
            messages = (
                await session.scalars(
                    select(PollMessage).where(PollMessage.batch_id.in_(batch_ids))
                )
            ).all()
            availability_message_ids = [
                message.telegram_message_id
                for message in messages
                if message.message_kind == "availability"
            ]
            pinned_poll_message_ids = [
                message.telegram_message_id
                for message in messages
                if message.message_kind == "poll" and message.pinned
            ]

            mentions: dict[int, str] = {}
            for vote in votes:
                if decode_option_ids(vote.option_ids):
                    mentions.setdefault(vote.telegram_user_id, vote.full_name or _rider_label(vote))

            for batch in batches:
                batch.status = "cancelled"
            await session.execute(
                delete(CancelledLift)
                .where(CancelledLift.environment == self._settings.app_env)
                .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                .where(CancelledLift.service_date == service_date)
            )
            # Hand-added riders go with the day. Nobody can tell whether they still
            # intend to come if it is revived, and leaving them behind means a fresh
            # poll opens with seats already taken by people nobody can name.
            await session.execute(
                delete(ManualBookingCount)
                .where(ManualBookingCount.environment == self._settings.app_env)
                .where(ManualBookingCount.chat_id == self._settings.telegram_target_chat_id)
                .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
                .where(ManualBookingCount.service_date == service_date)
            )
            await session.commit()

        await self._send_tagged_notice(
            f"❌ All lifts on {_service_day_label(service_date)} are cancelled.",
            mentions,
            log_label="lift_cancel_notice_failed",
        )
        board_text = _cancelled_day_board(service_date)
        for message_id in availability_message_ids:
            await self._telegram_client.edit_text(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message_id,
                text=board_text,
                parse_mode=AVAILABILITY_PARSE_MODE,
            )
        # The poll stays in the chat as history, but a cancelled day must not keep
        # occupying the pin.
        for message_id in pinned_poll_message_ids:
            unpinned = await self._telegram_client.unpin_message(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message_id,
            )
            if unpinned:
                async with self._session_factory() as session:
                    await session.execute(
                        update(PollMessage)
                        .where(PollMessage.telegram_message_id == message_id)
                        .values(pinned=False)
                    )
                    await session.commit()
        await self._refresh_booking_monitors()

    async def restore_lift(
        self,
        *,
        service_date: date,
        lift_time: str,
        admin_user_id: int,
    ) -> None:
        if self._settings.telegram_target_chat_id is None:
            return
        async with self._session_factory() as session:
            await session.execute(
                delete(CancelledLift)
                .where(CancelledLift.environment == self._settings.app_env)
                .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                .where(CancelledLift.service_date == service_date)
                .where(CancelledLift.lift_time == lift_time)
            )
            await session.commit()
        poll_id = await self._active_poll_id(service_date=service_date, lift_time=lift_time)
        if poll_id is not None:
            await self._refresh_availability(poll_id)
        await self._refresh_booking_monitors()

    async def _set_lift_cancelled(
        self,
        *,
        service_dates_times: tuple[tuple[date, str], ...],
        notice: str,
        admin_user_id: int,
    ) -> None:
        if self._settings.telegram_target_chat_id is None or not service_dates_times:
            return
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            for service_date, lift_time in service_dates_times:
                existing = await session.scalar(
                    select(CancelledLift)
                    .where(CancelledLift.environment == self._settings.app_env)
                    .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                    .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                    .where(CancelledLift.service_date == service_date)
                    .where(CancelledLift.lift_time == lift_time)
                )
                if existing is None:
                    session.add(
                        CancelledLift(
                            environment=self._settings.app_env,
                            chat_id=self._settings.telegram_target_chat_id,
                            thread_id=self._settings.telegram_target_thread_id,
                            service_date=service_date,
                            lift_time=lift_time,
                            cancelled_by_user_id=admin_user_id,
                            created_at=now,
                        )
                    )
            await session.commit()

        await self._notify_cancelled_lifts(notice, service_dates_times)

        poll_ids: set[str] = set()
        for service_date, lift_time in service_dates_times:
            poll_id = await self._active_poll_id(service_date=service_date, lift_time=lift_time)
            if poll_id is not None:
                poll_ids.add(poll_id)
        for poll_id in poll_ids:
            await self._refresh_availability(poll_id)
        await self._refresh_booking_monitors()

    async def _notify_cancelled_lifts(
        self,
        notice: str,
        service_dates_times: tuple[tuple[date, str], ...],
    ) -> None:
        if self._settings.telegram_target_chat_id is None:
            return
        mentions: dict[int, str] = {}
        for service_date, lift_time in service_dates_times:
            for user_id, label in await self._lift_voters(
                service_date=service_date,
                lift_time=lift_time,
            ):
                mentions.setdefault(user_id, label)
        await self._send_tagged_notice(notice, mentions, log_label="lift_cancel_notice_failed")

    async def _send_tagged_notice(
        self,
        notice: str,
        mentions: dict[int, str],
        *,
        log_label: str,
    ) -> int | None:
        """Post one group message and tag the riders it concerns.

        Tagging beats a DM: most riders never pressed /start, so Telegram will
        not let the bot message them at all.
        """
        if self._settings.telegram_target_chat_id is None:
            return None
        text = notice
        if mentions:
            tags = " ".join(
                f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'
                for user_id, label in mentions.items()
            )
            text = f"{text}\n\n{tags}"
        try:
            sent = await self._telegram_client.send_text(
                chat_id=self._settings.telegram_target_chat_id,
                message_thread_id=self._settings.telegram_target_thread_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(log_label)
            return None
        return sent.message_id

    async def refresh_poll_notices(self, *, now: datetime | None = None) -> int:
        """Re-render the route/payment notice of every upcoming day, in place.

        The notice states the money rules, so a rule or price change must reach the
        notices already posted — recreating the polls would throw away live votes.
        A Telegram edit is silent, and an unchanged edit is a no-op, so this is
        attempted once per day per process rather than on every tick.
        """
        if self._settings.telegram_target_chat_id is None:
            return 0
        today = (now or datetime.now(UTC)).astimezone(self._zone).date()
        terms = await self._payment_terms()
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(PollBatch.id, PollBatch.first_lift_location, PollMessage)
                    .join(PollMessage, PollMessage.batch_id == PollBatch.id)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.status == "posted")
                    .where(PollBatch.service_date >= today)
                    .where(PollMessage.message_kind == "notice")
                )
            ).all()

        updated = 0
        for batch_id, location, message in rows:
            if batch_id in self._reconciled_notice_batch_ids:
                continue
            self._reconciled_notice_batch_ids.add(batch_id)
            if await self._telegram_client.edit_text(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message.telegram_message_id,
                text=render_poll_notice(StartLocation(location), terms=terms),
                parse_mode=PAYMENT_TERMS_PARSE_MODE,
            ):
                updated += 1
        if updated:
            logger.info("poll_notice_refreshed count=%s", updated, extra={"count": updated})
        return updated

    async def evaluate_lift_signals(self, *, now: datetime | None = None) -> tuple[LiftEvent, ...]:
        """Announce lifts crossing the rider minimum and ping the day's opener.

        Driven by the background tick rather than the vote handler: the debounced
        undershoot notice needs a clock anyway, and sending from inside the
        availability refresh would hold its lock across Telegram calls.
        """
        if self._settings.telegram_target_chat_id is None:
            return ()
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        events: list[LiftEvent] = []
        for day in await self._booking_monitor_days():
            if day.service_date < moment.date():
                continue
            events.extend(await self._evaluate_day_signals(day, now=moment))
            await self._remind_booking_deadline(day, now=moment)
        if events:
            await self._send_lift_signal_notices(tuple(events))
        return tuple(events)

    async def _remind_booking_deadline(self, day: BookingMonitorDay, *, now: datetime) -> None:
        """Nudge the group once, then keep that message honest until the deadline.

        A one-shot snapshot is stale within the hour, and this is the message people
        act on. After the deadline it stops moving and stands as the final list.
        """
        signals = _day_signals(day)
        deadline_at = booking_deadline_at(
            day.service_date,
            self._settings.booking_deadline_time,
            zone=self._zone,
        )
        async with self._session_factory() as session:
            notice = await self._service_day_notice(session=session, service_date=day.service_date)
            already_reminded = notice is not None and notice.deadline_reminded_at is not None
            message_id = notice.deadline_message_id if notice is not None else None
            if not already_reminded and not decide_deadline_reminder(
                signals,
                now=now,
                deadline_at=deadline_at,
                already_reminded=False,
            ):
                return

        terms = await self._payment_terms(service_date=day.service_date)
        deadline_passed = now > deadline_at
        text = render_deadline_reminder(
            day.service_date,
            signals,
            terms=terms,
            deadline_passed=deadline_passed,
        )

        if already_reminded:
            await self._refresh_deadline_reminder(
                message_id=message_id,
                text=text,
                service_date=day.service_date,
            )
            return

        # Nobody is tagged: whoever is booked is already booked, and tagging the
        # whole group is exactly the spam this bot avoids.
        sent_message_id = await self._send_tagged_notice(
            text,
            {},
            log_label="booking_deadline_reminder_failed",
        )
        async with self._session_factory() as session:
            notice = await self._service_day_notice(session=session, service_date=day.service_date)
            if notice is None:
                notice = ServiceDayNotice(
                    environment=self._settings.app_env,
                    chat_id=self._require_target_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=day.service_date,
                )
                session.add(notice)
            notice.deadline_reminded_at = now.astimezone(UTC)
            notice.deadline_message_id = sent_message_id
            notice.updated_at = now.astimezone(UTC)
            await session.commit()
        logger.info(
            "booking_deadline_reminded service_date=%s",
            day.service_date.isoformat(),
            extra={"service_date": day.service_date.isoformat()},
        )

    async def _refresh_deadline_reminder(
        self,
        *,
        message_id: int | None,
        text: str,
        service_date: date,
    ) -> None:
        """Edit the reminder in place, and only when the text actually moved.

        The last text is kept in memory rather than the database: a redundant edit
        after a restart costs one silent no-op, and a column would cost a migration.
        """
        if message_id is None:
            return
        if self._deadline_reminder_texts.get(service_date) == text:
            return
        self._deadline_reminder_texts[service_date] = text
        await self._telegram_client.edit_text(
            chat_id=self._require_target_chat_id(),
            message_id=message_id,
            text=text,
            parse_mode="HTML",
        )

    async def _service_day_notice(
        self,
        *,
        session: AsyncSession,
        service_date: date,
    ) -> ServiceDayNotice | None:
        return await session.scalar(
            select(ServiceDayNotice)
            .where(ServiceDayNotice.environment == self._settings.app_env)
            .where(ServiceDayNotice.chat_id == self._settings.telegram_target_chat_id)
            .where(ServiceDayNotice.thread_id == self._settings.telegram_target_thread_id)
            .where(ServiceDayNotice.service_date == service_date)
        )

    async def _evaluate_day_signals(
        self,
        day: BookingMonitorDay,
        *,
        now: datetime,
    ) -> list[LiftEvent]:
        signals = _day_signals(day)
        opener = day_opener_time(signals)
        events: list[LiftEvent] = []
        async with self._session_factory() as session:
            rows = await self._lift_signal_rows(session=session, service_date=day.service_date)
            for signal in signals:
                row = rows.get(signal.lift_time)
                decision = decide_lift_signal(
                    signal,
                    _lift_memory(row),
                    now=now,
                    departure_at=lift_departure_at(
                        signal.service_date,
                        signal.lift_time,
                        zone=self._zone,
                    ),
                    is_day_opener=signal.lift_time == opener,
                )
                if row is None:
                    if decision.memory == LiftMemory():
                        # Nothing to remember yet; do not store an empty row.
                        continue
                    row = LiftSignalState(
                        environment=self._settings.app_env,
                        chat_id=self._settings.telegram_target_chat_id,
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=signal.service_date,
                        lift_time=signal.lift_time,
                    )
                    session.add(row)
                _apply_lift_memory(row, decision.memory, now=now)
                if decision.event is not None:
                    events.append(decision.event)
            await session.commit()
        return events

    async def _lift_signal_rows(
        self,
        *,
        session: AsyncSession,
        service_date: date,
    ) -> dict[str, LiftSignalState]:
        rows = (
            await session.scalars(
                select(LiftSignalState)
                .where(LiftSignalState.environment == self._settings.app_env)
                .where(LiftSignalState.chat_id == self._settings.telegram_target_chat_id)
                .where(LiftSignalState.thread_id == self._settings.telegram_target_thread_id)
                .where(LiftSignalState.service_date == service_date)
            )
        ).all()
        return {row.lift_time: row for row in rows}

    async def _send_lift_signal_notices(self, events: tuple[LiftEvent, ...]) -> None:
        by_kind: dict[LiftEventKind, list[LiftEvent]] = {}
        for event in events:
            by_kind.setdefault(event.kind, []).append(event)
        for kind in LIFT_SIGNAL_KIND_ORDER:
            kind_events = by_kind.get(kind)
            if not kind_events:
                continue
            mentions: dict[int, str] = {}
            for event in kind_events:
                for user_id, label in await self._lift_voters(
                    service_date=event.service_date,
                    lift_time=event.lift_time,
                    seat_holders_only=True,
                ):
                    mentions.setdefault(user_id, label)
            await self._send_tagged_notice(
                render_lift_signal_notice(
                    kind,
                    tuple(kind_events),
                    terms=await self._payment_terms(service_date=_shared_date(kind_events)),
                ),
                mentions,
                log_label="lift_signal_notice_failed",
            )
            logger.info(
                "lift_signal_notified kind=%s lift_count=%s",
                kind,
                len(kind_events),
                extra={"kind": kind, "lift_count": len(kind_events)},
            )

    async def _lift_voters(
        self,
        *,
        service_date: date,
        lift_time: str,
        seat_holders_only: bool = False,
    ) -> list[tuple[int, str]]:
        async with self._session_factory() as session:
            snapshot = await self._active_snapshot_for_lift(
                session=session,
                service_date=service_date,
                lift_time=lift_time,
            )
            if snapshot is None:
                return []
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id == snapshot.poll_id))
            ).all()
            booked = [
                vote
                for vote in votes
                if snapshot.option_index in decode_option_ids(vote.option_ids)
            ]
            if not seat_holders_only:
                return [
                    (vote.telegram_user_id, vote.full_name or _rider_label(vote)) for vote in booked
                ]
            # A waitlisted rider has no seat, so "it is running, pay up" is not for them.
            capacity = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}.get(lift_time, 10)
            manual = await self._manual_booking_count(
                session=session, service_date=service_date, lift_time=lift_time
            )
            guests = await session.scalar(
                select(func.coalesce(func.sum(GuestSeat.count), 0))
                .where(GuestSeat.environment == self._settings.app_env)
                .where(GuestSeat.chat_id == self._settings.telegram_target_chat_id)
                .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                .where(GuestSeat.service_date == service_date)
                .where(GuestSeat.lift_time == lift_time)
            )
            allocation = allocate_seats(
                (
                    SeatCandidate(
                        telegram_user_id=vote.telegram_user_id,
                        label=vote.full_name or _rider_label(vote),
                        booked_at=vote_booked_at(vote, snapshot.option_index),
                    )
                    for vote in booked
                ),
                capacity=capacity,
                reserved=manual + (guests or 0),
            )
            return [
                (candidate.telegram_user_id, candidate.label) for candidate in allocation.holders
            ]

    async def _active_poll_id(self, *, service_date: date, lift_time: str) -> str | None:
        async with self._session_factory() as session:
            snapshot = await self._active_snapshot_for_lift(
                session=session,
                service_date=service_date,
                lift_time=lift_time,
            )
            return snapshot.poll_id if snapshot is not None else None

    async def _cancelled_lift_times(
        self,
        *,
        session: AsyncSession,
        service_date: date,
    ) -> set[str]:
        rows = (
            await session.scalars(
                select(CancelledLift.lift_time)
                .where(CancelledLift.environment == self._settings.app_env)
                .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                .where(CancelledLift.service_date == service_date)
            )
        ).all()
        return set(rows)

    async def has_existing_active_poll(self, setup: PollSetup) -> bool:
        return bool(await self.find_active_conflicts((setup,)))

    async def find_active_conflicts(
        self, setups: tuple[PollSetup, ...]
    ) -> tuple[ActivePollConflict, ...]:
        if self._settings.telegram_target_chat_id is None:
            return ()

        started_at = perf_counter()
        service_dates = tuple({setup.service_date for setup in setups})
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.service_date.in_(service_dates))
                    .where(PollBatch.status.in_(ACTIVE_BATCH_STATUSES))
                )
            ).all()

            conflicts: list[ActivePollConflict] = []
            for batch in batches:
                if await self._is_active_batch(session=session, batch=batch):
                    conflicts.append(
                        ActivePollConflict(
                            batch_id=batch.id,
                            service_date=batch.service_date,
                            status=batch.status,
                        )
                    )
            duration_ms = int((perf_counter() - started_at) * 1000)
            logger.info(
                "poll_active_conflict_check_done duration_ms=%s service_dates=%s "
                "candidate_count=%s conflict_count=%s",
                duration_ms,
                ",".join(item.isoformat() for item in service_dates),
                len(batches),
                len(conflicts),
                extra={
                    "duration_ms": duration_ms,
                    "service_dates": tuple(item.isoformat() for item in service_dates),
                    "candidate_count": len(batches),
                    "conflict_count": len(conflicts),
                },
            )
            return tuple(conflicts)

    async def create_poll(
        self,
        setup: PollSetup,
        *,
        allow_duplicate: bool = False,
        include_notice: bool = False,
        pin_after_send: bool = True,
    ) -> PollCreationResult:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to post polls."
            raise ValueError(msg)

        draft = render_poll(
            PollRenderInput(
                service_date=setup.service_date,
                cancelled_lift_times=setup.cancelled_lift_times,
            )
        )
        manual_counts = await self._manual_booking_counts_for_date(setup.service_date)
        initial_availability = render_availability_status(
            setup.service_date,
            _initial_lift_availability(setup.cancelled_lift_times, manual_counts),
        )
        base_idempotency_key = setup.idempotency_key(self._settings)
        idempotency_key = (
            f"{base_idempotency_key}:manual:{uuid4()}" if allow_duplicate else base_idempotency_key
        )

        async with self._session_factory() as session:
            active_statuses = (
                ACTIVE_POLL_BATCH_STATUSES if allow_duplicate else ACTIVE_BATCH_STATUSES
            )
            active_batches = await self._active_batches_for_setups(
                session=session,
                setups=(setup,),
                statuses=active_statuses,
            )
            if active_batches:
                raise DuplicatePollError("Poll batch already exists for this setup.")

            existing = None
            if not allow_duplicate:
                existing = await session.scalar(
                    select(PollBatch).where(PollBatch.idempotency_key == idempotency_key)
                )
            if await self._is_active_batch(session=session, batch=existing):
                raise DuplicatePollError("Poll batch already exists for this setup.")

            if existing and existing.status in REUSABLE_BATCH_STATUSES:
                batch = existing
                batch.status = "posting"
                batch.created_by_user_id = setup.created_by_user_id
                batch.first_lift_location = setup.first_lift_location.value
                batch.superseded_by_batch_id = None
                await session.execute(
                    delete(PollMessage).where(PollMessage.batch_id == existing.id)
                )
                await session.execute(
                    delete(PollOptionSnapshot).where(PollOptionSnapshot.batch_id == existing.id)
                )
            else:
                batch = PollBatch(
                    environment=self._settings.app_env,
                    chat_id=self._settings.telegram_target_chat_id,
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=setup.service_date,
                    service_day=setup.service_date.strftime("%A").lower(),
                    created_by_user_id=setup.created_by_user_id,
                    first_lift_location=setup.first_lift_location.value,
                    status="posting",
                    idempotency_key=idempotency_key,
                )
                session.add(batch)
            # A fresh poll for this date starts with a clean slate: drop lift
            # cancellations and the record of which notices already went out, or a
            # re-posted day would never announce itself again.
            await session.execute(
                delete(CancelledLift)
                .where(CancelledLift.environment == self._settings.app_env)
                .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                .where(CancelledLift.service_date == setup.service_date)
            )
            await session.execute(
                delete(LiftSignalState)
                .where(LiftSignalState.environment == self._settings.app_env)
                .where(LiftSignalState.chat_id == self._settings.telegram_target_chat_id)
                .where(LiftSignalState.thread_id == self._settings.telegram_target_thread_id)
                .where(LiftSignalState.service_date == setup.service_date)
            )
            await session.execute(
                delete(ServiceDayNotice)
                .where(ServiceDayNotice.environment == self._settings.app_env)
                .where(ServiceDayNotice.chat_id == self._settings.telegram_target_chat_id)
                .where(ServiceDayNotice.thread_id == self._settings.telegram_target_thread_id)
                .where(ServiceDayNotice.service_date == setup.service_date)
            )
            try:
                await session.flush()
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise DuplicatePollError("Poll batch already exists for this setup.") from error

            notice_message: SentTextMessage | None = None
            availability_message: SentTextMessage | None = None
            try:
                if include_notice:
                    notice_message = await self._telegram_client.send_text(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_thread_id=self._settings.telegram_target_thread_id,
                        text=render_poll_notice(
                            setup.first_lift_location,
                            terms=await self._payment_terms(),
                        ),
                        parse_mode=PAYMENT_TERMS_PARSE_MODE,
                    )
                availability_message = await self._telegram_client.send_text(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_thread_id=self._settings.telegram_target_thread_id,
                    text=initial_availability,
                    parse_mode=AVAILABILITY_PARSE_MODE,
                )
                sent_message = await self._telegram_client.send_poll(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_thread_id=self._settings.telegram_target_thread_id,
                    draft=draft,
                )
            except Exception:
                batch.status = "failed"
                await session.commit()
                for text_message in (availability_message, notice_message):
                    if text_message is None:
                        continue
                    await self._telegram_client.delete_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=text_message.message_id,
                    )
                raise

            pinned = False
            try:
                if self._settings.telegram_pin_poll and pin_after_send:
                    pinned = await self._telegram_client.pin_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=sent_message.message_id,
                    )
                batch.status = "posted"
                if notice_message is not None:
                    session.add(
                        PollMessage(
                            batch_id=batch.id,
                            telegram_message_id=notice_message.message_id,
                            poll_id=None,
                            message_kind="notice",
                            pinned=False,
                            cleanup_status="not_applicable",
                        )
                    )
                session.add(
                    PollMessage(
                        batch_id=batch.id,
                        telegram_message_id=availability_message.message_id,
                        poll_id=None,
                        message_kind="availability",
                        pinned=False,
                        cleanup_status="not_applicable",
                    )
                )
                message = PollMessage(
                    batch_id=batch.id,
                    telegram_message_id=sent_message.message_id,
                    poll_id=sent_message.poll_id,
                    message_kind="poll",
                    pinned=pinned,
                    cleanup_status="not_applicable",
                )
                session.add(message)
                if sent_message.poll_id is not None:
                    session.add_all(
                        _option_snapshots(
                            batch_id=batch.id,
                            telegram_message_id=sent_message.message_id,
                            poll_id=sent_message.poll_id,
                            draft=draft,
                        )
                    )
                await session.commit()
            except Exception:
                await session.rollback()
                await _mark_batch_status(
                    session=session,
                    batch_id=batch.id,
                    status="sent_unconfirmed",
                )
                raise

            result = PollCreationResult(
                batch_id=batch.id,
                message_id=sent_message.message_id,
                availability_message_id=availability_message.message_id,
                poll_id=sent_message.poll_id,
                pinned=pinned,
                notice_message_id=notice_message.message_id if notice_message is not None else None,
            )
            if pinned:
                await self._unpin_older_poll_messages(excluded_batch_ids=(batch.id,))
            await self._refresh_booking_monitors()
            return result

    async def track_poll_answer(
        self,
        *,
        poll_id: str,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        option_ids: tuple[int, ...],
    ) -> None:
        now = datetime.now(UTC)
        encoded_option_ids = _encode_option_ids(option_ids)
        event: PollVoteEvent | None = None
        async with self._session_factory() as session:
            poll_snapshot = await _poll_snapshot(session=session, poll_id=poll_id)
            vote = await session.scalar(
                select(PollVote)
                .where(PollVote.poll_id == poll_id)
                .where(PollVote.telegram_user_id == telegram_user_id)
            )
            old_option_ids = vote.option_ids if vote is not None else ""
            if old_option_ids != encoded_option_ids:
                event = _vote_event(
                    poll_snapshot=poll_snapshot,
                    poll_id=poll_id,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    old_option_ids=old_option_ids,
                    new_option_ids=encoded_option_ids,
                    created_at=now,
                )
                session.add(event)
            option_booked_at = option_times_after_vote(
                previous=vote.option_booked_at if vote is not None else "",
                previous_option_ids=decode_option_ids(old_option_ids),
                previous_updated_at=vote.updated_at if vote is not None else None,
                option_ids=option_ids,
                now=now,
            )
            if vote is None:
                session.add(
                    PollVote(
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        option_ids=encoded_option_ids,
                        option_booked_at=option_booked_at,
                        updated_at=now,
                    )
                )
            else:
                vote.username = username
                vote.full_name = full_name
                vote.option_ids = encoded_option_ids
                vote.option_booked_at = option_booked_at
                vote.updated_at = now
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                await self._update_existing_poll_vote(
                    poll_id=poll_id,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    option_ids=encoded_option_ids,
                    updated_at=now,
                )
                await self._refresh_availability(poll_id)
                await self._refresh_booking_monitors()
                return

        if event is None:
            _log_vote_noop(
                poll_id=poll_id,
                telegram_user_id=telegram_user_id,
                username=username,
                option_ids=encoded_option_ids,
            )
        else:
            _log_vote_event(event)
            await self._refresh_availability(poll_id)
            await self._refresh_booking_monitors()

    async def _refresh_availability(self, poll_id: str) -> None:
        lock = self._availability_locks.setdefault(poll_id, Lock())
        async with lock:
            await self._refresh_availability_locked(poll_id)

    async def _refresh_availability_locked(self, poll_id: str) -> None:
        async with self._session_factory() as session:
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot)
                    .where(PollOptionSnapshot.poll_id == poll_id)
                    .order_by(PollOptionSnapshot.option_index)
                )
            ).all()
            if not snapshots:
                return

            batch = await session.get(PollBatch, snapshots[0].batch_id)
            availability_message = await session.scalar(
                select(PollMessage)
                .where(PollMessage.batch_id == snapshots[0].batch_id)
                .where(PollMessage.message_kind == "availability")
            )
            if batch is None or availability_message is None:
                return

            if batch.status == "cancelled":
                # The poll is still votable in Telegram, so a late vote must not
                # resurrect the live board for a day that was cancelled.
                await self._telegram_client.edit_text(
                    chat_id=batch.chat_id,
                    message_id=availability_message.telegram_message_id,
                    text=_cancelled_day_board(batch.service_date),
                    parse_mode=AVAILABILITY_PARSE_MODE,
                )
                return

            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id == poll_id))
            ).all()
            manual_bookings = (
                await session.scalars(
                    select(ManualBookingCount)
                    .where(ManualBookingCount.environment == self._settings.app_env)
                    .where(ManualBookingCount.chat_id == batch.chat_id)
                    .where(ManualBookingCount.thread_id == batch.thread_id)
                    .where(ManualBookingCount.service_date == batch.service_date)
                )
            ).all()
            manual_by_time = {booking.lift_time: booking.count for booking in manual_bookings}
            guest_rows = (
                await session.scalars(
                    select(GuestSeat)
                    .where(GuestSeat.environment == self._settings.app_env)
                    .where(GuestSeat.chat_id == batch.chat_id)
                    .where(GuestSeat.thread_id == batch.thread_id)
                    .where(GuestSeat.service_date == batch.service_date)
                )
            ).all()
            guests_by_time: dict[str, int] = {}
            # Named parties, not just a total: the board says whose the extra seats
            # are, so a rider counting poll votes can see where the gap came from.
            guest_parties_by_time: dict[str, list[GuestParty]] = {}
            label_by_user = {vote.telegram_user_id: _rider_label(vote) for vote in votes}
            for row in sorted(guest_rows, key=lambda row: (_as_utc(row.updated_at), row.id)):
                if row.count <= 0:
                    continue
                guests_by_time[row.lift_time] = guests_by_time.get(row.lift_time, 0) + row.count
                guest_parties_by_time.setdefault(row.lift_time, []).append(
                    GuestParty(
                        telegram_user_id=row.host_user_id,
                        # A host who withdrew their own vote keeps the guest seat they
                        # paid for, and then there is no vote left to name them from.
                        label=label_by_user.get(row.host_user_id, "Rider"),
                        count=row.count,
                    )
                )
            votes_by_option: dict[int, list[PollVote]] = {}
            for vote in votes:
                for option_id in decode_option_ids(vote.option_ids):
                    votes_by_option.setdefault(option_id, []).append(vote)
            cancelled_times = await self._cancelled_lift_times(
                session=session,
                service_date=batch.service_date,
            )
            counts: dict[int, int] = {}
            for vote in votes:
                for option_id in decode_option_ids(vote.option_ids):
                    counts[option_id] = counts.get(option_id, 0) + 1

            capacity_by_time = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}
            availability = tuple(
                LiftAvailability(
                    time=snapshot.lift_time,
                    seat_count=(
                        counts.get(snapshot.option_index, 0)
                        + manual_by_time.get(snapshot.lift_time, 0)
                        + guests_by_time.get(snapshot.lift_time, 0)
                    ),
                    capacity=capacity_by_time.get(snapshot.lift_time, 10),
                    manual_count=manual_by_time.get(snapshot.lift_time, 0),
                    guests=tuple(guest_parties_by_time.get(snapshot.lift_time, ())),
                    cancelled=snapshot.lift_time in cancelled_times,
                    waitlist=(
                        ()
                        if snapshot.lift_time in cancelled_times
                        else _waitlist_for(
                            votes_by_option.get(snapshot.option_index, []),
                            option_index=snapshot.option_index,
                            capacity=capacity_by_time.get(snapshot.lift_time, 10),
                            reserved=manual_by_time.get(snapshot.lift_time, 0)
                            + guests_by_time.get(snapshot.lift_time, 0),
                        )
                    ),
                )
                for snapshot in snapshots
                if snapshot.lift_time is not None
            )
            text = render_availability_status(batch.service_date, availability)
            chat_id = batch.chat_id
            thread_id = batch.thread_id
            batch_id = batch.id
            availability_message_id = availability_message.telegram_message_id

        updated = await self._telegram_client.edit_text(
            chat_id=chat_id,
            message_id=availability_message_id,
            text=text,
            parse_mode=AVAILABILITY_PARSE_MODE,
        )
        if updated:
            return

        if await self._telegram_client.message_exists(
            chat_id=chat_id,
            message_id=availability_message_id,
        ):
            logger.warning(
                "poll_availability_update_failed poll_id=%s message_id=%s",
                poll_id,
                availability_message_id,
                extra={"poll_id": poll_id, "message_id": availability_message_id},
            )
            return

        try:
            replacement = await self._telegram_client.send_text(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode=AVAILABILITY_PARSE_MODE,
            )
        except Exception:
            logger.exception(
                "poll_availability_recovery_failed poll_id=%s old_message_id=%s",
                poll_id,
                availability_message_id,
                extra={"poll_id": poll_id, "message_id": availability_message_id},
            )
            return
        async with self._session_factory() as session:
            await session.execute(
                update(PollMessage)
                .where(PollMessage.batch_id == batch_id)
                .where(PollMessage.message_kind == "availability")
                .values(
                    telegram_message_id=replacement.message_id,
                    cleanup_status="not_applicable",
                )
            )
            await session.commit()

    async def recreate_polls(
        self,
        setups: tuple[PollSetup, ...],
    ) -> RecreatePollsResult:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to post polls."
            raise ValueError(msg)

        old_statuses: dict[int, str] = {}
        async with self._session_factory() as session:
            conflicts = await self._active_batches_for_setups(session=session, setups=setups)
            if not conflicts:
                raise DuplicatePollError("No active polls to recreate.")
            for batch in conflicts:
                old_statuses[batch.id] = batch.status
                batch.status = "cleanup_pending"
            await session.commit()
        logger.info(
            "poll_recreate_started old_batch_ids=%s service_dates=%s",
            ",".join(str(batch_id) for batch_id in old_statuses),
            ",".join(setup.service_date.isoformat() for setup in setups),
            extra={
                "old_batch_ids": tuple(old_statuses),
                "service_dates": tuple(setup.service_date.isoformat() for setup in setups),
            },
        )

        created: list[PollCreationResult] = []
        try:
            for index, setup in enumerate(setups):
                created.append(
                    await self.create_poll(
                        setup,
                        allow_duplicate=True,
                        include_notice=index == 0,
                        pin_after_send=False,
                    )
                )
            created = list(await self.pin_created_results(tuple(created)))
        except Exception:
            logger.exception(
                "poll_recreate_failed old_batch_ids=%s created_batch_ids=%s",
                ",".join(str(batch_id) for batch_id in old_statuses),
                ",".join(str(result.batch_id) for result in created),
                extra={
                    "old_batch_ids": tuple(old_statuses),
                    "created_batch_ids": tuple(result.batch_id for result in created),
                },
            )
            await self._rollback_created_replacements(tuple(created))
            await self._restore_replacing_batches(old_statuses)
            raise

        old_batch_ids = tuple(old_statuses)
        report_text = await self.render_vote_report(old_batch_ids)
        replacement_by_date = {
            setup.service_date: result.batch_id
            for setup, result in zip(setups, created, strict=True)
        }
        replaced_dates = tuple(setup.service_date for setup in setups)
        logger.info(
            "poll_recreate_created old_batch_ids=%s new_batch_ids=%s",
            ",".join(str(batch_id) for batch_id in old_batch_ids),
            ",".join(str(result.batch_id) for result in created),
            extra={
                "old_batch_ids": old_batch_ids,
                "new_batch_ids": tuple(result.batch_id for result in created),
            },
        )
        return RecreatePollsResult(
            created=tuple(created),
            report_text=report_text,
            old_batch_ids=old_batch_ids,
            replacement_by_date=replacement_by_date,
            replaced_dates=replaced_dates,
        )

    async def cleanup_recreated_polls(self, result: RecreatePollsResult) -> CleanupResult:
        return await self._cleanup_recreated_batches(
            old_batch_ids=result.old_batch_ids,
            replacement_by_date=result.replacement_by_date,
            # Recreate posts a fresh route/payment notice, so the old one is a
            # duplicate. Keep it only if no replacement was posted.
            replacement_has_notice=any(
                item.notice_message_id is not None for item in result.created
            ),
        )

    async def render_vote_report(self, batch_ids: tuple[int, ...]) -> str:
        if not batch_ids:
            return "No existing polls were replaced."

        async with self._session_factory() as session:
            batches = (
                await session.scalars(select(PollBatch).where(PollBatch.id.in_(batch_ids)))
            ).all()
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot).where(PollOptionSnapshot.batch_id.in_(batch_ids))
                )
            ).all()
            poll_ids = tuple({snapshot.poll_id for snapshot in snapshots})
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))
            ).all()

        if not snapshots:
            return (
                "Recreated existing polls.\n\nNo tracked votes; the poll may predate vote tracking."
            )

        options_by_poll = _options_by_poll_id(snapshots)
        votes_by_poll: dict[str, list[PollVote]] = {}
        for vote in votes:
            if decode_option_ids(vote.option_ids):
                votes_by_poll.setdefault(vote.poll_id, []).append(vote)

        lines = ["Recreated existing polls.", "", "Tracked votes before recreate:"]
        any_vote = False
        for batch in sorted(batches, key=lambda item: item.service_date):
            batch_snapshots = [snapshot for snapshot in snapshots if snapshot.batch_id == batch.id]
            lines.append(f"\n{batch.service_date:%Y-%m-%d}:")
            if not batch_snapshots:
                lines.append("- No tracked options for this poll.")
                continue
            poll_id = batch_snapshots[0].poll_id
            poll_votes = votes_by_poll.get(poll_id, [])
            if not poll_votes:
                lines.append("- No tracked votes; the poll may predate vote tracking.")
                continue
            grouped: dict[int, list[str]] = {}
            for vote in poll_votes:
                rider = _rider_label(vote)
                for option_id in decode_option_ids(vote.option_ids):
                    grouped.setdefault(option_id, []).append(rider)
            for option_id, riders in sorted(grouped.items()):
                option = options_by_poll.get(poll_id, {}).get(option_id)
                if option is not None and option.lift_time is None:
                    continue
                option_label = option.label if option else f"Option {option_id}"
                lines.append(f"- {option_label}: {', '.join(sorted(riders))}")
                any_vote = True

        if not any_vote:
            lines.append("\nNo tracked rider votes were found.")
        return "\n".join(lines)

    async def pin_created_poll(self, result: PollCreationResult) -> PollCreationResult:
        pinned_result = await self._pin_created_poll_only(result)
        if pinned_result.pinned:
            await self._unpin_older_poll_messages(excluded_batch_ids=(result.batch_id,))
        return pinned_result

    async def _pin_created_poll_only(self, result: PollCreationResult) -> PollCreationResult:
        if self._settings.telegram_target_chat_id is None or not self._settings.telegram_pin_poll:
            return result

        pinned = await self._telegram_client.pin_message(
            chat_id=self._settings.telegram_target_chat_id,
            message_id=result.message_id,
        )
        if pinned:
            async with self._session_factory() as session:
                await session.execute(
                    update(PollMessage)
                    .where(PollMessage.batch_id == result.batch_id)
                    .where(PollMessage.telegram_message_id == result.message_id)
                    .values(pinned=True)
                )
                await session.commit()
        return PollCreationResult(
            batch_id=result.batch_id,
            message_id=result.message_id,
            availability_message_id=result.availability_message_id,
            poll_id=result.poll_id,
            pinned=pinned,
            notice_message_id=result.notice_message_id,
        )

    async def pin_created_results(
        self, results: tuple[PollCreationResult, ...]
    ) -> tuple[PollCreationResult, ...]:
        pinned_results = tuple([await self._pin_created_poll_only(result) for result in results])
        if any(result.pinned for result in pinned_results):
            await self._unpin_older_poll_messages(
                excluded_batch_ids=tuple(result.batch_id for result in results)
            )
        if results:
            await self.reopen_booking_monitors()
        return pinned_results

    async def reopen_booking_monitors(self) -> None:
        """Re-post every admin's monitor so a new weekend arrives at the bottom.

        Editing in place would leave the monitor buried wherever it was last
        opened, which is how admins missed it before. Only admins who have opened
        it at least once can be reached: Telegram refuses a private message to
        anyone who never started a chat with the bot.
        """
        rows = await self._booking_monitor_rows()
        if not rows:
            return
        days = await self._booking_monitor_days()
        for row in rows:
            draft = await self._monitor_draft(
                days,
                selected_service_date=days[0].service_date if days else None,
            )
            await self._telegram_client.delete_message(
                chat_id=row.private_chat_id,
                message_id=row.telegram_message_id,
            )
            try:
                sent = await self._telegram_client.send_text(
                    chat_id=row.private_chat_id,
                    message_thread_id=None,
                    text=draft.text,
                    reply_markup=draft.reply_markup,
                )
            except Exception:
                logger.exception(
                    "booking_monitor_reopen_failed admin_user_id=%s",
                    row.admin_user_id,
                    extra={"admin_user_id": row.admin_user_id},
                )
                continue
            await self._store_booking_monitor(
                admin_user_id=row.admin_user_id,
                private_chat_id=row.private_chat_id,
                telegram_message_id=sent.message_id,
                selected_service_date=days[0].service_date if days else None,
            )

    async def _unpin_older_poll_messages(self, *, excluded_batch_ids: tuple[int, ...]) -> None:
        if self._settings.telegram_target_chat_id is None:
            return

        async with self._session_factory() as session:
            query = (
                select(PollMessage)
                .join(PollBatch, PollBatch.id == PollMessage.batch_id)
                .where(PollBatch.environment == self._settings.app_env)
                .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                .where(PollMessage.message_kind == "poll")
                .where(PollMessage.pinned.is_(True))
            )
            if excluded_batch_ids:
                query = query.where(PollMessage.batch_id.not_in(excluded_batch_ids))
            messages = (await session.scalars(query)).all()

            for message in messages:
                unpinned = await self._telegram_client.unpin_message(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_id=message.telegram_message_id,
                )
                if unpinned:
                    message.pinned = False
                else:
                    logger.warning(
                        "poll_old_pin_retirement_failed batch_id=%s message_id=%s",
                        message.batch_id,
                        message.telegram_message_id,
                    )
            await session.commit()

    async def _booking_monitor_row(
        self,
        *,
        session: AsyncSession,
        admin_user_id: int,
    ) -> AdminBookingMonitor | None:
        return await session.scalar(
            select(AdminBookingMonitor)
            .where(AdminBookingMonitor.environment == self._settings.app_env)
            .where(AdminBookingMonitor.chat_id == self._settings.telegram_target_chat_id)
            .where(AdminBookingMonitor.thread_id == self._settings.telegram_target_thread_id)
            .where(AdminBookingMonitor.admin_user_id == admin_user_id)
        )

    async def _booking_monitor_rows(self) -> tuple[AdminBookingMonitor, ...]:
        if self._settings.telegram_target_chat_id is None:
            return ()
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(AdminBookingMonitor)
                    .where(AdminBookingMonitor.environment == self._settings.app_env)
                    .where(AdminBookingMonitor.chat_id == self._settings.telegram_target_chat_id)
                    .where(
                        AdminBookingMonitor.thread_id == self._settings.telegram_target_thread_id
                    )
                )
            ).all()
        return tuple(rows)

    async def _store_booking_monitor(
        self,
        *,
        admin_user_id: int,
        private_chat_id: int,
        telegram_message_id: int,
        selected_service_date: date | None,
        reposted_for: date | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            monitor = await self._booking_monitor_row(
                session=session,
                admin_user_id=admin_user_id,
            )
            if monitor is None:
                session.add(
                    AdminBookingMonitor(
                        environment=self._settings.app_env,
                        chat_id=self._settings.telegram_target_chat_id,
                        thread_id=self._settings.telegram_target_thread_id,
                        admin_user_id=admin_user_id,
                        private_chat_id=private_chat_id,
                        telegram_message_id=telegram_message_id,
                        selected_service_date=selected_service_date,
                        reposted_for=reposted_for,
                        updated_at=now,
                    )
                )
            else:
                monitor.private_chat_id = private_chat_id
                monitor.telegram_message_id = telegram_message_id
                monitor.selected_service_date = selected_service_date
                if reposted_for is not None:
                    monitor.reposted_for = reposted_for
                monitor.updated_at = now
            await session.commit()

    async def repost_daily_monitors(self, *, now: datetime | None = None) -> int:
        """On a lift-day morning, put each admin's monitor back at the bottom.

        The card is edited in place all week, so by Sunday breakfast it sits above
        days of other messages — exactly when an admin needs it most. Once per lift
        day, and only from `MONITOR_REPOST_AT`: the point is to have it in hand
        before the first van, not to ping anybody overnight.
        """
        if self._settings.telegram_target_chat_id is None:
            return 0
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        if moment.time() < MONITOR_REPOST_AT:
            return 0
        days = await self._booking_monitor_days()
        today = moment.date()
        if today not in {day.service_date for day in days}:
            return 0

        async with self._session_factory() as session:
            monitors = (
                await session.scalars(
                    select(AdminBookingMonitor)
                    .where(AdminBookingMonitor.environment == self._settings.app_env)
                    .where(AdminBookingMonitor.chat_id == self._settings.telegram_target_chat_id)
                    .where(
                        AdminBookingMonitor.thread_id == self._settings.telegram_target_thread_id
                    )
                    .where(
                        or_(
                            AdminBookingMonitor.reposted_for.is_(None),
                            AdminBookingMonitor.reposted_for != today,
                        )
                    )
                )
            ).all()
        if not monitors:
            return 0

        draft = await self._monitor_draft(days, selected_service_date=today)
        reposted = 0
        for monitor in monitors:
            try:
                sent = await self._telegram_client.send_text(
                    chat_id=monitor.private_chat_id,
                    message_thread_id=None,
                    text=draft.text,
                    reply_markup=draft.reply_markup,
                )
            except Exception:
                logger.exception(
                    "booking_monitor_repost_failed admin_user_id=%s",
                    monitor.admin_user_id,
                    extra={"admin_user_id": monitor.admin_user_id},
                )
                continue
            # Posted before the old card goes, so the admin is never left without
            # one; the stale card would otherwise keep live buttons over old counts.
            await self._telegram_client.delete_message(
                chat_id=monitor.private_chat_id,
                message_id=monitor.telegram_message_id,
            )
            await self._store_booking_monitor(
                admin_user_id=monitor.admin_user_id,
                private_chat_id=monitor.private_chat_id,
                telegram_message_id=sent.message_id,
                selected_service_date=today,
                reposted_for=today,
            )
            reposted += 1
        if reposted:
            logger.info(
                "booking_monitor_reposted service_date=%s monitors=%s",
                today.isoformat(),
                reposted,
                extra={"service_date": today.isoformat(), "monitors": reposted},
            )
        return reposted

    async def _refresh_booking_monitors(self) -> None:
        if self._settings.telegram_target_chat_id is None:
            return
        async with self._session_factory() as session:
            monitors = (
                await session.scalars(
                    select(AdminBookingMonitor)
                    .where(AdminBookingMonitor.environment == self._settings.app_env)
                    .where(AdminBookingMonitor.chat_id == self._settings.telegram_target_chat_id)
                    .where(
                        AdminBookingMonitor.thread_id == self._settings.telegram_target_thread_id
                    )
                )
            ).all()
        if not monitors:
            return

        days = await self._booking_monitor_days()
        available_dates = {day.service_date for day in days}
        today = datetime.now(UTC).astimezone(self._zone).date()
        for monitor in monitors:
            selected_date = monitor.selected_service_date
            if selected_date not in available_dates:
                # Today first: on a lift day that is the tab the admin wants. The
                # stored tab is left alone while it still exists, so the card does
                # not jump under them while they are reading another day.
                selected_date = _preferred_monitor_date(days, today=today)
            draft = await self._monitor_draft(days, selected_service_date=selected_date)
            try:
                updated = await self._telegram_client.edit_text(
                    chat_id=monitor.private_chat_id,
                    message_id=monitor.telegram_message_id,
                    text=draft.text,
                    reply_markup=draft.reply_markup,
                )
                message_id = monitor.telegram_message_id
                if not updated:
                    sent = await self._telegram_client.send_text(
                        chat_id=monitor.private_chat_id,
                        message_thread_id=None,
                        text=draft.text,
                        reply_markup=draft.reply_markup,
                    )
                    message_id = sent.message_id
                await self._store_booking_monitor(
                    admin_user_id=monitor.admin_user_id,
                    private_chat_id=monitor.private_chat_id,
                    telegram_message_id=message_id,
                    selected_service_date=selected_date,
                )
            except Exception:
                logger.exception(
                    "booking_monitor_refresh_failed admin_user_id=%s",
                    monitor.admin_user_id,
                )

    async def _booking_monitor_days(self) -> tuple[BookingMonitorDay, ...]:
        if self._settings.telegram_target_chat_id is None:
            return ()
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.status == "posted")
                    .order_by(PollBatch.service_date, PollBatch.id)
                )
            ).all()
            latest_by_date = {batch.service_date: batch for batch in batches}
            all_dates = sorted(latest_by_date)
            cutoff = datetime.now(UTC).date() - timedelta(days=1)
            active_dates = [service_date for service_date in all_dates if service_date >= cutoff]
            # Only current/upcoming lifts; never fall back to past weekends.
            selected_dates = tuple(active_dates[:2])
            selected_batches = tuple(
                latest_by_date[service_date] for service_date in selected_dates
            )
            if not selected_batches:
                return ()

            batch_ids = tuple(batch.id for batch in selected_batches)
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
            manual_bookings = (
                await session.scalars(
                    select(ManualBookingCount)
                    .where(ManualBookingCount.environment == self._settings.app_env)
                    .where(ManualBookingCount.chat_id == self._settings.telegram_target_chat_id)
                    .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
                    .where(ManualBookingCount.service_date.in_(selected_dates))
                )
            ).all()
            cancelled = (
                await session.scalars(
                    select(CancelledLift)
                    .where(CancelledLift.environment == self._settings.app_env)
                    .where(CancelledLift.chat_id == self._settings.telegram_target_chat_id)
                    .where(CancelledLift.thread_id == self._settings.telegram_target_thread_id)
                    .where(CancelledLift.service_date.in_(selected_dates))
                )
            ).all()
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._settings.telegram_target_chat_id)
                    .where(PaymentClaim.service_date.in_(selected_dates))
                )
            ).all()
            guest_seats = (
                await session.scalars(
                    select(GuestSeat)
                    .where(GuestSeat.environment == self._settings.app_env)
                    .where(GuestSeat.chat_id == self._settings.telegram_target_chat_id)
                    .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                    .where(GuestSeat.service_date.in_(selected_dates))
                )
            ).all()
            deadline_rows = (
                await session.scalars(
                    select(DeadlineRoster)
                    .where(DeadlineRoster.environment == self._settings.app_env)
                    .where(DeadlineRoster.chat_id == self._settings.telegram_target_chat_id)
                    .where(DeadlineRoster.thread_id == self._settings.telegram_target_thread_id)
                    .where(DeadlineRoster.service_date.in_(selected_dates))
                )
            ).all()
            vote_events = (
                await session.scalars(
                    select(PollVoteEvent)
                    .where(PollVoteEvent.poll_id.in_(poll_ids))
                    .order_by(PollVoteEvent.created_at)
                )
            ).all()
            has_refund_reports = (
                await session.scalar(
                    select(RefundReport.id)
                    .where(RefundReport.environment == self._settings.app_env)
                    .where(RefundReport.chat_id == self._settings.telegram_target_chat_id)
                    .limit(1)
                )
            ) is not None

        votes_by_poll: dict[str, list[PollVote]] = {}
        for vote in votes:
            votes_by_poll.setdefault(vote.poll_id, []).append(vote)
        manual_by_date_time = {
            (booking.service_date, booking.lift_time): booking.count for booking in manual_bookings
        }
        cancelled_date_time = {(row.service_date, row.lift_time) for row in cancelled}
        guests_by_date_time: dict[tuple[date, str], int] = {}
        for guest in guest_seats:
            key = (guest.service_date, guest.lift_time)
            guests_by_date_time[key] = guests_by_date_time.get(key, 0) + guest.count
        capacity_by_time = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}
        today = datetime.now(UTC).astimezone(self._zone).date()
        days: list[BookingMonitorDay] = []
        for batch in selected_batches:
            batch_snapshots = [snapshot for snapshot in snapshots if snapshot.batch_id == batch.id]
            batch_votes = (
                votes_by_poll.get(batch_snapshots[0].poll_id, []) if batch_snapshots else []
            )
            day_claims = [claim for claim in claims if claim.service_date == batch.service_date]
            claim_by_user = {claim.telegram_user_id: claim for claim in day_claims}
            day_guests = [row for row in guest_seats if row.service_date == batch.service_date]
            day_deadline_rows = [
                row for row in deadline_rows if row.service_date == batch.service_date
            ]
            roster_running_times = {
                lift_time
                for lift_time in {row.lift_time for row in day_deadline_rows}
                if sum(row.seats for row in day_deadline_rows if row.lift_time == lift_time)
                >= MINIMUM_RIDERS
            }
            label_by_user = {vote.telegram_user_id: _rider_label(vote) for vote in batch_votes}
            label_by_user.update(
                {
                    claim.telegram_user_id: _rider_label_from_parts(claim.username, claim.full_name)
                    for claim in day_claims
                }
            )
            label_by_user.update(
                {
                    row.telegram_user_id: row.label
                    for row in deadline_rows
                    if row.service_date == batch.service_date and row.telegram_user_id
                }
            )
            lifts: list[BookingLiftStatus] = []
            held_lifts_by_user: dict[int, list[str]] = {}
            waitlist: list[MonitorWaitlistRider] = []
            owed_seats_by_user: dict[int, int] = {}
            for snapshot in batch_snapshots:
                if snapshot.lift_time is None:
                    continue
                lift_votes = [
                    vote
                    for vote in votes_by_poll.get(snapshot.poll_id, [])
                    if snapshot.option_index in decode_option_ids(vote.option_ids)
                ]
                manual_count = manual_by_date_time.get((batch.service_date, snapshot.lift_time), 0)
                guest_count = guests_by_date_time.get((batch.service_date, snapshot.lift_time), 0)
                capacity = capacity_by_time.get(snapshot.lift_time, 10)
                cancelled_lift = (batch.service_date, snapshot.lift_time) in cancelled_date_time
                lift = BookingLiftStatus(
                    time=snapshot.lift_time,
                    vote_count=len(lift_votes),
                    manual_count=manual_count,
                    guest_count=guest_count,
                    capacity=capacity,
                    cancelled=cancelled_lift,
                    running_locked=snapshot.lift_time in roster_running_times,
                )
                lifts.append(lift)
                if cancelled_lift:
                    continue
                allocation = allocate_seats(
                    (
                        SeatCandidate(
                            telegram_user_id=vote.telegram_user_id,
                            label=_rider_label(vote),
                            booked_at=vote_booked_at(vote, snapshot.option_index),
                        )
                        for vote in lift_votes
                    ),
                    capacity=capacity,
                    reserved=manual_count + guest_count,
                )
                for holder in allocation.holders:
                    held_lifts_by_user.setdefault(holder.telegram_user_id, []).append(
                        snapshot.lift_time
                    )
                    if lift.running:
                        owed_seats_by_user[holder.telegram_user_id] = (
                            owed_seats_by_user.get(holder.telegram_user_id, 0) + 1
                        )
                waitlist.extend(
                    MonitorWaitlistRider(
                        telegram_user_id=rider.telegram_user_id,
                        label=rider.label,
                        lift_time=snapshot.lift_time,
                        position=position,
                    )
                    for position, rider in enumerate(allocation.waitlist, start=1)
                )
                if lift.running:
                    for guest in day_guests:
                        if guest.lift_time == snapshot.lift_time and guest.count > 0:
                            owed_seats_by_user[guest.host_user_id] = (
                                owed_seats_by_user.get(guest.host_user_id, 0) + guest.count
                            )

            unpaid_riders = tuple(
                sorted(
                    (
                        MonitorRider(
                            telegram_user_id=user_id,
                            label=label_by_user.get(user_id, f"Rider {user_id}"),
                            amount_gel=max(owed - (claim.seats if claim is not None else 0), 0)
                            * self._settings.payment_price_gel,
                            lift_times=tuple(held_lifts_by_user.get(user_id, ())),
                        )
                        for user_id, owed in owed_seats_by_user.items()
                        if (claim := claim_by_user.get(user_id)) is None or claim.seats < owed
                    ),
                    key=lambda rider: rider.label,
                )
            )
            cash_pending = tuple(
                sorted(
                    (
                        MonitorRider(
                            telegram_user_id=claim.telegram_user_id,
                            label=label_by_user.get(
                                claim.telegram_user_id,
                                _rider_label_from_parts(claim.username, claim.full_name),
                            ),
                            amount_gel=claim.seats * self._settings.payment_price_gel,
                            lift_times=tuple(held_lifts_by_user.get(claim.telegram_user_id, ())),
                        )
                        for claim in day_claims
                        if claim.method == "cash" and claim.verified_by_user_id is None
                    ),
                    key=lambda rider: rider.label,
                )
            )
            monitor_guests = tuple(
                MonitorGuest(
                    host_user_id=guest.host_user_id,
                    host_label=label_by_user.get(guest.host_user_id, f"Rider {guest.host_user_id}"),
                    lift_time=guest.lift_time,
                    count=guest.count,
                )
                for guest in sorted(
                    day_guests, key=lambda row: (lift_minutes(row.lift_time), row.id)
                )
                if guest.count > 0
            )
            late_exits = _late_monitor_exits(
                service_date=batch.service_date,
                snapshots=batch_snapshots,
                votes=batch_votes,
                roster_rows=deadline_rows,
                events=vote_events,
                zone=self._zone,
            )
            booked_user_ids = set(held_lifts_by_user) | {
                guest.host_user_id for guest in day_guests if guest.count > 0
            }
            days.append(
                BookingMonitorDay(
                    service_date=batch.service_date,
                    lifts=tuple(lifts),
                    paid_rider_count=sum(user_id in claim_by_user for user_id in booked_user_ids),
                    booked_rider_count=len(booked_user_ids),
                    expected_gel=sum(claim.seats for claim in day_claims)
                    * self._settings.payment_price_gel,
                    # Seats on lifts that are actually running, capped at capacity:
                    # nobody on a waitlist is billed, so nobody on one is owed for.
                    owed_gel=sum(
                        min(lift.total_count, lift.capacity) for lift in lifts if lift.running
                    )
                    * self._settings.payment_price_gel,
                    past=batch.service_date < today,
                    has_refund_reports=has_refund_reports,
                    unpaid_riders=unpaid_riders,
                    cash_pending=cash_pending,
                    guests=monitor_guests,
                    waitlist=tuple(waitlist),
                    late_exits=late_exits,
                )
            )
        return tuple(days)

    async def _active_snapshot_for_lift(
        self,
        *,
        session: AsyncSession,
        service_date: date,
        lift_time: str,
    ) -> PollOptionSnapshot | None:
        return await session.scalar(
            select(PollOptionSnapshot)
            .join(PollBatch, PollBatch.id == PollOptionSnapshot.batch_id)
            .where(PollBatch.environment == self._settings.app_env)
            .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
            .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
            .where(PollBatch.service_date == service_date)
            .where(PollBatch.status == "posted")
            .where(PollOptionSnapshot.lift_time == lift_time)
            .order_by(PollBatch.id.desc())
        )

    async def _manual_booking_count(
        self,
        *,
        session: AsyncSession,
        service_date: date,
        lift_time: str,
    ) -> int:
        count = await session.scalar(
            select(ManualBookingCount.count)
            .where(ManualBookingCount.environment == self._settings.app_env)
            .where(ManualBookingCount.chat_id == self._settings.telegram_target_chat_id)
            .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
            .where(ManualBookingCount.service_date == service_date)
            .where(ManualBookingCount.lift_time == lift_time)
        )
        return count or 0

    async def _manual_booking_counts_for_date(self, service_date: date) -> dict[str, int]:
        if self._settings.telegram_target_chat_id is None:
            return {}
        async with self._session_factory() as session:
            bookings = (
                await session.scalars(
                    select(ManualBookingCount)
                    .where(ManualBookingCount.environment == self._settings.app_env)
                    .where(ManualBookingCount.chat_id == self._settings.telegram_target_chat_id)
                    .where(ManualBookingCount.thread_id == self._settings.telegram_target_thread_id)
                    .where(ManualBookingCount.service_date == service_date)
                )
            ).all()
        return {booking.lift_time: booking.count for booking in bookings}

    async def cleanup_setup_messages(
        self,
        *,
        chat_id: int,
        message_ids: tuple[int, ...],
    ) -> CleanupResult:
        deleted_count = 0
        failed_count = 0
        for message_id in message_ids:
            deleted = await self._telegram_client.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
            if deleted:
                deleted_count += 1
            else:
                failed_count += 1
        return CleanupResult(deleted_count=deleted_count, failed_count=failed_count)

    async def _is_active_batch(self, *, session: AsyncSession, batch: PollBatch | None) -> bool:
        if batch is None:
            return False
        if batch.status in {"posting", "sent_unconfirmed"}:
            return True
        if batch.status not in {"posted", "cleanup_pending", "cleanup_failed"}:
            return False
        if self._settings.telegram_target_chat_id is None:
            return True

        message_ids = (
            await session.scalars(
                select(PollMessage.telegram_message_id)
                .where(PollMessage.batch_id == batch.id)
                .where(PollMessage.message_kind == "poll")
            )
        ).all()
        if not message_ids:
            return True

        for message_id in message_ids:
            started_at = perf_counter()
            if await self._telegram_client.message_exists(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message_id,
            ):
                duration_ms = int((perf_counter() - started_at) * 1000)
                if duration_ms >= 500:
                    logger.warning(
                        "telegram_message_exists_slow duration_ms=%s batch_id=%s message_id=%s",
                        duration_ms,
                        batch.id,
                        message_id,
                        extra={
                            "duration_ms": duration_ms,
                            "batch_id": batch.id,
                            "message_id": message_id,
                        },
                    )
                return True
            duration_ms = int((perf_counter() - started_at) * 1000)
            if duration_ms >= 500:
                logger.warning(
                    "telegram_message_exists_slow duration_ms=%s batch_id=%s message_id=%s",
                    duration_ms,
                    batch.id,
                    message_id,
                    extra={
                        "duration_ms": duration_ms,
                        "batch_id": batch.id,
                        "message_id": message_id,
                    },
                )

        availability_messages = (
            await session.scalars(
                select(PollMessage)
                .where(PollMessage.batch_id == batch.id)
                .where(PollMessage.message_kind == "availability")
            )
        ).all()
        for availability_message in availability_messages:
            deleted = await self._telegram_client.delete_message(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=availability_message.telegram_message_id,
            )
            availability_message.cleanup_status = "deleted" if deleted else "delete_failed"

        await session.execute(
            update(PollMessage)
            .where(PollMessage.batch_id == batch.id)
            .where(PollMessage.message_kind == "poll")
            .values(cleanup_status="telegram_deleted")
        )
        batch.status = "deleted"
        await session.commit()
        return False

    async def _active_batches_for_setups(
        self,
        *,
        session: AsyncSession,
        setups: tuple[PollSetup, ...],
        statuses: tuple[str, ...] = ACTIVE_BATCH_STATUSES,
    ) -> tuple[PollBatch, ...]:
        service_dates = tuple({setup.service_date for setup in setups})
        batches = (
            await session.scalars(
                select(PollBatch)
                .where(PollBatch.environment == self._settings.app_env)
                .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                .where(PollBatch.service_date.in_(service_dates))
                .where(PollBatch.status.in_(statuses))
            )
        ).all()

        active_batches: list[PollBatch] = []
        for batch in batches:
            if await self._is_active_batch(session=session, batch=batch):
                active_batches.append(batch)
        return tuple(active_batches)

    async def _restore_replacing_batches(self, old_statuses: dict[int, str]) -> None:
        async with self._session_factory() as session:
            for batch_id, old_status in old_statuses.items():
                await session.execute(
                    update(PollBatch)
                    .where(PollBatch.id == batch_id)
                    .where(PollBatch.status == "cleanup_pending")
                    .values(status=old_status)
                )
            await session.commit()

    async def _update_existing_poll_vote(
        self,
        *,
        poll_id: str,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        option_ids: str,
        updated_at: datetime,
    ) -> None:
        async with self._session_factory() as session:
            poll_snapshot = await _poll_snapshot(session=session, poll_id=poll_id)
            vote = await session.scalar(
                select(PollVote)
                .where(PollVote.poll_id == poll_id)
                .where(PollVote.telegram_user_id == telegram_user_id)
            )
            old_option_ids = vote.option_ids if vote is not None else ""
            if old_option_ids != option_ids:
                session.add(
                    _vote_event(
                        poll_snapshot=poll_snapshot,
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        old_option_ids=old_option_ids,
                        new_option_ids=option_ids,
                        created_at=updated_at,
                    )
                )
            option_booked_at = option_times_after_vote(
                previous=vote.option_booked_at if vote is not None else "",
                previous_option_ids=decode_option_ids(old_option_ids),
                previous_updated_at=vote.updated_at if vote is not None else None,
                option_ids=decode_option_ids(option_ids),
                now=updated_at,
            )
            if vote is None:
                session.add(
                    PollVote(
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        option_ids=option_ids,
                        option_booked_at=option_booked_at,
                        updated_at=updated_at,
                    )
                )
            else:
                vote.username = username
                vote.full_name = full_name
                vote.option_ids = option_ids
                vote.option_booked_at = option_booked_at
                vote.updated_at = updated_at
            await session.commit()

    async def _rollback_created_replacements(
        self,
        created: tuple[PollCreationResult, ...],
    ) -> None:
        if self._settings.telegram_target_chat_id is None:
            return

        cleanup_failed_batch_ids: set[int] = set()
        cleanup_by_message_id: dict[int, str] = {}
        for result in created:
            message_ids = tuple(
                message_id
                for message_id in (
                    result.notice_message_id,
                    result.availability_message_id,
                    result.message_id,
                )
                if message_id is not None
            )
            for message_id in message_ids:
                deleted = await self._telegram_client.delete_message(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_id=message_id,
                )
                cleanup_by_message_id[message_id] = "deleted" if deleted else "delete_failed"
                if not deleted:
                    cleanup_failed_batch_ids.add(result.batch_id)

        async with self._session_factory() as session:
            for message_id, cleanup_status in cleanup_by_message_id.items():
                await session.execute(
                    update(PollMessage)
                    .where(PollMessage.telegram_message_id == message_id)
                    .values(cleanup_status=cleanup_status)
                )
            for result in created:
                if result.batch_id in cleanup_failed_batch_ids:
                    status = "cleanup_failed"
                    logger.warning(
                        "poll_recreate_rollback_cleanup_failed batch_id=%s message_ids=%s",
                        result.batch_id,
                        ",".join(
                            str(message_id)
                            for message_id in (
                                result.notice_message_id,
                                result.availability_message_id,
                                result.message_id,
                            )
                            if message_id is not None
                        ),
                        extra={
                            "batch_id": result.batch_id,
                            "message_ids": tuple(
                                message_id
                                for message_id in (
                                    result.notice_message_id,
                                    result.availability_message_id,
                                    result.message_id,
                                )
                                if message_id is not None
                            ),
                        },
                    )
                else:
                    status = "failed"
                await session.execute(
                    update(PollBatch).where(PollBatch.id == result.batch_id).values(status=status)
                )
            await session.commit()

    async def _cleanup_recreated_batches(
        self,
        *,
        old_batch_ids: tuple[int, ...],
        replacement_by_date: dict[date, int],
        replacement_has_notice: bool,
    ) -> CleanupResult:
        if self._settings.telegram_target_chat_id is None:
            return CleanupResult(deleted_count=0, failed_count=0)

        deleted_count = 0
        failed_count = 0
        delete_notice = replacement_has_notice

        async with self._session_factory() as session:
            batches = (
                await session.scalars(select(PollBatch).where(PollBatch.id.in_(old_batch_ids)))
            ).all()
            messages = (
                await session.scalars(
                    select(PollMessage).where(PollMessage.batch_id.in_(old_batch_ids))
                )
            ).all()

            for message in messages:
                if message.message_kind == "notice" and not delete_notice:
                    message.cleanup_status = "preserved"
                    continue
                if message.pinned:
                    unpinned = await self._telegram_client.unpin_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=message.telegram_message_id,
                    )
                    if not unpinned:
                        failed_count += 1
                        message.cleanup_status = "unpin_failed"
                        continue
                    message.pinned = False
                deleted = await self._telegram_client.delete_message(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_id=message.telegram_message_id,
                )
                if deleted:
                    deleted_count += 1
                    message.cleanup_status = "deleted"
                else:
                    failed_count += 1
                    message.cleanup_status = "delete_failed"

            for batch in batches:
                batch_failed_count = sum(
                    1
                    for message in messages
                    if message.batch_id == batch.id
                    and message.cleanup_status in {"delete_failed", "unpin_failed"}
                )
                batch.status = "cleanup_failed" if batch_failed_count else "recreated"
                batch.superseded_by_batch_id = replacement_by_date.get(batch.service_date)
            await session.commit()

        _log_recreated_cleanup_result(
            old_batch_ids=old_batch_ids,
            deleted_count=deleted_count,
            failed_count=failed_count,
        )
        return CleanupResult(deleted_count=deleted_count, failed_count=failed_count)


async def _mark_batch_status(*, session: AsyncSession, batch_id: int, status: str) -> None:
    await session.execute(update(PollBatch).where(PollBatch.id == batch_id).values(status=status))
    await session.commit()


def _option_snapshots(
    *,
    batch_id: int,
    telegram_message_id: int,
    poll_id: str,
    draft: PollDraft,
) -> list[PollOptionSnapshot]:
    return [
        PollOptionSnapshot(
            batch_id=batch_id,
            telegram_message_id=telegram_message_id,
            poll_id=poll_id,
            option_index=index,
            label=option,
            lift_time=_option_lift_time(option),
        )
        for index, option in enumerate(draft.options)
    ]


def _initial_lift_availability(
    cancelled_lift_times: tuple[str, ...],
    manual_counts: dict[str, int] | None = None,
) -> tuple[LiftAvailability, ...]:
    cancelled = set(cancelled_lift_times)
    manual_by_time = manual_counts or {}
    return tuple(
        LiftAvailability(
            time=lift.time,
            seat_count=manual_by_time.get(lift.time, 0),
            capacity=lift.capacity,
            manual_count=manual_by_time.get(lift.time, 0),
        )
        for lift in DEFAULT_LIFTS
        if lift.time not in cancelled
    )


def _preferred_monitor_date(
    days: tuple[BookingMonitorDay, ...],
    *,
    today: date,
) -> date | None:
    if not days:
        return None
    if any(day.service_date == today for day in days):
        return today
    return days[0].service_date


def _service_week_start(service_date: date) -> date:
    days_since_saturday = (service_date.weekday() - 5) % 7
    return service_date - timedelta(days=days_since_saturday)


def _service_day_label(service_date: date) -> str:
    return f"{service_date:%a}, {service_date.day} {service_date:%b}"


def _shared_date(events: list[LiftEvent]) -> date | None:
    """The one date these events cover, or None when they span a weekend."""
    dates = {event.service_date for event in events}
    return next(iter(dates)) if len(dates) == 1 else None


def _waitlist_for(
    votes: list[PollVote],
    *,
    option_index: int,
    capacity: int,
    reserved: int,
) -> tuple[WaitlistRider, ...]:
    allocation = allocate_seats(
        (
            SeatCandidate(
                telegram_user_id=vote.telegram_user_id,
                label=_rider_label(vote),
                booked_at=vote_booked_at(vote, option_index),
            )
            for vote in votes
        ),
        capacity=capacity,
        reserved=reserved,
    )
    return tuple(
        WaitlistRider(telegram_user_id=candidate.telegram_user_id, label=candidate.label)
        for candidate in allocation.waitlist
    )


def _day_signals(day: BookingMonitorDay) -> tuple[LiftSignal, ...]:
    return tuple(
        LiftSignal(
            service_date=day.service_date,
            lift_time=lift.time,
            seats=lift.total_count,
            cancelled=lift.cancelled,
        )
        for lift in day.lifts
    )


def _lift_memory(row: LiftSignalState | None) -> LiftMemory:
    if row is None:
        return LiftMemory()
    return LiftMemory(
        confirmed_at=_as_utc(row.confirmed_at),
        threshold_notified_at=_as_utc(row.threshold_notified_at),
        undershoot_since=_as_utc(row.undershoot_since),
        undershoot_notified_at=_as_utc(row.undershoot_notified_at),
        departure_ping_at=_as_utc(row.departure_ping_at),
    )


def _apply_lift_memory(row: LiftSignalState, memory: LiftMemory, *, now: datetime) -> None:
    row.confirmed_at = _to_utc(memory.confirmed_at)
    row.threshold_notified_at = _to_utc(memory.threshold_notified_at)
    row.undershoot_since = _to_utc(memory.undershoot_since)
    row.undershoot_notified_at = _to_utc(memory.undershoot_notified_at)
    row.departure_ping_at = _to_utc(memory.departure_ping_at)
    row.updated_at = now.astimezone(UTC)


def _to_utc(value: datetime | None) -> datetime | None:
    """Always persist UTC: SQLite stores the wall clock and drops the offset."""
    return value.astimezone(UTC) if value is not None else None


def _as_utc(value: datetime | None) -> datetime | None:
    """Reading back a stored instant; a missing offset means it was written as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cancelled_day_board(service_date: date) -> str:
    return f"{render_availability_status(service_date, ())}\n\n❌ All lifts cancelled."


async def _poll_snapshot(
    *,
    session: AsyncSession,
    poll_id: str,
) -> PollOptionSnapshot | None:
    return await session.scalar(
        select(PollOptionSnapshot)
        .where(PollOptionSnapshot.poll_id == poll_id)
        .order_by(PollOptionSnapshot.option_index)
    )


def _vote_event(
    *,
    poll_snapshot: PollOptionSnapshot | None,
    poll_id: str,
    telegram_user_id: int,
    username: str | None,
    full_name: str,
    old_option_ids: str,
    new_option_ids: str,
    created_at: datetime,
) -> PollVoteEvent:
    return PollVoteEvent(
        poll_id=poll_id,
        batch_id=poll_snapshot.batch_id if poll_snapshot is not None else None,
        telegram_message_id=(
            poll_snapshot.telegram_message_id if poll_snapshot is not None else None
        ),
        telegram_user_id=telegram_user_id,
        username=username,
        full_name=full_name,
        old_option_ids=old_option_ids,
        new_option_ids=new_option_ids,
        action=_vote_action(old_option_ids=old_option_ids, new_option_ids=new_option_ids),
        created_at=created_at,
    )


def _log_vote_event(event: PollVoteEvent) -> None:
    log = logger.warning if event.action == "retracted" else logger.info
    log(
        (
            "poll_vote_event action=%s poll_id=%s batch_id=%s telegram_message_id=%s "
            "telegram_user_id=%s username=%s old_option_ids=%s new_option_ids=%s"
        ),
        event.action,
        event.poll_id,
        event.batch_id,
        event.telegram_message_id,
        event.telegram_user_id,
        event.username,
        event.old_option_ids,
        event.new_option_ids,
        extra={
            "vote_action": event.action,
            "poll_id": event.poll_id,
            "batch_id": event.batch_id,
            "telegram_message_id": event.telegram_message_id,
            "telegram_user_id": event.telegram_user_id,
            "username": event.username,
            "old_option_ids": event.old_option_ids,
            "new_option_ids": event.new_option_ids,
        },
    )


def _log_vote_noop(
    *,
    poll_id: str,
    telegram_user_id: int,
    username: str | None,
    option_ids: str,
) -> None:
    logger.debug(
        "poll_vote_noop poll_id=%s telegram_user_id=%s username=%s option_ids=%s",
        poll_id,
        telegram_user_id,
        username,
        option_ids,
        extra={
            "poll_id": poll_id,
            "telegram_user_id": telegram_user_id,
            "username": username,
            "option_ids": option_ids,
        },
    )


def _log_recreated_cleanup_result(
    *,
    old_batch_ids: tuple[int, ...],
    deleted_count: int,
    failed_count: int,
) -> None:
    log = logger.warning if failed_count else logger.info
    event_name = "poll_recreate_cleanup_failed" if failed_count else "poll_recreate_cleanup_done"
    log(
        "%s old_batch_ids=%s deleted_count=%s failed_count=%s",
        event_name,
        ",".join(str(batch_id) for batch_id in old_batch_ids),
        deleted_count,
        failed_count,
        extra={
            "old_batch_ids": old_batch_ids,
            "deleted_count": deleted_count,
            "failed_count": failed_count,
        },
    )


def _vote_action(*, old_option_ids: str, new_option_ids: str) -> str:
    if not old_option_ids and new_option_ids:
        return "voted"
    if old_option_ids and not new_option_ids:
        return "retracted"
    return "changed"


def _option_lift_time(option: str) -> str | None:
    parts = option.split()
    if len(parts) >= 2 and ":" in parts[1]:
        return parts[1]
    return None


def _encode_option_ids(option_ids: tuple[int, ...]) -> str:
    return ",".join(str(option_id) for option_id in sorted(set(option_ids)))


def decode_option_ids(option_ids: str) -> tuple[int, ...]:
    if not option_ids:
        return ()
    return tuple(int(item) for item in option_ids.split(",") if item)


# Microseconds, not seconds: two people booking the same lift in the same second
# is ordinary, and a tie there would be broken by user id — which is to say by
# who joined Telegram first.
_TIME_SCALE = 1_000_000


def encode_option_times(booked_at: dict[int, datetime]) -> str:
    return ",".join(
        f"{option_id}:{int(moment.timestamp() * _TIME_SCALE)}"
        for option_id, moment in sorted(booked_at.items())
    )


def decode_option_times(value: str) -> dict[int, datetime]:
    times: dict[int, datetime] = {}
    for item in value.split(","):
        if not item:
            continue
        option_id, _, epoch = item.partition(":")
        if not epoch:
            continue
        times[int(option_id)] = datetime.fromtimestamp(int(epoch) / _TIME_SCALE, tz=UTC)
    return times


def option_times_after_vote(
    *,
    previous: str,
    previous_option_ids: tuple[int, ...],
    previous_updated_at: datetime | None,
    option_ids: tuple[int, ...],
    now: datetime,
) -> str:
    """Keep the time each option was first picked; stamp only the new ones.

    Telegram sends the whole answer on every change, so a rider adding a second
    lift looks identical to one re-picking their first. Carrying the old times
    over is what tells those apart — otherwise adding a lift silently costs the
    rider their place in the queue on the lift they already held.
    """
    kept = decode_option_times(previous)
    # Rows written before the per-option column existed have no encoded times.
    # Their last whole-answer timestamp is still the best booking time for every
    # option they already held. Seed only those old options; genuinely new picks
    # below still receive `now`, and a removed-then-restored option starts over.
    legacy_time = _as_utc(previous_updated_at) if previous_updated_at is not None else None
    if legacy_time is not None:
        for option_id in previous_option_ids:
            kept.setdefault(option_id, legacy_time)
    return encode_option_times(
        {option_id: kept.get(option_id, now) for option_id in sorted(set(option_ids))}
    )


def vote_booked_at(vote: PollVote, option_id: int) -> datetime:
    """When this rider took this lift. Falls back to the whole answer's timestamp.

    Votes cast before per-option times existed have none, and their `updated_at`
    is the best record there is.
    """
    times = decode_option_times(vote.option_booked_at)
    moment = times.get(option_id)
    if moment is not None:
        return moment
    return _as_utc(vote.updated_at) or datetime.now(UTC)


def _options_by_poll_id(
    snapshots: Sequence[PollOptionSnapshot],
) -> dict[str, dict[int, PollOptionSnapshot]]:
    options: dict[str, dict[int, PollOptionSnapshot]] = {}
    for snapshot in snapshots:
        options.setdefault(snapshot.poll_id, {})[snapshot.option_index] = snapshot
    return options


def _rider_label(vote: PollVote) -> str:
    return f"@{vote.username}" if vote.username else vote.full_name


def _rider_label_from_parts(username: str | None, full_name: str) -> str:
    return f"@{username}" if username else full_name


def _late_monitor_exits(
    *,
    service_date: date,
    snapshots: Sequence[PollOptionSnapshot],
    votes: Sequence[PollVote],
    roster_rows: Sequence[DeadlineRoster],
    events: Sequence[PollVoteEvent],
    zone: ZoneInfo,
) -> tuple[MonitorLateExit, ...]:
    """Riders who held a running seat at freeze time and removed it afterwards."""
    snapshot_by_time = {
        snapshot.lift_time: snapshot for snapshot in snapshots if snapshot.lift_time is not None
    }
    vote_by_user = {vote.telegram_user_id: vote for vote in votes}
    day_rows = [row for row in roster_rows if row.service_date == service_date]
    running_times = {
        lift_time
        for lift_time in {row.lift_time for row in day_rows}
        if sum(row.seats for row in day_rows if row.lift_time == lift_time) >= MINIMUM_RIDERS
    }
    late: list[MonitorLateExit] = []
    for row in day_rows:
        if (
            row.telegram_user_id == 0
            or row.seats <= row.guests
            or row.lift_time not in running_times
        ):
            continue
        snapshot = snapshot_by_time.get(row.lift_time)
        if snapshot is None:
            continue
        vote = vote_by_user.get(row.telegram_user_id)
        if vote is not None and snapshot.option_index in decode_option_ids(vote.option_ids):
            continue
        matching_event = next(
            (
                event
                for event in events
                if event.poll_id == snapshot.poll_id
                and event.telegram_user_id == row.telegram_user_id
                and (_as_utc(event.created_at) or datetime.min.replace(tzinfo=UTC))
                >= (_as_utc(row.captured_at) or datetime.min.replace(tzinfo=UTC))
                and snapshot.option_index in decode_option_ids(event.old_option_ids)
                and snapshot.option_index not in decode_option_ids(event.new_option_ids)
            ),
            None,
        )
        changed_at = _as_utc(matching_event.created_at) if matching_event is not None else None
        late.append(
            MonitorLateExit(
                telegram_user_id=row.telegram_user_id,
                label=row.label,
                lift_time=row.lift_time,
                changed_at=changed_at.astimezone(zone) if changed_at is not None else None,
            )
        )
    return tuple(sorted(late, key=lambda rider: (lift_minutes(rider.lift_time), rider.label)))
