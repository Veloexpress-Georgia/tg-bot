from veloexpress_bot.bot.keyboards import start_menu_keyboard


def test_start_menu_exposes_admin_actions_only_to_admins() -> None:
    admin_keyboard = start_menu_keyboard(is_admin=True)

    assert admin_keyboard is not None
    # No Close: /start replaces its own card, so there is nothing to dismiss.
    assert [
        [(button.callback_data, button.text) for button in row]
        for row in admin_keyboard.inline_keyboard
    ] == [
        [("menu:weekend_plan", "📋 Weekend"), ("menu:extra_day", "➕ Extra lift day")],
        [("menu:booking_monitor", "📊 Booking monitor")],
    ]
    assert start_menu_keyboard(is_admin=False) is None
