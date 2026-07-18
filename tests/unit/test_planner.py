from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
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
from veloexpress_bot.polls.planner import WeekendPlanner
from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.polls.service import (
    PollPostingService,
    SentPollMessage,
    SentTextMessage,
)

TBILISI = ZoneInfo("Asia/Tbilisi")
WEEK = date(2026, 7, 25)
NOW = datetime(2026, 7, 20, 10, 0, tzinfo=TBILISI)


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


def build_planner(
    db: SharedDatabase,
    client: RecordingTelegramClient,
) -> tuple[WeekendPlanner, PollAutoScheduler]:
    resolved_settings = settings()
    poll_service = PollPostingService(
        settings=resolved_settings,
        session_factory=db.session,
        telegram_client=client,
    )
    auto_scheduler = PollAutoScheduler(
        settings=resolved_settings,
        session_factory=db.session,
        poll_service=poll_service,
        telegram_client=client,
    )
    planner = WeekendPlanner(
        settings=resolved_settings,
        session_factory=db.session,
        poll_service=poll_service,
        auto_scheduler=auto_scheduler,
    )
    return planner, auto_scheduler


async def store_schedule(db: SharedDatabase, *, enabled: bool = True) -> None:
    async with db.session() as session:
        session.add(
            PollAutoSchedule(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                enabled=enabled,
                creation_weekday=4,
                creation_time="14:00",
                announce_lead_minutes=120,
                updated_by_user_id=42,
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def plan_row(db: SharedDatabase) -> PollWeekendPlan:
    async with db.session() as session:
        row = await session.scalar(select(PollWeekendPlan))
    assert row is not None
    return row


async def test_plan_edits_persist_and_render(db: SharedDatabase) -> None:
    planner, _ = build_planner(db, RecordingTelegramClient())
    await store_schedule(db)

    await planner.toggle_day("sun", admin_user_id=1, now=NOW)
    await planner.set_range_boundary("first", "10:00", admin_user_id=1, now=NOW)

    row = await plan_row(db)
    assert row.saturday_enabled is True
    assert row.sunday_enabled is False
    assert row.first_lift_time == "10:00"
    assert row.updated_by_user_id == 1

    card = await planner.plan_card(now=NOW)
    assert "🚲 Lifts: 10:00 → 13:30 · 3 lifts" in card.text
    assert "🕓 Opens: Fri, 24 Jul · 14:00 (auto)" in card.text


async def test_plan_cannot_disable_both_days(db: SharedDatabase) -> None:
    planner, _ = build_planner(db, RecordingTelegramClient())

    await planner.toggle_day("sat", admin_user_id=1, now=NOW)
    with pytest.raises(ValueError, match="At least one day"):
        await planner.toggle_day("sun", admin_user_id=1, now=NOW)


async def test_post_now_posts_planned_days_and_marks_week(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    planner, _ = build_planner(db, client)
    await store_schedule(db)

    await planner.toggle_day("sun", admin_user_id=1, now=NOW)
    result = await planner.post_now(admin_user_id=1, now=NOW)

    assert result.created_count == 1
    assert result.already_posted is False
    assert len(client.sent_polls) == 1

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        schedule = await session.scalar(select(PollAutoSchedule))
    assert [batch.service_date for batch in batches] == [WEEK]
    assert schedule is not None
    assert schedule.last_created_week_start == WEEK

    repeat = await planner.post_now(admin_user_id=1, now=NOW)
    assert repeat.already_posted is True
    assert len(client.sent_polls) == 1


async def test_recreate_replaces_posted_weekend(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    planner, _ = build_planner(db, client)
    await store_schedule(db)

    await planner.post_now(admin_user_id=1, now=NOW)
    assert len(client.sent_polls) == 2

    report_text, cleanup_failed = await planner.recreate(admin_user_id=1, now=NOW)

    assert "Recreated existing polls." in report_text
    assert cleanup_failed == 0
    assert len(client.sent_polls) == 4

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
    statuses = {batch.status for batch in batches}
    assert "posted" in statuses
    assert "recreated" in statuses


async def test_plan_card_shows_posted_days(db: SharedDatabase) -> None:
    client = RecordingTelegramClient()
    planner, _ = build_planner(db, client)
    await store_schedule(db)

    await planner.post_now(admin_user_id=1, now=NOW)
    card = await planner.plan_card(now=NOW)

    assert "✅ Polls are posted." in card.text
    buttons = {button.callback_data for row in card.reply_markup.inline_keyboard for button in row}
    assert "plan:view:recreate" in buttons
    assert "plan:post" not in buttons
