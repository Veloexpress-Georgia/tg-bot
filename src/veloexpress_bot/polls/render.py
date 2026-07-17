from dataclasses import dataclass
from datetime import date

from veloexpress_bot.polls.defaults import (
    CHECK_ANSWERS_OPTION,
    DEFAULT_LIFTS,
    PAYMENT_REMINDER,
    LiftTemplate,
    StartLocation,
)

DAY_LABELS: dict[int, tuple[str, str]] = {
    5: ("Суббота", "Saturday"),
    6: ("Воскресенье", "Sunday"),
}

RU_MONTHS: dict[int, str] = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
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


def render_poll(render_input: PollRenderInput) -> PollDraft:
    lifts = _active_lifts(render_input.cancelled_lift_times)
    if not lifts:
        msg = "At least one lift must remain in the poll."
        raise ValueError(msg)

    ru_day, en_day = DAY_LABELS.get(
        render_input.service_date.weekday(), ("День забросок", "Lift day")
    )
    question = (
        f"🚐 {ru_day} · {_ru_date(render_input.service_date)}\n"
        f"{en_day} · {_en_date(render_input.service_date)}"
    )

    options = (*(_format_lift_option(lift) for lift in lifts), CHECK_ANSWERS_OPTION)
    return PollDraft(question=question, options=options)


def render_poll_notice(first_lift_location: StartLocation) -> str:
    if first_lift_location == StartLocation.VAKE:
        route_notice = "📍 Все заброски — от Ваке-парка.\nAll lifts depart from Vake Park."
    else:
        route_notice = (
            "📍 Первая состоявшаяся заброска дня — от Дома Юстиции. Если заброска "
            "на 8:30 не набралась, первой считается следующее набравшееся время. "
            "Остальные — от Ваке-парка.\n"
            "The first running lift of the day departs from Justice Hall. If the 8:30 "
            "lift does not run, the next running time becomes the first lift. All later "
            "lifts depart from Vake Park."
        )
    return f"{route_notice}\n\n{PAYMENT_REMINDER}"


def _active_lifts(cancelled_lift_times: tuple[str, ...]) -> list[LiftTemplate]:
    cancelled = set(cancelled_lift_times)
    return [lift for lift in DEFAULT_LIFTS if lift.time not in cancelled]


def _format_lift_option(lift: LiftTemplate) -> str:
    return f"🚲 {lift.time}"


def _ru_date(service_date: date) -> str:
    return f"{service_date.day} {RU_MONTHS[service_date.month]}"


def _en_date(service_date: date) -> str:
    return f"{EN_MONTHS[service_date.month]} {service_date.day}"
