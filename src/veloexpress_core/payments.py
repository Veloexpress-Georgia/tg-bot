from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.locking import transaction_lock
from veloexpress_bot.db.models import (
    CashPromise,
    DeadlineRoster,
    GuestSeat,
    LiftDayResult,
    LiftDaySeat,
    LiftSeatState,
    PaymentClaim,
    PaymentEntry,
    PaymentsBoard,
    PollBatch,
    PollMessage,
    RefundObligation,
    RefundReport,
    RiderCard,
    SeatOffer,
    ServiceDayNotice,
)
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
    RosterSeat,
)
from veloexpress_bot.polls.defaults import MINIMUM_RIDERS
from veloexpress_bot.polls.liftsignals import (
    booking_deadline_at,
    lift_departure_at,
    lift_minutes,
)
from veloexpress_bot.polls.seating import WAITLIST_FREEZE_LEAD
from veloexpress_bot.polls.seatoffer import (
    DECLINED_TEXT,
    GONE_TEXT,
    NOT_FOR_YOU_TEXT,
    accepted_text,
    offer_keyboard,
    render_seat_offer,
    render_seat_taken,
    widen_due,
)
from veloexpress_bot.wording import day_label, money, plural
from veloexpress_core.daybook import DayBookings, load_day_bookings
from veloexpress_core.daybook import as_utc as _as_utc
from veloexpress_core.daybook import rider_label as _rider_label
from veloexpress_core.ledger import DayLedger, day_ledger
from veloexpress_core.lifts import (
    SessionFactory,
    TelegramPollClient,
)
from veloexpress_core.refunds import (
    as_utc,
    day_in_session,
    net_received_filter,
    released_by_lift,
)

logger = logging.getLogger(__name__)

NOT_BOOKED_TEXT = "You are not booked for this day."
# Booked, but only on lifts still below the minimum: there is nothing to pay yet.
NOT_RUNNING_YET_TEXT = f"Your lifts don't have {MINIMUM_RIDERS} riders yet — nothing to pay now."
ALREADY_SETTLED_TEXT = "You are already settled up for this day."
NOTHING_TO_UNDO_TEXT = "You have not marked a payment for this day."
# Undo erases the only record that money arrived, so it stops where the money
# stops being the rider's to take back: an admin recorded it, or booking closed.
UNDO_VERIFIED_TEXT = "Misho recorded this payment. Ask him if it needs undoing."
METHOD_VERIFIED_TEXT = "Misho recorded this payment. Ask him to correct its method."
UNDO_AFTER_DEADLINE_TEXT = "Too late to undo: the deadline has passed. Ask Misho."
UNDO_REFUND_PAID_TEXT = "A refund for this day was already paid out. Ask the admin to correct it."
NOT_CLAIMED_YET_TEXT = "After paying, report the transfer or the cash you handed to Misho."
BOARD_GONE_TEXT = "This payments board is no longer active."
PAYMENTS_DISABLED_TEXT = "Payments are not set up for this chat."

# Paying while every seat you hold is beyond capacity means paying for a waitlist
# place. The bot says so once and takes the same tap again as "yes, I know" — it is
# the rider's money and their call, but never a silent charge.
WAITLIST_WARNING_TEXT = (
    "⚠️ You are on the waitlist; payment does not secure a seat. "
    "Already paid? Tap again to report it. "
    "Arrange any refund with Misho; the bot does not return money."
)


@dataclass(frozen=True)
class ClaimOutcome:
    """A warning the rider must acknowledge, or the result of settling up.

    Returned instead of a bare string so the caller never has to recognise a warning
    by comparing text — the partial-booking one names lifts and so is not a constant.
    """

    text: str
    needs_confirmation: bool = False


@dataclass(frozen=True)
class StoredRefundReport:
    """A refund estimate as it was shown when the cancellation happened."""

    text: str
    service_date: date
    # None when the whole day went; otherwise the single lift that was called off.
    lift_time: str | None
    created_at: datetime


CASH_METHOD = "cash"
TRANSFER_METHOD = "transfer"

# A finished day is written down on the next tick, so the backlog is normally one
# day. These bound the exception: a first deploy with a season behind it, or a
# worker that was down across several weekends.
FREEZE_DAYS_PER_TICK = 20
# Past this, votes have had time to drift away from what the day actually was, so
# the row is marked as reconstructed rather than observed.
FRESH_FREEZE_DAYS = 2


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

    async def rider_day(self, *, service_date: date, telegram_user_id: int) -> RiderDayView | None:
        day = await self._day(service_date)
        return self._rider_day_view(day, telegram_user_id) if day is not None else None

    async def day_bookings(self, service_date: date) -> DayBookings | None:
        """Structured view shared with HTTP; no Telegram rendering or sending."""
        return await self._day(service_date)

    def rider_views(self, day: DayBookings) -> dict[int, RiderDayView]:
        """Every booked rider's view of a day that has already been read.

        Reading the day once per rider made the admin day screen re-run the
        whole active-day query for each person booked on it.
        """
        ledger = day_ledger(day)
        views = {
            uid: self._rider_day_view(day, uid, ledger=ledger)
            for uid in (*day.labels_by_user, *ledger.riders)
        }
        return {uid: view for uid, view in views.items() if view is not None}

    async def rider_days(
        self, *, service_dates: Iterable[date], telegram_user_id: int
    ) -> dict[date, RiderDayView]:
        """One rider's view of several days from a single read of the active days."""
        wanted = set(service_dates)
        if not wanted:
            return {}
        return {
            day.service_date: view
            for day in await self._active_days(today=min(wanted))
            if day.service_date in wanted
            and (view := self._rider_day_view(day, telegram_user_id)) is not None
        }

    async def convert_cash_promises(self) -> int:
        """Report cash-on-site choices stored while cash was a separate intention.

        Cash is reported like a transfer now. Each remaining choice becomes, once,
        a cash report for the seats the rider holds today; a rider already covered
        or holding no seat keeps their state. Choices for finished or cancelled
        days are dropped.
        """
        if not self.enabled:
            return 0
        async with self._session_factory() as session:
            promises = list(
                await session.scalars(
                    select(CashPromise).where(
                        CashPromise.environment == self._settings.app_env,
                        CashPromise.chat_id == self._require_chat_id(),
                        CashPromise.thread_id == self._settings.telegram_target_thread_id,
                    )
                )
            )
        if not promises:
            return 0
        days = {
            day.service_date: day
            for day in await self._active_days(today=min(p.service_date for p in promises))
        }
        converted = 0
        for promise in promises:
            day = days.get(promise.service_date)
            user_id = promise.telegram_user_id
            try:
                view = (
                    self._rider_day_view(day, user_id)
                    if day is not None and not day.cancelled
                    else None
                )
                if day is not None and view is not None and view.due_now_gel > view.paid_gel:
                    username, full_name = day.labels_by_user.get(user_id, (None, "Rider"))
                    await self.claim(
                        service_date=promise.service_date,
                        telegram_user_id=user_id,
                        username=username,
                        full_name=full_name,
                        method=CASH_METHOD,
                        acknowledged=True,
                    )
                async with self._session_factory() as session:
                    await session.execute(delete(CashPromise).where(CashPromise.id == promise.id))
                    await session.commit()
                converted += 1
            except Exception:
                # Left in place: the next tick tries again, and a report that did
                # land is not repeated because the rider is then covered.
                logger.exception(
                    "cash_promise_conversion_failed", extra={"cash_promise_id": promise.id}
                )
        return converted

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
        """Report a transfer, or cash the rider pays Misho on site.

        Both count toward the lift in the same way; cash is only never refunded,
        because nothing was handed over before the ride. `include_pending` covers
        lifts still short of the minimum. Nobody is pushed into it — the rule is
        that you need not pay before five — but one report can cover several lifts.
        """
        if not self.enabled:
            return ClaimOutcome(PAYMENTS_DISABLED_TEXT)
        received_delta = 0

        async with self._session_factory() as session:
            claim = await self._claim_row(
                session=session,
                service_date=service_date,
                telegram_user_id=telegram_user_id,
            )
            day = await self._day(service_date)
            if day is None:
                return ClaimOutcome(BOARD_GONE_TEXT)
            if day.cancelled:
                return ClaimOutcome("This day is cancelled. Payment cannot be recorded.")
            # Seats come from the day ledger, the same figures the board and the
            # cards show, so the amount a tap records is the amount they quote.
            rider = day_ledger(day).rider(telegram_user_id)
            seats = rider.seats if rider is not None else ()
            booked_running = day.lift_times_by_user.get(telegram_user_id, ())
            guest_times = tuple(seat.lift_time for seat in seats if seat.running and seat.guests)
            if not booked_running and not guest_times:
                if any(not seat.running and seat.seats for seat in seats):
                    return ClaimOutcome(NOT_RUNNING_YET_TEXT)
                return ClaimOutcome(NOT_BOOKED_TEXT)
            lift_times = tuple(seat.lift_time for seat in seats if seat.running and seat.own)
            waitlisted = day.is_waitlisted(telegram_user_id) and not guest_times
            # Preserve deliberate prepayment by someone entirely on the waitlist,
            # after the existing warning. Partial waitlists never enlarge a bill.
            if acknowledged and waitlisted:
                lift_times = booked_running
            pending = tuple(seat.lift_time for seat in seats if not seat.running and seat.seats)
            paying_for = tuple(
                dict.fromkeys((*lift_times, *guest_times, *(pending if include_pending else ())))
            )
            guests = sum(seat.guests for seat in seats if seat.lift_time in paying_for)
            pending_own = sum(1 for seat in seats if not seat.running and seat.own)
            owed = len(lift_times) + (pending_own if include_pending else 0) + guests
            owed_amount = owed * day.price_gel
            if claim is not None and claim.amount_gel >= owed_amount:
                if claim.method not in {CASH_METHOD, TRANSFER_METHOD} or claim.method == method:
                    return ClaimOutcome(
                        f"{ALREADY_SETTLED_TEXT}\n{day_label(service_date)} · "
                        f"reported {money(claim.amount_gel)}. Nothing added."
                    )
                if claim.verified_by_user_id is not None:
                    return ClaimOutcome(METHOD_VERIFIED_TEXT)
                # This corrects the reported method, not the money received. Keep
                # the original entry and append an audit event with zero GEL.
                claim.method = method
                claim.cash_amount_gel = claim.amount_gel if method == CASH_METHOD else 0
                claim.updated_at = datetime.now(UTC)
                session.add(
                    PaymentEntry(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=service_date,
                        telegram_user_id=telegram_user_id,
                        amount_gel=0,
                        method=method,
                        kind="method_change",
                        recorded_at=claim.updated_at,
                    )
                )
                await session.commit()
                method_corrected = True
            else:
                method_corrected = False
                if not acknowledged:
                    if waitlisted:
                        return ClaimOutcome(WAITLIST_WARNING_TEXT, needs_confirmation=True)
                    if pending and not include_pending:
                        # Charging for part of a booking without saying so is the silent
                        # one-seat charge that reads as a bug. One warning at a time, worst first.
                        return ClaimOutcome(
                            _partial_booking_warning(
                                confirmed=lift_times,
                                pending=pending,
                                due_now=rider.due_now_gel if rider is not None else 0,
                                due_later=(rider.due_all_gel - rider.due_now_gel)
                                if rider is not None
                                else 0,
                            ),
                            needs_confirmation=True,
                        )
                # Tapping again after re-voting or adding a guest settles the difference.
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

        refreshed = await self._day(service_date)
        if refreshed is not None:
            await self._announce_claim(refreshed, telegram_user_id)
            await self._refresh_board(refreshed)
        if method_corrected:
            label = "cash" if method == CASH_METHOD else "transfer"
            return ClaimOutcome(f"Payment method changed to {label}.")
        # Name the lifts: the amount only makes sense once you can see that lifts
        # still short of the minimum are not charged for.
        how = " in cash" if method == CASH_METHOD else ""
        guest_note = f" + {plural(guests, 'guest')}" if guests else ""
        return ClaimOutcome(
            f"{day_label(service_date)} · recorded +{money(received_delta)}{how}.\n"
            f"Total reported: {money(owed_amount)}.\n"
            f"{', '.join(paying_for)}{guest_note}."
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
                        .where(net_received_filter())
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

    def _rider_day_view(
        self,
        day: DayBookings,
        telegram_user_id: int,
        *,
        ledger: DayLedger | None = None,
    ) -> RiderDayView | None:
        rider = (ledger or day_ledger(day)).rider(telegram_user_id)
        if rider is None or not rider.seats:
            return None
        seats = sorted(rider.seats, key=lambda seat: lift_minutes(seat.lift_time))
        return RiderDayView(
            service_date=day.service_date,
            price_gel=day.price_gel,
            rows=tuple(
                RiderLiftRow(
                    lift_time=seat.lift_time,
                    guests=seat.guests,
                    seats_left=day.seats_left(seat.lift_time),
                    waitlist_position=_waitlist_position(day, seat.lift_time, telegram_user_id),
                    running=seat.running,
                )
                for seat in seats
            ),
            pending_lift_times=tuple(seat.lift_time for seat in seats if not seat.running),
            due_now_gel=rider.due_now_gel,
            # Settling the whole day covers the guests waiting on it as well;
            # leaving them out quoted an amount the next tap would not accept.
            due_all_gel=rider.due_all_gel,
            paid_gel=rider.paid_gel,
            payment_method=rider.method,
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
            await self._lock_day(session, service_date)
            # Another guest/manual mutation may have consumed the last seat while
            # this request waited for the day lock. Read capacity after acquiring it.
            latest = await self._day(service_date)
            if latest is None or latest.cancelled:
                return BOARD_GONE_TEXT
            day = latest
            for lift_time in lift_times:
                if delta > 0 and lift_time not in day.booked_lift_times_by_user.get(
                    telegram_user_id, ()
                ):
                    # Booked, not necessarily running: a guest is one of the five a
                    # lift needs, so refusing them until it fills is backwards.
                    continue
                if delta > 0 and day.seats_left(lift_time) <= 0:
                    # Never sell a seat the lift does not have.
                    continue
                if delta > 0 and day.waitlist_by_lift.get(lift_time):
                    # A free seat on a lift with a waitlist is on offer to the
                    # riders who wait; a guest must not take it ahead of them.
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
                if delta < 0 and (row is None or row.count <= 0):
                    continue
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
            await self._lock_day(session, service_date)
            obligations = list(
                await session.scalars(
                    select(RefundObligation)
                    .join(PaymentEntry, PaymentEntry.id == RefundObligation.reversal_entry_id)
                    .where(RefundObligation.environment == self._settings.app_env)
                    .where(RefundObligation.chat_id == self._require_chat_id())
                    .where(RefundObligation.thread_id == self._settings.telegram_target_thread_id)
                    .where(RefundObligation.service_date == service_date)
                    .where(RefundObligation.telegram_user_id == telegram_user_id)
                    # This booking cycle only; a reposted day starts a new claim.
                    .where(PaymentEntry.recorded_at >= claim.claimed_at)
                )
            )
            if any(obligation.paid_at is not None for obligation in obligations):
                # Money went back to someone who now says they never sent any.
                return UNDO_REFUND_PAID_TEXT
            # Nothing was sent, so nothing comes back: the cancellation's
            # reversals and the undo together net the receipts to zero.
            for obligation in obligations:
                await session.delete(obligation)
            posted_message_id = claim.posted_message_id
            entries = (
                await session.scalars(
                    select(PaymentEntry)
                    .where(PaymentEntry.environment == self._settings.app_env)
                    .where(PaymentEntry.chat_id == self._require_chat_id())
                    .where(PaymentEntry.service_date == service_date)
                    .where(PaymentEntry.telegram_user_id == telegram_user_id)
                    .where(PaymentEntry.kind == "received")
                )
            ).all()
            now = datetime.now(UTC)
            for entry in entries:
                reversed_amount = await session.scalar(
                    select(func.sum(PaymentEntry.amount_gel)).where(
                        PaymentEntry.kind == "reversal", PaymentEntry.reversed_entry_id == entry.id
                    )
                )
                remaining = entry.amount_gel + (reversed_amount or 0)
                # A zero reversal still withdraws the receipt's identity: restore
                # must never attach it to a later payment claim.
                withdrawn = await session.scalar(
                    select(PaymentEntry.id).where(PaymentEntry.reference_key == f"undo:{entry.id}")
                )
                if withdrawn is not None:
                    continue
                session.add(
                    PaymentEntry(
                        environment=entry.environment,
                        chat_id=entry.chat_id,
                        thread_id=entry.thread_id,
                        service_date=entry.service_date,
                        telegram_user_id=entry.telegram_user_id,
                        amount_gel=-max(remaining, 0),
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
        return "Payment report removed."

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
        """Cumulative refund list; generating it does not record a payout.

        Call this after the cancellation is recorded. It reads back the refunds
        that cancellation persisted and never decides a new amount itself.
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
        async with self._session_factory() as session:
            await self._lock_day(session, service_date)
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
            if remember and cancelled_lift_time is None:
                fallback = await session.scalar(
                    select(RefundReport)
                    .where(
                        RefundReport.environment == self._settings.app_env,
                        RefundReport.chat_id == self._require_chat_id(),
                        RefundReport.service_date == service_date,
                        RefundReport.lift_time.is_(None),
                        RefundReport.tracked.is_(False),
                        RefundReport.created_at >= min(claim.claimed_at for claim in claims),
                    )
                    .order_by(RefundReport.id.desc())
                    .limit(1)
                )
                if fallback is not None:
                    return fallback.text
            refunded = await self._cycle_refunds(session, service_date, claims)
            if remember or cancelled_lift_time is None:
                # A real cancellation already decided and reversed the money under
                # the day lock; the report only reads that back, cumulative for the
                # day. A whole-day preview returns every transfer anyway.
                released: dict[int, int] = {}
            else:
                day = await day_in_session(
                    session, settings=self._settings, service_date=service_date
                )
                released = (
                    released_by_lift(
                        day,
                        cancelled_lift_time,
                        {claim.telegram_user_id: claim.amount_gel for claim in claims},
                    )
                    if day is not None
                    else {}
                )
            rows = tuple(
                self._refund_row(
                    claim,
                    refunded=refunded.get(claim.telegram_user_id, 0),
                    released=released.get(claim.telegram_user_id, 0),
                    whole_day=cancelled_lift_time is None,
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

    async def _cycle_refunds(
        self, session: AsyncSession, service_date: date, claims: Sequence[PaymentClaim]
    ) -> dict[int, int]:
        """Refunds owed per rider in the current booking cycle, paid out or not."""
        first_claim = min(as_utc(claim.claimed_at) for claim in claims)
        rows = (
            await session.execute(
                select(
                    RefundObligation.telegram_user_id,
                    RefundObligation.amount_gel,
                    PaymentEntry.recorded_at,
                )
                .join(PaymentEntry, PaymentEntry.id == RefundObligation.reversal_entry_id)
                .where(RefundObligation.environment == self._settings.app_env)
                .where(RefundObligation.chat_id == self._require_chat_id())
                .where(RefundObligation.thread_id == self._settings.telegram_target_thread_id)
                .where(RefundObligation.service_date == service_date)
            )
        ).all()
        claimed_at = {claim.telegram_user_id: as_utc(claim.claimed_at) for claim in claims}
        totals: dict[int, int] = {}
        for user_id, amount, recorded_at in rows:
            if as_utc(recorded_at) >= claimed_at.get(user_id, first_claim):
                totals[user_id] = totals.get(user_id, 0) + amount
        return totals

    async def reconcile_cancelled_days(self) -> int:
        """Finish a day cancellation interrupted before refunds were persisted."""
        if not self.enabled:
            return 0
        # "Latest" is by creation time: a repost may reuse an older batch row.
        ranked = (
            select(
                PollBatch.id.label("id"),
                func.row_number()
                .over(
                    partition_by=PollBatch.service_date,
                    order_by=(PollBatch.created_at.desc(), PollBatch.id.desc()),
                )
                .label("rank"),
            )
            .where(PollBatch.environment == self._settings.app_env)
            .where(PollBatch.chat_id == self._require_chat_id())
            .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
            .where(PollBatch.status.in_(("posted", "cancelled")))
            .subquery()
        )
        latest_batch_ids = select(ranked.c.id).where(ranked.c.rank == 1)
        has_receipts = (
            select(PaymentClaim.id)
            .where(
                PaymentClaim.environment == self._settings.app_env,
                PaymentClaim.chat_id == self._require_chat_id(),
                PaymentClaim.thread_id == self._settings.telegram_target_thread_id,
                PaymentClaim.service_date == PollBatch.service_date,
            )
            .exists()
        )
        async with self._session_factory() as session:
            service_dates = tuple(
                await session.scalars(
                    select(PollBatch.service_date).where(
                        PollBatch.id.in_(latest_batch_ids),
                        PollBatch.status == "cancelled",
                        has_receipts,
                    )
                )
            )
        reconciled = 0
        for service_date in service_dates:
            await self.cancellation_report(service_date=service_date)
            await self.forget_day(service_date=service_date)
            reconciled += 1
        return reconciled

    @staticmethod
    def _refund_row(
        claim: PaymentClaim,
        *,
        refunded: int,
        released: int = 0,
        whole_day: bool = False,
    ) -> RefundRow:
        """One rider's line: what they transferred, and what goes back.

        `refunded` is what the cycle's cancellations already owe the rider; the
        claim has been lowered by it. `released` is a preview's not-yet-reversed
        money. Released seats use up the cash part first, because cash is handed
        over on site: only money transferred in advance can come back.
        """
        cash_gel = min(claim.cash_amount_gel or 0, claim.amount_gel)
        transferred_left = claim.amount_gel - cash_gel
        refund_now = min(transferred_left, max(released - cash_gel, 0))
        if whole_day:
            # Anything a cancellation did not reverse (a day settled from the old
            # estimate) still goes back when the whole day goes.
            refund_now = transferred_left
        return RefundRow(
            label=_rider_label(claim.username, claim.full_name),
            telegram_user_id=claim.telegram_user_id,
            seats=claim.seats,
            amount_gel=transferred_left + refunded,
            remaining_lift_times=(),
            refund_gel=refunded + refund_now,
        )

    async def refunds(self, *, paid: bool = False) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(RefundObligation)
                    .where(
                        RefundObligation.environment == self._settings.app_env,
                        RefundObligation.chat_id == self._require_chat_id(),
                        RefundObligation.thread_id == self._settings.telegram_target_thread_id,
                        RefundObligation.paid_at.is_not(None)
                        if paid
                        else RefundObligation.paid_at.is_(None),
                    )
                    .order_by(RefundObligation.service_date, RefundObligation.id)
                )
            )
            return [
                dict(
                    id=r.id,
                    service_date=r.service_date,
                    telegram_user_id=r.telegram_user_id,
                    label=r.label,
                    amount_gel=r.amount_gel,
                    method=r.method,
                    paid_at=r.paid_at,
                )
                for r in rows
            ]

    async def pay_refund(self, refund_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(RefundObligation, refund_id)
            if (
                row is None
                or row.environment != self._settings.app_env
                or row.chat_id != self._require_chat_id()
                or row.thread_id != self._settings.telegram_target_thread_id
            ):
                raise ValueError("Refund not found")
            await self._lock_day(session, row.service_date)
            row = await session.scalar(
                select(RefundObligation)
                .where(RefundObligation.id == refund_id)
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise ValueError("Refund not found")
            if row.paid_at is not None:
                return
            row.paid_at = datetime.now(UTC)
            session.add(
                PaymentEntry(
                    environment=row.environment,
                    chat_id=row.chat_id,
                    thread_id=row.thread_id,
                    service_date=row.service_date,
                    telegram_user_id=row.telegram_user_id,
                    amount_gel=-row.amount_gel,
                    method=row.method,
                    kind="refund",
                    reference_key=f"refund:{row.id}",
                    reversed_entry_id=row.reversal_entry_id,
                )
            )
            await session.commit()

    async def recent_refund_reports(
        self, *, limit: int = 10, tracked: bool | None = None
    ) -> tuple[StoredRefundReport, ...]:
        """The last few refund lists, newest first, so a deleted message is not lost.

        The text is returned exactly as it was shown at the time, wording and
        all — that is the point of storing it. When it was written comes along
        beside it, because an estimate from three weeks ago describes a world
        that has since moved on.
        """
        if not self.enabled:
            return ()
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(RefundReport)
                    .where(RefundReport.environment == self._settings.app_env)
                    .where(RefundReport.chat_id == self._require_chat_id())
                    .where(true() if tracked is None else RefundReport.tracked.is_(tracked))
                    .order_by(RefundReport.created_at.desc(), RefundReport.id.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            StoredRefundReport(
                text=row.text,
                service_date=row.service_date,
                lift_time=row.lift_time,
                created_at=_as_utc(row.created_at),
            )
            for row in rows
        )

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
            await session.execute(
                delete(CashPromise).where(
                    CashPromise.environment == self._settings.app_env,
                    CashPromise.chat_id == self._require_chat_id(),
                    CashPromise.thread_id == self._settings.telegram_target_thread_id,
                    CashPromise.service_date == service_date,
                )
            )
            await session.commit()
        logger.info(
            "payments_day_forgotten service_date=%s",
            service_date.isoformat(),
            extra={"service_date": service_date.isoformat()},
        )

    async def _announce_claim(self, day: DayBookings, telegram_user_id: int) -> None:
        """Post the rider's payment line, or edit the one already posted."""
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

    async def _refresh_payments_board(self, day: DayBookings, *, repost: bool = False) -> None:
        """Edit the day's board in place, or post it when it does not exist yet.

        `repost` moves an existing board to the bottom of the topic instead.
        """
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

        if repost:
            if board_message_id is None or draft.reply_markup is None:
                return
            # Delete first: a failed delete must not leave two boards for one day.
            if not await self._telegram_client.delete_message(
                chat_id=self._require_chat_id(), message_id=board_message_id
            ):
                return
        elif board_message_id is not None:
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
            # Moving a board the group already saw is not news.
            disable_notification=repost,
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
        if not repost:
            await self._repost_later_boards(day.service_date)

    async def _repost_later_boards(self, service_date: date) -> None:
        """Keep boards in day order: Sat above Sun, whichever funded first.

        A later day often reaches the minimum first, and a board posted below it
        then reads as the next day. Later live boards move below the new one.
        Locks are taken in date order, as `sync_boards` does, so this cannot
        deadlock against another refresh.
        """
        async with self._session_factory() as session:
            later_dates = list(
                await session.scalars(
                    select(PaymentsBoard.service_date)
                    .where(PaymentsBoard.environment == self._settings.app_env)
                    .where(PaymentsBoard.chat_id == self._require_chat_id())
                    .where(PaymentsBoard.service_date > service_date)
                    .where(PaymentsBoard.retired_at.is_(None))
                    .order_by(PaymentsBoard.service_date)
                )
            )
        for later_date in later_dates:
            later_day = await self._day(later_date)
            if later_day is None:
                continue
            async with self._session_factory() as session:
                await self._lock_board(session, later_date)
                await self._refresh_payments_board(later_day, repost=True)

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
        ledger = day_ledger(day)
        order = {user_id: index for index, user_id in enumerate(day.lift_times_by_user)}
        return PaymentsBoardView(
            service_date=day.service_date,
            running_lift_times=day.running_lift_times,
            price_gel=day.price_gel,
            payments=tuple(
                RiderPayment(
                    label=_rider_label(claim.username, claim.full_name),
                    seats=claim.seats,
                    amount_gel=claim.amount_gel,
                    due_gel=rider.due_now_gel if rider is not None else 0,
                    cash=claim.method == CASH_METHOD,
                    cash_gel=claim.cash_amount_gel,
                    prepaid_gel=rider.prepaid_gel if rider is not None else 0,
                )
                for claim in claims
                for rider in (ledger.rider(claim.telegram_user_id),)
            ),
            outstanding=tuple(
                OutstandingRider(
                    telegram_user_id=rider.telegram_user_id,
                    label=_rider_label(
                        *day.labels_by_user.get(rider.telegram_user_id, (None, "Rider"))
                    ),
                )
                for rider in sorted(
                    ledger.outstanding,
                    key=lambda rider: (
                        order.get(rider.telegram_user_id, len(order)),
                        rider.telegram_user_id,
                    ),
                )
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

    async def _day_in_session(
        self, session: AsyncSession, service_date: date
    ) -> DayBookings | None:
        self._require_chat_id()
        return await day_in_session(session, settings=self._settings, service_date=service_date)

    async def _day(self, service_date: date) -> DayBookings | None:
        days = await self._active_days(today=service_date)
        return next((day for day in days if day.service_date == service_date), None)

    async def _active_days(self, *, today: date) -> list[DayBookings]:
        return await load_day_bookings(
            settings=self._settings,
            session_factory=self._session_factory,
            chat_id=self._require_chat_id(),
            today=today,
        )

    async def offer_freed_seats(self, *, now: datetime | None = None) -> None:
        """Offer every freed seat to the lift's waitlist, and pace who is asked.

        A waitlisted rider no longer moves into a seat on their own. The vote may
        be days old; silence used to mean "riding and billed", and a tag minutes
        before departure reached nobody who could still make it. Now the first
        rider waiting is tagged with two buttons, the offer widens to the next
        one while nobody answers, and the seat belongs to nobody until a tap.
        """
        if not self.enabled:
            return
        moment = (now or datetime.now(UTC)).astimezone(self._zone)
        for day in await self._active_days(today=moment.date()):
            for offer_id in await self._scan_seat_offers(day, now=moment):
                await self._publish_seat_offer(offer_id, labels=day.labels_by_user)

    async def _scan_seat_offers(self, day: DayBookings, *, now: datetime) -> list[int]:
        """Bring one day's held lists and offers up to date; return offers to publish."""
        touched: list[int] = []
        async with self._session_factory() as session:
            await transaction_lock(session, self._seat_lock_key(day.service_date))
            if day.orphan_guest_ids:
                # Guest seats left behind by a host who no longer votes for the
                # lift. Read again under the lock: the host may have just returned.
                latest = await self._day(day.service_date)
                if latest is not None:
                    day = latest
                    if day.orphan_guest_ids:
                        await session.execute(
                            delete(GuestSeat).where(GuestSeat.id.in_(day.orphan_guest_ids))
                        )
                        day.orphan_guest_ids = ()
            lifts = {} if day.cancelled else day.current_seat_holders_by_lift
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
            offers = {
                offer.lift_time: offer
                for offer in (
                    await session.scalars(
                        select(SeatOffer)
                        .where(SeatOffer.environment == self._settings.app_env)
                        .where(SeatOffer.chat_id == self._require_chat_id())
                        .where(SeatOffer.thread_id == self._settings.telegram_target_thread_id)
                        .where(SeatOffer.service_date == day.service_date)
                        .where(SeatOffer.status == "open")
                    )
                ).all()
            }
            for lift_time, live_holders in lifts.items():
                holders = tuple(live_holders)
                waitlist = tuple(day.waitlist_by_lift.get(lift_time, ()))
                row = rows.get(lift_time)
                if row is None:
                    # First sight of this lift: whoever is waiting waits for an offer.
                    row = LiftSeatState(
                        environment=self._settings.app_env,
                        chat_id=self._require_chat_id(),
                        thread_id=self._settings.telegram_target_thread_id,
                        service_date=day.service_date,
                        lift_time=lift_time,
                        held_waitlist_ids=_encode_ids(waitlist),
                        declined_ids="",
                    )
                    session.add(row)
                elif _as_utc(row.updated_at) > now.astimezone(UTC):
                    # A vote, a tap or an admin correction committed after this
                    # cycle took its booking snapshot. Leave it; retry next cycle.
                    offers.pop(lift_time, None)
                    continue
                else:
                    voters = {*holders, *waitlist}
                    held = (
                        _decode_ids(row.held_waitlist_ids or "")
                        | _decode_ids(row.waitlist_ids)
                        | set(waitlist)
                    ) & voters
                    # Somebody who was waiting last tick and holds a seat now moved
                    # up between ticks without accepting anything. Back they go:
                    # they were never asked, so they must not end up on the bill.
                    slipped = tuple(user_id for user_id in holders if user_id in held)
                    holders = tuple(user_id for user_id in holders if user_id not in held)
                    waitlist = (*slipped, *waitlist)
                    row.held_waitlist_ids = _encode_ids(tuple(sorted(held)))
                    row.declined_ids = _encode_ids(
                        tuple(sorted(_decode_ids(row.declined_ids or "") & voters))
                    )
                row.holder_ids = _encode_ids(holders)
                row.waitlist_ids = _encode_ids(waitlist)
                row.updated_at = now.astimezone(UTC)

                declined = _decode_ids(row.declined_ids or "")
                queue = [user_id for user_id in waitlist if user_id not in declined]
                free = _free_seats(day, lift_time)
                departure_at = lift_departure_at(day.service_date, lift_time, zone=self._zone)
                offer = offers.pop(lift_time, None)
                # Close to departure nobody new is asked: a tag that late reaches
                # nobody who can still make it. Riders already asked may still tap.
                asking = now < departure_at - WAITLIST_FREEZE_LEAD
                if offer is None:
                    if free and queue and asking:
                        offer = SeatOffer(
                            environment=self._settings.app_env,
                            chat_id=self._require_chat_id(),
                            thread_id=self._settings.telegram_target_thread_id,
                            service_date=day.service_date,
                            lift_time=lift_time,
                            status="open",
                            seats=min(free, len(queue)),
                            offered_ids=_encode_ids(tuple(queue[:free])),
                            claimed_ids="",
                            text="",
                            created_at=now.astimezone(UTC),
                            widened_at=now.astimezone(UTC),
                        )
                        session.add(offer)
                        await session.flush()
                        touched.append(offer.id)
                    continue
                offered = [u for u in _decode_id_list(offer.offered_ids) if u in queue]
                waiting = [user_id for user_id in queue if user_id not in offered]
                widened = False
                if asking and now < departure_at:
                    while len(offered) < free and waiting:
                        offered.append(waiting.pop(0))
                        widened = True
                    if (
                        waiting
                        and not widened
                        and widen_due(
                            now=now,
                            widened_at=_as_utc(offer.widened_at).astimezone(self._zone),
                            departure_at=departure_at,
                        )
                    ):
                        offered.append(waiting.pop(0))
                        widened = True
                if not free or not offered or now >= departure_at:
                    offer.status = "closed"
                else:
                    offer.seats = min(free, len(offered))
                    offer.offered_ids = _encode_ids(tuple(offered))
                    if widened:
                        offer.widened_at = now.astimezone(UTC)
                        # A new rider is only pinged by a new message, not an edit.
                        offer.text = ""
                touched.append(offer.id)
            for offer in offers.values():
                # The lift was cancelled, or the whole day was.
                offer.status = "closed"
                touched.append(offer.id)
            await session.commit()
        return touched

    async def respond_to_seat_offer(
        self,
        *,
        offer_id: int,
        telegram_user_id: int,
        accept: bool,
    ) -> str:
        """A tagged rider takes the seat or passes. Returns the toast for them."""
        if not self.enabled:
            return GONE_TEXT
        labels: dict[int, tuple[str | None, str]] = {}
        async with self._session_factory() as session:
            offer = await session.get(SeatOffer, offer_id)
            if (
                offer is None
                or offer.environment != self._settings.app_env
                or offer.chat_id != self._require_chat_id()
            ):
                return GONE_TEXT
            service_date, lift_time = offer.service_date, offer.lift_time
            await transaction_lock(session, self._seat_lock_key(service_date))
            await session.refresh(offer)
            if datetime.now(UTC).astimezone(self._zone) >= lift_departure_at(
                service_date, lift_time, zone=self._zone
            ):
                offer.status = "closed"
                await session.commit()
                return GONE_TEXT
            day = await self._day(service_date)
            if offer.status != "open" or day is None or day.cancelled:
                return GONE_TEXT
            labels = day.labels_by_user
            price = day.price_gel
            offered = _decode_id_list(offer.offered_ids)
            waitlist = tuple(day.waitlist_by_lift.get(lift_time, ()))
            row = await session.scalar(
                select(LiftSeatState)
                .where(LiftSeatState.environment == self._settings.app_env)
                .where(LiftSeatState.chat_id == self._require_chat_id())
                .where(LiftSeatState.thread_id == self._settings.telegram_target_thread_id)
                .where(LiftSeatState.service_date == service_date)
                .where(LiftSeatState.lift_time == lift_time)
            )
            if telegram_user_id not in offered or telegram_user_id not in waitlist or row is None:
                return NOT_FOR_YOU_TEXT
            free = _free_seats(day, lift_time)
            declined = set(_decode_ids(row.declined_ids or ""))
            offered.remove(telegram_user_id)
            if accept:
                if free <= 0:
                    return GONE_TEXT
                held = set(_decode_ids(row.held_waitlist_ids or ""))
                held.discard(telegram_user_id)
                row.held_waitlist_ids = _encode_ids(tuple(sorted(held)))
                row.waitlist_ids = _encode_ids(
                    tuple(user_id for user_id in waitlist if user_id != telegram_user_id)
                )
                row.holder_ids = _encode_ids((*_decode_id_list(row.holder_ids), telegram_user_id))
                offer.claimed_ids = _encode_ids(
                    (*_decode_id_list(offer.claimed_ids), telegram_user_id)
                )
                free -= 1
            else:
                declined.add(telegram_user_id)
                row.declined_ids = _encode_ids(tuple(sorted(declined)))
            row.updated_at = datetime.now(UTC)
            still_waiting = [
                user_id
                for user_id in waitlist
                if user_id != telegram_user_id and user_id not in declined
            ]
            offer.offered_ids = _encode_ids(tuple(offered))
            if free <= 0 or not still_waiting:
                offer.status = "closed"
            else:
                offer.seats = min(free, max(len(offered), 1))
            await session.commit()
        await self._publish_seat_offer(offer_id, labels=labels)
        logger.info(
            "seat_offer_answered offer_id=%s telegram_user_id=%s accepted=%s",
            offer_id,
            telegram_user_id,
            accept,
            extra={"offer_id": offer_id, "telegram_user_id": telegram_user_id, "accepted": accept},
        )
        return accepted_text(price) if accept else DECLINED_TEXT

    def _seat_lock_key(self, service_date: date) -> str:
        return f"payment-day:{self._settings.app_env}:{self._require_chat_id()}:{service_date}"

    async def _publish_seat_offer(
        self,
        offer_id: int,
        *,
        labels: dict[int, tuple[str | None, str]],
    ) -> None:
        """Make the group message say what the offer row says. Safe to repeat."""

        def label(user_id: int) -> str:
            return _rider_label(*labels.get(user_id, (None, "Rider")))

        async with self._session_factory() as session:
            offer = await session.get(SeatOffer, offer_id)
            if offer is None:
                return
            chat_id = self._require_chat_id()
            message_id = offer.message_id
            offered = _decode_id_list(offer.offered_ids)
            claimed = _decode_id_list(offer.claimed_ids)
            try:
                if offer.status == "open" and offered:
                    text = render_seat_offer(
                        offer.service_date,
                        offer.lift_time,
                        seats=offer.seats,
                        riders=[(user_id, label(user_id)) for user_id in offered],
                    )
                    if text == offer.text and message_id is not None:
                        return
                    if offer.text and message_id is not None:
                        # Same riders, different wording: an edit is enough.
                        await self._telegram_client.edit_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=text,
                            reply_markup=offer_keyboard(offer.id),
                            parse_mode=PAYMENTS_PARSE_MODE,
                        )
                    else:
                        sent = await self._telegram_client.send_text(
                            chat_id=chat_id,
                            message_thread_id=self._settings.telegram_target_thread_id,
                            text=text,
                            reply_markup=offer_keyboard(offer.id),
                            parse_mode=PAYMENTS_PARSE_MODE,
                        )
                        if message_id is not None:
                            await self._telegram_client.delete_message(
                                chat_id=chat_id, message_id=message_id
                            )
                        offer.message_id = sent.message_id
                    offer.text = text
                elif message_id is not None:
                    if claimed:
                        text = render_seat_taken(
                            offer.service_date,
                            offer.lift_time,
                            labels=[label(user_id) for user_id in claimed],
                        )
                        if text == offer.text:
                            return
                        await self._telegram_client.edit_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=text,
                            parse_mode=PAYMENTS_PARSE_MODE,
                        )
                        offer.text = text
                    else:
                        # Nobody took it: the board already shows the free seat.
                        await self._telegram_client.delete_message(
                            chat_id=chat_id, message_id=message_id
                        )
                        offer.message_id = None
            except Exception:
                logger.exception("seat_offer_publish_failed", extra={"offer_id": offer_id})
                return
            await session.commit()

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
            await self._lock_day(session, day.service_date)
            latest = await self._day_in_session(session, day.service_date)
            if latest is None or latest.cancelled:
                return None
            day = latest
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
            await self._lock_day(session, day.service_date)
            notice = await self._service_day_notice(session=session, service_date=day.service_date)
            if notice is not None and notice.results_frozen_at is not None:
                return
            latest = await self._day_in_session(session, day.service_date)
            if latest is None:
                return
            day = latest
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
                        waiting_count=(
                            day.waiting_by_lift.get(lift_time)
                            if source == "closed" and not day.cancelled
                            else None
                        ),
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


MANUAL_LABEL = "Offline seats"


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
    """Freeze movable day money onto the seats it covered at the deadline.

    Coverage is read from the day ledger, so the frozen roster records exactly
    what the board and the cabinet showed as paid at that moment. Offline seats
    are Misho's own and count as covered.
    """
    # Claims are re-read under the day lock; trust them over the earlier read.
    ledger = day_ledger(
        replace(
            day,
            paid_amount_by_user={claim.telegram_user_id: claim.amount_gel for claim in claims},
        )
    )
    result: list[RosterSeat] = []
    for row in rows:
        if row.telegram_user_id == MANUAL_USER_ID:
            covered = row.seats
        else:
            rider = ledger.rider(row.telegram_user_id)
            seat = rider.seat(row.lift_time) if rider is not None else None
            covered = min(seat.covered, row.seats) if seat is not None else 0
        result.append(
            RosterSeat(
                lift_time=row.lift_time,
                telegram_user_id=row.telegram_user_id,
                label=row.label,
                seats=row.seats,
                guests=row.guests,
                covered_seats=covered,
            )
        )
    return tuple(result)


def _partial_booking_warning(
    *,
    confirmed: tuple[str, ...],
    pending: tuple[str, ...],
    due_now: int,
    due_later: int,
) -> str:
    """Telegram caps a callback answer at 200 characters, so this stays terse."""
    has = "has" if len(confirmed) == 1 else "have"
    need = "needs" if len(pending) == 1 else "need"
    return (
        f"⚠️ Only {', '.join(confirmed)} {has} {MINIMUM_RIDERS} riders — "
        f"pay {money(due_now)} now. "
        f"{', '.join(pending)} {need} more riders (+{money(due_later)} later). "
        f"Paid already? Tap again to report {money(due_now)}."
    )


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


def _decode_id_list(value: str) -> list[int]:
    """Order matters here: it is the order riders were asked in."""
    return [int(part) for part in value.split(",") if part]


def _free_seats(day: DayBookings, lift_time: str) -> int:
    """Physical capacity, independent of demand and deadline obligations."""
    return day.seats_left(lift_time)


def _decode_ids(value: str) -> frozenset[int]:
    return frozenset(int(part) for part in value.split(",") if part)
