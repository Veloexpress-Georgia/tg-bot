import html
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
    # Guests hold seats like anybody else. Left out of this count, the monitor
    # showed an admin free space the public board knew was gone.
    guest_count: int = 0
    capacity: int = 10
    cancelled: bool = False

    @property
    def total_count(self) -> int:
        return self.vote_count + self.manual_count + self.guest_count


@dataclass(frozen=True)
class LiftRider:
    telegram_user_id: int
    label: str
    paid: bool = False


@dataclass(frozen=True)
class BookingMonitorDay:
    service_date: date
    lifts: tuple[BookingLiftStatus, ...]
    paid_rider_count: int = 0
    booked_rider_count: int = 0
    # Money in, and money the running lifts should bring. Two figures rather than
    # one, because either alone invites the wrong conclusion.
    expected_gel: int = 0
    owed_gel: int = 0
    # A day that has already happened stays visible for late bookkeeping, but
    # cancelling it would refund a trip people took.
    past: bool = False
    has_refund_reports: bool = False

    @property
    def running_count(self) -> int:
        return sum(not lift.cancelled and lift.total_count >= MINIMUM_RIDERS for lift in self.lifts)

    @property
    def seat_count(self) -> int:
        return sum(lift.total_count for lift in self.lifts if not lift.cancelled)


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
        _summary_line(selected_day),
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

    if selected_day.has_refund_reports:
        # Cancelling clears the day's claims, so the report the admin was sent is
        # the only surviving list of who is owed. One deleted message should not
        # be the end of it.
        rows.append([InlineKeyboardButton(text="🧾 Past refunds", callback_data="mon:refunds")])

    # Never on a day that has already run: cancelling retires the day and reports
    # every payment as a refund, which for a trip people actually took is the bot
    # handing back money that was earned. The day stays readable, and payments can
    # still be marked on it — late bookkeeping is the reason it is still here.
    if not selected_day.past and any(not lift.cancelled for lift in selected_day.lifts):
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


# The monitor cards are plain text; this one carries mentions so the admin can
# reach the rider they owe.
BUMPED_REPORT_PARSE_MODE = "HTML"


def render_bumped_report(
    *,
    service_date: date,
    lift_time: str,
    riders: tuple[tuple[str, int, bool], ...],
    price_gel: int,
) -> str:
    """Name the riders a manual booking just pushed onto the waitlist.

    A manual seat outranks a poll vote, so adding one can evict a rider who has
    already paid. The admin took that seat deliberately; owing somebody a refund
    without being told is the part that would be an accident.
    """
    lines = [
        f"⚠️ {lift_time} · {_long_day_label(service_date)} — manual seat added.",
        "",
        "Off the lift now:",
        "",
    ]
    refund = 0
    for label, user_id, paid in riders:
        mention = f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'
        if paid:
            refund += price_gel
            lines.append(f"{mention} — paid, refund {price_gel} GEL")
        else:
            lines.append(f"{mention} — nothing paid")
    lines.append("")
    lines.append(f"Refund {refund} GEL." if refund else "Nobody paid for those seats.")
    return "\n".join(lines)


def render_start_status(
    days: tuple[BookingMonitorDay, ...],
    *,
    schedule_line: str,
) -> str:
    """The start card answers instead of greeting: state first, buttons after."""
    lines = ["🚐 Veloexpress", ""]
    if days:
        lines.extend(_start_day_line(day) for day in days)
    else:
        lines.append("No active lift polls.")
    lines.extend(("", schedule_line))
    return "\n".join(lines)


def _start_day_line(day: BookingMonitorDay) -> str:
    label = _long_day_label(day.service_date)
    active = sum(not lift.cancelled for lift in day.lifts)
    if not day.running_count:
        return f"{label} · {day.seat_count} booked, nothing running yet"
    paid = ""
    if day.booked_rider_count:
        paid = f" · paid {day.paid_rider_count}/{day.booked_rider_count}"
    return f"{label} · {day.running_count} of {active} lifts running{paid}"


def render_lift_detail(
    *,
    service_date: date,
    lift: BookingLiftStatus,
    riders: tuple[LiftRider, ...],
) -> BookingMonitorDraft:
    compact_date = _compact_date(service_date)
    compact_time = _compact_time(lift.time)
    lines = [f"🚲 {lift.time} · {_long_day_label(service_date)}", ""]
    if lift.cancelled:
        lines.append("❌ Cancelled.")
        lines.append("")
    lines.append(f"Telegram: {lift.vote_count}")
    lines.append(f"Manual: {lift.manual_count}")
    if lift.guest_count:
        # Only when there are any: an always-zero line is noise on most lifts.
        lines.append(f"Guests: {lift.guest_count}")
    lines.append(f"Total: {lift.total_count}/{lift.capacity}")
    if riders:
        paid = sum(rider.paid for rider in riders)
        lines.append(f"Paid: {paid}/{len(riders)}")
        lines.append("")
        lines.append(", ".join(rider.label for rider in riders))

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
    rows: list[list[InlineKeyboardButton]] = []
    # Tapping a rider records a payment made outside Telegram — cash, or a direct
    # message to Misho. Riders who can tap the payments board do not need this.
    for index in range(0, len(riders), 2):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if rider.paid else ''}{rider.label}",
                    callback_data=f"mon:paid:{compact_date}:{rider.telegram_user_id}",
                )
                for rider in riders[index : index + 2]
            ]
        )
    rows.append([action])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"mon:back:{compact_date}")])
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


def _summary_line(day: BookingMonitorDay) -> str:
    """The day at a glance: what runs, how many seats, and how the money stands.

    Money is stated as paid-of-owed rather than a bare total. On its own,
    `300 GEL in` sat next to a seat count computed from a different universe —
    claims cover guests and lifts paid ahead, seats did not — and the two read
    as a contradiction that needed the source to explain.
    """
    active = sum(not lift.cancelled for lift in day.lifts)
    parts = [
        f"Running {day.running_count} of {active}",
        f"{day.seat_count} seats",
    ]
    if day.booked_rider_count:
        parts.append(f"paid {day.paid_rider_count}/{day.booked_rider_count}")
    if day.expected_gel or day.owed_gel:
        parts.append(f"{day.expected_gel} of {day.owed_gel} GEL")
    return " · ".join(parts)


def _lift_line(lift: BookingLiftStatus) -> str:
    if lift.cancelled:
        return f"{lift.time} — ❌ cancelled"
    extra = []
    if lift.manual_count:
        extra.append(f"{lift.manual_count} manual")
    if lift.guest_count:
        extra.append(
            f"{lift.guest_count} guest" if lift.guest_count == 1 else f"{lift.guest_count} guests"
        )
    suffix = f" · {' · '.join(extra)}" if extra else ""
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
