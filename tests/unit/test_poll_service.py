import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.config import Settings
from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import (
    PollBatch,
    PollMessage,
    PollOptionSnapshot,
    PollVote,
    PollVoteEvent,
)
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
        fail_send_on_call: int | None = None,
        send_error_once: Exception | None = None,
        fail_pin_once: bool = False,
        fail_delete_once: bool = False,
        existing_message_ids: set[int] | None = None,
    ) -> None:
        self.sent: list[PollDraft] = []
        self.sent_texts: list[str] = []
        self.pinned: list[int] = []
        self.deleted: list[int] = []
        self.fail_send_once = fail_send_once
        self.fail_send_on_call = fail_send_on_call
        self.send_call_count = 0
        self.send_error_once = send_error_once
        self.fail_pin_once = fail_pin_once
        self.fail_delete_once = fail_delete_once
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
        self.send_call_count += 1
        if self.send_error_once:
            error = self.send_error_once
            self.send_error_once = None
            raise error
        if self.fail_send_on_call == self.send_call_count:
            msg = "temporary Telegram failure"
            raise RuntimeError(msg)
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
        if self.fail_delete_once:
            self.fail_delete_once = False
            return False
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
async def test_poll_service_can_send_notice_before_poll_and_pin_poll(
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

    pinned_result = await service.pin_created_poll(result)

    assert pinned_result.pinned is True
    assert client.pinned == [43]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [message.message_kind for message in messages] == ["notice", "poll"]
    assert [message.pinned for message in messages] == [False, True]


@pytest.mark.asyncio
async def test_poll_service_pins_poll_when_notice_is_sent_inline(
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
    )

    assert result.notice_message_id == 42
    assert result.message_id == 43
    assert result.pinned is True
    assert client.pinned == [43]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [message.message_kind for message in messages] == ["notice", "poll"]
    assert [message.pinned for message in messages] == [False, True]


@pytest.mark.asyncio
async def test_poll_service_pins_all_created_polls_and_not_notice(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    saturday = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),
        include_notice=True,
        pin_after_send=False,
    )
    sunday = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 17), created_by_user_id=1),
        pin_after_send=False,
    )

    results = await service.pin_created_results((saturday, sunday))

    assert [result.pinned for result in results] == [True, True]
    assert client.pinned == [43, 44]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [
        (message.telegram_message_id, message.message_kind, message.pinned) for message in messages
    ] == [
        (42, "notice", False),
        (43, "poll", True),
        (44, "poll", True),
    ]


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
async def test_poll_service_reports_conflict_for_same_day_with_changed_setup(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    original = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    changed = PollSetup(
        service_date=date(2026, 5, 16),
        created_by_user_id=1,
        first_lift_location=StartLocation.VAKE,
        cancelled_lift_times=("15:30",),
    )

    await service.create_poll(original)

    conflicts = await service.find_active_conflicts((changed,))

    assert [conflict.service_date for conflict in conflicts] == [date(2026, 5, 16)]


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
async def test_poll_service_tracks_votes_and_reports_by_option_label(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )
    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    report = await service.render_vote_report((result.batch_id,))

    assert "2026-05-16" in report
    assert "🚲 10:00 · Дом Юстиции / Justice hall: @stas" in report

    async with db.session() as session:
        event = await session.scalar(select(PollVoteEvent))

    assert event is not None
    assert event.batch_id == result.batch_id
    assert event.telegram_message_id == result.message_id
    assert event.poll_id == result.poll_id
    assert event.telegram_user_id == 10
    assert event.username == "stas"
    assert event.old_option_ids == ""
    assert event.new_option_ids == "0"
    assert event.action == "voted"


@pytest.mark.asyncio
async def test_poll_service_appends_vote_events_without_duplicating_latest_vote(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )

    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(1, 0),
    )
    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(2,),
    )

    async with db.session() as session:
        votes = (await session.scalars(select(PollVote))).all()
        events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()

    assert [vote.option_ids for vote in votes] == ["2"]
    assert [(event.old_option_ids, event.new_option_ids, event.action) for event in events] == [
        ("", "0,1", "voted"),
        ("0,1", "2", "changed"),
    ]


@pytest.mark.asyncio
async def test_vote_report_ignores_check_answers_option(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )
    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(4,),
    )

    report = await service.render_vote_report((result.batch_id,))

    assert "Check answers" not in report
    assert "No tracked rider votes were found." in report


@pytest.mark.asyncio
async def test_poll_service_clears_vote_selection_with_empty_answer(
    db: SharedDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="veloexpress_bot.polls.service")
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )

    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0, 1),
    )
    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(),
    )

    async with db.session() as session:
        vote = await session.scalar(select(PollVote))
        events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()

    assert vote is not None
    assert vote.option_ids == ""
    assert [(event.old_option_ids, event.new_option_ids, event.action) for event in events] == [
        ("", "0,1", "voted"),
        ("0,1", "", "retracted"),
    ]

    report = await service.render_vote_report((result.batch_id,))

    assert "@stas" not in report
    assert "No tracked rider votes were found." in report
    assert any(
        "poll_vote_event action=retracted" in record.getMessage()
        and record.__dict__["telegram_user_id"] == 10
        and record.__dict__["old_option_ids"] == "0,1"
        and record.__dict__["new_option_ids"] == ""
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_check_answers_vote_is_audited_but_not_reported(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )

    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(4,),
    )

    async with db.session() as session:
        event = await session.scalar(select(PollVoteEvent))

    report = await service.render_vote_report((result.batch_id,))

    assert event is not None
    assert event.new_option_ids == "4"
    assert event.action == "voted"
    assert "Check answers" not in report
    assert "No tracked rider votes were found." in report


@pytest.mark.asyncio
async def test_recreate_report_uses_latest_vote_state_not_audit_history(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )

    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )
    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(1,),
    )

    recreate = await service.recreate_polls(
        (PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),)
    )

    assert "🚲 10:00 · Дом Юстиции / Justice hall: @stas" not in recreate.report_text
    assert "🚲 11:45 · от Ваке парка / Vake park: @stas" in recreate.report_text

    async with db.session() as session:
        events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()

    assert [(event.old_option_ids, event.new_option_ids) for event in events] == [
        ("", "0"),
        ("0", "1"),
    ]


@pytest.mark.asyncio
async def test_recreate_restores_old_batch_when_replacement_creation_fails(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    await service.create_poll(setup)
    client.fail_send_once = True

    with pytest.raises(RuntimeError, match="temporary Telegram failure"):
        await service.recreate_polls((setup,))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()

    assert [batch.status for batch in batches] == ["posted", "failed"]


@pytest.mark.asyncio
async def test_recreate_rolls_back_created_replacements_after_partial_failure(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    sunday = PollSetup(service_date=date(2026, 5, 17), created_by_user_id=1)
    await service.create_poll(saturday)
    await service.create_poll(sunday)
    client.fail_send_on_call = 4

    with pytest.raises(RuntimeError, match="temporary Telegram failure"):
        await service.recreate_polls((saturday, sunday))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [batch.status for batch in batches] == ["posted", "posted", "failed", "failed"]
    assert [(message.telegram_message_id, message.cleanup_status) for message in messages] == [
        (42, "not_applicable"),
        (43, "not_applicable"),
        (44, "deleted"),
        (45, "deleted"),
    ]
    assert client.deleted == [44, 45]


@pytest.mark.asyncio
async def test_recreate_reports_votes_supersedes_old_and_preserves_single_day_notice(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    original = await service.create_poll(setup, include_notice=True)
    await service.track_poll_answer(
        poll_id=original.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    result = await service.recreate_polls((setup,))

    assert [created.message_id for created in result.created] == [45]
    assert "🚲 10:00 · Дом Юстиции / Justice hall: @stas" in result.report_text

    cleanup = await service.cleanup_recreated_polls(result)

    assert cleanup.deleted_count == 1
    assert client.deleted == [43]

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [batch.status for batch in batches] == ["recreated", "posted"]
    assert batches[0].superseded_by_batch_id == batches[1].id
    assert [(message.telegram_message_id, message.cleanup_status) for message in messages[:2]] == [
        (42, "preserved"),
        (43, "deleted"),
    ]


@pytest.mark.asyncio
async def test_recreate_cleanup_failure_keeps_old_batch_conflict_visible(
    db: SharedDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="veloexpress_bot.polls.service")
    client = FakeTelegramClient(fail_delete_once=True)
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    await service.create_poll(setup)

    result = await service.recreate_polls((setup,))
    cleanup = await service.cleanup_recreated_polls(result)

    assert cleanup.failed_count == 1

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()
        message = await session.scalar(
            select(PollMessage).where(PollMessage.telegram_message_id == 42)
        )

    assert [batch.status for batch in batches] == ["cleanup_failed", "posted"]
    assert message is not None
    assert message.cleanup_status == "delete_failed"

    conflicts = await service.find_active_conflicts((setup,))

    assert {conflict.batch_id for conflict in conflicts} == {batches[0].id, batches[1].id}
    assert any(
        "poll_recreate_cleanup_failed" in record.getMessage()
        and record.__dict__["failed_count"] == 1
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_recreate_targets_previous_cleanup_failed_batch(db: SharedDatabase) -> None:
    client = FakeTelegramClient(fail_delete_once=True)
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    await service.create_poll(setup)
    first_recreate = await service.recreate_polls((setup,))
    await service.cleanup_recreated_polls(first_recreate)

    second_recreate = await service.recreate_polls((setup,))

    assert set(second_recreate.old_batch_ids) == {1, 2}

    await service.cleanup_recreated_polls(second_recreate)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()

    assert [batch.status for batch in batches] == ["recreated", "recreated", "posted"]


@pytest.mark.asyncio
async def test_recreate_without_conflicts_is_blocked(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )

    with pytest.raises(DuplicatePollError, match="No active polls"):
        await service.recreate_polls(
            (PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),)
        )

    assert client.sent == []


@pytest.mark.asyncio
async def test_recreate_ignores_deleted_telegram_poll(db: SharedDatabase) -> None:
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

    with pytest.raises(DuplicatePollError, match="No active polls"):
        await service.recreate_polls((setup,))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()

    assert len(client.sent) == 1
    assert batches[0].status == "deleted"


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
    assert len(messages) == 1
    assert messages[0].telegram_message_id == 43


@pytest.mark.asyncio
async def test_poll_service_creates_changed_setup_after_manual_delete(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    original = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    changed = PollSetup(
        service_date=date(2026, 5, 16),
        created_by_user_id=1,
        first_lift_location=StartLocation.VAKE,
        cancelled_lift_times=("15:30",),
    )

    await service.create_poll(original)
    assert client.existing_message_ids is not None
    client.existing_message_ids.clear()

    result = await service.create_poll(changed)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()

    assert result.message_id == 43
    assert [batch.status for batch in batches] == ["deleted", "posted"]


@pytest.mark.asyncio
async def test_reused_deleted_batch_reports_current_poll_votes(db: SharedDatabase) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    first = await service.create_poll(setup)
    await service.track_poll_answer(
        poll_id=first.poll_id or "",
        telegram_user_id=10,
        username="old",
        full_name="Old vote",
        option_ids=(1,),
    )
    assert client.existing_message_ids is not None
    client.existing_message_ids.clear()
    second = await service.create_poll(setup)
    await service.track_poll_answer(
        poll_id=second.poll_id or "",
        telegram_user_id=20,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    result = await service.recreate_polls((setup,))

    assert "🚲 10:00 · Дом Юстиции / Justice hall: @stas" in result.report_text
    assert "@old" not in result.report_text

    async with db.session() as session:
        snapshots = (await session.scalars(select(PollOptionSnapshot))).all()
        events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()

    assert {snapshot.poll_id for snapshot in snapshots} == {"poll-43", "poll-45"}
    assert [(event.poll_id, event.new_option_ids) for event in events] == [
        ("poll-42", "1"),
        ("poll-43", "0"),
    ]


@pytest.mark.asyncio
async def test_poll_service_reuses_recreated_batch_after_replacement_is_deleted(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)

    await service.create_poll(setup)
    recreate = await service.recreate_polls((setup,))
    await service.cleanup_recreated_polls(recreate)
    assert client.existing_message_ids is not None
    client.existing_message_ids.clear()

    result = await service.create_poll(setup)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert result.message_id == 45
    assert len(client.sent) == 3
    assert [batch.status for batch in batches] == ["posted", "deleted"]
    assert batches[0].superseded_by_batch_id is None
    assert [
        (message.batch_id, message.telegram_message_id, message.cleanup_status)
        for message in messages
    ] == [
        (2, 43, "telegram_deleted"),
        (2, 44, "telegram_deleted"),
        (1, 45, "not_applicable"),
    ]


@pytest.mark.asyncio
async def test_recreate_keeps_old_live_poll_conflict_visible_until_cleanup(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    await service.create_poll(setup)

    await service.recreate_polls((setup,))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()

    assert [batch.status for batch in batches] == ["cleanup_pending", "posted"]

    conflicts = await service.find_active_conflicts((setup,))

    assert {conflict.batch_id for conflict in conflicts} == {batch.id for batch in batches}


@pytest.mark.asyncio
async def test_cleanup_failed_batch_is_inactive_after_manual_poll_delete(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient(fail_delete_once=True, existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    await service.create_poll(setup)
    result = await service.recreate_polls((setup,))
    await service.cleanup_recreated_polls(result)
    assert client.existing_message_ids is not None
    client.existing_message_ids.discard(42)

    conflicts = await service.find_active_conflicts((setup,))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()

    assert [batch.status for batch in batches] == ["deleted", "posted"]
    assert {conflict.batch_id for conflict in conflicts} == {batches[1].id}


@pytest.mark.asyncio
async def test_poll_service_blocks_manual_duplicate_active_poll(db: SharedDatabase) -> None:
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

    with pytest.raises(DuplicatePollError):
        await service.create_poll(setup, allow_duplicate=True)

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert len(client.sent) == 1
    assert len(batches) == 1
    assert len(messages) == 1


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
