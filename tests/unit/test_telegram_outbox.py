import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import TelegramOutbox
from veloexpress_bot.telegram.errors import TelegramPollPostError, TelegramTargetForbiddenError
from veloexpress_bot.telegram.outbox import (
    MAX_DELIVERY_ATTEMPTS,
    PENDING_MAX_AGE,
    TelegramOutboxDispatcher,
)
from veloexpress_core.lifts import SentTextMessage

_METHOD = SendMessage(chat_id=-100123, text="x")


def _wrapped(cause: Exception) -> TelegramPollPostError:
    """What the real client raises: the aiogram error is kept as the cause."""
    error = TelegramPollPostError("Telegram failed to create text message.")
    error.__cause__ = cause
    return error


class FailingOnceSender:
    def __init__(self) -> None:
        self.failed = False
        self.sent: list[str] = []

    async def send_text(self, **kwargs: object) -> SentTextMessage:
        if not self.failed:
            self.failed = True
            msg = "temporary Telegram failure"
            raise RuntimeError(msg)
        self.sent.append(str(kwargs["text"]))
        return SentTextMessage(message_id=42)


class AlwaysFailingSender:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def send_text(self, **kwargs: object) -> SentTextMessage:
        self.calls += 1
        raise self.error


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, **kwargs: object) -> SentTextMessage:
        self.sent.append(str(kwargs["text"]))
        return SentTextMessage(message_id=len(self.sent))


@dataclass
class Db:
    sessions: async_sessionmaker[AsyncSession]

    def outbox(self, sender: object) -> TelegramOutboxDispatcher:
        return TelegramOutboxDispatcher(
            environment="test", session_factory=self.sessions, sender=sender
        )

    async def only_row(self) -> TelegramOutbox:
        async with self.sessions() as session:
            row = await session.scalar(select(TelegramOutbox))
        assert row is not None
        return row


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Db]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield Db(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def _enqueue(outbox: TelegramOutboxDispatcher, key: str = "lift-confirmed:1") -> None:
    await outbox.enqueue_text(operation_key=key, chat_id=-100123, thread_id=7, text="Payment.")


async def test_outbox_retries_a_failed_delivery_without_duplicate_rows(db: Db) -> None:
    sender = FailingOnceSender()
    outbox = db.outbox(sender)

    for _ in range(2):
        await outbox.enqueue_text(
            operation_key="lift-confirmed:20260830:0830",
            chat_id=-100123,
            thread_id=7,
            text="Payment is open.",
        )
    assert await outbox.deliver_pending() == 0
    assert await outbox.deliver_pending() == 1

    async with db.sessions() as session:
        count = await session.scalar(select(func.count()).select_from(TelegramOutbox))
    row = await db.only_row()

    assert count == 1
    assert row.status == "sent" and row.attempts == 2
    assert sender.sent == ["Payment is open."]


@pytest.mark.parametrize(
    "error",
    [
        TelegramTargetForbiddenError("forbidden"),
        _wrapped(TelegramBadRequest(_METHOD, "Bad Request: can't parse entities")),
        _wrapped(TelegramBadRequest(_METHOD, "Bad Request: message thread not found")),
    ],
    ids=["forbidden", "bad-html", "deleted-topic"],
)
async def test_a_permanent_error_fails_the_row_at_once(
    db: Db, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    sender = AlwaysFailingSender(error)
    outbox = db.outbox(sender)
    await _enqueue(outbox)

    with caplog.at_level(logging.WARNING, logger="veloexpress_bot.telegram.outbox"):
        for _ in range(3):
            assert await outbox.deliver_pending() == 0

    row = await db.only_row()
    assert row.status == "failed" and row.attempts == 1
    assert sender.calls == 1
    assert row.last_error
    gave_up = [r for r in caplog.records if "telegram_outbox_gave_up" in r.getMessage()]
    assert len(gave_up) == 1


async def test_a_transient_error_retries_until_the_attempt_limit(
    db: Db, caplog: pytest.LogCaptureFixture
) -> None:
    sender = AlwaysFailingSender(_wrapped(TelegramNetworkError(_METHOD, "timeout")))
    outbox = db.outbox(sender)
    await _enqueue(outbox)

    with caplog.at_level(logging.WARNING, logger="veloexpress_bot.telegram.outbox"):
        for _ in range(MAX_DELIVERY_ATTEMPTS - 1):
            await outbox.deliver_pending()
        assert (await db.only_row()).status == "pending"
        for _ in range(3):
            await outbox.deliver_pending()

    row = await db.only_row()
    assert row.status == "failed"
    assert row.attempts == MAX_DELIVERY_ATTEMPTS == sender.calls
    gave_up = [r for r in caplog.records if "telegram_outbox_gave_up" in r.getMessage()]
    assert len(gave_up) == 1


async def test_a_stale_pending_row_expires_instead_of_being_sent_late(
    db: Db, caplog: pytest.LogCaptureFixture
) -> None:
    sender = RecordingSender()
    outbox = db.outbox(sender)
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    async with db.sessions() as session:
        for key, age in (("stale", PENDING_MAX_AGE + timedelta(minutes=1)), ("fresh", None)):
            session.add(
                TelegramOutbox(
                    environment="test",
                    operation_key=key,
                    chat_id=-100123,
                    thread_id=7,
                    text=key,
                    status="pending",
                    attempts=3,
                    created_at=now - (age or timedelta(minutes=5)),
                )
            )
        await session.commit()

    with caplog.at_level(logging.WARNING, logger="veloexpress_bot.telegram.outbox"):
        assert await outbox.deliver_pending(now=now) == 1
        assert await outbox.deliver_pending(now=now) == 0

    async with db.sessions() as session:
        rows = {r.operation_key: r for r in await session.scalars(select(TelegramOutbox))}
    assert rows["stale"].status == "expired" and rows["stale"].attempts == 3
    assert rows["fresh"].status == "sent"
    assert sender.sent == ["fresh"]
    expired = [r for r in caplog.records if "telegram_outbox_expired" in r.getMessage()]
    assert len(expired) == 1


async def test_a_failed_row_does_not_block_later_messages(db: Db) -> None:
    class FirstFails(RecordingSender):
        async def send_text(self, **kwargs: object) -> SentTextMessage:
            if kwargs["text"] == "broken":
                raise TelegramTargetForbiddenError("forbidden")
            return await super().send_text(**kwargs)

    sender = FirstFails()
    outbox = db.outbox(sender)
    await outbox.enqueue_text(operation_key="a", chat_id=1, thread_id=None, text="broken")
    await outbox.enqueue_text(operation_key="b", chat_id=1, thread_id=None, text="ok")

    assert await outbox.deliver_pending() == 1
    assert sender.sent == ["ok"]


async def test_a_given_up_message_can_be_requested_again_after_a_rest(db: Db) -> None:
    """A row nobody received must not hold its key forever."""
    from veloexpress_bot.telegram.outbox import REARM_AFTER

    class FailsOnce(RecordingSender):
        failed = False

        async def send_text(self, **kwargs: object) -> SentTextMessage:
            if not self.failed:
                self.failed = True
                raise TelegramTargetForbiddenError("forbidden")
            return await super().send_text(**kwargs)

    sender = FailsOnce()
    outbox = db.outbox(sender)
    await _enqueue(outbox)
    assert await outbox.deliver_pending() == 0
    assert (await db.only_row()).status == "failed"

    # Asked again straight away: still resting, nothing is sent.
    await _enqueue(outbox)
    assert await outbox.deliver_pending() == 0
    assert (await db.only_row()).status == "failed"

    async with db.sessions() as session:
        row = await session.scalar(select(TelegramOutbox))
        assert row is not None
        row.created_at = datetime.now(UTC) - REARM_AFTER - timedelta(minutes=1)
        await session.commit()
    await _enqueue(outbox)
    assert await outbox.deliver_pending() == 1
    row = await db.only_row()
    assert row.status == "sent" and sender.sent == ["Payment."]
