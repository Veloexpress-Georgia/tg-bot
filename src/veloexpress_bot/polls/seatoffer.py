"""A freed seat offered to the waitlist: wording, buttons and pacing.

Pure, so the rules that decide who gets tagged when can be tested without a
database or a clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

CALLBACK_PREFIX = "seat"

# Silence widens the offer to the next rider; it never takes it away from the
# previous one. Half an hour is long enough to notice a tag during the day.
WIDEN_STEP = timedelta(minutes=30)
# Close to departure there is no half hour to spare.
URGENT_WINDOW = timedelta(hours=3)
URGENT_WIDEN_STEP = timedelta(minutes=10)
# Nobody answers at 3am. The first rider is still tagged at once — the tag will
# be there in the morning — but the queue is not walked while everyone sleeps.
QUIET_FROM = time(22, 0)
QUIET_UNTIL = time(8, 0)

ACCEPTED_TEXT = "✅ The seat is yours."
DECLINED_TEXT = "OK, passed on. Vote again in the poll if you change your mind."
NOT_FOR_YOU_TEXT = "This seat is offered to the riders tagged in the message."
GONE_TEXT = "This seat is already taken."


def is_quiet(now: datetime) -> bool:
    """`now` must already be in the service timezone."""
    moment = now.time()
    return moment >= QUIET_FROM or moment < QUIET_UNTIL


def widen_due(*, now: datetime, widened_at: datetime, departure_at: datetime) -> bool:
    """Whether the next waiting rider should be tagged as well."""
    if is_quiet(now):
        return False
    step = URGENT_WIDEN_STEP if departure_at - now <= URGENT_WINDOW else WIDEN_STEP
    return now - widened_at >= step


def offer_callback(action: str, offer_id: int) -> str:
    return f"{CALLBACK_PREFIX}:{action}:{offer_id}"


def parse_offer_callback(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[1] not in {"in", "skip"}:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


def offer_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ I'm in", callback_data=offer_callback("in", offer_id)
                ),
                InlineKeyboardButton(
                    text="🙅 Skip", callback_data=offer_callback("skip", offer_id)
                ),
            ]
        ]
    )


def lift_label(service_date: date, lift_time: str) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{lift_time} · {day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"


def render_seat_offer(
    service_date: date,
    lift_time: str,
    *,
    seats: int,
    riders: Sequence[tuple[int, str]],
) -> str:
    """An offer, not an order: the rider asked for this lift once, maybe days ago.

    Until somebody taps, the seat is nobody's and nobody is billed for it.
    """
    mentions = " ".join(
        f'<a href="tg://user?id={user_id}">{escape(label)}</a>' for user_id, label in riders
    )
    what = "A seat" if seats == 1 else f"{seats} seats"
    lines = [f"🎟 {what} freed up on {lift_label(service_date, lift_time)}: {mentions}"]
    if len(riders) > seats:
        lines.append("First to tap takes it.")
    return "\n".join(lines)


def render_seat_taken(service_date: date, lift_time: str, *, labels: Sequence[str]) -> str:
    names = ", ".join(escape(label) for label in labels)
    return f"🎟 {lift_label(service_date, lift_time)} — seat taken: {names}"
