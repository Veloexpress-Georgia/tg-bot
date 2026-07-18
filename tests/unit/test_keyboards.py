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
        cancelled_lift_times=("15:30",),
    )

    assert keyboard.inline_keyboard[1][0].text == "▶️ First · 8:30"
    assert keyboard.inline_keyboard[1][0].callback_data == "view:first"
    assert keyboard.inline_keyboard[1][1].text == "⏹ Last · 13:30"
    assert keyboard.inline_keyboard[1][1].callback_data == "view:last"
    assert keyboard.inline_keyboard[-1][1].text == "✖️ Close setup"
    assert all(
        button.callback_data != "first:toggle" for row in keyboard.inline_keyboard for button in row
    )


def test_setup_keyboard_main_view_supports_full_range() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16),),
        selected_service_dates=(date(2026, 5, 16),),
        cancelled_lift_times=(),
    )

    assert keyboard.inline_keyboard[1][0].text == "▶️ First · 8:30"
    assert keyboard.inline_keyboard[1][1].text == "⏹ Last · 15:30"


def test_setup_keyboard_first_lift_picker_contains_all_times_and_back() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        cancelled_lift_times=("15:30",),
        view="first",
    )

    assert [button.text for button in keyboard.inline_keyboard[0]] == [
        "✅ 8:30",
        "10:00",
        "11:45",
    ]
    assert [button.text for button in keyboard.inline_keyboard[1]] == [
        "13:30",
        "15:30",
    ]
    assert keyboard.inline_keyboard[0][1].callback_data == "range:first:10:00"
    assert keyboard.inline_keyboard[-1][0].text == "⬅️ Back"
    assert keyboard.inline_keyboard[-1][0].callback_data == "view:main"
    assert keyboard.inline_keyboard[-1][1].text == "✖️ Close setup"


def test_setup_keyboard_last_lift_picker_marks_current_boundary() -> None:
    keyboard = setup_keyboard(
        service_dates=(date(2026, 5, 16),),
        selected_service_dates=(date(2026, 5, 16),),
        cancelled_lift_times=("15:30",),
        view="last",
    )

    assert keyboard.inline_keyboard[1][0].text == "✅ 13:30"
    assert keyboard.inline_keyboard[1][0].callback_data == "range:last:13:30"


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
    assert admin_keyboard.inline_keyboard[0][1].text == "📊 Booking monitor"
    assert admin_keyboard.inline_keyboard[0][1].callback_data == "menu:booking_monitor"
    assert admin_keyboard.inline_keyboard[1][0].text == "✖️ Close"
    assert admin_keyboard.inline_keyboard[1][0].callback_data == "menu:cancel"
    assert start_menu_keyboard(is_admin=False) is None
