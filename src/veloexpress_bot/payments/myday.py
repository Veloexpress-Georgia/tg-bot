"""One rider's day, shown in the bot's private chat.

Anything that depends on who is asking lives here rather than on the public
payments board: a form can show how many seats a lift has left and what this
rider owes, while a shared group keyboard cannot hold a row per lift per person.
The board links here, and the link doubles as onboarding — tapping a deep link is
pressing Start, so it reaches riders who never opened the bot.

The board keeps the one-tap common case. This is where the nuance goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

MY_DAY_PARSE_MODE = "HTML"
DEEP_LINK_PREFIX = "guests-"


@dataclass(frozen=True)
class GuestLiftRow:
    lift_time: str
    guests: int
    seats_left: int


@dataclass(frozen=True)
class MyDayView:
    service_date: date
    price_gel: int
    rows: tuple[GuestLiftRow, ...] = ()
    # Lifts the rider booked that have not reached the minimum yet. Paying for these
    # is optional, which is why the whole-day button only appears when some exist.
    pending_lift_times: tuple[str, ...] = ()
    due_now_gel: int = 0
    due_all_gel: int = 0

    @property
    def total_guests(self) -> int:
        return sum(row.guests for row in self.rows)


@dataclass(frozen=True)
class MyDayDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_my_day_card(view: MyDayView) -> MyDayDraft:
    header = f"🚲 My day · {_long_day_label(view.service_date)}"
    if not view.rows:
        return MyDayDraft(
            text=(
                f"{header}\n\nYou are not booked on any running lift that day, "
                "so there is nothing to settle or bring a guest to yet."
            ),
            reply_markup=None,
        )

    lines = [
        header,
        "",
        f"{view.price_gel} GEL per seat. Someone riding with you? Add a seat.",
        "",
    ]
    lines.extend(_row_line(row) for row in view.rows)
    if view.pending_lift_times:
        # Naming them is the point: "15 GEL" alone looks wrong to somebody who booked
        # three lifts, and cash cannot easily be topped up later.
        pending = ", ".join(view.pending_lift_times)
        lines.extend(
            (
                "",
                f"Not filled yet: {pending}.",
                f"Due now {view.due_now_gel} GEL · whole day {view.due_all_gel} GEL.",
            )
        )
    elif view.due_all_gel:
        lines.extend(("", f"Your total: {view.due_all_gel} GEL."))

    return MyDayDraft(text="\n".join(lines), reply_markup=_keyboard(view))


def _row_line(row: GuestLiftRow) -> str:
    guests = f"{row.guests} guest" if row.guests == 1 else f"{row.guests} guests"
    if row.seats_left <= 0:
        return f"{row.lift_time} — {guests} · full"
    return f"{row.lift_time} — {guests} · {row.seats_left} seats left"


def _keyboard(view: MyDayView) -> InlineKeyboardMarkup:
    encoded_date = encode_guest_date(view.service_date)
    rows: list[list[InlineKeyboardButton]] = []

    # Settling the whole day up front only makes sense while something is unfilled.
    # It matters most for cash: handing money over twice means finding Misho twice.
    if view.pending_lift_times:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💸 Pay all · {view.due_all_gel}",
                    callback_data=f"guest:payall:{encoded_date}",
                ),
                InlineKeyboardButton(
                    text=f"💵 Cash all · {view.due_all_gel}",
                    callback_data=f"guest:cashall:{encoded_date}",
                ),
            ]
        )

    # The common case is one guest riding the whole day with their host, so that is
    # one tap. Per-lift rows exist for the rarer guest who only does some laps, and
    # are pointless when the host holds a single lift.
    if len(view.rows) > 1:
        rows.append(_all_lifts_row(view, encoded_date=encoded_date))
        rows.extend(_lift_row(row, encoded_date=encoded_date) for row in view.rows)
    else:
        rows.extend(_lift_row(row, encoded_date=encoded_date) for row in view.rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _all_lifts_row(
    view: MyDayView,
    *,
    encoded_date: str,
) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if view.total_guests:
        buttons.append(
            InlineKeyboardButton(
                text="➖ Guest leaves",
                callback_data=f"guest:allsub:{encoded_date}",
            )
        )
    if any(row.seats_left > 0 for row in view.rows):
        buttons.append(
            InlineKeyboardButton(
                text="➕ Guest rides with me",
                callback_data=f"guest:all:{encoded_date}",
            )
        )
    return buttons or [InlineKeyboardButton(text="All lifts full", callback_data="guest:noop")]


def _lift_row(row: GuestLiftRow, *, encoded_date: str) -> list[InlineKeyboardButton]:
    encoded_time = encode_guest_time(row.lift_time)
    buttons = [
        InlineKeyboardButton(
            text="➖" if row.guests else " ",
            callback_data=(
                f"guest:sub:{encoded_date}:{encoded_time}" if row.guests else "guest:noop"
            ),
        ),
        InlineKeyboardButton(
            text=f"{row.lift_time} · {row.guests}",
            callback_data="guest:noop",
        ),
    ]
    # No plus button once the lift is full: never offer a seat that is gone.
    if row.seats_left > 0:
        buttons.append(
            InlineKeyboardButton(
                text="➕",
                callback_data=f"guest:add:{encoded_date}:{encoded_time}",
            )
        )
    return buttons


def deep_link(*, bot_username: str, service_date: date) -> str:
    """Tapping this is pressing Start, so it also reaches riders with no bot chat."""
    return f"https://t.me/{bot_username}?start={DEEP_LINK_PREFIX}{encode_guest_date(service_date)}"


def parse_deep_link(payload: str) -> date | None:
    if not payload.startswith(DEEP_LINK_PREFIX):
        return None
    try:
        return decode_guest_date(payload.removeprefix(DEEP_LINK_PREFIX))
    except ValueError:
        return None


def encode_guest_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def decode_guest_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def encode_guest_time(lift_time: str) -> str:
    hour, minute = lift_time.split(":", maxsplit=1)
    return f"{int(hour):02d}{minute}"


def decode_guest_time(value: str) -> str:
    normalized = value.zfill(4)
    return f"{int(normalized[:2])}:{normalized[2:]}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
