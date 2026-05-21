from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import PollBatch, PollMessage
from veloexpress_bot.polls.defaults import StartLocation
from veloexpress_bot.polls.render import (
    PollDraft,
    PollRenderInput,
    render_payment_notice,
    render_poll,
)


class DuplicatePollError(RuntimeError):
    pass


@dataclass(frozen=True)
class SentPollMessage:
    message_id: int
    poll_id: str | None = None


@dataclass(frozen=True)
class SentTextMessage:
    message_id: int


class TelegramPollClient(Protocol):
    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
    ) -> SentTextMessage: ...

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage: ...

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool: ...


@dataclass(frozen=True)
class PollSetup:
    service_date: date
    created_by_user_id: int
    first_lift_location: StartLocation = StartLocation.JUSTICE_HALL
    cancelled_lift_times: tuple[str, ...] = ()

    def idempotency_key(self, settings: Settings) -> str:
        cancelled = ",".join(sorted(self.cancelled_lift_times))
        return (
            f"{settings.app_env}:{settings.telegram_target_chat_id}:"
            f"{settings.telegram_target_thread_id}:{self.service_date.isoformat()}:"
            f"{self.first_lift_location}:{cancelled}"
        )


@dataclass(frozen=True)
class PollCreationResult:
    batch_id: int
    message_id: int
    poll_id: str | None
    pinned: bool
    notice_message_id: int | None = None


@dataclass(frozen=True)
class CleanupResult:
    deleted_count: int
    failed_count: int


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PollPostingService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        telegram_client: TelegramPollClient,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._telegram_client = telegram_client

    async def has_existing_active_poll(self, setup: PollSetup) -> bool:
        idempotency_key = setup.idempotency_key(self._settings)
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(PollBatch).where(PollBatch.idempotency_key == idempotency_key)
            )
            return await self._is_active_batch(session=session, batch=existing)

    async def create_poll(
        self,
        setup: PollSetup,
        *,
        allow_duplicate: bool = False,
        include_notice: bool = False,
        pin_after_send: bool = True,
    ) -> PollCreationResult:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to post polls."
            raise ValueError(msg)

        draft = render_poll(
            PollRenderInput(
                service_date=setup.service_date,
                first_lift_location=setup.first_lift_location,
                cancelled_lift_times=setup.cancelled_lift_times,
            )
        )
        base_idempotency_key = setup.idempotency_key(self._settings)
        idempotency_key = (
            f"{base_idempotency_key}:manual:{uuid4()}" if allow_duplicate else base_idempotency_key
        )

        async with self._session_factory() as session:
            existing = None
            if not allow_duplicate:
                existing = await session.scalar(
                    select(PollBatch).where(PollBatch.idempotency_key == idempotency_key)
                )
            if await self._is_active_batch(session=session, batch=existing):
                raise DuplicatePollError("Poll batch already exists for this setup.")

            if existing and existing.status in {"deleted", "failed"}:
                batch = existing
                batch.status = "posting"
                batch.created_by_user_id = setup.created_by_user_id
                batch.first_lift_location = setup.first_lift_location.value
            else:
                batch = PollBatch(
                    environment=self._settings.app_env,
                    chat_id=self._settings.telegram_target_chat_id,
                    thread_id=self._settings.telegram_target_thread_id,
                    service_date=setup.service_date,
                    service_day=setup.service_date.strftime("%A").lower(),
                    created_by_user_id=setup.created_by_user_id,
                    first_lift_location=setup.first_lift_location.value,
                    status="posting",
                    idempotency_key=idempotency_key,
                )
                session.add(batch)
            await session.flush()
            await session.commit()

            notice_message: SentTextMessage | None = None
            try:
                if include_notice:
                    notice_message = await self._telegram_client.send_text(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_thread_id=self._settings.telegram_target_thread_id,
                        text=render_payment_notice(),
                    )
                sent_message = await self._telegram_client.send_poll(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_thread_id=self._settings.telegram_target_thread_id,
                    draft=draft,
                )
            except Exception:
                batch.status = "failed"
                await session.commit()
                if notice_message is not None:
                    await self._telegram_client.delete_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=notice_message.message_id,
                    )
                raise

            pinned = False
            try:
                if self._settings.telegram_pin_poll and pin_after_send:
                    message_id_to_pin = (
                        notice_message.message_id
                        if notice_message is not None
                        else sent_message.message_id
                    )
                    pinned = await self._telegram_client.pin_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=message_id_to_pin,
                    )
                batch.status = "posted"
                if notice_message is not None:
                    session.add(
                        PollMessage(
                            batch_id=batch.id,
                            telegram_message_id=notice_message.message_id,
                            poll_id=None,
                            message_kind="notice",
                            pinned=pinned,
                            cleanup_status="not_applicable",
                        )
                    )
                message = PollMessage(
                    batch_id=batch.id,
                    telegram_message_id=sent_message.message_id,
                    poll_id=sent_message.poll_id,
                    message_kind="poll",
                    pinned=pinned if notice_message is None else False,
                    cleanup_status="not_applicable",
                )
                session.add(message)
                await session.commit()
            except Exception:
                await session.rollback()
                await _mark_batch_status(
                    session=session,
                    batch_id=batch.id,
                    status="sent_unconfirmed",
                )
                raise

            return PollCreationResult(
                batch_id=batch.id,
                message_id=sent_message.message_id,
                poll_id=sent_message.poll_id,
                pinned=pinned,
                notice_message_id=notice_message.message_id if notice_message is not None else None,
            )

    async def pin_created_poll(self, result: PollCreationResult) -> PollCreationResult:
        if self._settings.telegram_target_chat_id is None or not self._settings.telegram_pin_poll:
            return result

        pinned = await self._telegram_client.pin_message(
            chat_id=self._settings.telegram_target_chat_id,
            message_id=result.message_id,
        )
        if pinned:
            async with self._session_factory() as session:
                await session.execute(
                    update(PollMessage)
                    .where(PollMessage.batch_id == result.batch_id)
                    .where(PollMessage.telegram_message_id == result.message_id)
                    .values(pinned=True)
                )
                await session.commit()
        return PollCreationResult(
            batch_id=result.batch_id,
            message_id=result.message_id,
            poll_id=result.poll_id,
            pinned=pinned,
            notice_message_id=result.notice_message_id,
        )

    async def pin_created_notice(self, result: PollCreationResult) -> PollCreationResult:
        if (
            self._settings.telegram_target_chat_id is None
            or not self._settings.telegram_pin_poll
            or result.notice_message_id is None
        ):
            return result

        pinned = await self._telegram_client.pin_message(
            chat_id=self._settings.telegram_target_chat_id,
            message_id=result.notice_message_id,
        )
        if pinned:
            async with self._session_factory() as session:
                await session.execute(
                    update(PollMessage)
                    .where(PollMessage.batch_id == result.batch_id)
                    .where(PollMessage.telegram_message_id == result.notice_message_id)
                    .values(pinned=True)
                )
                await session.commit()
        return PollCreationResult(
            batch_id=result.batch_id,
            message_id=result.message_id,
            poll_id=result.poll_id,
            pinned=pinned,
            notice_message_id=result.notice_message_id,
        )

    async def cleanup_setup_messages(
        self,
        *,
        chat_id: int,
        message_ids: tuple[int, ...],
    ) -> CleanupResult:
        deleted_count = 0
        failed_count = 0
        for message_id in message_ids:
            deleted = await self._telegram_client.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
            if deleted:
                deleted_count += 1
            else:
                failed_count += 1
        return CleanupResult(deleted_count=deleted_count, failed_count=failed_count)

    async def _is_active_batch(self, *, session: AsyncSession, batch: PollBatch | None) -> bool:
        if batch is None:
            return False
        if batch.status in {"posting", "sent_unconfirmed"}:
            return True
        if batch.status != "posted":
            return False
        if self._settings.telegram_target_chat_id is None:
            return True

        message_ids = (
            await session.scalars(
                select(PollMessage.telegram_message_id)
                .where(PollMessage.batch_id == batch.id)
                .where(PollMessage.message_kind == "poll")
            )
        ).all()
        if not message_ids:
            return True

        for message_id in message_ids:
            if await self._telegram_client.message_exists(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message_id,
            ):
                return True

        await session.execute(
            update(PollMessage)
            .where(PollMessage.batch_id == batch.id)
            .values(cleanup_status="telegram_deleted")
        )
        batch.status = "deleted"
        await session.commit()
        return False


async def _mark_batch_status(*, session: AsyncSession, batch_id: int, status: str) -> None:
    await session.execute(update(PollBatch).where(PollBatch.id == batch_id).values(status=status))
    await session.commit()
