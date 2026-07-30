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
    # Poll-making actions share the top row; the live view stands on its own.
    assert [[button.callback_data for button in row] for row in admin_keyboard.inline_keyboard] == [
        ["menu:weekend_plan", "menu:extra_day"],
        ["menu:booking_monitor"],
        ["menu:cancel"],
    ]
    assert start_menu_keyboard(is_admin=False) is None
