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
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(TelegramOutbox)
                    .where(TelegramOutbox.environment == self._environment)
                    .where(TelegramOutbox.status == "pending")
                    .order_by(TelegramOutbox.id)
                    .limit(limit)
                )
            ).all()
        delivered = 0
        for row in rows:
            try:
                sent = await self._sender.send_text(
                    chat_id=row.chat_id,
                    message_thread_id=row.thread_id,
                    text=row.text,
                    parse_mode=row.parse_mode,
                )
            except Exception as error:
                await self._mark_failed(row.id, error)
                continue
            async with self._session_factory() as session:
                current = await session.get(TelegramOutbox, row.id)
                if current is not None and current.status == "pending":
                    current.status = "sent"
                    current.telegram_message_id = sent.message_id
                    current.sent_at = datetime.now(UTC)
                    current.attempts += 1
                    current.last_error = None
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

    async def _mark_failed(self, row_id: int, error: Exception) -> None:
        async with self._session_factory() as session:
            row = await session.get(TelegramOutbox, row_id)
            if row is None or row.status != "pending":
                return
            row.attempts += 1
            row.last_error = f"{type(error).__name__}: {error}"[:2000]
            await session.commit()
        logger.warning("telegram_outbox_delivery_failed outbox_id=%s", row_id)
