from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.db.models import TelegramOutbox

logger = logging.getLogger(__name__)


class TelegramOutboxDispatcher:
    def __init__(
        self,
        *,
        environment: str,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        sender: Any,
    ) -> None:
        self._environment = environment
        self._session_factory = session_factory
        self._sender = sender

    async def enqueue_text(
        self,
        *,
        operation_key: str,
        chat_id: int,
        thread_id: int | None,
        text: str,
        parse_mode: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                TelegramOutbox(
                    environment=self._environment,
                    operation_key=operation_key,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    text=text,
                    parse_mode=parse_mode,
                    status="pending",
                    attempts=0,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def deliver_pending(self, *, limit: int = 20) -> int:
        delivered = 0
        attempted: list[int] = []
        for _ in range(limit):
            async with self._session_factory() as session:
                # Hold the row lock through send and acknowledgement. Another
                # dispatcher skips it; a crashed process releases it automatically.
                row = await session.scalar(
                    select(TelegramOutbox)
                    .where(TelegramOutbox.environment == self._environment)
                    .where(TelegramOutbox.status == "pending")
                    .where(TelegramOutbox.id.not_in(attempted))
                    .order_by(TelegramOutbox.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    break
                attempted.append(row.id)
                try:
                    sent = await self._sender.send_text(
                        chat_id=row.chat_id,
                        message_thread_id=row.thread_id,
                        text=row.text,
                        parse_mode=row.parse_mode,
                    )
                except Exception as error:
                    row.attempts += 1
                    row.last_error = f"{type(error).__name__}: {error}"[:2000]
                    await session.commit()
                    logger.warning("telegram_outbox_delivery_failed outbox_id=%s", row.id)
                    continue
                row.status = "sent"
                row.telegram_message_id = sent.message_id
                row.sent_at = datetime.now(UTC)
                row.attempts += 1
                row.last_error = None
                await session.commit()
                delivered += 1
        return delivered

    async def message_id(self, operation_key: str) -> int | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(TelegramOutbox.telegram_message_id)
                .where(TelegramOutbox.environment == self._environment)
                .where(TelegramOutbox.operation_key == operation_key)
            )
