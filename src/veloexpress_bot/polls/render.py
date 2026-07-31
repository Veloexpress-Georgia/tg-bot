import html
from dataclasses import dataclass
from datetime import date

from veloexpress_bot.polls.defaults import (
    CHECK_ANSWERS_OPTION,
    DEFAULT_LIFTS,
    MINIMUM_RIDERS,
    LiftTemplate,
    PaymentTerms,
    StartLocation,
)

# Availability uses plain proportional lines (no code block): monospace bars
# render as an ugly "copy code" box on mobile and column alignment is unreliable.
AVAILABILITY_PARSE_MODE = "HTML"

DAY_LABELS: dict[int, str] = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}

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

SHORT_DAY_LABELS: dict[int, str] = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}

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
class WaitlistRider:
    telegram_user_id: int
    label: str


@dataclass(frozen=True)
class LiftAvailability:
    time: str
    voter_count: int
    capacity: int = 10
    manual_count: int = 0
    cancelled: bool = False
    waitlist: tuple[WaitlistRider, ...] = ()


def render_poll(render_input: PollRenderInput) -> PollDraft:
    lifts = _active_lifts(render_input.cancelled_lift_times)
    if not lifts:
        msg = "At least one lift must remain in the poll."
        raise ValueError(msg)

    day = DAY_LABELS.get(render_input.service_date.weekday(), "Lift day")
    question = f"🚐 {day} · {_en_date(render_input.service_date)}"

    options = (*(_format_lift_option(lift) for lift in lifts), CHECK_ANSWERS_OPTION)
    return PollDraft(question=question, options=options)


def render_poll_notice(first_lift_location: StartLocation, *, terms: PaymentTerms) -> str:
    if first_lift_location == StartLocation.VAKE:
        route_notice = "📍 All lifts: Vake Park."
    else:
        route_notice = (
            "📍 The day's first running lift departs from Justice Hall. "
            "All later lifts depart from Vake Park."
        )
    return "\n".join((route_notice, "", *terms.rules()))


def render_availability_status(
    service_date: date,
    lifts: tuple[LiftAvailability, ...],
) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    header = f"🚐 Availability · {day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
    if not lifts:
        return header

    lines = [header, ""]
    for lift in lifts:
        lines.append(_availability_line(lift))
        if lift.waitlist:
            # Named right where the seats are counted, so "waitlist +2" stops being a
            # riddle. The board is edited in place and a Telegram edit sends no
            # notification, so this informs without pinging anyone.
            lines.append(f"    ⏳ {' '.join(_waitlist_mention(r) for r in lift.waitlist)}")
    return "\n".join(lines)


def _waitlist_mention(rider: WaitlistRider) -> str:
    return f'<a href="tg://user?id={rider.telegram_user_id}">{html.escape(rider.label)}</a>'


def _availability_line(lift: LiftAvailability) -> str:
    if lift.cancelled:
        return f"{lift.time} — ❌ cancelled"
    return f"{lift.time} — <b>{lift.voter_count}/{lift.capacity}</b> · {_availability_tag(lift)}"


def _availability_tag(lift: LiftAvailability) -> str:
    over = lift.voter_count - lift.capacity
    if over > 0:
        return f"waitlist +{over}"
    if lift.voter_count >= lift.capacity:
        return "full"
    if lift.voter_count < MINIMUM_RIDERS:
        return f"needs {MINIMUM_RIDERS - lift.voter_count} more"
    return f"{lift.capacity - lift.voter_count} left"


def _active_lifts(cancelled_lift_times: tuple[str, ...]) -> list[LiftTemplate]:
    cancelled = set(cancelled_lift_times)
    return [lift for lift in DEFAULT_LIFTS if lift.time not in cancelled]


def _format_lift_option(lift: LiftTemplate) -> str:
    return f"🚲 {lift.time}"


def _en_date(service_date: date) -> str:
    return f"{EN_MONTHS[service_date.month]} {service_date.day}"
