import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
from sqlalchemy import select
from tests.unit.test_poll_service import (
    FakeTelegramClient,
    SharedDatabase,
    _fill_lift,
    _upcoming_weekend,
    settings,
)

from veloexpress_bot.db.models import BookingOrderChange, PollVote
from veloexpress_core.lifts import PollPostingService, PollSetup


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


async def setup_order(database: SharedDatabase):
    client = FakeTelegramClient()
    service = PollPostingService(
        settings=settings(), session_factory=database.session, telegram_client=client
    )
    day, _ = _upcoming_weekend()
    poll = await service.create_poll(PollSetup(service_date=day, created_by_user_id=1))
    poll_id = poll.poll_id or ""
    await _fill_lift(service, poll_id, riders=12)
    return service, client, day, poll_id


async def test_order_shows_transfer_cash_and_unpaid_riders_and_rechecks_payments(
    db: SharedDatabase,
):
    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _record_payment, _setup

    service, payments, _client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    await _fill_lift(service, poll_id, riders=12)
    await payments.claim(
        service_date=day, telegram_user_id=109, username="rider9", full_name="Rider 9"
    )
    await payments.claim(
        service_date=day,
        telegram_user_id=100,
        username="rider0",
        full_name="Rider 0",
        method="cash",
    )
    view = await service.booking_order_view(service_date=day, lift_time="8:30")
    riders = {r["user_id"]: r for r in view["riders"]}
    assert riders[109]["paid"] is True and riders[109]["paid_gel"] == 15
    assert riders[100]["paid"] is True and riders[100]["cash"] is True
    assert riders[101]["paid"] is False and riders[101]["paid_gel"] == 0
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    assert preview["paid_demoted"] == [109]
    assert "Already-paid bookings will move to the waitlist" in preview["details"]
    assert "@rider9 · waitlist · payment reported" in preview["details"]
    await _record_payment(cast(PaymentsDatabase, db), payments, day, 101, 5)
    refreshed = await service.booking_order_view(service_date=day, lift_time="8:30")
    partial = next(r for r in refreshed["riders"] if r["user_id"] == 101)
    assert partial["paid"] is False and partial["paid_gel"] == 5
    with pytest.raises(ValueError, match="Data changed"):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=ids,
            expected_digest=preview["digest"],
            admin_user_id=1,
        )


async def test_guest_payment_does_not_mark_another_lift_paid(db: SharedDatabase):
    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _record_payment, _setup

    from veloexpress_bot.db.models import GuestSeat

    service, payments, _client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    for uid in range(100, 106):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(0, 1),
        )
    await payments.adjust_guest_seats(
        service_date=day, telegram_user_id=100, lift_times=("8:30",), delta=1
    )
    await _record_payment(cast(PaymentsDatabase, db), payments, day, 100, 30, "cash")
    first = await service.booking_order_view(service_date=day, lift_time="8:30")
    second = await service.booking_order_view(service_date=day, lift_time="10:00")
    first_rider = next(r for r in first["riders"] if r["user_id"] == 100)
    second_rider = next(r for r in second["riders"] if r["user_id"] == 100)
    assert first_rider["paid"] is True
    assert second_rider["paid"] is False and second_rider["paid_gel"] == 30
    async with db.session() as session:
        assert (await session.scalars(select(GuestSeat))).one().count == 1


async def test_reserved_guest_still_uses_money_when_host_moves_to_waitlist(db: SharedDatabase):
    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _record_payment, _setup

    service, payments, _client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    for uid in range(100, 106):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(1, 2, 3),
        )
    await payments.adjust_guest_seats(
        service_date=day, telegram_user_id=100, lift_times=("11:45",), delta=1
    )
    for uid in range(106, 110):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(1, 2, 3),
        )
    ids = [*range(101, 110), 100]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="11:45", ordered_user_ids=ids
    )
    await service.save_booking_order(
        service_date=day,
        lift_time="11:45",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    await _record_payment(cast(PaymentsDatabase, db), payments, day, 100, 30)
    view = await service.booking_order_view(service_date=day, lift_time="13:30")
    assert next(r for r in view["riders"] if r["user_id"] == 100)["paid"] is False


async def test_order_does_not_reuse_money_spent_on_a_deadline_booking(db: SharedDatabase):
    from datetime import UTC, datetime

    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _record_payment, _setup

    service, payments, _client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    for uid in range(100, 106):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(0, 1),
        )
    await _record_payment(cast(PaymentsDatabase, db), payments, day, 100, 15)
    bookings = await payments.day_bookings(day)
    assert bookings is not None
    await payments._capture_roster(bookings, now=datetime.now(UTC), deadline_at=datetime.now(UTC))
    await service.track_poll_answer(
        poll_id=poll_id, telegram_user_id=100, username=None, full_name="Rider 100", option_ids=(1,)
    )
    view = await service.booking_order_view(service_date=day, lift_time="10:00")
    rider = next(r for r in view["riders"] if r["user_id"] == 100)
    assert rider["paid"] is False and rider["paid_gel"] == 15


async def test_bot_order_labels_distinguish_reported_payments_and_promised_cash(db: SharedDatabase):
    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _setup

    from veloexpress_bot.bookings.order_render import render_booking_order

    service, payments, _client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    await _fill_lift(service, poll_id, riders=6)
    await payments.claim(
        service_date=day, telegram_user_id=101, username="rider1", full_name="Rider 1"
    )
    await payments.claim(
        service_date=day,
        telegram_user_id=100,
        username="rider0",
        full_name="Rider 0",
        method="cash",
    )
    view = await service.booking_order_view(service_date=day, lift_time="8:30")
    draft = render_booking_order(
        {"token": "test", "date": day.isoformat(), "time": "8:30", "view": view}
    )
    assert "@rider1 · payment reported" in draft.text
    assert "@rider0 · cash on site" in draft.text
    assert "@rider2 · payment not reported" in draft.text


async def test_reorder_is_atomic_audited_and_preserves_vote_times(db: SharedDatabase):
    service, client, day, _poll_id = await setup_order(db)
    async with db.session() as session:
        times = {
            v.telegram_user_id: v.option_booked_at for v in await session.scalars(select(PollVote))
        }
    order = await service.booking_order_view(service_date=day, lift_time="8:30")
    ids = [r["user_id"] for r in order["riders"]]
    ids.remove(111)
    ids.insert(9, 111)
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    assert preview["promoted"] == [111]
    assert preview["demoted"] == [109]
    before_messages = len(client.sent_texts)
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    detail = await service.lift_detail(service_date=day, lift_time="8:30")
    assert detail is not None
    assert [r.telegram_user_id for r in detail[1]] == ids
    assert next(r for r in detail[1] if r.telegram_user_id == 111).waitlisted is False
    assert next(r for r in detail[1] if r.telegram_user_id == 109).waitlisted is True
    notices = client.sent_texts[before_messages:]
    assert len([n for n in notices if "Booking order updated" in n]) == 1
    notice = next(n for n in notices if "Booking order updated" in n)
    assert "tg://user?id=111" in notice and "tg://user?id=109" in notice
    async with db.session() as session:
        assert times == {
            v.telegram_user_id: v.option_booked_at for v in await session.scalars(select(PollVote))
        }
        audit = (await session.scalars(select(BookingOrderChange))).one()
        assert audit.admin_user_id == 1 and json.loads(audit.after_order) == ids


async def test_new_bookings_append_and_rejoining_loses_override(db: SharedDatabase):
    service, _client, day, poll_id = await setup_order(db)
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    for options in [(0, 1), (1,), (0, 1)]:
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=111,
            username=None,
            full_name="Rider 11",
            option_ids=options,
        )
        current = await service.booking_order_view(service_date=day, lift_time="8:30")
        if options == (0, 1) and current["riders"][0]["user_id"] == 111:
            assert [r["user_id"] for r in current["riders"]] == ids
    current = await service.booking_order_view(service_date=day, lift_time="8:30")
    assert current["riders"][-1]["user_id"] == 111
    assert current["previous_positions"]["111"] == 1
    restore = await service.preview_booking_order(
        service_date=day, lift_time="8:30", restore_user_id=111
    )
    assert restore["ordered_user_ids"] == ids
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        restore_user_id=111,
        expected_digest=restore["digest"],
        admin_user_id=1,
    )
    await service.track_poll_answer(
        poll_id=poll_id, telegram_user_id=112, username=None, full_name="New", option_ids=(0,)
    )
    current = await service.booking_order_view(service_date=day, lift_time="8:30")
    assert [r["user_id"] for r in current["riders"]] == [*ids, 112]
    other = await service.booking_order_view(service_date=day, lift_time="10:00")
    assert [r["user_id"] for r in other["riders"]] == [111]


async def test_stale_preview_and_invalid_membership_do_not_write(db: SharedDatabase):
    service, _client, day, poll_id = await setup_order(db)
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    await service.track_poll_answer(
        poll_id=poll_id, telegram_user_id=111, username=None, full_name="Rider 11", option_ids=()
    )
    with pytest.raises(ValueError):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=ids,
            expected_digest=preview["digest"],
            admin_user_id=1,
        )
    with pytest.raises(ValueError):
        await service.preview_booking_order(
            service_date=day, lift_time="8:30", ordered_user_ids=[100, 100]
        )
    with pytest.raises(PermissionError):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=ids,
            expected_digest=preview["digest"],
            admin_user_id=999,
        )
    async with db.session() as session:
        assert not (await session.scalars(select(BookingOrderChange))).all()


async def test_restoration_keeps_priority_when_earlier_riders_have_left(db: SharedDatabase):
    service, _client, day, poll_id = await setup_order(db)
    for uid in (105, 100, 101):
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=uid,
            username=None,
            full_name=f"Rider {uid}",
            option_ids=(),
        )
    await service.track_poll_answer(
        poll_id=poll_id, telegram_user_id=105, username=None, full_name="Rider 105", option_ids=(0,)
    )
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", restore_user_id=105
    )
    assert preview["ordered_user_ids"] == [102, 103, 104, 105, 106, 107, 108, 109, 110, 111]


async def test_order_changes_invalidate_other_admins_previews(db: SharedDatabase):
    service, _client, day, _poll_id = await setup_order(db)
    one = [111, *range(100, 111)]
    two = [110, *range(100, 110), 111]
    first = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=one
    )
    second = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=two
    )
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        ordered_user_ids=one,
        expected_digest=first["digest"],
        admin_user_id=1,
    )
    with pytest.raises(ValueError, match="Data changed"):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=two,
            expected_digest=second["digest"],
            admin_user_id=1,
        )


async def test_reserved_seats_are_preserved_and_changes_invalidate_preview(db: SharedDatabase):
    from veloexpress_bot.db.models import GuestSeat, ManualBookingCount

    service, _client, day, _poll_id = await setup_order(db)
    async with db.session() as session:
        session.add(
            ManualBookingCount(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                service_date=day,
                lift_time="8:30",
                count=1,
                updated_by_user_id=1,
            )
        )
        session.add(
            GuestSeat(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                service_date=day,
                lift_time="8:30",
                host_user_id=100,
                count=2,
            )
        )
        await session.commit()
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    assert preview["available_seats"] == 7
    assert preview["promoted"] == [111] and preview["demoted"] == [106]
    async with db.session() as session:
        guest = (await session.scalars(select(GuestSeat))).one()
        guest.count = 1
        await session.commit()
    with pytest.raises(ValueError, match="Data changed"):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=ids,
            expected_digest=preview["digest"],
            admin_user_id=1,
        )


async def test_cancelling_the_lift_invalidates_a_pending_correction(db: SharedDatabase):
    service, _client, day, _poll_id = await setup_order(db)
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    await service.cancel_lift(service_date=day, lift_time="8:30", admin_user_id=1)
    with pytest.raises(ValueError, match="cancelled"):
        await service.save_booking_order(
            service_date=day,
            lift_time="8:30",
            ordered_user_ids=ids,
            expected_digest=preview["digest"],
            admin_user_id=1,
        )


async def test_admin_api_requires_signed_confirmation_and_executes_via_bot_queue(
    db: SharedDatabase,
):
    from uuid import uuid4

    import httpx
    from tests.unit.test_web_api import SECRET, cookie

    from veloexpress_api.app import create_app
    from veloexpress_api.settings import WebSettings
    from veloexpress_core.commands import CommandQueue, Operations
    from veloexpress_core.runtime import build_runtime

    service, telegram, day, _poll_id = await setup_order(db)
    config = settings().model_copy(update={"telegram_bot_token": "123456:test-token"})
    app = create_app(
        settings=config,
        web_settings=WebSettings(session_secret=SECRET, public_url="http://localhost:5173"),
        session_factory=db.session,
    )
    queue = CommandQueue(settings=config, session_factory=db.session)
    runtime = build_runtime(settings=config, session_factory=db.session, telegram_client=telegram)
    spec = {
        "request_id": str(uuid4()),
        "action": "booking_order",
        "service_date": day.isoformat(),
        "lift_time": "8:30",
        "ordered_user_ids": [111, *range(100, 111)],
    }
    headers = {"X-CSRF-Token": "csrf"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:5173"
    ) as client:
        path = f"/api/admin/days/{day}/lifts/8:30/order"
        client.cookies.set("veloexpress_session", cookie(100))
        assert (await client.get(path)).status_code == 403
        assert (
            await client.post("/api/commands", json={"command": spec}, headers=headers)
        ).status_code == 403
        client.cookies.set("veloexpress_session", cookie(1))
        view = await client.get(path)
        assert view.status_code == 200
        assert [r["user_id"] for r in view.json()["riders"]] == list(range(100, 112))
        assert (
            await client.post("/api/commands", json={"command": spec}, headers=headers)
        ).status_code == 409
        preview = (await client.post("/api/admin/preview", json=spec, headers=headers)).json()
        body = {"command": spec, "confirmation": preview["confirmation"]}
        tampered = {**body, "command": {**spec, "ordered_user_ids": list(range(100, 112))}}
        assert (
            await client.post("/api/commands", json=tampered, headers=headers)
        ).status_code == 409
        submitted = await client.post("/api/commands", json=body, headers=headers)
        assert submitted.status_code == 202
        assert (await service.booking_order_view(service_date=day, lift_time="8:30"))["riders"][0][
            "user_id"
        ] == 100
        await queue.process_one(Operations(settings=config, runtime=runtime))
        result = await queue.get(submitted.json()["id"], actor_user_id=1)
        assert result is not None and result["status"] == "complete"
        assert (await service.booking_order_view(service_date=day, lift_time="8:30"))["riders"][0][
            "user_id"
        ] == 111
        assert (await client.post("/api/commands", json=body, headers=headers)).json()[
            "id"
        ] == result["id"]


async def test_bot_cards_move_a_rider_and_reject_stale_buttons(
    db: SharedDatabase, monkeypatch: pytest.MonkeyPatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from veloexpress_bot.bot import handlers

    service, _client, day, _poll_id = await setup_order(db)
    message_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    message = SimpleNamespace(message_id=message_id, answer=AsyncMock())
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=1), answer=AsyncMock(), data=f"mon:order:{day:%Y%m%d}:0830"
    )
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=555, user_id=1))
    monkeypatch.setattr(handlers, "_admin_private_message", AsyncMock(return_value=message))
    monkeypatch.setattr(handlers, "_edit_card", AsyncMock())
    monkeypatch.setattr(handlers, "_show_lift", AsyncMock())
    await handlers.open_booking_order(callback, settings(), service, state)
    data = (await state.get_data())["booking_order"]
    callback.data = f"ord:rider:{data['token']}:111"
    await handlers.change_booking_order(callback, settings(), service, state)
    data = (await state.get_data())["booking_order"]
    callback.data = f"ord:before:{data['token']}:100"
    await handlers.change_booking_order(callback, settings(), service, state)
    data = (await state.get_data())["booking_order"]
    assert data["preview"]["promoted"] == [111]
    stale = callback.data
    await handlers.change_booking_order(callback, settings(), service, state)
    assert "outdated" in callback.answer.call_args.args[0]

    assert (await service.booking_order_view(service_date=day, lift_time="8:30"))["riders"][0][
        "user_id"
    ] != 111
    callback.data = f"ord:save:{data['token']}"
    await handlers.change_booking_order(callback, settings(), service, state)
    assert (await service.booking_order_view(service_date=day, lift_time="8:30"))["riders"][0][
        "user_id"
    ] == 111
    callback.data = stale
    await handlers.change_booking_order(callback, settings(), service, state)
    assert "outdated" in callback.answer.call_args.args[0]


async def test_queue_only_correction_notifies_the_right_rider_on_next_vacancy(db: SharedDatabase):
    from datetime import UTC, datetime

    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _setup

    service, payments, client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    await _fill_lift(service, poll_id, riders=12)
    await payments.announce_seat_promotions()
    cycle_started = datetime.now(UTC)
    stale_day = await payments.day_bookings(day)
    assert stale_day is not None
    ids = [*range(100, 110), 111, 110]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    assert not any("Booking order updated" in record.text for record in client.sent)
    assert await payments._promotions_for_day(stale_day, now=cycle_started) == []
    await service.track_poll_answer(
        poll_id=poll_id, telegram_user_id=109, username=None, full_name="Rider 9", option_ids=()
    )
    await payments.announce_seat_promotions()
    notices = [r.text for r in client.sent if "off the waitlist" in r.text]
    assert len(notices) == 1 and "tg://user?id=111" in notices[0]
    assert "tg://user?id=110" not in notices[0]
    view = await payments.rider_day(service_date=day, telegram_user_id=111)
    assert view is not None and view.rows[0].waitlist_position == 0
    view = await payments.rider_day(service_date=day, telegram_user_id=110)
    assert view is not None and view.rows[0].waitlist_position == 1


async def test_correction_keeps_payments_and_deadline_roster_and_sends_no_duplicate_ping(
    db: SharedDatabase,
):
    from datetime import UTC, datetime

    from tests.unit.test_payments_service import SharedDatabase as PaymentsDatabase
    from tests.unit.test_payments_service import _setup

    from veloexpress_bot.db.models import DeadlineRoster, PaymentEntry

    service, payments, client, poll_id, day = await _setup(cast(PaymentsDatabase, db))
    await _fill_lift(service, poll_id, riders=12)
    await payments.claim(
        service_date=day, telegram_user_id=109, username="rider9", full_name="Rider 9"
    )
    before_day = await payments.day_bookings(day)
    assert before_day is not None
    await payments._capture_roster(before_day, now=datetime.now(UTC), deadline_at=datetime.now(UTC))
    await payments.announce_seat_promotions()
    async with db.session() as session:
        ledger = [
            (p.id, p.telegram_user_id, p.amount_gel)
            for p in await session.scalars(select(PaymentEntry))
        ]
        roster = [
            (r.id, r.telegram_user_id, r.seats, r.covered_seats)
            for r in await session.scalars(select(DeadlineRoster))
        ]
    ids = [111, *range(100, 111)]
    preview = await service.preview_booking_order(
        service_date=day, lift_time="8:30", ordered_user_ids=ids
    )
    assert preview["deadline_closed"] is True
    await service.save_booking_order(
        service_date=day,
        lift_time="8:30",
        ordered_user_ids=ids,
        expected_digest=preview["digest"],
        admin_user_id=1,
    )
    await payments.announce_seat_promotions()
    notices = [
        r.text
        for r in client.sent
        if "Booking order updated" in r.text or "off the waitlist" in r.text
    ]
    assert len(notices) == 1
    async with db.session() as session:
        assert ledger == [
            (p.id, p.telegram_user_id, p.amount_gel)
            for p in await session.scalars(select(PaymentEntry))
        ]
        assert roster == [
            (r.id, r.telegram_user_id, r.seats, r.covered_seats)
            for r in await session.scalars(select(DeadlineRoster))
        ]
    rider = await payments.rider_day(service_date=day, telegram_user_id=109)
    assert rider is not None and rider.paid_gel == 15
