from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.db.models import TelegramOutbox
from veloexpress_bot.telegram.errors import TelegramTargetForbiddenError

logger = logging.getLogger(__name__)

# About ten minutes of 30-second scheduler ticks: long enough to ride out a
# Telegram hiccup, short enough that a broken row stops being retried.
MAX_DELIVERY_ATTEMPTS = 20
# Notices are about a lift today (a departure ping, a confirmation). One that
# could not go out for this long is wrong news, not late news, so it is dropped.
PENDING_MAX_AGE = timedelta(hours=6)
# How long a given-up message rests before the same request may try it again.
REARM_AFTER = timedelta(minutes=15)

# Telegram rejected the request itself; sending the same text again cannot help.
_PERMANENT_ERRORS = (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
    TelegramTargetForbiddenError,
)


def _is_permanent(error: BaseException) -> bool:
    # The client wraps aiogram errors and keeps the original as the cause.
    return isinstance(error, _PERMANENT_ERRORS) or isinstance(error.__cause__, _PERMANENT_ERRORS)


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
                # The key is taken. A row that was sent, or is still on its way,
                # is the duplicate this guards against. One that was given up on
                # never reached anybody, so a caller asking again gets a fresh try.
                stale = await session.scalar(
                    select(TelegramOutbox)
                    .where(TelegramOutbox.environment == self._environment)
                    .where(TelegramOutbox.operation_key == operation_key)
                    .where(TelegramOutbox.status.in_(("failed", "expired")))
                    # Not on every scheduler tick: a caller that asks each tick
                    # would otherwise retry a broken message forever again.
                    .where(TelegramOutbox.created_at < datetime.now(UTC) - REARM_AFTER)
                    .with_for_update()
                )
                if stale is not None:
                    stale.chat_id = chat_id
                    stale.thread_id = thread_id
                    stale.text = text
                    stale.parse_mode = parse_mode
                    stale.status = "pending"
                    stale.attempts = 0
                    stale.created_at = datetime.now(UTC)
                    await session.commit()

    async def deliver_pending(self, *, limit: int = 20, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - PENDING_MAX_AGE
        await self._expire_stale(cutoff)
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
                    .where(TelegramOutbox.created_at >= cutoff)
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
                    permanent = _is_permanent(error)
                    if permanent or row.attempts >= MAX_DELIVERY_ATTEMPTS:
                        row.status = "failed"
                    await session.commit()
                    if row.status == "failed":
                        logger.warning(
                            "telegram_outbox_gave_up outbox_id=%s operation_key=%s "
                            "attempts=%s permanent=%s",
                            row.id,
                            row.operation_key,
                            row.attempts,
                            permanent,
                        )
                    else:
                        logger.info(
                            "telegram_outbox_delivery_failed outbox_id=%s attempts=%s",
                            row.id,
                            row.attempts,
                        )
                    continue
                row.status = "sent"
                row.telegram_message_id = sent.message_id
                row.sent_at = datetime.now(UTC)
                row.attempts += 1
                row.last_error = None
                await session.commit()
                delivered += 1
        return delivered

    async def _expire_stale(self, cutoff: datetime) -> None:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(TelegramOutbox)
                    .where(TelegramOutbox.environment == self._environment)
                    .where(TelegramOutbox.status == "pending")
                    .where(TelegramOutbox.created_at < cutoff)
                    .order_by(TelegramOutbox.id)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = "expired"
            await session.commit()
        for row in rows:
            logger.warning(
                "telegram_outbox_expired outbox_id=%s operation_key=%s attempts=%s",
                row.id,
                row.operation_key,
                row.attempts,
            )

    async def message_id(self, operation_key: str) -> int | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(TelegramOutbox.telegram_message_id)
                .where(TelegramOutbox.environment == self._environment)
                .where(TelegramOutbox.operation_key == operation_key)
            )
