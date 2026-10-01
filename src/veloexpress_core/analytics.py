"""Historical analytics. Money and seats are read independently to avoid fan-out."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import LiftDayResult, LiftDaySeat, PaymentEntry
from veloexpress_core.lifts import SessionFactory


def _scope(query: Any, model: Any, settings: Settings) -> Any:
    return query.where(
        model.environment == settings.app_env,
        model.chat_id == settings.telegram_target_chat_id,
        model.thread_id == settings.telegram_target_thread_id,
    )


def _bucket(day: date, granularity: str) -> date:
    if granularity == "year":
        return date(day.year, 1, 1)
    if granularity == "month":
        return day.replace(day=1)
    return day


def _next_bucket(day: date, granularity: str) -> date:
    if granularity == "year":
        return date(day.year + 1, 1, 1)
    if granularity == "month":
        return date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return day + timedelta(days=1)


class DashboardAnalytics:
    def __init__(self, *, settings: Settings, session_factory: SessionFactory) -> None:
        self.settings = settings
        self.sessions = session_factory

    async def first_record(self, *, user_id: int | None = None) -> date | None:
        async with self.sessions() as session:
            model = LiftDayResult if user_id is None else LiftDaySeat
            query = _scope(select(model.service_date), model, self.settings)
            money = _scope(select(PaymentEntry.service_date), PaymentEntry, self.settings)
            if user_id is not None:
                query = query.where(LiftDaySeat.telegram_user_id == user_id)
                money = money.where(PaymentEntry.telegram_user_id == user_id)
            first_ride = await session.scalar(query.order_by(model.service_date).limit(1))
            first_money = await session.scalar(money.order_by(PaymentEntry.service_date).limit(1))
        return min((d for d in (first_ride, first_money) if d is not None), default=None)

    async def read(
        self, *, start: date, end: date, today: date, user_id: int | None = None
    ) -> dict[str, Any]:
        if end < start:
            raise ValueError("End date must not precede start date.")
        end = min(end, today - timedelta(days=1))
        previous_end = start - timedelta(days=1)
        previous_start = start - timedelta(days=max((end - start).days + 1, 1))
        async with self.sessions() as session:
            result_query = _scope(select(LiftDayResult), LiftDayResult, self.settings).where(
                LiftDayResult.service_date <= end
            )
            seat_query = _scope(select(LiftDaySeat), LiftDaySeat, self.settings).where(
                LiftDaySeat.service_date <= end, LiftDaySeat.telegram_user_id != 0
            )
            money_query = (
                _scope(select(PaymentEntry), PaymentEntry, self.settings)
                .where(PaymentEntry.service_date.between(previous_start, end))
                .order_by(PaymentEntry.recorded_at, PaymentEntry.id)
            )
            if user_id is not None:
                seat_query = seat_query.where(LiftDaySeat.telegram_user_id == user_id)
                money_query = money_query.where(PaymentEntry.telegram_user_id == user_id)
            results = list(await session.scalars(result_query))
            seats = list(await session.scalars(seat_query))
            money = list(await session.scalars(money_query))
        ran = {(r.service_date, r.lift_time) for r in results if r.ran and not r.cancelled}
        seats_in_ran = [s for s in seats if (s.service_date, s.lift_time) in ran]
        if user_id is not None:
            own = {(s.service_date, s.lift_time) for s in seats_in_ran}
            results = [r for r in results if (r.service_date, r.lift_time) in own]
        current_results = [r for r in results if start <= r.service_date <= end]
        current_seats = [s for s in seats_in_ran if start <= s.service_date <= end]
        current_money = [m for m in money if start <= m.service_date <= end]
        previous_results = [r for r in results if previous_start <= r.service_date <= previous_end]
        previous_seats = [
            s for s in seats_in_ran if previous_start <= s.service_date <= previous_end
        ]
        previous_money = [m for m in money if previous_start <= m.service_date <= previous_end]
        summary = _summary(
            current_results, current_seats, current_money, personal=user_id is not None
        )
        previous = _summary(
            previous_results, previous_seats, previous_money, personal=user_id is not None
        )
        granularity = (
            "day" if (end - start).days <= 62 else "month" if (end - start).days <= 1461 else "year"
        )
        series = []
        cursor = _bucket(start, granularity)
        while cursor <= end:
            group_results = [
                r for r in current_results if _bucket(r.service_date, granularity) == cursor
            ]
            group_seats = [
                s for s in current_seats if _bucket(s.service_date, granularity) == cursor
            ]
            group_money = [
                m for m in current_money if _bucket(m.service_date, granularity) == cursor
            ]
            series.append(
                {
                    "date": cursor.isoformat(),
                    **_summary(
                        group_results, group_seats, group_money, personal=user_id is not None
                    ),
                }
            )
            cursor = _next_bucket(cursor, granularity)
        first_seen: dict[int, date] = {}
        for seat in seats_in_ran:
            if seat.seats > seat.guests:
                first_seen[seat.telegram_user_id] = min(
                    first_seen.get(seat.telegram_user_id, seat.service_date), seat.service_date
                )
        riders = []
        for uid in {s.telegram_user_id for s in current_seats} | {
            m.telegram_user_id for m in current_money if m.telegram_user_id != 0
        }:
            own_seats = [s for s in current_seats if s.telegram_user_id == uid]
            own_rides = [s for s in own_seats if s.seats > s.guests]
            riders.append(
                {
                    "user_id": uid,
                    "label": max(own_seats, key=lambda s: s.service_date).label
                    if own_seats
                    else f"Rider {uid}",
                    "days": len({s.service_date for s in own_rides}),
                    "lifts": len(own_rides),
                    "guests": sum(s.guests for s in own_seats),
                    "net_gel": sum(
                        m.amount_gel for m in current_money if m.telegram_user_id == uid
                    ),
                    "last_day": max((s.service_date for s in own_rides), default=None),
                    "new": start <= first_seen.get(uid, date.min) <= end,
                }
            )
        summary["new_riders"] = sum(r["new"] for r in riders if r["days"])
        days = []
        for day in sorted(
            {r.service_date for r in current_results} | {m.service_date for m in current_money},
            reverse=True,
        ):
            day_results = [r for r in current_results if r.service_date == day]
            days.append(
                {
                    "date": day.isoformat(),
                    **_summary(
                        day_results,
                        [s for s in current_seats if s.service_date == day],
                        [m for m in current_money if m.service_date == day],
                        personal=user_id is not None,
                    ),
                }
            )
        by_time = []
        for lift_time in sorted(
            {r.lift_time for r in current_results}, key=lambda t: tuple(map(int, t.split(":")))
        ):
            group = [r for r in current_results if r.lift_time == lift_time]
            by_time.append(
                {
                    "time": lift_time,
                    **_summary(
                        group,
                        [s for s in current_seats if s.lift_time == lift_time],
                        [],
                        personal=user_id is not None,
                    ),
                }
            )
        return {
            "start": start,
            "end": end,
            "granularity": granularity,
            "first_record": await self.first_record(user_id=user_id),
            "summary": summary,
            "previous": previous,
            "series": series,
            "by_time": by_time,
            "riders": sorted(riders, key=lambda r: (-r["days"], -r["lifts"], r["user_id"])),
            "days": days,
        }


def _summary(
    results: list[LiftDayResult],
    seats: list[LiftDaySeat],
    money: list[PaymentEntry],
    *,
    personal: bool,
) -> dict[str, Any]:
    running = [r for r in results if r.ran and not r.cancelled]
    expected = (
        sum(
            s.seats
            * next(
                (
                    r.price_gel
                    for r in running
                    if r.service_date == s.service_date and r.lift_time == s.lift_time
                ),
                0,
            )
            for s in seats
        )
        if personal
        else sum(r.seats * r.price_gel for r in running)
    )
    occupied = sum(s.seats for s in seats) if personal else sum(r.seats for r in running)
    capacity = sum(r.capacity for r in running)
    net = sum(m.amount_gel for m in money)
    methods: dict[tuple[date, int], dict[str, int]] = defaultdict(
        lambda: {"cash": 0, "transfer": 0, "unknown": 0}
    )
    for entry in money:
        balance = methods[(entry.service_date, entry.telegram_user_id)]
        method = entry.method if entry.method in {"cash", "transfer"} else "unknown"
        if entry.kind == "method_change":
            total = sum(balance.values())
            for key in balance:
                balance[key] = 0
            balance[method] = total
        else:
            balance[method] += entry.amount_gel
    return {
        "days": len({r.service_date for r in running}),
        "lifts": len(running),
        "seats": occupied,
        "capacity": capacity,
        "occupancy_pct": round(occupied / capacity * 100, 1) if capacity else 0,
        "riders": len({s.telegram_user_id for s in seats if s.seats > s.guests}),
        "guests": sum(s.guests for s in seats) if personal else sum(r.guest_seats for r in running),
        "manual": 0 if personal else sum(r.manual_seats for r in running),
        "expected_gel": expected,
        "received_gel": sum(m.amount_gel for m in money if m.kind == "received"),
        "reversed_gel": -sum(m.amount_gel for m in money if m.kind == "reversal"),
        "net_gel": net,
        "cash_gel": sum(b["cash"] for b in methods.values()),
        "transfer_gel": sum(b["transfer"] for b in methods.values()),
        "unknown_gel": sum(b["unknown"] for b in methods.values()),
        "gap_gel": max(expected - net, 0),
        "cancelled_lifts": sum(r.cancelled for r in results),
        "backfilled_days": len({r.service_date for r in results if r.source == "backfilled"}),
    }
