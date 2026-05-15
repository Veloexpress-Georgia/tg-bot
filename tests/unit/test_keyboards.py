from datetime import date

from veloexpress_bot.bot.keyboards import setup_keyboard
from veloexpress_bot.polls.defaults import StartLocation


def test_setup_keyboard_includes_day_toggles() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 17),),
        cancelled_lift_times=(),
        first_lift_location=StartLocation.JUSTICE_HALL,
    )

    first_button = keyboard.inline_keyboard[0][0]
    second_button = keyboard.inline_keyboard[1][0]

    assert first_button.text == "🚫 Saturday 16.05"
    assert first_button.callback_data == "day:toggle:2026-05-16"
    assert second_button.text == "✅ Sunday 17.05"
    assert second_button.callback_data == "day:toggle:2026-05-17"
