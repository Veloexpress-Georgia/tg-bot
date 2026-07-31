from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import Literal

from veloexpress_bot.polls.defaults import MINIMUM_RIDERS, PaymentTerms
from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

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

    @property
    def running(self) -> bool:
        return not self.cancelled and self.seats >= MINIMUM_RIDERS


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


@dataclass(frozen=True)
class LiftDecision:
    memory: LiftMemory
    event: LiftEvent | None = None


def decide_lift_signal(
    signal: LiftSignal,
    memory: LiftMemory,
    *,
    now: datetime,
    departure_at: datetime,
    is_day_opener: bool,
) -> LiftDecision:
    """Decide at most one event for a lift; never repeats an announcement."""
    if signal.cancelled:
        return LiftDecision(memory=memory)
    if signal.seats >= MINIMUM_RIDERS:
        return _decide_running(
            signal,
            memory,
            now=now,
            departure_at=departure_at,
            is_day_opener=is_day_opener,
        )
    return _decide_short(signal, memory, now=now)


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


def _decide_short(signal: LiftSignal, memory: LiftMemory, *, now: datetime) -> LiftDecision:
    if memory.threshold_notified_at is None:
        # Never announced as running, so there is nothing to walk back.
        return LiftDecision(memory=memory)
    if memory.undershoot_since is None:
        return LiftDecision(memory=replace(memory, undershoot_since=now))
    if memory.undershoot_notified_at is not None:
        return LiftDecision(memory=memory)
    if now - memory.undershoot_since < UNDERSHOOT_DEBOUNCE:
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
    if not any(_is_short(signal) for signal in signals):
        # Everything already runs; there is nothing to ask the group for.
        return False
    return deadline_at - DEADLINE_REMINDER_LEAD <= now <= deadline_at


def render_deadline_reminder(
    service_date: date,
    signals: Iterable[LiftSignal],
    *,
    terms: PaymentTerms,
) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    month = EN_SHORT_MONTHS[service_date.month]
    lines = [
        f"⏳ Tomorrow · {day}, {service_date.day} {month} — book and pay by {terms.deadline_time}.",
        "",
    ]
    ordered = sorted(signals, key=lambda signal: lift_minutes(signal.lift_time))
    lines.extend(_reminder_line(signal) for signal in ordered)
    if terms.link:
        lines.extend(("", terms.rules()[-1]))
    return "\n".join(lines)


def _reminder_line(signal: LiftSignal) -> str:
    if signal.cancelled:
        return f"{signal.lift_time} — ❌ cancelled"
    if signal.seats < MINIMUM_RIDERS:
        missing = MINIMUM_RIDERS - signal.seats
        return f"{signal.lift_time} — {signal.seats}/{MINIMUM_RIDERS} · needs {missing} more"
    return f"{signal.lift_time} — {signal.seats} riders · running"


def _is_short(signal: LiftSignal) -> bool:
    return not signal.cancelled and signal.seats < MINIMUM_RIDERS


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
    *,
    terms: PaymentTerms,
) -> str:
    """One message per event kind per tick, so a busy tick is not a spam burst."""
    if not events:
        msg = "A lift notice needs at least one event."
        raise ValueError(msg)
    ordered = sorted(events, key=lambda event: (event.service_date, lift_minutes(event.lift_time)))
    if kind == "confirmed":
        return _confirmed_notice(ordered, terms=terms)
    if kind == "undershoot":
        return _undershoot_notice(ordered)
    return _departure_notice(ordered)


def _confirmed_notice(events: list[LiftEvent], *, terms: PaymentTerms) -> str:
    if len(events) == 1:
        event = events[0]
        headline = f"✅ {_lift_label(event)} is running — {event.seats} riders booked."
    else:
        headline = "\n".join(
            (
                "✅ These lifts are running:",
                "",
                *(f"{_lift_label(event)} — {event.seats} riders" for event in events),
            )
        )
    # One line, not the whole rulebook: the rules live in the pinned notice and the
    # amount lives on the board this links to. Three lines of money talk per confirmed
    # lift read as spam in the lift topic, which is what admins said.
    if terms.link:
        return f"{headline} {terms.pay_link('💸 Pay')}"
    return headline


def _undershoot_notice(events: list[LiftEvent]) -> str:
    if len(events) == 1:
        event = events[0]
        return (
            f"⚠️ {_lift_label(event)} is short — {event.seats}/{MINIMUM_RIDERS} riders. "
            f"{_needs_phrase(event.seats)}"
        )
    lines = ["⚠️ These lifts dropped below the minimum:", ""]
    lines.extend(
        f"{_lift_label(event)} — {event.seats}/{MINIMUM_RIDERS} · {_needs_phrase(event.seats)}"
        for event in events
    )
    return "\n".join(lines)


def _departure_notice(events: list[LiftEvent]) -> str:
    lines = ["🚐 First lift of the day, leaving soon — please be on time.", ""]
    lines.extend(_lift_label(event) for event in events)
    return "\n".join(lines)


def _needs_phrase(seats: int) -> str:
    missing = MINIMUM_RIDERS - seats
    if missing <= 1:
        return "One more and it runs."
    return f"Needs {missing} more to run."


def _lift_label(event: LiftEvent) -> str:
    day = SHORT_DAY_LABELS.get(event.service_date.weekday(), "Lift day")
    month = EN_SHORT_MONTHS[event.service_date.month]
    return f"{event.lift_time} · {day}, {event.service_date.day} {month}"


def _parse_lift_time(lift_time: str) -> tuple[int, int]:
    hour_text, minute_text = lift_time.split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        msg = f"Invalid lift time: {lift_time}"
        raise ValueError(msg)
    return hour, minute
