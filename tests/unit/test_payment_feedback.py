"""A payment report must be visible and must not promise an automatic payout."""

from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery

from veloexpress_bot.bot.handlers import handle_payment_button
from veloexpress_bot.payments.details import bank_details_text
from veloexpress_bot.payments.myday import (
    RiderCardView,
    RiderDayView,
    RiderLiftRow,
    render_rider_card,
)
from veloexpress_core.payments import ALREADY_SETTLED_TEXT, WAITLIST_WARNING_TEXT, ClaimOutcome


@pytest.mark.parametrize("notice", ["Recorded 60 GEL.", ALREADY_SETTLED_TEXT])
async def test_group_payment_result_requires_visible_acknowledgement(notice: str) -> None:
    callback = SimpleNamespace(
        data="pay:paid:20261004",
        from_user=SimpleNamespace(id=123, username="rider", full_name="Rider"),
        answer=AsyncMock(),
    )
    payments = AsyncMock()
    payments.claim.return_value = ClaimOutcome(notice)

    await handle_payment_button(cast(CallbackQuery, callback), payments)

    callback.answer.assert_awaited_once_with(notice, show_alert=True)


def test_waitlist_warning_does_not_promise_an_automatic_refund() -> None:
    assert "you get a refund" not in WAITLIST_WARNING_TEXT
    assert "Misho" in WAITLIST_WARNING_TEXT
    assert len(WAITLIST_WARNING_TEXT) <= 200


def test_surplus_is_not_presented_as_a_promised_payout() -> None:
    draft = render_rider_card(
        RiderCardView(
            days=(
                RiderDayView(
                    service_date=date(2026, 10, 4),
                    price_gel=20,
                    rows=(RiderLiftRow("8:30"),),
                    due_now_gel=60,
                    due_all_gel=60,
                    paid_gel=80,
                ),
            ),
            price_gel=20,
        )
    )
    assert "20 GEL above current bookings" in draft.text
    assert "to come back" not in draft.text
    assert "Misho" in draft.text


def test_money_already_reported_is_visible_even_when_all_seats_are_waitlisted() -> None:
    draft = render_rider_card(
        RiderCardView(
            days=(
                RiderDayView(
                    service_date=date(2026, 10, 4),
                    price_gel=20,
                    rows=(RiderLiftRow("8:30", waitlist_position=1),),
                    paid_gel=20,
                ),
            ),
            price_gel=20,
        )
    )
    assert "Paid 20 GEL" in draft.text
    assert "Misho" in draft.text


def test_bank_details_explain_how_to_report_payment_for_another_rider() -> None:
    text = bank_details_text()
    assert "another rider" in text
    assert "their day" in text
    assert "Misho" in text
