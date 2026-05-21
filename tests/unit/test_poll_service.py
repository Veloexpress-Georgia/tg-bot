from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.config import Settings
from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import PollBatch, PollMessage
from veloexpress_bot.polls.defaults import StartLocation
from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.polls.service import (
    DuplicatePollError,
    PollPostingService,
    PollSetup,
    SentPollMessage,
    SentTextMessage,
)
from veloexpress_bot.telegram.errors import TelegramTargetForbiddenError


class FakeTelegramClient:
    def __init__(
        self,
        *,
        fail_send_once: bool = False,
        send_error_once: Exception | None = None,
        fail_pin_once: bool = False,
        existing_message_ids: set[int] | None = None,
    ) -> None:
        self.sent: list[PollDraft] = []
        self.sent_texts: list[str] = []
        self.pinned: list[int] = []
        self.deleted: list[int] = []
        self.fail_send_once = fail_send_once
        self.send_error_once = send_error_once
        self.fail_pin_once = fail_pin_once
        self.existing_message_ids = existing_message_ids
        self.next_message_id = 42

    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
    ) -> SentTextMessage:
        assert chat_id == -100123
        assert message_thread_id == 7
        self.sent_texts.append(text)
        message_id = self.next_message_id
        self.next_message_id += 1
        if self.existing_message_ids is not None:
            self.existing_message_ids.add(message_id)
        return SentTextMessage(message_id=message_id)

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage:
        assert chat_id == -100123
        assert message_thread_id == 7
        if self.send_error_once:
            error = self.send_error_once
            self.send_error_once = None
            raise error
        if self.fail_send_once:
            self.fail_send_once = False
            msg = "temporary Telegram failure"
            raise RuntimeError(msg)
        self.sent.append(draft)
        message_id = self.next_message_id
        self.next_message_id += 1
        if self.existing_message_ids is not None:
            self.existing_message_ids.add(message_id)
        return SentPollMessage(message_id=message_id, poll_id=f"poll-{message_id}")

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool:
        assert chat_id == -100123
        if self.fail_pin_once:
            self.fail_pin_once = False
            msg = "post-send persistence failure"
            raise RuntimeError(msg)
        self.pinned.append(message_id)
        return True

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        assert chat_id == -100123
        if message_id < 0:
            return False
        self.deleted.append(message_id)
        if self.existing_message_ids is not None:
            self.existing_message_ids.discard(message_id)
        return True

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool:
        assert chat_id == -100123
        return self.existing_message_ids is None or message_id in self.existing_message_ids


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


@pytest.mark.asyncio
async def test_poll_service_posts_and_persists_message(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.create_poll(
        PollSetup(
            service_date=date(2026, 5, 16),
            created_by_user_id=1,
            first_lift_location=StartLocation.VAKE,
            cancelled_lift_times=("15:30",),
        )
    )

    assert result.message_id == 42
    assert result.poll_id == "poll-42"
    assert result.pinned is True
    assert client.pinned == [42]
    assert "15:30" not in "\n".join(client.sent[0].options)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert len(batches) == 1
    assert batches[0].status == "posted"
    assert len(messages) == 1
    assert messages[0].telegram_message_id == 42
    assert messages[0].message_kind == "poll"


@pytest.mark.asyncio
async def test_poll_service_can_defer_pin_until_after_creation(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),
        pin_after_send=False,
    )

    assert result.pinned is False
    assert client.pinned == []

    async with db.session() as session:
        message = await session.scalar(select(PollMessage))
        assert message is not None
        assert message.pinned is False

    pinned_result = await service.pin_created_poll(result)

    assert pinned_result.pinned is True
    assert client.pinned == [42]

    async with db.session() as session:
        message = await session.scalar(select(PollMessage))
    assert message is not None
    assert message.pinned is True


@pytest.mark.asyncio
async def test_poll_service_can_send_notice_before_poll_and_pin_notice(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),
        include_notice=True,
        pin_after_send=False,
    )

    assert result.notice_message_id == 42
    assert result.message_id == 43
    assert client.sent_texts == [
        "💳 После голосования внесите предоплату.\nPlease send the prepayment after voting."
    ]
    assert client.sent[0].question == "🚐 Суббота · 16 мая\nSaturday · May 16"

    pinned_result = await service.pin_created_notice(result)

    assert pinned_result.pinned is True
    assert client.pinned == [42]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [message.message_kind for message in messages] == ["notice", "poll"]
    assert [message.pinned for message in messages] == [True, False]


@pytest.mark.asyncio
async def test_poll_service_reuses_failed_pre_send_idempotency_key(db: SharedDatabase) -> None:
    client = FakeTelegramClient(fail_send_once=True)
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    with pytest.raises(RuntimeError, match="temporary Telegram failure"):
        await service.create_poll(setup)

    async with db.session() as session:
        failed_batch = await session.scalar(select(PollBatch))
        assert failed_batch is not None
        assert failed_batch.status == "failed"

    result = await service.create_poll(setup)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert result.message_id == 42
    assert len(batches) == 1
    assert batches[0].id == failed_batch.id
    assert batches[0].status == "posted"
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_poll_service_marks_forbidden_target_chat_as_failed(db: SharedDatabase) -> None:
    client = FakeTelegramClient(
        send_error_once=TelegramTargetForbiddenError(
            "Telegram target chat rejected poll posting.",
            telegram_message="Forbidden: bot was kicked from the supergroup chat",
        )
    )
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    with pytest.raises(TelegramTargetForbiddenError):
        await service.create_poll(setup)

    async with db.session() as session:
        failed_batch = await session.scalar(select(PollBatch))

    assert failed_batch is not None
    assert failed_batch.status == "failed"


@pytest.mark.asyncio
async def test_poll_service_reports_existing_active_poll(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    assert await service.has_existing_active_poll(setup) is False

    await service.create_poll(setup)

    assert await service.has_existing_active_poll(setup) is True


@pytest.mark.asyncio
async def test_poll_service_marks_deleted_telegram_poll_as_inactive(db: SharedDatabase) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    await service.create_poll(setup)
    assert client.existing_message_ids is not None
    client.existing_message_ids.clear()

    assert await service.has_existing_active_poll(setup) is False

    async with db.session() as session:
        batch = await session.scalar(select(PollBatch))
        message = await session.scalar(select(PollMessage))

    assert batch is not None
    assert batch.status == "deleted"
    assert message is not None
    assert message.cleanup_status == "telegram_deleted"


@pytest.mark.asyncio
async def test_notice_does_not_keep_deleted_poll_active(db: SharedDatabase) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    await service.create_poll(setup, include_notice=True)
    assert client.existing_message_ids == {42, 43}
    client.existing_message_ids = {42}

    assert await service.has_existing_active_poll(setup) is False


@pytest.mark.asyncio
async def test_poll_service_reuses_deleted_batch_idempotency_key(db: SharedDatabase) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    await service.create_poll(setup)
    assert client.existing_message_ids is not None
    client.existing_message_ids.clear()

    result = await service.create_poll(setup)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert result.message_id == 43
    assert len(client.sent) == 2
    assert len(batches) == 1
    assert batches[0].status == "posted"
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_poll_service_can_manually_create_duplicate_poll(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    await service.create_poll(setup)

    with pytest.raises(DuplicatePollError):
        await service.create_poll(setup)

    result = await service.create_poll(setup, allow_duplicate=True)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert result.message_id == 43
    assert len(client.sent) == 2
    assert len(batches) == 2
    assert len(messages) == 2
    assert len({batch.idempotency_key for batch in batches}) == 2


@pytest.mark.asyncio
async def test_poll_service_blocks_retry_after_post_send_failure(db: SharedDatabase) -> None:
    client = FakeTelegramClient(fail_pin_once=True)
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    with pytest.raises(RuntimeError, match="post-send persistence failure"):
        await service.create_poll(setup)

    async with db.session() as session:
        batch = await session.scalar(select(PollBatch))
        assert batch is not None
        assert batch.status == "sent_unconfirmed"

    with pytest.raises(DuplicatePollError):
        await service.create_poll(setup)

    assert len(client.sent) == 1


@pytest.mark.asyncio
async def test_poll_service_cleans_setup_messages_via_telegram_client(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.cleanup_setup_messages(chat_id=-100123, message_ids=(1, -2, 3))

    assert result.deleted_count == 2
    assert result.failed_count == 1
    assert client.deleted == [1, 3]
