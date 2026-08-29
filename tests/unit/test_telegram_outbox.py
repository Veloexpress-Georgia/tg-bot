from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import TelegramOutbox
from veloexpress_bot.polls.service import SentTextMessage
from veloexpress_bot.telegram.outbox import TelegramOutboxDispatcher


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


@pytest.mark.asyncio
async def test_outbox_retries_a_failed_delivery_without_duplicate_rows(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sender = FailingOnceSender()
    outbox = TelegramOutboxDispatcher(
        environment="test",
        session_factory=sessions,
        sender=sender,
    )

    for _ in range(2):
        await outbox.enqueue_text(
            operation_key="lift-confirmed:20260830:0830",
            chat_id=-100123,
            thread_id=7,
            text="Payment is open.",
        )
    assert await outbox.deliver_pending() == 0
    assert await outbox.deliver_pending() == 1

    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(TelegramOutbox))
        row = await session.scalar(select(TelegramOutbox))
    await engine.dispose()

    assert count == 1
    assert row is not None and row.status == "sent" and row.attempts == 2
    assert sender.sent == ["Payment is open."]
