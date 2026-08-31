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
    covered_count: int = 0
    deadline_closed: bool = False

    @property
    def total_count(self) -> int:
        return self.vote_count + self.manual_count + self.guest_count

    @property
    def running(self) -> bool:
        return not self.cancelled and (self.total_count >= MINIMUM_RIDERS or self.running_locked)

    @property
    def funded(self) -> bool:
        return not self.cancelled and self.covered_count >= MINIMUM_RIDERS


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
class LiftHistoryDay:
    """A finished day, as it was written down the morning after."""

    service_date: date
    ran_count: int
    lift_count: int
    seat_count: int
    paid_gel: int
    cancelled: bool = False
    # Written down late — a deploy, or a worker down over the weekend — so it was
    # read off votes that had already had time to move.
    reconstructed: bool = False


@dataclass(frozen=True)
class LiftHistory:
    days: tuple[LiftHistoryDay, ...]
    total_days: int
    total_ran: int
    total_seats: int
    total_gel: int
    # Set when there are older days than this page shows.
    older_before: date | None = None
    # False on the first page, where "latest" would go nowhere.
    has_newer: bool = False

    @property
    def last_weekend(self) -> tuple[LiftHistoryDay, ...]:
        """The most recent lift day and anything within a day of it."""
        if not self.days:
            return ()
        newest = self.days[0].service_date
        return tuple(day for day in self.days if (newest - day.service_date).days <= 1)


@dataclass(frozen=True)
class LiftDayAuditSeat:
    label: str
    seats: int
    guests: int
    covered_seats: int

    @property
    def covered(self) -> bool:
        return self.covered_seats >= self.seats


@dataclass(frozen=True)
class LiftDayAuditLift:
    lift_time: str
    ran: bool
    seats: int
    capacity: int
    covered_seats: int
    manual_seats: int
    guest_seats: int
    riders: tuple[LiftDayAuditSeat, ...] = ()


@dataclass(frozen=True)
class LiftDayAudit:
    service_date: date
    lifts: tuple[LiftDayAuditLift, ...]
    price_gel: int
    received_gel: int
    refunded_gel: int
    cancelled: bool = False
    reconstructed: bool = False


@dataclass(frozen=True)
class TrendRow:
    label: str
    days_ran: int
    days_offered: int
    total_seats: int

    @property
    def average_seats(self) -> float:
        return self.total_seats / self.days_ran if self.days_ran else 0.0


@dataclass(frozen=True)
class LiftTrend:
    by_lift: tuple[TrendRow, ...]
    by_weekday: tuple[TrendRow, ...]
    since: date | None = None
    until: date | None = None


@dataclass(frozen=True)
class BookingMonitorDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_booking_monitor(
    days: tuple[BookingMonitorDay, ...],
    *,
    selected_service_date: date | None,
    schedule_line: str = "",
    history: LiftHistory | None = None,
) -> BookingMonitorDraft:
    if not days:
        return _render_quiet_week(schedule_line=schedule_line, history=history)

    selected_day = next(
        (day for day in days if day.service_date == selected_service_date),
        days[0],
    )
    lines = [
        f"📊 Booking monitor · {_long_day_label(selected_day.service_date)}",
        "",
        _summary_line(selected_day),
    ]
    # Each block opens with its own blank line, so a day with nothing to flag does
    # not gather a stack of them.
    lines.extend(_attention_lines(selected_day))
    lines.extend(("", *_lift_lines(selected_day)))

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
    if history is not None and history.days:
        # Also here, not only on the quiet-week card. The weeks an admin is
        # actually in the bot are the weeks with lifts running, and history used
        # to be unreachable on every one of them.
        rows.append([InlineKeyboardButton(text="📜 Lift history", callback_data="mon:history")])

    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _render_quiet_week(
    *,
    schedule_line: str,
    history: LiftHistory | None,
) -> BookingMonitorDraft:
    """Midweek there is nothing to monitor, so the card becomes the way in.

    Left as a bare "No active lift polls." it was a dead end with no buttons at
    all — an admin opening it on a Monday had to run /start again to get anywhere.
    """
    lines = ["📊 Booking monitor · quiet week", "", "No lift polls are open."]
    if schedule_line:
        lines.append(schedule_line)
    if history is not None and history.last_weekend:
        recent = history.last_weekend
        lines.extend(("", f"Last lifts · {_day_span_label(recent)}", _weekend_total_line(recent)))
    rows = [
        [
            InlineKeyboardButton(text="📋 Weekend", callback_data="menu:weekend_plan"),
            InlineKeyboardButton(text="➕ Extra lift day", callback_data="menu:extra_day"),
        ],
        [InlineKeyboardButton(text="⏰ Schedule", callback_data="plan:schedule")],
    ]
    if history is not None and history.days:
        rows.append([InlineKeyboardButton(text="📜 Lift history", callback_data="mon:history")])
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_lift_history(history: LiftHistory) -> BookingMonitorDraft:
    """A page of finished days, newest first, with the season under it.

    The list is the readable part; the buttons under it are how you get into a
    day. Naming each day twice would be shorter to write and worse to read, so
    the buttons stay compact and the lines carry the figures.
    """
    lines = ["📜 Lift history", ""]
    if not history.days:
        lines.append("No lift day has finished yet.")
    else:
        lines.extend(_history_day_line(day) for day in history.days)
        lines.extend(
            (
                "",
                f"All time · {history.total_days} days · {history.total_ran} lifts · "
                f"{history.total_seats} seats · {history.total_gel} GEL",
            )
        )

    rows: list[list[InlineKeyboardButton]] = []
    for chunk in _chunked(history.days, size=4):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_short_day_label(day.service_date),
                    callback_data=f"mon:past:{_compact_date(day.service_date)}",
                )
                for day in chunk
            ]
        )
    pager: list[InlineKeyboardButton] = []
    if history.has_newer:
        pager.append(InlineKeyboardButton(text="⏮ Latest", callback_data="mon:history"))
    if history.older_before is not None:
        pager.append(
            InlineKeyboardButton(
                text="📅 Older",
                callback_data=f"mon:history:{_compact_date(history.older_before)}",
            )
        )
    if pager:
        rows.append(pager)
    if history.days:
        rows.append([InlineKeyboardButton(text="📈 Trend", callback_data="mon:trend")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="mon:menu")])
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_lift_day_audit(audit: LiftDayAudit) -> BookingMonitorDraft:
    """One finished day in full: which vans went, who was on them, what came in.

    The seat list says "on the van" rather than "paid up", and those are not the
    same list — an unpaid rider who came anyway is on it, and the 🔴 next to them
    is the whole point of the screen.
    """
    lines = [f"🗓 {_long_day_label(audit.service_date)}", ""]
    if audit.cancelled:
        lines.extend(("❌ The whole day was cancelled.", ""))
    if audit.reconstructed:
        # Never quietly: these figures were read off votes days later, and an
        # admin comparing them against a bank statement deserves to know.
        lines.extend(("⚠️ Written down late — figures reconstructed from votes.", ""))
    lines.append(_audit_money_line(audit))

    if not audit.lifts:
        lines.extend(("", "No lifts were offered on this day."))
    for lift in audit.lifts:
        lines.extend(("", _audit_lift_line(lift)))
        lines.extend(_audit_seat_line(seat) for seat in lift.riders)
        if not lift.riders:
            lines.append("—")

    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back to history", callback_data="mon:history")],
            ]
        ),
    )


def render_lift_trend(trend: LiftTrend) -> BookingMonitorDraft:
    """Which departures earn their place in the template, and which days carry them."""
    lines = ["📈 Trend"]
    if trend.since is not None and trend.until is not None:
        lines.append(_range_label(trend.since, trend.until))
    if not trend.by_lift:
        lines.extend(("", "No finished lift day to read a trend from yet."))
    if trend.by_lift:
        lines.extend(("", "By departure"))
        lines.extend(_trend_line(row) for row in trend.by_lift)
    if trend.by_weekday:
        lines.extend(("", "By day"))
        lines.extend(_trend_line(row) for row in trend.by_weekday)
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back to history", callback_data="mon:history")],
            ]
        ),
    )


def _trend_line(row: TrendRow) -> str:
    if not row.days_ran:
        return f"{row.label} — never ran in {row.days_offered}"
    return (
        f"{row.label} — ran {row.days_ran} of {row.days_offered} · "
        f"{row.average_seats:.1f} seats avg"
    )


def _audit_money_line(audit: LiftDayAudit) -> str:
    parts = [f"{audit.price_gel} GEL per seat", f"{audit.received_gel} GEL received"]
    if audit.refunded_gel:
        parts.append(f"{audit.refunded_gel} GEL refunded")
    return " · ".join(parts)


def _audit_lift_line(lift: LiftDayAuditLift) -> str:
    marker = "🚐" if lift.ran else "💤"
    state = "ran" if lift.ran else "did not run"
    parts = [f"{marker} {lift.lift_time}", state, f"{lift.seats}/{lift.capacity}"]
    parts.append(f"{lift.covered_seats} paid")
    if lift.manual_seats:
        parts.append(f"{lift.manual_seats} manual")
    if lift.guest_seats == 1:
        parts.append("1 guest")
    elif lift.guest_seats > 1:
        parts.append(f"{lift.guest_seats} guests")
    return " · ".join(parts)


def _audit_seat_line(seat: LiftDayAuditSeat) -> str:
    marker = "✅" if seat.covered else "🔴"
    guests = ""
    if seat.guests == 1:
        guests = " · +1 guest"
    elif seat.guests > 1:
        guests = f" · +{seat.guests} guests"
    return f"{marker} {seat.label}{guests}"


def _history_day_line(day: LiftHistoryDay) -> str:
    if day.cancelled:
        return f"{_long_day_label(day.service_date)} · ❌ cancelled"
    mark = " ⚠️" if day.reconstructed else ""
    return (
        f"{_long_day_label(day.service_date)} · {day.ran_count}/{day.lift_count} lifts · "
        f"{day.seat_count} seats · {day.paid_gel} GEL{mark}"
    )


def _range_label(since: date, until: date) -> str:
    if since.year != until.year:
        return f"{since.day} {EN_SHORT_MONTHS[since.month]} {since.year} – {_long_date(until)}"
    return f"{since.day} {EN_SHORT_MONTHS[since.month]} – {_long_date(until)}"


def _long_date(value: date) -> str:
    return f"{value.day} {EN_SHORT_MONTHS[value.month]}"


def _chunked(
    days: tuple[LiftHistoryDay, ...],
    *,
    size: int,
) -> Iterable[tuple[LiftHistoryDay, ...]]:
    for start in range(0, len(days), size):
        yield days[start : start + size]


def _day_span_label(days: tuple[LiftHistoryDay, ...]) -> str:
    oldest = days[-1].service_date
    newest = days[0].service_date
    if oldest == newest:
        return _long_day_label(newest)
    return f"{oldest.day}–{newest.day} {EN_SHORT_MONTHS[newest.month]}"


def _weekend_total_line(days: tuple[LiftHistoryDay, ...]) -> str:
    ran = sum(day.ran_count for day in days)
    seats = sum(day.seat_count for day in days)
    paid = sum(day.paid_gel for day in days)
    return f"🚐 {ran} lifts · {seats} seats · {paid} GEL"


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
    mode: Literal["view", "manage"] = "view",
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
    if mode == "manage":
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
    back_callback = f"mon:manage:{compact_date}" if mode == "manage" else f"mon:back:{compact_date}"
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
    funded = sum(lift.funded for lift in day.lifts)
    parts = [
        f"At minimum {day.running_count} of {active}",
        f"funded {funded}",
        f"{day.confirmed_seat_count} seats",
    ]
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
        lines.extend(("", "👥 Guests: " + _summarize(_guest_parties(day.guests))))
    return tuple(lines)


def _guest_parties(guests: tuple[MonitorGuest, ...]) -> tuple[str, ...]:
    """One entry per host, listing their lifts.

    The same rider bringing the same guest on three lifts is one arrangement, and
    reading their name three times says nothing the lift list does not.
    """
    lifts_by_party: dict[tuple[int, str, int], list[str]] = {}
    for guest in guests:
        key = (guest.host_user_id, guest.host_label, guest.count)
        lifts_by_party.setdefault(key, []).append(guest.lift_time)
    return tuple(
        f"{label} +{count} ({', '.join(lifts)})"
        for (_, label, count), lifts in lifts_by_party.items()
    )


def _money_riders(riders: tuple[MonitorRider, ...]) -> str:
    return _summarize(f"{rider.label} {rider.amount_gel} GEL" for rider in riders)


def _summarize(items: Iterable[str], *, limit: int = 8) -> str:
    values = list(items)
    shown = ", ".join(values[:limit])
    remaining = len(values) - limit
    return f"{shown}, +{remaining} more" if remaining > 0 else shown


def _changed_at(moment: datetime | None) -> str:
    return f" · {moment:%H:%M}" if moment is not None else ""


def _lift_lines(day: BookingMonitorDay) -> tuple[str, ...]:
    """Lifts that are happening, one line each; the rest in a single line.

    An admin does not need "needs 5 more" spelled out five times — they know what
    five means, and reading it on every empty lift buried the three that are
    actually running. Empty lifts still get named: nothing is hidden, it is just
    not given a line of its own.
    """
    lines = [_running_lift_line(lift) for lift in day.lifts if lift.running]
    cancelled = tuple(lift.time for lift in day.lifts if lift.cancelled)
    quiet = tuple(lift.time for lift in day.lifts if not lift.running and not lift.cancelled)
    if quiet:
        lines.append(f"💤 Not filled: {', '.join(quiet)}")
    if cancelled:
        lines.append(f"❌ Cancelled: {', '.join(cancelled)}")
    if not lines:
        lines.append("Nothing booked yet.")
    return tuple(lines)


def _running_lift_line(lift: BookingLiftStatus) -> str:
    """Seats and nothing else, unless something needs acting on.

    "1 left" is arithmetic the admin can do from 9/10; "full" and "waitlist +2"
    are the two states that change what they would do next.
    """
    parts = [f"🚐 {lift.time}", f"{lift.total_count}/{lift.capacity}"]
    if lift.funded:
        parts.append("funded")
    elif lift.deadline_closed:
        parts.append(f"{lift.covered_count}/{MINIMUM_RIDERS} paid · decision needed")
    else:
        parts.append(f"payment open · {lift.covered_count}/{MINIMUM_RIDERS} paid")
    over = lift.total_count - lift.capacity
    if over > 0:
        parts.append(f"waitlist +{over}")
    elif lift.total_count >= lift.capacity:
        parts.append("full")
    if lift.manual_count:
        # Not in any section above, and it is the one number an admin put there
        # by hand.
        parts.append(f"{lift.manual_count} manual")
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
    if lift.running_locked:
        if lift.funded:
            return f"funded · {lift.capacity - lift.total_count} left"
        return f"decision needed · {lift.covered_count}/{MINIMUM_RIDERS} paid"
    if lift.total_count < MINIMUM_RIDERS:
        return f"needs {MINIMUM_RIDERS - lift.total_count} more"
    if lift.funded:
        return f"funded · {lift.capacity - lift.total_count} left"
    return f"payment open · {lift.covered_count}/{MINIMUM_RIDERS} paid"


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
