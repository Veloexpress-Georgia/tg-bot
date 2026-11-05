from datetime import UTC, date, datetime

from veloexpress_core.daybook import DayBookings
from veloexpress_core.ledger import RiderMoney, SeatMoney, day_ledger

PRICE = 20


def _seat(rider: RiderMoney, lift_time: str) -> SeatMoney:
    seat = rider.seat(lift_time)
    assert seat is not None
    return seat


def _day(**kwargs) -> DayBookings:
    defaults = dict(
        service_date=date(2026, 11, 8),
        running_lift_times=("10:00",),
        cancelled=False,
        polls_created_at=datetime(2026, 11, 5, tzinfo=UTC),
        price_gel=PRICE,
    )
    day = DayBookings(**{**defaults, **{k: v for k, v in kwargs.items() if k in defaults}})
    for key, value in kwargs.items():
        if key not in defaults:
            setattr(day, key, value)
    return day


def test_a_seat_on_a_lift_still_filling_is_booked_not_owed() -> None:
    day = _day(
        running_lift_times=("10:00",),
        seats_by_lift={"10:00": 5, "12:00": 2},
        booked_lift_times_by_user={1: ("10:00", "12:00")},
        seat_holders_by_lift={"10:00": (1,), "12:00": (1,)},
        paid_amount_by_user={1: PRICE},
    )
    rider = day_ledger(day).riders[1]

    assert rider.due_now_gel == PRICE
    assert rider.due_all_gel == 2 * PRICE
    assert rider.outstanding_gel == 0
    assert _seat(rider, "10:00").state == "paid"
    assert _seat(rider, "12:00").state == "pending"


def test_a_guest_left_unpaid_makes_the_seat_partial_not_paid() -> None:
    day = _day(
        seats_by_lift={"10:00": 6},
        booked_lift_times_by_user={1: ("10:00",)},
        seat_holders_by_lift={"10:00": (1,)},
        guests_by_user_lift={(1, "10:00"): 1},
        paid_amount_by_user={1: PRICE},
    )
    ledger = day_ledger(day)
    rider = ledger.riders[1]

    assert _seat(rider, "10:00").state == "partial"
    assert rider.outstanding_gel == PRICE
    assert [r.telegram_user_id for r in ledger.outstanding] == [1]


def test_guests_are_billed_to_a_host_who_holds_no_seat() -> None:
    day = _day(
        seats_by_lift={"10:00": 7},
        booked_lift_times_by_user={1: ("10:00",)},
        seat_holders_by_lift={"10:00": ()},
        waitlist_by_lift={"10:00": (1,)},
        guests_by_user_lift={(1, "10:00"): 2, (2, "10:00"): 1},
    )
    ledger = day_ledger(day)

    waitlisted_host = ledger.riders[1]
    assert _seat(waitlisted_host, "10:00").waitlisted
    assert waitlisted_host.due_now_gel == 2 * PRICE
    # The host withdrew their own vote and kept the guest.
    absent_host = ledger.riders[2]
    assert absent_host.due_now_gel == PRICE
    assert {r.telegram_user_id for r in ledger.outstanding} == {1, 2}


def test_a_waitlisted_rider_without_guests_owes_nothing() -> None:
    day = _day(
        seats_by_lift={"10:00": 11},
        booked_lift_times_by_user={1: ("10:00",)},
        waitlist_by_lift={"10:00": (1,)},
    )
    rider = day_ledger(day).riders[1]

    assert rider.due_now_gel == 0
    assert _seat(rider, "10:00").state == "waitlist"


def test_money_the_deadline_froze_stays_on_its_lift() -> None:
    # Paid 20 for 10:00 before the deadline, then also took 12:00 afterwards.
    day = _day(
        running_lift_times=("10:00", "12:00"),
        seats_by_lift={"10:00": 5, "12:00": 6},
        booked_lift_times_by_user={1: ("10:00", "12:00")},
        seat_holders_by_lift={"10:00": (1,), "12:00": (1,)},
        paid_amount_by_user={1: PRICE},
        locked_covered_by_user_lift={(1, "12:00"): 1},
    )
    rider = day_ledger(day).riders[1]

    assert _seat(rider, "12:00").state == "paid"
    assert _seat(rider, "10:00").state == "unpaid"
    assert rider.outstanding_gel == PRICE


def test_credit_is_prepaid_while_a_lift_fills_and_unallocated_beyond_it() -> None:
    day = _day(
        seats_by_lift={"10:00": 5, "12:00": 2},
        booked_lift_times_by_user={1: ("10:00", "12:00"), 2: ()},
        seat_holders_by_lift={"10:00": (1,), "12:00": (1,)},
        paid_amount_by_user={1: 3 * PRICE, 2: PRICE},
    )
    ledger = day_ledger(day)

    assert ledger.riders[1].prepaid_gel == PRICE
    assert ledger.riders[1].unallocated_gel == PRICE
    # Paid, then left the poll: nothing held, all of it unallocated.
    assert ledger.riders[2].unallocated_gel == PRICE
    assert not ledger.riders[2].holds_seat


def test_offline_seats_count_as_covered_but_are_never_owed() -> None:
    day = _day(
        seats_by_lift={"10:00": 5},
        manual_by_lift={"10:00": 2},
        booked_lift_times_by_user={1: ("10:00",), 2: ("10:00",), 3: ("10:00",)},
        seat_holders_by_lift={"10:00": (1, 2, 3)},
        paid_amount_by_user={1: PRICE, 2: PRICE, 3: PRICE},
    )
    ledger = day_ledger(day)

    assert ledger.covered_by_lift["10:00"] == 5
    assert ledger.funded("10:00")
    assert ledger.owed_gel == 3 * PRICE
    assert ledger.outstanding_gel == 0


def test_cash_is_marked_only_while_misho_still_collects_it() -> None:
    day = _day(
        seats_by_lift={"10:00": 5},
        booked_lift_times_by_user={1: ("10:00",), 2: ("10:00",)},
        seat_holders_by_lift={"10:00": (1, 2)},
        paid_amount_by_user={1: PRICE, 2: PRICE},
        payment_method_by_user={1: "cash", 2: "cash"},
        cash_amount_by_user={1: PRICE, 2: PRICE},
        verified_user_ids=frozenset({2}),
    )
    ledger = day_ledger(day)

    assert ledger.riders[1].pays_cash
    assert not ledger.riders[2].pays_cash
