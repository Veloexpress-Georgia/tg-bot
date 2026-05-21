import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import (
    ACTIVE_POLL_BATCH_STATUSES,
    PollBatch,
    PollMessage,
    PollOptionSnapshot,
    PollVote,
    PollVoteEvent,
)
from veloexpress_bot.polls.defaults import StartLocation
from veloexpress_bot.polls.render import (
    PollDraft,
    PollRenderInput,
    render_payment_notice,
    render_poll,
)


class DuplicatePollError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

ACTIVE_BATCH_STATUSES = (*ACTIVE_POLL_BATCH_STATUSES, "cleanup_pending", "cleanup_failed")
REUSABLE_BATCH_STATUSES = {"deleted", "failed", "recreated"}


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


@dataclass(frozen=True)
class ActivePollConflict:
    batch_id: int
    service_date: date
    status: str


@dataclass(frozen=True)
class RecreatePollsResult:
    created: tuple[PollCreationResult, ...]
    report_text: str
    old_batch_ids: tuple[int, ...]
    replacement_by_date: dict[date, int]
    replaced_dates: tuple[date, ...]


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
        return bool(await self.find_active_conflicts((setup,)))

    async def find_active_conflicts(
        self, setups: tuple[PollSetup, ...]
    ) -> tuple[ActivePollConflict, ...]:
        if self._settings.telegram_target_chat_id is None:
            return ()

        started_at = perf_counter()
        service_dates = tuple({setup.service_date for setup in setups})
        async with self._session_factory() as session:
            batches = (
                await session.scalars(
                    select(PollBatch)
                    .where(PollBatch.environment == self._settings.app_env)
                    .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                    .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                    .where(PollBatch.service_date.in_(service_dates))
                    .where(PollBatch.status.in_(ACTIVE_BATCH_STATUSES))
                )
            ).all()

            conflicts: list[ActivePollConflict] = []
            for batch in batches:
                if await self._is_active_batch(session=session, batch=batch):
                    conflicts.append(
                        ActivePollConflict(
                            batch_id=batch.id,
                            service_date=batch.service_date,
                            status=batch.status,
                        )
                    )
            duration_ms = int((perf_counter() - started_at) * 1000)
            logger.info(
                "poll_active_conflict_check_done duration_ms=%s service_dates=%s "
                "candidate_count=%s conflict_count=%s",
                duration_ms,
                ",".join(item.isoformat() for item in service_dates),
                len(batches),
                len(conflicts),
                extra={
                    "duration_ms": duration_ms,
                    "service_dates": tuple(item.isoformat() for item in service_dates),
                    "candidate_count": len(batches),
                    "conflict_count": len(conflicts),
                },
            )
            return tuple(conflicts)

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
            active_statuses = (
                ACTIVE_POLL_BATCH_STATUSES if allow_duplicate else ACTIVE_BATCH_STATUSES
            )
            active_batches = await self._active_batches_for_setups(
                session=session,
                setups=(setup,),
                statuses=active_statuses,
            )
            if active_batches:
                raise DuplicatePollError("Poll batch already exists for this setup.")

            existing = None
            if not allow_duplicate:
                existing = await session.scalar(
                    select(PollBatch).where(PollBatch.idempotency_key == idempotency_key)
                )
            if await self._is_active_batch(session=session, batch=existing):
                raise DuplicatePollError("Poll batch already exists for this setup.")

            if existing and existing.status in REUSABLE_BATCH_STATUSES:
                batch = existing
                batch.status = "posting"
                batch.created_by_user_id = setup.created_by_user_id
                batch.first_lift_location = setup.first_lift_location.value
                batch.superseded_by_batch_id = None
                await session.execute(
                    delete(PollMessage).where(PollMessage.batch_id == existing.id)
                )
                await session.execute(
                    delete(PollOptionSnapshot).where(PollOptionSnapshot.batch_id == existing.id)
                )
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
            try:
                await session.flush()
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise DuplicatePollError("Poll batch already exists for this setup.") from error

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
                    pinned = await self._telegram_client.pin_message(
                        chat_id=self._settings.telegram_target_chat_id,
                        message_id=sent_message.message_id,
                    )
                batch.status = "posted"
                if notice_message is not None:
                    session.add(
                        PollMessage(
                            batch_id=batch.id,
                            telegram_message_id=notice_message.message_id,
                            poll_id=None,
                            message_kind="notice",
                            pinned=False,
                            cleanup_status="not_applicable",
                        )
                    )
                message = PollMessage(
                    batch_id=batch.id,
                    telegram_message_id=sent_message.message_id,
                    poll_id=sent_message.poll_id,
                    message_kind="poll",
                    pinned=pinned,
                    cleanup_status="not_applicable",
                )
                session.add(message)
                if sent_message.poll_id is not None:
                    session.add_all(
                        _option_snapshots(
                            batch_id=batch.id,
                            telegram_message_id=sent_message.message_id,
                            poll_id=sent_message.poll_id,
                            draft=draft,
                        )
                    )
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

    async def track_poll_answer(
        self,
        *,
        poll_id: str,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        option_ids: tuple[int, ...],
    ) -> None:
        now = datetime.now(UTC)
        encoded_option_ids = _encode_option_ids(option_ids)
        event: PollVoteEvent | None = None
        async with self._session_factory() as session:
            poll_snapshot = await _poll_snapshot(session=session, poll_id=poll_id)
            vote = await session.scalar(
                select(PollVote)
                .where(PollVote.poll_id == poll_id)
                .where(PollVote.telegram_user_id == telegram_user_id)
            )
            old_option_ids = vote.option_ids if vote is not None else ""
            if old_option_ids != encoded_option_ids:
                event = _vote_event(
                    poll_snapshot=poll_snapshot,
                    poll_id=poll_id,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    old_option_ids=old_option_ids,
                    new_option_ids=encoded_option_ids,
                    created_at=now,
                )
                session.add(event)
            if vote is None:
                session.add(
                    PollVote(
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        option_ids=encoded_option_ids,
                        updated_at=now,
                    )
                )
            else:
                vote.username = username
                vote.full_name = full_name
                vote.option_ids = encoded_option_ids
                vote.updated_at = now
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                await self._update_existing_poll_vote(
                    poll_id=poll_id,
                    telegram_user_id=telegram_user_id,
                    username=username,
                    full_name=full_name,
                    option_ids=encoded_option_ids,
                    updated_at=now,
                )
                return

        if event is None:
            _log_vote_noop(
                poll_id=poll_id,
                telegram_user_id=telegram_user_id,
                username=username,
                option_ids=encoded_option_ids,
            )
        else:
            _log_vote_event(event)

    async def recreate_polls(
        self,
        setups: tuple[PollSetup, ...],
    ) -> RecreatePollsResult:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to post polls."
            raise ValueError(msg)

        old_statuses: dict[int, str] = {}
        async with self._session_factory() as session:
            conflicts = await self._active_batches_for_setups(session=session, setups=setups)
            if not conflicts:
                raise DuplicatePollError("No active polls to recreate.")
            for batch in conflicts:
                old_statuses[batch.id] = batch.status
                batch.status = "cleanup_pending"
            await session.commit()
        logger.info(
            "poll_recreate_started old_batch_ids=%s service_dates=%s",
            ",".join(str(batch_id) for batch_id in old_statuses),
            ",".join(setup.service_date.isoformat() for setup in setups),
            extra={
                "old_batch_ids": tuple(old_statuses),
                "service_dates": tuple(setup.service_date.isoformat() for setup in setups),
            },
        )

        created: list[PollCreationResult] = []
        try:
            for index, setup in enumerate(setups):
                created.append(
                    await self.create_poll(
                        setup,
                        allow_duplicate=True,
                        include_notice=index == 0,
                        pin_after_send=False,
                    )
                )
            created = list(await self.pin_created_results(tuple(created)))
        except Exception:
            logger.exception(
                "poll_recreate_failed old_batch_ids=%s created_batch_ids=%s",
                ",".join(str(batch_id) for batch_id in old_statuses),
                ",".join(str(result.batch_id) for result in created),
                extra={
                    "old_batch_ids": tuple(old_statuses),
                    "created_batch_ids": tuple(result.batch_id for result in created),
                },
            )
            await self._rollback_created_replacements(tuple(created))
            await self._restore_replacing_batches(old_statuses)
            raise

        old_batch_ids = tuple(old_statuses)
        report_text = await self.render_vote_report(old_batch_ids)
        replacement_by_date = {
            setup.service_date: result.batch_id
            for setup, result in zip(setups, created, strict=True)
        }
        replaced_dates = tuple(setup.service_date for setup in setups)
        logger.info(
            "poll_recreate_created old_batch_ids=%s new_batch_ids=%s",
            ",".join(str(batch_id) for batch_id in old_batch_ids),
            ",".join(str(result.batch_id) for result in created),
            extra={
                "old_batch_ids": old_batch_ids,
                "new_batch_ids": tuple(result.batch_id for result in created),
            },
        )
        return RecreatePollsResult(
            created=tuple(created),
            report_text=report_text,
            old_batch_ids=old_batch_ids,
            replacement_by_date=replacement_by_date,
            replaced_dates=replaced_dates,
        )

    async def cleanup_recreated_polls(self, result: RecreatePollsResult) -> CleanupResult:
        return await self._cleanup_recreated_batches(
            old_batch_ids=result.old_batch_ids,
            replacement_by_date=result.replacement_by_date,
            replaced_dates=result.replaced_dates,
        )

    async def render_vote_report(self, batch_ids: tuple[int, ...]) -> str:
        if not batch_ids:
            return "No existing polls were replaced."

        async with self._session_factory() as session:
            batches = (
                await session.scalars(select(PollBatch).where(PollBatch.id.in_(batch_ids)))
            ).all()
            snapshots = (
                await session.scalars(
                    select(PollOptionSnapshot).where(PollOptionSnapshot.batch_id.in_(batch_ids))
                )
            ).all()
            poll_ids = tuple({snapshot.poll_id for snapshot in snapshots})
            votes = (
                await session.scalars(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))
            ).all()

        if not snapshots:
            return (
                "Recreated existing polls.\n\nNo tracked votes; the poll may predate vote tracking."
            )

        options_by_poll = _options_by_poll_id(snapshots)
        votes_by_poll: dict[str, list[PollVote]] = {}
        for vote in votes:
            if _decode_option_ids(vote.option_ids):
                votes_by_poll.setdefault(vote.poll_id, []).append(vote)

        lines = ["Recreated existing polls.", "", "Tracked votes before recreate:"]
        any_vote = False
        for batch in sorted(batches, key=lambda item: item.service_date):
            batch_snapshots = [snapshot for snapshot in snapshots if snapshot.batch_id == batch.id]
            lines.append(f"\n{batch.service_date:%Y-%m-%d}:")
            if not batch_snapshots:
                lines.append("- No tracked options for this poll.")
                continue
            poll_id = batch_snapshots[0].poll_id
            poll_votes = votes_by_poll.get(poll_id, [])
            if not poll_votes:
                lines.append("- No tracked votes; the poll may predate vote tracking.")
                continue
            grouped: dict[int, list[str]] = {}
            for vote in poll_votes:
                rider = _rider_label(vote)
                for option_id in _decode_option_ids(vote.option_ids):
                    grouped.setdefault(option_id, []).append(rider)
            for option_id, riders in sorted(grouped.items()):
                option = options_by_poll.get(poll_id, {}).get(option_id)
                if option is not None and option.lift_time is None:
                    continue
                option_label = option.label if option else f"Option {option_id}"
                lines.append(f"- {option_label}: {', '.join(sorted(riders))}")
                any_vote = True

        if not any_vote:
            lines.append("\nNo tracked rider votes were found.")
        return "\n".join(lines)

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

    async def pin_created_results(
        self, results: tuple[PollCreationResult, ...]
    ) -> tuple[PollCreationResult, ...]:
        return tuple([await self.pin_created_poll(result) for result in results])

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
        if batch.status not in {"posted", "cleanup_pending", "cleanup_failed"}:
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
            started_at = perf_counter()
            if await self._telegram_client.message_exists(
                chat_id=self._settings.telegram_target_chat_id,
                message_id=message_id,
            ):
                duration_ms = int((perf_counter() - started_at) * 1000)
                if duration_ms >= 500:
                    logger.warning(
                        "telegram_message_exists_slow duration_ms=%s batch_id=%s message_id=%s",
                        duration_ms,
                        batch.id,
                        message_id,
                        extra={
                            "duration_ms": duration_ms,
                            "batch_id": batch.id,
                            "message_id": message_id,
                        },
                    )
                return True
            duration_ms = int((perf_counter() - started_at) * 1000)
            if duration_ms >= 500:
                logger.warning(
                    "telegram_message_exists_slow duration_ms=%s batch_id=%s message_id=%s",
                    duration_ms,
                    batch.id,
                    message_id,
                    extra={
                        "duration_ms": duration_ms,
                        "batch_id": batch.id,
                        "message_id": message_id,
                    },
                )

        await session.execute(
            update(PollMessage)
            .where(PollMessage.batch_id == batch.id)
            .values(cleanup_status="telegram_deleted")
        )
        batch.status = "deleted"
        await session.commit()
        return False

    async def _active_batches_for_setups(
        self,
        *,
        session: AsyncSession,
        setups: tuple[PollSetup, ...],
        statuses: tuple[str, ...] = ACTIVE_BATCH_STATUSES,
    ) -> tuple[PollBatch, ...]:
        service_dates = tuple({setup.service_date for setup in setups})
        batches = (
            await session.scalars(
                select(PollBatch)
                .where(PollBatch.environment == self._settings.app_env)
                .where(PollBatch.chat_id == self._settings.telegram_target_chat_id)
                .where(PollBatch.thread_id == self._settings.telegram_target_thread_id)
                .where(PollBatch.service_date.in_(service_dates))
                .where(PollBatch.status.in_(statuses))
            )
        ).all()

        active_batches: list[PollBatch] = []
        for batch in batches:
            if await self._is_active_batch(session=session, batch=batch):
                active_batches.append(batch)
        return tuple(active_batches)

    async def _restore_replacing_batches(self, old_statuses: dict[int, str]) -> None:
        async with self._session_factory() as session:
            for batch_id, old_status in old_statuses.items():
                await session.execute(
                    update(PollBatch)
                    .where(PollBatch.id == batch_id)
                    .where(PollBatch.status == "cleanup_pending")
                    .values(status=old_status)
                )
            await session.commit()

    async def _update_existing_poll_vote(
        self,
        *,
        poll_id: str,
        telegram_user_id: int,
        username: str | None,
        full_name: str,
        option_ids: str,
        updated_at: datetime,
    ) -> None:
        async with self._session_factory() as session:
            poll_snapshot = await _poll_snapshot(session=session, poll_id=poll_id)
            vote = await session.scalar(
                select(PollVote)
                .where(PollVote.poll_id == poll_id)
                .where(PollVote.telegram_user_id == telegram_user_id)
            )
            old_option_ids = vote.option_ids if vote is not None else ""
            if old_option_ids != option_ids:
                session.add(
                    _vote_event(
                        poll_snapshot=poll_snapshot,
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        old_option_ids=old_option_ids,
                        new_option_ids=option_ids,
                        created_at=updated_at,
                    )
                )
            if vote is None:
                session.add(
                    PollVote(
                        poll_id=poll_id,
                        telegram_user_id=telegram_user_id,
                        username=username,
                        full_name=full_name,
                        option_ids=option_ids,
                        updated_at=updated_at,
                    )
                )
            else:
                vote.username = username
                vote.full_name = full_name
                vote.option_ids = option_ids
                vote.updated_at = updated_at
            await session.commit()

    async def _rollback_created_replacements(
        self,
        created: tuple[PollCreationResult, ...],
    ) -> None:
        if self._settings.telegram_target_chat_id is None:
            return

        cleanup_failed_batch_ids: set[int] = set()
        cleanup_by_message_id: dict[int, str] = {}
        for result in created:
            message_ids = tuple(
                message_id
                for message_id in (result.notice_message_id, result.message_id)
                if message_id is not None
            )
            for message_id in message_ids:
                deleted = await self._telegram_client.delete_message(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_id=message_id,
                )
                cleanup_by_message_id[message_id] = "deleted" if deleted else "delete_failed"
                if not deleted:
                    cleanup_failed_batch_ids.add(result.batch_id)

        async with self._session_factory() as session:
            for message_id, cleanup_status in cleanup_by_message_id.items():
                await session.execute(
                    update(PollMessage)
                    .where(PollMessage.telegram_message_id == message_id)
                    .values(cleanup_status=cleanup_status)
                )
            for result in created:
                if result.batch_id in cleanup_failed_batch_ids:
                    status = "cleanup_failed"
                    logger.warning(
                        "poll_recreate_rollback_cleanup_failed batch_id=%s message_ids=%s",
                        result.batch_id,
                        ",".join(
                            str(message_id)
                            for message_id in (result.notice_message_id, result.message_id)
                            if message_id is not None
                        ),
                        extra={
                            "batch_id": result.batch_id,
                            "message_ids": tuple(
                                message_id
                                for message_id in (result.notice_message_id, result.message_id)
                                if message_id is not None
                            ),
                        },
                    )
                else:
                    status = "failed"
                await session.execute(
                    update(PollBatch).where(PollBatch.id == result.batch_id).values(status=status)
                )
            await session.commit()

    async def _cleanup_recreated_batches(
        self,
        *,
        old_batch_ids: tuple[int, ...],
        replacement_by_date: dict[date, int],
        replaced_dates: tuple[date, ...],
    ) -> CleanupResult:
        if self._settings.telegram_target_chat_id is None:
            return CleanupResult(deleted_count=0, failed_count=0)

        deleted_count = 0
        failed_count = 0
        delete_notice = _replaces_full_weekend(replaced_dates)

        async with self._session_factory() as session:
            batches = (
                await session.scalars(select(PollBatch).where(PollBatch.id.in_(old_batch_ids)))
            ).all()
            messages = (
                await session.scalars(
                    select(PollMessage).where(PollMessage.batch_id.in_(old_batch_ids))
                )
            ).all()

            for message in messages:
                if message.message_kind == "notice" and not delete_notice:
                    message.cleanup_status = "preserved"
                    continue
                deleted = await self._telegram_client.delete_message(
                    chat_id=self._settings.telegram_target_chat_id,
                    message_id=message.telegram_message_id,
                )
                if deleted:
                    deleted_count += 1
                    message.cleanup_status = "deleted"
                else:
                    failed_count += 1
                    message.cleanup_status = "delete_failed"

            for batch in batches:
                batch_failed_count = sum(
                    1
                    for message in messages
                    if message.batch_id == batch.id and message.cleanup_status == "delete_failed"
                )
                batch.status = "cleanup_failed" if batch_failed_count else "recreated"
                batch.superseded_by_batch_id = replacement_by_date.get(batch.service_date)
            await session.commit()

        _log_recreated_cleanup_result(
            old_batch_ids=old_batch_ids,
            deleted_count=deleted_count,
            failed_count=failed_count,
        )
        return CleanupResult(deleted_count=deleted_count, failed_count=failed_count)


async def _mark_batch_status(*, session: AsyncSession, batch_id: int, status: str) -> None:
    await session.execute(update(PollBatch).where(PollBatch.id == batch_id).values(status=status))
    await session.commit()


def _option_snapshots(
    *,
    batch_id: int,
    telegram_message_id: int,
    poll_id: str,
    draft: PollDraft,
) -> list[PollOptionSnapshot]:
    return [
        PollOptionSnapshot(
            batch_id=batch_id,
            telegram_message_id=telegram_message_id,
            poll_id=poll_id,
            option_index=index,
            label=option,
            lift_time=_option_lift_time(option),
        )
        for index, option in enumerate(draft.options)
    ]


async def _poll_snapshot(
    *,
    session: AsyncSession,
    poll_id: str,
) -> PollOptionSnapshot | None:
    return await session.scalar(
        select(PollOptionSnapshot)
        .where(PollOptionSnapshot.poll_id == poll_id)
        .order_by(PollOptionSnapshot.option_index)
    )


def _vote_event(
    *,
    poll_snapshot: PollOptionSnapshot | None,
    poll_id: str,
    telegram_user_id: int,
    username: str | None,
    full_name: str,
    old_option_ids: str,
    new_option_ids: str,
    created_at: datetime,
) -> PollVoteEvent:
    return PollVoteEvent(
        poll_id=poll_id,
        batch_id=poll_snapshot.batch_id if poll_snapshot is not None else None,
        telegram_message_id=(
            poll_snapshot.telegram_message_id if poll_snapshot is not None else None
        ),
        telegram_user_id=telegram_user_id,
        username=username,
        full_name=full_name,
        old_option_ids=old_option_ids,
        new_option_ids=new_option_ids,
        action=_vote_action(old_option_ids=old_option_ids, new_option_ids=new_option_ids),
        created_at=created_at,
    )


def _log_vote_event(event: PollVoteEvent) -> None:
    log = logger.warning if event.action == "retracted" else logger.info
    log(
        (
            "poll_vote_event action=%s poll_id=%s batch_id=%s telegram_message_id=%s "
            "telegram_user_id=%s username=%s old_option_ids=%s new_option_ids=%s"
        ),
        event.action,
        event.poll_id,
        event.batch_id,
        event.telegram_message_id,
        event.telegram_user_id,
        event.username,
        event.old_option_ids,
        event.new_option_ids,
        extra={
            "vote_action": event.action,
            "poll_id": event.poll_id,
            "batch_id": event.batch_id,
            "telegram_message_id": event.telegram_message_id,
            "telegram_user_id": event.telegram_user_id,
            "username": event.username,
            "old_option_ids": event.old_option_ids,
            "new_option_ids": event.new_option_ids,
        },
    )


def _log_vote_noop(
    *,
    poll_id: str,
    telegram_user_id: int,
    username: str | None,
    option_ids: str,
) -> None:
    logger.debug(
        "poll_vote_noop poll_id=%s telegram_user_id=%s username=%s option_ids=%s",
        poll_id,
        telegram_user_id,
        username,
        option_ids,
        extra={
            "poll_id": poll_id,
            "telegram_user_id": telegram_user_id,
            "username": username,
            "option_ids": option_ids,
        },
    )


def _log_recreated_cleanup_result(
    *,
    old_batch_ids: tuple[int, ...],
    deleted_count: int,
    failed_count: int,
) -> None:
    log = logger.warning if failed_count else logger.info
    event_name = "poll_recreate_cleanup_failed" if failed_count else "poll_recreate_cleanup_done"
    log(
        "%s old_batch_ids=%s deleted_count=%s failed_count=%s",
        event_name,
        ",".join(str(batch_id) for batch_id in old_batch_ids),
        deleted_count,
        failed_count,
        extra={
            "old_batch_ids": old_batch_ids,
            "deleted_count": deleted_count,
            "failed_count": failed_count,
        },
    )


def _vote_action(*, old_option_ids: str, new_option_ids: str) -> str:
    if not old_option_ids and new_option_ids:
        return "voted"
    if old_option_ids and not new_option_ids:
        return "retracted"
    return "changed"


def _option_lift_time(option: str) -> str | None:
    parts = option.split()
    if len(parts) >= 2 and ":" in parts[1]:
        return parts[1]
    return None


def _encode_option_ids(option_ids: tuple[int, ...]) -> str:
    return ",".join(str(option_id) for option_id in sorted(set(option_ids)))


def _decode_option_ids(option_ids: str) -> tuple[int, ...]:
    if not option_ids:
        return ()
    return tuple(int(item) for item in option_ids.split(",") if item)


def _options_by_poll_id(
    snapshots: Sequence[PollOptionSnapshot],
) -> dict[str, dict[int, PollOptionSnapshot]]:
    options: dict[str, dict[int, PollOptionSnapshot]] = {}
    for snapshot in snapshots:
        options.setdefault(snapshot.poll_id, {})[snapshot.option_index] = snapshot
    return options


def _rider_label(vote: PollVote) -> str:
    return f"@{vote.username}" if vote.username else vote.full_name


def _replaces_full_weekend(replaced_dates: tuple[date, ...]) -> bool:
    weekdays = {item.weekday() for item in replaced_dates}
    return {5, 6} <= weekdays
