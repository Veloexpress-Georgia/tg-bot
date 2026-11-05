from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from tests.unit.test_payments_service import SharedDatabase, settings
from tests.unit.test_poll_service import FakeTelegramClient

from veloexpress_api.app import create_app
from veloexpress_api.settings import WebSettings
from veloexpress_bot.db.models import (
    BikeParkSession,
    BikeParkVote,
    PaymentEntry,
    PollBatch,
    PollVote,
)
from veloexpress_bot.polls.bikepark import render_bike_park_poll, validate_bike_park_date
from veloexpress_bot.polls.schedule import cancelled_lift_times_for_range
from veloexpress_core.commands import CommandInput, Operations
from veloexpress_core.lifts import PollSetup
from veloexpress_core.runtime import build_runtime


@pytest.fixture
async def park_setup():
    db = SharedDatabase()
    await db.create()
    config = settings().model_copy(update={"telegram_bot_token": "123456:test-token"})
    telegram = FakeTelegramClient()
    runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=telegram)
    operations = Operations(settings=config, runtime=runtime)
    day = runtime.lifts.bike_park.now().date() + timedelta(days=1)
    try:
        yield db, config, telegram, runtime, operations, day
    finally:
        await db.dispose()


async def publish(operations, day, **kwargs):
    spec = CommandInput(
        request_id=uuid4(), action="bike_park", service_date=day, start_time="13:00", **kwargs
    )
    preview = await operations.preview(spec)
    return await operations.execute(
        spec.model_copy(update={"expected_digest": preview["digest"]}), actor_user_id=1, identity={}
    )


async def test_park_price_snapshot_and_votes_are_independent_of_lifts_and_money(park_setup):
    db, _, telegram, runtime, operations, day = park_setup
    await runtime.terms.set_values(
        price_gel=20, bike_park_price_gel=5, deadline_time="20:00", admin_user_id=1
    )
    await publish(operations, day)
    park = (await runtime.lifts.bike_park.list())[0]
    assert park.price_gel == 5
    assert len(telegram.sent) == 1
    assert not telegram.sent[0].allows_multiple_answers
    assert not telegram.sent[0].is_anonymous
    assert "Vake" in telegram.sent[0].question
    assert "minimum 6" in telegram.sent[0].question
    await runtime.terms.adjust_bike_park_price(1, admin_user_id=1)
    await runtime.terms.adjust_price(1, admin_user_id=1)
    values = await runtime.terms.values()
    assert (values.price_gel, values.bike_park_price_gel) == (21, 6)
    assert (await runtime.lifts.bike_park.list())[0].price_gel == 5
    for uid in range(1, 8):
        await runtime.lifts.track_poll_answer(
            poll_id="poll-42",
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(0,),
        )
    await runtime.lifts.track_poll_answer(
        poll_id="poll-42", telegram_user_id=2, username=None, full_name="Rider 2", option_ids=(1,)
    )
    assert len((await runtime.lifts.bike_park.list())[0].riders) == 6
    assert await runtime.lifts.status_days() == ()
    displayed = await runtime.lifts.display_days()
    assert len(displayed) == 1 and displayed[0].lifts == ()
    assert displayed[0].bike_park and displayed[0].owed_gel == 0
    assert await runtime.payments.day_bookings(day) is None
    async with db.session() as session:
        for table in (PollBatch, PollVote, PaymentEntry):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert await session.scalar(select(func.count()).select_from(BikeParkVote)) == 7
    monitor = await runtime.lifts.booking_monitor_view(selected_service_date=day)
    assert "Bike park" in monitor.text and "6 signed up" in monitor.text
    assert "funded" not in monitor.text
    with pytest.raises(ValueError, match="already has"):
        await publish(operations, day)
    assert len(telegram.sent) == 1


async def test_same_date_regular_lifts_and_park_cancellations_are_independent(park_setup):
    _, _, _, runtime, operations, day = park_setup
    await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("8:30", "13:30"),
        )
    )
    await publish(operations, day)
    days = await runtime.lifts.display_days()
    assert len(days) == 1 and days[0].lifts and days[0].bike_park
    await runtime.lifts.cancel_day(service_date=day, admin_user_id=1)
    assert (await runtime.lifts.display_days())[0].bike_park
    spec = CommandInput(request_id=uuid4(), action="cancel_bike_park", service_date=day)
    preview = await operations.preview(spec)
    await operations.execute(
        spec.model_copy(update={"expected_digest": preview["digest"]}), actor_user_id=1, identity={}
    )
    assert await runtime.lifts.bike_park.list() == ()


async def test_preview_price_change_requires_new_confirmation(park_setup):
    _, _, telegram, runtime, operations, day = park_setup
    spec = CommandInput(
        request_id=uuid4(), action="bike_park", service_date=day, start_time="13:00"
    )
    preview = await operations.preview(spec)
    await runtime.terms.adjust_bike_park_price(1, admin_user_id=1)
    with pytest.raises(ValueError, match="Data changed"):
        await operations.execute(
            spec.model_copy(update={"expected_digest": preview["digest"]}),
            actor_user_id=1,
            identity={},
        )
    assert not telegram.sent


async def test_replace_only_last_unfilled_lift_and_keep_other_riders(park_setup):
    _, _, _, runtime, operations, day = park_setup
    result = await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("10:00", "13:30"),
        )
    )
    await runtime.lifts.track_poll_answer(
        poll_id=result.poll_id or "",
        telegram_user_id=10,
        username=None,
        full_name="Alice",
        option_ids=(0, 2),
    )
    invalid = CommandInput(
        request_id=uuid4(),
        action="bike_park",
        service_date=day,
        start_time="13:30",
        replace_lift_time="10:00",
    )
    with pytest.raises(ValueError, match="Only the last"):
        await operations.preview(invalid)
    spec = invalid.model_copy(update={"replace_lift_time": "13:30"})
    preview = await operations.preview(spec)
    assert "Alice" in preview["details"] and "vote again" in preview["details"]
    await operations.execute(
        spec.model_copy(update={"expected_digest": preview["digest"]}), actor_user_id=1, identity={}
    )
    days = await runtime.lifts.status_days()
    assert days[0].lifts[-1].cancelled
    assert not days[0].lifts[0].cancelled
    assert (await runtime.lifts.bike_park.list())[0].riders == ()


async def test_deleted_park_poll_releases_date_without_affecting_regular_day(park_setup):
    _, _, telegram, runtime, operations, day = park_setup
    telegram.existing_message_ids = set()
    await publish(operations, day)
    telegram.existing_message_ids.clear()
    await runtime.lifts.bike_park.reconcile()
    assert await runtime.lifts.bike_park.list() == ()
    await publish(operations, day)
    assert len(telegram.sent) == 2


async def test_unknown_send_outcome_blocks_duplicate_and_keeps_ordinary_lift(park_setup):
    db, _, telegram, runtime, operations, day = park_setup
    await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("13:30", "13:30"),
        )
    )
    telegram.fail_send_once = True
    with pytest.raises(RuntimeError):
        await publish(operations, day, replace_lift_time="13:30")
    assert not (await runtime.lifts.status_days())[0].lifts[0].cancelled
    assert (await runtime.lifts.bike_park.list())[0].status == "sent_unconfirmed"
    with pytest.raises(ValueError, match="already has"):
        await publish(operations, day)
    async with db.session() as session:
        assert await session.scalar(select(func.count()).select_from(BikeParkSession)) == 1


async def test_api_shows_park_only_day_and_rider_view_does_not_leak_other_riders(park_setup):
    from tests.unit.test_web_api import SECRET, cookie

    db, config, _, runtime, operations, day = park_setup
    await publish(operations, day)
    await runtime.lifts.track_poll_answer(
        poll_id="poll-42",
        telegram_user_id=99,
        username=None,
        full_name="Private rider",
        option_ids=(0,),
    )
    app = create_app(
        settings=config,
        web_settings=WebSettings(session_secret=SECRET, public_url="http://localhost:5173"),
        session_factory=db.session,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:5173"
    ) as client:
        client.cookies.set("veloexpress_session", cookie(1))
        response = await client.get("/api/admin/days")
        assert response.status_code == 200
        days = response.json()
        assert len(days) == 1 and days[0]["bike_park"]["price_gel"] == 5
        detail = await client.get(f"/api/admin/days/{day}")
        assert detail.status_code == 200
        assert detail.json()["bike_park"]["riders"][0]["label"] == "Private rider"
        client.cookies.set("veloexpress_session", cookie(99))
        personal = await client.get("/api/my-days")
        assert personal.status_code == 200
        park = personal.json()["bike_parks"][0]
        assert park["attending"] and park["rider_count"] == 1
        assert "riders" not in park
        assert (await client.get("/api/admin/days")).status_code == 403


def test_session_copy_and_dates():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 28, 12, tzinfo=ZoneInfo("Asia/Tbilisi"))
    assert validate_bike_park_date(now.date(), "13:00", now) == "13:00"
    for day, value in (
        (now.date(), "11:00"),
        (now.date(), "25:00"),
        (now.date() + timedelta(days=8), "13:00"),
    ):
        with pytest.raises(ValueError):
            validate_bike_park_date(day, value, now)
    draft = render_bike_park_poll(now.date(), "13:00", 10000)
    assert len(draft.question) <= 300


async def test_topic_posts_on_a_park_day_record_no_payment(park_setup):
    from datetime import datetime, time

    db, _, _, runtime, operations, day = park_setup
    regular = await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("13:30", "13:30"),
        )
    )
    for uid in range(100, 105):
        await runtime.lifts.track_poll_answer(
            poll_id=regular.poll_id or "",
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(0,),
        )
    await publish(operations, day)
    async with db.session() as session:
        park_poll = await session.scalar(select(BikeParkSession.poll_id))
    await runtime.lifts.track_poll_answer(
        poll_id=park_poll or "",
        telegram_user_id=100,
        username=None,
        full_name="Rider 100",
        option_ids=(0,),
    )
    from tests.unit.test_payments_service import FakeTelegramClient as PaymentClient

    runtime.payments._telegram_client = PaymentClient()
    posted = datetime.combine(day, time(16), runtime.lifts.bike_park.zone)
    await runtime.payments.record_topic_post(telegram_user_id=100, posted_at=posted)
    await runtime.payments.record_topic_post(telegram_user_id=101, posted_at=posted)
    async with db.session() as session:
        assert await session.scalar(select(func.count()).select_from(PaymentEntry)) == 0
    # A deliberate ordinary-lift payment remains available to the park rider.
    await runtime.payments.claim(
        service_date=day,
        telegram_user_id=100,
        username=None,
        full_name="Rider 100",
        method="transfer",
    )
    async with db.session() as session:
        rows = list(await session.scalars(select(PaymentEntry)))
        assert [row.telegram_user_id for row in rows] == [100]
        assert rows[0].amount_gel == (await runtime.terms.values()).price_gel


async def test_lift_reaching_minimum_during_park_publication_is_not_cancelled(
    park_setup, monkeypatch
):
    _, _, telegram, runtime, operations, day = park_setup
    regular = await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("13:30", "13:30"),
        )
    )
    original = telegram.send_poll

    async def send_with_new_votes(**kwargs):
        result = await original(**kwargs)
        for uid in range(100, 105):
            await runtime.lifts.track_poll_answer(
                poll_id=regular.poll_id or "",
                telegram_user_id=uid,
                username=None,
                full_name=f"Rider {uid}",
                option_ids=(0,),
            )
        return result

    monkeypatch.setattr(telegram, "send_poll", send_with_new_votes)
    result = await publish(operations, day, replace_lift_time="13:30")
    assert "was kept" in result["message"]
    assert not (await runtime.lifts.status_days())[0].lifts[0].cancelled
    assert await runtime.lifts.bike_park.list()


async def test_park_scope_and_settings_are_isolated(park_setup):
    db, config, telegram, runtime, operations, day = park_setup
    await publish(operations, day)
    other = build_runtime(
        settings=config.model_copy(update={"app_env": "other"}),
        session_factory=db.session,
        telegram_client=telegram,
    )
    assert await other.lifts.bike_park.list() == ()
    await other.terms.adjust_bike_park_price(1, admin_user_id=1)
    assert (await runtime.terms.values()).bike_park_price_gel == 5
    await publish(Operations(settings=other.lifts._settings, runtime=other), day)
    assert (await other.lifts.bike_park.list())[0].price_gel == 6
    assert (await runtime.lifts.bike_park.list())[0].price_gel == 5


async def test_bot_creation_uses_the_same_preview_and_published_price(park_setup, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import Chat, Message, User

    from veloexpress_bot.bot import bikepark

    _, config, telegram, runtime, operations, day = park_setup
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    message = Message(
        message_id=1, date=runtime.lifts.bike_park.now(), chat=Chat(id=1, type="private")
    )
    callback = SimpleNamespace(
        data="park:new",
        from_user=User(id=1, is_bot=False, first_name="Admin"),
        message=message,
        answer=AsyncMock(),
    )
    show = AsyncMock()
    monkeypatch.setattr(bikepark, "_show_frozen", show)
    for action in ("park:new", f"park:date:{day}", "park:preview"):
        callback.data = action
        await bikepark.handle_bike_park(callback, state, config, runtime.lifts, operations)
    assert not telegram.sent
    assert "5 GEL" in show.call_args.args[4]
    callback.data = "park:post"
    await bikepark.handle_bike_park(callback, state, config, runtime.lifts, operations)
    assert len(telegram.sent) == 1
    assert (await runtime.lifts.bike_park.list())[0].start_time == "13:00"


async def test_live_monitor_keeps_selected_park_date_when_ordinary_lifts_exist_later(park_setup):
    from veloexpress_bot.bookings.screens import AdminScreen

    _, _, _, runtime, operations, day = park_setup
    await publish(operations, day)
    await runtime.lifts.create_poll(
        PollSetup(
            service_date=day + timedelta(days=1),
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("8:30", "8:30"),
        )
    )
    draft = await runtime.lifts.live_screen_draft(AdminScreen(name="day", service_date=day))
    assert draft and "Bike park" in draft.text and "Reported" not in draft.text
    assert draft.reply_markup
    assert any(
        button.callback_data == f"park:open:{day}"
        for row in draft.reply_markup.inline_keyboard
        for button in row
    )


async def test_replacing_a_prepaid_unfilled_lift_keeps_the_normal_refund_report(park_setup):
    from tests.unit.test_payments_service import _record_payment

    db, _, _, runtime, operations, day = park_setup
    regular = await runtime.lifts.create_poll(
        PollSetup(
            service_date=day,
            created_by_user_id=1,
            cancelled_lift_times=cancelled_lift_times_for_range("13:30", "13:30"),
        )
    )
    await runtime.lifts.track_poll_answer(
        poll_id=regular.poll_id or "",
        telegram_user_id=100,
        username=None,
        full_name="Alice",
        option_ids=(0,),
    )
    await _record_payment(db, runtime.payments, day, 100, 15)
    result = await publish(operations, day, replace_lift_time="13:30")
    assert result["report"] and "15" in result["report"]
    reports = await runtime.payments.recent_refund_reports()
    assert len(reports) == 1 and reports[0].lift_time == "13:30"
    async with db.session() as session:
        assert await session.scalar(select(func.sum(PaymentEntry.amount_gel))) == 15
