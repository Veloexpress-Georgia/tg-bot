"""Every surface reads one day ledger, so they agree on who owes what.

The group board, the rider card, the admin monitor, the lift page and the
booking order screen used to compute payment state separately and drifted.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from tests.unit.test_payments_service import SharedDatabase, _fill, _setup, _vote

from veloexpress_bot.db.models import PaymentClaim
from veloexpress_bot.polls.defaults import PaymentTerms
from veloexpress_bot.polls.liftsignals import LiftSignal, render_deadline_reminder
from veloexpress_bot.polls.render import LiftAvailability, partition_availability_lifts
from veloexpress_core.lifts import PollSetup
from veloexpress_core.payments import CASH_METHOD

PRICE = 15


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


async def test_a_host_who_paid_only_their_own_seat_owes_on_every_surface(
    db: SharedDatabase,
) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 0, riders=5)
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="rider100", full_name="Rider 100"
    )
    await payments.adjust_guest_seats(
        service_date=saturday, telegram_user_id=100, lift_times=("8:30",), delta=1
    )

    day = await payments.day_bookings(saturday)
    assert day is not None
    board = await payments._board_view(day)
    assert 100 in {rider.telegram_user_id for rider in board.outstanding}
    card = payments.rider_views(day)[100]
    assert card.due_now_gel - card.paid_gel == PRICE

    monitor = next(d for d in await polls.status_days() if d.service_date == saturday)
    assert {r.telegram_user_id: r.amount_gel for r in monitor.unpaid_riders}[100] == PRICE

    detail = await polls.lift_detail(service_date=saturday, lift_time="8:30")
    assert detail is not None
    host = next(rider for rider in detail[1] if rider.telegram_user_id == 100)
    assert (host.paid, host.state, host.due_gel) == (False, "partial", PRICE)


async def test_a_seat_on_a_lift_still_filling_owes_nothing_anywhere(db: SharedDatabase) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 1, riders=3)

    detail = await polls.lift_detail(service_date=saturday, lift_time="10:00")
    assert detail is not None
    assert {rider.state for rider in detail[1]} == {"pending"}
    monitor = next(d for d in await polls.status_days() if d.service_date == saturday)
    assert monitor.unpaid_riders == ()
    day = await payments.day_bookings(saturday)
    assert day is not None
    assert (await payments._board_view(day)).outstanding == ()


async def test_offline_seats_are_never_counted_as_missing_money(db: SharedDatabase) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 0, riders=4)
    for _ in range(2):
        await polls.adjust_manual_booking(
            service_date=saturday, lift_time="8:30", delta=1, admin_user_id=1
        )
    for user_id in range(100, 104):
        await payments.claim(
            service_date=saturday,
            telegram_user_id=user_id,
            username=f"rider{user_id}",
            full_name=f"Rider {user_id}",
        )

    monitor = next(d for d in await polls.status_days() if d.service_date == saturday)
    assert monitor.owed_gel == 4 * PRICE
    assert monitor.expected_gel == monitor.owed_gel
    assert monitor.unpaid_riders == ()
    lift = next(lift for lift in monitor.lifts if lift.time == "8:30")
    assert lift.funded


async def test_guests_keep_their_host_on_the_hook_after_the_host_leaves(
    db: SharedDatabase,
) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 0, riders=5)
    await payments.adjust_guest_seats(
        service_date=saturday, telegram_user_id=100, lift_times=("8:30",), delta=1
    )
    await _vote(polls, poll_id, 100)

    day = await payments.day_bookings(saturday)
    assert day is not None
    board = await payments._board_view(day)
    assert 100 in {rider.telegram_user_id for rider in board.outstanding}
    assert payments.rider_views(day)[100].due_now_gel == PRICE
    monitor = next(d for d in await polls.status_days() if d.service_date == saturday)
    assert 100 in {rider.telegram_user_id for rider in monitor.unpaid_riders}
    # And the guest seat can be paid for from the card.
    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="rider100", full_name="Rider 100"
    )
    assert "recorded +15 GEL" in outcome.text


async def test_every_upcoming_day_is_shown_not_just_the_next_two(db: SharedDatabase) -> None:
    polls, _payments, _client, _poll_id, saturday = await _setup(db)
    friday = saturday - timedelta(days=1)
    sunday = saturday + timedelta(days=1)
    for extra in (friday, sunday):
        await polls.create_poll(
            PollSetup(service_date=extra, created_by_user_id=1), pin_after_send=False
        )

    dates = [day.service_date for day in await polls.status_days()]
    assert {friday, saturday, sunday} <= set(dates)


def test_a_lift_locked_at_the_deadline_stays_running_after_a_drop_out() -> None:
    locked = LiftAvailability(time="10:00", seat_count=4, running_locked=True)
    short = LiftAvailability(time="12:00", seat_count=4)

    running, below, _ = partition_availability_lifts((locked, short))
    assert running == (locked,)
    assert below == (short,)

    reminder = render_deadline_reminder(
        date(2026, 11, 8),
        (
            LiftSignal(
                service_date=date(2026, 11, 8),
                lift_time="10:00",
                seats=4,
                covered_seats=4,
                running_locked=True,
            ),
        ),
        terms=PaymentTerms(price_gel=PRICE, deadline_time="20:00"),
        deadline_passed=True,
    )
    line = next(text for text in reminder.splitlines() if text.startswith("10:00"))
    # Counted as a lift that is happening, short of paid seats, not of riders.
    assert line.startswith("10:00 — 4 riders · 4/5 paid")


async def test_the_booking_order_screen_receives_the_ledger_state(db: SharedDatabase) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 0, riders=5)
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="rider100", full_name="Rider 100"
    )

    view = await polls.booking_order_view(service_date=saturday, lift_time="8:30")

    states = {rider["user_id"]: rider["state"] for rider in view["riders"]}
    assert states[100] == "paid"
    assert states[101] == "unpaid"


async def test_cash_misho_verified_is_no_longer_marked_as_cash_to_collect(
    db: SharedDatabase,
) -> None:
    polls, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(polls, poll_id, 0, riders=5)
    for user_id in (100, 101):
        await payments.claim(
            service_date=saturday,
            telegram_user_id=user_id,
            username=f"rider{user_id}",
            full_name=f"Rider {user_id}",
            method=CASH_METHOD,
        )
    async with db.session() as session:
        claim = await session.scalar(
            select(PaymentClaim).where(PaymentClaim.telegram_user_id == 101)
        )
        assert claim is not None
        claim.verified_by_user_id = 1
        await session.commit()

    detail = await polls.lift_detail(service_date=saturday, lift_time="8:30")
    assert detail is not None
    cash = {rider.telegram_user_id: rider.cash for rider in detail[1]}
    assert (cash[100], cash[101]) == (True, False)
