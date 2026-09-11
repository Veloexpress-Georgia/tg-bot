from datetime import date

import pytest
from aiogram import Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, ErrorEvent, Update

from veloexpress_bot.bot.handlers import (
    _should_cleanup_bot_pin_notice,
    _should_send_recreate_report,
    _split_callback,
    report_expired_callback_query,
    router,
)
from veloexpress_bot.config import Settings


def _error_event(message: str, *, data: str | None = "menu:weekend_plan") -> ErrorEvent:
    callback = CallbackQuery.model_construct(data=data) if data is not None else None
    return ErrorEvent.model_construct(
        update=Update.model_construct(update_id=7, callback_query=callback),
        exception=TelegramBadRequest(
            method=AnswerCallbackQuery(callback_query_id="stale"),
            message=message,
        ),
    )


def test_error_handler_is_registered() -> None:
    assert [handler.callback.__name__ for handler in router.errors.handlers] == [
        "report_expired_callback_query"
    ]


async def test_a_dead_callback_ack_is_logged_instead_of_raised() -> None:
    # The handler already finished its work; only the acknowledgement was late.
    event = _error_event(
        "Bad Request: query is too old and response timeout expired or query ID is invalid"
    )

    assert await report_expired_callback_query(event) is True


async def test_other_telegram_bad_requests_still_surface() -> None:
    assert await report_expired_callback_query(_error_event("Bad Request: chat not found")) is (
        UNHANDLED
    )


async def test_a_dead_ack_without_a_callback_query_does_not_crash_the_logging() -> None:
    event = _error_event("Bad Request: query is too old", data=None)

    assert await report_expired_callback_query(event) is True


async def test_the_dispatcher_actually_reaches_the_error_handler() -> None:
    """The suppression only works if aiogram routes the error into this router.

    aiogram re-raises whenever an errors observer answers UNHANDLED, so a wiring
    mistake here would put the stack trace straight back in the log.
    """
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        stale = await dispatcher.propagate_event(
            update_type="error",
            event=_error_event("Bad Request: query is too old"),
        )
        other = await dispatcher.propagate_event(
            update_type="error",
            event=_error_event("Bad Request: chat not found", data=None),
        )
    finally:
        # The router is a module-level singleton; leave it unparented so the app
        # builder can still include it in another test.
        dispatcher.sub_routers.remove(router)
        router._parent_router = None

    assert stale is True
    assert other is UNHANDLED


def test_core_callback_handlers_are_registered() -> None:
    names = {handler.callback.__name__ for handler in router.callback_query.handlers}

    assert {
        "open_admin_menu",
        "open_booking_monitor",
        "select_booking_monitor_day",
        "open_day_from_retired_card",
        "open_all_riders",
        "open_lift_history",
        "open_lift_day_audit",
        "open_lift_trend",
        "adjust_manual_booking",
        "open_lift_card",
        "handle_lift_cancellation",
        "open_weekend_plan",
        "handle_weekend_plan_card",
        "open_extra_day",
        "handle_extra_day_card",
        "handle_poll_schedule_card",
        "handle_payment_button",
    } <= names
    # Payments are the riders' own business now: the monitor reads, never records.
    assert "toggle_rider_payment" not in names


def test_the_payments_topic_watcher_yields_to_narrower_message_handlers() -> None:
    names = [handler.callback.__name__ for handler in router.message.handlers]

    # It matches any forum-topic message, and the first match ends propagation.
    assert names.index("watch_payments_topic") > names.index("cleanup_bot_pin_notice")
    assert names.index("watch_payments_topic") > names.index("show_start_menu")


def test_split_callback_keeps_colons_in_lift_time_value() -> None:
    assert _split_callback("plan:first:15:30") == ("first", "15:30")
    assert _split_callback("extra:last:8:30") == ("last", "8:30")
    assert _split_callback("plan:view:recreate") == ("view", "recreate")
    assert _split_callback("plan:post") == ("post", "")
    assert _split_callback(None) == ("", "")


def test_recreate_report_is_only_sent_when_votes_are_present() -> None:
    assert (
        _should_send_recreate_report(
            "Recreated existing polls.\n\nTracked votes before recreate:\n\n"
            "2026-05-23:\n- No tracked votes; the poll may predate vote tracking.\n\n"
            "No tracked rider votes were found."
        )
        is False
    )
    assert (
        _should_send_recreate_report(
            "Recreated existing polls.\n\nTracked votes before recreate:\n\n"
            "2026-05-23:\n- 🚲 10:00: @stas"
        )
        is True
    )


def test_only_bot_created_pin_notices_in_target_topic_are_cleaned() -> None:
    settings = Settings.model_construct(
        telegram_target_chat_id=-100123,
        telegram_target_thread_id=7,
    )

    assert _should_cleanup_bot_pin_notice(
        actor_user_id=42,
        bot_user_id=42,
        chat_id=-100123,
        thread_id=7,
        settings=settings,
    )
    assert not _should_cleanup_bot_pin_notice(
        actor_user_id=10,
        bot_user_id=42,
        chat_id=-100123,
        thread_id=7,
        settings=settings,
    )
    assert not _should_cleanup_bot_pin_notice(
        actor_user_id=42,
        bot_user_id=42,
        chat_id=-100123,
        thread_id=8,
        settings=settings,
    )


@pytest.mark.parametrize(
    "data",
    [
        "guest:unknown:20260912:0830",
        "guest:add:20260912",
        "guest:sub:20260912:2960",
        "guest:add:20260912:nope",
        "guest:pay:20260912:extra",
    ],
)
async def test_unknown_guest_action_cannot_change_seats(data: str) -> None:
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock, Mock

    from veloexpress_bot.bot.handlers import handle_guest_form

    callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=123), answer=AsyncMock())
    payments = Mock()
    await handle_guest_form(cast(CallbackQuery, callback), payments)
    assert payments.mock_calls == []
    callback.answer.assert_awaited_once()


@pytest.mark.parametrize(
    "data, allowed",
    [
        ("rstats:30d:0", True),
        ("rstats:30d:999:0", False),
        ("astats:30d:999:0", False),
        ("stats:30d:0", False),
    ],
)
async def test_personal_statistics_cannot_select_another_rider(
    monkeypatch, data: str, allowed: bool
) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    from tests.unit.test_payments_service import settings

    from veloexpress_bot.bot import handlers
    from veloexpress_bot.payments.myday import MyDayDraft

    message = SimpleNamespace(chat=SimpleNamespace(type="private", id=100))
    callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=100), answer=AsyncMock())
    service = AsyncMock()
    monkeypatch.setattr(handlers, "_accessible_message", lambda _: message)
    monkeypatch.setattr(handlers, "_edit_card", AsyncMock())
    monkeypatch.setattr(
        handlers, "render_statistics", lambda *args, **kwargs: MyDayDraft("test", None)
    )
    await handlers.handle_statistics(
        cast(CallbackQuery, callback), settings(), AsyncMock(), service
    )
    if allowed:
        service.read.assert_awaited_once_with(period="30d", user_id=100)
    else:
        service.read.assert_not_awaited()


def test_start_prefers_today_over_the_next_lift_day() -> None:
    """On a lift morning the admin wants today, whatever tab they left open."""
    from veloexpress_bot.bookings.render import BookingMonitorDay
    from veloexpress_bot.bot.handlers import _preferred_day

    today = date(2026, 9, 12)
    days = (
        BookingMonitorDay(service_date=date(2026, 9, 6), lifts=(), past=True),
        BookingMonitorDay(service_date=today, lifts=()),
        BookingMonitorDay(service_date=date(2026, 9, 13), lifts=()),
    )

    assert _preferred_day(days, today=today) == today
    # Midweek: the nearest day still ahead, never a finished one.
    assert _preferred_day(days[:1] + days[2:], today=today) == date(2026, 9, 13)
    # Only finished days left, so there is nothing live to open on.
    assert _preferred_day(days[:1], today=today) is None
    assert _preferred_day((), today=today) is None


@pytest.mark.parametrize(
    "data, handler",
    [
        # Cards already sitting in admin chats carry the retired callbacks.
        ("mon:manage:20260912", "open_day_from_retired_card"),
        ("mon:back:20260912", "open_day_from_retired_card"),
        ("mon:managelift:20260912:0830", "open_lift_card"),
        ("mon:info:20260912:0830", "open_lift_card"),
        ("mon:lift:20260912:0830", "open_lift_card"),
        ("mon:settings", "open_admin_menu"),
        ("mon:menu", "open_admin_menu"),
    ],
)
def test_every_retired_callback_still_lands_somewhere(data: str, handler: str) -> None:
    """A button that spins forever reads as a dead bot, so nothing may fall through."""
    matched = [
        registered.callback.__name__
        for registered in router.callback_query.handlers
        if _matches(registered, data)
    ]

    assert matched[:1] == [handler], f"{data} → {matched}"


def _matches(registered, data: str) -> bool:  # type: ignore[no-untyped-def]
    callback = CallbackQuery.model_construct(data=data)
    return all(
        bool(callback_filter.callback(callback)) for callback_filter in registered.filters or ()
    )


async def test_admin_start_opens_live_monitor_and_cleans_previous_menu(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.unit.test_payments_service import settings

    from veloexpress_bot.bot.handlers import show_start_menu

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        message_id=99,
        answer=AsyncMock(),
    )
    state = AsyncMock()
    state.get_data.return_value = {"menu_message_ids": [55]}
    service = AsyncMock()
    service.status_days.return_value = [SimpleNamespace(service_date=date(2099, 1, 1), past=False)]
    service.open_booking_monitor.return_value = 66
    await show_start_menu(message, state, settings(), service)
    message.answer.assert_not_awaited()
    service.open_booking_monitor.assert_awaited_once_with(
        admin_user_id=1, private_chat_id=1, selected_service_date=date(2099, 1, 1)
    )
    state.update_data.assert_awaited_once_with(menu_message_ids=[66])
    service.cleanup_setup_messages.assert_awaited_once_with(chat_id=1, message_ids=(99, 55))
