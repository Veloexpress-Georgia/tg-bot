from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import LiftDayResult, LiftDaySeat, PaymentEntry
from veloexpress_bot.polls.service import SessionFactory

Period = Literal["30d", "year", "all"]


@dataclass(frozen=True)
class RiderStatistics:
    user_id: int
    label: str
    days: int
    lifts: int
    guests: int
    last_day: date | None
    received_gel: int
    reversed_gel: int
    net_gel: int


@dataclass(frozen=True)
class Statistics:
    period: Period
    start: date
    end: date
    first_record: date | None
    days: int
    lifts: int
    seats: int
    guests: int
    manual: int
    capacity: int
    backfilled_days: int
    received_gel: int
    reversed_gel: int
    net_gel: int
    riders: tuple[RiderStatistics, ...]


class HistoryStatistics:
    def __init__(self, *, settings: Settings, session_factory: SessionFactory) -> None:
        self._settings = settings
        self._sessions = session_factory

    async def read(
        self, *, period: Period, user_id: int | None = None, today: date | None = None
    ) -> Statistics:
        if period not in {"30d", "year", "all"}:
            raise ValueError("Unknown statistics period")
        today = (
            today or datetime.now(UTC).astimezone(ZoneInfo(self._settings.schedule_timezone)).date()
        )
        start = (
            today - timedelta(days=30)
            if period == "30d"
            else date(today.year, 1, 1)
            if period == "year"
            else date.min
        )
        end = today - timedelta(days=1)
        # Money is queried independently: joining it to lifts multiplies a day payment.
        async with self._sessions() as session:
            results = list(
                await session.scalars(
                    select(LiftDayResult).where(
                        LiftDayResult.environment == self._settings.app_env,
                        LiftDayResult.chat_id == self._settings.telegram_target_chat_id,
                        LiftDayResult.thread_id == self._settings.telegram_target_thread_id,
                        LiftDayResult.service_date.between(start, end),
                        LiftDayResult.ran.is_(True),
                        LiftDayResult.cancelled.is_(False),
                    )
                )
            )
            seat_query = (
                select(LiftDaySeat)
                .where(
                    LiftDaySeat.environment == self._settings.app_env,
                    LiftDaySeat.chat_id == self._settings.telegram_target_chat_id,
                    LiftDaySeat.thread_id == self._settings.telegram_target_thread_id,
                    LiftDaySeat.service_date.between(start, end),
                    LiftDaySeat.telegram_user_id != 0,
                )
                .order_by(LiftDaySeat.service_date)
            )
            money_query = select(PaymentEntry).where(
                PaymentEntry.environment == self._settings.app_env,
                PaymentEntry.chat_id == self._settings.telegram_target_chat_id,
                PaymentEntry.thread_id == self._settings.telegram_target_thread_id,
                PaymentEntry.service_date.between(start, end),
            )
            if user_id is not None:
                seat_query = seat_query.where(LiftDaySeat.telegram_user_id == user_id)
                money_query = money_query.where(PaymentEntry.telegram_user_id == user_id)
            raw_seats = list(await session.scalars(seat_query))
            money = list(await session.scalars(money_query))
        ran = {(r.service_date, r.lift_time) for r in results}
        seats = [s for s in raw_seats if (s.service_date, s.lift_time) in ran]
        by_user: dict[int, list[LiftDaySeat]] = defaultdict(list)
        payments: dict[int, list[PaymentEntry]] = defaultdict(list)
        for seat in seats:
            by_user[seat.telegram_user_id].append(seat)
        for entry in money:
            if entry.telegram_user_id != 0:
                payments[entry.telegram_user_id].append(entry)
        riders = []
        for uid in by_user.keys() | payments.keys():
            own = by_user[uid]
            own_rides = [seat for seat in own if seat.seats > seat.guests]
            entries = payments[uid]
            riders.append(
                RiderStatistics(
                    user_id=uid,
                    label=own[-1].label if own else f"Rider {uid}",
                    days=len({s.service_date for s in own_rides}),
                    lifts=len({(s.service_date, s.lift_time) for s in own_rides}),
                    guests=sum(s.guests for s in own),
                    last_day=max((s.service_date for s in own_rides), default=None),
                    received_gel=sum(e.amount_gel for e in entries if e.kind == "received"),
                    reversed_gel=-sum(e.amount_gel for e in entries if e.kind == "reversal"),
                    net_gel=sum(e.amount_gel for e in entries),
                )
            )
        if user_id is not None:
            own_lifts = {(s.service_date, s.lift_time) for s in seats}
            results = [r for r in results if (r.service_date, r.lift_time) in own_lifts]
        return Statistics(
            period=period,
            start=start,
            end=end,
            first_record=min((r.service_date for r in results), default=None),
            days=len({r.service_date for r in results}),
            lifts=len(results),
            seats=sum(s.seats for s in seats)
            if user_id is not None
            else sum(r.seats for r in results),
            guests=sum(s.guests for s in seats)
            if user_id is not None
            else sum(r.guest_seats for r in results),
            manual=0 if user_id is not None else sum(r.manual_seats for r in results),
            capacity=sum(r.capacity for r in results),
            backfilled_days=len({r.service_date for r in results if r.source == "backfilled"}),
            received_gel=sum(e.amount_gel for e in money if e.kind == "received"),
            reversed_gel=-sum(e.amount_gel for e in money if e.kind == "reversal"),
            net_gel=sum(e.amount_gel for e in money),
            riders=tuple(sorted(riders, key=lambda r: (-r.days, -r.lifts, r.user_id))),
        )
