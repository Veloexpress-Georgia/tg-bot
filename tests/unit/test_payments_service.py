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
    PAYMENTS_DISABLED_TEXT,
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


async def test_the_running_notice_links_to_that_days_board(db: SharedDatabase) -> None:
    """The board is the thing worth linking to, so the link must point at the message.

    Boards are synced before signals on each tick for exactly this reason: on the
    tick a lift crosses the minimum, the board has to exist first.
    """
    poll_service, payments, client, poll_id, _ = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.sync_boards()
    board = client.payments_sends()[-1]
    await poll_service.evaluate_lift_signals()

    running = next(record for record in client.sent if "is running" in record.text)
    # A private supergroup link drops the -100 prefix: -100123 becomes 123.
    assert f'href="https://t.me/c/123/{PAYMENTS_THREAD}/{board.message_id}"' in running.text
    assert "Pay here" in running.text


async def test_writing_in_the_payments_topic_marks_the_rider_paid(db: SharedDatabase) -> None:
    """The group convention is that you post there once you have paid.

    The bot never reads the text, so presence is the entire signal.
    """
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[-1]
    sends_before = len(client.payments_sends())

    await payments.record_topic_post(telegram_user_id=100, posted_at=datetime.now(UTC))

    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.service_date == saturday
    assert claim.seats == 1
    # The rider spoke for themselves, so the bot adds no line of its own.
    assert len(client.payments_sends()) == sends_before
    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "✓ @rider100 — 15 GEL" in board_edits[-1]


async def test_a_topic_post_marks_only_the_nearest_day(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    sunday = saturday + timedelta(days=1)
    sunday_poll = await poll_service.create_poll(
        PollSetup(service_date=sunday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        include_notice=False,
        pin_after_send=False,
    )
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, sunday_poll.poll_id or "", 0, riders=5)
    await payments.sync_boards()

    await payments.record_topic_post(telegram_user_id=100, posted_at=datetime.now(UTC))

    async with db.session() as session:
        claims = (await session.scalars(select(PaymentClaim))).all()
    # One message cannot be read as paying for a whole weekend.
    assert [claim.service_date for claim in claims] == [saturday]


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


async def test_an_admin_can_record_a_cash_payment_without_posting_to_the_topic(
    db: SharedDatabase,
) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[-1]
    sends_before = len(client.payments_sends())

    notice = await payments.toggle_admin_payment(
        service_date=saturday,
        telegram_user_id=100,
        admin_user_id=1,
    )

    assert notice == "@rider100: paid 15 GEL."
    # Whoever took the cash already knows; only the board changes.
    assert len(client.payments_sends()) == sends_before
    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "✓ @rider100 — 15 GEL" in board_edits[-1]

    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.verified_by_user_id == 1


async def test_the_admin_mark_toggles_back_off(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    await payments.toggle_admin_payment(
        service_date=saturday, telegram_user_id=100, admin_user_id=1
    )
    notice = await payments.toggle_admin_payment(
        service_date=saturday, telegram_user_id=100, admin_user_id=1
    )

    assert notice == "@rider100: not paid."
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None


async def test_unmarking_a_rider_claim_also_removes_the_line_the_bot_posted(
    db: SharedDatabase,
) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    posted = client.payments_sends()[-1]

    await payments.toggle_admin_payment(
        service_date=saturday, telegram_user_id=100, admin_user_id=1
    )

    assert posted.message_id in client.deleted


async def test_nothing_is_posted_when_the_payments_topic_is_unset(db: SharedDatabase) -> None:
    """The admin mark is reachable from the monitor even with payments switched off.

    Without a guard the board would be sent with no thread id, which lands in the
    group's root topic rather than nowhere.
    """
    poll_service, payments, client, poll_id, saturday = await _setup(db, payments_thread=None)
    await _fill(poll_service, poll_id, 0, riders=5)

    notice = await payments.toggle_admin_payment(
        service_date=saturday,
        telegram_user_id=100,
        admin_user_id=1,
    )

    assert notice == PAYMENTS_DISABLED_TEXT
    assert [record for record in client.sent if record.thread_id is None] == []
    async with db.session() as session:
        assert await session.scalar(select(PaymentsBoard)) is None
        assert await session.scalar(select(PaymentClaim)) is None


async def test_cancelling_a_day_reports_every_payment_as_a_refund(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    await payments.adjust_seats(service_date=saturday, telegram_user_id=100, delta=1)
    await payments.claim(
        service_date=saturday, telegram_user_id=101, username="anna", full_name="Anna"
    )

    await poll_service.cancel_day(service_date=saturday, admin_user_id=1)
    report = await payments.cancellation_report(service_date=saturday)

    assert report is not None
    assert "cancelled — who paid" in report
    assert "@stas</a> — 30 GEL · 2 seats" in report
    assert "@anna</a> — 15 GEL" in report
    assert "Refund 45 GEL." in report
    # A whole day leaves nothing to ride, and the header already said so, so rows
    # carry neither "still on" nor a per-rider refund note.
    assert "still on" not in report
    assert "nothing left" not in report


async def test_cancelling_one_lift_separates_refunds_from_riders_who_stay(
    db: SharedDatabase,
) -> None:
    """Payment covers the day, so losing one lift is not automatically a refund."""
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    # @rider100 booked both 8:30 and 10:00; the others only 8:30.
    await _vote(poll_service, poll_id, 100, 0, 1)
    for user_id in range(101, 105):
        await _vote(poll_service, poll_id, user_id, 0)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="rider100", full_name="R100"
    )
    await payments.claim(
        service_date=saturday, telegram_user_id=101, username="rider101", full_name="R101"
    )

    await poll_service.cancel_lift(service_date=saturday, lift_time="8:30", admin_user_id=1)
    report = await payments.cancellation_report(
        service_date=saturday,
        cancelled_lift_time="8:30",
    )

    assert report is not None
    # A seat is priced per running lift, so @rider100 paid for 8:30 only — 10:00
    # was still short of the minimum when they claimed.
    assert "@rider100</a> — 15 GEL · still on 10:00" in report
    assert "@rider101</a> — 15 GEL · nothing left, refund" in report
    assert "Refund 15 GEL of 30 GEL paid." in report


async def test_forgetting_a_day_clears_its_payments_after_the_report(
    db: SharedDatabase,
) -> None:
    """The refund report is the record; keeping the claims would overstate the till."""
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    await poll_service.cancel_day(service_date=saturday, admin_user_id=1)
    assert await payments.cancellation_report(service_date=saturday) is not None
    await payments.forget_day(service_date=saturday)

    assert await payments.cancellation_report(service_date=saturday) is None
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None


async def test_no_refund_report_when_nobody_had_paid(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    await poll_service.cancel_day(service_date=saturday, admin_user_id=1)

    assert await payments.cancellation_report(service_date=saturday) is None


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
