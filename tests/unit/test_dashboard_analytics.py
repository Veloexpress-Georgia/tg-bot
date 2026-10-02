from datetime import date

from tests.unit.test_payments_service import SharedDatabase, settings

from veloexpress_bot.db.models import LiftDayResult, LiftDaySeat, PaymentEntry
from veloexpress_core.analytics import DashboardAnalytics


async def test_dashboard_scopes_history_and_does_not_multiply_day_payments() -> None:
    db = SharedDatabase()
    await db.create()
    async with db.session() as session:
        for time in ("8:30", "10:00"):
            session.add(
                LiftDayResult(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=date(2026, 9, 12),
                    lift_time=time,
                    ran=True,
                    seats=4,
                    guest_seats=1,
                    capacity=10,
                    price_gel=20,
                    source="backfilled",
                )
            )
            session.add(
                LiftDaySeat(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=date(2026, 9, 12),
                    lift_time=time,
                    telegram_user_id=42,
                    label="Alice",
                    seats=2,
                    guests=1,
                )
            )
        for env, thread, amount in (("test", 7, 60), ("other", 7, 500), ("test", 8, 900)):
            session.add(
                PaymentEntry(
                    environment=env,
                    chat_id=-100123,
                    thread_id=thread,
                    service_date=date(2026, 9, 12),
                    telegram_user_id=42,
                    amount_gel=amount,
                    kind="received",
                    method="cash",
                )
            )
        await session.commit()
    result = await DashboardAnalytics(settings=settings(), session_factory=db.session).read(
        start=date(2026, 9, 1), end=date(2026, 9, 30), today=date(2026, 10, 1)
    )
    assert result["summary"]["net_gel"] == 60
    assert result["summary"]["seats"] == 8
    assert result["summary"]["riders"] == 1
    assert result["summary"]["expected_gel"] == 160
    assert result["summary"]["backfilled_days"] == 1
    assert result["series"][11]["net_gel"] == 60
    assert result["riders"][0]["days"] == 1
    assert result["riders"][0]["lifts"] == 2
    await db.dispose()


async def test_personal_dashboard_and_empty_period_are_private_and_finite() -> None:
    db = SharedDatabase()
    await db.create()
    result = await DashboardAnalytics(settings=settings(), session_factory=db.session).read(
        start=date(2026, 9, 1), end=date(2026, 9, 30), today=date(2026, 10, 1), user_id=42
    )
    assert result["summary"]["occupancy_pct"] == 0
    assert result["riders"] == []
    assert result["demand"] is None
    assert len(result["series"]) == 30
    await db.dispose()


async def test_demand_keeps_underfilled_lifts_and_unknown_queues_distinct() -> None:
    db = SharedDatabase()
    await db.create()
    async with db.session() as session:
        for day, time, seats, ran, cancelled, waiting, source in (
            (12, "8:30", 10, True, False, 3, "closed"),
            (13, "8:30", 3, False, False, 0, "closed"),
            (19, "8:30", 10, True, False, None, "closed"),
            (20, "8:30", 10, True, False, 8, "backfilled"),
            (26, "8:30", 0, False, True, None, "closed"),
            (12, "10:00", 4, False, False, None, "backfilled"),
        ):
            session.add(
                LiftDayResult(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=date(2026, 9, day),
                    lift_time=time,
                    seats=seats,
                    capacity=10,
                    price_gel=20,
                    ran=ran,
                    cancelled=cancelled,
                    waiting_count=waiting,
                    source=source,
                )
            )
        session.add(
            LiftDayResult(
                environment="other",
                chat_id=-100123,
                thread_id=7,
                service_date=date(2026, 9, 12),
                lift_time="8:30",
                seats=10,
                capacity=10,
                price_gel=20,
                ran=True,
                waiting_count=999,
            )
        )
        await session.commit()
    service = DashboardAnalytics(settings=settings(), session_factory=db.session)
    result = await service.read(
        start=date(2026, 9, 1), end=date(2026, 9, 30), today=date(2026, 10, 1)
    )
    demand = result["demand"]
    assert demand["summary"]["offered_lifts"] == 5
    assert demand["summary"]["not_run_lifts"] == 2
    assert demand["summary"]["waiting_total"] == 3
    row = demand["by_time"][0]
    assert row["time"] == "8:30"
    assert row["full_lifts"] == 3
    assert row["booked_seats"] == 33
    assert row["occupancy_pct"] == 82.5
    assert row["queue_recorded_lifts"] == 2
    assert row["queue_unknown_lifts"] == 2
    assert row["days"][0]["waiting_count"] is None
    assert demand["by_time"][1]["waiting_total"] is None
    personal = await service.read(
        start=date(2026, 9, 1), end=date(2026, 9, 30), today=date(2026, 10, 1), user_id=42
    )
    assert personal["demand"] is None
    await db.dispose()


async def test_method_corrections_and_returning_riders_use_history() -> None:
    db = SharedDatabase()
    await db.create()
    async with db.session() as session:
        for day in (date(2026, 6, 6), date(2026, 9, 12)):
            session.add(
                LiftDayResult(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    lift_time="8:30",
                    ran=True,
                    seats=1,
                    capacity=10,
                    price_gel=15,
                )
            )
            session.add(
                LiftDaySeat(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=day,
                    lift_time="8:30",
                    telegram_user_id=42,
                    label="Alice",
                    seats=1,
                    guests=0,
                )
            )
        for amount, kind, method in ((15, "received", "cash"), (0, "method_change", "transfer")):
            session.add(
                PaymentEntry(
                    environment="test",
                    chat_id=-100123,
                    thread_id=7,
                    service_date=date(2026, 9, 12),
                    telegram_user_id=42,
                    amount_gel=amount,
                    kind=kind,
                    method=method,
                )
            )
        await session.commit()
    result = await DashboardAnalytics(settings=settings(), session_factory=db.session).read(
        start=date(2026, 9, 1), end=date(2026, 9, 30), today=date(2026, 10, 1)
    )
    assert result["summary"]["cash_gel"] == 0
    assert result["summary"]["transfer_gel"] == 15
    assert result["summary"]["new_riders"] == 0
    assert result["riders"][0]["new"] is False
    await db.dispose()
