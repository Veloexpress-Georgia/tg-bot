from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

# /start only. Everything else is a menu button, so the command list stays a
# single door in rather than a second, drifting copy of the menu. The
# /create_lift_poll handler survives as an unlisted shortcut for muscle memory.
BOT_COMMANDS = (BotCommand(command="start", description="🚐 Open menu"),)


async def register_bot_commands(bot: Bot) -> None:
    # Admin controls only work in private chat, so scope suggestions there and
    # clear the default scope groups fall back to.
    await bot.set_my_commands(list(BOT_COMMANDS), scope=BotCommandScopeAllPrivateChats())
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
