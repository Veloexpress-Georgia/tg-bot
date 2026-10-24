from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import CallbackQuery, Message

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    LiftRider,
    render_cancel_day_confirmation,
)
from veloexpress_bot.bot.handlers import handle_lift_cancellation
from veloexpress_bot.config import Settings
from veloexpress_bot.payments.render import RefundRow, render_cancellation_report
from veloexpress_core.lifts import PollPostingService
from veloexpress_core.payments import PaymentsService


@pytest.mark.parametrize("whole_day", [True, False])
@pytest.mark.parametrize("has_refund", [True, False])
async def test_cancellation_confirmation_renders_refund_mentions_as_html(
    whole_day: bool, has_refund: bool
) -> None:
    service_date = date(2026, 10, 25)
    lift = BookingLiftStatus(time="8:30", vote_count=1, manual_count=0)
    day = BookingMonitorDay(service_date=service_date, lifts=(lift,))
    preview = render_cancellation_report(
        service_date=service_date,
        cancelled_lift_time=None if whole_day else lift.time,
        rows=(RefundRow("Anna <&>", 123, 1, 20, (), refund_gel=20),) if has_refund else (),
    )
    edit_text = AsyncMock()
    message = cast(
        Message,
        Mock(spec=Message, chat=SimpleNamespace(id=1, type="private"), edit_text=edit_text),
    )
    callback = cast(
        CallbackQuery,
        SimpleNamespace(
            data="mon:cancelday:20261025" if whole_day else "mon:cancel:20261025:0830",
            from_user=SimpleNamespace(id=1),
            message=message,
            answer=AsyncMock(),
        ),
    )
    poll_service = Mock(spec=PollPostingService)
    poll_service.cancel_day_confirmation_view.side_effect = lambda **kwargs: (
        render_cancel_day_confirmation(day, refund_preview=kwargs["refund_preview"])
    )
    poll_service.lift_detail.return_value = (lift, (LiftRider(123, "Anna <&>", paid=True),))
    payments_service = Mock(spec=PaymentsService)
    payments_service.cancellation_preview.return_value = preview

    await handle_lift_cancellation(
        callback,
        Settings.model_construct(telegram_admin_ids=[1]),
        poll_service,
        payments_service,
    )

    edit_text.assert_awaited_once()
    sent = edit_text.await_args
    assert sent is not None
    assert sent.kwargs["parse_mode"] == "HTML"
    if has_refund:
        assert '<a href="tg://user?id=123">Anna &lt;&amp;&gt;</a>' in sent.args[0]
        assert "Total to return: 20 GEL" in sent.args[0]
    else:
        assert "The bot writes the refund list once" in sent.args[0]
    poll_service.cancel_day.assert_not_awaited()
    poll_service.cancel_lift.assert_not_awaited()
    payments_service.cancellation_report.assert_not_awaited()
    payments_service.forget_day.assert_not_awaited()
