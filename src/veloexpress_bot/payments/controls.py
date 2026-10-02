from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.payments.copy import CASH_BUTTON, PAID_BUTTON, UNDO_BUTTON


def payment_keyboard(
    service_date: date, *, paid_url: str = "", cash_url: str = ""
) -> InlineKeyboardMarkup:
    encoded = service_date.strftime("%Y%m%d")
    linked = bool(paid_url and cash_url)
    paid = (
        InlineKeyboardButton(text=PAID_BUTTON, url=paid_url)
        if linked
        else InlineKeyboardButton(text=PAID_BUTTON, callback_data=f"pay:paid:{encoded}")
    )
    cash = (
        InlineKeyboardButton(text=CASH_BUTTON, url=cash_url)
        if linked
        else InlineKeyboardButton(text=CASH_BUTTON, callback_data=f"pay:cash:{encoded}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [paid],
            [cash, InlineKeyboardButton(text=UNDO_BUTTON, callback_data=f"pay:undo:{encoded}")],
        ]
    )
