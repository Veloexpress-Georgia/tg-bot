import html
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

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
    running_locked: bool = False

    @property
    def total_count(self) -> int:
        return self.vote_count + self.manual_count + self.guest_count

    @property
    def running(self) -> bool:
        return not self.cancelled and (self.total_count >= MINIMUM_RIDERS or self.running_locked)


@dataclass(frozen=True)
class LiftRider:
    telegram_user_id: int
    label: str
    paid: bool = False
    cash: bool = False
    guests: int = 0
    waitlisted: bool = False


@dataclass(frozen=True)
class MonitorRider:
    telegram_user_id: int
    label: str
    amount_gel: int = 0
    lift_times: tuple[str, ...] = ()


@dataclass(frozen=True)
class MonitorGuest:
    host_user_id: int
    host_label: str
    lift_time: str
    count: int


@dataclass(frozen=True)
class MonitorWaitlistRider:
    telegram_user_id: int
    label: str
    lift_time: str
    position: int


@dataclass(frozen=True)
class MonitorLateExit:
    telegram_user_id: int
    label: str
    lift_time: str
    changed_at: datetime | None = None


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
    unpaid_riders: tuple[MonitorRider, ...] = ()
    cash_pending: tuple[MonitorRider, ...] = ()
    guests: tuple[MonitorGuest, ...] = ()
    waitlist: tuple[MonitorWaitlistRider, ...] = ()
    late_exits: tuple[MonitorLateExit, ...] = ()

    @property
    def running_count(self) -> int:
        return sum(lift.running for lift in self.lifts)

    @property
    def seat_count(self) -> int:
        return sum(lift.total_count for lift in self.lifts if not lift.cancelled)

    @property
    def confirmed_seat_count(self) -> int:
        return sum(
            min(lift.total_count, lift.capacity) for lift in self.lifts if not lift.cancelled
        )

    @property
    def attention_count(self) -> int:
        return (
            len(self.unpaid_riders)
            + len(self.cash_pending)
            + len(self.waitlist)
            + len(self.late_exits)
        )


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
    lines.extend(_attention_lines(selected_day))
    lines.extend(("", *(_lift_line(lift) for lift in selected_day.lifts)))

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
    rows.append(
        [
            InlineKeyboardButton(
                text="👥 All riders",
                callback_data=f"mon:all:{compact_date}",
            )
        ]
    )
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
                    text=f"{lift.time} · {lift.total_count}/{lift.capacity}{_button_suffix(lift)}",
                    callback_data=f"mon:info:{compact_date}:{compact_time}",
                )
            ]
        )

    if not selected_day.past and selected_day.lifts:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Manage bookings",
                    callback_data=f"mon:manage:{compact_date}",
                )
            ]
        )

    if selected_day.has_refund_reports:
        # Cancelling clears the day's claims, so the report the admin was sent is
        # the only surviving list of who is owed. One deleted message should not
        # be the end of it.
        rows.append([InlineKeyboardButton(text="🧾 Past refunds", callback_data="mon:refunds")])

    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_booking_management(
    days: tuple[BookingMonitorDay, ...],
    *,
    selected_service_date: date | None,
) -> BookingMonitorDraft:
    if not days:
        return render_booking_monitor(days, selected_service_date=selected_service_date)
    selected_day = next(
        (day for day in days if day.service_date == selected_service_date),
        days[0],
    )
    compact_date = _compact_date(selected_day.service_date)
    lines = [
        f"⚙️ Manage bookings · {_long_day_label(selected_day.service_date)}",
        "",
        "Changes here affect seats immediately.",
        "Open a lift to change manual seats, cancel it, or restore it.",
        "",
    ]
    lines.extend(_lift_line(lift) for lift in selected_day.lifts)
    rows: list[list[InlineKeyboardButton]] = []
    for lift in selected_day.lifts:
        compact_time = _compact_time(lift.time)
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{'❌ ' if lift.cancelled else ''}{lift.time} · "
                        f"{lift.total_count}/{lift.capacity}"
                    ),
                    callback_data=f"mon:managelift:{compact_date}:{compact_time}",
                ),
            ]
        )
    if not selected_day.past and any(not lift.cancelled for lift in selected_day.lifts):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚫 Cancel all · {_short_day_label(selected_day.service_date)}",
                    callback_data=f"mon:cancelday:{compact_date}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Back to monitor", callback_data=f"mon:back:{compact_date}")]
    )
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_all_riders(
    days: tuple[BookingMonitorDay, ...],
    *,
    selected_service_date: date,
    rosters: tuple[tuple[BookingLiftStatus, tuple[LiftRider, ...]], ...],
) -> BookingMonitorDraft:
    lines = [f"👥 All riders · {_long_day_label(selected_service_date)}"]
    for lift, riders in rosters:
        lines.extend(("", _lift_line(lift)))
        if riders:
            lines.extend(_lift_rider_line(rider) for rider in riders)
        elif not lift.manual_count and not lift.guest_count:
            lines.append("—")
    rows: list[list[InlineKeyboardButton]] = []
    if len(days) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=("✅ " if day.service_date == selected_service_date else "")
                    + _short_day_label(day.service_date),
                    callback_data=f"mon:all:{_compact_date(day.service_date)}",
                )
                for day in days
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Back to monitor",
                callback_data=f"mon:back:{_compact_date(selected_service_date)}",
            )
        ]
    )
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_cancel_lift_confirmation(
    *,
    service_date: date,
    lift: BookingLiftStatus,
    riders: tuple[LiftRider, ...],
) -> BookingMonitorDraft:
    compact_date = _compact_date(service_date)
    compact_time = _compact_time(lift.time)
    holders = tuple(rider for rider in riders if not rider.waitlisted)
    paid = sum(rider.paid for rider in holders)
    lines = [
        f"⚠️ Cancel {lift.time} · {_long_day_label(service_date)}?",
        "",
        f"{lift.total_count} seats · {len(holders)} Telegram riders",
        f"{lift.manual_count} manual · {lift.guest_count} guests · {paid} payment claims",
        "",
        "The lift will disappear from the running schedule immediately.",
        "The bot will produce the exact refund list after cancellation.",
    ]
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🚫 Confirm cancel {lift.time}",
                        callback_data=f"mon:docancel:{compact_date}:{compact_time}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Keep lift",
                        callback_data=f"mon:managelift:{compact_date}:{compact_time}",
                    )
                ],
            ]
        ),
    )


def render_cancel_day_confirmation(day: BookingMonitorDay) -> BookingMonitorDraft:
    compact_date = _compact_date(day.service_date)
    active_lifts = sum(not lift.cancelled for lift in day.lifts)
    lines = [
        f"⚠️ Cancel all · {_long_day_label(day.service_date)}?",
        "",
        f"{active_lifts} lifts · {day.confirmed_seat_count} seats",
        f"{day.paid_rider_count} payment claims · {day.expected_gel} GEL claimed",
        "",
        "This retires the whole day and may require multiple refunds.",
        "The bot will produce the exact refund list after cancellation.",
    ]
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚫 Confirm cancel whole day",
                        callback_data=f"mon:docancelday:{compact_date}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Keep day",
                        callback_data=f"mon:manage:{compact_date}",
                    )
                ],
            ]
        ),
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
    mode: Literal["view", "payments", "manage"] = "view",
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
        paid = sum(rider.paid for rider in riders if not rider.waitlisted)
        holders = sum(not rider.waitlisted for rider in riders)
        lines.append(f"Paid: {paid}/{holders}")
        lines.append("")
        lines.extend(_lift_rider_line(rider) for rider in riders)

    rows: list[list[InlineKeyboardButton]] = []
    if mode == "payments":
        # Deliberately a separate mode: names on the read-only roster must not be
        # live payment toggles.
        payable_riders = [rider for rider in riders if not rider.waitlisted]
        for index in range(0, len(payable_riders), 2):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{'💵 ' if rider.cash else '✓ ' if rider.paid else ''}{rider.label}",
                        callback_data=(
                            f"mon:{'cashreceived' if rider.cash else 'paid'}:"
                            f"{compact_date}:{compact_time}:{rider.telegram_user_id}"
                        ),
                    )
                    for rider in payable_riders[index : index + 2]
                ]
            )
    elif mode == "manage":
        if not lift.cancelled:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="➖ Manual",
                        callback_data=f"mon:sub:{compact_date}:{compact_time}",
                    ),
                    InlineKeyboardButton(
                        text="➕ Manual",
                        callback_data=f"mon:add:{compact_date}:{compact_time}",
                    ),
                ]
            )
        action = InlineKeyboardButton(
            text="♻️ Restore lift" if lift.cancelled else "🚫 Cancel lift",
            callback_data=(
                f"mon:restore:{compact_date}:{compact_time}"
                if lift.cancelled
                else f"mon:cancel:{compact_date}:{compact_time}"
            ),
        )
        rows.append([action])
    else:
        if not lift.cancelled:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💰 Update payments",
                        callback_data=f"mon:liftmoney:{compact_date}:{compact_time}",
                    )
                ]
            )
    back_callback = (
        f"mon:manage:{compact_date}"
        if mode == "manage"
        else f"mon:info:{compact_date}:{compact_time}"
        if mode == "payments"
        else f"mon:back:{compact_date}"
    )
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=back_callback)])
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
    parts = [f"Running {day.running_count} of {active}", f"{day.confirmed_seat_count} seats"]
    if day.booked_rider_count:
        parts.append(f"claimed {day.paid_rider_count}/{day.booked_rider_count}")
    if day.expected_gel or day.owed_gel:
        parts.append(f"{day.expected_gel} of {day.owed_gel} GEL")
    return " · ".join(parts)


def _lift_rider_line(rider: LiftRider) -> str:
    if rider.waitlisted:
        marker = "⏳"
    elif rider.cash:
        marker = "💵"
    elif rider.paid:
        marker = "✅"
    else:
        marker = "🔴"
    guests = f" · +{rider.guests} guest" if rider.guests == 1 else ""
    if rider.guests > 1:
        guests = f" · +{rider.guests} guests"
    return f"{marker} {rider.label}{guests}"


def _attention_lines(day: BookingMonitorDay) -> tuple[str, ...]:
    lines: list[str] = []
    if day.attention_count:
        lines.extend(("", f"⚠️ Needs attention · {day.attention_count}"))
    if day.unpaid_riders:
        lines.append(f"🔴 Unpaid: {_money_riders(day.unpaid_riders)}")
    if day.cash_pending:
        lines.append(f"💵 Cash to collect: {_money_riders(day.cash_pending)}")
    if day.waitlist:
        lines.append(
            "⏳ Waitlist: "
            + _summarize(
                f"{rider.label} {rider.lift_time} #{rider.position}" for rider in day.waitlist
            )
        )
    if day.late_exits:
        lines.append(
            "⏰ Left after deadline: "
            + _summarize(
                f"{rider.label} {rider.lift_time}{_changed_at(rider.changed_at)}"
                for rider in day.late_exits
            )
        )
    if day.guests:
        lines.extend(
            (
                "",
                "👥 Guests: "
                + _summarize(
                    f"{guest.host_label} +{guest.count} ({guest.lift_time})" for guest in day.guests
                ),
            )
        )
    return tuple(lines)


def _money_riders(riders: tuple[MonitorRider, ...]) -> str:
    return _summarize(f"{rider.label} {rider.amount_gel} GEL" for rider in riders)


def _summarize(items: Iterable[str], *, limit: int = 8) -> str:
    values = list(items)
    shown = ", ".join(values[:limit])
    remaining = len(values) - limit
    return f"{shown}, +{remaining} more" if remaining > 0 else shown


def _changed_at(moment: datetime | None) -> str:
    return f" · {moment:%H:%M}" if moment is not None else ""


def _button_suffix(lift: BookingLiftStatus) -> str:
    if lift.cancelled:
        return " · cancelled"
    over = lift.total_count - lift.capacity
    if over > 0:
        return f" +{over} waiting"
    if lift.total_count >= lift.capacity:
        return " · full"
    return ""


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
    if lift.running_locked:
        return f"running · {lift.capacity - lift.total_count} left"
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
