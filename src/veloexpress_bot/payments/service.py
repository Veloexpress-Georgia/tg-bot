from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.locking import transaction_lock
from veloexpress_bot.db.models import (
    CancelledLift,
    DeadlineRoster,
    GuestSeat,
    LiftDayResult,
    LiftDaySeat,
    LiftSeatState,
    ManualBookingCount,
    PaymentClaim,
    PaymentEntry,
    PaymentsBoard,
    PaymentsTopicPost,
    PollBatch,
    PollMessage,
    PollOptionSnapshot,
    PollVote,
    RefundReport,
    RiderCard,
    ServiceDayNotice,
    ServiceDayTerms,
)
from veloexpress_bot.payments.coverage import CoverageTarget, reconcile_coverage
from veloexpress_bot.payments.myday import (
    CASH_LINK_PREFIX,
    DEEP_LINK_PREFIX,
    MY_DAY_PARSE_MODE,
    PAID_LINK_PREFIX,
    MyDayDraft,
    RiderCardView,
    RiderDayView,
    RiderLiftRow,
    RiderSeason,
    RiderSeasonDay,
    deep_link,
    render_rider_card,
    render_rider_season,
)
from veloexpress_bot.payments.render import (
    PAYMENTS_PARSE_MODE,
    OutstandingRider,
    PaymentsBoardView,
    RefundRow,
    RiderPayment,
    UnpaidLift,
    render_cancellation_report,
    render_payment_post,
    render_payments_board,
    render_unpaid_deadline_notice,
)
from veloexpress_bot.payments.roster import (
    MANUAL_USER_ID,
    DeadlineSnapshot,
    RosterSeat,
)
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS, MINIMUM_RIDERS
from veloexpress_bot.polls.liftsignals import (
    SeatPromotion,
    booking_deadline_at,
    lift_minutes,
    render_seat_promotions,
)
from veloexpress_bot.polls.seating import SeatCandidate, allocate_seats
from veloexpress_bot.polls.service import (
    SessionFactory,
    TelegramPollClient,
    decode_option_ids,
    vote_booked_at,
)

logger = logging.getLogger(__name__)

NOT_BOOKED_TEXT = "You are not booked for this day."
ALREADY_SETTLED_TEXT = "You are already settled up for this day."
NOTHING_TO_UNDO_TEXT = "You have not marked a payment for this day."
# Undo erases the only record that money arrived, so it stops where the money
# stops being the rider's to take back: an admin recorded it, or booking closed.
UNDO_VERIFIED_TEXT = "Misho recorded this payment. Ask him if it needs undoing."
UNDO_AFTER_DEADLINE_TEXT = "Booking closed for this day. Ask Misho about a refund."
NOT_CLAIMED_YET_TEXT = "Tap 💸 I paid or 💵 Cash first."
BOARD_GONE_TEXT = "This payments board is no longer active."
PAYMENTS_DISABLED_TEXT = "Payments are not set up for this chat."

# Paying while every seat you hold is beyond capacity means paying for a waitlist
# place. The bot says so once and takes the same tap again as "yes, I know" — it is
# the rider's money and their call, but never a silent charge.
WAITLIST_WARNING_TEXT = (
    "⚠️ Your seats are past the 10-person capacity — you are on the waitlist. "
    "Tap again to pay anyway; you get a refund if you do not ride."
)


@dataclass(frozen=True)
class ClaimOutcome:
    """A warning the rider must acknowledge, or the result of settling up.

    Returned instead of a bare string so the caller never has to recognise a warning
    by comparing text — the partial-booking one names lifts and so is not a constant.
    """

    text: str
    needs_confirmation: bool = False


CASH_METHOD = "cash"
TRANSFER_METHOD = "transfer"

# A finished day is written down on the next tick, so the backlog is normally one
# day. These bound the exception: a first deploy with a season behind it, or a
# worker that was down across several weekends.
FREEZE_DAYS_PER_TICK = 20
# Past this, votes have had time to drift away from what the day actually was, so
# the row is marked as reconstructed rather than observed.
FRESH_FREEZE_DAYS = 2


@dataclass
class DayBookings:
    service_date: date
    running_lift_times: tuple[str, ...]
    cancelled: bool
    polls_created_at: datetime
    price_gel: int = 15
    deadline_time: str = "20:00"
    lift_times_by_user: dict[int, tuple[str, ...]] = field(default_factory=dict)
    # Every non-cancelled lift a rider booked, including ones still short of the
    # minimum: a refund question must not treat "not full yet" as "gone".
    booked_lift_times_by_user: dict[int, tuple[str, ...]] = field(default_factory=dict)
    labels_by_user: dict[int, tuple[str | None, str]] = field(default_factory=dict)
    # Occupied seats and capacity per lift, so the guest form can say what is left.
    seats_by_lift: dict[str, int] = field(default_factory=dict)
    capacity_by_lift: dict[str, int] = field(default_factory=dict)
    manual_by_lift: dict[str, int] = field(default_factory=dict)
    guests_by_user_lift: dict[tuple[int, str], int] = field(default_factory=dict)
    # Riders holding a real seat on each lift, in booking order. Everyone booked
    # beyond this is on the waitlist.
    seat_holders_by_lift: dict[str, tuple[int, ...]] = field(default_factory=dict)
    waitlist_by_lift: dict[str, tuple[int, ...]] = field(default_factory=dict)
    paid_seats_by_user: dict[int, int] = field(default_factory=dict)
    paid_amount_by_user: dict[int, int] = field(default_factory=dict)

    def pending_lift_times(self, telegram_user_id: int) -> tuple[str, ...]:
        """Lifts the rider booked that have not reached the minimum yet."""
        running = self.lift_times_by_user.get(telegram_user_id, ())
        return tuple(
            lift_time
            for lift_time in self.booked_lift_times_by_user.get(telegram_user_id, ())
            if lift_time not in running
        )

    def is_waitlisted(self, telegram_user_id: int) -> bool:
        """True when the rider holds no actual seat on any lift they booked."""
        lifts = self.lift_times_by_user.get(telegram_user_id, ())
        if not lifts:
            return False
        return not any(
            telegram_user_id in self.seat_holders_by_lift.get(lift_time, ()) for lift_time in lifts
        )

    def seats_left(self, lift_time: str) -> int:
        return max(
            self.capacity_by_lift.get(lift_time, 0) - self.seats_by_lift.get(lift_time, 0), 0
        )

    def guest_seats(
        self,
        telegram_user_id: int,
        *,
        lift_times: tuple[str, ...] | None = None,
        include_pending: bool = False,
    ) -> int:
        """Guest seats the rider holds, on running lifts unless told otherwise.

        A guest on a lift still short of the minimum is a real seat — they are part
        of what gets it to five — but no more billable than the host's own seat
        there, so the default stays the running lifts.
        """
        if lift_times is None:
            lift_times = (
                self.booked_lift_times_by_user.get(telegram_user_id, ())
                if include_pending
                else self.lift_times_by_user.get(telegram_user_id, ())
            )
        return sum(
            self.guests_by_user_lift.get((telegram_user_id, lift_time), 0)
            for lift_time in lift_times
        )

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
        bot_username: str = "",
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._telegram_client = telegram_client
        self._bot_username = bot_username
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
        if self._settings.telegram_target_chat_id is not None:
            await self._remove_lift_payment_cards()
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        await self._retire_boards(today=moment.date())
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
        acknowledged: bool = False,
        include_pending: bool = False,
    ) -> ClaimOutcome:
        """Settle up to what the rider owes right now, or to their whole day.

        `include_pending` covers lifts still short of the minimum. Nobody is pushed
        into it — the rule is that you need not pay before five — but one transfer
        beats two, and cash cannot be topped up without finding Misho again.
        """
        if not self.enabled:
            return ClaimOutcome(PAYMENTS_DISABLED_TEXT)
        day = await self._day(service_date)
        if day is None:
            return ClaimOutcome(BOARD_GONE_TEXT)
        lift_times = day.lift_times_by_user.get(telegram_user_id)
        if not lift_times:
            return ClaimOutcome(NOT_BOOKED_TEXT)
        pending = day.pending_lift_times(telegram_user_id)
        paying_for = (*lift_times, *pending) if include_pending else lift_times

        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            guests = day.guest_seats(telegram_user_id, lift_times=paying_for)
            owed = len(paying_for) + guests
            owed_amount = owed * day.price_gel
            if claim is not None and claim.amount_gel >= owed_amount:
                return ClaimOutcome(ALREADY_SETTLED_TEXT)
            if not acknowledged:
                if day.is_waitlisted(telegram_user_id):
                    return ClaimOutcome(WAITLIST_WARNING_TEXT, needs_confirmation=True)
                if pending and not include_pending:
                    # Charging for part of a booking without saying so is the silent
                    # 15 GEL that reads as a bug. One warning at a time, worst first.
                    return ClaimOutcome(
                        _partial_booking_warning(
                            confirmed=lift_times,
                            pending=pending,
                            due_now=(len(lift_times) + guests) * day.price_gel,
                            due_later=len(pending) * day.price_gel,
                        ),
                        needs_confirmation=True,
                    )
            # The button means "I have paid everything I owe right now", so tapping it
            # again after re-voting or adding a guest settles the difference.
            if claim is None:
                claim = PaymentClaim(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=service_date,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    amount_gel=0,
                    cash_amount_gel=0,
                )
                session.add(claim)
            claim.username = username
            claim.full_name = full_name
            claim.seats = owed
            received_delta = owed_amount - (claim.amount_gel or 0)
            claim.amount_gel = owed_amount
            if method == CASH_METHOD:
                claim.cash_amount_gel = (claim.cash_amount_gel or 0) + received_delta
            if claim.cash_amount_gel == claim.amount_gel:
                claim.method = CASH_METHOD
            elif claim.cash_amount_gel:
                claim.method = "mixed"
            else:
                claim.method = TRANSFER_METHOD
            recorded_at = datetime.now(UTC)
            claim.updated_at = recorded_at
            session.add(
                PaymentEntry(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=service_date,
                    telegram_user_id=telegram_user_id,
                    amount_gel=received_delta,
                    method=method,
                    kind="received",
                    recorded_at=recorded_at,
                )
            )
            await session.commit()

        await self._announce_claim(day, telegram_user_id)
        await self._refresh_board(day)
        # Name the lifts: the amount only makes sense once you can see that lifts
        # still short of the minimum are not charged for.
        how = " in cash" if method == CASH_METHOD else ""
        guest_note = f" +{guests} guest(s)" if guests else ""
        return ClaimOutcome(
            f"Thanks! {', '.join(paying_for)}{guest_note} · {owed_amount} GEL{how}."
        )

    async def my_day_card(
        self,
        *,
        telegram_user_id: int,
        service_date: date | None = None,
        today: date | None = None,
    ) -> MyDayDraft:
        """The rider's own weekend, with `service_date` as the open tab.

        Every upcoming day is included rather than only the one the link named:
        a rider who came here from Saturday's board usually also rides Sunday,
        and a second card for it would go stale in their chat the moment either
        day moved.
        """
        days: list[RiderDayView] = []
        has_history = False
        if self.enabled:
            moment = today or datetime.now(UTC).astimezone(self._zone).date()
            for day in await self._active_days(today=moment):
                if day.cancelled:
                    continue
                view = self._rider_day_view(day, telegram_user_id)
                if view is not None:
                    days.append(view)
            has_history = await self._has_ridden_before(telegram_user_id=telegram_user_id)
        return render_rider_card(
            RiderCardView(
                days=tuple(days),
                price_gel=self._settings.payment_price_gel,
                selected_service_date=service_date,
                has_history=has_history,
            )
        )

    async def _has_ridden_before(self, *, telegram_user_id: int) -> bool:
        async with self._session_factory() as session:
            return (
                await session.scalar(
                    select(LiftDaySeat.id)
                    .where(LiftDaySeat.environment == self._settings.app_env)
                    .where(LiftDaySeat.chat_id == self._require_chat_id())
                    .where(LiftDaySeat.telegram_user_id == telegram_user_id)
                    .limit(1)
                )
            ) is not None

    async def rider_season(self, *, telegram_user_id: int, limit: int = 12) -> RiderSeason:
        """Every lift this rider actually rode, newest first.

        Off the frozen day rather than the deadline roster, because the two
        disagree exactly where it matters to a rider: a van they joined on the
        morning counts, and a booking they dropped before it went does not.
        """
        empty = RiderSeason(days=(), total_days=0, total_rides=0, total_seats=0, total_gel=0)
        if self._settings.telegram_target_chat_id is None:
            return empty
        async with self._session_factory() as session:
            ridden = (
                await session.execute(
                    select(LiftDaySeat.service_date, LiftDaySeat.lift_time, LiftDaySeat.seats)
                    .join(
                        LiftDayResult,
                        (LiftDayResult.environment == LiftDaySeat.environment)
                        & (LiftDayResult.chat_id == LiftDaySeat.chat_id)
                        # Coalesced rather than compared directly: the thread is
                        # nullable, and NULL never equals NULL in a join.
                        & (
                            func.coalesce(LiftDayResult.thread_id, 0)
                            == func.coalesce(LiftDaySeat.thread_id, 0)
                        )
                        & (LiftDayResult.service_date == LiftDaySeat.service_date)
                        & (LiftDayResult.lift_time == LiftDaySeat.lift_time),
                    )
                    .where(LiftDaySeat.environment == self._settings.app_env)
                    .where(LiftDaySeat.chat_id == self._require_chat_id())
                    .where(LiftDaySeat.thread_id == self._settings.telegram_target_thread_id)
                    .where(LiftDaySeat.telegram_user_id == telegram_user_id)
                    .where(LiftDayResult.ran.is_(True))
                    .order_by(LiftDaySeat.service_date.desc())
                )
            ).all()
            if not ridden:
                return empty
            service_dates = tuple({row[0] for row in ridden})
            paid_by_date: dict[date, int] = {
                row[0]: row[1]
                for row in (
                    await session.execute(
                        select(
                            PaymentEntry.service_date,
                            func.coalesce(func.sum(PaymentEntry.amount_gel), 0),
                        )
                        .where(PaymentEntry.environment == self._settings.app_env)
                        .where(PaymentEntry.chat_id == self._require_chat_id())
                        .where(PaymentEntry.telegram_user_id == telegram_user_id)
                        .where(PaymentEntry.service_date.in_(service_dates))
                        .group_by(PaymentEntry.service_date)
                    )
                ).all()
            }

        lifts_by_date: dict[date, list[str]] = {}
        seats_by_date: dict[date, int] = {}
        for service_date, lift_time, seats in ridden:
            lifts_by_date.setdefault(service_date, []).append(lift_time)
            seats_by_date[service_date] = seats_by_date.get(service_date, 0) + seats
        ordered = sorted(lifts_by_date, reverse=True)
        days = tuple(
            RiderSeasonDay(
                service_date=service_date,
                lift_times=tuple(sorted(lifts_by_date[service_date], key=lift_minutes)),
                seats=seats_by_date[service_date],
                paid_gel=paid_by_date.get(service_date, 0),
            )
            for service_date in ordered
        )
        return RiderSeason(
            days=days[:limit],
            total_days=len(ordered),
            total_rides=len(ridden),
            total_seats=sum(seats_by_date.values()),
            total_gel=sum(paid_by_date.get(service_date, 0) for service_date in ordered),
        )

    async def rider_season_card(self, *, telegram_user_id: int) -> MyDayDraft:
        return render_rider_season(await self.rider_season(telegram_user_id=telegram_user_id))

    async def open_rider_card(
        self,
        *,
        telegram_user_id: int,
        private_chat_id: int,
        service_date: date,
    ) -> None:
        """Show the rider their card, replacing the one they were shown before.

        A new message each time would leave the chat full of old cards, each
        still carrying live buttons over amounts that have since moved. Posted
        before the old one is removed, so there is never a moment with no card —
        and posted rather than edited in place because the rider has just sent
        `/start`, and a silent edit far above their own message reads as nothing
        having happened.
        """
        card = await self.my_day_card(
            service_date=service_date,
            telegram_user_id=telegram_user_id,
        )
        async with self._session_factory() as session:
            row = await self._rider_card_row(session=session, telegram_user_id=telegram_user_id)
            previous = (
                row.telegram_message_id
                if row is not None and row.private_chat_id == private_chat_id
                else None
            )

        sent = await self._telegram_client.send_text(
            chat_id=private_chat_id,
            message_thread_id=None,
            text=card.text,
            reply_markup=card.reply_markup,
            parse_mode=MY_DAY_PARSE_MODE,
        )
        if previous is not None:
            await self._telegram_client.delete_message(
                chat_id=private_chat_id,
                message_id=previous,
            )
        async with self._session_factory() as session:
            row = await self._rider_card_row(session=session, telegram_user_id=telegram_user_id)
            if row is None:
                row = RiderCard(
                    environment=self._settings.app_env,
                    telegram_user_id=telegram_user_id,
                    private_chat_id=private_chat_id,
                    telegram_message_id=sent.message_id,
                )
                session.add(row)
            row.private_chat_id = private_chat_id
            row.telegram_message_id = sent.message_id
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def _rider_card_row(
        self,
        *,
        session: AsyncSession,
        telegram_user_id: int,
    ) -> RiderCard | None:
        return await session.scalar(
            select(RiderCard)
            .where(RiderCard.environment == self._settings.app_env)
            .where(RiderCard.telegram_user_id == telegram_user_id)
        )

    def _rider_day_view(self, day: DayBookings, telegram_user_id: int) -> RiderDayView | None:
        booked = day.booked_lift_times_by_user.get(telegram_user_id, ())
        if not booked:
            return None
        running = day.lift_times_by_user.get(telegram_user_id, ())
        rows = tuple(
            RiderLiftRow(
                lift_time=lift_time,
                guests=day.guests_by_user_lift.get((telegram_user_id, lift_time), 0),
                seats_left=day.seats_left(lift_time),
                waitlist_position=_waitlist_position(day, lift_time, telegram_user_id),
                running=lift_time in running,
            )
            for lift_time in booked
        )
        pending = day.pending_lift_times(telegram_user_id)
        guests_now = day.guest_seats(telegram_user_id, lift_times=running)
        guests_all = day.guest_seats(telegram_user_id, include_pending=True)
        return RiderDayView(
            service_date=day.service_date,
            price_gel=day.price_gel,
            rows=rows,
            pending_lift_times=pending,
            due_now_gel=(len(running) + guests_now) * day.price_gel,
            # Settling the whole day covers the guests waiting on it as well;
            # leaving them out quoted an amount the next tap would not accept.
            due_all_gel=(len(running) + len(pending) + guests_all) * day.price_gel,
            paid_gel=day.paid_amount_by_user.get(telegram_user_id, 0),
        )

    async def guest_lift_times(
        self,
        *,
        service_date: date,
        telegram_user_id: int,
        delta: int,
    ) -> tuple[str, ...]:
        """Which of the rider's lifts a whole-day guest change may touch.

        The rule lives here rather than being read back off the rendered card.
        A button being visible is a consequence of the rule, not the rule
        itself, and letting the markup answer meant a stale card could decide
        which seats moved.
        """
        if not self.enabled:
            return ()
        day = await self._day(service_date)
        if day is None or day.cancelled:
            return ()
        view = self._rider_day_view(day, telegram_user_id)
        if view is None:
            return ()
        if delta < 0:
            return tuple(row.lift_time for row in view.rows if row.guests)
        return tuple(row.lift_time for row in view.rows if row.seats_left > 0)

    async def adjust_guest_seats(
        self,
        *,
        service_date: date,
        telegram_user_id: int,
        lift_times: tuple[str, ...],
        delta: int,
    ) -> str:
        """Move guest seats on specific lifts. Raises the bill; never claims it paid."""
        if not self.enabled:
            return PAYMENTS_DISABLED_TEXT
        day = await self._day(service_date)
        if day is None or day.cancelled:
            return BOARD_GONE_TEXT

        changed = 0
        async with self._session_factory() as session:
            for lift_time in lift_times:
                if lift_time not in day.booked_lift_times_by_user.get(telegram_user_id, ()):
                    # Booked, not necessarily running: a guest is one of the five a
                    # lift needs, so refusing them until it fills is backwards.
                    continue
                current = day.guests_by_user_lift.get((telegram_user_id, lift_time), 0)
                if delta > 0 and day.seats_left(lift_time) <= 0:
                    # Never sell a seat the lift does not have.
                    continue
                if delta < 0 and current <= 0:
                    continue
                row = await session.scalar(
                    select(GuestSeat)
                    .where(GuestSeat.environment == self._settings.app_env)
                    .where(GuestSeat.chat_id == self._require_chat_id())
                    .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                    .where(GuestSeat.service_date == service_date)
                    .where(GuestSeat.lift_time == lift_time)
                    .where(GuestSeat.host_user_id == telegram_user_id)
                )
                if row is None:
                    row = GuestSeat(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=service_date,
                        lift_time=lift_time,
                        host_user_id=telegram_user_id,
                        count=0,
                    )
                    session.add(row)
                row.count = max(row.count + delta, 0)
                row.updated_at = datetime.now(UTC)
                changed += 1
            await session.commit()

        if not changed:
            return "No seats left on those lifts." if delta > 0 else "You have no guests there."

        refreshed = await self._day(service_date)
        if refreshed is not None:
            await self._announce_claim(refreshed, telegram_user_id)
            await self._refresh_board(refreshed)
        return "Guests updated."

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
            if claim.verified_by_user_id is not None:
                return UNDO_VERIFIED_TEXT
            notice = await self._service_day_notice(
                session=session,
                service_date=service_date,
            )
            if notice is not None and notice.roster_captured_at is not None:
                return UNDO_AFTER_DEADLINE_TEXT
            posted_message_id = claim.posted_message_id
            entries = (
                await session.scalars(
                    select(PaymentEntry)
                    .where(PaymentEntry.environment == self._settings.app_env)
                    .where(PaymentEntry.chat_id == self._require_chat_id())
                    .where(PaymentEntry.service_date == service_date)
                    .where(PaymentEntry.telegram_user_id == telegram_user_id)
                    .where(PaymentEntry.kind == "received")
                    .where(
                        PaymentEntry.id.not_in(
                            select(PaymentEntry.reversed_entry_id).where(
                                PaymentEntry.reversed_entry_id.is_not(None)
                            )
                        )
                    )
                )
            ).all()
            now = datetime.now(UTC)
            for entry in entries:
                session.add(
                    PaymentEntry(
                        environment=entry.environment,
                        chat_id=entry.chat_id,
                        thread_id=entry.thread_id,
                        service_date=entry.service_date,
                        telegram_user_id=entry.telegram_user_id,
                        amount_gel=-entry.amount_gel,
                        method=entry.method,
                        kind="reversal",
                        reference_key=f"undo:{entry.id}",
                        reversed_entry_id=entry.id,
                        recorded_at=now,
                    )
                )
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

    async def cancellation_preview(
        self,
        *,
        service_date: date,
        cancelled_lift_time: str | None = None,
    ) -> str | None:
        """The same estimate, before anything is cancelled and without filing it.

        A confirmation screen is a question, not a decision: an admin who reads
        what a cancellation would cost and then keeps the lift must not leave a
        refund report behind. The figures still read as "if you cancelled now",
        because the lift is still in the rider's bookings at this point.
        """
        return await self._cancellation_estimate(
            service_date=service_date,
            cancelled_lift_time=cancelled_lift_time,
            remember=False,
        )

    async def cancellation_report(
        self,
        *,
        service_date: date,
        cancelled_lift_time: str | None = None,
    ) -> str | None:
        """Cumulative refund estimate; generating it does not record a payout.

        Call this after the cancellation is recorded, so a rider's remaining lifts
        already exclude what was just cancelled.
        """
        return await self._cancellation_estimate(
            service_date=service_date,
            cancelled_lift_time=cancelled_lift_time,
            remember=True,
        )

    async def _cancellation_estimate(
        self,
        *,
        service_date: date,
        cancelled_lift_time: str | None,
        remember: bool,
    ) -> str | None:
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
            self._refund_row(
                claim,
                day=day,
                cancelled_lift_time=cancelled_lift_time,
                pending=not remember,
            )
            for claim in claims
        )
        report = render_cancellation_report(
            service_date=service_date,
            cancelled_lift_time=cancelled_lift_time,
            rows=rows,
        )
        if report is not None and remember:
            # Kept, because cancelling clears the day's claims: without this the
            # admin's chat message is the only list of who is owed money, and a
            # stray delete takes it with them.
            async with self._session_factory() as session:
                existing_report = await session.scalar(
                    select(RefundReport.id)
                    .where(RefundReport.environment == self._settings.app_env)
                    .where(RefundReport.chat_id == self._require_chat_id())
                    .where(RefundReport.service_date == service_date)
                    .where(RefundReport.lift_time == cancelled_lift_time)
                    .where(RefundReport.text == report)
                    .limit(1)
                )
                if existing_report is None:
                    session.add(
                        RefundReport(
                            environment=self._settings.app_env,
                            chat_id=self._require_chat_id(),
                            service_date=service_date,
                            lift_time=cancelled_lift_time,
                            text=report,
                        )
                    )
                await session.commit()
        return report

    async def reconcile_cancelled_days(self) -> int:
        """Finish a day cancellation interrupted before refunds were persisted."""
        if not self.enabled:
            return 0
        async with self._session_factory() as session:
            service_dates = tuple(
                (
                    await session.scalars(
                        select(PollBatch.service_date)
                        .join(
                            PaymentClaim,
                            PaymentClaim.service_date == PollBatch.service_date,
                        )
                        .where(PollBatch.environment == self._settings.app_env)
                        .where(PollBatch.chat_id == self._require_chat_id())
                        .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                        .where(PollBatch.status == "cancelled")
                        .where(PaymentClaim.environment == self._settings.app_env)
                        .where(PaymentClaim.chat_id == self._require_chat_id())
                        .distinct()
                    )
                ).all()
            )
        reconciled = 0
        for service_date in service_dates:
            await self.cancellation_report(service_date=service_date)
            await self.forget_day(service_date=service_date)
            reconciled += 1
        return reconciled

    def _refund_row(
        self,
        claim: PaymentClaim,
        *,
        day: DayBookings | None,
        cancelled_lift_time: str | None,
        pending: bool = False,
    ) -> RefundRow:
        """One rider's line: what they paid, what they still hold, what comes back.

        Cancelling one lift out of two does not undo the whole payment — the seats
        they still hold keep their share. Reporting the full amount had the admin
        handing back money for a trip that is still happening.

        `pending` is the preview: the lift has not been cancelled yet, so it is
        still among the rider's bookings and has to be taken out here. Without
        that, every preview reported nothing to refund.
        """
        remaining: tuple[str, ...] = (
            ()
            if day is None or cancelled_lift_time is None
            else day.booked_lift_times_by_user.get(claim.telegram_user_id, ())
        )
        if pending and cancelled_lift_time is not None:
            remaining = tuple(
                lift_time for lift_time in remaining if lift_time != cancelled_lift_time
            )
        held = len(remaining)
        if day is not None:
            held += sum(
                day.guests_by_user_lift.get((claim.telegram_user_id, lift_time), 0)
                for lift_time in remaining
            )
        return RefundRow(
            label=_rider_label(claim.username, claim.full_name),
            telegram_user_id=claim.telegram_user_id,
            seats=claim.seats,
            amount_gel=claim.amount_gel,
            remaining_lift_times=remaining,
            refund_gel=min(
                claim.amount_gel,
                max(claim.seats - held, 0) * (claim.amount_gel // max(claim.seats, 1)),
            ),
        )

    async def recent_refund_reports(self, *, limit: int = 5) -> tuple[str, ...]:
        """The last few refund lists, newest first, so a deleted message is not lost."""
        if not self.enabled:
            return ()
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(RefundReport)
                    .where(RefundReport.environment == self._settings.app_env)
                    .where(RefundReport.chat_id == self._require_chat_id())
                    .order_by(RefundReport.created_at.desc(), RefundReport.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(row.text for row in rows)

    async def forget_day(self, *, service_date: date) -> None:
        """Drop the day's payments once the refunds have been reported.

        A revived poll starts a fresh booking cycle. Archive the old projection
        after cancellation_report preserves its refund estimate. The immutable
        payment entries remain: an estimate does not prove money was returned.
        """
        if not self.enabled:
            return
        async with self._session_factory() as session:
            await self._lock_day(session, service_date)
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
            await transaction_lock(
                session,
                f"payment-post:{self._settings.app_env}:{self._require_chat_id()}:{telegram_user_id}",
            )
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
                seats = len(day.lift_times_by_user[telegram_user_id]) + day.guest_seats(
                    telegram_user_id
                )
                amount_gel = seats * day.price_gel
                session.add(
                    PaymentClaim(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        seats=seats,
                        amount_gel=amount_gel,
                        cash_amount_gel=0,
                    )
                )
                session.add(
                    PaymentEntry(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        telegram_user_id=telegram_user_id,
                        amount_gel=amount_gel,
                        method=None,
                        kind="received",
                        recorded_at=posted_at,
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
            guests = day.guest_seats(telegram_user_id)
            label = _rider_label(claim.username, claim.full_name)
            cash = claim.method == CASH_METHOD
            cash_gel = claim.cash_amount_gel
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
            seats=seats,
            guests=guests,
            amount_gel=claim.amount_gel,
            user_id=telegram_user_id,
            cash=cash,
            cash_gel=cash_gel,
        )
        if posted_message_id is not None:
            updated = await self._telegram_client.edit_text(
                chat_id=self._require_chat_id(),
                message_id=posted_message_id,
                text=text,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
            if updated:
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

    async def _retire_boards(self, *, today: date) -> None:
        """Leave past boards as history, without payment controls or a pin."""
        async with self._session_factory() as session:
            boards = list(
                await session.scalars(
                    select(PaymentsBoard)
                    .where(PaymentsBoard.environment == self._settings.app_env)
                    .where(PaymentsBoard.chat_id == self._require_chat_id())
                    .where(PaymentsBoard.service_date < today)
                    .where(PaymentsBoard.retired_at.is_(None))
                )
            )
            for board in boards:
                await self._lock_board(session, board.service_date)
                await session.refresh(board)
                if board.retired_at is not None:
                    continue
                completed = True
                for message_id in (board.telegram_message_id, board.lift_message_id):
                    if message_id is None:
                        continue
                    cleared = await self._telegram_client.clear_keyboard(
                        chat_id=board.chat_id,
                        message_id=message_id,
                    )
                    if not cleared:
                        completed = False
                        continue
                    if not await self._telegram_client.unpin_message(
                        chat_id=board.chat_id,
                        message_id=message_id,
                    ):
                        completed = False
                availability_ids = list(
                    await session.scalars(
                        select(PollMessage.telegram_message_id)
                        .join(PollBatch, PollBatch.id == PollMessage.batch_id)
                        .where(PollBatch.environment == self._settings.app_env)
                        .where(PollBatch.chat_id == board.chat_id)
                        .where(PollBatch.service_date == board.service_date)
                        .where(PollMessage.message_kind == "availability")
                    )
                )
                for message_id in availability_ids:
                    if not await self._telegram_client.clear_keyboard(
                        chat_id=board.chat_id, message_id=message_id
                    ):
                        completed = False
                if not completed:
                    continue
                board.retired_at = datetime.now(UTC)
                await session.commit()

    async def _lock_board(self, session: AsyncSession, service_date: date) -> None:
        await transaction_lock(
            session,
            f"payment-board:{self._settings.app_env}:{self._require_chat_id()}:{service_date}",
        )

    async def _refresh_board(self, day: DayBookings) -> None:
        # Money is already committed. Serialize the separate Telegram projection
        # so two payments or a worker tick cannot create competing day cards.
        async with self._session_factory() as session:
            await self._lock_board(session, day.service_date)
            await self._refresh_payments_board(day)

    async def _remove_lift_payment_cards(self) -> None:
        # Only tracked, bot-owned standalone payment cards are removed.
        async with self._session_factory() as session:
            boards = list(
                await session.scalars(
                    select(PaymentsBoard)
                    .where(PaymentsBoard.environment == self._settings.app_env)
                    .where(PaymentsBoard.chat_id == self._require_chat_id())
                    .where(PaymentsBoard.lift_message_id.is_not(None))
                    .order_by(PaymentsBoard.service_date, PaymentsBoard.id)
                )
            )
            for board in boards:
                await self._lock_board(session, board.service_date)
                await session.refresh(board)
                message_id = board.lift_message_id
                if message_id is None:
                    continue
                if not await self._telegram_client.clear_keyboard(
                    chat_id=board.chat_id, message_id=message_id
                ):
                    continue
                if not await self._telegram_client.unpin_message(
                    chat_id=board.chat_id, message_id=message_id
                ):
                    continue
                if not await self._telegram_client.delete_message(
                    chat_id=board.chat_id, message_id=message_id
                ):
                    continue
                board.lift_message_id = None
                await session.commit()

    async def _refresh_payments_board(self, day: DayBookings) -> None:
        view = await self._board_view(day)
        draft = render_payments_board(view)
        async with self._session_factory() as session:
            board = await session.scalar(
                select(PaymentsBoard)
                .where(PaymentsBoard.environment == self._settings.app_env)
                .where(PaymentsBoard.chat_id == self._require_chat_id())
                .where(PaymentsBoard.service_date == day.service_date)
            )
            if board is not None and board.retired_at is not None:
                return
            board_message_id = board.telegram_message_id if board is not None else None

        if board_message_id is not None:
            updated = await self._telegram_client.edit_text(
                chat_id=self._require_chat_id(),
                message_id=board_message_id,
                text=draft.text,
                reply_markup=draft.reply_markup,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
            if updated:
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
            board = await session.scalar(
                select(PaymentsBoard)
                .where(PaymentsBoard.environment == self._settings.app_env)
                .where(PaymentsBoard.chat_id == self._require_chat_id())
                .where(PaymentsBoard.service_date == day.service_date)
                .with_for_update()
            )
            if board is None:
                session.add(
                    PaymentsBoard(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        service_date=day.service_date,
                        telegram_message_id=sent.message_id,
                    )
                )
            else:
                board.telegram_message_id = sent.message_id
                board.updated_at = datetime.now(UTC)
            await session.commit()
        # The board is the payments menu, so it belongs at the top of the topic
        # rather than wherever the day's chatter pushed it.
        await self._telegram_client.pin_message(
            chat_id=self._require_chat_id(),
            message_id=sent.message_id,
        )
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
        claim_by_user = {claim.telegram_user_id: claim for claim in claims}
        return PaymentsBoardView(
            service_date=day.service_date,
            running_lift_times=day.running_lift_times,
            price_gel=day.price_gel,
            payments=tuple(
                RiderPayment(
                    label=_rider_label(claim.username, claim.full_name),
                    seats=claim.seats,
                    amount_gel=claim.amount_gel,
                    due_gel=self._owed_seats(day, claim) * day.price_gel,
                    cash=claim.method == CASH_METHOD,
                    cash_gel=claim.cash_amount_gel,
                    prepaid=bool(day.pending_lift_times(claim.telegram_user_id)),
                )
                for claim in claims
            ),
            outstanding=tuple(
                OutstandingRider(
                    telegram_user_id=user_id,
                    label=_rider_label(*day.labels_by_user.get(user_id, (None, "Rider"))),
                )
                for user_id in day.lift_times_by_user
                if (
                    (claim := claim_by_user.get(user_id)) is None
                    or claim.amount_gel < self._owed_seats(day, claim) * day.price_gel
                )
                and not day.is_waitlisted(user_id)
            ),
            guests_url=self._deep_link(day.service_date, DEEP_LINK_PREFIX),
            # Behind a setting, so paying stays a single in-group tap that records
            # the moment it lands until the private-chat route has been watched
            # working. `👤 Guests` above is unaffected: it always had to open a
            # private chat, because only a form can show one row per lift.
            paid_url=self._payment_link(day.service_date, PAID_LINK_PREFIX),
            cash_url=self._payment_link(day.service_date, CASH_LINK_PREFIX),
            deadline_time=day.deadline_time,
            cancelled=day.cancelled,
        )

    def _owed_seats(self, day: DayBookings, claim: PaymentClaim) -> int:
        """What the rider owes: the lifts they hold, plus their guests.

        Derived rather than stored, because re-voting is allowed until the
        deadline — after which the day carries its frozen roster and the figure
        stops falling. See `veloexpress_bot.payments.roster`.
        """
        return len(day.lift_times_by_user.get(claim.telegram_user_id, ())) + day.guest_seats(
            claim.telegram_user_id
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
            guest_seats = (
                await session.scalars(
                    select(GuestSeat)
                    .where(GuestSeat.environment == self._settings.app_env)
                    .where(GuestSeat.chat_id == chat_id)
                    .where(GuestSeat.thread_id == self._settings.telegram_target_thread_id)
                    .where(GuestSeat.service_date.in_(service_dates))
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
            roster_rows = (
                await session.scalars(
                    select(DeadlineRoster)
                    .where(DeadlineRoster.environment == self._settings.app_env)
                    .where(DeadlineRoster.chat_id == chat_id)
                    .where(DeadlineRoster.thread_id == self._settings.telegram_target_thread_id)
                    .where(DeadlineRoster.service_date.in_(service_dates))
                )
            ).all()
            claim_keys = (
                await session.execute(
                    select(
                        PaymentClaim.service_date,
                        PaymentClaim.telegram_user_id,
                        PaymentClaim.seats,
                        PaymentClaim.amount_gel,
                    )
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == chat_id)
                    .where(PaymentClaim.service_date.in_(service_dates))
                )
            ).all()
            terms_rows = (
                await session.scalars(
                    select(ServiceDayTerms)
                    .where(ServiceDayTerms.environment == self._settings.app_env)
                    .where(ServiceDayTerms.chat_id == chat_id)
                    .where(ServiceDayTerms.thread_id == self._settings.telegram_target_thread_id)
                    .where(ServiceDayTerms.service_date.in_(service_dates))
                )
            ).all()

        votes_by_poll: dict[str, list[PollVote]] = {}
        for vote in votes:
            votes_by_poll.setdefault(vote.poll_id, []).append(vote)
        manual_by_date_time = {(row.service_date, row.lift_time): row.count for row in manual}
        cancelled_date_time = {(row.service_date, row.lift_time) for row in cancelled_lifts}
        paid_by_date: dict[date, set[int]] = {}
        paid_seats_by_date: dict[date, dict[int, int]] = {}
        paid_amount_by_date: dict[date, dict[int, int]] = {}
        for claim_date, claim_user_id, claim_seats, claim_amount in claim_keys:
            paid_by_date.setdefault(claim_date, set()).add(claim_user_id)
            paid_seats_by_date.setdefault(claim_date, {})[claim_user_id] = claim_seats
            paid_amount_by_date.setdefault(claim_date, {})[claim_user_id] = claim_amount
        frozen_by_date: dict[date, DeadlineSnapshot] = {}
        terms_by_date = {row.service_date: row for row in terms_rows}
        for roster_row in roster_rows:
            previous = frozen_by_date.get(roster_row.service_date, DeadlineSnapshot(rows=()))
            frozen_by_date[roster_row.service_date] = DeadlineSnapshot(
                rows=(
                    *previous.rows,
                    RosterSeat(
                        lift_time=roster_row.lift_time,
                        telegram_user_id=roster_row.telegram_user_id,
                        label=roster_row.label,
                        seats=roster_row.seats,
                        guests=roster_row.guests,
                        covered_seats=roster_row.covered_seats,
                    ),
                )
            )

        capacity_by_time = {lift.time: lift.capacity for lift in DEFAULT_LIFTS}
        days: list[DayBookings] = []
        for service_date, batch in latest_by_date.items():
            terms = terms_by_date.get(service_date)
            day = DayBookings(
                service_date=service_date,
                running_lift_times=(),
                cancelled=batch.status == "cancelled",
                polls_created_at=_as_utc(batch.created_at),
                price_gel=terms.price_gel
                if terms is not None
                else self._settings.payment_price_gel,
                deadline_time=(
                    terms.deadline_time
                    if terms is not None
                    else self._settings.booking_deadline_time
                ),
                paid_seats_by_user=dict(paid_seats_by_date.get(service_date, {})),
                paid_amount_by_user=dict(paid_amount_by_date.get(service_date, {})),
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
                guest_total = sum(
                    row.count
                    for row in guest_seats
                    if row.service_date == service_date and row.lift_time == lift_time
                )
                for row in guest_seats:
                    if row.service_date == service_date and row.lift_time == lift_time:
                        day.guests_by_user_lift[(row.host_user_id, lift_time)] = row.count
                manual_total = manual_by_date_time.get((service_date, lift_time), 0)
                seats = len(voters) + manual_total + guest_total
                capacity = capacity_by_time.get(lift_time, 10)
                day.seats_by_lift[lift_time] = seats
                day.capacity_by_lift[lift_time] = capacity
                day.manual_by_lift[lift_time] = manual_total
                allocation = allocate_seats(
                    (
                        SeatCandidate(
                            telegram_user_id=vote.telegram_user_id,
                            label=_rider_label(vote.username, vote.full_name),
                            booked_at=vote_booked_at(vote, snapshot.option_index),
                        )
                        for vote in voters
                    ),
                    capacity=capacity,
                    reserved=manual_total + guest_total,
                )
                day.seat_holders_by_lift[lift_time] = tuple(
                    candidate.telegram_user_id for candidate in allocation.holders
                )
                day.waitlist_by_lift[lift_time] = tuple(
                    candidate.telegram_user_id for candidate in allocation.waitlist
                )
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
            frozen = frozen_by_date.get(service_date)
            if frozen is not None:
                _apply_snapshot(
                    day,
                    frozen.without_lifts(
                        frozenset(
                            lift_time
                            for date_key, lift_time in cancelled_date_time
                            if date_key == service_date
                        )
                    ),
                    capacity_by_time=capacity_by_time,
                    paid_user_ids=paid_by_date.get(service_date, set()),
                )
            days.append(day)
        return days

    async def announce_seat_promotions(self, *, now: datetime | None = None) -> None:
        """Tell riders who came off a waitlist that they are riding.

        Somebody cancelling is not bad news — it is a seat for whoever was
        waiting — but only if they hear about it. The availability board shows
        the new order and a Telegram edit notifies nobody, so the person it
        matters to most is the one who would miss it. This keeps working past
        the booking deadline, where late cancellations actually happen.
        """
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        promotions: list[SeatPromotion] = []
        for day in await self._active_days(today=moment.date()):
            if day.cancelled:
                continue
            promotions.extend(await self._promotions_for_day(day, now=moment))
        if not promotions:
            return
        await self._send_lift_topic_text(
            render_seat_promotions(promotions),
            log_label="seat_promotion_notice_failed",
        )

    async def _promotions_for_day(
        self,
        day: DayBookings,
        *,
        now: datetime,
    ) -> list[SeatPromotion]:
        promotions: list[SeatPromotion] = []
        async with self._session_factory() as session:
            rows = {
                row.lift_time: row
                for row in (
                    await session.scalars(
                        select(LiftSeatState)
                        .where(LiftSeatState.environment == self._settings.app_env)
                        .where(LiftSeatState.chat_id == self._require_chat_id())
                        .where(LiftSeatState.thread_id == self._settings.telegram_target_thread_id)
                        .where(LiftSeatState.service_date == day.service_date)
                    )
                ).all()
            }
            for lift_time, holders in day.seat_holders_by_lift.items():
                waitlist = day.waitlist_by_lift.get(lift_time, ())
                row = rows.get(lift_time)
                if row is None:
                    # First sight of this lift: record the order and stay quiet.
                    # Nobody was promoted, the bot simply started watching.
                    session.add(
                        LiftSeatState(
                            environment=self._settings.app_env,
                            chat_id=self._require_chat_id(),
                            thread_id=self._settings.telegram_target_thread_id,
                            service_date=day.service_date,
                            lift_time=lift_time,
                            holder_ids=_encode_ids(holders),
                            waitlist_ids=_encode_ids(waitlist),
                            updated_at=now.astimezone(UTC),
                        )
                    )
                    continue
                # Promoted means exactly this: was waiting, now holds a seat. A
                # rider who simply booked into a free seat is not news to them.
                was_waiting = _decode_ids(row.waitlist_ids)
                promoted = tuple(user_id for user_id in holders if user_id in was_waiting)
                row.holder_ids = _encode_ids(holders)
                row.waitlist_ids = _encode_ids(waitlist)
                row.updated_at = now.astimezone(UTC)
                if promoted:
                    promotions.append(
                        SeatPromotion(
                            service_date=day.service_date,
                            lift_time=lift_time,
                            riders=tuple(
                                (
                                    user_id,
                                    _rider_label(*day.labels_by_user.get(user_id, (None, "Rider"))),
                                )
                                for user_id in promoted
                            ),
                        )
                    )
            await session.commit()
        return promotions

    async def _send_lift_topic_text(self, text: str, *, log_label: str) -> None:
        """Seats are a lift matter, so this goes where the poll and board live."""
        try:
            await self._telegram_client.send_text(
                chat_id=self._require_chat_id(),
                message_thread_id=self._settings.telegram_target_thread_id,
                text=text,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
        except Exception:
            logger.exception(log_label)

    async def capture_deadline_rosters(self, *, now: datetime | None = None) -> None:
        """Freeze each day's roster once its booking deadline passes.

        This is what stops the money moving. Until it runs, every figure comes
        from live votes; after it, dropping out no longer reduces the bill and a
        lift that reached five stays reached. Run from the worker tick, so the
        exact moment is "the first tick after 20:00" rather than a scheduled job
        that a restart could miss entirely.

        A missed deadline is not caught up. The deadline sits on the evening
        before, so once the lift day itself has started the moment is gone: a
        snapshot taken at Saturday lunchtime records who is booked at lunchtime,
        which is precisely the number the freeze exists to stop trusting. It
        would also chase people about a van that has already left. Missing it
        degrades to live behaviour — how the bot worked before any of this —
        which is the honest failure and the one that surprises nobody.
        """
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        for day in await self._active_days(today=moment.date()):
            if day.cancelled:
                # A cancelled day is being refunded, so there is nothing to owe.
                continue
            deadline_at = booking_deadline_at(
                day.service_date,
                day.deadline_time,
                zone=self._zone,
            )
            if moment < deadline_at:
                continue
            if moment.date() >= day.service_date:
                # The evening before is over — a bot restart, or a deploy landing
                # mid-weekend. Too late to be a snapshot of the deadline.
                continue
            if day.polls_created_at > deadline_at:
                # A day added after its own deadline never had one. Freezing it at
                # creation would bill the first voter for an empty lift.
                continue
            rows = await self._capture_roster(day, now=moment, deadline_at=deadline_at)
            if rows is not None:
                await self._chase_unpaid_lifts(day, rows)

    async def _chase_unpaid_lifts(
        self,
        day: DayBookings,
        rows: tuple[RosterSeat, ...],
    ) -> None:
        """Booking closed with a lift underfunded: ask, never cancel.

        The group settles a lift at five prepayments, but somebody forgetting to
        tap a button is not a reason to call off a van — so the bot names the
        shortfall, tags whoever is missing from it, and leaves the decision with
        Misho. Sent once, at the freeze, because the roster is captured once.
        """
        short: list[UnpaidLift] = []
        unpaid: dict[int, str] = {}
        for lift_time in sorted({row.lift_time for row in rows}, key=lift_minutes):
            lift_rows = [row for row in rows if row.lift_time == lift_time]
            if sum(row.seats for row in lift_rows) < MINIMUM_RIDERS:
                # Never reached the minimum, so nothing was due on it in the first
                # place. Chasing money for a lift that is not happening is noise.
                continue
            # Manual bookings count as paid: Misho took them himself and settles
            # them himself, and there is no button for them to tap.
            paid_seats = sum(row.covered_seats for row in lift_rows)
            if paid_seats >= MINIMUM_RIDERS:
                continue
            short.append(UnpaidLift(lift_time=lift_time, paid_seats=paid_seats))
            for row in lift_rows:
                if row.telegram_user_id != MANUAL_USER_ID and row.covered_seats < row.seats:
                    unpaid[row.telegram_user_id] = row.label
        if not short:
            return

        text = render_unpaid_deadline_notice(
            service_date=day.service_date,
            lifts=tuple(short),
            riders=tuple(
                OutstandingRider(telegram_user_id=user_id, label=label)
                for user_id, label in unpaid.items()
            ),
            minimum=MINIMUM_RIDERS,
        )
        try:
            await self._telegram_client.send_text(
                chat_id=self._require_chat_id(),
                message_thread_id=self._settings.telegram_payments_thread_id,
                text=text,
                parse_mode=PAYMENTS_PARSE_MODE,
            )
        except Exception:
            logger.exception("unpaid_deadline_notice_failed")

    async def _capture_roster(
        self,
        day: DayBookings,
        *,
        now: datetime,
        deadline_at: datetime,
    ) -> tuple[RosterSeat, ...] | None:
        """Returns the frozen rows, or None when the day was already frozen."""
        async with self._session_factory() as session:
            notice = await self._service_day_notice(session=session, service_date=day.service_date)
            if notice is not None and notice.roster_captured_at is not None:
                return None
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._require_chat_id())
                    .where(PaymentClaim.service_date == day.service_date)
                )
            ).all()
            rows = _covered_roster_rows(day, _roster_rows(day), claims=tuple(claims))
            for row in rows:
                session.add(
                    DeadlineRoster(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        lift_time=row.lift_time,
                        telegram_user_id=row.telegram_user_id,
                        label=row.label,
                        seats=row.seats,
                        guests=row.guests,
                        covered_seats=row.covered_seats,
                    )
                )
            if notice is None:
                notice = ServiceDayNotice(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=day.service_date,
                )
                session.add(notice)
            notice.roster_captured_at = deadline_at.astimezone(UTC)
            notice.updated_at = now.astimezone(UTC)
            await session.commit()
        logger.info(
            "deadline_roster_captured service_date=%s rows=%s",
            day.service_date.isoformat(),
            len(rows),
            extra={"service_date": day.service_date.isoformat(), "rows": len(rows)},
        )
        return rows

    async def freeze_day_results(self, *, now: datetime | None = None) -> None:
        """Write down what each finished lift day came to, once it can no longer change.

        Deliberately not the deadline. The evening before answers a money
        question and nothing else: riders still put themselves on the morning
        van, an unpaid rider still drops out too late to matter, and Misho still
        decides on the day whether a van short of five goes out. All of that is
        the day happening, not error. It is the *next* day that nothing can move
        any more, and that is the moment worth recording.

        Until this ran, history was recomputed from live votes on every read —
        and the polls are never closed, so a vote changed weeks later silently
        rewrote a weekend the group had already lived through.

        Cancelled lifts are left out rather than recorded as cancelled: 15:30 is
        switched off on most weekends by template, so writing them down would put
        a line about a van nobody planned on every day in the history.
        """
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        pending = await self._unfrozen_service_dates(today=moment.date())
        if not pending:
            return
        if len(pending) > FREEZE_DAYS_PER_TICK:
            # A first deploy, or an outage over several weekends. Oldest first, a
            # bounded slice per tick, and said out loud — a silent cap here would
            # read as "the season is all there" when most of it is still missing.
            logger.info(
                "lift_day_results_backlog pending=%s freezing=%s",
                len(pending),
                FREEZE_DAYS_PER_TICK,
                extra={"pending": len(pending), "freezing": FREEZE_DAYS_PER_TICK},
            )
            pending = pending[:FREEZE_DAYS_PER_TICK]

        days = {day.service_date: day for day in await self._active_days(today=min(pending))}
        for service_date in pending:
            day = days.get(service_date)
            if day is None:
                # The batch went away under us — nothing to record, but the day is
                # over, so mark it done rather than retrying it every tick forever.
                await self._mark_results_frozen(service_date=service_date, now=moment)
                continue
            await self._freeze_day(day, now=moment)

    async def _unfrozen_service_dates(self, *, today: date) -> list[date]:
        """Finished lift days the bot has not written down yet, oldest first.

        Answered by the database rather than by pulling both tables into memory
        and subtracting: this runs on every worker tick for the whole life of the
        chat, and the normal answer is nothing at all.
        """
        already_written = (
            select(ServiceDayNotice.id)
            .where(ServiceDayNotice.environment == self._settings.app_env)
            .where(ServiceDayNotice.chat_id == self._require_chat_id())
            .where(ServiceDayNotice.thread_id == self._settings.telegram_target_thread_id)
            .where(ServiceDayNotice.service_date == PollBatch.service_date)
            .where(ServiceDayNotice.results_frozen_at.is_not(None))
            .exists()
        )
        async with self._session_factory() as session:
            service_dates = (
                await session.scalars(
                    select(PollBatch.service_date)
                    .distinct()
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._require_chat_id())
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.status.in_(("posted", "cancelled")))
                    .where(PollBatch.service_date < today)
                    .where(~already_written)
                    .order_by(PollBatch.service_date)
                )
            ).all()
        return list(service_dates)

    async def _freeze_day(self, day: DayBookings, *, now: datetime) -> None:
        age_days = (now.date() - day.service_date).days
        source = "closed" if age_days <= FRESH_FREEZE_DAYS else "backfilled"
        async with self._session_factory() as session:
            claims = (
                await session.scalars(
                    select(PaymentClaim)
                    .where(PaymentClaim.environment == self._settings.app_env)
                    .where(PaymentClaim.chat_id == self._require_chat_id())
                    .where(PaymentClaim.service_date == day.service_date)
                )
            ).all()
            seat_rows = _covered_roster_rows(day, _roster_rows(day), claims=tuple(claims))
            by_lift: dict[str, list[RosterSeat]] = {}
            for row in seat_rows:
                by_lift.setdefault(row.lift_time, []).append(row)

            for lift_time in sorted(day.seats_by_lift, key=lift_minutes):
                rows = by_lift.get(lift_time, [])
                session.add(
                    LiftDayResult(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        lift_time=lift_time,
                        # A cancelled day retires every lift on it, whatever the
                        # seats said before somebody called it off.
                        ran=not day.cancelled and lift_time in day.running_lift_times,
                        cancelled=day.cancelled,
                        # Counted off the same rows the seat list is written from,
                        # so the total and the names can never disagree.
                        seats=sum(row.seats for row in rows),
                        guest_seats=sum(row.guests for row in rows),
                        manual_seats=sum(
                            row.seats for row in rows if row.telegram_user_id == MANUAL_USER_ID
                        ),
                        covered_seats=sum(row.covered_seats for row in rows),
                        capacity=day.capacity_by_lift.get(lift_time, 10),
                        price_gel=day.price_gel,
                        source=source,
                        frozen_at=now.astimezone(UTC),
                    )
                )
                for row in rows:
                    session.add(
                        LiftDaySeat(
                            environment=self._settings.app_env,
                            chat_id=self._require_chat_id(),
                            thread_id=self._settings.telegram_target_thread_id,
                            service_date=day.service_date,
                            lift_time=lift_time,
                            telegram_user_id=row.telegram_user_id,
                            label=row.label,
                            seats=row.seats,
                            guests=row.guests,
                            covered_seats=row.covered_seats,
                        )
                    )

            notice = await self._service_day_notice(
                session=session,
                service_date=day.service_date,
            )
            if notice is None:
                notice = ServiceDayNotice(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=day.service_date,
                )
                session.add(notice)
            notice.results_frozen_at = now.astimezone(UTC)
            notice.updated_at = now.astimezone(UTC)
            await session.commit()
        logger.info(
            "lift_day_results_frozen service_date=%s lifts=%s seats=%s source=%s",
            day.service_date.isoformat(),
            len(day.seats_by_lift),
            len(seat_rows),
            source,
            extra={
                "service_date": day.service_date.isoformat(),
                "lifts": len(day.seats_by_lift),
                "source": source,
            },
        )

    async def _mark_results_frozen(self, *, service_date: date, now: datetime) -> None:
        async with self._session_factory() as session:
            notice = await self._service_day_notice(session=session, service_date=service_date)
            if notice is None:
                notice = ServiceDayNotice(
                    environment=self._settings.app_env,
                    chat_id=self._require_chat_id(),
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=service_date,
                )
                session.add(notice)
            notice.results_frozen_at = now.astimezone(UTC)
            notice.updated_at = now.astimezone(UTC)
            await session.commit()

    async def _service_day_notice(
        self,
        *,
        session: AsyncSession,
        service_date: date,
    ) -> ServiceDayNotice | None:
        return await session.scalar(
            select(ServiceDayNotice)
            .where(ServiceDayNotice.environment == self._settings.app_env)
            .where(ServiceDayNotice.chat_id == self._require_chat_id())
            .where(ServiceDayNotice.thread_id == self._settings.telegram_target_thread_id)
            .where(ServiceDayNotice.service_date == service_date)
        )

    async def _lock_day(self, session: AsyncSession, service_date: date) -> None:
        await transaction_lock(
            session,
            f"payment-day:{self._settings.app_env}:{self._require_chat_id()}:{service_date}",
        )

    async def _claim_row(
        self,
        *,
        session: AsyncSession,
        service_date: date,
        telegram_user_id: int,
    ) -> PaymentClaim | None:
        await self._lock_day(session, service_date)
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

    def _payment_link(self, service_date: date, prefix: str) -> str:
        if not self._settings.payments_via_private_chat:
            return ""
        return self._deep_link(service_date, prefix)

    def _deep_link(self, service_date: date, prefix: str) -> str:
        if not self._bot_username:
            return ""
        return deep_link(
            bot_username=self._bot_username,
            service_date=service_date,
            prefix=prefix,
        )

    def _require_chat_id(self) -> int:
        chat_id = self._settings.telegram_target_chat_id
        if chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required for payments."
            raise ValueError(msg)
        return chat_id


MANUAL_LABEL = "Manual bookings"


def _roster_rows(day: DayBookings) -> tuple[RosterSeat, ...]:
    """The day's seats as they stand, ready to be frozen.

    Waitlisted riders are left out on purpose: they were never charged, so there
    is nothing to hold them to. Manual bookings are in, because they decide
    whether a lift reached five.
    """
    rows: list[RosterSeat] = []
    for lift_time in sorted(day.seats_by_lift, key=lift_minutes):
        manual = day.manual_by_lift.get(lift_time, 0)
        if manual:
            rows.append(
                RosterSeat(
                    lift_time=lift_time,
                    telegram_user_id=MANUAL_USER_ID,
                    label=MANUAL_LABEL,
                    seats=manual,
                )
            )
        holders = day.seat_holders_by_lift.get(lift_time, ())
        hosts = tuple(
            user_id
            for (user_id, time_), count in day.guests_by_user_lift.items()
            if time_ == lift_time and count > 0 and user_id not in holders
        )
        for user_id in (*holders, *hosts):
            guests = day.guests_by_user_lift.get((user_id, lift_time), 0)
            own_seat = 1 if user_id in holders else 0
            rows.append(
                RosterSeat(
                    lift_time=lift_time,
                    telegram_user_id=user_id,
                    label=_rider_label(*day.labels_by_user.get(user_id, (None, "Rider"))),
                    seats=own_seat + guests,
                    guests=guests,
                )
            )
    return tuple(rows)


def _covered_roster_rows(
    day: DayBookings,
    rows: tuple[RosterSeat, ...],
    *,
    claims: tuple[PaymentClaim, ...],
) -> tuple[RosterSeat, ...]:
    """Freeze movable day money onto the seats it covered at the deadline."""
    lift_totals: dict[str, int] = {}
    for row in rows:
        lift_totals[row.lift_time] = lift_totals.get(row.lift_time, 0) + row.seats
    running = {lift_time for lift_time, seats in lift_totals.items() if seats >= MINIMUM_RIDERS}
    amount_by_user = {claim.telegram_user_id: claim.amount_gel for claim in claims}
    result: list[RosterSeat] = []
    for user_id in {row.telegram_user_id for row in rows}:
        user_rows = [row for row in rows if row.telegram_user_id == user_id]
        ordered = sorted(
            user_rows,
            key=lambda row: (row.lift_time not in running, lift_minutes(row.lift_time)),
        )
        received = (
            sum(row.seats for row in ordered) * day.price_gel
            if user_id == MANUAL_USER_ID
            else amount_by_user.get(user_id, 0)
        )
        coverage = reconcile_coverage(
            received_gel=received,
            price_gel=day.price_gel,
            targets=tuple(CoverageTarget(row.lift_time, row.seats) for row in ordered),
        )
        remaining_by_lift = {target.lift_time: target.covered_seats for target in coverage.targets}
        for row in user_rows:
            result.append(
                RosterSeat(
                    lift_time=row.lift_time,
                    telegram_user_id=row.telegram_user_id,
                    label=row.label,
                    seats=row.seats,
                    guests=row.guests,
                    covered_seats=remaining_by_lift.get(row.lift_time, 0),
                )
            )
    return tuple(result)


def _apply_snapshot(
    day: DayBookings,
    snapshot: DeadlineSnapshot,
    *,
    capacity_by_time: dict[str, int],
    paid_user_ids: set[int],
) -> None:
    """Overlay the frozen roster on the live day, holding two things still.

    A lift that reached the minimum by the deadline stays running, so the board
    cannot walk back a trip the group treats as settled — and money paid for it
    stays spent rather than turning into `prepaid` credit for a trip that has
    already happened. And a rider who still holds their booking, or who paid,
    stays on the hook for it.

    The one exemption is walking away unpaid. Booking and cancelling stay free
    after the deadline — the poll is open and the bot enforces nothing — and the
    group agreed that a prepayment is not refundable, never that forgetting to
    come is a debt. Somebody who leaves late shows up in the admin monitor for
    Misho to judge, not on the public board as owing money. Staying is not
    leaving, though: a rider still booked on a lift that filled by the deadline
    owes for it however many other people dropped out overnight.

    Seats freed by a late cancellation really are free: the waitlist moves up.
    """
    running = list(day.running_lift_times)
    for lift_time in snapshot.running_lift_times():
        if lift_time not in running:
            running.append(lift_time)
        day.capacity_by_lift.setdefault(lift_time, capacity_by_time.get(lift_time, 10))
    for row in snapshot.running_rows():
        user_id = row.telegram_user_id
        if user_id == MANUAL_USER_ID:
            # Counted in the lift total; there is no button for them to tap.
            continue
        still_booked = row.lift_time in day.booked_lift_times_by_user.get(user_id, ())
        if not still_booked and user_id not in paid_user_ids:
            # Left, and never paid: nothing was taken, so nothing is owed.
            continue
        day.lift_times_by_user[user_id] = _with_lift(
            day.lift_times_by_user.get(user_id, ()), row.lift_time
        )
        day.booked_lift_times_by_user[user_id] = _with_lift(
            day.booked_lift_times_by_user.get(user_id, ()), row.lift_time
        )
        day.labels_by_user.setdefault(user_id, (None, row.label))
        seat_key = (user_id, row.lift_time)
        day.guests_by_user_lift[seat_key] = max(
            day.guests_by_user_lift.get(seat_key, 0), row.guests
        )
        holders = day.seat_holders_by_lift.get(row.lift_time, ())
        if row.seats > row.guests and user_id not in holders:
            day.seat_holders_by_lift[row.lift_time] = (*holders, user_id)
    day.running_lift_times = tuple(sorted(running, key=lift_minutes))


def _with_lift(lift_times: tuple[str, ...], lift_time: str) -> tuple[str, ...]:
    if lift_time in lift_times:
        return lift_times
    return tuple(sorted((*lift_times, lift_time), key=lift_minutes))


def _partial_booking_warning(
    *,
    confirmed: tuple[str, ...],
    pending: tuple[str, ...],
    due_now: int,
    due_later: int,
) -> str:
    """Telegram caps a callback answer at 200 characters, so this stays terse."""
    return (
        f"⚠️ Only {', '.join(confirmed)} filled — {due_now} GEL now. "
        f"{', '.join(pending)} still short (+{due_later} later). "
        "Tap again to pay now, or wait and pay once."
    )


def _rider_label(username: str | None, full_name: str) -> str:
    return f"@{username}" if username else full_name


def _waitlist_position(day: DayBookings, lift_time: str, telegram_user_id: int) -> int:
    """Their place in the queue, 1-based. 0 when they hold a real seat.

    Nowhere else can tell a rider this: the public board names the waitlist but
    not the order, and the order is the only part they actually want.
    """
    waitlist = day.waitlist_by_lift.get(lift_time, ())
    if telegram_user_id not in waitlist:
        return 0
    return waitlist.index(telegram_user_id) + 1


def _encode_ids(user_ids: tuple[int, ...]) -> str:
    return ",".join(str(user_id) for user_id in user_ids)


def _decode_ids(value: str) -> frozenset[int]:
    return frozenset(int(part) for part in value.split(",") if part)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
