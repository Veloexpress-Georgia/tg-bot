from veloexpress_bot.bot.keyboards import start_menu_keyboard


def test_start_menu_exposes_admin_actions_only_to_admins() -> None:
    admin_keyboard = start_menu_keyboard(is_admin=True)

    assert admin_keyboard is not None
    labels = {
        button.callback_data: button.text
        for row in admin_keyboard.inline_keyboard
        for button in row
    }
    assert labels == {
        "menu:weekend_plan": "📋 Weekend",
        "menu:booking_monitor": "📊 Booking monitor",
        "menu:extra_day": "➕ Extra lift day",
        "menu:cancel": "✖️ Close",
    }
    assert start_menu_keyboard(is_admin=False) is None
