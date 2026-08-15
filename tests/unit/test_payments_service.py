from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    DeadlineRoster,
    PaymentClaim,
    PaymentsBoard,
    RiderCard,
)
from veloexpress_bot.payments.service import (
    ALREADY_SETTLED_TEXT,
    CASH_METHOD,
    NOT_BOOKED_TEXT,
    PAYMENTS_DISABLED_TEXT,
    WAITLIST_WARNING_TEXT,
    PaymentsService,
)
from veloexpress_bot.polls.liftsignals import booking_deadline_at
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
    service_date: date | None = None,
    bot_username: str = "",
) -> tuple[PollPostingService, PaymentsService, FakeTelegramClient, str, date]:
    client = FakeTelegramClient()
    resolved = settings(payments_thread=payments_thread)
    poll_service = PollPostingService(
        settings=resolved,
        session_factory=db.session,
        telegram_client=client,
        bot_username=bot_username,
    )
    payments_service = PaymentsService(
        settings=resolved,
        session_factory=db.session,
        telegram_client=client,
        bot_username=bot_username,
    )
    saturday = service_date or _saturday()
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


async def test_a_claim_posts_the_rider_line_with_the_amount(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, poll_id, 1, riders=5, first_user_id=200)
    # Rider 100 books both lifts, so the claim covers two seats.
    await _vote(poll_service, poll_id, 100, 0, 1)
    await payments.sync_boards()

    notice = (
        await payments.claim(
            service_date=saturday,
            telegram_user_id=100,
            username="stas",
            full_name="Stas",
        )
    ).text

    # The toast names the lifts so the amount explains itself; the posted receipt
    # does not, because re-voting would make a lift list stale.
    assert "8:30, 10:00 · 30 GEL" in notice
    posted = client.payments_sends()[-1]
    assert "30 GEL" in posted.text
    assert "8:30" not in posted.text
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

    assert notice.text == NOT_BOOKED_TEXT
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
    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert outcome.text == ALREADY_SETTLED_TEXT
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

    running = next(record for record in client.sent if "pay to lock it in" in record.text)
    # Fallback with no bot username: a private supergroup link, which drops the
    # -100 prefix so -100123 becomes 123.
    assert f'href="https://t.me/c/123/{PAYMENTS_THREAD}/{board.message_id}"' in running.text
    assert "💸 Pay" in running.text


async def test_the_pay_link_is_a_deep_link_into_the_riders_own_chat(
    db: SharedDatabase,
) -> None:
    """The most-tapped link in the bot, spent on onboarding rather than a group jump.

    Tapping a deep link is pressing Start, so it both shows the rider their own
    day and leaves the bot able to message them afterwards — which is the only
    route to the members who never opened the bot.
    """
    poll_service, payments, client, poll_id, saturday = await _setup(
        db, bot_username="veloexpress_bot"
    )
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    await poll_service.evaluate_lift_signals()

    running = next(record for record in client.sent if "pay to lock it in" in record.text)
    encoded = saturday.strftime("%Y%m%d")
    assert f'href="https://t.me/veloexpress_bot?start=guests-{encoded}"' in running.text


async def test_re_voting_after_paying_moves_the_bill_not_the_payment(
    db: SharedDatabase,
) -> None:
    """Re-voting is free until the deadline, so what a rider owes changes after they
    pay. Restating the paid figure would be a lie about money; the gap is shown."""
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    # 8:30 and 10:00 both run, so the rider holds two seats when they settle.
    await _fill(poll_service, poll_id, 0, 1, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[-1]
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    # Now they drop 10:00 and take 11:45 and 13:30 instead — three lifts, not two.
    await _vote(poll_service, poll_id, 100, 0, 2, 3)
    for user_id in range(200, 204):
        await _vote(poll_service, poll_id, user_id, 2, 3)
    await payments.sync_boards()

    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "✓ @stas — 30 GEL · 2 seats · +15 due" in board_edits[-1]

    # Settling again clears the gap without a second receipt in the topic.
    sends_before = len(client.payments_sends())
    notice = (
        await payments.claim(
            service_date=saturday, telegram_user_id=100, username="rider100", full_name="R100"
        )
    ).text
    assert "45 GEL" in notice
    assert len(client.payments_sends()) == sends_before
    await payments.sync_boards()
    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "45 GEL · 3 seats" in board_edits[-1]
    assert "due" not in board_edits[-1]


async def test_paying_twice_without_changes_says_you_are_already_settled(
    db: SharedDatabase,
) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert outcome.text == ALREADY_SETTLED_TEXT


async def test_the_waitlist_is_not_billed_and_is_warned_before_paying(
    db: SharedDatabase,
) -> None:
    """A rider past the ten-seat capacity holds no seat, so the bot must not bill them.

    They may still pay for a waitlist place — it is their money — but never silently.
    """
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    # Eleven riders on 8:30: the eleventh is past capacity.
    await _fill(poll_service, poll_id, 0, riders=11)
    await payments.sync_boards()
    board = client.payments_sends()[-1]

    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    latest = board_edits[-1] if board_edits else board.text
    assert "tg://user?id=110" not in latest, "the waitlisted rider is not asked to pay"
    assert "tg://user?id=100" in latest

    first = await payments.claim(
        service_date=saturday, telegram_user_id=110, username="last", full_name="Last"
    )
    assert first.text == WAITLIST_WARNING_TEXT
    assert first.needs_confirmation is True
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None

    second = await payments.claim(
        service_date=saturday,
        telegram_user_id=110,
        username="last",
        full_name="Last",
        acknowledged=True,
    )
    assert "15 GEL" in second.text
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is not None


async def test_manual_bookings_take_seats_before_telegram_voters(db: SharedDatabase) -> None:
    """An admin accepted them personally, so a poll vote cannot bump them."""
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=9)
    for _ in range(2):
        await poll_service.adjust_manual_booking(
            service_date=saturday,
            lift_time="8:30",
            delta=1,
            admin_user_id=1,
        )

    # 9 voters + 2 manual = 11 for ten seats, so the last voter loses their place.
    ninth = await payments.claim(
        service_date=saturday, telegram_user_id=108, username="ninth", full_name="Ninth"
    )
    assert ninth.text == WAITLIST_WARNING_TEXT
    first = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="first", full_name="First"
    )
    assert "15 GEL" in first.text


async def test_partial_booking_warns_before_charging_for_only_the_filled_lift(
    db: SharedDatabase,
) -> None:
    """Charging 15 when the rider booked three lifts reads as a bug, not a rule.

    The warning names what filled and what did not, and the same tap again means
    "yes, charge me for the filled one now".
    """
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    # 8:30 fills; 10:00 stays short, and the rider holds both.
    await _fill(poll_service, poll_id, 0, riders=4)
    await _vote(poll_service, poll_id, 104, 0, 1)
    await payments.sync_boards()

    first = await payments.claim(
        service_date=saturday, telegram_user_id=104, username="stas", full_name="Stas"
    )

    assert first.needs_confirmation is True
    assert "Only 8:30 filled — 15 GEL now" in first.text
    assert "10:00 still short (+15 later)" in first.text
    assert len(first.text) <= 200, "Telegram truncates a callback answer past 200 characters"
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None

    second = await payments.claim(
        service_date=saturday,
        telegram_user_id=104,
        username="stas",
        full_name="Stas",
        acknowledged=True,
    )
    assert "15 GEL" in second.text


async def test_a_rider_can_settle_the_whole_day_including_unfilled_lifts(
    db: SharedDatabase,
) -> None:
    """One transfer beats two, and cash cannot be topped up without finding Misho."""
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=4)
    await _vote(poll_service, poll_id, 104, 0, 1)
    await payments.sync_boards()
    board = client.payments_sends()[-1]

    outcome = await payments.claim(
        service_date=saturday,
        telegram_user_id=104,
        username="stas",
        full_name="Stas",
        method=CASH_METHOD,
        acknowledged=True,
        include_pending=True,
    )

    assert "8:30, 10:00" in outcome.text
    assert "30 GEL in cash" in outcome.text
    await payments.sync_boards()
    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    # Paid 30, owes 15 right now — that is a prepayment, not an overpayment.
    assert "· 15 prepaid" in board_edits[-1]
    assert "back" not in board_edits[-1]


async def test_a_cash_tap_records_the_method_so_misho_can_reconcile(
    db: SharedDatabase,
) -> None:
    """Cash needs its own tap: telling riders to press the transfer button anyway is a
    rule 166 people will not follow, and Misho cannot otherwise tell which lines to
    look for in his bank statement."""
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[-1]

    notice = (
        await payments.claim(
            service_date=saturday,
            telegram_user_id=100,
            username="konstantin",
            full_name="Konstantin",
            method=CASH_METHOD,
        )
    ).text

    assert notice.endswith("15 GEL in cash.")
    posted = client.payments_sends()[-1]
    assert posted.text.startswith("💵 ")
    assert posted.text.endswith("· cash")
    board_edits = [text for message_id, text in client.edits if message_id == board.message_id]
    assert "· cash" in board_edits[-1]
    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.method == CASH_METHOD


async def test_an_admin_mark_leaves_the_method_unknown(db: SharedDatabase) -> None:
    """Guessing "cash" could send Misho hunting for a transfer that never existed."""
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    await payments.toggle_admin_payment(
        service_date=saturday, telegram_user_id=100, admin_user_id=1
    )

    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.method is None


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

    notice = await payments.adjust_guest_seats(
        service_date=saturday,
        telegram_user_id=100,
        lift_times=("8:30",),
        delta=1,
    )

    # A guest raises the bill; it does not claim the extra seat is already paid.
    assert notice == "Guests updated."
    assert len(client.payments_sends()) == sends_before
    edits = [text for message_id, text in client.edits if message_id == posted.message_id]
    assert "+1 guest" in edits[-1]


async def test_a_guest_seat_can_be_taken_before_paying(db: SharedDatabase) -> None:
    poll_service, payments, _, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    # No claim yet, so there is nothing to attach a guest to on the board — but the
    # seat itself is still bookable, because a guest is capacity, not a payment.
    notice = await payments.adjust_guest_seats(
        service_date=saturday,
        telegram_user_id=100,
        lift_times=("8:30",),
        delta=1,
    )

    assert notice == "Guests updated."


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
    await payments.adjust_guest_seats(
        service_date=saturday,
        telegram_user_id=100,
        lift_times=("8:30",),
        delta=1,
    )
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
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
    # @rider100 holds 10:00 too, which has not filled, so the bot warns before
    # charging for part of a booking. Acknowledge and settle what is due.
    await payments.claim(
        service_date=saturday,
        telegram_user_id=100,
        username="rider100",
        full_name="R100",
        acknowledged=True,
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


def _future_saturday() -> date:
    """Far enough out that the poll is always created before its own deadline."""
    return _saturday() + timedelta(days=14)


def _after_deadline(service_date: date) -> datetime:
    deadline = booking_deadline_at(service_date, "20:00", zone=ZoneInfo("Asia/Tbilisi"))
    return deadline + timedelta(minutes=1)


async def test_a_paid_seat_stays_paid_after_a_late_cancellation(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, _client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    # Rider 100 retracts their vote once booking has closed. The money is spent.
    await _vote(poll_service, poll_id, 100)

    notice = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    assert notice.text == ALREADY_SETTLED_TEXT


async def test_leaving_unpaid_after_the_deadline_owes_nothing(db: SharedDatabase) -> None:
    """A prepayment is not refundable; forgetting to come was never made a debt."""
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=6)
    await payments.sync_boards()
    board = client.payments_sends()[0]

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await _vote(poll_service, poll_id, 105)
    await payments.sync_boards()

    notice = await payments.claim(
        service_date=saturday, telegram_user_id=105, username="rider105", full_name="Rider 105"
    )
    assert notice.text == NOT_BOOKED_TEXT
    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    assert "@rider105" not in board_text


async def test_a_late_drop_out_is_not_offered_money_back(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    board = client.payments_sends()[0]

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await _vote(poll_service, poll_id, 100)
    await payments.sync_boards()

    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    assert "back" not in board_text
    assert "@stas" in board_text


async def test_a_lift_that_filled_by_the_deadline_keeps_running(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    board = client.payments_sends()[0]

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    # Two riders leave, which live votes would read as "needs 2 more".
    await _vote(poll_service, poll_id, 100)
    await _vote(poll_service, poll_id, 101)
    await payments.sync_boards()

    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    assert "Running: 8:30" in board_text


async def test_a_lift_short_at_the_deadline_owes_nothing(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, _client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=4)

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await _vote(poll_service, poll_id, 100)

    notice = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    assert notice.text == NOT_BOOKED_TEXT


async def test_a_seat_booked_after_the_deadline_is_still_charged(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, _client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, poll_id, 1, riders=5, first_user_id=200)
    await payments.sync_boards()

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    # Rider 100 adds the second lift late. Nothing stops that, so it must be billed.
    await _vote(poll_service, poll_id, 100, 0, 1)

    notice = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    assert "8:30, 10:00 · 30 GEL" in notice.text


async def test_cancelling_a_lift_after_the_deadline_still_refunds(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, poll_id, 1, riders=5, first_user_id=200)
    # Rider 100 pays for both lifts, so losing one leaves a real difference.
    await _vote(poll_service, poll_id, 100, 0, 1)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    board = client.payments_sends()[0]

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await poll_service.cancel_lift(service_date=saturday, lift_time="8:30", admin_user_id=1)
    await payments.sync_boards()

    # The roster holds a rider to what they paid for; it cannot hold them to a
    # lift Misho called off. That is the one refund the group does recognise.
    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    assert "15 back" in board_text


async def test_the_roster_is_frozen_once_and_never_updated(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, _client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await _vote(poll_service, poll_id, 300, 0)
    await payments.capture_deadline_rosters(now=_after_deadline(saturday) + timedelta(hours=1))

    async with db.session() as session:
        rows = (await session.scalars(select(DeadlineRoster))).all()
    assert sorted(row.telegram_user_id for row in rows) == [100, 101, 102, 103, 104]


async def test_nothing_is_frozen_before_the_deadline(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, _client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.capture_deadline_rosters(now=_after_deadline(saturday) - timedelta(minutes=2))

    async with db.session() as session:
        assert (await session.scalars(select(DeadlineRoster))).all() == []


async def _lift_sends(client: FakeTelegramClient) -> list[SentRecord]:
    """Promotion notices only; the lift topic also carries the availability board."""
    return [
        record
        for record in client.sent
        if record.thread_id == LIFT_THREAD and "waitlist" in record.text
    ]


async def test_a_freed_seat_tags_whoever_was_waiting(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, _ = await _setup(db)
    # Eleven riders for ten seats, so rider 110 is on the waitlist.
    await _fill(poll_service, poll_id, 0, riders=11)
    await payments.announce_seat_promotions()
    before = len(await _lift_sends(client))

    await _vote(poll_service, poll_id, 100)
    await payments.announce_seat_promotions()

    notices = await _lift_sends(client)
    assert len(notices) == before + 1
    assert "off the waitlist" in notices[-1].text
    assert "tg://user?id=110" in notices[-1].text
    # And only that rider: the ten who never moved are not tagged.
    assert "tg://user?id=101" not in notices[-1].text


async def test_a_freed_seat_still_tags_after_the_deadline(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=11)
    await payments.announce_seat_promotions()
    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    before = len(await _lift_sends(client))

    await _vote(poll_service, poll_id, 100)
    await payments.announce_seat_promotions(now=_after_deadline(saturday))

    notices = await _lift_sends(client)
    assert len(notices) == before + 1
    assert "tg://user?id=110" in notices[-1].text


async def test_a_new_booking_into_a_free_seat_is_not_a_promotion(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, _ = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.announce_seat_promotions()
    before = len(await _lift_sends(client))

    # Booking into an empty seat is not news to the person who just booked.
    await _vote(poll_service, poll_id, 300, 0)
    await payments.announce_seat_promotions()

    assert len(await _lift_sends(client)) == before


async def test_the_first_sight_of_a_lift_announces_nothing(db: SharedDatabase) -> None:
    poll_service, payments, client, poll_id, _ = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=11)

    await payments.announce_seat_promotions()

    assert await _lift_sends(client) == []


def _unpaid_notices(client: FakeTelegramClient) -> list[SentRecord]:
    return [record for record in client.payments_sends() if "not paid up yet" in record.text]


async def test_an_underfunded_lift_is_chased_not_cancelled(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )
    # Cash counts the same as a transfer.
    await payments.claim(
        service_date=saturday,
        telegram_user_id=101,
        username="anna",
        full_name="Anna",
        method=CASH_METHOD,
    )

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))

    notices = _unpaid_notices(client)
    assert len(notices) == 1
    assert "8:30 — 2/5 paid" in notices[0].text
    assert "Misho decides" in notices[0].text
    # The three who have not paid are tagged; the two who have are not.
    assert "tg://user?id=102" in notices[0].text
    assert "tg://user?id=100" not in notices[0].text
    # And the lift is still running: nobody's forgotten tap calls off a van.
    board = client.payments_sends()[0]
    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    assert "Running: 8:30" in board_text


async def test_a_fully_paid_lift_is_not_chased(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    for user_id in range(100, 105):
        await payments.claim(
            service_date=saturday,
            telegram_user_id=user_id,
            username=f"rider{user_id}",
            full_name=f"Rider {user_id}",
        )

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))

    assert _unpaid_notices(client) == []


async def test_a_lift_that_never_filled_is_not_chased(db: SharedDatabase) -> None:
    """Nothing was due on it, so asking for money would be noise."""
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=3)

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))

    assert _unpaid_notices(client) == []


async def test_the_chase_is_sent_once(db: SharedDatabase) -> None:
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    await payments.capture_deadline_rosters(now=_after_deadline(saturday) + timedelta(hours=1))

    assert len(_unpaid_notices(client)) == 1


async def test_a_missed_deadline_is_not_caught_up_on_the_lift_day(db: SharedDatabase) -> None:
    """A snapshot taken at Saturday lunchtime is not a snapshot of Friday 20:00."""
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)

    # The bot was down over the deadline and comes back on the lift day itself.
    lunchtime = _after_deadline(saturday) + timedelta(hours=16)
    await payments.capture_deadline_rosters(now=lunchtime)

    async with db.session() as session:
        assert (await session.scalars(select(DeadlineRoster))).all() == []
    # And nobody is chased about a van that has already left.
    assert _unpaid_notices(client) == []


def _private_sends(client: FakeTelegramClient) -> list[SentRecord]:
    return [record for record in client.sent if record.thread_id is None]


async def test_the_rider_card_replaces_itself_instead_of_piling_up(
    db: SharedDatabase,
) -> None:
    """Old cards keep live buttons over amounts that have moved, so only one exists."""
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.open_rider_card(
        telegram_user_id=100, private_chat_id=5000, service_date=saturday
    )
    first = _private_sends(client)[-1]
    await payments.open_rider_card(
        telegram_user_id=100, private_chat_id=5000, service_date=saturday
    )

    assert len(_private_sends(client)) == 2
    assert first.message_id in client.deleted
    async with db.session() as session:
        rows = (await session.scalars(select(RiderCard))).all()
    assert len(rows) == 1
    assert rows[0].telegram_message_id == _private_sends(client)[-1].message_id


async def test_the_card_covers_the_whole_weekend_not_one_day(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    sunday = saturday + timedelta(days=1)
    second = await poll_service.create_poll(
        PollSetup(service_date=sunday, created_by_user_id=1, cancelled_lift_times=("15:30",)),
        pin_after_send=False,
    )
    await _fill(poll_service, poll_id, 0, riders=5)
    await _fill(poll_service, second.poll_id or "", 1, riders=5)

    card = await payments.my_day_card(service_date=sunday, telegram_user_id=100)

    assert "10:00 — riding" in card.text
    assert card.reply_markup is not None
    tabs = [button.callback_data for button in card.reply_markup.inline_keyboard[-1]]
    assert tabs == [
        f"guest:day:{saturday.strftime('%Y%m%d')}",
        f"guest:day:{sunday.strftime('%Y%m%d')}",
    ]


async def test_the_card_tells_a_waitlisted_rider_their_place(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    # Twelve riders for ten seats: 110 is first in the queue, 111 second.
    await _fill(poll_service, poll_id, 0, riders=12)

    card = await payments.my_day_card(service_date=saturday, telegram_user_id=111)

    assert "8:30 — ⏳ waitlist, 2nd in line" in card.text
    assert "nothing to pay until one frees up" in card.text


async def test_the_board_pay_buttons_are_deep_links_when_the_username_is_known(
    db: SharedDatabase,
) -> None:
    """Paying is the most frequent action, so it is also the best onboarding route."""
    poll_service, payments, client, poll_id, saturday = await _setup(
        db, bot_username="veloexpress_bot"
    )
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.sync_boards()

    board = client.payments_sends()[0]
    assert board.markup is not None
    encoded = saturday.strftime("%Y%m%d")
    urls = [button.url for button in board.markup.inline_keyboard[0]]
    assert urls == [
        f"https://t.me/veloexpress_bot?start=paid-{encoded}",
        f"https://t.me/veloexpress_bot?start=cash-{encoded}",
    ]


async def test_the_board_falls_back_to_callbacks_without_a_username(
    db: SharedDatabase,
) -> None:
    """A missing username must not leave the board with no way to pay at all."""
    poll_service, payments, client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)

    await payments.sync_boards()

    board = client.payments_sends()[0]
    assert board.markup is not None
    encoded = saturday.strftime("%Y%m%d")
    data = [button.callback_data for button in board.markup.inline_keyboard[0]]
    assert data == [f"pay:paid:{encoded}", f"pay:cash:{encoded}"]


async def test_arriving_by_a_warning_free_pay_link_settles_up(db: SharedDatabase) -> None:
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()

    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert outcome.needs_confirmation is False
    async with db.session() as session:
        claim = await session.scalar(select(PaymentClaim))
    assert claim is not None
    assert claim.seats == 1


async def test_a_link_that_needs_explaining_records_nothing(db: SharedDatabase) -> None:
    """The card gets to explain instead; charging quietly is what the toast did badly."""
    poll_service, payments, _client, poll_id, saturday = await _setup(db)
    await _fill(poll_service, poll_id, 0, riders=5)
    # Rider 100 also holds 10:00, which has not filled — the partial-booking case.
    await _vote(poll_service, poll_id, 100, 0, 1)
    await payments.sync_boards()

    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=100, username="stas", full_name="Stas"
    )

    assert outcome.needs_confirmation is True
    async with db.session() as session:
        assert await session.scalar(select(PaymentClaim)) is None


async def test_a_rider_who_stayed_still_owes_when_others_dropped_out(
    db: SharedDatabase,
) -> None:
    """The lift filled by the deadline and ran; overnight leavers do not change that.

    Reported from production: several riders showed `prepaid` for a 10:00 lift
    that actually ran, because the poll had fallen to four by morning. Money for
    a trip that happened is spent, not credit towards the next one.
    """
    saturday = _future_saturday()
    poll_service, payments, client, poll_id, _ = await _setup(db, service_date=saturday)
    await _fill(poll_service, poll_id, 0, riders=5)
    await payments.sync_boards()
    await payments.claim(
        service_date=saturday, telegram_user_id=104, username="egor", full_name="Egor"
    )
    board = client.payments_sends()[0]

    await payments.capture_deadline_rosters(now=_after_deadline(saturday))
    # Overnight two riders retract, leaving three in the poll.
    await _vote(poll_service, poll_id, 100)
    await _vote(poll_service, poll_id, 101)
    await payments.sync_boards()

    board_text = [text for message_id, text in client.edits if message_id == board.message_id][-1]
    # Egor paid for a ride he took: spent, not held for next week.
    assert "prepaid" not in board_text
    assert "back" not in board_text
    # And rider 102, who stayed but never paid, is still asked for the money.
    outcome = await payments.claim(
        service_date=saturday, telegram_user_id=102, username="rider102", full_name="Rider 102"
    )
    assert outcome.text.endswith("15 GEL.")
