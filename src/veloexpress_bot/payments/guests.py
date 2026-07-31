"""The guest form, shown in the bot's private chat.

Guests live here rather than on the public payments board for two reasons: a form
can show how many seats are actually left on each lift, and a shared group
keyboard cannot hold one row per lift per rider. The board links here instead,
and the link doubles as onboarding — tapping a deep link is pressing Start, so it
reaches riders who never opened the bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

GUEST_CARD_PARSE_MODE = "HTML"
DEEP_LINK_PREFIX = "guests-"


@dataclass(frozen=True)
class GuestLiftRow:
    lift_time: str
    guests: int
    seats_left: int


@dataclass(frozen=True)
class GuestCardView:
    service_date: date
    price_gel: int
    rows: tuple[GuestLiftRow, ...] = ()

    @property
    def total_guests(self) -> int:
        return sum(row.guests for row in self.rows)


@dataclass(frozen=True)
class GuestCardDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_guest_card(view: GuestCardView) -> GuestCardDraft:
    header = f"👤 Guests · {_long_day_label(view.service_date)}"
    if not view.rows:
        return GuestCardDraft(
            text=(
                f"{header}\n\nYou are not booked on any running lift that day, "
                "so there is nothing to bring a guest to yet."
            ),
            reply_markup=None,
        )

    lines = [
        header,
        "",
        f"Someone riding with you? Add a seat. {view.price_gel} GEL each.",
        "",
    ]
    lines.extend(_row_line(row) for row in view.rows)
    if view.total_guests:
        due = view.total_guests * view.price_gel
        lines.extend(("", f"Guests add {due} GEL to your total."))

    return GuestCardDraft(text="\n".join(lines), reply_markup=_keyboard(view))


def _row_line(row: GuestLiftRow) -> str:
    guests = f"{row.guests} guest" if row.guests == 1 else f"{row.guests} guests"
    if row.seats_left <= 0:
        return f"{row.lift_time} — {guests} · full"
    return f"{row.lift_time} — {guests} · {row.seats_left} seats left"


def _keyboard(view: GuestCardView) -> InlineKeyboardMarkup:
    encoded_date = encode_guest_date(view.service_date)
    rows: list[list[InlineKeyboardButton]] = []

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
    view: GuestCardView,
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
