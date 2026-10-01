"""One composition root for Telegram and HTTP. Only the bot starts background loops."""

from dataclasses import dataclass

from veloexpress_bot.config import Settings
from veloexpress_core.history import HistoryStatistics
from veloexpress_core.lifts import PollPostingService, SessionFactory, TelegramPollClient
from veloexpress_core.payments import PaymentsService
from veloexpress_core.planning import WeekendPlanner
from veloexpress_core.scheduler import PollAutoScheduler
from veloexpress_core.terms import ServiceDayDefaultsStore


@dataclass
class Runtime:
    lifts: PollPostingService
    payments: PaymentsService
    planning: WeekendPlanner
    scheduler: PollAutoScheduler
    terms: ServiceDayDefaultsStore
    history: HistoryStatistics


def build_runtime(
    *,
    settings: Settings,
    session_factory: SessionFactory,
    telegram_client: TelegramPollClient,
    bot_username: str = "",
) -> Runtime:
    terms = ServiceDayDefaultsStore(settings=settings, session_factory=session_factory)
    lifts = PollPostingService(
        settings=settings,
        session_factory=session_factory,
        telegram_client=telegram_client,
        bot_username=bot_username,
        service_day_defaults=terms,
    )
    payments = PaymentsService(
        settings=settings,
        session_factory=session_factory,
        telegram_client=telegram_client,
        bot_username=bot_username,
    )
    scheduler = PollAutoScheduler(
        settings=settings,
        session_factory=session_factory,
        poll_service=lifts,
        telegram_client=telegram_client,
        payments_service=payments,
    )
    planning = WeekendPlanner(
        settings=settings,
        session_factory=session_factory,
        poll_service=lifts,
        auto_scheduler=scheduler,
    )
    return Runtime(
        lifts,
        payments,
        planning,
        scheduler,
        terms,
        HistoryStatistics(settings=settings, session_factory=session_factory),
    )
