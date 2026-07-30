from veloexpress_bot.bot.commands import BOT_COMMANDS


def test_only_start_is_suggested_so_the_menu_is_the_single_entry_point() -> None:
    commands = {command.command: command.description for command in BOT_COMMANDS}

    assert commands == {"start": "🚐 Open menu"}
