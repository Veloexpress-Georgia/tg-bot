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

from veloexpress_bot.payments.details import bank_details_text
from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

MY_DAY_PARSE_MODE = "HTML"
# Wire format, not a description: this prefix and the `guest:` callbacks are
# baked into board buttons already posted in the group, so they outlive the
# rename from "guest form" to "rider card".
DEEP_LINK_PREFIX = "guests-"
# "I paid" and "Cash" carry their meaning through the link. Unlike "Pay", which
# only invites somebody to go and pay, these are the rider asserting that money
# has moved — so acting on arrival is the same promise the button already made.
PAID_LINK_PREFIX = "paid-"
CASH_LINK_PREFIX = "cash-"


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
    price_gel: int = 15
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
    # Whether this rider has ridden before. The card is otherwise a dead end
    # midweek, which is exactly when somebody idly opens it.
    has_history: bool = False

    @property
    def selected(self) -> RiderDayView | None:
        if not self.days:
            return None
        for day in self.days:
            if day.service_date == self.selected_service_date:
                return day
        return self.days[0]


@dataclass(frozen=True)
class RiderSeasonDay:
    service_date: date
    lift_times: tuple[str, ...]
    seats: int
    paid_gel: int


@dataclass(frozen=True)
class RiderSeason:
    days: tuple[RiderSeasonDay, ...]
    total_days: int
    total_rides: int
    total_seats: int
    total_gel: int


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
            reply_markup=_empty_card_keyboard(view),
        )

    lines = [
        f"🚲 My rides · {_long_day_label(day.service_date)}",
        "",
        f"{day.price_gel} GEL per seat",
        "",
    ]
    lines.extend(_row_line(row) for row in day.rows)
    lines.extend(_money_lines(day))
    if not day.waitlisted:
        lines.extend(("", bank_details_text()))
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
            prepaid = (
                min(-gap, max(day.due_all_gel - day.due_now_gel, 0))
                if day.pending_lift_times
                else 0
            )
            surplus = -gap - prepaid
            parts = [f"Paid {day.paid_gel} GEL"]
            if prepaid:
                parts.append(f"{prepaid} GEL prepaid")
            if surplus:
                parts.append(f"{surplus} GEL to come back")
            lines.append(" · ".join(parts) + ".")
        else:
            lines.append(f"Paid {day.paid_gel} GEL ✅")
    return lines


def _row_line(row: RiderLiftRow) -> str:
    if row.waitlisted:
        return f"{row.lift_time} — ⏳ waitlist, {_ordinal(row.waitlist_position)} in line"
    parts = ["riding" if row.running else "not filled yet"]
    if row.guests:
        parts.append(f"{row.guests} guest" if row.guests == 1 else f"{row.guests} guests")
    parts.append(
        "full"
        if row.seats_left <= 0
        else "1 seat left"
        if row.seats_left == 1
        else f"{row.seats_left} seats left"
    )
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
            bulk = _all_lifts_row(day, encoded_date=encoded_date)
            if bulk:
                rows.append(bulk)
        rows.extend(
            buttons for row in seated if (buttons := _lift_row(row, encoded_date=encoded_date))
        )
    rows.extend(_day_tabs(view))
    rows.extend(_season_row(view))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _season_row(view: RiderCardView) -> list[list[InlineKeyboardButton]]:
    if not view.has_history:
        return []
    return [[InlineKeyboardButton(text="📜 My past rides", callback_data="guest:season")]]


def _empty_card_keyboard(view: RiderCardView) -> InlineKeyboardMarkup | None:
    rows = [*_day_tabs(view), *_season_row(view)]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def render_rider_season(season: RiderSeason) -> MyDayDraft:
    """Every lift this rider actually rode, and what they paid for it.

    Built from the days after they closed, so it counts the van they joined on
    the morning and leaves out the booking they dropped before it went.
    """
    lines = ["📜 My past rides", ""]
    if not season.days:
        lines.append("No finished lift yet. Once you ride one, it shows up here.")
    else:
        lines.extend(_season_day_line(day) for day in season.days)
        lines.extend(
            (
                "",
                f"{season.total_days} days · {season.total_rides} lifts · "
                f"{season.total_seats} seats · {season.total_gel} GEL",
            )
        )
    return MyDayDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back", callback_data="guest:card")],
            ]
        ),
    )


def _season_day_line(day: RiderSeasonDay) -> str:
    seats = "1 seat" if day.seats == 1 else f"{day.seats} seats"
    return (
        f"{_long_day_label(day.service_date)} · {', '.join(day.lift_times)} · "
        f"{seats} · {day.paid_gel} GEL"
    )


def _money_rows(day: RiderDayView, *, encoded_date: str) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    due_now = max(day.due_now_gel - day.paid_gel, 0)
    due_all = max(day.due_all_gel - day.paid_gel, 0)
    if due_now:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💸 I paid · {due_now}",
                    callback_data=f"guest:pay:{encoded_date}",
                ),
                InlineKeyboardButton(
                    text=f"💵 Cash {due_now}",
                    callback_data=f"guest:cash:{encoded_date}",
                ),
            ]
        )
    # Settling the whole day up front only makes sense while something is unfilled.
    # It matters most for cash: handing money over twice means finding Misho twice.
    if day.pending_lift_times and due_all:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💸 I paid all · {due_all}",
                    callback_data=f"guest:payall:{encoded_date}",
                ),
                InlineKeyboardButton(
                    text=f"💵 Cash all · {due_all}",
                    callback_data=f"guest:cashall:{encoded_date}",
                ),
            ]
        )
    if day.paid_gel:
        rows.append(
            [InlineKeyboardButton(text="↩️ Undo", callback_data=f"guest:undo:{encoded_date}")]
        )
    return rows


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
                text="− Guest from all lifts",
                callback_data=f"guest:allsub:{encoded_date}",
            )
        )
    if all(row.seats_left > 0 for row in day.rows if not row.waitlisted):
        buttons.append(
            InlineKeyboardButton(
                text="＋ Guest on all lifts",
                callback_data=f"guest:all:{encoded_date}",
            )
        )
    return buttons


def _lift_row(row: RiderLiftRow, *, encoded_date: str) -> list[InlineKeyboardButton]:
    encoded_time = encode_guest_time(row.lift_time)
    buttons: list[InlineKeyboardButton] = []
    if row.guests:
        buttons.append(
            InlineKeyboardButton(
                text=f"− Guest · {row.lift_time}",
                callback_data=f"guest:sub:{encoded_date}:{encoded_time}",
            )
        )
    if row.seats_left > 0:
        buttons.append(
            InlineKeyboardButton(
                text=f"＋ Guest · {row.lift_time}",
                callback_data=f"guest:add:{encoded_date}:{encoded_time}",
            )
        )
    return buttons


@dataclass(frozen=True)
class DeepLinkIntent:
    """What a rider asked for by tapping a link into the bot's private chat."""

    service_date: date
    # "" to just look; otherwise the payment method they are asserting.
    method: str = ""


def deep_link(*, bot_username: str, service_date: date, prefix: str = DEEP_LINK_PREFIX) -> str:
    """Tapping this is pressing Start, so it also reaches riders with no bot chat."""
    return f"https://t.me/{bot_username}?start={prefix}{encode_guest_date(service_date)}"


def parse_deep_link(payload: str) -> DeepLinkIntent | None:
    for prefix, method in (
        (DEEP_LINK_PREFIX, ""),
        (PAID_LINK_PREFIX, "transfer"),
        (CASH_LINK_PREFIX, "cash"),
    ):
        if not payload.startswith(prefix):
            continue
        try:
            return DeepLinkIntent(
                service_date=decode_guest_date(payload.removeprefix(prefix)),
                method=method,
            )
        except ValueError:
            return None
    return None


def encode_guest_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def decode_guest_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def encode_guest_time(lift_time: str) -> str:
    hour, minute = lift_time.split(":", maxsplit=1)
    return f"{int(hour):02d}{minute}"


def decode_guest_time(value: str) -> str:
    if len(value) not in {3, 4} or not value.isascii() or not value.isdigit():
        raise ValueError("Invalid lift time")
    normalized = value.zfill(4)
    if int(normalized[:2]) > 23 or int(normalized[2:]) > 59:
        raise ValueError("Invalid lift time")
    return f"{int(normalized[:2])}:{normalized[2:]}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"


def _short_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Day")
    return f"{day} {service_date.day}"
