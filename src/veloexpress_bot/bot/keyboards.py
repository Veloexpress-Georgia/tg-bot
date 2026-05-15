from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.defaults import DEFAULT_LIFTS, StartLocation


def setup_keyboard(
    *,
    service_dates: tuple[date, ...],
    selected_service_dates: tuple[date, ...],
    cancelled_lift_times: tuple[str, ...],
    first_lift_location: StartLocation,
    allow_duplicate: bool = False,
) -> InlineKeyboardMarkup:
    cancelled = set(cancelled_lift_times)
    selected_dates = set(selected_service_dates)
    rows: list[list[InlineKeyboardButton]] = []

    for service_date in service_dates:
        marker = "✅" if service_date in selected_dates else "🚫"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {_day_label(service_date)} {service_date:%d.%m}",
                    callback_data=f"day:toggle:{service_date.isoformat()}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"First: {_location_label(first_lift_location)}",
                callback_data="first:toggle",
            )
        ]
    )

    for lift in DEFAULT_LIFTS:
        active = lift.time not in cancelled
        marker = "✅" if active else "🚫"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker} {lift.time}",
                    callback_data=f"lift:toggle:{lift.time}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="Create anyway" if allow_duplicate else "Create polls",
                callback_data="poll:confirm_force" if allow_duplicate else "poll:confirm",
            ),
            InlineKeyboardButton(text="Cancel", callback_data="poll:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _location_label(location: StartLocation) -> str:
    match location:
        case StartLocation.JUSTICE_HALL:
            return "Justice Hall"
        case StartLocation.VAKE:
            return "Vake"


def _day_label(service_date: date) -> str:
    return "Sunday" if service_date.weekday() == 6 else "Saturday"
