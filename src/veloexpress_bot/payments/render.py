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
# Cash gets its own button rather than a rule telling riders to press the transfer
# one anyway. It also tells Misho which lines to look for in his bank statement and
# which not to, which he cannot otherwise know.
CASH_BUTTON = "💵 Cash"
# Guests open a form in the bot's private chat: only a form can say how many seats
# a lift has left, and only a private chat has room for one row per lift. This is
# the one button that navigates away, so it stays the rarer action while paying
# remains a single in-group tap.
GUEST_BUTTON = "👤 Guests"
UNDO_BUTTON = "↩️ Undo"


@dataclass(frozen=True)
class RiderPayment:
    label: str
    seats: int
    amount_gel: int
    due_gel: int = 0
    cash: bool = False

    @property
    def gap_gel(self) -> int:
        """Positive when they owe more, negative when they have overpaid."""
        return self.due_gel - self.amount_gel


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
    guests_url: str = ""
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
    lines.append("💸 transfer · 💵 cash · 👤 Guests opens a form in the bot")
    if view.payments:
        lines.extend(("", "Paid:"))
        lines.extend(_payment_line(payment) for payment in view.payments)
    if view.outstanding:
        # Tags rather than a bare count. The board is edited in place, and a Telegram
        # edit sends no notification, so this shows who still owes without nagging.
        lines.extend(("", "Waiting on:", " ".join(_mention(rider) for rider in view.outstanding)))

    return PaymentsBoardDraft(
        text="\n".join(lines),
        reply_markup=_keyboard(view.service_date, guests_url=view.guests_url),
    )


def render_payment_post(
    *,
    label: str,
    service_date: date,
    amount_gel: int,
    user_id: int,
    guests: int = 0,
    cash: bool = False,
) -> str:
    """The line the bot posts on a rider's behalf, tagged so Misho can see who.

    Deliberately names no lifts. Payment covers the day, and riders may re-vote
    until the deadline, so a receipt listing lifts would go stale the moment they
    changed slots. The live breakdown belongs on the board.
    """
    mention = f'<a href="tg://user?id={user_id}">{html.escape(label)}</a>'
    marker = "💵" if cash else "💸"
    parts = [f"{marker} {mention} — {amount_gel} GEL · {_long_day_label(service_date)}"]
    if guests > 0:
        parts.append(f"+{guests} guest" if guests == 1 else f"+{guests} guests")
    if cash:
        parts.append("cash")
    return " · ".join(parts)


def encode_board_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def decode_board_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _keyboard(service_date: date, *, guests_url: str = "") -> InlineKeyboardMarkup:
    encoded = encode_board_date(service_date)
    second_row = [InlineKeyboardButton(text=UNDO_BUTTON, callback_data=f"pay:undo:{encoded}")]
    if guests_url:
        # A url button, not a callback: it has to open the private chat, and that tap
        # is also a Start, which is how riders with no bot chat get one.
        second_row.insert(0, InlineKeyboardButton(text=GUEST_BUTTON, url=guests_url))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=PAID_BUTTON, callback_data=f"pay:paid:{encoded}"),
                InlineKeyboardButton(text=CASH_BUTTON, callback_data=f"pay:cash:{encoded}"),
            ],
            second_row,
        ]
    )


def _mention(rider: OutstandingRider) -> str:
    return f'<a href="tg://user?id={rider.telegram_user_id}">{html.escape(rider.label)}</a>'


def _payment_line(payment: RiderPayment) -> str:
    # Paid is paid — who recorded it is bookkeeping the group need not read. Cash is
    # the one exception worth naming: it is the line Misho will not find in his bank.
    seats = f" · {payment.seats} seats" if payment.seats > 1 else ""
    cash = " · cash" if payment.cash else ""
    # Re-voting is free until the deadline, so what a rider owes moves after they
    # pay. Showing the gap keeps the paid figure honest instead of restating it.
    gap = ""
    if payment.gap_gel > 0:
        gap = f" · +{payment.gap_gel} due"
    elif payment.gap_gel < 0:
        gap = f" · {-payment.gap_gel} back"
    return f"✓ {payment.label} — {payment.amount_gel} GEL{seats}{cash}{gap}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
