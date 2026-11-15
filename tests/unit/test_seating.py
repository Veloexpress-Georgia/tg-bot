from datetime import UTC, datetime, timedelta

from veloexpress_bot.polls.seating import SeatCandidate, allocate_seats

START = datetime(2026, 11, 14, 8, tzinfo=UTC)


def _riders(count: int) -> list[SeatCandidate]:
    return [
        SeatCandidate(telegram_user_id=i, label=f"R{i}", booked_at=START + timedelta(minutes=i))
        for i in range(count)
    ]


def test_held_back_riders_wait_even_with_a_free_seat() -> None:
    allocation = allocate_seats(_riders(4), capacity=3, held_back={1})

    assert [c.telegram_user_id for c in allocation.holders] == [0, 2, 3]
    assert [c.telegram_user_id for c in allocation.waitlist] == [1]


def test_held_back_riders_go_behind_the_open_waitlist() -> None:
    allocation = allocate_seats(_riders(5), capacity=2, held_back={0})

    assert [c.telegram_user_id for c in allocation.holders] == [1, 2]
    assert [c.telegram_user_id for c in allocation.waitlist] == [3, 4, 0]


def test_widening_waits_for_the_step_and_for_morning() -> None:
    from zoneinfo import ZoneInfo

    from veloexpress_bot.polls.seatoffer import parse_offer_callback, widen_due

    zone = ZoneInfo("Asia/Tbilisi")
    departure = datetime(2026, 11, 14, 11, 45, tzinfo=zone)
    asked = datetime(2026, 11, 13, 12, 0, tzinfo=zone)

    def due(now: datetime, since: datetime = asked) -> bool:
        return widen_due(now=now, widened_at=since, departure_at=departure)

    assert due(asked + timedelta(minutes=29)) is False
    assert due(asked + timedelta(minutes=30)) is True
    # Asleep: however long ago the first rider was asked.
    assert due(datetime(2026, 11, 13, 23, 0, tzinfo=zone)) is False
    assert due(datetime(2026, 11, 14, 7, 59, tzinfo=zone)) is False
    assert due(datetime(2026, 11, 14, 8, 0, tzinfo=zone)) is True
    # Within three hours of departure the step shortens.
    late = datetime(2026, 11, 14, 9, 0, tzinfo=zone)
    assert due(late + timedelta(minutes=9), late) is False
    assert due(late + timedelta(minutes=10), late) is True

    assert parse_offer_callback("seat:in:42") == ("in", 42)
    assert parse_offer_callback("seat:skip:7") == ("skip", 7)
    assert parse_offer_callback("seat:steal:7") is None
    assert parse_offer_callback("seat:in:x") is None
