from datetime import date

from veloexpress_bot.bot.handlers import (
    _completion_text,
    _setup_message_ids_for_cleanup,
    _setup_text,
    _should_notify_completion,
)
from veloexpress_bot.polls.defaults import StartLocation


def test_completion_text_surfaces_pin_and_cleanup_outcomes() -> None:
    expected = (
        "Polls created. Message IDs: 42, 43; not pinned; "
        "cleaned 1 setup message(s), failed to clean 2."
    )
    assert (
        _completion_text(
            message_ids=(42, 43),
            pinned=False,
            deleted_count=1,
            failed_count=2,
        )
        == expected
    )


def test_completion_notice_is_only_needed_for_non_ideal_outcomes() -> None:
    assert _should_notify_completion(pinned=True, failed_count=0) is False
    assert _should_notify_completion(pinned=False, failed_count=0) is True
    assert _should_notify_completion(pinned=True, failed_count=1) is True


def test_private_chat_cleanup_excludes_admin_command_message() -> None:
    assert _setup_message_ids_for_cleanup(
        chat_type="private",
        command_message_id=10,
        setup_message_id=11,
    ) == (11,)


def test_group_chat_cleanup_includes_admin_command_message() -> None:
    assert _setup_message_ids_for_cleanup(
        chat_type="supergroup",
        command_message_id=10,
        setup_message_id=11,
    ) == (10, 11)


def test_setup_text_only_lists_selected_days() -> None:
    text = _setup_text(
        (date(2026, 5, 16), date(2026, 5, 17)),
        StartLocation.JUSTICE_HALL,
        (),
        selected_service_dates=(date(2026, 5, 17),),
    )

    assert "Sunday 17.05.2026" in text
    assert "Saturday 16.05.2026" not in text
