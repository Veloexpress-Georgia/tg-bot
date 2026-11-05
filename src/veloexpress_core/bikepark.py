"""Independent session polls: participation never becomes a seat or a payment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from veloexpress_bot.config import Settings
from veloexpress_bot.db.locking import transaction_lock
from veloexpress_bot.db.models import BikeParkSession, BikeParkVote
from veloexpress_bot.deeplinks import message_link
from veloexpress_bot.polls.bikepark import render_bike_park_poll, validate_bike_park_date
from veloexpress_bot.telegram.errors import TelegramTargetForbiddenError

if TYPE_CHECKING:
    from veloexpress_core.lifts import SessionFactory, TelegramPollClient
    from veloexpress_core.terms import ServiceDayDefaultsStore

ACTIVE = ("posting", "posted", "sent_unconfirmed")


@dataclass(frozen=True)
class BikeParkRider:
    telegram_user_id: int
    label: str


@dataclass(frozen=True)
class BikeParkView:
    id: int
    service_date: date
    start_time: str
    price_gel: int
    status: str
    riders: tuple[BikeParkRider, ...]
    poll_url: str | None
    started: bool


class BikeParkService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        telegram_client: TelegramPollClient,
        defaults: ServiceDayDefaultsStore,
    ) -> None:
        self.settings = settings
        self.sessions = session_factory
        self.telegram = telegram_client
        self.defaults = defaults
        self.zone = ZoneInfo(settings.schedule_timezone)

    def scope(self, query: Any) -> Any:
        return query.where(
            BikeParkSession.environment == self.settings.app_env,
            BikeParkSession.chat_id == self.settings.telegram_target_chat_id,
            BikeParkSession.thread_id == self.settings.telegram_target_thread_id,
        )

    def now(self) -> datetime:
        return datetime.now(UTC).astimezone(self.zone)

    async def list(self, *, service_date: date | None = None) -> tuple[BikeParkView, ...]:
        now = self.now()
        query = self.scope(select(BikeParkSession)).where(BikeParkSession.status.in_(ACTIVE))
        if service_date is not None:
            query = query.where(BikeParkSession.service_date == service_date)
        else:
            query = query.where(BikeParkSession.service_date >= now.date())
        async with self.sessions() as session:
            rows = list(await session.scalars(query.order_by(BikeParkSession.service_date)))
            if not rows:
                return ()
            votes = list(
                await session.scalars(
                    select(BikeParkVote)
                    .where(
                        BikeParkVote.session_id.in_([row.id for row in rows]),
                        BikeParkVote.attending.is_(True),
                    )
                    .order_by(BikeParkVote.id)
                )
            )
        return tuple(
            BikeParkView(
                id=row.id,
                service_date=row.service_date,
                start_time=row.start_time,
                price_gel=row.price_gel,
                status=row.status,
                riders=tuple(
                    BikeParkRider(v.telegram_user_id, v.full_name)
                    for v in votes
                    if v.session_id == row.id
                ),
                poll_url=message_link(
                    chat_id=row.chat_id, thread_id=row.thread_id, message_id=row.telegram_message_id
                )
                if row.telegram_message_id
                else None,
                started=(row.service_date, row.start_time) <= (now.date(), now.strftime("%H:%M")),
            )
            for row in rows
        )

    async def preview(self, service_date: date, start_time: str) -> tuple[str, int]:
        normalized = validate_bike_park_date(service_date, start_time, self.now())
        if await self.list(service_date=service_date):
            raise ValueError("This day already has a bike park poll.")
        values = await self.defaults.values()
        return render_bike_park_poll(
            service_date, normalized, values.bike_park_price_gel
        ).question, values.bike_park_price_gel

    async def create(
        self,
        *,
        service_date: date,
        start_time: str,
        admin_user_id: int,
        expected_price: int,
        replace_lift_time: str | None = None,
    ) -> BikeParkView:
        chat_id = self.settings.telegram_target_chat_id
        if chat_id is None:
            raise ValueError("TELEGRAM_TARGET_CHAT_ID is required.")
        start = validate_bike_park_date(service_date, start_time, self.now())
        price = (await self.defaults.values()).bike_park_price_gel
        if price != expected_price:
            raise ValueError("Price changed. Open the confirmation again.")
        async with self.sessions() as session:
            await transaction_lock(
                session,
                f"bikepark:{self.settings.app_env}:{chat_id}:{self.settings.telegram_target_thread_id}:{service_date}",
            )
            existing = await session.scalar(
                self.scope(select(BikeParkSession)).where(
                    BikeParkSession.service_date == service_date, BikeParkSession.status.in_(ACTIVE)
                )
            )
            if existing is not None:
                raise ValueError("This day already has a bike park poll.")
            row = BikeParkSession(
                environment=self.settings.app_env,
                chat_id=chat_id,
                thread_id=self.settings.telegram_target_thread_id,
                service_date=service_date,
                start_time=start,
                price_gel=price,
                created_by_user_id=admin_user_id,
                replace_lift_time=replace_lift_time,
                replacement_completed=replace_lift_time is None,
            )
            session.add(row)
            await session.commit()
            row_id = row.id
        try:
            sent = await self.telegram.send_poll(
                chat_id=chat_id,
                message_thread_id=self.settings.telegram_target_thread_id,
                draft=render_bike_park_poll(service_date, start, price),
            )
        except BaseException as error:
            # A transport failure can mean Telegram received the poll. Do not
            # silently duplicate it or cancel the ordinary lift in that case.
            async with self.sessions() as session:
                saved = await session.get(BikeParkSession, row_id)
                assert saved is not None
                saved.status = (
                    "failed"
                    if isinstance(error, TelegramTargetForbiddenError)
                    else "sent_unconfirmed"
                )
                await session.commit()
            raise
        async with self.sessions() as session:
            saved = await session.get(BikeParkSession, row_id)
            assert saved is not None
            saved.telegram_message_id = sent.message_id
            saved.poll_id = sent.poll_id
            saved.status = "posted"
            await session.commit()
        return (await self.list(service_date=service_date))[0]

    async def track_answer(
        self, *, poll_id: str, telegram_user_id: int, full_name: str, option_ids: tuple[int, ...]
    ) -> bool:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(BikeParkSession))
                .where(BikeParkSession.poll_id == poll_id)
                .with_for_update()
            )
            if row is None:
                return False
            if row.status != "posted":
                return True
            vote = await session.scalar(
                select(BikeParkVote).where(
                    BikeParkVote.session_id == row.id,
                    BikeParkVote.telegram_user_id == telegram_user_id,
                )
            )
            if vote is None:
                session.add(
                    BikeParkVote(
                        session_id=row.id,
                        telegram_user_id=telegram_user_id,
                        full_name=full_name,
                        attending=0 in option_ids,
                    )
                )
            else:
                vote.attending = 0 in option_ids
                vote.full_name = full_name
            await session.commit()
        return True

    async def cancel(self, *, service_date: date) -> None:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(BikeParkSession))
                .where(
                    BikeParkSession.service_date == service_date, BikeParkSession.status.in_(ACTIVE)
                )
                .with_for_update()
            )
            if row is None:
                raise ValueError("Bike park session is no longer active.")
            if (row.service_date, row.start_time) <= (
                self.now().date(),
                self.now().strftime("%H:%M"),
            ):
                raise ValueError("A started session cannot be cancelled.")
            if row.status == "posting":
                raise ValueError("The poll is still being published.")
            row.status = "cancelled"
            await session.commit()
        await self.reconcile(service_date=service_date)

    async def finish_replacement(self, session_id: int) -> None:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(BikeParkSession)).where(BikeParkSession.id == session_id)
            )
            if row:
                row.replacement_completed = True
                await session.commit()

    async def reconcile(self, *, service_date: date | None = None) -> None:
        query = self.scope(select(BikeParkSession)).where(
            BikeParkSession.status.in_(("posting", "posted", "cancelled"))
        )
        if service_date:
            query = query.where(BikeParkSession.service_date == service_date)
        else:
            query = query.where(
                or_(
                    BikeParkSession.service_date >= self.now().date(),
                    (BikeParkSession.status == "cancelled")
                    & BikeParkSession.poll_closed.is_(False),
                )
            )
        async with self.sessions() as session:
            rows = list(await session.scalars(query.with_for_update()))
            for row in rows:
                created_at = row.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                if (
                    row.status == "posting"
                    and (datetime.now(UTC) - created_at).total_seconds() > 300
                ):
                    row.status = "sent_unconfirmed"
                if row.telegram_message_id is None:
                    continue
                if row.status == "cancelled" and not row.poll_closed:
                    row.poll_closed = await self.telegram.stop_poll(
                        chat_id=row.chat_id, message_id=row.telegram_message_id
                    )
                elif (
                    row.status == "posted"
                    and (row.service_date, row.start_time)
                    > (self.now().date(), self.now().strftime("%H:%M"))
                    and not await self.telegram.message_exists(
                        chat_id=row.chat_id, message_id=row.telegram_message_id
                    )
                ):
                    row.status = "cancelled"
                    row.poll_closed = True
            await session.commit()
