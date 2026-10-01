from asyncio import CancelledError, create_task
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo

from veloexpress_bot.bot.admin_rights import register_default_admin_rights
from veloexpress_bot.bot.commands import register_bot_commands
from veloexpress_bot.bot.handlers import router
from veloexpress_bot.config import Settings, get_settings
from veloexpress_bot.db.session import check_database, create_session_factory
from veloexpress_bot.health import start_heartbeat_task
from veloexpress_bot.observability import configure_logging
from veloexpress_bot.telegram.client import AiogramTelegramClient
from veloexpress_core.commands import CommandQueue, Operations
from veloexpress_core.history import HistoryStatistics
from veloexpress_core.lifts import PollPostingService
from veloexpress_core.payments import PaymentsService
from veloexpress_core.planning import WeekendPlanner
from veloexpress_core.runtime import build_runtime
from veloexpress_core.scheduler import PollAutoScheduler
from veloexpress_core.terms import ServiceDayDefaultsStore


def build_dispatcher(
    *,
    settings: Settings,
    poll_service: PollPostingService,
    auto_scheduler: PollAutoScheduler,
    planner: WeekendPlanner,
    payments_service: PaymentsService,
    service_day_defaults: ServiceDayDefaultsStore,
    history_statistics: HistoryStatistics,
) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    dispatcher["history_statistics"] = history_statistics
    dispatcher["settings"] = settings
    dispatcher["poll_service"] = poll_service
    dispatcher["auto_scheduler"] = auto_scheduler
    dispatcher["planner"] = planner
    dispatcher["payments_service"] = payments_service
    dispatcher["service_day_defaults"] = service_day_defaults
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
    commands_task = None
    try:
        session_factory = create_session_factory(settings)
        await check_database(session_factory)
        telegram_client = AiogramTelegramClient(bot)
        bot_username = (await bot.me()).username or ""
        runtime = build_runtime(
            settings=settings,
            session_factory=session_factory,
            telegram_client=telegram_client,
            bot_username=bot_username,
        )
        service_day_defaults = runtime.terms
        poll_service = runtime.lifts
        payments_service = runtime.payments
        auto_scheduler = runtime.scheduler
        planner = runtime.planning
        if settings.web_app_url:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="VeloExpress", web_app=WebAppInfo(url=settings.web_app_url)
                )
            )
        commands_task = create_task(
            CommandQueue(settings=settings, session_factory=session_factory).run(
                Operations(settings=settings, runtime=runtime)
            ),
            name="veloexpress-web-commands",
        )
        scheduler_task = create_task(auto_scheduler.run(), name="veloexpress-poll-auto-scheduler")
        dispatcher = build_dispatcher(
            settings=settings,
            history_statistics=runtime.history,
            poll_service=poll_service,
            auto_scheduler=auto_scheduler,
            planner=planner,
            payments_service=payments_service,
            service_day_defaults=service_day_defaults,
        )
        await register_bot_commands(bot)
        await register_default_admin_rights(bot)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for task in (scheduler_task, commands_task, heartbeat_task):
            if task is not None:
                task.cancel()
                with suppress(CancelledError):
                    await task
        await bot.session.close()
