from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Literal

from veloexpress_bot.payment_copy import CASH_ON_SITE_TEXT
from veloexpress_bot.polls.defaults import MINIMUM_RIDERS, PaymentTerms, StartLocation
from veloexpress_bot.wording import day_label, plural

# Dropping below the minimum is usually one rider re-picking slots, so the
# notice waits for the dust to settle instead of firing on every vote change.
UNDERSHOOT_DEBOUNCE = timedelta(minutes=10)

# Lateness is what kills the day's opening lift; later ones self-correct because
# the van is already cycling. Only the opener gets a departure ping.
DEPARTURE_PING_LEAD = timedelta(minutes=30)

# A ping that lands seconds before departure is noise, not a reminder. This also
# keeps a lift that only reaches the minimum at the last moment from pinging.
DEPARTURE_PING_CUTOFF = timedelta(minutes=5)

# How long before the booking deadline the group gets its one reminder. Early
# enough that somebody can still decide to come, late enough to be the last word.
DEADLINE_REMINDER_LEAD = timedelta(hours=2)

LiftEventKind = Literal["confirmed", "undershoot", "departure"]


@dataclass(frozen=True)
class LiftSignal:
    """Current world for one lift: booked seats and whether it was cancelled."""

    service_date: date
    lift_time: str
    seats: int
    cancelled: bool = False
    covered_seats: int = 0
    # Had the minimum when booking closed: still happening after late drop-outs.
    running_locked: bool = False

    @property
    def running(self) -> bool:
        return not self.cancelled and (self.seats >= MINIMUM_RIDERS or self.running_locked)


@dataclass(frozen=True)
class LiftMemory:
    """What the bot already announced about one lift."""

    confirmed_at: datetime | None = None
    threshold_notified_at: datetime | None = None
    undershoot_since: datetime | None = None
    undershoot_notified_at: datetime | None = None
    departure_ping_at: datetime | None = None


@dataclass(frozen=True)
class LiftEvent:
    kind: LiftEventKind
    service_date: date
    lift_time: str
    seats: int
    occurred_at: datetime | None = None
    # Where the lift leaves from, for the departure ping. None when unknown.
    location: StartLocation | None = None


@dataclass(frozen=True)
class LiftDecision:
    memory: LiftMemory
    event: LiftEvent | None = None


def decide_lift_signal(
    signal: LiftSignal,
    memory: LiftMemory,
    *,
    now: datetime,
    deadline_at: datetime,
    departure_at: datetime,
    is_day_opener: bool,
) -> LiftDecision:
    """Decide at most one event for a lift; never repeats an announcement."""
    if signal.cancelled:
        return LiftDecision(memory=memory)
    if signal.running:
        return _decide_running(
            signal,
            memory,
            now=now,
            departure_at=departure_at,
            is_day_opener=is_day_opener,
        )
    return _decide_short(signal, memory, now=now, deadline_at=deadline_at)


def _decide_running(
    signal: LiftSignal,
    memory: LiftMemory,
    *,
    now: datetime,
    departure_at: datetime,
    is_day_opener: bool,
) -> LiftDecision:
    if memory.threshold_notified_at is None:
        return LiftDecision(
            memory=replace(
                memory,
                confirmed_at=memory.confirmed_at or now,
                threshold_notified_at=now,
                undershoot_since=None,
                undershoot_notified_at=None,
            ),
            event=_event("confirmed", signal),
        )

    updated = memory
    if memory.undershoot_since is not None:
        # Recovered: stay quiet, but let a later drop speak up again.
        updated = replace(updated, undershoot_since=None, undershoot_notified_at=None)

    if _departure_ping_due(
        memory,
        now=now,
        departure_at=departure_at,
        is_day_opener=is_day_opener,
    ):
        return LiftDecision(
            memory=replace(updated, departure_ping_at=now),
            event=_event("departure", signal),
        )
    return LiftDecision(memory=updated)


def _decide_short(
    signal: LiftSignal, memory: LiftMemory, *, now: datetime, deadline_at: datetime
) -> LiftDecision:
    if memory.threshold_notified_at is None:
        # Never announced as running, so there is nothing to walk back.
        return LiftDecision(memory=memory)
    if memory.undershoot_since is None:
        return LiftDecision(memory=replace(memory, undershoot_since=now))
    if memory.undershoot_notified_at is not None:
        return LiftDecision(memory=memory)
    if now - memory.undershoot_since < UNDERSHOOT_DEBOUNCE:
        return LiftDecision(memory=memory)
    # Early vote changes are normal planning, not a reason to tag riders. Use
    # the same evening-before window as the day's general deadline reminder.
    if not deadline_at - DEADLINE_REMINDER_LEAD <= now <= deadline_at:
        return LiftDecision(memory=memory)
    return LiftDecision(
        memory=replace(memory, undershoot_notified_at=now),
        event=_event("undershoot", signal),
    )


def _departure_ping_due(
    memory: LiftMemory,
    *,
    now: datetime,
    departure_at: datetime,
    is_day_opener: bool,
) -> bool:
    if not is_day_opener or memory.departure_ping_at is not None:
        return False
    return departure_at - DEPARTURE_PING_LEAD <= now <= departure_at - DEPARTURE_PING_CUTOFF


def _event(kind: LiftEventKind, signal: LiftSignal) -> LiftEvent:
    return LiftEvent(
        kind=kind,
        service_date=signal.service_date,
        lift_time=signal.lift_time,
        seats=signal.seats,
    )


def day_opener_time(signals: Iterable[LiftSignal]) -> str | None:
    """Earliest lift of the day that currently has enough riders to run."""
    running = [signal for signal in signals if signal.running]
    if not running:
        return None
    return min(running, key=lambda signal: lift_minutes(signal.lift_time)).lift_time


def booking_deadline_at(service_date: date, deadline_time: str, *, zone: tzinfo) -> datetime:
    """The deadline sits on the evening before the lift day, not on the day itself."""
    hour, minute = _parse_lift_time(deadline_time)
    return datetime(
        service_date.year,
        service_date.month,
        service_date.day,
        hour,
        minute,
        tzinfo=zone,
    ) - timedelta(days=1)


def decide_deadline_reminder(
    signals: Iterable[LiftSignal],
    *,
    now: datetime,
    deadline_at: datetime,
    already_reminded: bool,
) -> bool:
    """One reminder per day, and only while a lift can still be saved."""
    if already_reminded:
        return False
    if not any(
        not signal.cancelled and (not signal.running or signal.covered_seats < MINIMUM_RIDERS)
        for signal in signals
    ):
        # Every active lift has enough riders and paid seats.
        return False
    return deadline_at - DEADLINE_REMINDER_LEAD <= now <= deadline_at


def render_deadline_reminder(
    service_date: date,
    signals: Iterable[LiftSignal],
    *,
    terms: PaymentTerms,
) -> str:
    """Kept fresh until the deadline, because this is the message people act on.

    A one-shot snapshot goes stale within the hour — riders book after it — and a
    stale count is worse than none. At the deadline the message is deleted: the
    poll and the live card already say everything after that, and a second list
    reading "deadline has passed" made late riders think booking was shut.
    """
    lines = [
        f"⏰ Tomorrow · {day_label(service_date)} — please book and transfer "
        f"by {terms.deadline_time} today.",
        "You can still book later while seats are free.",
        "",
    ]
    ordered = sorted(signals, key=lambda signal: lift_minutes(signal.lift_time))
    lines.extend(_reminder_line(signal) for signal in ordered)
    lines.extend(("", f"💵 {CASH_ON_SITE_TEXT}"))
    if terms.link:
        lines.extend(("", terms.link_line("Where to pay")))
    return "\n".join(lines)


def _reminder_line(signal: LiftSignal) -> str:
    if signal.cancelled:
        return f"{signal.lift_time} — ❌ cancelled"
    if not signal.running:
        # A lift that had the minimum when booking closed is happening, so it
        # never reads short.
        missing = MINIMUM_RIDERS - signal.seats
        return f"{signal.lift_time} — {signal.seats}/{MINIMUM_RIDERS} seats · needs {missing} more"
    if signal.covered_seats < MINIMUM_RIDERS:
        missing_paid = MINIMUM_RIDERS - signal.covered_seats
        return (
            f"{signal.lift_time} — {plural(signal.seats, 'seat')} · "
            f"{signal.covered_seats}/{MINIMUM_RIDERS} paid · "
            f"needs {plural(missing_paid, 'more paid seat', 'more paid seats')}"
        )
    return f"{signal.lift_time} — {plural(signal.seats, 'seat')} · minimum paid ✅"


def _is_short(signal: LiftSignal) -> bool:
    return not signal.cancelled and not signal.running


def lift_departure_at(service_date: date, lift_time: str, *, zone: tzinfo) -> datetime:
    hour, minute = _parse_lift_time(lift_time)
    return datetime(
        service_date.year,
        service_date.month,
        service_date.day,
        hour,
        minute,
        tzinfo=zone,
    )


def lift_minutes(lift_time: str) -> int:
    hour, minute = _parse_lift_time(lift_time)
    return hour * 60 + minute


def render_lift_signal_notice(
    kind: LiftEventKind,
    events: tuple[LiftEvent, ...],
) -> str:
    """One message per event kind per tick, so a busy tick is not a spam burst.

    A lift reaching the minimum is announced by the day's payment card, not here.
    """
    if not events:
        msg = "A lift notice needs at least one event."
        raise ValueError(msg)
    if kind == "confirmed":
        msg = "Confirmed lifts are announced on the payments board."
        raise ValueError(msg)
    ordered = sorted(events, key=lambda event: (event.service_date, lift_minutes(event.lift_time)))
    if kind == "undershoot":
        return _undershoot_notice(ordered)
    return _departure_notice(ordered)


def _undershoot_notice(events: list[LiftEvent]) -> str:
    if len(events) == 1:
        event = events[0]
        missing = MINIMUM_RIDERS - event.seats
        return (
            f"⚠️ {_lift_label(event)} has {event.seats}/{MINIMUM_RIDERS} seats. "
            f"It needs {plural(missing, 'more rider')} to run — "
            "invite a friend or check another lift."
        )
    lines = [f"⚠️ These lifts have fewer than {MINIMUM_RIDERS} seats again:", ""]
    lines.extend(
        f"{_lift_label(event)} — {event.seats}/{MINIMUM_RIDERS} seats · "
        f"needs {MINIMUM_RIDERS - event.seats} more"
        for event in events
    )
    lines.extend(("", "Invite a friend or check another lift."))
    return "\n".join(lines)


START_POINTS = {
    StartLocation.VAKE: "opposite Vake Park",
    StartLocation.JUSTICE_HALL: "Justice Hall",
}


def _departure_notice(events: list[LiftEvent]) -> str:
    lines = [
        f"🚐 {_lift_label(event)} leaves soon."
        + (f" Start: {START_POINTS[event.location]}." if event.location else "")
        for event in events
    ]
    lines.append("Please be on time.")
    return "\n".join(lines)


def _lift_label(event: LiftEvent) -> str:
    return f"{event.lift_time} · {day_label(event.service_date)}"


def _parse_lift_time(lift_time: str) -> tuple[int, int]:
    hour_text, minute_text = lift_time.split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        msg = f"Invalid lift time: {lift_time}"
        raise ValueError(msg)
    return hour, minute
