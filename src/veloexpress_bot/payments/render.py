from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardMarkup

from veloexpress_bot.payments.controls import payment_keyboard
from veloexpress_bot.payments.details import bank_details_text
from veloexpress_bot.polls.render import EN_SHORT_MONTHS, SHORT_DAY_LABELS

PAYMENTS_PARSE_MODE = "HTML"

# Riders tap these in the group, so the labels cannot be personal: one shared
# keyboard serves everyone and the bot answers each tap with a private toast.
PAID_BUTTON = "💸 I paid"
# Cash gets its own button rather than a rule telling riders to press the transfer
# one anyway. It also tells Misho which lines to look for in his bank statement and
# which not to, which he cannot otherwise know.
CASH_BUTTON = "💵 Cash"
UNDO_BUTTON = "↩️ Undo"


@dataclass(frozen=True)
class RiderPayment:
    label: str
    seats: int
    amount_gel: int
    due_gel: int = 0
    cash: bool = False
    cash_gel: int = 0
    # True when the rider still holds a booking that has not filled. Paying ahead and
    # overpaying are the same arithmetic but opposite meanings.
    prepaid: bool = False

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
    # Deep links for the two payment buttons. Empty when the bot's username is
    # unknown, in which case they fall back to in-group callbacks.
    paid_url: str = ""
    cash_url: str = ""
    deadline_time: str = "20:00"
    cancelled: bool = False


@dataclass(frozen=True)
class RefundRow:
    label: str
    telegram_user_id: int
    seats: int
    amount_gel: int
    remaining_lift_times: tuple[str, ...]
    # What is actually owed back. Not the whole payment whenever the rider still
    # holds seats elsewhere on the day: their money follows them there.
    refund_gel: int = 0


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
    heading = f"💸 {what}{_long_day_label(service_date)} cancelled"
    refunds = tuple(row for row in rows if row.refund_gel > 0)
    if not refunds:
        return f"{heading}\n\nNothing to refund."
    lines = [heading, ""]
    for row in refunds:
        mention = f'<a href="tg://user?id={row.telegram_user_id}">{html.escape(row.label)}</a>'
        lines.extend(
            (
                f"{mention} — {row.refund_gel} GEL back",
                f"Paid {row.amount_gel} · rides {row.amount_gel - row.refund_gel} GEL",
                "",
            )
        )
    lines.extend(
        (
            f"Total to return: {sum(row.refund_gel for row in refunds)} GEL",
            "Cumulative for the day · payouts not tracked.",
        )
    )
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
        f"Payment open: {', '.join(view.running_lift_times)}",
        f"{view.price_gel} GEL per seat · pay by {view.deadline_time}"
        f" ({_long_day_label(view.service_date - timedelta(days=1))})",
    ]
    if view.payments:
        lines.extend(("", "Paid:"))
        lines.extend(_payment_line(payment) for payment in view.payments)
    if view.outstanding:
        # Tags rather than a bare count. The board is edited in place, and a Telegram
        # edit sends no notification, so this shows who still owes without nagging.
        lines.extend(("", "Waiting on:", " ".join(_mention(rider) for rider in view.outstanding)))

    lines.extend(("", bank_details_text()))
    if view.guests_url:
        lines.extend(("", f'👤 <a href="{html.escape(view.guests_url, quote=True)}">Guests</a>'))
    return PaymentsBoardDraft(
        text="\n".join(lines),
        reply_markup=payment_keyboard(
            view.service_date, paid_url=view.paid_url, cash_url=view.cash_url
        ),
    )


@dataclass(frozen=True)
class UnpaidLift:
    lift_time: str
    paid_seats: int


def render_unpaid_deadline_notice(
    *,
    service_date: date,
    lifts: tuple[UnpaidLift, ...],
    riders: tuple[OutstandingRider, ...],
    minimum: int,
) -> str:
    """Booking closed with the trip underfunded — ask, do not cancel.

    Somebody forgetting to tap a button is not a reason to call off a van, so
    this names the shortfall and tags whoever is missing from it. Whether the
    lift actually runs is Misho's decision, and the bot does not pre-empt it.
    """
    lines = [
        f"⏳ {_long_day_label(service_date)} — booking is closed and these lifts "
        "are not paid up yet:",
        "",
    ]
    lines.extend(f"{lift.lift_time} — {lift.paid_seats}/{minimum} paid" for lift in lifts)
    lines.extend(
        (
            "",
            f"A lift is settled once {minimum} seats are paid for, so these are still "
            "open questions — Misho decides.",
            "💵 Already paid in cash? Tap 💵 Cash on the board so it counts.",
        )
    )
    if riders:
        lines.extend(("", " ".join(_mention(rider) for rider in riders)))
    return "\n".join(lines)


def render_payment_post(
    *,
    label: str,
    service_date: date,
    amount_gel: int,
    user_id: int,
    seats: int = 1,
    guests: int = 0,
    cash: bool = False,
    cash_gel: int = 0,
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
        # Seats, not people: one guest riding three lifts is three seats, and
        # "+3 guests" had the group counting three visitors who did not exist.
        parts.append(f"{seats} seats · {guests} for guests")
    if cash:
        parts.append("cash")
    elif cash_gel:
        parts.append(f"{cash_gel} GEL cash")
    return " · ".join(parts)


def encode_board_date(service_date: date) -> str:
    return service_date.strftime("%Y%m%d")


def decode_board_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _mention(rider: OutstandingRider) -> str:
    return f'<a href="tg://user?id={rider.telegram_user_id}">{html.escape(rider.label)}</a>'


def _payment_line(payment: RiderPayment) -> str:
    # Paid is paid — who recorded it is bookkeeping the group need not read. Cash is
    # the one exception worth naming: it is the line Misho will not find in his bank.
    seats = f" · {payment.seats} seats" if payment.seats > 1 else ""
    cash = " · cash" if payment.cash else ""
    if not payment.cash and payment.cash_gel:
        cash = f" · {payment.cash_gel} cash"
    # Re-voting is free until the deadline, so what a rider owes moves after they
    # pay. Showing the gap keeps the paid figure honest instead of restating it.
    gap = ""
    if payment.gap_gel > 0:
        gap = f" · +{payment.gap_gel} due"
    elif payment.gap_gel < 0:
        # "back" would call a deliberate prepayment a mistake.
        gap = f" · {-payment.gap_gel} {'prepaid' if payment.prepaid else 'back'}"
    return f"✓ {html.escape(payment.label)} — {payment.amount_gel} GEL{seats}{cash}{gap}"


def _long_day_label(service_date: date) -> str:
    day = SHORT_DAY_LABELS.get(service_date.weekday(), "Lift day")
    return f"{day}, {service_date.day} {EN_SHORT_MONTHS[service_date.month]}"
