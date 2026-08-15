"""One rider's weekend, shown in the bot's private chat.

Anything that depends on who is asking lives here rather than on the public
payments board: a card can say where you stand in a waitlist queue and what you
personally owe, while a shared group keyboard cannot hold a row per lift per
person. Every "pay" link is a deep link here, and the link doubles as
onboarding — tapping it is pressing Start, so it reaches riders who never opened
the bot.

The board keeps the one-tap common case, which has to work for the ~160 members
with no private chat. This is where the nuance goes.

Days are tabs rather than separate messages, the same shape as the admin
monitor: one card per rider that stays current, instead of a chat filling with
stale snapshots of single days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

MY_DAY_PARSE_MODE = "HTML"
# Wire format, not a description: this prefix and the `guest:` callbacks are
# baked into board buttons already posted in the group, so they outlive the
# rename from "guest form" to "rider card".
DEEP_LINK_PREFIX = "guests-"


@dataclass(frozen=True)
class RiderLiftRow:
    lift_time: str
    guests: int = 0
    seats_left: int = 0
    # 0 when the rider holds a real seat; otherwise their place in the queue.
    waitlist_position: int = 0
    # False while the lift is still short of the minimum, when nothing is due.
    running: bool = True

    @property
    def waitlisted(self) -> bool:
        return self.waitlist_position > 0


@dataclass(frozen=True)
class RiderDayView:
    service_date: date
    rows: tuple[RiderLiftRow, ...] = ()
    # Lifts the rider booked that have not reached the minimum yet. Paying for these
    # is optional, which is why the whole-day button only appears when some exist.
    pending_lift_times: tuple[str, ...] = ()
    due_now_gel: int = 0
    due_all_gel: int = 0
    paid_gel: int = 0

    @property
    def total_guests(self) -> int:
        return sum(row.guests for row in self.rows)

    @property
    def waitlisted(self) -> bool:
        return bool(self.rows) and all(row.waitlisted for row in self.rows)


@dataclass(frozen=True)
class RiderCardView:
    days: tuple[RiderDayView, ...]
    price_gel: int
    selected_service_date: date | None = None

    @property
    def selected(self) -> RiderDayView | None:
        if not self.days:
            return None
        for day in self.days:
            if day.service_date == self.selected_service_date:
                return day
        return self.days[0]


@dataclass(frozen=True)
class MyDayDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_rider_card(view: RiderCardView) -> MyDayDraft:
    day = view.selected
    if day is None or not day.rows:
        return MyDayDraft(
            text=(
                "🚲 My rides\n\nYou are not booked on any lift yet. "
                "Vote in the lift poll and come back here."
            ),
            reply_markup=_day_tabs_only(view),
        )

    lines = [
        f"🚲 My rides · {_long_day_label(day.service_date)}",
        "",
        f"{view.price_gel} GEL per seat. Someone riding with you? Add a seat.",
        "",
    ]
    lines.extend(_row_line(row) for row in day.rows)
    lines.extend(_money_lines(day))
    return MyDayDraft(text="\n".join(lines), reply_markup=_keyboard(view, day))


def _money_lines(day: RiderDayView) -> list[str]:
    lines: list[str] = []
    if day.waitlisted:
        # Said plainly, because the board only shows it as a name in a list and a
        # rider who cannot see a seat should not be guessing whether they have one.
        lines.extend(("", "⏳ You hold no seat yet — nothing to pay until one frees up."))
        return lines
    if day.pending_lift_times:
        # Naming them is the point: "15 GEL" alone looks wrong to somebody who booked
        # three lifts, and cash cannot easily be topped up later.
        pending = ", ".join(day.pending_lift_times)
        lines.extend(
            (
                "",
                f"Not filled yet: {pending}.",
                f"Due now {day.due_now_gel} GEL · whole day {day.due_all_gel} GEL.",
            )
        )
    elif day.due_all_gel:
        lines.extend(("", f"Your total: {day.due_all_gel} GEL."))
    if day.paid_gel:
        gap = day.due_now_gel - day.paid_gel
        if gap > 0:
            lines.append(f"Paid {day.paid_gel} GEL · {gap} GEL still due.")
        elif gap < 0:
            lines.append(f"Paid {day.paid_gel} GEL · {-gap} GEL to come back.")
        else:
            lines.append(f"Paid {day.paid_gel} GEL ✅")
    return lines


def _row_line(row: RiderLiftRow) -> str:
    if row.waitlisted:
        return f"{row.lift_time} — ⏳ waitlist, {_ordinal(row.waitlist_position)} in line"
    parts = ["riding" if row.running else "not filled yet"]
    if row.guests:
        parts.append(f"{row.guests} guest" if row.guests == 1 else f"{row.guests} guests")
    parts.append("full" if row.seats_left <= 0 else f"{row.seats_left} seats left")
    return f"{row.lift_time} — {' · '.join(parts)}"


def _ordinal(position: int) -> str:
    if 10 <= position % 100 <= 20:
        return f"{position}th"
    return f"{position}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(position % 10, 'th') }"


def _keyboard(view: RiderCardView, day: RiderDayView) -> InlineKeyboardMarkup:
    encoded_date = encode_guest_date(day.service_date)
    rows: list[list[InlineKeyboardButton]] = []

    if not day.waitlisted:
        rows.extend(_money_rows(day, encoded_date=encoded_date))
        # The common case is one guest riding the whole day with their host, so that
        # is one tap. Per-lift rows exist for the rarer guest who only does some
        # laps, and are pointless when the host holds a single lift.
        seated = tuple(row for row in day.rows if not row.waitlisted)
        if len(seated) > 1:
            rows.append(_all_lifts_row(day, encoded_date=encoded_date))
        rows.extend(_lift_row(row, encoded_date=encoded_date) for row in seated)
    rows.extend(_day_tabs(view))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _money_rows(day: RiderDayView, *, encoded_date: str) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    if day.due_now_gel and day.due_now_gel != day.paid_gel:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💸 Pay {day.due_now_gel}",
                    callback_data=f"guest:pay:{encoded_date}",
                ),
                InlineKeyboardButton(
                    text=f"💵 Cash {day.due_now_gel}",
                    callback_data=f"guest:cash:{encoded_date}",
                ),
            ]
        )
    # Settling the whole day up front only makes sense while something is unfilled.
    # It matters most for cash: handing money over twice means finding Misho twice.
    if day.pending_lift_times:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💸 Pay all · {day.due_all_gel}",
                    callback_data=f"guest:payall:{encoded_date}",
                ),
                InlineKeyboardButton(
                    text=f"💵 Cash all · {day.due_all_gel}",
                    callback_data=f"guest:cashall:{encoded_date}",
                ),
            ]
        )
    if day.paid_gel:
        rows.append(
            [InlineKeyboardButton(text="↩️ Undo", callback_data=f"guest:undo:{encoded_date}")]
        )
    return rows


def _day_tabs_only(view: RiderCardView) -> InlineKeyboardMarkup | None:
    tabs = _day_tabs(view)
    return InlineKeyboardMarkup(inline_keyboard=tabs) if tabs else None


def _day_tabs(view: RiderCardView) -> list[list[InlineKeyboardButton]]:
    """One card with tabs, not one message per day, so nothing goes stale in the chat."""
    if len(view.days) < 2:
        return []
    selected = view.selected
    return [
        [
            InlineKeyboardButton(
                text=(
                    "✅ "
                    if selected is not None and day.service_date == selected.service_date
                    else ""
                )
                + _short_day_label(day.service_date),
                callback_data=f"guest:day:{encode_guest_date(day.service_date)}",
            )
            for day in view.days
        ]
    ]


def _all_lifts_row(day: RiderDayView, *, encoded_date: str) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if day.total_guests:
        buttons.append(
            InlineKeyboardButton(
                text="➖ Guest leaves",
                callback_data=f"guest:allsub:{encoded_date}",
            )
        )
    if any(row.seats_left > 0 for row in day.rows if not row.waitlisted):
        buttons.append(
            InlineKeyboardButton(
                text="➕ Guest rides with me",
                callback_data=f"guest:all:{encoded_date}",
            )
        )
    return buttons or [InlineKeyboardButton(text="All lifts full", callback_data="guest:noop")]


def _lift_row(row: RiderLiftRow, *, encoded_date: str) -> list[InlineKeyboardButton]:
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


def _short_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Day")
    return f"{day} {service_date.day}"
