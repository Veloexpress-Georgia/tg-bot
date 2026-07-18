from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.autoschedule import EN_SHORT_MONTHS, SHORT_WEEKDAY_LABELS
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS
from veloexpress_bot.polls.schedule import enabled_lift_count, lift_range_from_cancelled

ExtraDayView = Literal["date", "main", "first", "last"]

DATE_CHOICE_COUNT = 7


@dataclass(frozen=True)
class ExtraDayDraftState:
    selected_date: date | None
    cancelled_lift_times: tuple[str, ...]


@dataclass(frozen=True)
class ExtraDayCardDraft:
    text: str
    reply_markup: InlineKeyboardMarkup


def date_choices(today: date) -> tuple[date, ...]:
    return tuple(today + timedelta(days=offset) for offset in range(DATE_CHOICE_COUNT))


def render_extra_day_card(
    state: ExtraDayDraftState,
    *,
    view: ExtraDayView,
    today: date,
) -> ExtraDayCardDraft:
    if view == "date" or state.selected_date is None:
        return ExtraDayCardDraft(
            text="➕ Extra lift day\n\nPick a date — polls post to the group immediately.",
            reply_markup=_date_picker_keyboard(today, selected_date=state.selected_date),
        )
    if view in {"first", "last"}:
        prompt = (
            "▶️ Choose the first lift time." if view == "first" else "⏹ Choose the last lift time."
        )
        return ExtraDayCardDraft(
            text=f"{_card_text(state.selected_date, state.cancelled_lift_times)}\n\n{prompt}",
            reply_markup=_range_picker_keyboard(
                boundary=view,
                cancelled_lift_times=state.cancelled_lift_times,
            ),
        )
    return ExtraDayCardDraft(
        text=_card_text(state.selected_date, state.cancelled_lift_times),
        reply_markup=_main_keyboard(state.selected_date, state.cancelled_lift_times),
    )


def _card_text(selected_date: date, cancelled_lift_times: tuple[str, ...]) -> str:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    lift_count = enabled_lift_count(cancelled_lift_times)
    lift_label = "lift" if lift_count == 1 else "lifts"
    return (
        f"➕ Extra lift day · {_date_label(selected_date)}\n"
        "\n"
        f"🚲 Lifts: {first_time} → {last_time} · {lift_count} {lift_label}\n"
        "🚀 Posts to the group immediately."
    )


def _date_picker_keyboard(
    today: date,
    *,
    selected_date: date | None,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if choice == selected_date else "") + _date_label(choice),
            callback_data=f"extra:date:{choice.isoformat()}",
        )
        for choice in date_choices(today)
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Menu", callback_data="extra:menu"),
            InlineKeyboardButton(text="✖️ Close", callback_data="extra:close"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_keyboard(
    selected_date: date,
    cancelled_lift_times: tuple[str, ...],
) -> InlineKeyboardMarkup:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📅 {_date_label(selected_date)} · change",
                    callback_data="extra:view:date",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"▶️ First · {first_time}", callback_data="extra:view:first"
                ),
                InlineKeyboardButton(text=f"⏹ Last · {last_time}", callback_data="extra:view:last"),
            ],
            [InlineKeyboardButton(text="🚀 Post polls", callback_data="extra:post")],
            [
                InlineKeyboardButton(text="⬅️ Menu", callback_data="extra:menu"),
                InlineKeyboardButton(text="✖️ Close", callback_data="extra:close"),
            ],
        ]
    )


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
            callback_data=f"extra:{boundary}:{lift.time}",
        )
        for lift in DEFAULT_LIFTS
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="extra:view:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _date_label(choice: date) -> str:
    return (
        f"{SHORT_WEEKDAY_LABELS[choice.weekday()]} {choice.day} {EN_SHORT_MONTHS[choice.month - 1]}"
    )
