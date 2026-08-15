from asyncio import CancelledError, create_task
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from veloexpress_bot.bot.admin_rights import register_default_admin_rights
from veloexpress_bot.bot.commands import register_bot_commands
from veloexpress_bot.bot.handlers import router
from veloexpress_bot.config import Settings, get_settings
from veloexpress_bot.db.session import check_database, create_session_factory
from veloexpress_bot.health import start_heartbeat_task
from veloexpress_bot.observability import configure_logging
from veloexpress_bot.payments.service import PaymentsService
from veloexpress_bot.polls.autoposter import PollAutoScheduler
from veloexpress_bot.polls.planner import WeekendPlanner
from veloexpress_bot.polls.service import PollPostingService
from veloexpress_bot.telegram.client import AiogramTelegramClient


def build_dispatcher(
    *,
    settings: Settings,
    poll_service: PollPostingService,
    auto_scheduler: PollAutoScheduler,
    planner: WeekendPlanner,
    payments_service: PaymentsService,
) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    dispatcher["settings"] = settings
    dispatcher["poll_service"] = poll_service
    dispatcher["auto_scheduler"] = auto_scheduler
    dispatcher["planner"] = planner
    dispatcher["payments_service"] = payments_service
    return dispatcher


async def run_polling() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telegram_bot_token:
        msg = "TELEGRAM_BOT_TOKEN is required."
        raise RuntimeError(msg)

    heartbeat_task = start_heartbeat_task()
    bot = Bot(token=settings.telegram_bot_token)
    scheduler_task = None
    try:
        session_factory = create_session_factory(settings)
        await check_database(session_factory)
        telegram_client = AiogramTelegramClient(bot)
        # Every "pay" link is a deep link, which needs the bot's own username.
        # Read once from Telegram rather than kept in config, so there is one source.
        bot_username = (await bot.me()).username or ""
        poll_service = PollPostingService(
            settings=settings,
            session_factory=session_factory,
            telegram_client=telegram_client,
            bot_username=bot_username,
        )
        payments_service = PaymentsService(
            settings=settings,
            session_factory=session_factory,
            telegram_client=telegram_client,
            bot_username=bot_username,
        )
        auto_scheduler = PollAutoScheduler(
            settings=settings,
            session_factory=session_factory,
            poll_service=poll_service,
            telegram_client=telegram_client,
            payments_service=payments_service,
        )
        planner = WeekendPlanner(
            settings=settings,
            session_factory=session_factory,
            poll_service=poll_service,
            auto_scheduler=auto_scheduler,
        )
        scheduler_task = create_task(auto_scheduler.run(), name="veloexpress-poll-auto-scheduler")
        dispatcher = build_dispatcher(
            settings=settings,
            poll_service=poll_service,
            auto_scheduler=auto_scheduler,
            planner=planner,
            payments_service=payments_service,
        )
        await register_bot_commands(bot)
        await register_default_admin_rights(bot)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for task in (scheduler_task, heartbeat_task):
            if task is not None:
                task.cancel()
                with suppress(CancelledError):
                    await task
        await bot.session.close()
