from dataclasses import dataclass
from datetime import date

from veloexpress_bot.polls.defaults import (
    CHECK_ANSWERS_OPTION,
    DEFAULT_LIFTS,
    LOCATION_LABELS,
    PAYMENT_REMINDER,
    LiftTemplate,
    StartLocation,
)

DAY_LABELS: dict[int, tuple[str, str]] = {
    5: ("Суббота", "Saturday"),
    6: ("Воскресенье", "Sunday"),
}


@dataclass(frozen=True)
class PollRenderInput:
    service_date: date
    first_lift_location: StartLocation = StartLocation.JUSTICE_HALL
    cancelled_lift_times: tuple[str, ...] = ()


@dataclass(frozen=True)
class PollDraft:
    question: str
    options: tuple[str, ...]
    is_anonymous: bool = False
    allows_multiple_answers: bool = True


def render_poll(render_input: PollRenderInput) -> PollDraft:
    lifts = _active_lifts(render_input.cancelled_lift_times, render_input.first_lift_location)
    if not lifts:
        msg = "At least one lift must remain in the poll."
        raise ValueError(msg)

    ru_day, en_day = DAY_LABELS.get(
        render_input.service_date.weekday(), ("День забросок", "Lift day")
    )
    question = (
        f"{PAYMENT_REMINDER}\n\n"
        f"🚐 {ru_day} {render_input.service_date:%d.%m} / {en_day}\n"
        "Выберите время заброски / Choose departure time"
    )

    options = (*(_format_lift_option(lift) for lift in lifts), CHECK_ANSWERS_OPTION)
    return PollDraft(question=question, options=options)


def _active_lifts(
    cancelled_lift_times: tuple[str, ...], first_location: StartLocation
) -> list[LiftTemplate]:
    cancelled = set(cancelled_lift_times)
    active: list[LiftTemplate] = []
    for index, lift in enumerate(DEFAULT_LIFTS):
        if lift.time in cancelled:
            continue
        location = first_location if index == 0 else lift.default_location
        active.append(
            LiftTemplate(time=lift.time, default_location=location, capacity=lift.capacity)
        )
    return active


def _format_lift_option(lift: LiftTemplate) -> str:
    location = LOCATION_LABELS[lift.default_location].format()
    return f"🚲 {lift.time} · {location}"
