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
# Guests have no vote of their own, so they need a button. There is deliberately no
# "one fewer seat": paying for fewer laps than you booked leaves a seat you still
# occupy, which is the phantom booking the group keeps tripping over. Ride less and
# the honest fix is to change your poll answer, which frees the seat too. Undo
# retracts the whole payment when something needs starting over.
GUEST_BUTTON = "➕ Guest"
UNDO_BUTTON = "↩️ Undo"


@dataclass(frozen=True)
class RiderPayment:
    label: str
    seats: int
    amount_gel: int


@dataclass(frozen=True)
class OutstandingRider:
    telegram_user_id: int
    label: str


@dataclass(frozen=True)
class PaymentsBoardView:
    service_date: date
    running_lift_times: tuple[str, ...]
    price_gel: int
    payments: tuple[RiderPayment, ...]
    outstanding: tuple[OutstandingRider, ...] = ()
    deadline_time: str = "20:00"
    cancelled: bool = False


@dataclass(frozen=True)
class RefundRow:
    label: str
    telegram_user_id: int
    seats: int
    amount_gel: int
    remaining_lift_times: tuple[str, ...]


def render_cancellation_report(
    *,
    service_date: date,
    cancelled_lift_time: str | None,
    rows: tuple[RefundRow, ...],
) -> str | None:
    """Tell the admin who paid for something that just stopped running.

    Returns None when nobody paid, so a quiet cancellation stays quiet.
    """
    if not rows:
        return None
    what = f"{cancelled_lift_time} · " if cancelled_lift_time else ""
    lines = [f"💸 {what}{_long_day_label(service_date)} cancelled — who paid:", ""]
    refund_due = 0
    for row in rows:
        seats = f" · {row.seats} seats" if row.seats > 1 else ""
        mention = f'<a href="tg://user?id={row.telegram_user_id}">{html.escape(row.label)}</a>'
        if row.remaining_lift_times:
            # Payment covers the day, so another lift still earns what they paid.
            still = ", ".join(row.remaining_lift_times)
            lines.append(f"{mention} — {row.amount_gel} GEL{seats} · still on {still}")
            continue
        refund_due += row.amount_gel
        if cancelled_lift_time is None:
            # The header already said the whole day is gone; every row is a refund.
            lines.append(f"{mention} — {row.amount_gel} GEL{seats}")
        else:
            lines.append(f"{mention} — {row.amount_gel} GEL{seats} · nothing left, refund")
    total = sum(row.amount_gel for row in rows)
    lines.append("")
    if refund_due and refund_due != total:
        lines.append(f"Refund {refund_due} GEL of {total} GEL paid.")
    else:
        lines.append(f"Refund {total} GEL.")
    return "\n".join(lines)


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
        f"{view.price_gel} GEL per seat · pay by {view.deadline_time}",
    ]
    lines.append("")
    lines.append("Bringing someone? ➕ Guest adds a seat.")
    if view.payments:
        lines.extend(("", "Paid:"))
        lines.extend(_payment_line(payment) for payment in view.payments)
    if view.outstanding:
        # Tags rather than a bare count. The board is edited in place, and a Telegram
        # edit sends no notification, so this shows who still owes without nagging.
        lines.extend(("", "Waiting on:", " ".join(_mention(rider) for rider in view.outstanding)))

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
            [InlineKeyboardButton(text=UNDO_BUTTON, callback_data=f"pay:undo:{encoded}")],
        ]
    )


def _mention(rider: OutstandingRider) -> str:
    return f'<a href="tg://user?id={rider.telegram_user_id}">{html.escape(rider.label)}</a>'


def _payment_line(payment: RiderPayment) -> str:
    # One state only. Whether the rider claimed it or an admin recorded a cash
    # payment is bookkeeping, not something the group needs to read.
    seats = f" · {payment.seats} seats" if payment.seats > 1 else ""
    return f"✓ {payment.label} — {payment.amount_gel} GEL{seats}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
