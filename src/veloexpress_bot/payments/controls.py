from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def payment_keyboard(
    service_date: date, *, paid_url: str = "", cash_url: str = ""
) -> InlineKeyboardMarkup:
    encoded = service_date.strftime("%Y%m%d")
    linked = bool(paid_url and cash_url)
    paid = (
        InlineKeyboardButton(text="💸 I paid", url=paid_url)
        if linked
        else InlineKeyboardButton(text="💸 I paid", callback_data=f"pay:paid:{encoded}")
    )
    cash = (
        InlineKeyboardButton(text="💵 Cash", url=cash_url)
        if linked
        else InlineKeyboardButton(text="💵 Cash", callback_data=f"pay:cash:{encoded}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [paid],
            [cash, InlineKeyboardButton(text="↩️ Undo", callback_data=f"pay:undo:{encoded}")],
        ]
    )
