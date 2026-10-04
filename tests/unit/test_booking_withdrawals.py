from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from tests.unit.test_poll_service import FakeTelegramClient, SharedDatabase, settings

from veloexpress_bot.bookings.render import BookingWithdrawal, render_booking_withdrawals
from veloexpress_bot.bookings.screens import AdminScreen
from veloexpress_bot.db.models import PollVoteEvent, ServiceDayTerms
from veloexpress_core.lifts import PollPostingService, PollSetup


@pytest.mark.asyncio
async def test_withdrawals_keep_every_removed_lift_with_timestamp_and_scope() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        service = PollPostingService(
            settings=settings(), session_factory=db.session, telegram_client=FakeTelegramClient()
        )
        day = date(2026, 10, 4)
        poll = await service.create_poll(
            PollSetup(service_date=day, created_by_user_id=1), pin_after_send=False
        )
        assert poll.poll_id

        async def vote(options: tuple[int, ...]) -> None:
            await service.track_poll_answer(
                poll_id=poll.poll_id or "",
                telegram_user_id=12,
                username="rider",
                full_name="Rider",
                option_ids=options,
            )

        await vote((0, 4))
        await vote((0,))
        await vote((0,))  # A duplicate update is not another cancellation.
        await vote((0, 4))
        await vote(())
        async with db.session() as session:
            terms = await session.scalar(
                select(ServiceDayTerms).where(ServiceDayTerms.service_date == day)
            )
            assert terms
            terms.deadline_time = "21:00"
            events = (await session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.id))).all()
            for event, stamp in zip(
                events,
                (
                    datetime(2026, 10, 3, 14, tzinfo=UTC),
                    datetime(2026, 10, 3, 16, 13, 22, tzinfo=UTC),
                    datetime(2026, 10, 3, 18, tzinfo=UTC),
                    datetime(2026, 10, 4, 1, 13, 51, tzinfo=UTC),
                ),
                strict=True,
            ):
                event.created_at = stamp
            await session.commit()
        events = await service.booking_withdrawals(service_date=day)
        assert [event.lift_time for event in events] == ["8:30", "15:30", "15:30"]
        assert [event.after_deadline for event in events] == [True, True, False]
        assert events[-1].changed_at == datetime(
            2026, 10, 3, 20, 13, 22, tzinfo=ZoneInfo("Asia/Tbilisi")
        )
        assert len({(event.event_id, event.lift_time) for event in events}) == 3
        assert [
            event.lift_time
            for event in await service.booking_withdrawals(service_date=day, lift_time="15:30")
        ] == ["15:30", "15:30"]
        assert await service.booking_withdrawals(service_date=day + timedelta(days=1)) == ()
        other = PollPostingService(
            settings=settings().model_copy(update={"telegram_target_thread_id": 99}),
            session_factory=db.session,
            telegram_client=FakeTelegramClient(),
        )
        assert await other.booking_withdrawals(service_date=day) == ()
        card = await service.lift_screen_view(service_date=day, lift_time="15:30")
        assert card and card.reply_markup
        assert any(
            button.callback_data == "mon:withdrawals:20261004:1530:0"
            for row in card.reply_markup.inline_keyboard
            for button in row
        )
        live = await service.live_screen_draft(
            AdminScreen(name="withdrawals", service_date=day, lift_time="15:30")
        )
        assert live and "04 Oct 2026 · 05:13:51" in live.text
    finally:
        await db.dispose()


def test_bot_withdrawals_are_paginated_without_hiding_names_or_times() -> None:
    stamp = datetime(2026, 10, 4, 5, 13, 51, tzinfo=ZoneInfo("Asia/Tbilisi"))
    events = tuple(
        BookingWithdrawal(
            event_id=i,
            telegram_user_id=i,
            label=f"@rider{i}",
            lift_time="15:30",
            changed_at=stamp,
            after_deadline=True,
        )
        for i in range(25)
    )
    card = render_booking_withdrawals(
        service_date=date(2026, 10, 4),
        lift_time="15:30",
        withdrawals=events,
        page=1,
        timezone="Asia/Tbilisi",
    )
    assert "04 Oct 2026 · 05:13:51" in card.text
    assert "@rider10" in card.text and "@rider19" in card.text
    assert "@rider9 " not in card.text and "@rider20" not in card.text
    assert "Page 2 of 3" in card.text
    assert "Asia/Tbilisi" in card.text
    assert card.reply_markup
    buttons = {button.callback_data for row in card.reply_markup.inline_keyboard for button in row}
    assert "mon:withdrawals:20261004:1530:0" in buttons
    assert "mon:withdrawals:20261004:1530:2" in buttons
    assert "mon:lift:20261004:1530" in buttons
    empty = render_booking_withdrawals(
        service_date=date(2026, 10, 4),
        lift_time=None,
        withdrawals=(),
        page=0,
        timezone="Asia/Tbilisi",
    )
    assert "No recorded withdrawals" in empty.text
    assert AdminScreen(name="withdrawals").live


async def test_open_withdrawal_page_refreshes_after_a_vote_without_switching_screen() -> None:
    from tests.unit.test_poll_service import _upcoming_weekend

    db = SharedDatabase()
    await db.create()
    try:
        telegram = FakeTelegramClient()
        service = PollPostingService(
            settings=settings(), session_factory=db.session, telegram_client=telegram
        )
        day, _ = _upcoming_weekend()
        poll = await service.create_poll(
            PollSetup(service_date=day, created_by_user_id=1), pin_after_send=False
        )
        message_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
        screen = AdminScreen(name="withdrawals", service_date=day, lift_time="15:30", page=1)
        await service.record_screen(admin_user_id=1, screen=screen)
        for options in ((4,), ()):
            await service.track_poll_answer(
                poll_id=poll.poll_id or "",
                telegram_user_id=12,
                username="rider",
                full_name="Rider",
                option_ids=options,
            )
        texts = [text for edited_id, text in telegram.edited_texts if edited_id == message_id]
        assert texts[-1].startswith("🕒 Withdrawals")
        assert "@rider · 15:30" in texts[-1]
        assert (await service.admin_screen(admin_user_id=1)) == screen
    finally:
        await db.dispose()


async def test_api_history_is_admin_only_and_contains_offset_and_seconds() -> None:
    import httpx
    from tests.unit.test_web_api import SECRET, cookie

    from veloexpress_api.app import create_app
    from veloexpress_api.settings import WebSettings

    db = SharedDatabase()
    await db.create()
    try:
        config = settings().model_copy(update={"telegram_bot_token": "123456:test-token"})
        service = PollPostingService(
            settings=config, session_factory=db.session, telegram_client=FakeTelegramClient()
        )
        day = date.today() + timedelta(days=7)
        poll = await service.create_poll(
            PollSetup(service_date=day, created_by_user_id=1), pin_after_send=False
        )
        for options in ((0, 4), (0,)):
            await service.track_poll_answer(
                poll_id=poll.poll_id or "",
                telegram_user_id=12,
                username="rider",
                full_name="Rider",
                option_ids=options,
            )
        stamp = datetime(2026, 10, 3, 14, 13, 22, tzinfo=UTC)
        async with db.session() as session:
            event = await session.scalar(
                select(PollVoteEvent).where(PollVoteEvent.new_option_ids == "0")
            )
            assert event
            event.created_at = stamp
            await session.commit()
        app = create_app(
            settings=config,
            web_settings=WebSettings(session_secret=SECRET, public_url="https://test"),
            session_factory=db.session,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            path = f"/api/admin/days/{day}"
            assert (await client.get(path)).status_code == 401
            client.cookies.set("veloexpress_session", cookie(12))
            assert (await client.get(path)).status_code == 403
            client.cookies.set("veloexpress_session", cookie(1))
            response = await client.get(path)
            assert response.status_code == 200
            history = response.json()["withdrawals"]
            assert len(history) == 1
            assert history[0]["label"] == "@rider"
            assert history[0]["lift_time"] == "15:30"
            assert history[0]["changed_at"] == "2026-10-03T18:13:22+04:00"
    finally:
        await db.dispose()
