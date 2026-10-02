"""Cash on site must not inherit the previous-evening transfer deadline."""

from datetime import date

import pytest

from veloexpress_bot.payments.details import bank_details_text
from veloexpress_bot.payments.myday import (
    RiderCardView,
    RiderDayView,
    RiderLiftRow,
    render_rider_card,
)
from veloexpress_bot.payments.render import (
    PaymentsBoardView,
    UnpaidLift,
    render_payments_board,
    render_unpaid_deadline_notice,
)
from veloexpress_bot.polls.defaults import PaymentTerms, StartLocation
from veloexpress_bot.polls.liftsignals import (
    LiftEvent,
    LiftSignal,
    render_deadline_reminder,
    render_lift_signal_notice,
)
from veloexpress_bot.polls.render import render_poll_notice

DAY = date(2026, 7, 18)
TERMS = PaymentTerms(price_gel=20, deadline_time="19:30")


@pytest.mark.parametrize(
    "text",
    [
        "\n".join(TERMS.rules()),
        "\n".join(TERMS.pay_now()),
        render_poll_notice(StartLocation.VAKE, terms=TERMS),
        bank_details_text(),
        render_payments_board(
            PaymentsBoardView(DAY, ("8:30",), 20, (), deadline_time="19:30")
        ).text,
        render_rider_card(
            RiderCardView(
                (RiderDayView(DAY, 20, (RiderLiftRow("8:30"),), due_now_gel=20, due_all_gel=20),),
                20,
            )
        ).text,
        render_deadline_reminder(DAY, (LiftSignal(DAY, "8:30", 5),), terms=TERMS),
        render_lift_signal_notice(
            "confirmed", terms=TERMS, events=(LiftEvent("confirmed", DAY, "8:30", 5),)
        ),
        render_unpaid_deadline_notice(
            service_date=DAY, lifts=(UnpaidLift("8:30", 2),), riders=(), minimum=5
        ),
    ],
)
def test_payment_messages_separate_cash_on_site_from_transfer_deadline(text: str) -> None:
    assert "cash" in text.lower()
    assert "on site" in text.lower()
    assert "pay by 19:30" not in text.lower()
    assert "book and pay by" not in text.lower()


def test_cash_choice_is_a_promise_and_misho_records_actual_receipt() -> None:
    text = bank_details_text().lower()
    assert "promise" in text
    assert "misho records cash after collecting" in text
