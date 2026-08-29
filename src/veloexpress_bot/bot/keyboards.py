from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_menu_keyboard(*, is_admin: bool) -> InlineKeyboardMarkup | None:
    if not is_admin:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            # Top row makes polls; the monitor is the live view, so it stands alone.
            [
                InlineKeyboardButton(
                    text="📋 Weekend",
                    callback_data="menu:weekend_plan",
                ),
                InlineKeyboardButton(
                    text="➕ Extra lift day",
                    callback_data="menu:extra_day",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Booking monitor",
                    callback_data="menu:booking_monitor",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Service defaults",
                    callback_data="menu:service_defaults",
                ),
            ],
        ]
    )
