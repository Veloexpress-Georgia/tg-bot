"""Seat ownership regressions exercised through booking/payment services."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from tests.unit.test_payments_service import (
    SharedDatabase,
    _fill,
    _noon_before,
    _setup,
    _vote,
)


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


async def test_held_demand_does_not_fill_or_start_a_lift(db: SharedDatabase) -> None:
    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.offer_freed_seats(now=_noon_before(date))
    for uid in range(100, 106):
        await _vote(service, poll, uid)
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.seats_by_lift["8:30"] == 4
    assert day.seats_left("8:30") == 6
    assert day.waiting_by_lift["8:30"] == 1
    assert "8:30" not in day.running_lift_times
    assert day.confirmed_lift_times(106) == ()
    await payments.offer_freed_seats(now=_noon_before(date) + timedelta(minutes=1))
    day = await payments.day_bookings(date)
    assert day is not None and day.seats_left("8:30") == 6


async def test_order_labels_and_save_notice_use_actual_holders(db: SharedDatabase) -> None:
    service, payments, client, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.offer_freed_seats(now=_noon_before(date))
    await _vote(service, poll, 100)
    view = await service.booking_order_view(service_date=date, lift_time="8:30")
    assert next(r for r in view["riders"] if r["user_id"] == 110)["waitlisted"]
    ids = [102, 101, *range(103, 111)]
    preview = await service.preview_booking_order(
        service_date=date,
        lift_time="8:30",
        ordered_user_ids=ids,
    )
    assert preview["promoted"] == [110]
    await service.save_booking_order(
        service_date=date,
        lift_time="8:30",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    assert any("Now holds a seat" in r.text and "tg://user?id=110" in r.text for r in client.sent)


async def test_offer_tap_after_departure_closes_offer_without_a_scan(
    db: SharedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from sqlalchemy import select
    from tests.unit.test_payments_service import ZONE, _lift_sends, _offer_id

    from veloexpress_bot.db.models import SeatOffer
    from veloexpress_bot.polls.liftsignals import lift_departure_at
    from veloexpress_bot.polls.seatoffer import GONE_TEXT

    service, payments, client, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.offer_freed_seats(now=_noon_before(date))
    await _vote(service, poll, 100)
    await payments.offer_freed_seats(now=_noon_before(date) + timedelta(minutes=1))
    offer_id = _offer_id((await _lift_sends(client))[0])
    departure = lift_departure_at(date, "8:30", zone=ZONE)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (departure + timedelta(seconds=1)).astimezone(tz)

    monkeypatch.setattr("veloexpress_core.payments.datetime", Clock)
    assert (
        await payments.respond_to_seat_offer(
            offer_id=offer_id,
            telegram_user_id=110,
            accept=True,
        )
        == GONE_TEXT
    )
    async with db.session() as session:
        offer = await session.scalar(select(SeatOffer).where(SeatOffer.id == offer_id))
        assert offer is not None and offer.status == "closed"


async def test_new_waitlisted_vote_is_held_before_first_scan(db: SharedDatabase) -> None:
    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=10)
    await _vote(service, poll, 110, 0)
    await _vote(service, poll, 100)
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.waitlist_by_lift["8:30"] == (110,)
    assert 110 not in day.current_seat_holders_by_lift["8:30"]


async def test_withdrawing_host_removes_guests_in_vote_transaction(db: SharedDatabase) -> None:
    from sqlalchemy import select

    from veloexpress_bot.db.models import GuestSeat

    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=4)
    await payments.adjust_guest_seats(
        service_date=date,
        telegram_user_id=100,
        lift_times=("8:30",),
        delta=1,
    )
    await _vote(service, poll, 100)
    async with db.session() as session:
        assert not (await session.scalars(select(GuestSeat).where(GuestSeat.count > 0))).all()
    day = await payments.day_bookings(date)
    assert day is not None and day.seats_by_lift["8:30"] == 3


async def test_orphan_guests_do_not_occupy_seats_and_can_be_removed(db: SharedDatabase) -> None:
    from sqlalchemy import select
    from tests.unit.test_payments_service import CHAT_ID, LIFT_THREAD

    from veloexpress_bot.db.models import GuestSeat

    _, payments, _, _, date = await _setup(db)
    async with db.session() as session:
        session.add(
            GuestSeat(
                environment="test",
                chat_id=CHAT_ID,
                thread_id=LIFT_THREAD,
                service_date=date,
                lift_time="8:30",
                host_user_id=999,
                count=2,
            )
        )
        await session.commit()
    day = await payments.day_bookings(date)
    assert day is not None and day.seats_by_lift["8:30"] == 0
    assert day.guest_seats(999, lift_times=("8:30",)) == 0
    assert (
        await payments.adjust_guest_seats(
            service_date=date,
            telegram_user_id=999,
            lift_times=("8:30",),
            delta=-1,
        )
        == "Guests updated."
    )
    async with db.session() as session:
        assert (await session.scalars(select(GuestSeat.count))).one() == 1


async def test_cancellation_refund_does_not_retain_money_for_waitlist(db: SharedDatabase) -> None:
    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=5)
    await _fill(service, poll, 1, riders=10, first_user_id=200)
    await _vote(service, poll, 100, 0, 1)
    await payments.claim(
        service_date=date,
        telegram_user_id=100,
        username="rider100",
        full_name="Rider 100",
        acknowledged=True,
    )
    await service.cancel_lift(service_date=date, lift_time="8:30", admin_user_id=1)
    report = await payments.cancellation_report(service_date=date, cancelled_lift_time="8:30")
    assert report is not None and "Total to return: 15 GEL" in report


async def test_deadline_obligation_does_not_occupy_a_freed_seat(db: SharedDatabase) -> None:
    from tests.unit.test_payments_service import _after_deadline

    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.claim(
        service_date=date,
        telegram_user_id=100,
        username="rider100",
        full_name="Rider 100",
        acknowledged=True,
    )
    await payments.capture_deadline_rosters(now=_after_deadline(date))
    await _vote(service, poll, 100)
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.seats_by_lift["8:30"] == 9
    assert day.seats_left("8:30") == 1
    assert day.waiting_by_lift["8:30"] == 1
    assert day.confirmed_lift_times(100) == ("8:30",)
    assert 100 not in day.current_seat_holders_by_lift["8:30"]
    assert 110 not in day.current_seat_holders_by_lift["8:30"]


@pytest.mark.parametrize("withdraw_host", [True, False])
async def test_guest_removal_after_deadline_keeps_paid_obligation(
    db: SharedDatabase,
    withdraw_host: bool,
) -> None:
    from tests.unit.test_payments_service import _after_deadline

    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=4)
    await payments.adjust_guest_seats(
        service_date=date, telegram_user_id=100, lift_times=("8:30",), delta=1
    )
    await payments.claim(
        service_date=date,
        telegram_user_id=100,
        username="rider100",
        full_name="Rider 100",
        acknowledged=True,
    )
    await payments.capture_deadline_rosters(now=_after_deadline(date))
    if withdraw_host:
        await _vote(service, poll, 100)
    else:
        assert (
            await payments.adjust_guest_seats(
                service_date=date,
                telegram_user_id=100,
                lift_times=("8:30",),
                delta=-1,
            )
            == "Guests updated."
        )
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.seats_by_lift["8:30"] == (3 if withdraw_host else 4)
    assert "8:30" in day.running_lift_times
    assert day.guest_seats(100) == 1  # Frozen financial obligation only.
    assert day.paid_amount_by_user[100] == 30


async def test_newcomer_inside_freeze_lead_is_not_held(
    db: SharedDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from tests.unit.test_payments_service import ZONE

    from veloexpress_bot.polls.liftsignals import lift_departure_at

    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=10)
    departure = lift_departure_at(date, "8:30", zone=ZONE)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (departure - timedelta(minutes=30)).astimezone(tz)

    monkeypatch.setattr("veloexpress_core.lifts.datetime", Clock)
    await _vote(service, poll, 110, 0)
    await _vote(service, poll, 100)
    day = await payments.day_bookings(date)
    assert day is not None
    assert 110 in day.current_seat_holders_by_lift["8:30"]
    assert day.waitlist_by_lift["8:30"] == ()


def test_card_and_monitor_count_held_seats_not_votes() -> None:
    """A rider waiting for a seat offer holds no seat on any surface."""
    from veloexpress_bot.bookings.render import BookingLiftStatus
    from veloexpress_bot.polls.card import _state
    from veloexpress_bot.polls.render import LiftAvailability, WaitlistRider

    # Four seated voters and one rider waiting for the offered seat.
    monitor = BookingLiftStatus(time="10:00", vote_count=5, manual_count=0, waitlisted_count=1)
    assert monitor.seat_count == 4
    assert monitor.waiting_count == 1
    assert not monitor.running

    card = LiftAvailability(time="10:00", seat_count=9, waitlist=(WaitlistRider(7, "Nika"),))
    assert _state(card, deadline_passed=False)[0] == "WAITLIST +1"


async def test_seat_taken_after_the_deadline_on_a_locked_lift_can_be_paid(
    db: SharedDatabase,
) -> None:
    """The roster keeps the lift running; a rider who joins it later is on it too."""
    from tests.unit.test_payments_service import (
        _after_deadline,
        _answer,
        _future_saturday,
        _lift_sends,
        _offer_id,
    )

    date = _future_saturday()
    service, payments, client, poll, _ = await _setup(db, service_date=date)
    await _fill(service, poll, 0, riders=11)
    late = _after_deadline(date)
    await payments.capture_deadline_rosters(now=late)
    await payments.offer_freed_seats(now=late)
    # Seven of the ten leave: three live seats, far below the minimum.
    for uid in range(100, 107):
        await _vote(service, poll, uid)
    await payments.offer_freed_seats(now=late + timedelta(minutes=1))
    offer = _offer_id((await _lift_sends(client))[-1])
    assert await _answer(payments, offer, 110, accept=True) == "✅ The seat is yours."
    day = await payments.day_bookings(date)
    assert day is not None
    assert "8:30" in day.running_lift_times
    assert day.confirmed_lift_times(110) == ("8:30",)
    outcome = await payments.claim(
        service_date=date, telegram_user_id=110, username="rider110", full_name="Rider 110"
    )
    assert "not booked" not in outcome.text.lower()


async def test_a_guest_cannot_take_a_seat_that_is_on_offer(db: SharedDatabase) -> None:
    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.offer_freed_seats(now=_noon_before(date))
    await _vote(service, poll, 100)
    for host in (110, 101):
        await payments.adjust_guest_seats(
            service_date=date, telegram_user_id=host, lift_times=("8:30",), delta=1
        )
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.seats_left("8:30") == 1
    assert day.guest_seats(110, lift_times=("8:30",)) == 0
    assert day.guest_seats(101, lift_times=("8:30",)) == 0


async def test_an_offline_seat_can_take_a_seat_a_held_rider_waits_for(
    db: SharedDatabase,
) -> None:
    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=11)
    await payments.offer_freed_seats(now=_noon_before(date))
    await _vote(service, poll, 100)
    await service.adjust_manual_booking(
        service_date=date, lift_time="8:30", delta=1, admin_user_id=1
    )
    day = await payments.day_bookings(date)
    assert day is not None
    assert day.seats_by_lift["8:30"] == 10
    assert day.seats_left("8:30") == 0
    with pytest.raises(ValueError, match="No seats left"):
        await service.adjust_manual_booking(
            service_date=date, lift_time="8:30", delta=1, admin_user_id=1
        )


async def test_the_scan_deletes_guest_seats_without_a_host(db: SharedDatabase) -> None:
    from sqlalchemy import select
    from tests.unit.test_payments_service import CHAT_ID, LIFT_THREAD

    from veloexpress_bot.db.models import GuestSeat

    service, payments, _, poll, date = await _setup(db)
    await _fill(service, poll, 0, riders=3)
    async with db.session() as session:
        for host, count in ((999, 2), (100, 1)):
            session.add(
                GuestSeat(
                    environment="test",
                    chat_id=CHAT_ID,
                    thread_id=LIFT_THREAD,
                    service_date=date,
                    lift_time="8:30",
                    host_user_id=host,
                    count=count,
                )
            )
        await session.commit()
    await payments.offer_freed_seats(now=_noon_before(date))
    async with db.session() as session:
        assert [row.host_user_id for row in await session.scalars(select(GuestSeat))] == [100]
