"""One loader for a service day's bookings and money, shared by every surface.

The group board, the rider card, the admin monitor and the web cabinet all read
the same day from here, so they cannot drift apart on who holds a seat, which
lifts are running and what the deadline froze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import (
    CancelledLift,
    DeadlineRoster,
    GuestSeat,
    ManualBookingCount,
    PaymentClaim,
    PollBatch,
    PollOptionSnapshot,
    PollVote,
    ServiceDayTerms,
)
from veloexpress_bot.payments.roster import MANUAL_USER_ID, DeadlineSnapshot, RosterSeat
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS, MINIMUM_RIDERS
from veloexpress_bot.polls.liftsignals import lift_minutes
from veloexpress_bot.polls.seating import SeatCandidate, allocate_seats, queue_rank
from veloexpress_core.lifts import SessionFactory, decode_option_ids, vote_booked_at


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
    # Actual seats before deadline obligations are overlaid for billing. A paid
    # rider can retain a financial obligation after losing their physical seat;
    # that must never look like a promotion back into the van.
    current_seat_holders_by_lift: dict[str, tuple[int, ...]] = field(default_factory=dict)
    waitlist_by_lift: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # Observed before the deadline's money obligations are overlaid on bookings.
    waiting_by_lift: dict[str, int] = field(default_factory=dict)
    paid_seats_by_user: dict[int, int] = field(default_factory=dict)
    paid_amount_by_user: dict[int, int] = field(default_factory=dict)
    payment_method_by_user: dict[int, str | None] = field(default_factory=dict)
    # Seats the deadline froze as paid, per rider and lift. That money is spent
    # on those seats and never moves to a lift booked later.
    locked_covered_by_user_lift: dict[tuple[int, str], int] = field(default_factory=dict)
    # Unverified cash, so surfaces can tell Misho which money he still collects.
    cash_amount_by_user: dict[int, int] = field(default_factory=dict)
    verified_user_ids: frozenset[int] = frozenset()

    def pending_lift_times(self, telegram_user_id: int) -> tuple[str, ...]:
        """Lifts the rider booked that have not reached the minimum yet."""
        running = self.lift_times_by_user.get(telegram_user_id, ())
        return tuple(
            lift_time
            for lift_time in self.booked_lift_times_by_user.get(telegram_user_id, ())
            if lift_time not in running
        )

    def confirmed_lift_times(self, telegram_user_id: int) -> tuple[str, ...]:
        """Chargeable own seats: real holders plus retained deadline obligations.

        A vote on a running lift still expresses intent when the rider is on
        its waitlist. It cannot add a seat to the bill.
        """
        return tuple(
            lift_time
            for lift_time in self.lift_times_by_user.get(telegram_user_id, ())
            if telegram_user_id in self.seat_holders_by_lift.get(lift_time, ())
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


async def load_day_bookings(
    *,
    settings: Settings,
    session_factory: SessionFactory,
    chat_id: int,
    today: date,
    until: date | None = None,
) -> list[DayBookings]:
    """Every posted or cancelled day from `today` on, as it stands now."""
    async with session_factory() as session:
        batches = (
            await session.scalars(
                select(PollBatch)
                .where(PollBatch.environment == settings.app_env)
                .where(PollBatch.chat_id == chat_id)
                .where(PollBatch.thread_id == settings.telegram_target_thread_id)
                .where(PollBatch.status.in_(("posted", "cancelled")))
                .where(PollBatch.service_date >= today)
                .where(PollBatch.service_date <= (until or date.max))
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
                .where(ManualBookingCount.environment == settings.app_env)
                .where(ManualBookingCount.chat_id == chat_id)
                .where(ManualBookingCount.thread_id == settings.telegram_target_thread_id)
                .where(ManualBookingCount.service_date.in_(service_dates))
            )
        ).all()
        guest_seats = (
            await session.scalars(
                select(GuestSeat)
                .where(GuestSeat.environment == settings.app_env)
                .where(GuestSeat.chat_id == chat_id)
                .where(GuestSeat.thread_id == settings.telegram_target_thread_id)
                .where(GuestSeat.service_date.in_(service_dates))
            )
        ).all()
        cancelled_lifts = (
            await session.scalars(
                select(CancelledLift)
                .where(CancelledLift.environment == settings.app_env)
                .where(CancelledLift.chat_id == chat_id)
                .where(CancelledLift.thread_id == settings.telegram_target_thread_id)
                .where(CancelledLift.service_date.in_(service_dates))
            )
        ).all()
        roster_rows = (
            await session.scalars(
                select(DeadlineRoster)
                .where(DeadlineRoster.environment == settings.app_env)
                .where(DeadlineRoster.chat_id == chat_id)
                .where(DeadlineRoster.thread_id == settings.telegram_target_thread_id)
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
                    PaymentClaim.method,
                    PaymentClaim.cash_amount_gel,
                    PaymentClaim.verified_by_user_id,
                )
                .where(PaymentClaim.environment == settings.app_env)
                .where(PaymentClaim.chat_id == chat_id)
                .where(PaymentClaim.service_date.in_(service_dates))
            )
        ).all()
        terms_rows = (
            await session.scalars(
                select(ServiceDayTerms)
                .where(ServiceDayTerms.environment == settings.app_env)
                .where(ServiceDayTerms.chat_id == chat_id)
                .where(ServiceDayTerms.thread_id == settings.telegram_target_thread_id)
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
    payment_method_by_date: dict[date, dict[int, str | None]] = {}
    cash_by_date: dict[date, dict[int, int]] = {}
    verified_by_date: dict[date, set[int]] = {}
    for (
        claim_date,
        claim_user_id,
        claim_seats,
        claim_amount,
        claim_method,
        claim_cash,
        claim_verified_by,
    ) in claim_keys:
        cash_by_date.setdefault(claim_date, {})[claim_user_id] = claim_cash
        if claim_verified_by is not None:
            verified_by_date.setdefault(claim_date, set()).add(claim_user_id)
        paid_by_date.setdefault(claim_date, set()).add(claim_user_id)
        paid_seats_by_date.setdefault(claim_date, {})[claim_user_id] = claim_seats
        paid_amount_by_date.setdefault(claim_date, {})[claim_user_id] = claim_amount
        payment_method_by_date.setdefault(claim_date, {})[claim_user_id] = claim_method
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
            polls_created_at=as_utc(batch.created_at),
            price_gel=terms.price_gel if terms is not None else settings.payment_price_gel,
            deadline_time=(
                terms.deadline_time if terms is not None else settings.booking_deadline_time
            ),
            paid_seats_by_user=dict(paid_seats_by_date.get(service_date, {})),
            paid_amount_by_user=dict(paid_amount_by_date.get(service_date, {})),
            payment_method_by_user=dict(payment_method_by_date.get(service_date, {})),
            cash_amount_by_user=dict(cash_by_date.get(service_date, {})),
            verified_user_ids=frozenset(verified_by_date.get(service_date, set())),
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
            day.waiting_by_lift[lift_time] = max(seats - capacity, 0)
            allocation = allocate_seats(
                (
                    SeatCandidate(
                        telegram_user_id=vote.telegram_user_id,
                        label=rider_label(vote.username, vote.full_name),
                        booked_at=vote_booked_at(vote, snapshot.option_index),
                        queue_rank=queue_rank(vote.option_queue_ranks, snapshot.option_index),
                    )
                    for vote in voters
                ),
                capacity=capacity,
                reserved=manual_total + guest_total,
            )
            day.seat_holders_by_lift[lift_time] = tuple(
                candidate.telegram_user_id for candidate in allocation.holders
            )
            day.current_seat_holders_by_lift[lift_time] = day.seat_holders_by_lift[lift_time]
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
        if row.covered_seats:
            day.locked_covered_by_user_lift[seat_key] = row.covered_seats
        holders = day.seat_holders_by_lift.get(row.lift_time, ())
        if row.seats > row.guests and user_id not in holders:
            day.seat_holders_by_lift[row.lift_time] = (*holders, user_id)
    day.running_lift_times = tuple(sorted(running, key=lift_minutes))


def _with_lift(lift_times: tuple[str, ...], lift_time: str) -> tuple[str, ...]:
    if lift_time in lift_times:
        return lift_times
    return tuple(sorted((*lift_times, lift_time), key=lift_minutes))


def rider_label(username: str | None, full_name: str) -> str:
    return f"@{username}" if username else full_name


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
