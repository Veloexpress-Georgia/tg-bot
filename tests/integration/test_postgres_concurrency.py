"""Real database locking tests; run against a disposable, migrated PostgreSQL."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.unit import test_payments_service as helpers

from veloexpress_bot.db.models import (
    BookingOrderChange,
    CashPromise,
    PaymentClaim,
    PaymentEntry,
    PollVote,
    TelegramOutbox,
)
from veloexpress_bot.telegram.outbox import TelegramOutboxDispatcher
from veloexpress_core.lifts import SentTextMessage
from veloexpress_core.payments import PaymentsService


async def test_competing_booking_corrections_commit_only_one_reviewed_order(
    pg: helpers.SharedDatabase,
):
    polls, payments, _client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0, riders=11)
    polls._settings = polls._settings.model_copy(update={"telegram_admin_ids": (1, 2)})
    orders = [[110, *range(100, 110)], [109, *range(100, 109), 110]]
    previews = [
        await polls.preview_booking_order(
            service_date=day, lift_time="8:30", ordered_user_ids=order
        )
        for order in orders
    ]
    async with pg.session() as holder:
        await payments._lock_day(holder, day)
        tasks = [
            asyncio.create_task(
                polls.save_booking_order(
                    service_date=day,
                    lift_time="8:30",
                    ordered_user_ids=order,
                    expected_digest=preview["digest"],
                    admin_user_id=actor,
                )
            )
            for actor, order, preview in zip((1, 2), orders, previews, strict=True)
        ]
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
        await holder.commit()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    async with pg.session() as session:
        audit = list(
            await session.scalars(
                select(BookingOrderChange).where(
                    BookingOrderChange.environment == polls._settings.app_env
                )
            )
        )
        assert len(audit) == 1
    final = await polls.booking_order_view(service_date=day, lift_time="8:30")
    assert [r["user_id"] for r in final["riders"]] == json.loads(audit[0].after_order)


@pytest.mark.parametrize("source", ["button", "topic"])
async def test_receipts_wait_for_queue_edit_and_do_not_charge_a_new_waitlist(
    pg: helpers.SharedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
):
    polls, payments, _client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0, riders=11)
    entered = asyncio.Event()
    original = payments._claim_row

    async def observe_lock(**kwargs):
        entered.set()
        return await original(**kwargs)

    monkeypatch.setattr(payments, "_claim_row", observe_lock)
    async with pg.session() as holder:
        await payments._lock_day(holder, day)
        task = asyncio.create_task(
            payments.claim(
                service_date=day, telegram_user_id=100, username="test", full_name="Test"
            )
            if source == "button"
            else payments.record_topic_post(telegram_user_id=100, posted_at=datetime.now(UTC))
        )
        await asyncio.wait_for(entered.wait(), 5)
        assert not task.done()
        votes = {
            v.telegram_user_id: v
            for v in await holder.scalars(select(PollVote).where(PollVote.poll_id == poll_id))
        }
        for rank, uid in enumerate([*range(101, 111), 100]):
            votes[uid].option_queue_ranks = json.dumps({"0": rank})
        await holder.commit()
    await asyncio.wait_for(task, 15)
    async with pg.session() as session:
        assert (
            await session.scalar(
                select(PaymentClaim).where(
                    PaymentClaim.environment == payments._settings.app_env,
                    PaymentClaim.telegram_user_id == 100,
                )
            )
            is None
        )


@pytest.fixture
async def pg(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[helpers.SharedDatabase]:
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to a disposable database migrated with Alembic")
    db = helpers.SharedDatabase()
    await db.dispose()
    db.engine = create_async_engine(url)
    db.factory = async_sessionmaker(db.engine, expire_on_commit=False)
    original_settings = helpers.settings
    scope = f"race-{uuid4().hex}"
    monkeypatch.setattr(
        helpers,
        "settings",
        lambda **kwargs: original_settings(**kwargs).model_copy(update={"app_env": scope}),
    )
    original_init = helpers.FakeTelegramClient.__init__

    def init(client: helpers.FakeTelegramClient) -> None:
        original_init(client)
        client.next_message_id = uuid4().int % 1_000_000_000_000

    monkeypatch.setattr(helpers.FakeTelegramClient, "__init__", init)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.mark.parametrize("initial_payment", [False, True])
async def test_concurrent_claims_record_only_one_amount(
    pg: helpers.SharedDatabase, initial_payment: bool
) -> None:
    polls, payments, client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0)
    if initial_payment:
        await payments.claim(
            service_date=day, telegram_user_id=100, username="test", full_name="Test"
        )
    await helpers._fill(polls, poll_id, 0, 1)
    other = PaymentsService(
        settings=payments._settings, session_factory=pg.session, telegram_client=client
    )
    # Isolate ledger mutation from unrelated Telegram message creation races.
    for service in (payments, other):
        service._announce_claim = AsyncMock()
        service._refresh_board = AsyncMock()
    async with pg.session() as holder:
        await payments._lock_day(holder, day)
        tasks = [
            asyncio.create_task(
                service.claim(
                    service_date=day, telegram_user_id=100, username="test", full_name="Test"
                )
            )
            for service in (payments, other)
        ]
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
        await holder.commit()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    async with pg.session() as session:
        total = await session.scalar(
            select(func.sum(PaymentEntry.amount_gel)).where(
                PaymentEntry.environment == payments._settings.app_env
            )
        )
        claims = list(
            await session.scalars(
                select(PaymentClaim).where(PaymentClaim.environment == payments._settings.app_env)
            )
        )
    assert total == 30
    assert len(claims) == 1 and claims[0].amount_gel == 30


async def test_concurrent_cash_choices_store_one_intention_and_no_money(
    pg: helpers.SharedDatabase,
) -> None:
    polls, payments, client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0)
    other = PaymentsService(
        settings=payments._settings, session_factory=pg.session, telegram_client=client
    )
    for service in (payments, other):
        service._refresh_board = AsyncMock()
    async with pg.session() as holder:
        await payments._lock_day(holder, day)
        tasks = [
            asyncio.create_task(service.promise_cash(service_date=day, telegram_user_id=100))
            for service in (payments, other)
        ]
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
        await holder.commit()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    scope = payments._settings.app_env
    async with pg.session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CashPromise)
                .where(CashPromise.environment == scope)
            )
            == 1
        )
        assert (
            await session.scalar(select(PaymentClaim).where(PaymentClaim.environment == scope))
            is None
        )
        assert (
            await session.scalar(select(PaymentEntry).where(PaymentEntry.environment == scope))
            is None
        )


async def test_concurrent_undo_and_claim_keep_projection_equal_to_ledger(
    pg: helpers.SharedDatabase,
) -> None:
    polls, payments, client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0)
    await payments.claim(service_date=day, telegram_user_id=100, username="test", full_name="Test")
    other = PaymentsService(
        settings=payments._settings, session_factory=pg.session, telegram_client=client
    )
    for service in (payments, other):
        service._announce_claim = AsyncMock()
        service._refresh_board = AsyncMock()
    await asyncio.wait_for(
        asyncio.gather(
            payments.undo(service_date=day, telegram_user_id=100),
            other.claim(service_date=day, telegram_user_id=100, username="test", full_name="Test"),
        ),
        5,
    )
    # Repeated pay/undo must not reverse earlier received rows for a second time.
    await payments.claim(service_date=day, telegram_user_id=100, username="test", full_name="Test")
    await payments.undo(service_date=day, telegram_user_id=100)
    async with pg.session() as session:
        total = await session.scalar(
            select(func.sum(PaymentEntry.amount_gel)).where(
                PaymentEntry.environment == payments._settings.app_env
            )
        )
        claim = await session.scalar(
            select(PaymentClaim).where(PaymentClaim.environment == payments._settings.app_env)
        )
    assert total == 0 and claim is None


async def test_topic_report_and_button_cannot_both_record_payment(
    pg: helpers.SharedDatabase,
) -> None:
    polls, payments, _, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0)
    payments._announce_claim = AsyncMock()
    payments._refresh_board = AsyncMock()
    await asyncio.wait_for(
        asyncio.gather(
            payments.record_topic_post(telegram_user_id=100, posted_at=datetime.now(UTC)),
            payments.claim(
                service_date=day, telegram_user_id=100, username="test", full_name="Test"
            ),
        ),
        5,
    )
    async with pg.session() as session:
        total = await session.scalar(
            select(func.sum(PaymentEntry.amount_gel)).where(
                PaymentEntry.environment == payments._settings.app_env
            )
        )
    assert total == 15


async def test_two_dispatchers_skip_a_message_being_sent(pg: helpers.SharedDatabase) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class Sender:
        calls = 0

        async def send_text(self, **kwargs: object) -> SentTextMessage:
            self.calls += 1
            entered.set()
            await release.wait()
            return SentTextMessage(message_id=42)

    sender = Sender()
    scope = uuid4().hex
    first = TelegramOutboxDispatcher(environment=scope, session_factory=pg.session, sender=sender)
    second = TelegramOutboxDispatcher(environment=scope, session_factory=pg.session, sender=sender)
    await first.enqueue_text(operation_key="one", chat_id=1, thread_id=1, text="test")
    task = asyncio.create_task(first.deliver_pending())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await asyncio.wait_for(second.deliver_pending(), 2) == 0
    finally:
        release.set()
        await task
    assert sender.calls == 1
    async with pg.session() as session:
        row = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.environment == scope)
        )
        assert row is not None and row.status == "sent"


async def test_cancelled_dispatcher_releases_pending_message(pg: helpers.SharedDatabase) -> None:
    entered = asyncio.Event()

    class Sender:
        async def send_text(self, **kwargs: object) -> SentTextMessage:
            entered.set()
            await asyncio.Event().wait()
            return SentTextMessage(message_id=42)

    scope = uuid4().hex
    first = TelegramOutboxDispatcher(environment=scope, session_factory=pg.session, sender=Sender())
    await first.enqueue_text(operation_key="one", chat_id=1, thread_id=1, text="test")
    task = asyncio.create_task(first.deliver_pending())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sender = AsyncMock()
    sender.send_text.return_value = SentTextMessage(message_id=43)
    restarted = TelegramOutboxDispatcher(
        environment=scope, session_factory=pg.session, sender=sender
    )
    assert await restarted.deliver_pending() == 1
    sender.send_text.assert_awaited_once()


async def test_concurrent_board_sync_creates_only_one_payments_board(
    pg: helpers.SharedDatabase,
) -> None:
    polls, payments, client, poll_id, _ = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0)
    other = PaymentsService(
        settings=payments._settings, session_factory=pg.session, telegram_client=client
    )
    await asyncio.wait_for(asyncio.gather(payments.sync_boards(), other.sync_boards()), 5)
    cards = [record for record in client.sent if "Payment open:" in record.text]
    assert len(cards) == 1
    assert cards[0].thread_id == helpers.PAYMENTS_THREAD


async def test_concurrent_web_requests_keep_one_command(pg: helpers.SharedDatabase) -> None:
    from veloexpress_bot.db.models import AdminCommand
    from veloexpress_core.commands import CommandInput, CommandQueue

    config = helpers.settings()
    first = CommandQueue(settings=config, session_factory=pg.session)
    second = CommandQueue(settings=config, session_factory=pg.session)
    spec = CommandInput(request_id=uuid4(), action="terms", price_gel=20, deadline_time="20:00")
    results = await asyncio.wait_for(
        asyncio.gather(first.enqueue(spec, actor_user_id=1), second.enqueue(spec, actor_user_id=1)),
        5,
    )
    assert results[0]["id"] == results[1]["id"]
    async with pg.session() as session:
        assert (
            await session.scalar(
                select(func.count(AdminCommand.id)).where(
                    AdminCommand.environment == config.app_env
                )
            )
            == 1
        )


async def test_two_guest_requests_cannot_take_the_same_last_seat(
    pg: helpers.SharedDatabase,
) -> None:
    polls, payments, client, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0, riders=9)
    other = PaymentsService(
        settings=payments._settings, session_factory=pg.session, telegram_client=client
    )
    for service in (payments, other):
        service._announce_claim = AsyncMock()
        service._refresh_board = AsyncMock()
    results = await asyncio.wait_for(
        asyncio.gather(
            payments.adjust_guest_seats(
                service_date=day, telegram_user_id=100, lift_times=("8:30",), delta=1
            ),
            other.adjust_guest_seats(
                service_date=day, telegram_user_id=101, lift_times=("8:30",), delta=1
            ),
        ),
        5,
    )
    assert results.count("Guests updated.") == 1
    bookings = await payments.day_bookings(day)
    assert bookings is not None and bookings.seats_left("8:30") == 0
    assert sum(bookings.guests_by_user_lift.values()) == 1


async def test_concurrent_first_manual_seats_stay_within_capacity(
    pg: helpers.SharedDatabase,
) -> None:
    polls, _, _, poll_id, day = await helpers._setup(pg)
    await helpers._fill(polls, poll_id, 0, riders=9)
    polls._refresh_availability = AsyncMock()
    polls._refresh_booking_monitors = AsyncMock()
    results = await asyncio.wait_for(
        asyncio.gather(
            polls.adjust_manual_booking(
                service_date=day, lift_time="8:30", delta=1, admin_user_id=1
            ),
            polls.adjust_manual_booking(
                service_date=day, lift_time="8:30", delta=1, admin_user_id=1
            ),
            return_exceptions=True,
        ),
        5,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    detail = await polls.lift_detail(service_date=day, lift_time="8:30")
    assert detail is not None and detail[0].manual_count == 1 and detail[0].seat_count == 10
