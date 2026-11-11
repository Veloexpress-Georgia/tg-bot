"""Bike park copy describes a session, never a fixed number of booked lifts."""

from datetime import date, datetime

from veloexpress_bot.polls.defaults import BIKE_PARK_MINIMUM, DEFAULT_LIFTS
from veloexpress_bot.polls.render import DAY_LABELS, SHORT_DAY_LABELS, PollDraft

MINIMUM_BIKE_PARK_RIDERS = BIKE_PARK_MINIMUM

# Earliest and latest start for the weekend poll's bike park. Five to seven
# lifts take most of an afternoon, so a later start ends in the dark.
WEEKEND_BIKE_PARK_WINDOW = ("11:45", "13:30")


def normalize_start_time(value: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise ValueError("Start time must use HH:MM.") from error
    return f"{hour:02d}:{minute:02d}"


def validate_bike_park_date(service_date: date, start_time: str, now: datetime) -> str:
    normalized = normalize_start_time(start_time)
    if not 0 <= (service_date - now.date()).days <= 7:
        raise ValueError("Choose a date within the next seven days.")
    if service_date == now.date() and normalized <= now.strftime("%H:%M"):
        raise ValueError("Choose a start time in the future.")
    return normalized


def render_bike_park_poll(service_date: date, start_time: str, price_gel: int) -> PollDraft:
    return PollDraft(
        question=(
            f"🚵 Bike park · {DAY_LABELS[service_date.weekday()]} {service_date:%d.%m.%Y}"
            f" · {start_time}\n"
            f"Vake · {price_gel} GEL per lift · minimum {MINIMUM_BIKE_PARK_RIDERS} riders\n"
            "Usually 5–7 lifts; decide on site. Riders can leave; lifts end below 6 riders.\n"
            "Pay for actual lifts afterwards: cash or bank transfer."
        ),
        options=("🚵 I'm coming", "👀 View answers"),
        allows_multiple_answers=False,
    )


def is_weekend_bike_park_day(service_date: date) -> bool:
    return service_date.weekday() >= 5


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def choose_weekend_bike_park_start(
    running_lift_times: tuple[str, ...] | frozenset[str] | set[str],
    window: tuple[str, str] = WEEKEND_BIKE_PARK_WINDOW,
) -> str | None:
    """The first lift slot after every running lift, inside the window.

    Ordinary lifts keep priority: the bike park only takes a slot no running lift
    needs. None when the running lifts leave no slot before the latest start.
    """
    after = max((_minutes(time) for time in running_lift_times), default=-1)
    earliest, latest = (_minutes(value) for value in window)
    return next(
        (
            lift.time
            for lift in DEFAULT_LIFTS
            if after < _minutes(lift.time) and earliest <= _minutes(lift.time) <= latest
        ),
        None,
    )


def lifts_replaced_by_bike_park(start: str, idle_lift_times: tuple[str, ...]) -> tuple[str, ...]:
    """Lifts below the minimum from the bike park start onwards: the van is busy."""
    return tuple(time for time in idle_lift_times if _minutes(time) >= _minutes(start))


def render_weekend_bike_park_notice(
    service_date: date,
    start_time: str,
    *,
    price_gel: int,
    rider_count: int,
    replaced_lift_times: tuple[str, ...],
) -> str:
    """One message, once: the time is fixed and nothing else is announced."""
    lines = [
        f"🚵 <b>Bike park is on · {SHORT_DAY_LABELS[service_date.weekday()]} "
        f"{service_date:%d.%m} · {start_time}</b>",
        f"From Vake · {rider_count} voted · {price_gel} GEL per lift, pay on site.",
    ]
    if replaced_lift_times:
        label = "Lift" if len(replaced_lift_times) == 1 else "Lifts"
        verb = "is" if len(replaced_lift_times) == 1 else "are"
        lines.append(f"❌ {label} {', '.join(replaced_lift_times)} {verb} cancelled for it.")
    lines.append("Can't come? Remove your bike park vote.")
    return "\n".join(lines)
