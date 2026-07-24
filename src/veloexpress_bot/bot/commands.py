from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

BOT_COMMANDS = (
    BotCommand(command="start", description="🚐 Open menu"),
    BotCommand(command="create_lift_poll", description="📋 Plan weekend polls"),
)


async def register_bot_commands(bot: Bot) -> None:
    # Admin controls only work in private chat, so scope suggestions there and
    # clear the default scope groups fall back to.
    await bot.set_my_commands(list(BOT_COMMANDS), scope=BotCommandScopeAllPrivateChats())
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
