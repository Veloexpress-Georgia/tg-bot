from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.db.models import PollAutoSchedule

SATURDAY_WEEKDAY = 5
ANNOUNCE_LEAD_CHOICES = (0, 60, 120, 180)
CREATION_TIME_CHOICES = tuple(f"{hour:02d}:00" for hour in range(9, 21))
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
SHORT_WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CREATION_WEEKDAY_CHOICES = 6  # Monday..Saturday; Sunday is mid-weekend
PENDING_WEEK_LOOKAHEAD = 4

EN_SHORT_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

CardView = Literal["main", "day", "time"]
TickKind = Literal["announce", "create", "mark_skipped"]


@dataclass(frozen=True)
class AutoScheduleState:
    enabled: bool = False
    creation_weekday: int = 4
    creation_time: str = "14:00"
    announce_lead_minutes: int = 120
    skip_week_start: date | None = None
    last_announced_week_start: date | None = None
    last_created_week_start: date | None = None


@dataclass(frozen=True)
class TickAction:
    kind: TickKind
    service_week_start: date
    creation_at: datetime


@dataclass(frozen=True)
class ScheduleCardDraft:
    text: str
    reply_markup: InlineKeyboardMarkup


def upcoming_service_week_start(today: date) -> date:
    return today + timedelta(days=(SATURDAY_WEEKDAY - today.weekday()) % 7)


def creation_moment(
    service_week_start: date,
    *,
    weekday: int,
    creation_time: str,
    zone: tzinfo,
) -> datetime:
    if not 0 <= weekday <= SATURDAY_WEEKDAY:
        msg = "Creation weekday must be between Monday and Saturday."
        raise ValueError(msg)
    days_back = (service_week_start.weekday() - weekday) % 7
    hour, minute = _parse_time(creation_time)
    return datetime.combine(
        service_week_start - timedelta(days=days_back),
        time(hour, minute),
        tzinfo=zone,
    )


def decide_tick(state: AutoScheduleState, now: datetime) -> TickAction | None:
    if not state.enabled or now.tzinfo is None:
        return None

    week = upcoming_service_week_start(now.date())
    creation_at = creation_moment(
        week,
        weekday=state.creation_weekday,
        creation_time=state.creation_time,
        zone=now.tzinfo,
    )
    skipped = state.skip_week_start == week

    if now >= creation_at:
        if state.last_created_week_start == week:
            return None
        kind: TickKind = "mark_skipped" if skipped else "create"
        return TickAction(kind=kind, service_week_start=week, creation_at=creation_at)

    if (
        state.announce_lead_minutes > 0
        and not skipped
        and state.last_announced_week_start != week
        and now >= creation_at - timedelta(minutes=state.announce_lead_minutes)
    ):
        return TickAction(kind="announce", service_week_start=week, creation_at=creation_at)
    return None


def next_pending_creation(
    state: AutoScheduleState,
    now: datetime,
    *,
    zone: tzinfo,
) -> tuple[date, datetime] | None:
    week = upcoming_service_week_start(now.date())
    for _ in range(PENDING_WEEK_LOOKAHEAD):
        creation_at = creation_moment(
            week,
            weekday=state.creation_weekday,
            creation_time=state.creation_time,
            zone=zone,
        )
        if week not in (state.last_created_week_start, state.skip_week_start):
            return week, creation_at
        week += timedelta(days=7)
    return None


def skip_target_week(state: AutoScheduleState, now: datetime, *, zone: tzinfo) -> date | None:
    if not state.enabled:
        # Skipping suppresses one automatic run, so with auto-posting paused there
        # is nothing to suppress and the button would be a lie.
        return None
    week = upcoming_service_week_start(now.date())
    for _ in range(PENDING_WEEK_LOOKAHEAD):
        creation_at = creation_moment(
            week,
            weekday=state.creation_weekday,
            creation_time=state.creation_time,
            zone=zone,
        )
        if week != state.last_created_week_start and now < creation_at:
            return week
        week += timedelta(days=7)
    return None


def next_announce_lead(current_minutes: int) -> int:
    try:
        index = ANNOUNCE_LEAD_CHOICES.index(current_minutes)
    except ValueError:
        return ANNOUNCE_LEAD_CHOICES[0]
    return ANNOUNCE_LEAD_CHOICES[(index + 1) % len(ANNOUNCE_LEAD_CHOICES)]


def state_from_row(row: PollAutoSchedule | None) -> AutoScheduleState:
    """The stored schedule, with the defaults a half-written row leaves out."""
    if row is None:
        return AutoScheduleState()
    return AutoScheduleState(
        enabled=row.enabled if row.enabled is not None else False,
        creation_weekday=row.creation_weekday if row.creation_weekday is not None else 4,
        creation_time=row.creation_time or "14:00",
        announce_lead_minutes=(
            row.announce_lead_minutes if row.announce_lead_minutes is not None else 120
        ),
        skip_week_start=row.skip_week_start,
        last_announced_week_start=row.last_announced_week_start,
        last_created_week_start=row.last_created_week_start,
    )


def render_schedule_summary(
    state: AutoScheduleState | None,
    *,
    now: datetime,
    zone: tzinfo,
) -> str:
    """One line: when the next polls appear, or why they will not."""
    if state is None or not state.enabled:
        return "⏰ Auto-posting is off — post from 📋 Weekend."
    pending = next_pending_creation(state, now, zone=zone)
    if pending is None:
        return f"⏰ Polls open {WEEKDAY_LABELS[state.creation_weekday]} {state.creation_time}."
    _, creation_at = pending
    if now >= creation_at:
        return "⏰ Polls are due now."
    return (
        f"⏰ Next polls open {SHORT_WEEKDAY_LABELS[creation_at.weekday()]} "
        f"{creation_at.hour}:{creation_at.minute:02d}."
    )


def render_schedule_announcement(
    service_week_start: date,
    *,
    creation_at: datetime,
    announced_at: datetime,
) -> str:
    weekend = _weekend_label(service_week_start)
    creation_time = f"{creation_at.hour}:{creation_at.minute:02d}"
    if announced_at.astimezone(creation_at.tzinfo).date() == creation_at.date():
        when = f"today at {creation_time}"
    else:
        when = f"on {WEEKDAY_LABELS[creation_at.weekday()]} at {creation_time}"
    return f"📣 Lift polls for {weekend} open {when}. Get ready to vote!"


def render_schedule_card(
    state: AutoScheduleState,
    *,
    view: CardView = "main",
    timezone_label: str,
    now: datetime,
    zone: tzinfo,
    planned_lifts_label: str | None = None,
) -> ScheduleCardDraft:
    text = _card_text(
        state,
        timezone_label=timezone_label,
        now=now,
        zone=zone,
        planned_lifts_label=planned_lifts_label,
    )
    if view == "day":
        return ScheduleCardDraft(
            text=f"{text}\n\n🗓 Choose the day polls are created.",
            reply_markup=_day_picker_keyboard(state.creation_weekday),
        )
    if view == "time":
        return ScheduleCardDraft(
            text=f"{text}\n\n🕑 Choose the time polls are created.",
            reply_markup=_time_picker_keyboard(state.creation_time),
        )
    return ScheduleCardDraft(
        text=text,
        reply_markup=_main_keyboard(state, now=now, zone=zone),
    )


def encode_card_time(creation_time: str) -> str:
    hour, minute = _parse_time(creation_time)
    return f"{hour:02d}{minute:02d}"


def decode_card_time(value: str) -> str:
    normalized = value.zfill(4)
    return f"{normalized[:2]}:{normalized[2:]}"


def _card_text(
    state: AutoScheduleState,
    *,
    timezone_label: str,
    now: datetime,
    zone: tzinfo,
    planned_lifts_label: str | None = None,
) -> str:
    lines = ["⏰ When polls open", ""]
    if state.enabled:
        lines.append("Status: ✅ polls are created automatically")
    else:
        lines.append("Status: ⏸ paused — create polls manually")
    lines.append(
        f"🗓 When: {WEEKDAY_LABELS[state.creation_weekday]} · "
        f"{state.creation_time} ({timezone_label})"
    )
    lines.append(f"📣 Announce: {_announce_label(state.announce_lead_minutes)}")

    if state.enabled:
        pending = next_pending_creation(state, now, zone=zone)
        if pending is not None:
            week, creation_at = pending
            due = "due now" if now >= creation_at else _moment_label(creation_at)
            lines.append(f"🚀 Next: {due} → polls for {_weekend_label(week)}")
        if planned_lifts_label is not None:
            lines.append(f"🚲 Lifts: {planned_lifts_label} — learned from recent polls")
            lines.append("Need a different weekend? Create polls manually before the run:")
            lines.append("the schedule only fills in days without active polls.")
    if state.skip_week_start is not None and state.skip_week_start >= now.date():
        lines.append(f"⏭ Skipping the {_weekend_label(state.skip_week_start)} weekend.")
    return "\n".join(lines)


def _main_keyboard(
    state: AutoScheduleState,
    *,
    now: datetime,
    zone: tzinfo,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="⏸ Pause" if state.enabled else "▶️ Enable",
                callback_data="sched:toggle",
            ),
            InlineKeyboardButton(
                text=f"📣 {_announce_label(state.announce_lead_minutes)}",
                callback_data="sched:announce",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"🗓 {WEEKDAY_LABELS[state.creation_weekday]}",
                callback_data="sched:view:day",
            ),
            InlineKeyboardButton(
                text=f"🕑 {state.creation_time}",
                callback_data="sched:view:time",
            ),
        ],
    ]
    # Skipping belongs to one weekend, so it lives on the weekend card and only
    # there. Offering it from both screens meant two buttons for one flag, in two
    # places that named the weekend differently.
    rows.append([InlineKeyboardButton(text="⬅️ Weekend", callback_data="sched:plan")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _day_picker_keyboard(selected_weekday: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if weekday == selected_weekday else "") + label,
            callback_data=f"sched:day:{weekday}",
        )
        for weekday, label in enumerate(SHORT_WEEKDAY_LABELS[:CREATION_WEEKDAY_CHOICES])
    ]
    rows = [buttons[:3], buttons[3:]]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="sched:view:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _time_picker_keyboard(selected_time: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if choice == selected_time else "") + choice,
            callback_data=f"sched:time:{encode_card_time(choice)}",
        )
        for choice in CREATION_TIME_CHOICES
    ]
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="sched:view:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _announce_label(lead_minutes: int) -> str:
    if lead_minutes <= 0:
        return "off"
    hours = lead_minutes // 60
    return f"{hours}h before"


def _weekend_label(service_week_start: date) -> str:
    sunday = service_week_start + timedelta(days=1)
    saturday_label = f"Sat {service_week_start.day} {EN_SHORT_MONTHS[service_week_start.month - 1]}"
    if sunday.month == service_week_start.month:
        return f"{saturday_label} + Sun {sunday.day}"
    return f"{saturday_label} + Sun {sunday.day} {EN_SHORT_MONTHS[sunday.month - 1]}"


def _moment_label(moment: datetime) -> str:
    return (
        f"{SHORT_WEEKDAY_LABELS[moment.weekday()]}, {moment.day} "
        f"{EN_SHORT_MONTHS[moment.month - 1]} · {moment.hour}:{moment.minute:02d}"
    )


def _parse_time(value: str) -> tuple[int, int]:
    hour_text, minute_text = value.split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        msg = f"Invalid creation time: {value}"
        raise ValueError(msg)
    return hour, minute
