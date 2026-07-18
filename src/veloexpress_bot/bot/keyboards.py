from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.defaults import DEFAULT_LIFTS
from veloexpress_bot.polls.schedule import lift_range_from_cancelled


def start_menu_keyboard(*, is_admin: bool) -> InlineKeyboardMarkup | None:
    if not is_admin:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚐 Create lift polls",
                    callback_data="menu:create_lift_poll",
                ),
                InlineKeyboardButton(
                    text="📊 Booking monitor",
                    callback_data="menu:booking_monitor",
                ),
            ],
            [
                InlineKeyboardButton(text="✖️ Close", callback_data="menu:cancel"),
            ],
        ]
    )


def setup_keyboard(
    *,
    service_dates: tuple[date, ...],
    selected_service_dates: tuple[date, ...],
    cancelled_lift_times: tuple[str, ...],
    allow_recreate: bool = False,
    view: str = "main",
) -> InlineKeyboardMarkup:
    if view in {"first", "last"}:
        return _range_picker_keyboard(
            boundary=view,
            cancelled_lift_times=cancelled_lift_times,
        )

    selected_dates = set(selected_service_dates)
    rows: list[list[InlineKeyboardButton]] = []
    day_row: list[InlineKeyboardButton] = []

    for service_date in service_dates:
        marker = "✅" if service_date in selected_dates else "🚫"
        day_row.append(
            InlineKeyboardButton(
                text=f"{marker} {_short_day_label(service_date)} {service_date:%d.%m}",
                callback_data=f"day:toggle:{service_date.isoformat()}",
            )
        )
    rows.append(day_row)

    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    rows.append(
        [
            InlineKeyboardButton(
                text=f"▶️ First · {first_time}",
                callback_data="view:first",
            ),
            InlineKeyboardButton(
                text=f"⏹ Last · {last_time}",
                callback_data="view:last",
            ),
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="♻️ Recreate polls" if allow_recreate else "🚀 Create polls",
                callback_data="poll:recreate_confirm" if allow_recreate else "poll:confirm",
            ),
            InlineKeyboardButton(text="✖️ Close setup", callback_data="poll:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _range_picker_keyboard(
    *,
    boundary: str,
    cancelled_lift_times: tuple[str, ...],
) -> InlineKeyboardMarkup:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    selected_time = first_time if boundary == "first" else last_time
    lift_buttons: list[InlineKeyboardButton] = []
    for lift in DEFAULT_LIFTS:
        marker = "✅ " if lift.time == selected_time else ""
        lift_buttons.append(
            InlineKeyboardButton(
                text=f"{marker}{lift.time}",
                callback_data=f"range:{boundary}:{lift.time}",
            )
        )

    rows = _chunked(lift_buttons, size=3)
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="view:main"),
            InlineKeyboardButton(text="✖️ Close setup", callback_data="poll:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_day_label(service_date: date) -> str:
    return "Sun" if service_date.weekday() == 6 else "Sat"


def _chunked(
    buttons: list[InlineKeyboardButton],
    *,
    size: int,
) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]
