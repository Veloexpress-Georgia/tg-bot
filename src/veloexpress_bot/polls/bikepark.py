"""Bike park copy describes a session, never a fixed number of booked lifts."""

from datetime import date, datetime

from veloexpress_bot.polls.render import DAY_LABELS, PollDraft

MINIMUM_BIKE_PARK_RIDERS = 6


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
