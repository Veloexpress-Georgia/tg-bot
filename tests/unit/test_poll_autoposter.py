from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.config import Settings
from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import PollAutoSchedule, PollBatch, PollWeekendPlan
from veloexpress_bot.polls.autoposter import PollAutoScheduler
from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.polls.service import (
    PollPostingService,
    PollSetup,
    SentPollMessage,
    SentTextMessage,
)

TBILISI = ZoneInfo("Asia/Tbilisi")
WEEK = date(2026, 7, 25)  # Saturday


class RecordingTelegramClient:
    def __init__(self) -> None:
        self.sent_polls: list[PollDraft] = []
        self.sent_texts: list[str] = []
        self.deleted: list[int] = []
        self.next_message_id = 100

    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> SentTextMessage:
        self.sent_texts.append(text)
        self.next_message_id += 1
        return SentTextMessage(message_id=self.next_message_id)

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage:
        self.sent_polls.append(draft)
        self.next_message_id += 1
        return SentPollMessage(
            message_id=self.next_message_id,
            poll_id=f"poll-{self.next_message_id}",
        )

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        return True

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool:
        return True

    async def unpin_message(self, *, chat_id: int, message_id: int) -> bool:
        return True

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool:
        return True


class SharedDatabase:
    def __init__(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.factory() as session:
            yield session


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


def settings() -> Settings:
    settings_factory = cast(Any, Settings)
    return settings_factory(
        _env_file=None,
        app_env="test",
        telegram_bot_token="token",
        telegram_target_chat_id=-100123,
        telegram_target_thread_id=7,
        telegram_admin_ids=(1,),
    )


def build_scheduler(
    db: SharedDatabase,
    client: RecordingTelegramClient,
) -> tuple[PollAutoScheduler, PollPostingService]:
    resolved_settings = settings()
    poll_service = PollPostingService(
        settings=resolved_settings,
        session_factory=db.session,
        telegram_client=client,
    )
    scheduler = PollAutoScheduler(
        settings=resolved_settings,
        session_factory=db.session,
        poll_service=poll_service,
        telegram_client=client,
    )
    return scheduler, poll_service


async def store_schedule(
    db: SharedDatabase,
    *,
    enabled: bool = True,
    creation_weekday: int = 4,
    creation_time: str = "14:00",
    announce_lead_minutes: int = 120,
    skip_week_start: date | None = None,
) -> None:
    async with db.session() as session:
        session.add(
            PollAutoSchedule(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                enabled=enabled,
                creation_weekday=creation_weekday,
                creation_time=creation_time,
                announce_lead_minutes=announce_lead_minutes,
                skip_week_start=skip_week_start,
                updated_by_user_id=42,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def schedule_row(db: SharedDatabase) -> PollAutoSchedule:
    async with db.session() as session:
        row = await session.scalar(select(PollAutoSchedule))
    assert row is not None
    return row


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=TBILISI)


async def test_tick_does_nothing_without_configured_schedule(db: SharedDatabase) -> None:
    scheduler, _ = build_scheduler(db, RecordingTelegramClient())

    assert await scheduler.tick(at(24, 14)) is None


async def test_tick_runs_lift_signals_even_without_a_configured_schedule(
    db: SharedDatabase,
) -> None:
    client = RecordingTelegramClient()
    scheduler, poll_service = build_scheduler(db, client)
    service_date = datetime.now(UTC).date() + timedelta(days=2)
    poll = await poll_service.create_poll(
        PollSetup(
            service_date=service_date,
            created_by_user_id=1,
            cancelled_lift_times=("15:30",),
        ),
        include_notice=False,
        pin_after_send=False,
    )
    for index in range(5):
        await poll_service.track_poll_answer(
            poll_id=poll.poll_id or "",
            telegram_user_id=300 + index,
            username=f"rider{index}",
            full_name=f"Rider {index}",
            option_ids=(0,),
        )

    # Threshold notices are not part of auto-posting, so an admin who never set
    # up a schedule must still get them.
    assert await scheduler.tick(datetime.now(UTC)) is None
    assert any("pay to lock it in" in text for text in client.sent_texts)


async def test_tick_announces_once_then_creates_weekend_polls(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    scheduler, _ = build_scheduler(db, client)
    await store_schedule(db)

    assert await scheduler.tick(at(24, 12, 30)) == "announce"
    assert len(client.sent_texts) == 1
    assert "Sat 25 Jul + Sun 26" in client.sent_texts[0]
    row = await schedule_row(db)
    assert row.last_announced_week_start == WEEK
    announce_message_id = row.announce_message_id
    assert announce_message_id is not None

    assert await scheduler.tick(at(24, 12, 31)) is None
    assert len(client.sent_texts) == 1

    assert await scheduler.tick(at(24, 14, 0)) == "create"
    assert len(client.sent_polls) == 2
    assert announce_message_id in client.deleted
    row = await schedule_row(db)
    assert row.last_created_week_start == WEEK
    assert row.announce_message_id is None

    assert await scheduler.tick(at(24, 15)) is None
    assert len(client.sent_polls) == 2

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
    assert {batch.service_date for batch in batches} == {WEEK, date(2026, 7, 26)}
    assert all(batch.status == "posted" for batch in batches)
    assert all(batch.created_by_user_id == 42 for batch in batches)


async def test_tick_creates_only_missing_days_when_admin_already_posted(
    db: SharedDatabase,
) -> None:
    client = RecordingTelegramClient()
    scheduler, poll_service = build_scheduler(db, client)
    await store_schedule(db, announce_lead_minutes=0)

    await poll_service.create_poll(
        PollSetup(
            service_date=WEEK,
            created_by_user_id=1,
            cancelled_lift_times=("15:30",),
        ),
        include_notice=False,
        pin_after_send=False,
    )
    manual_poll_count = len(client.sent_polls)

    assert await scheduler.tick(at(24, 14)) == "create"
    assert len(client.sent_polls) == manual_poll_count + 1

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
    assert {batch.service_date for batch in batches} == {WEEK, date(2026, 7, 26)}


async def test_tick_honours_weekend_plan_days_and_range(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    scheduler, _ = build_scheduler(db, client)
    await store_schedule(db, announce_lead_minutes=0)
    async with db.session() as session:
        session.add(
            PollWeekendPlan(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                service_week_start=WEEK,
                saturday_enabled=False,
                sunday_enabled=True,
                first_lift_time="10:00",
                last_lift_time="13:30",
                updated_by_user_id=77,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()

    assert await scheduler.tick(at(24, 14)) == "create"

    assert len(client.sent_polls) == 1
    assert "Sunday" in client.sent_polls[0].question
    assert client.sent_polls[0].options[0] == "🚲 10:00"

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
    assert [batch.service_date for batch in batches] == [date(2026, 7, 26)]
    assert batches[0].created_by_user_id == 77


async def test_tick_marks_skipped_week_without_posting(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    scheduler, _ = build_scheduler(db, client)
    await store_schedule(db, skip_week_start=WEEK)

    assert await scheduler.tick(at(24, 12, 30)) is None
    assert client.sent_texts == []

    assert await scheduler.tick(at(24, 14, 1)) == "mark_skipped"
    assert client.sent_polls == []
    row = await schedule_row(db)
    assert row.last_created_week_start == WEEK


async def test_toggle_skip_removes_stale_announcement(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    scheduler, _ = build_scheduler(db, client)
    await store_schedule(db)

    assert await scheduler.tick(at(24, 12, 30)) == "announce"
    row = await schedule_row(db)
    announce_message_id = row.announce_message_id
    assert announce_message_id is not None

    await scheduler.toggle_skip(admin_user_id=1, now=at(24, 13))

    row = await schedule_row(db)
    assert row.skip_week_start == WEEK
    assert announce_message_id in client.deleted
    assert row.announce_message_id is None

    await scheduler.toggle_skip(admin_user_id=1, now=at(24, 13))
    row = await schedule_row(db)
    assert row.skip_week_start is None


async def test_schedule_card_uses_defaults_before_first_save(db: SharedDatabase) -> None:
    scheduler, _ = build_scheduler(db, RecordingTelegramClient())

    card = await scheduler.schedule_card()
    assert "⏸ paused" in card.text
    assert "Friday · 14:00 (Asia/Tbilisi)" in card.text

    await scheduler.toggle_enabled(admin_user_id=1)
    card = await scheduler.schedule_card()
    assert "✅ polls are created automatically" in card.text
    assert "🚲 Lifts: 8:30 → 13:30 · 4 lifts" in card.text
    row = await schedule_row(db)
    assert row.enabled is True
    assert row.updated_by_user_id == 1
