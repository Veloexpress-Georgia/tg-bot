from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.config import Settings
from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import PaymentClaim, PaymentsBoard
from veloexpress_bot.payments.service import (
    ALREADY_CLAIMED_TEXT,
    NOT_BOOKED_TEXT,
    NOT_CLAIMED_YET_TEXT,
    PaymentsService,
)
from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.polls.service import (
    PollPostingService,
    PollSetup,
    SentPollMessage,
    SentTextMessage,
)

CHAT_ID = -100123
LIFT_THREAD = 7
PAYMENTS_THREAD = 2


@dataclass
class SentRecord:
    thread_id: int | None
    text: str
    markup: InlineKeyboardMarkup | None
    message_id: int


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[SentRecord] = []
        self.edits: list[tuple[int, str]] = []
        self.deleted: list[int] = []
        self.next_message_id = 500

    def payments_sends(self) -> list[SentRecord]:
        return [record for record in self.sent if record.thread_id == PAYMENTS_THREAD]

    async def send_text(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> SentTextMessage:
        self.next_message_id += 1
        self.sent.append(
            SentRecord(
                thread_id=message_thread_id,
                text=text,
                markup=reply_markup,
                message_id=self.next_message_id,
            )
        )
        return SentTextMessage(message_id=self.next_message_id)

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage:
        self.next_message_id += 1
        return SentPollMessage(
            message_id=self.next_message_id,
            poll_id=f"poll-{self.next_message_id}",
        )

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        self.edits.append((message_id, text))
        return True

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool:
        return True

    async def unpin_message(self, *, chat_id: int, message_id: int) -> bool:
        return True

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool:
        return True


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


def settings(*, payments_thread: int | None = PAYMENTS_THREAD) -> Settings:
    settings_factory = cast(Any, Settings)
    return settings_factory(
        _env_file=None,
        app_env="test",
        telegram_bot_token="token",
        telegram_target_chat_id=CHAT_ID,
        telegram_target_thread_id=LIFT_THREAD,
        telegram_payments_thread_id=payments_thread,
        telegram_admin_ids=(1,),
        payment_price_gel=15,
    )


def _saturday() -> date:
    today = datetime.now(UTC).date()
    return today + timedelta(days=(5 - today.weekday()) % 7)


async def _setup(
    db: SharedDatabase,
    *,
    payments_thread: int | None = PAYMENTS_THREAD,
) -> tuple[PollPostingService, PaymentsService, FakeTelegramClient, str, date]:
    client = FakeTelegramClient()
    resolved = settings(payments_thread=payments_thread)
    poll_service = PollPostingService(
        settings=resolved,
        session_factory=db.session,
        telegram_client=client,
    )
    payments_service = PaymentsService(
        settings=resolved,
        session_factory=db.session,
        telegram_client=client,
    )
    saturday = _saturday()
    poll = await poll_service.create_poll(
        PollSetup(service_date=saturday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    return poll_service, payments_service, client, poll.poll_id or "", saturday


async def _vote(
    poll_service: PollPostingService, poll_id: str, user_id: int, *options: int
) -> None:
    await poll_service.track_poll_answer(
        poll_id=poll_id,
        telegram_user_id=user_id,
        username=f"rider{user_id}",
        full_name=f"Rider {user_id}",
        option_ids=tuple(options),
    )


async def _fill(
    poll_service: PollPostingService,
    poll_id: str,
    *options: int,
    riders: int = 5,
    first_user_id: int = 100,
) -> None:
    """Telegram replaces a user's whole answer, so each lift needs its own riders."""
    for index in range(riders):
        await _vote(poll_service, poll_id, first_user_id + index, *options)


async def test_no_board_appears_until_a_lift_reaches_the_minimum(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, _ = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=4)

    await payments.sync_boards()

    assert client.payments_sends() == []


async def test_the_board_appears_once_a_lift_runs_and_is_then_edited_in_place(
    db: SharedDatabase,
) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.sync_boards()
    boards = client.payments_sends()
    assert len(boards) == 1
    assert "Running: 8:30" in boards[0].text
    assert boards[0].markup is not None

    # A second tick must edit the same message, never post another board.
    await payments.sync_boards()
    assert len(client.payments_sends()) == 1
    assert boards[0].message_id in [message_id for message_id, _ in client.edits]

    async with db.session() as session:
        stored = await session.scalar(select(PaymentsBoard))
    assert stored is not None
    assert stored.service_date == saturday


async def test_a_claim_posts_the_rider_line_with_amount_and_lifts(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, poll_id, 1, riders=5, first_user_id=200)
    # Rider 100 books both lifts, so the claim covers two seats.
    await _vote(poll_service, poll_id, 100, 0, 1)
    await payments.sync_boards()

    notice = await payments.claim(
        service_date=saturday,
        telegram_user_id=100,
        username="stas",
        full_name="Stas",
    )

    assert "30 GEL" in notice
    posted = client.payments_sends()[-1]
    assert "30 GEL" in posted.text
    assert "8:30, 10:00" in posted.text
    assert "tg://user?id=100" in posted.text

    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.seats == 2
    assert claim.posted_message_id == posted.message_id


async def test_a_rider_who_is_not_booked_cannot_claim(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    before = len(client.payments_sends())

    notice = await payments.claim(
        service_date=saturday,
        telegram_user_id=999,
        username="stranger",
        full_name="Stranger",
    )

    assert notice == NOT_BOOKED_TEXT
    assert len(client.payments_sends()) == before
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None


async def test_claiming_twice_says_so_instead_of_posting_again(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    after_first = len(client.payments_sends())
    notice = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert notice == ALREADY_CLAIMED_TEXT
    assert len(client.payments_sends()) == after_first


async def test_the_bot_stays_quiet_when_the_rider_already_wrote_in_the_topic(
    db: SharedDatabase,
) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    before = len(client.payments_sends())

    await payments.record_topic_post(telegram_user_id=100, posted_at=datetime.now(UTC))
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    # The claim is recorded, but the rider already spoke for themselves.
    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.posted_message_id is None
    assert len(client.payments_sends()) == before


async def test_an_older_topic_post_does_not_count_for_this_weekend(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    # Written before the polls for this weekend existed, so it cannot be about them.
    await payments.record_topic_post(
        telegram_user_id=100,
        posted_at=datetime.now(UTC) - timedelta(days=9),
    )
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.posted_message_id is not None


async def test_a_guest_seat_edits_the_posted_line_instead_of_adding_one(
    db: SharedDatabase,
) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    posted = client.payments_sends()[-1]
    sends_before = len(client.payments_sends())

    notice = await payments.adjust_seats(service_date=saturday, telegram_user_id=100, delta=1)

    assert "30 GEL" in notice
    assert len(client.payments_sends()) == sends_before
    edits = [text for message_id, text in client.edits if message_id == posted.message_id]
    assert "30 GEL" in edits[-1]
    assert "+1 guest" in edits[-1]


async def test_seats_never_drop_below_one(db: SharedDatabase) -> None:
    poll_service, payments, _, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert "At least one seat." in await payments.adjust_seats(
        service_date=saturday, telegram_user_id=100, delta=-1
    )


async def test_adjusting_before_claiming_asks_for_the_claim_first(db: SharedDatabase) -> None:
    poll_service, payments, _, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    notice = await payments.adjust_seats(service_date=saturday, telegram_user_id=100, delta=1)

    assert notice == NOT_CLAIMED_YET_TEXT


async def test_undo_removes_the_claim_and_the_posted_line(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    posted = client.payments_sends()[-1]

    assert await payments.undo(service_date=saturday, telegram_user_id=100) == "Removed."

    assert posted.message_id in client.deleted
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None


async def test_a_cancelled_day_closes_the_board(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[-1]

    await poll_service.cancel_day(service_date=saturday, admin_user_id=1)
    await payments.sync_boards()

    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "❌ The day is cancelled." in board_edits[-1]


async def test_payments_stay_off_without_a_configured_topic(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, _ = await _setup(db, payments_thread=None)
    await _fill(poll_service, poll_id, 0, riders=5)

    assert payments.enabled is False
    await payments.sync_boards()
    assert client.payments_sends() == []
