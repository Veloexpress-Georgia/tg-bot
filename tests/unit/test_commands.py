from veloexpress_bot.bot.commands import BOT_COMMANDS


def test_bot_commands_include_help_and_poll_creation() -> None:
    commands = {command.command: command.description for command in BOT_COMMANDS}

    assert commands["help"] == "Show bot help"
    assert commands["create_lift_poll"] == "Create a Veloexpress lift poll"
