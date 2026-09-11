"""What the admin card is showing, and who is allowed to change it.

These are the sequences an admin actually performs — open a lift, walk away,
come back after a vote landed — rather than assertions about which buttons a
card carries. A button list cannot catch a background job replacing the screen
under somebody's thumb, which is the failure these exist for.
"""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from tests.unit.test_poll_service import (
    FakeTelegramClient,
    SharedDatabase,
    _upcoming_weekend,
    settings,
)

from veloexpress_bot.bookings.screens import AdminScreen, decode_screen
from veloexpress_bot.polls.service import PollPostingService, PollSetup


@pytest.fixture
async def db() -> AsyncIterator[SharedDatabase]:
    database = SharedDatabase()
    await database.create()
    try:
        yield database
    finally:
        await database.dispose()


def _service(db: SharedDatabase, client: FakeTelegramClient) -> PollPostingService:
    return PollPostingService(
        settings=settings(),
        session_factory=db.session,
        telegram_client=client,
    )


async def _day_with_riders(
    service: PollPostingService,
    *,
    service_date: date,
    riders: range = range(100, 106),
    option_ids: tuple[int, ...] = (0,),
) -> str:
    poll = await service.create_poll(
        PollSetup(service_date=service_date, created_by_user_id=1),
        pin_after_send=False,
    )
    poll_id = poll.poll_id or ""
    for user_id in riders:
        await service.track_poll_answer(
            poll_id=poll_id,
            telegram_user_id=user_id,
            username=f"rider{user_id}",
            full_name=f"Rider {user_id}",
            option_ids=option_ids,
        )
    return poll_id


def _last_text(client: FakeTelegramClient, message_id: int) -> str:
    texts = [text for edited_id, text in client.edited_texts if edited_id == message_id]
    return texts[-1] if texts else ""


async def test_a_vote_keeps_an_open_lift_card_on_that_lift(db: SharedDatabase) -> None:
    """The refresh redraws the screen the admin is on, not the one it prefers."""
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    poll_id = await _day_with_riders(service, service_date=saturday)
    monitor_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="lift", service_date=saturday, lift_time="8:30"),
    )

    await service.track_poll_answer(
        poll_id=poll_id,
        telegram_user_id=200,
        username="latecomer",
        full_name="Late Comer",
        option_ids=(0,),
    )

    text = _last_text(client, monitor_id)
    assert text.startswith("🚲 8:30 ·")
    # And it is the new figure, not the one the card opened with.
    assert "7/10 seats" in text
    assert (await service.admin_screen(admin_user_id=1)).lift_time == "8:30"


async def test_a_vote_keeps_an_open_all_riders_list_open(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    poll_id = await _day_with_riders(service, service_date=saturday)
    monitor_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="riders", service_date=saturday),
    )

    await service.track_poll_answer(
        poll_id=poll_id,
        telegram_user_id=201,
        username="another",
        full_name="An Other",
        option_ids=(0,),
    )

    assert _last_text(client, monitor_id).startswith("👥 All riders")


async def test_a_cancelled_lift_under_an_open_card_falls_back_to_its_day(
    db: SharedDatabase,
) -> None:
    """The lift is gone, so the card cannot stay on it — but it stays on the day."""
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    monitor_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="lift", service_date=saturday, lift_time="19:00"),
    )

    await service._refresh_booking_monitors()

    assert _last_text(client, monitor_id).startswith("📊 ")
    assert (await service.admin_screen(admin_user_id=1)).name == "day"


async def test_two_admins_hold_different_screens_at_once(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    first = await service.open_booking_monitor(admin_user_id=1, private_chat_id=111)
    second = await service.open_booking_monitor(admin_user_id=2, private_chat_id=222)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="lift", service_date=saturday, lift_time="8:30"),
    )
    await service.record_screen(admin_user_id=2, screen=AdminScreen(name="settings"))

    await service._refresh_booking_monitors()

    assert _last_text(client, first).startswith("🚲 8:30 ·")
    # The second admin is in settings; nothing was written to their card at all.
    assert not [text for message_id, text in client.edited_texts if message_id == second]
    assert (await service.admin_screen(admin_user_id=2)).name == "settings"


async def test_a_deleted_card_comes_back_on_the_same_screen(db: SharedDatabase) -> None:
    """An admin who deletes the card by hand gets a new one, not a dead monitor."""
    live_message_ids: set[int] = set()
    client = FakeTelegramClient(existing_message_ids=live_message_ids)
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    monitor_id = await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="riders", service_date=saturday),
    )
    live_message_ids.discard(monitor_id)

    await service._refresh_booking_monitors()

    assert client.sent_texts[-1].startswith("👥 All riders")
    screen = await service.admin_screen(admin_user_id=1)
    assert screen.name == "riders"


async def test_reopening_the_monitor_resets_to_the_day(db: SharedDatabase) -> None:
    """`/start` is a fresh card, so it never reopens somebody's old settings screen."""
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(admin_user_id=1, screen=AdminScreen(name="settings"))

    await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)

    assert (await service.admin_screen(admin_user_id=1)).name == "day"
    assert client.sent_texts[-1].startswith("📊 ")


async def test_the_selected_day_survives_a_refresh(db: SharedDatabase) -> None:
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, sunday = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    await _day_with_riders(service, service_date=sunday, riders=range(300, 306))
    monitor_id = await service.open_booking_monitor(
        admin_user_id=1,
        private_chat_id=555,
        selected_service_date=sunday,
    )

    await service._refresh_booking_monitors()

    assert str(sunday.day) in _last_text(client, monitor_id).splitlines()[0]
    assert (await service.admin_screen(admin_user_id=1)).service_date == sunday


async def test_a_screen_survives_a_restart(db: SharedDatabase) -> None:
    """The screen lives in the database, so a redeploy does not lose the admin."""
    client = FakeTelegramClient()
    service = _service(db, client)
    saturday, _ = _upcoming_weekend()
    await _day_with_riders(service, service_date=saturday)
    await service.open_booking_monitor(admin_user_id=1, private_chat_id=555)
    await service.record_screen(
        admin_user_id=1,
        screen=AdminScreen(name="stats", period="year", page=2),
    )

    restarted = _service(db, FakeTelegramClient())
    screen = await restarted.admin_screen(admin_user_id=1)

    assert (screen.name, screen.period, screen.page) == ("stats", "year", 2)
    assert not screen.live


async def test_recording_a_screen_without_a_card_is_harmless(db: SharedDatabase) -> None:
    """A stale button from before the admin ever opened a card must not crash."""
    service = _service(db, FakeTelegramClient())

    await service.record_screen(admin_user_id=99, screen=AdminScreen(name="history"))

    assert (await service.admin_screen(admin_user_id=99)) == AdminScreen()


def test_a_screen_survives_an_unreadable_state_column() -> None:
    """Downgrades and hand-edits happen; a broken value reads as the day screen."""
    assert decode_screen("lift", "not json").name == "lift"
    assert decode_screen("lift", "not json").lift_time is None
    assert decode_screen(None, None) == AdminScreen()
    assert decode_screen("stats", '{"page": -4, "rider_id": 0}').page == 0
    assert decode_screen("stats", '{"page": -4, "rider_id": 0}').rider_id is None
    assert decode_screen("history", '{"before": "nonsense"}').before is None


def test_only_live_screens_are_refreshed() -> None:
    assert AdminScreen(name="day").live
    assert AdminScreen(name="riders").live
    assert AdminScreen(name="lift").live
    for name in ("menu", "settings", "plan", "schedule", "extra", "history", "confirm_day"):
        assert not AdminScreen(name=name).live


def test_a_round_trip_keeps_every_field() -> None:
    screen = AdminScreen(
        name="rider",
        service_date=date(2026, 9, 12),
        lift_time="11:45",
        period="year",
        page=3,
        rider_id=77,
        before=date(2026, 8, 1),
    )

    restored = decode_screen(screen.name, screen.to_state(), service_date=screen.service_date)

    assert restored == screen
