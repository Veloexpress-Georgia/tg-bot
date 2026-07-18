from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
    marker = _availability_marker(lift.total_count, lift.capacity)
    suffix = f" · {lift.manual_count} manual" if lift.manual_count else ""
    if lift.total_count > lift.capacity:
        suffix += f" · waitlist +{lift.total_count - lift.capacity}"
    return f"{marker} {lift.time} — {lift.total_count}/{lift.capacity}{suffix}"


def _availability_marker(total_count: int, capacity: int) -> str:
    remaining = capacity - total_count
    if remaining <= 0:
        return "🔴"
    if remaining <= 2:
        return "🟡"
    return "🟢"


def _long_day_label(service_date: date) -> str:
    day = "Sun" if service_date.weekday() == 6 else "Sat"
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"


def _short_day_label(service_date: date) -> str:
    day = "Sun" if service_date.weekday() == 6 else "Sat"
    return f"{day} {service_date.day}"


def _compact_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def _compact_time(lift_time: str) -> str:
    hour, minute = lift_time.split(":", maxsplit=1)
    return f"{int(hour):02d}{minute}"
