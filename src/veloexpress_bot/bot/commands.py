from aiogram import Bot
from aiogram.types import BotCommand

BOT_COMMANDS = (
    BotCommand(command="help", description="Show bot help"),
    BotCommand(command="create_lift_poll", description="Create a Veloexpress lift poll"),
)


async def register_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(list(BOT_COMMANDS))
