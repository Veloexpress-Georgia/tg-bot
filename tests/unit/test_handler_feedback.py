from veloexpress_bot.bot.handlers import (
    _menu_message_ids_for_cleanup,
    _should_cleanup_bot_pin_notice,
    _should_send_recreate_report,
)
from veloexpress_bot.config import Settings


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
