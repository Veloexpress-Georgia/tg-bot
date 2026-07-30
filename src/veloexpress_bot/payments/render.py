from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

PAYMENTS_PARSE_MODE = "HTML"

# Riders tap these in the group, so the labels cannot be personal: one shared
# keyboard serves everyone and the bot answers each tap with a private toast.
PAID_BUTTON = "💸 I paid"
GUEST_BUTTON = "➕ Guest"
FEWER_BUTTON = "➖ Seat"
UNDO_BUTTON = "↩️ Undo"


@dataclass(frozen=True)
class RiderPayment:
    label: str
    seats: int
    amount_gel: int


@dataclass(frozen=True)
class PaymentsBoardView:
    service_date: date
    running_lift_times: tuple[str, ...]
    price_gel: int
    payments: tuple[RiderPayment, ...]
    booked_rider_count: int
    cancelled: bool = False


@dataclass(frozen=True)
class PaymentsBoardDraft:
    text: str
    reply_markup: InlineKeyboardMarkup | None


def render_payments_board(view: PaymentsBoardView) -> PaymentsBoardDraft:
    header = f"💸 Payments · {_long_day_label(view.service_date)}"
    if view.cancelled:
        return PaymentsBoardDraft(text=f"{header}\n\n❌ The day is cancelled.", reply_markup=None)
    if not view.running_lift_times:
        return PaymentsBoardDraft(
            text=f"{header}\n\nNo lift has reached the minimum yet.",
            reply_markup=None,
        )

    lines = [
        header,
        "",
        f"Running: {', '.join(view.running_lift_times)}",
        f"{view.price_gel} GEL per seat",
    ]
    if view.payments:
        lines.extend(("", "Paid:"))
        lines.extend(_payment_line(payment) for payment in view.payments)
    outstanding = view.booked_rider_count - len(view.payments)
    if outstanding > 0:
        # A count, never a name list: this is a nudge, not a public shaming.
        lines.extend(("", f"Waiting on {outstanding} more."))

    return PaymentsBoardDraft(
        text="\n".join(lines),
        reply_markup=_keyboard(view.service_date),
    )


def render_payment_post(
    *,
    label: str,
    service_date: date,
    lift_times: tuple[str, ...],
    seats: int,
    amount_gel: int,
    user_id: int,
) -> str:
    """The line the bot posts on a rider's behalf, tagged so Misho can see who."""
    mention = f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'
    parts = [f"💸 {mention} — {amount_gel} GEL · {_long_day_label(service_date)}"]
    if lift_times:
        parts.append(", ".join(lift_times))
    guests = seats - len(lift_times)
    if guests > 0:
        parts.append(f"+{guests} guest" if guests == 1 else f"+{guests} guests")
    return " · ".join(parts)


def encode_board_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def decode_board_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _keyboard(service_date: date) -> InlineKeyboardMarkup:
    encoded = encode_board_date(service_date)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=PAID_BUTTON, callback_data=f"pay:paid:{encoded}"),
                InlineKeyboardButton(text=GUEST_BUTTON, callback_data=f"pay:guest:{encoded}"),
            ],
            [
                InlineKeyboardButton(text=FEWER_BUTTON, callback_data=f"pay:fewer:{encoded}"),
                InlineKeyboardButton(text=UNDO_BUTTON, callback_data=f"pay:undo:{encoded}"),
            ],
        ]
    )


def _payment_line(payment: RiderPayment) -> str:
    # One state only. Whether the rider claimed it or an admin recorded a cash
    # payment is bookkeeping, not something the group needs to read.
    seats = f" · {payment.seats} seats" if payment.seats > 1 else ""
    return f"✓ {payment.label} — {payment.amount_gel} GEL{seats}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
