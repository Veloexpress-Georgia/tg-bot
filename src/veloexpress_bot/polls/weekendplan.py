from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.autoschedule import EN_SHORT_MONTHS, SHORT_WEEKDAY_LABELS
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS
from veloexpress_bot.polls.schedule import enabled_lift_count, lift_range_from_cancelled

PlanCardView = Literal["main", "first", "last", "recreate"]

RECREATE_WARNING = (
    "Polls for this weekend are already posted. Recreating deletes them and posts "
    "fresh polls; tracked votes are reported first."
)


@dataclass(frozen=True)
class DayPlanStatus:
    service_date: date
    enabled: bool
    posted: bool


@dataclass(frozen=True)
class WeekendPlanView:
    week_start: date
    days: tuple[DayPlanStatus, ...]
    cancelled_lift_times: tuple[str, ...]
    opens_at: datetime | None
    auto_enabled: bool
    skipped: bool
    schedule_label: str = "off"

    @property
    def any_posted(self) -> bool:
        return any(day.posted for day in self.days)

    @property
    def has_pending_day(self) -> bool:
        return any(day.enabled and not day.posted for day in self.days)

    @property
    def all_enabled_posted(self) -> bool:
        enabled_days = [day for day in self.days if day.enabled]
        return bool(enabled_days) and all(day.posted for day in enabled_days)


@dataclass(frozen=True)
class PlanCardDraft:
    text: str
    reply_markup: InlineKeyboardMarkup


def render_weekend_plan_card(
    view_state: WeekendPlanView,
    *,
    view: PlanCardView = "main",
) -> PlanCardDraft:
    if view in {"first", "last"}:
        prompt = (
            "▶️ Choose the first lift time." if view == "first" else "⏹ Choose the last lift time."
        )
        return PlanCardDraft(
            text=f"{_card_text(view_state)}\n\n{prompt}",
            reply_markup=_range_picker_keyboard(
                boundary=view,
                cancelled_lift_times=view_state.cancelled_lift_times,
            ),
        )
    if view == "recreate":
        return PlanCardDraft(
            text=f"{_card_text(view_state)}\n\n{RECREATE_WARNING}",
            reply_markup=_recreate_keyboard(),
        )
    return PlanCardDraft(
        text=_card_text(view_state),
        reply_markup=_main_keyboard(view_state),
    )


def _card_text(view_state: WeekendPlanView) -> str:
    first_time, last_time = lift_range_from_cancelled(view_state.cancelled_lift_times)
    lift_count = enabled_lift_count(view_state.cancelled_lift_times)
    lift_label = "lift" if lift_count == 1 else "lifts"

    lines = [
        f"📋 Weekend plan · {_weekend_label(view_state.week_start)}",
        "",
        f"🚲 Lifts: {first_time} → {last_time} · {lift_count} {lift_label}",
    ]
    lines.append(_opens_line(view_state))
    posted_days = [day for day in view_state.days if day.posted]
    for day in posted_days:
        lines.append(f"✅ {_day_label(day.service_date)}: posted")
    return "\n".join(lines)


def _opens_line(view_state: WeekendPlanView) -> str:
    if view_state.all_enabled_posted:
        return "✅ Polls are posted."
    if not view_state.auto_enabled:
        # Say this before "skipped": a paused schedule is the real reason nothing
        # will open, and a leftover skip flag would otherwise take the blame.
        return "🕓 Auto-posting is off — use Post now, or ⏰ Opens to switch it on."
    if view_state.skipped:
        return "⏭ This weekend is skipped — polls will not open automatically."
    if view_state.opens_at is not None:
        return f"🕓 Opens: {_moment_label(view_state.opens_at)} (auto)"
    return "🕓 Auto-posting already ran for this weekend — use Post now if needed."


def _main_keyboard(view_state: WeekendPlanView) -> InlineKeyboardMarkup:
    day_row: list[InlineKeyboardButton] = []
    for day, key in zip(view_state.days, ("sat", "sun"), strict=True):
        if day.posted:
            text = f"📌 {_day_label(day.service_date)}"
            callback_data = "plan:posted"
        else:
            marker = "✅" if day.enabled else "🚫"
            text = f"{marker} {_day_label(day.service_date)}"
            callback_data = f"plan:day:{key}"
        day_row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
    rows = [day_row]

    first_time, last_time = lift_range_from_cancelled(view_state.cancelled_lift_times)
    rows.append(
        [
            InlineKeyboardButton(text=f"▶️ First · {first_time}", callback_data="plan:view:first"),
            InlineKeyboardButton(text=f"⏹ Last · {last_time}", callback_data="plan:view:last"),
        ]
    )

    # "When" is part of planning the weekend, not a separate menu entry: the admin
    # thinks about one weekend, not about a standing setting.
    rows.append(
        [
            InlineKeyboardButton(
                text=f"⏰ Opens · {view_state.schedule_label}",
                callback_data="plan:schedule",
            )
        ]
    )

    action_row: list[InlineKeyboardButton] = []
    if view_state.has_pending_day:
        action_row.append(InlineKeyboardButton(text="🚀 Post now", callback_data="plan:post"))
    elif view_state.all_enabled_posted:
        action_row.append(
            InlineKeyboardButton(text="♻️ Recreate polls", callback_data="plan:view:recreate")
        )
    # Skip only makes sense against a run that is actually coming.
    if view_state.auto_enabled and view_state.opens_at is not None and not view_state.any_posted:
        skip_text = "↩️ Unskip weekend" if view_state.skipped else "⏭ Skip weekend"
        action_row.append(InlineKeyboardButton(text=skip_text, callback_data="plan:skip"))
    if action_row:
        rows.append(action_row)

    rows.append([InlineKeyboardButton(text="⬅️ Menu", callback_data="plan:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _range_picker_keyboard(
    *,
    boundary: str,
    cancelled_lift_times: tuple[str, ...],
) -> InlineKeyboardMarkup:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    selected_time = first_time if boundary == "first" else last_time
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if lift.time == selected_time else "") + lift.time,
            callback_data=f"plan:{boundary}:{lift.time}",
        )
        for lift in DEFAULT_LIFTS
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="plan:view:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _recreate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="♻️ Confirm recreate", callback_data="plan:recreate"),
                InlineKeyboardButton(text="⬅️ Back", callback_data="plan:view:main"),
            ]
        ]
    )


def _weekend_label(week_start: date) -> str:
    sunday = week_start + timedelta(days=1)
    saturday_label = f"Sat {week_start.day} {EN_SHORT_MONTHS[week_start.month - 1]}"
    if sunday.month == week_start.month:
        return f"{saturday_label} + Sun {sunday.day}"
    return f"{saturday_label} + Sun {sunday.day} {EN_SHORT_MONTHS[sunday.month - 1]}"


def _day_label(service_date: date) -> str:
    return f"{SHORT_WEEKDAY_LABELS[service_date.weekday()]} {service_date.day}"


def _moment_label(moment: datetime) -> str:
    return (
        f"{SHORT_WEEKDAY_LABELS[moment.weekday()]}, {moment.day} "
        f"{EN_SHORT_MONTHS[moment.month - 1]} · {moment.hour}:{moment.minute:02d}"
    )
