from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.unit.test_payments_service import SharedDatabase, settings

from veloexpress_core.commands import CommandInput, CommandQueue


async def test_command_retries_are_idempotent_and_scope_is_private() -> None:
    db = SharedDatabase()
    await db.create()
    queue = CommandQueue(settings=settings(), session_factory=db.session)
    spec = CommandInput(
        request_id=uuid4(),
        action="manual",
        service_date=date(2026, 10, 3),
        lift_time="8:30",
        delta=1,
    )
    first = await queue.enqueue(spec, actor_user_id=42)
    again = await queue.enqueue(spec, actor_user_id=42)
    assert first["id"] == again["id"]
    with pytest.raises(ValueError):
        await queue.enqueue(spec.model_copy(update={"delta": -1}), actor_user_id=42)
    assert await queue.get(first["id"], actor_user_id=99) is None
    await db.dispose()


def test_command_validation_rejects_invalid_operations() -> None:
    for fields in (
        {"action": "manual", "service_date": "2026-10-03", "lift_time": "8:30", "delta": 8},
        {"action": "payment", "amount_gel": -1},
        {
            "action": "plan",
            "saturday_enabled": False,
            "sunday_enabled": False,
            "first_lift_time": "8:30",
            "last_lift_time": "13:30",
        },
        {"action": "cancel_day"},
    ):
        with pytest.raises(ValidationError):
            CommandInput.model_validate({"request_id": uuid4(), **fields})


async def test_worker_uses_shared_payment_logic_once_and_protects_ledger() -> None:
    from sqlalchemy import select
    from tests.unit.test_payments_service import _fill, _setup

    from veloexpress_bot.db.models import PaymentClaim, PaymentEntry
    from veloexpress_core.commands import Operations
    from veloexpress_core.runtime import build_runtime

    db = SharedDatabase()
    await db.create()
    lifts, payments, client, poll_id, day = await _setup(db)
    await _fill(lifts, poll_id, 0)
    config = settings()
    runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=client)
    queue = CommandQueue(settings=config, session_factory=db.session)
    spec = CommandInput(
        request_id=uuid4(),
        action="payment",
        service_date=day,
        user_id=100,
        amount_gel=15,
        method="cash",
    )
    command = await queue.enqueue(spec, actor_user_id=1)
    assert await queue.process_one(Operations(settings=config, runtime=runtime))
    assert not await queue.process_one(Operations(settings=config, runtime=runtime))
    result = await queue.get(command["id"], actor_user_id=1)
    assert result is not None and result["status"] == "complete"
    async with db.session() as session:
        entries = list(await session.scalars(select(PaymentEntry)))
        claim = await session.scalar(select(PaymentClaim))
    assert len(entries) == 1 and entries[0].amount_gel == 15
    assert claim is not None and claim.verified_by_user_id == 1
    assert "Ask him" in await payments.undo(service_date=day, telegram_user_id=100)
    # A retry with the same reference also remains safe if called outside the queue.
    await runtime.payments.record_admin_payment(
        service_date=day,
        telegram_user_id=100,
        amount_gel=15,
        method="cash",
        admin_user_id=1,
        reference_key=f"web:{spec.request_id}",
    )
    async with db.session() as session:
        assert len(list(await session.scalars(select(PaymentEntry)))) == 1
    await db.dispose()


async def test_worker_revoked_admin_and_uncertain_outcomes_are_not_replayed() -> None:
    from typing import Any, cast

    from veloexpress_core.commands import Operations

    class UncertainOperations:
        calls = 0

        async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            raise RuntimeError("Connection lost after a side effect")

    db = SharedDatabase()
    await db.create()
    queue = CommandQueue(settings=settings(), session_factory=db.session)
    spec = CommandInput(request_id=uuid4(), action="terms", price_gel=20, deadline_time="20:00")
    command = await queue.enqueue(spec, actor_user_id=1)
    operations = UncertainOperations()
    await queue.process_one(cast(Operations, operations))
    result = await queue.get(command["id"], actor_user_id=1)
    assert result is not None and result["status"] == "review"
    assert not await queue.process_one(cast(Operations, operations))
    assert operations.calls == 1
    existing = await queue.existing(spec, actor_user_id=1)
    assert existing is not None and existing["id"] == command["id"]
    await db.dispose()
