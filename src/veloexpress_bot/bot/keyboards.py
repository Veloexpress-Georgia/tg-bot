from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_menu_keyboard(*, is_admin: bool) -> InlineKeyboardMarkup | None:
    if not is_admin:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Weekend",
                    callback_data="menu:weekend_plan",
                ),
                InlineKeyboardButton(
                    text="📊 Booking monitor",
                    callback_data="menu:booking_monitor",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="➕ Extra lift day",
                    callback_data="menu:extra_day",
                ),
            ],
            [
                InlineKeyboardButton(text="✖️ Close", callback_data="menu:cancel"),
            ],
        ]
    )
