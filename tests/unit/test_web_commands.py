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


async def test_day_activity_keeps_older_pending_requests_and_excludes_other_scopes() -> None:
    from sqlalchemy import select

    from veloexpress_bot.db.models import AdminCommand

    db = SharedDatabase()
    await db.create()
    queue = CommandQueue(settings=settings(), session_factory=db.session)
    day = date(2026, 10, 3)
    first = await queue.enqueue(
        CommandInput(
            request_id=uuid4(), action="manual", service_date=day, lift_time="8:30", delta=1
        ),
        actor_user_id=42,
    )
    for _ in range(35):
        await queue.enqueue(
            CommandInput(
                request_id=uuid4(), action="manual", service_date=day, lift_time="8:30", delta=1
            ),
            actor_user_id=42,
        )
    async with db.session() as session:
        for row in await session.scalars(
            select(AdminCommand).where(AdminCommand.id != first["id"])
        ):
            row.status = "complete"
        await session.commit()
    await queue.enqueue(
        CommandInput(
            request_id=uuid4(),
            action="manual",
            service_date=date(2026, 10, 4),
            lift_time="8:30",
            delta=1,
        ),
        actor_user_id=42,
    )
    other = CommandQueue(
        settings=settings().model_copy(update={"app_env": "other"}), session_factory=db.session
    )
    await other.enqueue(
        CommandInput(
            request_id=uuid4(), action="manual", service_date=day, lift_time="8:30", delta=1
        ),
        actor_user_id=99,
    )
    entries = await queue.recent(service_date=day)
    assert entries[0]["id"] == first["id"]
    assert entries[0]["lift_time"] == "8:30"
    assert len(entries) == 30
    assert all(e["service_date"] == day.isoformat() and e["actor_user_id"] == 42 for e in entries)
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


@pytest.mark.parametrize("guests, expected_refund", [(0, 15), (1, 30)])
async def test_cancel_lift_worker_reports_the_paid_seat_after_cancellation(
    guests: int, expected_refund: int
) -> None:
    from sqlalchemy import select
    from tests.unit.test_payments_service import _fill, _setup

    from veloexpress_bot.db.models import PaymentEntry, RefundReport
    from veloexpress_core.commands import Operations
    from veloexpress_core.runtime import build_runtime

    db = SharedDatabase()
    await db.create()
    try:
        lifts, payments, client, poll_id, day = await _setup(db)
        await _fill(lifts, poll_id, 0)
        if guests:
            await payments.adjust_guest_seats(
                service_date=day, telegram_user_id=100, lift_times=("8:30",), delta=1
            )
        await payments.claim(
            service_date=day, telegram_user_id=100, username="alice", full_name="Alice"
        )
        config = settings()
        runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=client)
        operations = Operations(settings=config, runtime=runtime)
        queue = CommandQueue(settings=config, session_factory=db.session)
        spec = CommandInput(
            request_id=uuid4(), action="cancel_lift", service_date=day, lift_time="8:30"
        )
        preview = await operations.preview(spec)
        assert f"Total to return: {expected_refund} GEL" in preview["details"]
        assert (
            not next(d for d in await lifts.status_days() if d.service_date == day)
            .lifts[0]
            .cancelled
        )
        assert await payments.recent_refund_reports() == ()
        spec = spec.model_copy(update={"expected_digest": preview["digest"]})
        command = await queue.enqueue(spec, actor_user_id=1)
        assert await queue.process_one(operations)
        result = await queue.get(command["id"], actor_user_id=1)
        assert result is not None and result["status"] == "complete"
        assert result["result"]["report"] == preview["details"]
        assert f"Paid {expected_refund} · rides 0 GEL" in result["result"]["report"]
        assert (
            next(d for d in await lifts.status_days() if d.service_date == day).lifts[0].cancelled
        )
        assert (await queue.enqueue(spec, actor_user_id=1))["id"] == command["id"]
        assert not await queue.process_one(operations)
        assert (
            await payments.cancellation_report(service_date=day, cancelled_lift_time="8:30")
            == preview["details"]
        )
        async with db.session() as session:
            assert len(list(await session.scalars(select(RefundReport)))) == 1
            entries = list(await session.scalars(select(PaymentEntry)))
        assert [(entry.kind, entry.amount_gel) for entry in entries] == [
            ("received", expected_refund)
        ]
    finally:
        await db.dispose()


@pytest.mark.parametrize("cancel_whole_day", [False, True])
async def test_worker_refunds_stay_cumulative_and_keep_remaining_rides(
    cancel_whole_day: bool,
) -> None:
    from sqlalchemy import select
    from tests.unit.test_payments_service import _fill, _setup

    from veloexpress_bot.db.models import PaymentClaim, PaymentEntry, RefundReport
    from veloexpress_core.commands import Operations
    from veloexpress_core.runtime import build_runtime

    db = SharedDatabase()
    await db.create()
    try:
        lifts, payments, client, poll_id, day = await _setup(db)
        await _fill(lifts, poll_id, 0, 1)
        await payments.adjust_guest_seats(
            service_date=day, telegram_user_id=100, lift_times=("8:30",), delta=1
        )
        await payments.claim(
            service_date=day, telegram_user_id=100, username="alice", full_name="Alice"
        )
        config = settings()
        runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=client)
        operations = Operations(settings=config, runtime=runtime)
        queue = CommandQueue(settings=config, session_factory=db.session)
        for action, lift_time, expected_return, expected_rides in (
            ("cancel_lift", "10:00", 15, 30),
            (
                "cancel_day" if cancel_whole_day else "cancel_lift",
                None if cancel_whole_day else "8:30",
                45,
                0,
            ),
        ):
            spec = CommandInput.model_validate(
                {
                    "request_id": uuid4(),
                    "action": action,
                    "service_date": day,
                    "lift_time": lift_time,
                }
            )
            preview = await operations.preview(spec)
            assert f"Total to return: {expected_return} GEL" in preview["details"]
            spec = spec.model_copy(update={"expected_digest": preview["digest"]})
            command = await queue.enqueue(spec, actor_user_id=1)
            assert await queue.process_one(operations)
            result = await queue.get(command["id"], actor_user_id=1)
            assert result is not None and result["status"] == "complete"
            assert result["result"]["report"] == preview["details"]
            assert f"Paid 45 · rides {expected_rides} GEL" in result["result"]["report"]
            assert (await queue.enqueue(spec, actor_user_id=1))["id"] == command["id"]
            assert not await queue.process_one(operations)
        reports = await payments.recent_refund_reports()
        assert len(reports) == 2
        assert "Total to return: 45 GEL" in reports[0].text
        async with db.session() as session:
            assert len(list(await session.scalars(select(RefundReport)))) == 2
            entries = list(await session.scalars(select(PaymentEntry)))
            claim = await session.scalar(select(PaymentClaim))
        assert [(entry.kind, entry.amount_gel) for entry in entries] == [("received", 45)]
        assert (claim is None) == cancel_whole_day
        if cancel_whole_day:
            assert all(d.service_date != day for d in await lifts.status_days())
        else:
            assert (
                await payments.cancellation_report(service_date=day, cancelled_lift_time="8:30")
                == reports[0].text
            )
            assert len(await payments.recent_refund_reports()) == 2
    finally:
        await db.dispose()


@pytest.mark.parametrize("action", ["cancel_lift", "cancel_day"])
async def test_failed_cancellation_does_not_file_a_refund_report(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from tests.unit.test_payments_service import _fill, _setup

    from veloexpress_core.commands import Operations
    from veloexpress_core.runtime import build_runtime

    db = SharedDatabase()
    await db.create()
    try:
        lifts, payments, client, poll_id, day = await _setup(db)
        await _fill(lifts, poll_id, 0)
        await payments.claim(
            service_date=day, telegram_user_id=100, username="alice", full_name="Alice"
        )
        config = settings()
        runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=client)
        operations = Operations(settings=config, runtime=runtime)
        queue = CommandQueue(settings=config, session_factory=db.session)
        spec = CommandInput.model_validate(
            {
                "request_id": uuid4(),
                "action": action,
                "service_date": day,
                "lift_time": "8:30" if action == "cancel_lift" else None,
            }
        )
        preview = await operations.preview(spec)
        spec = spec.model_copy(update={"expected_digest": preview["digest"]})
        monkeypatch.setattr(
            runtime.lifts, action, AsyncMock(side_effect=RuntimeError("DB unavailable"))
        )
        command = await queue.enqueue(spec, actor_user_id=1)
        assert await queue.process_one(operations)
        result = await queue.get(command["id"], actor_user_id=1)
        assert result is not None and result["status"] == "review"
        assert not await queue.process_one(operations)
        assert await payments.recent_refund_reports() == ()
        assert await operations.preview(spec) == preview
    finally:
        await db.dispose()
