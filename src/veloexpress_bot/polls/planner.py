from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import PollWeekendPlan
from veloexpress_bot.polls.autoschedule import (
    creation_moment,
    upcoming_service_week_start,
)
from veloexpress_bot.polls.schedule import (
    RangeBoundary,
    cancelled_lift_times_for_range,
    lift_range_from_cancelled,
    select_lift_range_boundary,
)
from veloexpress_bot.polls.service import (
    PollPostingService,
    PollSetup,
    SessionFactory,
)
from veloexpress_bot.polls.weekendplan import (
    DayPlanStatus,
    PlanCardDraft,
    PlanCardView,
    WeekendPlanView,
    render_weekend_plan_card,
)

if TYPE_CHECKING:
    from veloexpress_bot.polls.autoposter import PollAutoScheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeekPlan:
    week_start: date
    saturday_enabled: bool
    sunday_enabled: bool
    cancelled_lift_times: tuple[str, ...]
    updated_by_user_id: int | None

    @property
    def enabled_dates(self) -> tuple[date, ...]:
        dates = []
        if self.saturday_enabled:
            dates.append(self.week_start)
        if self.sunday_enabled:
            dates.append(self.week_start + timedelta(days=1))
        return tuple(dates)


@dataclass(frozen=True)
class PostNowResult:
    created_count: int
    already_posted: bool
    # The days that now have polls, so the caller can offer to open one rather
    # than leaving the admin to find it.
    service_dates: tuple[date, ...] = ()


async def load_week_plan(
    session: AsyncSession,
    *,
    settings: Settings,
    service_week_start: date,
) -> PollWeekendPlan | None:
    return await session.scalar(
        select(PollWeekendPlan)
        .where(PollWeekendPlan.environment == settings.app_env)
        .where(PollWeekendPlan.chat_id == settings.telegram_target_chat_id)
        .where(PollWeekendPlan.thread_id == settings.telegram_target_thread_id)
        .where(PollWeekendPlan.service_week_start == service_week_start)
    )


def plan_from_row(row: PollWeekendPlan) -> WeekPlan:
    return WeekPlan(
        week_start=row.service_week_start,
        saturday_enabled=row.saturday_enabled,
        sunday_enabled=row.sunday_enabled,
        cancelled_lift_times=cancelled_lift_times_for_range(
            row.first_lift_time, row.last_lift_time
        ),
        updated_by_user_id=row.updated_by_user_id,
    )


async def resolve_week_plan(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    poll_service: PollPostingService,
    service_week_start: date,
) -> WeekPlan:
    async with session_factory() as session:
        row = await load_week_plan(
            session,
            settings=settings,
            service_week_start=service_week_start,
        )
    if row is not None:
        return plan_from_row(row)
    return WeekPlan(
        week_start=service_week_start,
        saturday_enabled=True,
        sunday_enabled=True,
        cancelled_lift_times=await poll_service.suggested_cancelled_lift_times(),
        updated_by_user_id=None,
    )


class WeekendPlanner:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        poll_service: PollPostingService,
        auto_scheduler: PollAutoScheduler,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._poll_service = poll_service
        self._auto_scheduler = auto_scheduler

    def current_week_start(self, now: datetime | None = None) -> date:
        now_local = (now or datetime.now(UTC)).astimezone(self._auto_scheduler.zone)
        return upcoming_service_week_start(now_local.date())

    async def load_plan(self, service_week_start: date) -> WeekPlan:
        return await resolve_week_plan(
            session_factory=self._session_factory,
            settings=self._settings,
            poll_service=self._poll_service,
            service_week_start=service_week_start,
        )

    async def plan_card(
        self,
        *,
        view: PlanCardView = "main",
        now: datetime | None = None,
        schedule_label: str = "off",
    ) -> PlanCardDraft:
        plan_view = replace(await self._plan_view(now=now), schedule_label=schedule_label)
        return render_weekend_plan_card(plan_view, view=view)

    async def toggle_day(
        self,
        day_key: str,
        *,
        admin_user_id: int,
        now: datetime | None = None,
    ) -> None:
        if day_key not in {"sat", "sun"}:
            msg = "Unknown weekend day."
            raise ValueError(msg)

        def apply(plan: WeekPlan) -> WeekPlan:
            updated = (
                replace(plan, saturday_enabled=not plan.saturday_enabled)
                if day_key == "sat"
                else replace(plan, sunday_enabled=not plan.sunday_enabled)
            )
            if not updated.saturday_enabled and not updated.sunday_enabled:
                msg = "At least one day must remain."
                raise ValueError(msg)
            return updated

        await self._update_plan(apply, admin_user_id=admin_user_id, now=now)

    async def set_range_boundary(
        self,
        boundary: RangeBoundary,
        lift_time: str,
        *,
        admin_user_id: int,
        now: datetime | None = None,
    ) -> None:
        def apply(plan: WeekPlan) -> WeekPlan:
            cancelled = select_lift_range_boundary(
                plan.cancelled_lift_times,
                boundary=boundary,
                selected_time=lift_time,
            )
            return replace(plan, cancelled_lift_times=cancelled)

        await self._update_plan(apply, admin_user_id=admin_user_id, now=now)

    async def toggle_skip(self, *, admin_user_id: int) -> None:
        await self._auto_scheduler.toggle_skip(admin_user_id=admin_user_id)

    async def post_now(
        self,
        *,
        admin_user_id: int,
        now: datetime | None = None,
    ) -> PostNowResult:
        week = self.current_week_start(now)
        plan = await self.load_plan(week)
        setups = self._setups(plan, created_by_user_id=admin_user_id)
        if not setups:
            return PostNowResult(created_count=0, already_posted=False)

        conflicts = await self._poll_service.find_active_conflicts(setups)
        conflict_dates = {conflict.service_date for conflict in conflicts}
        pending = tuple(setup for setup in setups if setup.service_date not in conflict_dates)
        if not pending:
            return PostNowResult(created_count=0, already_posted=True)

        results = []
        for index, setup in enumerate(pending):
            results.append(
                await self._poll_service.create_poll(
                    setup,
                    include_notice=index == 0 and not conflicts,
                    pin_after_send=False,
                )
            )
        await self._poll_service.pin_created_results(tuple(results))
        await self._record_posted_plan(plan, admin_user_id=admin_user_id)
        return PostNowResult(
            created_count=len(results),
            already_posted=False,
            service_dates=tuple(setup.service_date for setup in pending),
        )

    async def recreate(
        self,
        *,
        admin_user_id: int,
        now: datetime | None = None,
    ) -> tuple[str, int]:
        week = self.current_week_start(now)
        plan = await self.load_plan(week)
        setups = self._setups(plan, created_by_user_id=admin_user_id)
        result = await self._poll_service.recreate_polls(setups)
        cleanup = await self._poll_service.cleanup_recreated_polls(result)
        await self._record_posted_plan(plan, admin_user_id=admin_user_id)
        return result.report_text, cleanup.failed_count

    async def _record_posted_plan(self, plan: WeekPlan, *, admin_user_id: int) -> None:
        await self._poll_service.record_schedule_selection(
            service_dates=plan.enabled_dates,
            cancelled_lift_times=plan.cancelled_lift_times,
            created_by_user_id=admin_user_id,
        )
        await self._auto_scheduler.mark_week_created(plan.week_start)

    def _setups(self, plan: WeekPlan, *, created_by_user_id: int) -> tuple[PollSetup, ...]:
        return tuple(
            PollSetup(
                service_date=service_date,
                created_by_user_id=created_by_user_id,
                cancelled_lift_times=plan.cancelled_lift_times,
            )
            for service_date in plan.enabled_dates
        )

    async def _plan_view(self, *, now: datetime | None = None) -> WeekendPlanView:
        week = self.current_week_start(now)
        plan = await self.load_plan(week)
        saturday = week
        sunday = week + timedelta(days=1)

        probe_setups = tuple(
            PollSetup(service_date=service_date, created_by_user_id=0)
            for service_date in (saturday, sunday)
        )
        conflicts = await self._poll_service.find_active_conflicts(probe_setups)
        posted_dates = {conflict.service_date for conflict in conflicts}

        schedule_state = await self._auto_scheduler.schedule_state()
        opens_at = None
        auto_enabled = False
        skipped = False
        if schedule_state is not None:
            auto_enabled = schedule_state.enabled
            skipped = schedule_state.skip_week_start == week
            if schedule_state.enabled and schedule_state.last_created_week_start != week:
                moment = creation_moment(
                    week,
                    weekday=schedule_state.creation_weekday,
                    creation_time=schedule_state.creation_time,
                    zone=self._auto_scheduler.zone,
                )
                now_local = (now or datetime.now(UTC)).astimezone(self._auto_scheduler.zone)
                if now_local < moment:
                    opens_at = moment

        return WeekendPlanView(
            week_start=week,
            days=(
                DayPlanStatus(
                    service_date=saturday,
                    enabled=plan.saturday_enabled,
                    posted=saturday in posted_dates,
                ),
                DayPlanStatus(
                    service_date=sunday,
                    enabled=plan.sunday_enabled,
                    posted=sunday in posted_dates,
                ),
            ),
            cancelled_lift_times=plan.cancelled_lift_times,
            opens_at=opens_at,
            auto_enabled=auto_enabled,
            skipped=skipped,
        )

    async def _update_plan(
        self,
        apply: Callable[[WeekPlan], WeekPlan],
        *,
        admin_user_id: int,
        now: datetime | None = None,
    ) -> None:
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to plan polls."
            raise ValueError(msg)

        week = self.current_week_start(now)
        current = await self.load_plan(week)
        updated = apply(current)
        first_time, last_time = lift_range_from_cancelled(updated.cancelled_lift_times)
        now = datetime.now(UTC)

        async with self._session_factory() as session:
            row = await load_week_plan(
                session,
                settings=self._settings,
                service_week_start=week,
            )
            if row is None:
                session.add(
                    PollWeekendPlan(
                        environment=self._settings.app_env,
                        chat_id=self._settings.telegram_target_chat_id,
                        thread_id=self._settings.telegram_target_thread_id,
                        service_week_start=week,
                        saturday_enabled=updated.saturday_enabled,
                        sunday_enabled=updated.sunday_enabled,
                        first_lift_time=first_time,
                        last_lift_time=last_time,
                        updated_by_user_id=admin_user_id,
                        updated_at=now,
                    )
                )
            else:
                row.saturday_enabled = updated.saturday_enabled
                row.sunday_enabled = updated.sunday_enabled
                row.first_lift_time = first_time
                row.last_lift_time = last_time
                row.updated_by_user_id = admin_user_id
                row.updated_at = now
            await session.commit()
