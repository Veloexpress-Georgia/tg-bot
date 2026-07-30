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
        "open_booking_monitor",
        "select_booking_monitor_day",
        "adjust_manual_booking",
        "open_lift_detail",
        "handle_lift_cancellation",
        "open_weekend_plan",
        "handle_weekend_plan_card",
        "open_extra_day",
        "handle_extra_day_card",
        "handle_poll_schedule_card",
        "handle_payment_button",
        "toggle_rider_payment",
    } <= names


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
