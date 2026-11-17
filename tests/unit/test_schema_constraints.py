"""The database, not only the code, refuses impossible states."""

from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from veloexpress_bot.db.base import Base
from veloexpress_bot.db.models import (
    AdminCommand,
    BikeParkSession,
    PaymentClaim,
    PaymentEntry,
    PollBatch,
    SeatOffer,
    TelegramOutbox,
)

DAY = date(2026, 11, 21)
SCOPE: dict[str, Any] = {"environment": "test", "chat_id": -100, "thread_id": None}


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _commit(sessions: async_sessionmaker[AsyncSession], *rows: Base) -> None:
    async with sessions() as session:
        session.add_all(rows)
        await session.commit()


def _offer(**values: Any) -> SeatOffer:
    return SeatOffer(**{**SCOPE, "service_date": DAY, "lift_time": "8:30", **values})


def _batch(status: str) -> PollBatch:
    return PollBatch(
        **SCOPE,
        service_date=DAY,
        service_day="saturday",
        created_by_user_id=1,
        first_lift_location="Vake",
        status=status,
        idempotency_key=f"key-{status}",
    )


def _park(status: str) -> BikeParkSession:
    return BikeParkSession(
        **SCOPE,
        service_date=DAY,
        start_time="10:00",
        price_gel=5,
        created_by_user_id=1,
        status=status,
    )


def _outbox(status: str) -> TelegramOutbox:
    return TelegramOutbox(
        environment="test", operation_key=status, chat_id=-100, text="x", status=status
    )


def _command(status: str) -> AdminCommand:
    return AdminCommand(
        **SCOPE,
        actor_user_id=1,
        request_key=status,
        action="noop",
        payload="{}",
        status=status,
    )


def _entry(kind: str) -> PaymentEntry:
    return PaymentEntry(
        **SCOPE, service_date=DAY, telegram_user_id=1, amount_gel=20, method="cash", kind=kind
    )


def _claim(method: str | None) -> PaymentClaim:
    return PaymentClaim(
        **SCOPE, service_date=DAY, telegram_user_id=1, full_name="Rider", method=method
    )


ALLOWED: list[tuple[Callable[[Any], Base], tuple[Any, ...]]] = [
    (lambda status: _offer(status=status), ("open", "closed")),
    (
        _batch,
        (
            "draft",
            "posting",
            "sent_unconfirmed",
            "posted",
            "failed",
            "cancelled",
            "cleanup_pending",
            "cleanup_failed",
            "recreated",
            "deleted",
        ),
    ),
    (_park, ("posting", "posted", "sent_unconfirmed", "failed", "cancelled", "not_reached")),
    (_outbox, ("pending", "sent", "failed", "expired")),
    (_command, ("pending", "running", "complete", "failed", "review")),
    (_entry, ("received", "reversal", "method_change", "refund")),
    (_claim, ("cash", "transfer", "mixed", None)),
]


@pytest.mark.parametrize(("make", "values"), ALLOWED)
async def test_every_value_the_code_writes_is_accepted(
    sessions: async_sessionmaker[AsyncSession],
    make: Callable[[Any], Base],
    values: tuple[Any, ...],
) -> None:
    for value in values:
        async with sessions() as session:
            row = make(value)
            session.add(row)
            await session.commit()
            # Some of these tables also allow one active row per day.
            await session.delete(row)
            await session.commit()


@pytest.mark.parametrize(("make", "_values"), ALLOWED)
async def test_an_unknown_value_is_refused(
    sessions: async_sessionmaker[AsyncSession],
    make: Callable[[Any], Base],
    _values: tuple[Any, ...],
) -> None:
    with pytest.raises(IntegrityError, match="CHECK constraint"):
        await _commit(sessions, make("typo"))


async def test_a_lift_has_at_most_one_open_seat_offer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _commit(sessions, _offer(status="open"))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        await _commit(sessions, _offer(status="open"))


async def test_closed_offers_and_other_lifts_do_not_conflict(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _commit(
        sessions,
        _offer(status="closed"),
        _offer(status="closed"),
        _offer(status="open"),
        _offer(status="open", lift_time="10:30"),
        _offer(status="open", thread_id=7),
    )


def test_hot_foreign_keys_are_indexed_and_redundant_indexes_are_gone() -> None:
    def names(table: str) -> set[str | None]:
        return {index.name for index in Base.metadata.tables[table].indexes}

    assert {"ix_seat_offer_day", "uq_seat_offer_open_lift"} <= names("seat_offer")
    assert "ix_poll_option_snapshot_batch_id" in names("poll_option_snapshot")
    assert "ix_poll_message_batch_id" in names("poll_message")
    assert "ix_payment_entry_reversed_entry_id" in names("payment_entry")
    assert "ix_telegram_outbox_pending" in names("telegram_outbox")
    assert "ix_poll_vote_poll_id" not in names("poll_vote")
    assert "uq_poll_vote_poll_user" in names("poll_vote")
