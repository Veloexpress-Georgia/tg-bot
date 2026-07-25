from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.defaults import MINIMUM_RIDERS

EN_SHORT_MONTHS = {
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
class BookingLiftStatus:
    time: str
    vote_count: int
    manual_count: int
    capacity: int = 10
    cancelled: bool = False

    @property
    def total_count(self) -> int:
        return self.vote_count + self.manual_count


@dataclass(frozen=True)
class BookingMonitorDay:
    service_date: date
    lifts: tuple[BookingLiftStatus, ...]


@dataclass(frozen=True)
class BookingMonitorDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_booking_monitor(
    days: tuple[BookingMonitorDay, ...],
    *,
    selected_service_date: date | None,
) -> BookingMonitorDraft:
    if not days:
        return BookingMonitorDraft(
            text="📊 Booking monitor\n\nNo active lift polls.",
            reply_markup=None,
        )

    selected_day = next(
        (day for day in days if day.service_date == selected_service_date),
        days[0],
    )
    lines = [
        f"📊 Booking monitor · {_long_day_label(selected_day.service_date)}",
        "",
    ]
    lines.extend(_lift_line(lift) for lift in selected_day.lifts)

    rows: list[list[InlineKeyboardButton]] = []
    if len(days) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=("✅ " if day.service_date == selected_day.service_date else "")
                    + _short_day_label(day.service_date),
                    callback_data=f"mon:day:{_compact_date(day.service_date)}",
                )
                for day in days
            ]
        )
    compact_date = _compact_date(selected_day.service_date)
    for lift in selected_day.lifts:
        compact_time = _compact_time(lift.time)
        if lift.cancelled:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"❌ {lift.time} · cancelled",
                        callback_data=f"mon:info:{compact_date}:{compact_time}",
                    )
                ]
            )
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text="➖",
                    callback_data=f"mon:sub:{compact_date}:{compact_time}",
                ),
                InlineKeyboardButton(
                    text=f"{lift.time} · {lift.total_count}/{lift.capacity}",
                    callback_data=f"mon:info:{compact_date}:{compact_time}",
                ),
                InlineKeyboardButton(
                    text="➕",
                    callback_data=f"mon:add:{compact_date}:{compact_time}",
                ),
            ]
        )

    if any(not lift.cancelled for lift in selected_day.lifts):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚫 Cancel all · {_short_day_label(selected_day.service_date)}",
                    callback_data=f"mon:cancelday:{compact_date}",
                )
            ]
        )

    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_lift_detail(
    *,
    service_date: date,
    lift: BookingLiftStatus,
    riders: tuple[str, ...],
) -> BookingMonitorDraft:
    compact_date = _compact_date(service_date)
    compact_time = _compact_time(lift.time)
    lines = [f"🚲 {lift.time} · {_long_day_label(service_date)}", ""]
    if lift.cancelled:
        lines.append("❌ Cancelled.")
        lines.append("")
    lines.append(f"Telegram: {lift.vote_count}")
    lines.append(f"Manual: {lift.manual_count}")
    lines.append(f"Total: {lift.total_count}/{lift.capacity}")
    if riders:
        lines.append("")
        lines.append(", ".join(riders))

    if lift.cancelled:
        action = InlineKeyboardButton(
            text="♻️ Restore lift",
            callback_data=f"mon:restore:{compact_date}:{compact_time}",
        )
    else:
        action = InlineKeyboardButton(
            text="🚫 Cancel lift",
            callback_data=f"mon:cancel:{compact_date}:{compact_time}",
        )
    rows = [
        [action],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"mon:back:{compact_date}")],
    ]
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def decode_monitor_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def decode_monitor_time(value: str) -> str:
    normalized = value.zfill(4)
    hour = int(normalized[:2])
    minute = normalized[2:]
    return f"{hour}:{minute}"


def _lift_line(lift: BookingLiftStatus) -> str:
    if lift.cancelled:
        return f"{lift.time} — ❌ cancelled"
    suffix = f" · {lift.manual_count} manual" if lift.manual_count else ""
    return f"{lift.time} — {lift.total_count}/{lift.capacity} · {_lift_state(lift)}{suffix}"


def _lift_state(lift: BookingLiftStatus) -> str:
    over = lift.total_count - lift.capacity
    if over > 0:
        return f"waitlist +{over}"
    if lift.total_count >= lift.capacity:
        return "full"
    if lift.total_count < MINIMUM_RIDERS:
        return f"needs {MINIMUM_RIDERS - lift.total_count} more"
    return f"{lift.capacity - lift.total_count} left"


EN_SHORT_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _long_day_label(service_date: date) -> str:
    day = EN_SHORT_WEEKDAYS[service_date.weekday()]
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"


def _short_day_label(service_date: date) -> str:
    day = EN_SHORT_WEEKDAYS[service_date.weekday()]
    return f"{day} {service_date.day}"


def _compact_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def _compact_time(lift_time: str) -> str:
    hour, minute = lift_time.split(":", maxsplit=1)
    return f"{int(hour):02d}{minute}"
