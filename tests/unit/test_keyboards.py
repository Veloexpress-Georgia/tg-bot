from datetime import date

from veloexpress_bot.bot.keyboards import setup_keyboard, start_menu_keyboard


def test_setup_keyboard_includes_day_toggles() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 17),),
        cancelled_lift_times=(),
    )

    first_button = keyboard.inline_keyboard[0][0]
    second_button = keyboard.inline_keyboard[0][1]

    assert first_button.text == "🚫 Sat 16.05"
    assert first_button.callback_data == "day:toggle:2026-05-16"
    assert second_button.text == "✅ Sun 17.05"
    assert second_button.callback_data == "day:toggle:2026-05-17"


def test_setup_keyboard_main_view_summarizes_times() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        cancelled_lift_times=("13:30",),
    )

    assert keyboard.inline_keyboard[1][0].text == "🕓 4 times enabled"
    assert keyboard.inline_keyboard[1][0].callback_data == "view:times"
    assert all(
        button.callback_data != "first:toggle" for row in keyboard.inline_keyboard for button in row
    )


def test_setup_keyboard_summary_counts_all_enabled_lifts() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16),),
        selected_service_dates=(date(2026, 5, 16),),
        cancelled_lift_times=(),
    )

    assert keyboard.inline_keyboard[1][0].text == "🕓 All 5 times enabled"


def test_setup_keyboard_times_view_contains_lift_toggles_and_back() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        cancelled_lift_times=("13:30",),
        view="times",
    )

    assert [button.text for button in keyboard.inline_keyboard[0]] == [
        "✅ 8:30",
        "✅ 10:00",
    ]
    assert [button.text for button in keyboard.inline_keyboard[1]] == [
        "✅ 11:45",
        "🚫 13:30",
    ]
    assert [button.text for button in keyboard.inline_keyboard[2]] == [
        "✅ 15:30 · extra",
    ]
    assert keyboard.inline_keyboard[-1][0].text == "⬅️ Back"
    assert keyboard.inline_keyboard[-1][0].callback_data == "view:main"


def test_setup_keyboard_marks_additional_time_as_disabled_by_default() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16),),
        selected_service_dates=(date(2026, 5, 16),),
        cancelled_lift_times=("15:30",),
        view="times",
    )

    assert keyboard.inline_keyboard[2][0].text == "🚫 15:30 · extra"


def test_setup_keyboard_uses_explicit_recreate_button_without_force_duplicate() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16),),
        selected_service_dates=(date(2026, 5, 16),),
        cancelled_lift_times=(),
        allow_recreate=True,
    )

    create_button = keyboard.inline_keyboard[-1][0]

    assert create_button.text == "♻️ Recreate polls"
    assert create_button.callback_data == "poll:recreate_confirm"


def test_start_menu_exposes_admin_actions_only_to_admins() -> None:
    admin_keyboard = start_menu_keyboard(is_admin=True)

    assert admin_keyboard is not None
    assert admin_keyboard.inline_keyboard[0][0].text == "🚐 Create lift polls"
    assert admin_keyboard.inline_keyboard[0][0].callback_data == "menu:create_lift_poll"
    assert admin_keyboard.inline_keyboard[0][1].text == "✖️ Cancel"
    assert admin_keyboard.inline_keyboard[0][1].callback_data == "menu:cancel"
    assert start_menu_keyboard(is_admin=False) is None
