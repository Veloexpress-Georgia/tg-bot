import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.config import Settings
from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import (
    AdminBookingMonitor,
    ManualBookingCount,
    PollBatch,
    PollMessage,
    PollOptionSnapshot,
    PollScheduleHistory,
    PollVote,
    PollVoteEvent,
)
from veloexpress_bot.polls.defaults import StartLocation
from veloexpress_bot.polls.liftsignals import booking_deadline_at, lift_departure_at
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
        fail_unpin_once: bool = False,
        fail_delete_once: bool = False,
        existing_message_ids: set[int] | None = None,
    ) -> None:
        self.sent: list[PollDraft] = []
        self.sent_texts: list[str] = []
        self.sent_markups: list[InlineKeyboardMarkup | None] = []
        self.edited_texts: list[tuple[int, str]] = []
        self.edited_markups: list[InlineKeyboardMarkup | None] = []
        self.pinned: list[int] = []
        self.unpinned: list[int] = []
        self.deleted: list[int] = []
        self.operations: list[tuple[str, int]] = []
        self.fail_send_once = fail_send_once
        self.fail_send_on_call = fail_send_on_call
        self.send_call_count = 0
        self.send_error_once = send_error_once
        self.fail_pin_once = fail_pin_once
        self.fail_unpin_once = fail_unpin_once
        self.fail_delete_once = fail_delete_once
        self.existing_message_ids = existing_message_ids
        self.next_message_id = 42

    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> SentTextMessage:
        if chat_id == -100123:
            assert message_thread_id == 7
        else:
            assert message_thread_id is None
        self.sent_texts.append(text)
        self.sent_markups.append(reply_markup)
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

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        if self.existing_message_ids is not None and message_id not in self.existing_message_ids:
            return False
        self.edited_texts.append((message_id, text))
        self.edited_markups.append(reply_markup)
        return True

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool:
        assert chat_id == -100123
        if self.fail_pin_once:
            self.fail_pin_once = False
            msg = "post-send persistence failure"
            raise RuntimeError(msg)
        self.pinned.append(message_id)
        self.operations.append(("pin", message_id))
        return True

    async def unpin_message(self, *, chat_id: int, message_id: int) -> bool:
        assert chat_id == -100123
        if self.fail_unpin_once:
            self.fail_unpin_once = False
            return False
        self.unpinned.append(message_id)
        self.operations.append(("unpin", message_id))
        return True

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        # Group messages and an admin's private monitor are both deletable.
        assert chat_id == -100123 or chat_id > 0
        if self.fail_delete_once:
            self.fail_delete_once = False
            return False
        if message_id < 0:
            return False
        self.deleted.append(message_id)
        self.operations.append(("delete", message_id))
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


def _upcoming_weekend() -> tuple[date, date]:
    today = datetime.now(UTC).date()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    return saturday, saturday + timedelta(days=1)


async def test_poll_service_learns_schedule_after_two_consecutive_weekends(
    db: SharedDatabase,
) -> None:
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=FakeTelegramClient(),
    )
    starts_at_ten = ("8:30", "15:30")

    assert await service.suggested_cancelled_lift_times() == ("15:30",)

    assert await service.record_schedule_selection(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        cancelled_lift_times=starts_at_ten,
        created_by_user_id=1,
    )
    assert await service.suggested_cancelled_lift_times() == ("15:30",)

    assert await service.record_schedule_selection(
        service_dates=(date(2026, 5, 23), date(2026, 5, 24)),
        cancelled_lift_times=starts_at_ten,
        created_by_user_id=1,
    )
    assert await service.suggested_cancelled_lift_times() == starts_at_ten


async def test_poll_service_counts_recreate_as_same_weekend_observation(
    db: SharedDatabase,
) -> None:
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=FakeTelegramClient(),
    )
    weekend = (date(2026, 5, 16), date(2026, 5, 17))

    await service.record_schedule_selection(
        service_dates=weekend,
        cancelled_lift_times=("8:30", "15:30"),
        created_by_user_id=1,
    )
    await service.record_schedule_selection(
        service_dates=weekend,
        cancelled_lift_times=("15:30",),
        created_by_user_id=2,
    )

    async with db.session() as session:
        rows = (await session.scalars(select(PollScheduleHistory))).all()

    assert len(rows) == 1
    assert rows[0].service_week_start == date(2026, 5, 16)
    assert rows[0].first_lift_time == "8:30"
    assert rows[0].created_by_user_id == 2


async def test_admin_booking_monitor_controls_manual_counts_and_tracks_votes(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, sunday = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1),
        pin_after_send=False,
    )
    await service.create_poll(
        PollSetup(service_date=sunday, created_by_user_id=1),
        pin_after_send=False,
    )

    monitor_message_id = await service.open_booking_monitor(
        admin_user_id=1,
        private_chat_id=1,
    )

    assert client.sent_markups[-1] is not None
    assert client.sent_texts[-1].startswith("📊 Booking monitor · ")

    adjustment = await service.adjust_manual_booking(
        service_date=saturday,
        lift_time="8:30",
        delta=1,
        admin_user_id=1,
    )

    assert adjustment.manual_count == 1
    availability_updates = [
        text
        for message_id, text in client.edited_texts
        if message_id == poll.availability_message_id
    ]
    monitor_updates = [
        text for message_id, text in client.edited_texts if message_id == monitor_message_id
    ]
    assert "8:30 — <b>1/10</b> · needs 4 more" in availability_updates[-1]
    assert "8:30 — 1/10 · needs 4 more · 1 manual" in monitor_updates[-1]

    await service.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    monitor_updates = [
        text for message_id, text in client.edited_texts if message_id == monitor_message_id
    ]
    assert "8:30 — 2/10 · needs 3 more · 1 manual" in monitor_updates[-1]

    detail = await service.lift_detail(service_date=saturday, lift_time="8:30")
    assert detail is not None
    status, riders = detail
    assert (status.vote_count, status.manual_count, status.total_count) == (1, 1, 2)
    assert [(rider.label, rider.paid) for rider in riders] == [("@stas", False)]

    async with db.session() as session:
        booking = await session.scalar(select(ManualBookingCount))
        monitor = await session.scalar(select(AdminBookingMonitor))
    assert booking is not None
    assert booking.count == 1
    assert monitor is not None
    assert monitor.telegram_message_id == monitor_message_id


async def test_cancel_lift_marks_board_and_tags_voters(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    await service.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    await service.cancel_lift(service_date=saturday, lift_time="8:30", admin_user_id=1)

    notice = client.sent_texts[-1]
    assert "❌ 8:30" in notice
    assert "tg://user?id=10" in notice
    availability_updates = [
        text
        for message_id, text in client.edited_texts
        if message_id == poll.availability_message_id
    ]
    assert "8:30 — ❌ cancelled" in availability_updates[-1]

    detail = await service.lift_detail(service_date=saturday, lift_time="8:30")
    assert detail is not None
    status, _ = detail
    assert status.cancelled is True

    await service.restore_lift(service_date=saturday, lift_time="8:30", admin_user_id=1)
    restored = await service.lift_detail(service_date=saturday, lift_time="8:30")
    assert restored is not None
    assert restored[0].cancelled is False


async def test_cancel_day_retires_batch_and_clears_monitor(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    await service.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    await service.cancel_day(service_date=saturday, admin_user_id=1)

    notice = client.sent_texts[-1]
    assert "❌ All lifts on" in notice
    assert "tg://user?id=10" in notice

    async with db.session() as session:
        batch = await session.scalar(select(PollBatch))
    assert batch is not None
    assert batch.status == "cancelled"

    # Day leaves the monitor; nothing active to manage.
    view = await service.booking_monitor_view(admin_user_id=1, selected_service_date=saturday)
    assert "No active lift polls." in view.text

    # A fresh poll for the same date is allowed and starts clean.
    await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    detail = await service.lift_detail(service_date=saturday, lift_time="8:30")
    assert detail is not None
    assert detail[0].cancelled is False


async def test_cancelled_day_board_survives_a_late_vote(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )

    await service.cancel_day(service_date=saturday, admin_user_id=1)

    # The Telegram poll stays votable, so a late vote must not rebuild the live board.
    await service.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    board_updates = [
        text
        for message_id, text in client.edited_texts
        if message_id == poll.availability_message_id
    ]
    assert "❌ All lifts cancelled." in board_updates[-1]
    assert "needs" not in board_updates[-1]


async def test_cancel_day_releases_the_pin(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
    )
    assert poll.pinned is True

    await service.cancel_day(service_date=saturday, admin_user_id=1)

    assert poll.message_id in client.unpinned
    async with db.session() as session:
        message = await session.scalar(
            select(PollMessage).where(PollMessage.telegram_message_id == poll.message_id)
        )
    assert message is not None
    assert message.pinned is False


async def _fill_lift(service: PollPostingService, poll_id: str, *, riders: int) -> None:
    for index in range(riders):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=100 + index,
            username=f"rider{index}",
            full_name=f"Rider {index}",
            option_ids=(0,),
        )


async def test_lift_signals_announce_the_threshold_once_and_tag_the_riders(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    poll_id = poll.poll_id or ""

    await _fill_lift(service, poll_id, riders=4)
    assert await service.evaluate_lift_signals() == ()

    await _fill_lift(service, poll_id, riders=5)
    events = await service.evaluate_lift_signals()
    assert [event.kind for event in events] == ["confirmed"]

    notice = client.sent_texts[-1]
    assert "8:30" in notice
    assert "is running — 5 riders booked." in notice
    assert "tg://user?id=104" in notice

    # A second tick must not repeat the announcement.
    before = len(client.sent_texts)
    assert await service.evaluate_lift_signals() == ()
    assert len(client.sent_texts) == before


async def test_lift_signals_report_a_settled_undershoot(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    poll_id = poll.poll_id or ""
    await _fill_lift(service, poll_id, riders=5)

    start = datetime.now(UTC)
    await service.evaluate_lift_signals(now=start)

    await service.track_poll_answer(
        poll_id=poll_id,
        telegram_user_id=104,
        username="rider4",
        full_name="Rider 4",
        option_ids=(),
    )

    assert await service.evaluate_lift_signals(now=start + timedelta(minutes=1)) == ()
    events = await service.evaluate_lift_signals(now=start + timedelta(minutes=12))
    assert [event.kind for event in events] == ["undershoot"]
    assert "is short — 4/5 riders" in client.sent_texts[-1]


async def test_lift_signals_ping_only_the_first_lift_before_departure(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    poll_id = poll.poll_id or ""
    await _fill_lift(service, poll_id, riders=5)

    # Fill 10:00 too, so the test proves only the opener is pinged.
    for index in range(5):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=200 + index,
            username=f"late{index}",
            full_name=f"Late {index}",
            option_ids=(1,),
        )

    await service.evaluate_lift_signals()

    departure = lift_departure_at(saturday, "8:30", zone=ZoneInfo("Asia/Tbilisi"))
    events = await service.evaluate_lift_signals(now=departure - timedelta(minutes=20))
    assert [(event.kind, event.lift_time) for event in events] == [("departure", "8:30")]
    assert client.sent_texts[-1].startswith("🚐 First lift of the day")

    assert await service.evaluate_lift_signals(now=departure - timedelta(minutes=10)) == ()


async def test_the_deadline_reminder_goes_out_once_and_names_no_one(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    # Three riders on 8:30: short of the minimum, so the day is worth a nudge.
    await _fill_lift(service, poll.poll_id or "", riders=3)

    deadline = booking_deadline_at(saturday, "20:00", zone=ZoneInfo("Asia/Tbilisi"))
    assert await service.evaluate_lift_signals(now=deadline - timedelta(hours=3)) == ()
    assert not any("⏳ Tomorrow" in text for text in client.sent_texts)

    await service.evaluate_lift_signals(now=deadline - timedelta(hours=1))
    reminders = [text for text in client.sent_texts if "⏳ Tomorrow" in text]
    assert len(reminders) == 1
    assert "book and pay by 20:00" in reminders[0]
    assert "8:30 — 3/5 · needs 2 more" in reminders[0]
    assert "tg://user?id=" not in reminders[0], "a nudge tags nobody"

    await service.evaluate_lift_signals(now=deadline - timedelta(minutes=30))
    assert len([text for text in client.sent_texts if "⏳ Tomorrow" in text]) == 1


async def test_the_deadline_reminder_stays_fresh_and_never_claims_booking_is_shut(
    db: SharedDatabase,
) -> None:
    """People book after the reminder lands, and this is the message they act on.

    Nothing actually closes at the deadline — the Telegram poll stays open and the
    bot enforces nothing — so past it only the call to action changes.
    """
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    poll_id = poll.poll_id or ""
    await _fill_lift(service, poll_id, riders=3)

    deadline = booking_deadline_at(saturday, "20:00", zone=ZoneInfo("Asia/Tbilisi"))
    reminder_id = client.next_message_id
    await service.evaluate_lift_signals(now=deadline - timedelta(hours=1))
    reminders = [text for text in client.sent_texts if "⏳ Tomorrow" in text]
    assert len(reminders) == 1
    assert "8:30 — 3/5 · needs 2 more" in reminders[0]

    # Two more riders arrive before the deadline: the message must follow.
    await _fill_lift(service, poll_id, riders=5)
    await service.evaluate_lift_signals(now=deadline - timedelta(minutes=30))
    edits = [text for message_id, text in client.edited_texts if message_id == reminder_id]
    assert "8:30 — 5 riders · running" in edits[-1]
    assert len([text for text in client.sent_texts if "⏳ Tomorrow" in text]) == 1

    await service.evaluate_lift_signals(now=deadline + timedelta(minutes=5))
    edits = [text for message_id, text in client.edited_texts if message_id == reminder_id]
    assert "deadline has passed. Late changes are up to Misho." in edits[-1]
    assert "closed" not in edits[-1]


async def test_no_deadline_reminder_when_every_lift_is_already_running(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    poll = await service.create_poll(
        PollSetup(
            service_date=saturday,
            created_by_user_id=1,
            cancelled_lift_times=("10:00", "11:45", "13:30", "15:30"),
        ),
        pin_after_send=False,
    )
    await _fill_lift(service, poll.poll_id or "", riders=5)

    deadline = booking_deadline_at(saturday, "20:00", zone=ZoneInfo("Asia/Tbilisi"))
    await service.evaluate_lift_signals(now=deadline - timedelta(hours=1))

    assert not any("⏳ Tomorrow" in text for text in client.sent_texts)


async def test_new_polls_reopen_the_monitor_at_the_bottom_of_the_admin_chat(
    db: SharedDatabase,
) -> None:
    """Editing in place would leave the monitor buried where it was last opened."""
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    monitor_message_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    assert monitor_message_id is not None

    result = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    await service.pin_created_results((result,))

    assert monitor_message_id in client.deleted
    async with db.session() as session:
        monitor = await session.scalar(select(AdminBookingMonitor))
    assert monitor is not None
    assert monitor.telegram_message_id != monitor_message_id


async def test_a_posted_notice_picks_up_changed_money_rules(db: SharedDatabase) -> None:
    """A price or rule change must reach notices already posted.

    Recreating the polls would carry the new text but throw away live votes, so
    the notice is re-rendered in place instead.
    """
    service_settings = settings()
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=service_settings,
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    result = await service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        include_notice=True,
        pin_after_send=False,
    )
    assert result.notice_message_id is not None
    service_settings.payment_price_gel = 20

    assert await service.refresh_poll_notices() == 1

    edits = [
        text for message_id, text in client.edited_texts if message_id == result.notice_message_id
    ]
    assert "20 GEL per seat." in edits[-1]
    # Once per day per process: a second pass would spend an API call to change
    # nothing, and Telegram edits are silent anyway.
    assert await service.refresh_poll_notices() == 0


async def test_a_revived_day_does_not_inherit_hand_added_riders(db: SharedDatabase) -> None:
    """A retired day takes its manual bookings with it.

    Nobody can tell whether a hand-added rider still intends to come, so leaving
    them behind opens a fresh poll with seats taken by people nobody can name.
    """
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    setup = PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",))
    await service.create_poll(setup, pin_after_send=False)
    for _ in range(3):
        await service.adjust_manual_booking(
            service_date=saturday,
            lift_time="10:00",
            delta=1,
            admin_user_id=1,
        )

    await service.cancel_day(service_date=saturday, admin_user_id=1)
    await service.create_poll(setup, pin_after_send=False)

    detail = await service.lift_detail(service_date=saturday, lift_time="10:00")
    assert detail is not None
    assert detail[0].manual_count == 0
    async with db.session() as session:
        assert await session.scalar(select(ManualBookingCount)) is None


async def test_a_reposted_day_announces_itself_again(db: SharedDatabase) -> None:
    """Cancelling a day and posting a fresh poll must not inherit sent notices."""
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    saturday, _ = _upcoming_weekend()
    setup = PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",))
    poll = await service.create_poll(setup, pin_after_send=False)
    await _fill_lift(service, poll.poll_id or "", riders=5)
    assert [event.kind for event in await service.evaluate_lift_signals()] == ["confirmed"]

    await service.cancel_day(service_date=saturday, admin_user_id=1)
    reposted = await service.create_poll(setup, pin_after_send=False)
    await _fill_lift(service, reposted.poll_id or "", riders=5)

    assert [event.kind for event in await service.evaluate_lift_signals()] == ["confirmed"]


async def test_manual_booking_count_cannot_go_below_zero(db: SharedDatabase) -> None:
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=FakeTelegramClient(),
    )
    await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),
        pin_after_send=False,
    )

    with pytest.raises(ValueError, match="No manual bookings to remove"):
        await service.adjust_manual_booking(
            service_date=date(2026, 5, 16),
            lift_time="8:30",
            delta=-1,
            admin_user_id=1,
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

    assert result.availability_message_id == 42
    assert result.message_id == 43
    assert result.poll_id == "poll-43"
    assert result.pinned is True
    assert client.pinned == [43]
    assert "15:30" not in "\n".join(client.sent[0].options)
    assert "15:30" not in client.sent_texts[0]

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch))).all()
        messages = (await session.scalars(select(PollMessage))).all()

    assert len(batches) == 1
    assert batches[0].status == "posted"
    assert [(message.telegram_message_id, message.message_kind) for message in messages] == [
        (42, "availability"),
        (43, "poll"),
    ]


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
        message = await session.scalar(
            select(PollMessage).where(PollMessage.message_kind == "poll")
        )
        assert message is not None
        assert message.pinned is False

    pinned_result = await service.pin_created_poll(result)

    assert pinned_result.pinned is True
    assert client.pinned == [43]

    async with db.session() as session:
        message = await session.scalar(
            select(PollMessage).where(PollMessage.message_kind == "poll")
        )
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
    assert result.availability_message_id == 43
    assert result.message_id == 44
    assert client.sent_texts[0].startswith("📍 All lifts: Vake Park.")
    assert "Wait for the ✅ message, then pay" in client.sent_texts[0]
    assert "🚐 Availability · Sat, 16 May" in client.sent_texts[1]
    assert client.sent[0].question == "🚐 Saturday · May 16"

    pinned_result = await service.pin_created_poll(result)

    assert pinned_result.pinned is True
    assert client.pinned == [44]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [message.message_kind for message in messages] == [
        "notice",
        "availability",
        "poll",
    ]
    assert [message.pinned for message in messages] == [False, False, True]


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
    assert result.availability_message_id == 43
    assert result.message_id == 44
    assert result.pinned is True
    assert client.pinned == [44]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [message.message_kind for message in messages] == [
        "notice",
        "availability",
        "poll",
    ]
    assert [message.pinned for message in messages] == [False, False, True]


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
    assert client.pinned == [44, 46]

    async with db.session() as session:
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [
        (message.telegram_message_id, message.message_kind, message.pinned) for message in messages
    ] == [
        (42, "notice", False),
        (43, "availability", False),
        (44, "poll", True),
        (45, "availability", False),
        (46, "poll", True),
    ]


@pytest.mark.asyncio
async def test_new_poll_batch_unpins_older_bot_managed_polls(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    older = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 9), created_by_user_id=1)
    )
    saturday = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1),
        pin_after_send=False,
    )
    sunday = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 17), created_by_user_id=1),
        pin_after_send=False,
    )

    current = await service.pin_created_results((saturday, sunday))

    assert client.unpinned == [older.message_id]
    assert client.operations.index(("unpin", older.message_id)) > client.operations.index(
        ("pin", current[-1].message_id)
    )
    async with db.session() as session:
        messages = (
            await session.scalars(
                select(PollMessage)
                .where(PollMessage.message_kind == "poll")
                .order_by(PollMessage.telegram_message_id)
            )
        ).all()
    assert [message.pinned for message in messages] == [False, True, True]


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

    assert result.availability_message_id == 43
    assert result.message_id == 44
    assert len(batches) == 1
    assert batches[0].id == failed_batch.id
    assert batches[0].status == "posted"
    assert len(messages) == 2
    assert client.deleted == [42]


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
        message = await session.scalar(
            select(PollMessage).where(PollMessage.message_kind == "poll")
        )

    assert batch is not None
    assert batch.status == "deleted"
    assert message is not None
    assert message.cleanup_status == "telegram_deleted"


@pytest.mark.asyncio
async def test_poll_service_removes_availability_when_poll_is_manually_deleted(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    result = await service.create_poll(setup)
    assert client.existing_message_ids is not None
    client.existing_message_ids.discard(result.message_id)

    assert await service.has_existing_active_poll(setup) is False

    async with db.session() as session:
        availability_message = await session.scalar(
            select(PollMessage).where(PollMessage.message_kind == "availability")
        )

    assert availability_message is not None
    assert availability_message.cleanup_status == "deleted"
    assert client.deleted == [result.availability_message_id]


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
    assert client.existing_message_ids == {42, 43, 44}
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
    assert "🚲 8:30: @stas" in report

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
async def test_poll_service_updates_daily_availability_after_vote_changes(
    db: SharedDatabase,
) -> None:
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
            cancelled_lift_times=("15:30",),
        )
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
        telegram_user_id=11,
        username="anna",
        full_name="Anna",
        option_ids=(0,),
    )

    assert client.edited_texts[-1][0] == result.availability_message_id
    status = client.edited_texts[-1][1]
    assert "8:30 — <b>2/10</b> · needs 3 more" in status
    assert "10:00 — <b>1/10</b> · needs 4 more" in status
    assert "15:30" not in status
    assert "Check answers" not in status


@pytest.mark.asyncio
async def test_poll_service_recovers_manually_deleted_availability_on_next_vote(
    db: SharedDatabase,
) -> None:
    client = FakeTelegramClient(existing_message_ids=set())
    service = PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )
    result = await service.create_poll(
        PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    )
    assert client.existing_message_ids is not None
    client.existing_message_ids.discard(result.availability_message_id)

    await service.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username="stas",
        full_name="Stas",
        option_ids=(0,),
    )

    async with db.session() as session:
        availability_message = await session.scalar(
            select(PollMessage).where(PollMessage.message_kind == "availability")
        )

    assert availability_message is not None
    assert availability_message.telegram_message_id == 44
    assert "8:30 — <b>1/10</b> · needs 4 more" in client.sent_texts[-1]


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
        option_ids=(5,),
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
    assert "8:30 — <b>0/10</b> · needs 5 more" in client.edited_texts[-1][1]
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
        option_ids=(5,),
    )

    async with db.session() as session:
        event = await session.scalar(select(PollVoteEvent))

    report = await service.render_vote_report((result.batch_id,))

    assert event is not None
    assert event.new_option_ids == "5"
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

    assert "🚲 8:30: @stas" not in recreate.report_text
    assert "🚲 10:00: @stas" in recreate.report_text

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
        (44, "not_applicable"),
        (45, "not_applicable"),
        (46, "deleted"),
        (47, "deleted"),
        (48, "deleted"),
    ]
    assert client.deleted == [49, 46, 47, 48]


@pytest.mark.asyncio
async def test_recreate_replaces_the_notice_instead_of_leaving_a_duplicate(
    db: SharedDatabase,
) -> None:
    """Recreate always posts a fresh notice, so keeping the old one duplicated it."""
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

    assert [created.message_id for created in result.created] == [47]
    assert "🚲 8:30: @stas" in result.report_text

    cleanup = await service.cleanup_recreated_polls(result)

    assert cleanup.deleted_count == 3
    assert client.deleted == [42, 43, 44]
    assert client.unpinned == [44]
    assert client.operations.index(("unpin", 44)) < client.operations.index(("delete", 44))

    async with db.session() as session:
        batches = (await session.scalars(select(PollBatch).order_by(PollBatch.id))).all()
        messages = (
            await session.scalars(select(PollMessage).order_by(PollMessage.telegram_message_id))
        ).all()

    assert [batch.status for batch in batches] == ["recreated", "posted"]
    assert batches[0].superseded_by_batch_id == batches[1].id
    assert [(message.telegram_message_id, message.cleanup_status) for message in messages[:2]] == [
        (42, "deleted"),
        (43, "deleted"),
    ]


@pytest.mark.asyncio
async def test_recreate_keeps_pinned_poll_when_unpin_fails(db: SharedDatabase) -> None:
    service_settings = settings()
    client = FakeTelegramClient(fail_unpin_once=True)
    service = PollPostingService(
        settings=service_settings,
        session_factory=db.session,
        telegram_client=client,
    )
    setup = PollSetup(service_date=date(2026, 5, 16), created_by_user_id=1)
    original = await service.create_poll(setup)
    service_settings.telegram_pin_poll = False

    result = await service.recreate_polls((setup,))
    cleanup = await service.cleanup_recreated_polls(result)

    assert cleanup.failed_count == 1
    assert original.message_id not in client.deleted
    async with db.session() as session:
        message = await session.scalar(
            select(PollMessage).where(PollMessage.telegram_message_id == original.message_id)
        )
        old_batch = await session.get(PollBatch, original.batch_id)
    assert message is not None
    assert message.cleanup_status == "unpin_failed"
    assert old_batch is not None
    assert old_batch.status == "cleanup_failed"


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

    assert result.availability_message_id == 44
    assert result.message_id == 45
    assert len(client.sent) == 2
    assert len(batches) == 1
    assert batches[0].status == "posted"
    assert len(messages) == 2
    assert {message.telegram_message_id for message in messages} == {44, 45}


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

    assert result.availability_message_id == 44
    assert result.message_id == 45
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

    assert "🚲 8:30: @stas" in result.report_text
    assert "@old" not in result.report_text

    async with db.session() as session:
        snapshots = (await session.scalars(select(PollOptionSnapshot))).all()
        events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()

    assert {snapshot.poll_id for snapshot in snapshots} == {"poll-45", "poll-48"}
    assert [(event.poll_id, event.new_option_ids) for event in events] == [
        ("poll-43", "1"),
        ("poll-45", "0"),
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

    assert result.availability_message_id == 47
    assert result.message_id == 48
    assert len(client.sent) == 3
    assert [batch.status for batch in batches] == ["posted", "deleted"]
    assert batches[0].superseded_by_batch_id is None
    assert [
        (message.batch_id, message.telegram_message_id, message.cleanup_status)
        for message in messages
    ] == [
        (2, 44, "not_applicable"),
        (2, 45, "deleted"),
        (2, 46, "telegram_deleted"),
        (1, 47, "not_applicable"),
        (1, 48, "not_applicable"),
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
    assert len(messages) == 2


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
