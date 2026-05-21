from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.defaults import DEFAULT_LIFTS, StartLocation


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
                InlineKeyboardButton(text="✖️ Cancel", callback_data="menu:cancel"),
            ]
        ]
    )


def setup_keyboard(
    *,
    service_dates: tuple[date, ...],
    selected_service_dates: tuple[date, ...],
    cancelled_lift_times: tuple[str, ...],
    first_lift_location: StartLocation,
    allow_recreate: bool = False,
    view: str = "main",
) -> InlineKeyboardMarkup:
    cancelled = set(cancelled_lift_times)
    if view == "times":
        return _times_keyboard(
            cancelled=cancelled,
            first_lift_location=first_lift_location,
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

    rows.append(
        [
            InlineKeyboardButton(
                text=f"📍 First lift: {_location_label(first_lift_location)}",
                callback_data="first:toggle",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🕓 {_times_summary(cancelled)}",
                callback_data="view:times",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="♻️ Recreate polls" if allow_recreate else "🚀 Create polls",
                callback_data="poll:recreate_confirm" if allow_recreate else "poll:confirm",
            ),
            InlineKeyboardButton(text="✖️ Cancel", callback_data="poll:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _times_keyboard(
    *,
    cancelled: set[str],
    first_lift_location: StartLocation,
) -> InlineKeyboardMarkup:
    lift_buttons: list[InlineKeyboardButton] = []
    for index, lift in enumerate(DEFAULT_LIFTS):
        active = lift.time not in cancelled
        marker = "✅" if active else "🚫"
        location = first_lift_location if index == 0 else StartLocation.VAKE
        lift_buttons.append(
            InlineKeyboardButton(
                text=f"{marker} {lift.time} · {_short_location_label(location)}",
                callback_data=f"lift:toggle:{lift.time}",
            )
        )

    rows = _chunked(lift_buttons, size=2)
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Back", callback_data="view:main"),
            InlineKeyboardButton(text="✖️ Cancel", callback_data="poll:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _times_summary(cancelled: set[str]) -> str:
    active_count = len(DEFAULT_LIFTS) - len(cancelled)
    if active_count == len(DEFAULT_LIFTS):
        return "All 4 times enabled"
    if active_count == 1:
        return "1 time enabled"
    return f"{active_count} times enabled"


def _location_label(location: StartLocation) -> str:
    match location:
        case StartLocation.JUSTICE_HALL:
            return "Justice Hall"
        case StartLocation.VAKE:
            return "Vake"


def _short_location_label(location: StartLocation) -> str:
    match location:
        case StartLocation.JUSTICE_HALL:
            return "Justice"
        case StartLocation.VAKE:
            return "Vake"


def _short_day_label(service_date: date) -> str:
    return "Sun" if service_date.weekday() == 6 else "Sat"


def _chunked(
    buttons: list[InlineKeyboardButton],
    *,
    size: int,
) -> list[list[InlineKeyboardButton]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]
