from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from veloexpress_bot.polls.liftsignals import (
    LiftEvent,
    LiftMemory,
    LiftSignal,
    booking_deadline_at,
    day_opener_time,
    decide_deadline_reminder,
    decide_lift_signal,
    lift_departure_at,
    render_deadline_reminder,
    render_lift_signal_notice,
)

SATURDAY = date(2026, 7, 18)
TBILISI = ZoneInfo("Asia/Tbilisi")


def _signal(seats: int, *, lift_time: str = "8:30", cancelled: bool = False) -> LiftSignal:
    return LiftSignal(
        service_date=SATURDAY,
        lift_time=lift_time,
        seats=seats,
        cancelled=cancelled,
    )


def _decide(
    signal: LiftSignal,
    memory: LiftMemory,
    *,
    now: datetime,
    is_day_opener: bool = False,
):  # type: ignore[no-untyped-def]
    return decide_lift_signal(
        signal,
        memory,
        now=now,
        departure_at=lift_departure_at(SATURDAY, signal.lift_time, zone=TBILISI),
        is_day_opener=is_day_opener,
    )


def test_threshold_is_announced_once_and_never_again() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)

    first = _decide(_signal(5), LiftMemory(), now=now)
    assert first.event is not None
    assert first.event.kind == "confirmed"
    assert first.memory.confirmed_at == now

    # More riders join: already announced, so the bot stays quiet.
    later = _decide(_signal(7), first.memory, now=now + timedelta(minutes=5))
    assert later.event is None
    assert later.memory.confirmed_at == now


def test_undershoot_waits_for_the_debounce_then_speaks_once() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    confirmed = _decide(_signal(5), LiftMemory(), now=now).memory

    dropped = _decide(_signal(4), confirmed, now=now + timedelta(minutes=1))
    assert dropped.event is None, "a fresh drop is probably someone re-picking slots"
    assert dropped.memory.undershoot_since == now + timedelta(minutes=1)

    still_early = _decide(_signal(4), dropped.memory, now=now + timedelta(minutes=5))
    assert still_early.event is None

    settled = _decide(_signal(4), dropped.memory, now=now + timedelta(minutes=12))
    assert settled.event is not None
    assert settled.event.kind == "undershoot"

    repeat = _decide(_signal(4), settled.memory, now=now + timedelta(minutes=30))
    assert repeat.event is None


def test_a_recovered_lift_stays_quiet_but_can_report_a_later_drop() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    confirmed = _decide(_signal(5), LiftMemory(), now=now).memory
    dropped = _decide(_signal(4), confirmed, now=now + timedelta(minutes=1)).memory
    notified = _decide(_signal(4), dropped, now=now + timedelta(minutes=12)).memory

    recovered = _decide(_signal(5), notified, now=now + timedelta(minutes=20))
    assert recovered.event is None, "coming back to five is not worth a second message"
    assert recovered.memory.undershoot_since is None
    assert recovered.memory.undershoot_notified_at is None

    dropped_again = _decide(_signal(4), recovered.memory, now=now + timedelta(minutes=40))
    settled_again = _decide(_signal(4), dropped_again.memory, now=now + timedelta(minutes=55))
    assert settled_again.event is not None
    assert settled_again.event.kind == "undershoot"


def test_a_cancelled_lift_never_signals() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    decision = _decide(_signal(8, cancelled=True), LiftMemory(), now=now)
    assert decision.event is None
    assert decision.memory == LiftMemory()


def test_a_lift_below_the_minimum_that_never_ran_says_nothing() -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    decision = _decide(_signal(3), LiftMemory(), now=now)
    assert decision.event is None
    assert decision.memory == LiftMemory()


def test_departure_ping_only_fires_for_the_day_opener_inside_the_window() -> None:
    departure = lift_departure_at(SATURDAY, "8:30", zone=TBILISI)
    confirmed = _decide(_signal(5), LiftMemory(), now=departure - timedelta(days=1)).memory

    too_early = _decide(
        _signal(5),
        confirmed,
        now=departure - timedelta(minutes=45),
        is_day_opener=True,
    )
    assert too_early.event is None

    not_the_opener = _decide(
        _signal(5),
        confirmed,
        now=departure - timedelta(minutes=20),
        is_day_opener=False,
    )
    assert not_the_opener.event is None, "later lifts self-correct; the van is already cycling"

    due = _decide(
        _signal(5),
        confirmed,
        now=departure - timedelta(minutes=20),
        is_day_opener=True,
    )
    assert due.event is not None
    assert due.event.kind == "departure"

    already_sent = _decide(
        _signal(5),
        due.memory,
        now=departure - timedelta(minutes=10),
        is_day_opener=True,
    )
    assert already_sent.event is None

    too_late = _decide(
        _signal(5),
        confirmed,
        now=departure - timedelta(minutes=2),
        is_day_opener=True,
    )
    assert too_late.event is None


def test_day_opener_is_the_earliest_lift_that_can_run() -> None:
    signals = (
        _signal(2, lift_time="8:30"),
        _signal(6, lift_time="10:00"),
        _signal(9, lift_time="11:45"),
        _signal(9, lift_time="13:30", cancelled=True),
    )
    assert day_opener_time(signals) == "10:00"
    assert day_opener_time((_signal(1, lift_time="8:30"),)) is None


def test_notices_read_naturally_for_one_lift_and_for_many() -> None:
    confirmed = render_lift_signal_notice(
        "confirmed",
        (LiftEvent("confirmed", SATURDAY, "8:30", 5),),
    )
    assert confirmed == "✅ 8:30 · Sat, 18 Jul is running — 5 riders booked."

    batched = render_lift_signal_notice(
        "confirmed",
        (
            LiftEvent("confirmed", SATURDAY, "10:00", 6),
            LiftEvent("confirmed", SATURDAY, "8:30", 5),
        ),
    )
    assert batched.splitlines()[0] == "✅ These lifts are running:"
    # Batched into one message, earliest first, so a busy tick is not a burst.
    assert batched.splitlines()[2:] == [
        "8:30 · Sat, 18 Jul — 5 riders",
        "10:00 · Sat, 18 Jul — 6 riders",
    ]

    short = render_lift_signal_notice(
        "undershoot",
        (LiftEvent("undershoot", SATURDAY, "8:30", 4),),
    )
    assert short == "⚠️ 8:30 · Sat, 18 Jul is short — 4/5 riders. One more and it runs."

    scarce = render_lift_signal_notice(
        "undershoot",
        (LiftEvent("undershoot", SATURDAY, "8:30", 2),),
    )
    assert "Needs 3 more to run." in scarce

    departure = render_lift_signal_notice(
        "departure",
        (LiftEvent("departure", SATURDAY, "8:30", 6),),
    )
    assert departure.splitlines()[0].startswith("🚐 First lift of the day")
    assert departure.splitlines()[-1] == "8:30 · Sat, 18 Jul"


def test_the_deadline_sits_on_the_evening_before_the_lift_day() -> None:
    deadline = booking_deadline_at(SATURDAY, "20:00", zone=TBILISI)

    assert deadline == datetime(2026, 7, 17, 20, 0, tzinfo=TBILISI)


def test_the_deadline_reminder_fires_once_while_a_lift_can_still_fill() -> None:
    deadline = booking_deadline_at(SATURDAY, "20:00", zone=TBILISI)
    short_day = (_signal(3, lift_time="8:30"), _signal(6, lift_time="10:00"))

    def due(now: datetime, *, already_reminded: bool = False) -> bool:
        return decide_deadline_reminder(
            short_day,
            now=now,
            deadline_at=deadline,
            already_reminded=already_reminded,
        )

    assert due(deadline - timedelta(hours=3)) is False, "too early to be the last word"
    assert due(deadline - timedelta(hours=1)) is True
    assert due(deadline + timedelta(minutes=1)) is False, "the deadline has passed"
    assert due(deadline - timedelta(hours=1), already_reminded=True) is False


def test_no_reminder_when_every_lift_already_runs() -> None:
    deadline = booking_deadline_at(SATURDAY, "20:00", zone=TBILISI)

    assert (
        decide_deadline_reminder(
            (_signal(6, lift_time="8:30"), _signal(9, lift_time="10:00")),
            now=deadline - timedelta(hours=1),
            deadline_at=deadline,
            already_reminded=False,
        )
        is False
    )


def test_a_cancelled_lift_does_not_justify_a_reminder() -> None:
    deadline = booking_deadline_at(SATURDAY, "20:00", zone=TBILISI)

    assert (
        decide_deadline_reminder(
            (_signal(6, lift_time="8:30"), _signal(0, lift_time="10:00", cancelled=True)),
            now=deadline - timedelta(hours=1),
            deadline_at=deadline,
            already_reminded=False,
        )
        is False
    )


def test_the_reminder_states_the_deadline_and_what_each_lift_needs() -> None:
    text = render_deadline_reminder(
        SATURDAY,
        (
            _signal(6, lift_time="10:00"),
            _signal(3, lift_time="8:30"),
            _signal(0, lift_time="11:45", cancelled=True),
        ),
        deadline_time="20:00",
    )

    assert text.splitlines() == [
        "⏳ Tomorrow · Sat, 18 Jul — book and pay by 20:00.",
        "",
        "8:30 — 3/5 · needs 2 more",
        "10:00 — 6 riders · running",
        "11:45 — ❌ cancelled",
    ]


def test_rendering_without_events_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        render_lift_signal_notice("confirmed", ())
