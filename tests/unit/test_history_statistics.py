from datetime import UTC, date, datetime

from tests.unit.test_payments_service import SharedDatabase, settings

from veloexpress_bot.db.models import LiftDayResult, LiftDaySeat, PaymentEntry
from veloexpress_bot.history.service import HistoryStatistics


async def test_statistics_do_not_multiply_money_by_lifts_or_count_guests_as_riders() -> None:
    db = SharedDatabase()
    await db.create()
    config = settings()
    day = date(2026, 9, 12)
    async with db.session() as session:
        for time in ("8:30", "10:00"):
            session.add(
                LiftDayResult(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    lift_time=time,
                    ran=True,
                    seats=4,
                    guest_seats=1,
                    manual_seats=2,
                    capacity=10,
                    source="backfilled",
                )
            )
            session.add(
                LiftDaySeat(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    lift_time=time,
                    telegram_user_id=100,
                    label="<Alice>",
                    seats=2,
                    guests=1,
                )
            )
            session.add(
                LiftDaySeat(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    lift_time=time,
                    telegram_user_id=0,
                    seats=2,
                )
            )
        for amount, kind in ((60, "received"), (-15, "reversal")):
            session.add(
                PaymentEntry(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    telegram_user_id=100,
                    amount_gel=amount,
                    kind=kind,
                    recorded_at=datetime(2026, 9, 20, tzinfo=UTC),
                )
            )
        await session.commit()
    service = HistoryStatistics(settings=config, session_factory=db.session)
    snapshot = await service.read(period="30d", today=date(2026, 9, 21))
    assert snapshot.days == 1 and snapshot.lifts == 2
    assert snapshot.seats == 8 and snapshot.capacity == 20
    assert snapshot.guests == 2 and snapshot.manual == 4
    assert snapshot.backfilled_days == 1
    assert len(snapshot.riders) == 1
    rider = snapshot.riders[0]
    assert (rider.days, rider.lifts, rider.guests, rider.net_gel) == (1, 2, 2, 45)
    assert snapshot.net_gel == 45
    own = await service.read(period="30d", user_id=100, today=date(2026, 9, 21))
    assert own.net_gel == 45 and len(own.riders) == 1
    other = await service.read(period="30d", user_id=101, today=date(2026, 9, 21))
    assert other.riders == () and other.net_gel == 0
    await db.dispose()


async def test_period_scope_and_payments_without_rides_are_preserved() -> None:
    db = SharedDatabase()
    await db.create()
    async with db.session() as session:
        for day, amount, env in (
            (date(2026, 8, 22), 10, "test"),
            (date(2026, 8, 21), 20, "test"),
            (date(2026, 9, 21), 30, "test"),
            (date(2026, 9, 12), 1000, "other"),
        ):
            session.add(
                PaymentEntry(
                    environment=env,
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    telegram_user_id=100,
                    amount_gel=amount,
                    kind="received",
                )
            )
        await session.commit()
    # Statistics remain readable with payment reporting disabled.
    service = HistoryStatistics(settings=settings(payments_thread=None), session_factory=db.session)
    recent = await service.read(period="30d", user_id=100, today=date(2026, 9, 21))
    year = await service.read(period="year", user_id=100, today=date(2026, 9, 21))
    assert recent.net_gel == 10 and recent.days == 0
    assert year.net_gel == 30 and year.riders[0].lifts == 0
    assert recent.start == date(2026, 8, 22)
    await db.dispose()


async def test_statistic_rendering_escapes_names_and_keeps_admin_period_controls() -> None:
    from dataclasses import replace

    from veloexpress_bot.history.render import render_statistics
    from veloexpress_bot.history.service import RiderStatistics

    db = SharedDatabase()
    await db.create()
    empty = await HistoryStatistics(settings=settings(), session_factory=db.session).read(
        period="30d", today=date(2026, 9, 21)
    )
    rider = RiderStatistics(100, "<Alice>", 1, 2, 1, date(2026, 9, 12), 45, 15, 30)
    view = replace(empty, riders=(rider,))
    draft = render_statistics(view, personal=False)
    assert "&lt;Alice&gt;" in draft.text and "<Alice>" not in draft.text
    detail = render_statistics(view, personal=True, page=2, admin_user_id=100)
    assert detail.reply_markup is not None
    callbacks = [b.callback_data for row in detail.reply_markup.inline_keyboard for b in row]
    assert "astats:year:100:2" in callbacks
    assert "stats:30d:2" in callbacks
    assert not any(c and c.startswith("rstats") for c in callbacks)

    # Admin overview: the period rides along into the days list and back out of
    # it, and there is a way out that is not "open a day and press back".
    overview = render_statistics(replace(view, period="year"), personal=False)
    assert overview.reply_markup is not None
    overview_callbacks = [
        b.callback_data for row in overview.reply_markup.inline_keyboard for b in row
    ]
    assert "mon:history:0:year" in overview_callbacks
    assert "mon:menu" in overview_callbacks
    await db.dispose()


async def test_hosting_guest_seats_does_not_invent_a_personal_ride() -> None:
    db = SharedDatabase()
    await db.create()
    async with db.session() as session:
        session.add(
            LiftDayResult(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                service_date=date(2026, 9, 12),
                lift_time="8:30",
                ran=True,
                seats=1,
                guest_seats=1,
                capacity=10,
            )
        )
        session.add(
            LiftDaySeat(
                environment="test",
                chat_id=-100123,
                thread_id=7,
                service_date=date(2026, 9, 12),
                lift_time="8:30",
                telegram_user_id=100,
                label="Host",
                seats=1,
                guests=1,
            )
        )
        await session.commit()
    view = await HistoryStatistics(settings=settings(), session_factory=db.session).read(
        period="all", today=date(2026, 9, 21)
    )
    assert view.riders[0].days == 0 and view.riders[0].lifts == 0
    assert view.riders[0].guests == 1
    await db.dispose()
