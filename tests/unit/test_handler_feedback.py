from veloexpress_bot.bot.handlers import (
    _menu_message_ids_for_cleanup,
    _should_cleanup_bot_pin_notice,
    _should_send_recreate_report,
    _split_callback,
    router,
)
from veloexpress_bot.config import Settings


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
        "open_poll_schedule",
        "handle_poll_schedule_card",
    } <= names


def test_split_callback_keeps_colons_in_lift_time_value() -> None:
    assert _split_callback("plan:first:15:30") == ("first", "15:30")
    assert _split_callback("extra:last:8:30") == ("last", "8:30")
    assert _split_callback("plan:view:recreate") == ("view", "recreate")
    assert _split_callback("plan:post") == ("post", "")
    assert _split_callback(None) == ("", "")


def test_menu_cleanup_includes_admin_command_message() -> None:
    assert _menu_message_ids_for_cleanup(
        command_message_id=10,
        menu_message_id=11,
    ) == (10, 11)


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
