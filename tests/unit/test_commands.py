from veloexpress_bot.bot.commands import BOT_COMMANDS


def test_bot_commands_include_start_menu_and_poll_creation() -> None:
    commands = {command.command: command.description for command in BOT_COMMANDS}

    assert commands["start"] == "🚐 Open menu"
    assert commands["create_lift_poll"] == "📊 Create lift polls"
    assert "help" not in commands
