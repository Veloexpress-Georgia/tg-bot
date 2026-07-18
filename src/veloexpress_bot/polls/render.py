from dataclasses import dataclass
from datetime import date

from veloexpress_bot.polls.defaults import (
    CHECK_ANSWERS_OPTION,
    DEFAULT_LIFTS,
    PAYMENT_REMINDER,
    LiftTemplate,
    StartLocation,
)

DAY_LABELS: dict[int, str] = {5: "Saturday", 6: "Sunday"}

EN_MONTHS: dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

SHORT_DAY_LABELS: dict[int, str] = {5: "Sat", 6: "Sun"}

EN_SHORT_MONTHS: dict[int, str] = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


@dataclass(frozen=True)
class PollRenderInput:
    service_date: date
    cancelled_lift_times: tuple[str, ...] = ()


@dataclass(frozen=True)
class PollDraft:
    question: str
    options: tuple[str, ...]
    is_anonymous: bool = False
    allows_multiple_answers: bool = True


@dataclass(frozen=True)
class LiftAvailability:
    time: str
    voter_count: int
    capacity: int = 10
    manual_count: int = 0


def render_poll(render_input: PollRenderInput) -> PollDraft:
    lifts = _active_lifts(render_input.cancelled_lift_times)
    if not lifts:
        msg = "At least one lift must remain in the poll."
        raise ValueError(msg)

    day = DAY_LABELS.get(render_input.service_date.weekday(), "Lift day")
    question = f"🚐 {day} · {_en_date(render_input.service_date)}"

    options = (*(_format_lift_option(lift) for lift in lifts), CHECK_ANSWERS_OPTION)
    return PollDraft(question=question, options=options)


def render_poll_notice(first_lift_location: StartLocation) -> str:
    if first_lift_location == StartLocation.VAKE:
        route_notice = "📍 All lifts: Vake Park."
    else:
        route_notice = (
            "📍 The day's first running lift departs from Justice Hall. "
            "All later lifts depart from Vake Park."
        )
    return f"{route_notice}\n\n{PAYMENT_REMINDER}"


def render_availability_status(
    service_date: date,
    lifts: tuple[LiftAvailability, ...],
) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    lines = [
        f"🚐 Availability · {day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}",
        "",
    ]
    lines.extend(_availability_line(lift) for lift in lifts)
    return "\n".join(lines)


def _active_lifts(cancelled_lift_times: tuple[str, ...]) -> list[LiftTemplate]:
    cancelled = set(cancelled_lift_times)
    return [lift for lift in DEFAULT_LIFTS if lift.time not in cancelled]


def _format_lift_option(lift: LiftTemplate) -> str:
    return f"🚲 {lift.time}"


def _availability_line(lift: LiftAvailability) -> str:
    remaining = lift.capacity - lift.voter_count
    manual_suffix = f" · {lift.manual_count} manual" if lift.manual_count else ""
    if remaining < 0:
        waiting = abs(remaining)
        return (
            f"🔴 {lift.time} — {lift.voter_count}/{lift.capacity}"
            f"{manual_suffix} · waitlist +{waiting}"
        )
    if remaining == 0:
        return f"🔴 {lift.time} — {lift.voter_count}/{lift.capacity}{manual_suffix}"

    marker = "🟡" if remaining <= 2 else "🟢"
    return f"{marker} {lift.time} — {lift.voter_count}/{lift.capacity}{manual_suffix}"


def _en_date(service_date: date) -> str:
    return f"{EN_MONTHS[service_date.month]} {service_date.day}"
