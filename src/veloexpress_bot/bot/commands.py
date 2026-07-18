from aiogram import Bot
from aiogram.types import BotCommand

BOT_COMMANDS = (
    BotCommand(command="start", description="🚐 Open menu"),
    BotCommand(command="create_lift_poll", description="📋 Plan weekend polls"),
)


async def register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(list(BOT_COMMANDS))
