from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from tests.unit.test_payments_service import FakeTelegramClient, SharedDatabase, settings
from tests.unit.test_poll_service import _upcoming_weekend

from veloexpress_bot.db.models import PaymentClaim, PaymentEntry
from veloexpress_core.lifts import PollPostingService, PollSetup
from veloexpress_core.payments import PaymentsService


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


async def partial_day(db: SharedDatabase):
    client = FakeTelegramClient()
    config = settings().model_copy(update={"payment_price_gel": 20})
    polls = PollPostingService(settings=config, session_factory=db.session, telegram_client=client)
    payments = PaymentsService(settings=config, session_factory=db.session, telegram_client=client)
    _, sunday = _upcoming_weekend()
    poll = await polls.create_poll(PollSetup(service_date=sunday, created_by_user_id=1))
    for uid in range(100, 109):
        await polls.track_poll_answer(
            poll_id=poll.poll_id or "",
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(1, 2, 3),
        )
    await polls.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=109,
        username=None,
        full_name="Last seat",
        option_ids=(2,),
    )
    await polls.track_poll_answer(
        poll_id=poll.poll_id or "",
        telegram_user_id=999,
        username="de02vd83ads",
        full_name="Vlad",
        option_ids=(1, 2, 3),
    )
    return polls, payments, client, poll.poll_id or "", sunday


async def test_two_seats_and_one_waitlist_cost_forty_not_sixty(db: SharedDatabase):
    _polls, payments, _client, _poll_id, day = await partial_day(db)
    view = await payments.rider_day(service_date=day, telegram_user_id=999)
    assert view is not None
    assert {r.lift_time: r.waitlist_position for r in view.rows} == {
        "10:00": 0,
        "11:45": 1,
        "13:30": 0,
    }
    assert view.due_now_gel == 40
    assert view.due_all_gel == 40


async def test_payment_button_records_only_two_real_seats(db: SharedDatabase):
    _polls, payments, _client, _poll_id, day = await partial_day(db)
    outcome = await payments.claim(
        service_date=day,
        telegram_user_id=999,
        username="de02vd83ads",
        full_name="Vlad",
        acknowledged=True,
    )
    assert "Total reported: 40 GEL" in outcome.text
    async with db.session() as session:
        claim = await session.scalar(
            select(PaymentClaim).where(PaymentClaim.telegram_user_id == 999)
        )
        assert claim is not None and claim.seats == 2 and claim.amount_gel == 40
        entries = list(
            await session.scalars(select(PaymentEntry).where(PaymentEntry.telegram_user_id == 999))
        )
        assert sum(row.amount_gel for row in entries) == 40


async def test_topic_payment_inference_records_only_real_seats(db: SharedDatabase):
    from datetime import UTC, datetime

    _polls, payments, _client, _poll_id, _day = await partial_day(db)
    await payments.record_topic_post(telegram_user_id=999, posted_at=datetime.now(UTC))
    async with db.session() as session:
        claim = await session.scalar(
            select(PaymentClaim).where(PaymentClaim.telegram_user_id == 999)
        )
        assert claim is not None and claim.amount_gel == 40


@pytest.mark.parametrize("source", ["button", "topic"])
async def test_payment_rechecks_seats_after_waiting_for_day_lock(
    db: SharedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
):
    from datetime import UTC, datetime

    polls, payments, _client, poll_id, day = await partial_day(db)
    original = payments._claim_row
    changed = False

    async def changed_before_lock(**kwargs):
        nonlocal changed
        if not changed:
            changed = True
            await polls.track_poll_answer(
                poll_id=poll_id,
                telegram_user_id=999,
                username="de02vd83ads",
                full_name="Vlad",
                option_ids=(1, 2),
            )
        return await original(**kwargs)

    monkeypatch.setattr(payments, "_claim_row", changed_before_lock)
    if source == "button":
        await payments.claim(
            service_date=day,
            telegram_user_id=999,
            username="de02vd83ads",
            full_name="Vlad",
            acknowledged=True,
        )
    else:
        await payments.record_topic_post(telegram_user_id=999, posted_at=datetime.now(UTC))
    async with db.session() as session:
        claim = await session.scalar(
            select(PaymentClaim).where(PaymentClaim.telegram_user_id == 999)
        )
        assert claim is not None and claim.amount_gel == 20
