import html
from collections.abc import Iterable
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
    running_locked: bool = False
    covered_count: int = 0
    deadline_closed: bool = False

    @property
    def total_count(self) -> int:
        return self.vote_count + self.manual_count + self.guest_count

    @property
    def seat_count(self) -> int:
        """Seats actually held. Anyone past capacity is waiting, not seated."""
        return min(self.total_count, self.capacity)

    @property
    def waiting_count(self) -> int:
        return max(self.total_count - self.capacity, 0)

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
        return render_admin_menu(days, schedule_line=schedule_line, history=history)

    selected_day = next(
        (day for day in days if day.service_date == selected_service_date),
        days[0],
    )
    lines = [f"📊 {_long_day_label(selected_day.service_date)}", *_summary_lines(selected_day)]
    lines.extend(("", *_lift_lines(selected_day)))
    # Each block opens with its own blank line, so a day with nothing to flag does
    # not gather a stack of them.
    lines.extend(_attention_lines(selected_day))

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
    # Lifts open from here directly. A middle "manage bookings" card listed the
    # same times a second time and cost a tap on the way to every one of them.
    rows.extend(_lift_button_rows(selected_day))
    day_row = [InlineKeyboardButton(text="👥 All riders", callback_data=f"mon:all:{compact_date}")]
    if not selected_day.past and any(not lift.cancelled for lift in selected_day.lifts):
        day_row.append(
            InlineKeyboardButton(
                text="🚫 Cancel day",
                callback_data=f"mon:cancelday:{compact_date}",
            )
        )
    rows.append(day_row)

    rows.append([InlineKeyboardButton(text="☰ Menu", callback_data="mon:menu")])

    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_admin_menu(
    days: tuple[BookingMonitorDay, ...] = (),
    *,
    schedule_line: str = "",
    history: LiftHistory | None = None,
) -> BookingMonitorDraft:
    """Everything that is not today's day: planning, the past, the settings.

    One screen rather than a start card and a quiet-week card saying the same
    thing in different words. It opens with what is live, so a midweek admin
    reads their next step instead of "No active lift polls." and nothing else.
    """
    lines = ["☰ Veloexpress admin", ""]
    if days:
        lines.extend(_menu_day_line(day) for day in days)
    else:
        lines.append("No lift polls are open.")
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
        [
            InlineKeyboardButton(text="📜 History", callback_data="mon:history"),
            InlineKeyboardButton(text="📈 Statistics", callback_data="stats:30d:0"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:service_defaults"),
            InlineKeyboardButton(text="🧾 Refunds", callback_data="mon:refunds"),
        ],
    ]
    if days:
        # Named, not "Back": from here the day is a destination, and which day it
        # is matters more than where the admin happened to come from. Yesterday
        # stays on the monitor for late bookkeeping, so it is not the one offered.
        day = next((day for day in days if not day.past), days[0])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📊 {_short_day_label(day.service_date)}",
                    callback_data=f"mon:day:{_compact_date(day.service_date)}",
                )
            ]
        )
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_posted_result(
    service_dates: tuple[date, ...],
    *,
    back_callback: str,
    back_text: str,
) -> BookingMonitorDraft:
    """What posting actually did, on the card, with the day one tap away.

    A toast says it for three seconds and leaves the planning screen behind,
    still looking like the work is pending. This says it in the card the admin
    is looking at and offers the day the polls are now open on.
    """
    ordered = tuple(sorted(service_dates))
    count = len(ordered)
    lines = [
        f"🚀 Posted {count} {'day' if count == 1 else 'days'}",
        "",
        *(f"✅ {_long_day_label(service_date)} — polls are open" for service_date in ordered),
        "",
        "Riders can book now. Seats and payments show up on the day card.",
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=f"📊 {_short_day_label(service_date)}",
                callback_data=f"mon:day:{_compact_date(service_date)}",
            )
            for service_date in ordered
        ],
        [InlineKeyboardButton(text=back_text, callback_data=back_callback)],
    ]
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _menu_day_line(day: BookingMonitorDay) -> str:
    label = _long_day_label(day.service_date)
    active = sum(not lift.cancelled for lift in day.lifts)
    if not day.running_count:
        return f"{label} · {day.confirmed_seat_count} seats booked, nothing running yet"
    funded = sum(lift.funded for lift in day.lifts)
    return f"{label} · {day.running_count} of {active} running · {funded} funded"


def render_lift_history(
    history: LiftHistory,
    *,
    period: str | None = None,
) -> BookingMonitorDraft:
    """A page of finished days, newest first, with the season under it.

    The list is the readable part; the buttons under it are how you get into a
    day. Naming each day twice would be shorter to write and worse to read, so
    the buttons stay compact and the lines carry the figures.

    `period` is the statistics period the admin came from, carried through every
    button on this page so that going back lands on the figures they were
    reading rather than resetting to the last 30 days.
    """
    suffix = f":{period}" if period else ""
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
                    callback_data=f"mon:past:{_compact_date(day.service_date)}{suffix}",
                )
                for day in chunk
            ]
        )
    pager: list[InlineKeyboardButton] = []
    if history.has_newer:
        pager.append(InlineKeyboardButton(text="⏮ Latest", callback_data=f"mon:history:0{suffix}"))
    if history.older_before is not None:
        pager.append(
            InlineKeyboardButton(
                text="📅 Older",
                callback_data=f"mon:history:{_compact_date(history.older_before)}{suffix}",
            )
        )
    if pager:
        rows.append(pager)
    bottom = []
    if history.days:
        # Two 📈 side by side told an admin nothing about which was which.
        bottom.append(InlineKeyboardButton(text="🕑 By time", callback_data="mon:trend"))
    bottom.append(
        InlineKeyboardButton(
            text="📈 Statistics",
            callback_data=f"stats:{period or '30d'}:0",
        )
    )
    rows.append(bottom)
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="mon:menu")])
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def render_lift_day_audit(
    audit: LiftDayAudit,
    *,
    period: str | None = None,
) -> BookingMonitorDraft:
    """One finished day in full: which vans went, who was on them, what came in.

    Shaped like the live day card on purpose — a count of what ran, then the
    money, then a block per lift — so reading a finished day is the same habit
    as reading today's. What is missing is every control that would change it.

    The seat list says "on the van" rather than "paid up", and those are not the
    same list — an unpaid rider who came anyway is on it, and the 🔴 next to them
    is the whole point of the screen.
    """
    lines = [f"🗓 {_long_day_label(audit.service_date)}"]
    ran = sum(lift.ran for lift in audit.lifts)
    seats = sum(lift.seats for lift in audit.lifts if lift.ran)
    lines.append(f"{ran} of {len(audit.lifts)} lifts ran · {seats} seats")
    lines.append(_audit_money_line(audit))
    if audit.cancelled:
        lines.extend(("", "❌ The whole day was cancelled."))
    if audit.reconstructed:
        # Never quietly: these figures were read off votes days later, and an
        # admin comparing them against a bank statement deserves to know.
        lines.extend(("", "⚠️ Written down late — figures reconstructed from votes."))

    if not audit.lifts:
        lines.extend(("", "No lifts were offered on this day."))
    for lift in audit.lifts:
        lines.extend(("", _audit_lift_line(lift)))
        lines.extend(_audit_seat_line(seat) for seat in lift.riders)
        if not lift.riders:
            lines.append("—")

    back = [
        InlineKeyboardButton(
            text="⬅️ Back to history",
            callback_data=f"mon:history:0:{period}" if period else "mon:history",
        )
    ]
    if audit.refunded_gel or audit.cancelled:
        # The day that owes money is where an admin looks for the refund list.
        back.insert(0, InlineKeyboardButton(text="🧾 Refunds", callback_data="mon:refunds"))
    return BookingMonitorDraft(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[back]),
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
            lines.extend(_lift_rider_line(rider, running=lift.running) for rider in riders)
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
                text="⬅️ Back",
                callback_data=f"mon:day:{_compact_date(selected_service_date)}",
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
    refund_preview: str | None = None,
) -> BookingMonitorDraft:
    compact_date = _compact_date(service_date)
    compact_time = _compact_time(lift.time)
    holders = tuple(rider for rider in riders if not rider.waitlisted)
    reported = sum(rider.paid or rider.cash for rider in holders)
    lines = [
        f"⚠️ Cancel {lift.time} · {_long_day_label(service_date)}?",
        "",
        f"{lift.seat_count} seats · {len(holders)} riders from the poll",
    ]
    offline = []
    if lift.manual_count:
        offline.append(f"{lift.manual_count} added by hand")
    if lift.guest_count:
        offline.append(
            f"{lift.guest_count} guest" if lift.guest_count == 1 else f"{lift.guest_count} guests"
        )
    if offline:
        lines.append(" · ".join(offline))
    lines.extend(
        (
            f"{reported} of them reported a payment",
            "",
            "The lift leaves the running schedule immediately.",
        )
    )
    if refund_preview:
        lines.extend(("", refund_preview))
    else:
        lines.append("The bot writes the refund list once the lift is cancelled.")
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
                        callback_data=f"mon:lift:{compact_date}:{compact_time}",
                    )
                ],
            ]
        ),
    )


def render_cancel_day_confirmation(
    day: BookingMonitorDay,
    *,
    refund_preview: str | None = None,
) -> BookingMonitorDraft:
    compact_date = _compact_date(day.service_date)
    active_lifts = sum(not lift.cancelled for lift in day.lifts)
    lines = [
        f"⚠️ Cancel the whole day · {_long_day_label(day.service_date)}?",
        "",
        f"{active_lifts} {'lift' if active_lifts == 1 else 'lifts'} · "
        f"{day.confirmed_seat_count} seats",
        f"{day.paid_rider_count} {'rider' if day.paid_rider_count == 1 else 'riders'} "
        f"reported {day.expected_gel} GEL",
        "",
        "This retires the day and may need several refunds.",
    ]
    if refund_preview:
        lines.extend(("", refund_preview))
    else:
        lines.append("The bot writes the refund list once the day is cancelled.")
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
                        callback_data=f"mon:day:{compact_date}",
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


def render_lift_detail(
    *,
    service_date: date,
    lift: BookingLiftStatus,
    riders: tuple[LiftRider, ...],
) -> BookingMonitorDraft:
    """One lift, everything about it, and the controls that change it.

    Reading and acting used to be two cards reached through a third. Opening a
    lift to see who is on it and then needing a different card to add the rider
    standing next to you was two taps of nothing.
    """
    compact_date = _compact_date(service_date)
    compact_time = _compact_time(lift.time)
    lines = [f"🚲 {lift.time} · {_long_day_label(service_date)}", ""]
    if lift.cancelled:
        lines.extend(("❌ Cancelled.", ""))
    seats = [f"{lift.seat_count}/{lift.capacity} seats"]
    if lift.waiting_count:
        seats.append(f"{lift.waiting_count} waiting")
    elif lift.seat_count >= lift.capacity:
        seats.append("full")
    seats.append("funded" if lift.funded else f"{lift.covered_count}/{MINIMUM_RIDERS} paid")
    lines.append(" · ".join(seats))
    made_of = [f"{lift.vote_count} from the poll"]
    if lift.manual_count:
        made_of.append(f"{lift.manual_count} added by hand")
    if lift.guest_count:
        made_of.append(
            f"{lift.guest_count} guest" if lift.guest_count == 1 else f"{lift.guest_count} guests"
        )
    lines.append(" · ".join(made_of))
    if riders:
        reported = sum(rider.paid or rider.cash for rider in riders if not rider.waitlisted)
        holders = sum(not rider.waitlisted for rider in riders)
        lines.append(f"Payment reported by {reported} of {holders}")
        lines.append("")
        lines.extend(_lift_rider_line(rider, running=lift.running) for rider in riders)

    rows: list[list[InlineKeyboardButton]] = []
    if not lift.cancelled:
        # Named for what they move. "Manual" left it to the admin to work out that
        # the number being changed is the offline seat count.
        rows.append(
            [
                InlineKeyboardButton(
                    text="➖ Offline seat",
                    callback_data=f"mon:sub:{compact_date}:{compact_time}",
                ),
                InlineKeyboardButton(
                    text="➕ Offline seat",
                    callback_data=f"mon:add:{compact_date}:{compact_time}",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="♻️ Restore lift" if lift.cancelled else "🚫 Cancel lift",
                callback_data=(
                    f"mon:restore:{compact_date}:{compact_time}"
                    if lift.cancelled
                    else f"mon:cancel:{compact_date}:{compact_time}"
                ),
            ),
            InlineKeyboardButton(text="⬅️ Back", callback_data=f"mon:day:{compact_date}"),
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


def _summary_lines(day: BookingMonitorDay) -> tuple[str, ...]:
    """The day in two short lines: what is leaving, and where the money stands.

    Six figures on one line was a dashboard, not a sentence — and two of them
    were misread on sight. Seats and money now stand apart, and reaching the
    rider minimum is never printed as the same thing as being paid for: a lift
    with five riders and no payments is running, not funded.

    Money is stated as reported-of-expected rather than a bare total. On its own,
    `300 GEL in` sat next to a seat count computed from a different universe —
    payments cover guests and lifts paid ahead, seats did not — and the two read
    as a contradiction that needed the source to explain.
    """
    active = sum(not lift.cancelled for lift in day.lifts)
    funded = sum(lift.funded for lift in day.lifts)
    if day.running_count:
        headline = f"{day.running_count} of {active} lifts running · {funded} funded"
    elif active:
        headline = f"Nothing running yet · {day.confirmed_seat_count} seats booked"
    else:
        headline = "Every lift is cancelled"
    lines = [headline]
    if day.expected_gel or day.owed_gel:
        lines.append(f"Reported {day.expected_gel} of {day.owed_gel} GEL")
    return tuple(lines)


def _lift_rider_line(rider: LiftRider, *, running: bool = True) -> str:
    if not running:
        # A lift that is not leaving has no paid/unpaid question to answer: the
        # money is still the day's, and it follows the rider to a lift that runs.
        marker = "💤"
    elif rider.waitlisted:
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
    """Categories worth a look, each said once and never totalled together.

    There used to be a `Needs attention · N` count above these. It added unpaid
    riders, cash, the waitlist and late exits into one number, so one rider in
    two categories counted twice and a full waitlist read as four problems.
    Waiting for a seat is not a fault, and neither is having handed over cash.
    """
    lines: list[str] = []
    if day.unpaid_riders:
        lines.append(_labelled("🔴 No payment reported: ", _money_rider_items(day.unpaid_riders)))
    if day.cash_pending:
        # The rider says the cash has changed hands and the ledger already counts
        # it as received. "Cash to collect" called that a debt.
        lines.append(_labelled("💵 Reported in cash: ", _money_rider_items(day.cash_pending)))
    if day.waitlist:
        lines.append(
            _labelled(
                "⏳ Waitlist: ",
                (f"{rider.label} {rider.lift_time} #{rider.position}" for rider in day.waitlist),
            )
        )
    if day.late_exits:
        lines.append(
            _labelled(
                "⏰ Left after deadline: ",
                (
                    f"{rider.label} {rider.lift_time}{_changed_at(rider.changed_at)}"
                    for rider in day.late_exits
                ),
            )
        )
    if day.guests:
        lines.append(_labelled("👥 Guests: ", _guest_parties(day.guests)))
    return ("", *lines) if lines else ()


def _labelled(label: str, items: Iterable[str]) -> str:
    """One line: a label and as many names as still fit beside it."""
    return label + _summarize(items, width=max(30, PHONE_LINE_WIDTH - len(label)))


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


def _money_rider_items(riders: tuple[MonitorRider, ...]) -> tuple[str, ...]:
    return tuple(f"{rider.label} {rider.amount_gel} GEL" for rider in riders)


# Roughly what a Telegram message shows on one phone line before wrapping.
PHONE_LINE_WIDTH = 78


def _summarize(items: Iterable[str], *, limit: int = 8, width: int = PHONE_LINE_WIDTH) -> str:
    """As many as fit on a phone line, then a count of the rest.

    Counting entries was not enough: eight ordinary handles fit, three long ones
    do not, and the line wrapped into a paragraph. The budget is on characters
    because that is what actually runs out.
    """
    values = list(items)
    shown: list[str] = []
    used = 0
    for value in values[:limit]:
        cost = len(value) + (2 if shown else 0)
        if shown and used + cost > width:
            break
        shown.append(value)
        used += cost
    remaining = len(values) - len(shown)
    joined = ", ".join(shown)
    return f"{joined}, +{remaining} more" if remaining > 0 else joined


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
    """Seats held, riders waiting, and how the money on this lift stands.

    Seats never exceed capacity here. `11/10` read as eleven people in a ten-seat
    van; the eleventh is waiting for a seat, so they are counted where they
    actually are.
    """
    parts = [f"🚐 {lift.time}", f"{lift.seat_count}/{lift.capacity} seats"]
    if lift.waiting_count:
        parts.append(f"{lift.waiting_count} waiting")
    elif lift.seat_count >= lift.capacity:
        parts.append("full")
    if lift.funded:
        parts.append("funded")
    elif lift.deadline_closed:
        parts.append(f"{lift.covered_count}/{MINIMUM_RIDERS} paid · decision needed")
    else:
        parts.append(f"{lift.covered_count}/{MINIMUM_RIDERS} paid")
    if lift.manual_count:
        # Not in any section above, and it is the one number an admin put there
        # by hand.
        parts.append(f"{lift.manual_count} manual")
    return " · ".join(parts)


def _lift_button_rows(day: BookingMonitorDay) -> list[list[InlineKeyboardButton]]:
    """Every still-meaningful lift of the day, three to a row.

    Short lifts that have not filled are here too: they are exactly the ones an
    admin opens to add an offline rider or to call them off.
    """
    compact_date = _compact_date(day.service_date)
    buttons = [
        InlineKeyboardButton(
            text=(
                f"{'❌ ' if lift.cancelled else ''}{lift.time} · {lift.seat_count}/{lift.capacity}"
            ),
            callback_data=f"mon:lift:{compact_date}:{_compact_time(lift.time)}",
        )
        for lift in day.lifts
    ]
    return [buttons[index : index + 3] for index in range(0, len(buttons), 3)]


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
    return f"{lift.time} — {lift.seat_count}/{lift.capacity} · {_lift_state(lift)}{suffix}"


def _lift_state(lift: BookingLiftStatus) -> str:
    if lift.waiting_count:
        return f"full · {lift.waiting_count} waiting"
    if lift.seat_count >= lift.capacity:
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
