"""Real database locking tests; run against a disposable, migrated PostgreSQL."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.unit import test_payments_service as helpers

from veloexpress_bot.db.models import PaymentClaim, PaymentEntry, TelegramOutbox
from veloexpress_bot.payments.service import PaymentsService
from veloexpress_bot.polls.service import SentTextMessage
from veloexpress_bot.telegram.outbox import TelegramOutboxDispatcher


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
