from __future__ import annotations

import logging
from asyncio import sleep
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import PollAutoSchedule
from veloexpress_bot.payments.service import PaymentsService
from veloexpress_bot.polls.autoschedule import (
    SHORT_WEEKDAY_LABELS,
    WEEKDAY_LABELS,
    AutoScheduleState,
    CardView,
    ScheduleCardDraft,
    TickAction,
    decide_tick,
    decode_card_time,
    next_announce_lead,
    next_pending_creation,
    render_schedule_announcement,
    render_schedule_card,
    skip_target_week,
)
from veloexpress_bot.polls.planner import resolve_week_plan
from veloexpress_bot.polls.schedule import enabled_lift_count, lift_range_from_cancelled
from veloexpress_bot.polls.service import (
    DuplicatePollError,
    PollPostingService,
    PollSetup,
    SessionFactory,
    TelegramPollClient,
)

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ScheduleRowData:
    state: AutoScheduleState
    announce_message_id: int | None
    updated_by_user_id: int


class PollAutoScheduler:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: SessionFactory,
        poll_service: PollPostingService,
        telegram_client: TelegramPollClient,
        payments_service: PaymentsService | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._poll_service = poll_service
        self._telegram_client = telegram_client
        self._payments_service = payments_service
        self._zone = ZoneInfo(settings.schedule_timezone)

    @property
    def zone(self) -> ZoneInfo:
        return self._zone

    async def schedule_state(self) -> AutoScheduleState | None:
        row_data = await self._load_row_data()
        return row_data.state if row_data is not None else None

    async def mark_week_created(self, service_week_start: date) -> None:
        row_data = await self._load_row_data()
        if row_data is None:
            return
        await self._mark_week_done(service_week_start)
        if row_data.announce_message_id is not None:
            await self._delete_announce_message(row_data.announce_message_id)

    async def run(self, *, interval_seconds: float = TICK_INTERVAL_SECONDS) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("poll_auto_schedule_tick_failed")
            await sleep(interval_seconds)

    async def tick(self, now: datetime | None = None) -> str | None:
        if self._settings.telegram_target_chat_id is None:
            return None
        now_local = (now or datetime.now(UTC)).astimezone(self._zone)
        if self._payments_service is not None:
            # Boards first: the "lift is running" notice links to that day's board,
            # and on the tick a lift crosses the minimum the board is created here.
            # Synced from current bookings rather than from events, so a restart
            # mid-weekend just catches up on the next tick.
            try:
                await self._payments_service.sync_boards(now=now_local)
            except Exception:
                logger.exception("payments_board_sync_failed")

        # Threshold and departure notices are independent of the posting
        # schedule: they must run even when auto-posting was never configured.
        try:
            await self._poll_service.evaluate_lift_signals(now=now_local)
        except Exception:
            logger.exception("lift_signal_evaluation_failed")

        row_data = await self._load_row_data()
        if row_data is None:
            return None

        action = decide_tick(row_data.state, now_local)
        if action is None:
            return None

        if action.kind == "announce":
            await self._announce(action, announced_at=now_local)
        elif action.kind == "create":
            await self._create_scheduled_polls(action, row_data)
        else:
            await self._mark_week_skipped(action, row_data)
        return action.kind

    async def schedule_label(self) -> str:
        """Two or three words for the weekend card's ⏰ button."""
        state = await self.schedule_state()
        if state is None or not state.enabled:
            return "off"
        return f"{SHORT_WEEKDAY_LABELS[state.creation_weekday]} {state.creation_time}"

    async def schedule_summary(self) -> str:
        """One line for the start card: when the next polls appear, or why they will not."""
        state = await self.schedule_state()
        if state is None or not state.enabled:
            return "⏰ Auto-posting is off — post from 📋 Weekend."
        now = datetime.now(UTC).astimezone(self._zone)
        pending = next_pending_creation(state, now, zone=self._zone)
        if pending is None:
            return f"⏰ Polls open {WEEKDAY_LABELS[state.creation_weekday]} {state.creation_time}."
        _, creation_at = pending
        if now >= creation_at:
            return "⏰ Polls are due now."
        return (
            f"⏰ Next polls open {SHORT_WEEKDAY_LABELS[creation_at.weekday()]} "
            f"{creation_at.hour}:{creation_at.minute:02d}."
        )

    async def schedule_card(self, *, view: CardView = "main") -> ScheduleCardDraft:
        row_data = await self._load_row_data()
        state = row_data.state if row_data is not None else AutoScheduleState()
        return render_schedule_card(
            state,
            view=view,
            timezone_label=self._settings.schedule_timezone,
            now=datetime.now(UTC).astimezone(self._zone),
            zone=self._zone,
            planned_lifts_label=await self._planned_lifts_label(),
        )

    async def _planned_lifts_label(self) -> str:
        cancelled_lift_times = await self._poll_service.suggested_cancelled_lift_times()
        first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
        lift_count = enabled_lift_count(cancelled_lift_times)
        lift_label = "lift" if lift_count == 1 else "lifts"
        return f"{first_time} → {last_time} · {lift_count} {lift_label}"

    async def toggle_enabled(self, *, admin_user_id: int) -> None:
        await self._update_state(
            lambda state: replace(state, enabled=not state.enabled),
            admin_user_id=admin_user_id,
        )

    async def set_creation_weekday(self, weekday: int, *, admin_user_id: int) -> None:
        if not 0 <= weekday <= 5:
            msg = "Creation weekday must be between Monday and Saturday."
            raise ValueError(msg)
        await self._update_state(
            lambda state: replace(state, creation_weekday=weekday),
            admin_user_id=admin_user_id,
        )

    async def set_creation_time(self, encoded_time: str, *, admin_user_id: int) -> None:
        creation_time = decode_card_time(encoded_time)
        await self._update_state(
            lambda state: replace(state, creation_time=creation_time),
            admin_user_id=admin_user_id,
        )

    async def cycle_announce_lead(self, *, admin_user_id: int) -> None:
        await self._update_state(
            lambda state: replace(
                state,
                announce_lead_minutes=next_announce_lead(state.announce_lead_minutes),
            ),
            admin_user_id=admin_user_id,
        )

    async def toggle_skip(self, *, admin_user_id: int, now: datetime | None = None) -> None:
        now_local = (now or datetime.now(UTC)).astimezone(self._zone)

        def apply(state: AutoScheduleState) -> AutoScheduleState:
            target_week = skip_target_week(state, now_local, zone=self._zone)
            if target_week is None:
                return state
            if state.skip_week_start == target_week:
                return replace(state, skip_week_start=None)
            return replace(state, skip_week_start=target_week)

        stale_announce_message_id = await self._update_state(apply, admin_user_id=admin_user_id)
        if stale_announce_message_id is not None:
            await self._delete_announce_message(stale_announce_message_id)

    async def _announce(self, action: TickAction, *, announced_at: datetime) -> None:
        if self._settings.telegram_target_chat_id is None:
            return
        text = render_schedule_announcement(
            action.service_week_start,
            creation_at=action.creation_at,
            announced_at=announced_at,
        )
        sent = await self._telegram_client.send_text(
            chat_id=self._settings.telegram_target_chat_id,
            message_thread_id=self._settings.telegram_target_thread_id,
            text=text,
        )
        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                return
            row.last_announced_week_start = action.service_week_start
            row.announce_message_id = sent.message_id
            await session.commit()
        logger.info(
            "poll_auto_schedule_announced service_week_start=%s message_id=%s",
            action.service_week_start.isoformat(),
            sent.message_id,
            extra={
                "service_week_start": action.service_week_start.isoformat(),
                "message_id": sent.message_id,
            },
        )

    async def _create_scheduled_polls(self, action: TickAction, row_data: ScheduleRowData) -> None:
        week = action.service_week_start
        plan = await resolve_week_plan(
            session_factory=self._session_factory,
            settings=self._settings,
            poll_service=self._poll_service,
            service_week_start=week,
        )
        created_by_user_id = (
            plan.updated_by_user_id
            if plan.updated_by_user_id is not None
            else row_data.updated_by_user_id
        )
        setups = tuple(
            PollSetup(
                service_date=service_date,
                created_by_user_id=created_by_user_id,
                cancelled_lift_times=plan.cancelled_lift_times,
            )
            for service_date in plan.enabled_dates
        )
        conflicts = await self._poll_service.find_active_conflicts(setups)
        conflict_dates = {conflict.service_date for conflict in conflicts}
        pending = tuple(setup for setup in setups if setup.service_date not in conflict_dates)

        results = []
        for index, setup in enumerate(pending):
            try:
                results.append(
                    await self._poll_service.create_poll(
                        setup,
                        # The route notice already exists when part of the weekend
                        # was posted manually.
                        include_notice=index == 0 and not conflicts,
                        pin_after_send=False,
                    )
                )
            except DuplicatePollError:
                logger.info(
                    "poll_auto_schedule_duplicate_skipped service_date=%s",
                    setup.service_date.isoformat(),
                    extra={"service_date": setup.service_date.isoformat()},
                )
        if results:
            await self._poll_service.pin_created_results(tuple(results))
            await self._poll_service.record_schedule_selection(
                service_dates=plan.enabled_dates,
                cancelled_lift_times=plan.cancelled_lift_times,
                created_by_user_id=created_by_user_id,
            )

        await self._mark_week_done(week)
        if row_data.announce_message_id is not None:
            await self._delete_announce_message(row_data.announce_message_id)
        logger.info(
            "poll_auto_schedule_created service_week_start=%s created_count=%s conflict_count=%s",
            week.isoformat(),
            len(results),
            len(conflicts),
            extra={
                "service_week_start": week.isoformat(),
                "created_count": len(results),
                "conflict_count": len(conflicts),
            },
        )

    async def _mark_week_skipped(self, action: TickAction, row_data: ScheduleRowData) -> None:
        await self._mark_week_done(action.service_week_start)
        if row_data.announce_message_id is not None:
            await self._delete_announce_message(row_data.announce_message_id)
        logger.info(
            "poll_auto_schedule_week_skipped service_week_start=%s",
            action.service_week_start.isoformat(),
            extra={"service_week_start": action.service_week_start.isoformat()},
        )

    async def _mark_week_done(self, service_week_start: date) -> None:
        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                return
            row.last_created_week_start = service_week_start
            row.announce_message_id = None
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def _delete_announce_message(self, message_id: int) -> None:
        if self._settings.telegram_target_chat_id is None:
            return
        deleted = await self._telegram_client.delete_message(
            chat_id=self._settings.telegram_target_chat_id,
            message_id=message_id,
        )
        if not deleted:
            logger.warning(
                "poll_auto_schedule_announce_cleanup_failed message_id=%s",
                message_id,
                extra={"message_id": message_id},
            )

    async def _update_state(
        self,
        apply: Callable[[AutoScheduleState], AutoScheduleState],
        *,
        admin_user_id: int,
    ) -> int | None:
        """Apply a state change; return an announce message id that became stale."""
        if self._settings.telegram_target_chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to configure the poll schedule."
            raise ValueError(msg)
        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                row = PollAutoSchedule(
                    environment=self._settings.app_env,
                    chat_id=self._settings.telegram_target_chat_id,
                    thread_id=self._settings.telegram_target_thread_id,
                    updated_by_user_id=admin_user_id,
                )
                session.add(row)
            state = _state_from_row(row)
            updated = apply(state)
            row.enabled = updated.enabled
            row.creation_weekday = updated.creation_weekday
            row.creation_time = updated.creation_time
            row.announce_lead_minutes = updated.announce_lead_minutes
            row.skip_week_start = updated.skip_week_start
            row.updated_by_user_id = admin_user_id
            row.updated_at = datetime.now(UTC)

            stale_announce_message_id = None
            if (
                updated.skip_week_start is not None
                and updated.skip_week_start == row.last_announced_week_start
                and row.announce_message_id is not None
            ):
                stale_announce_message_id = row.announce_message_id
                row.announce_message_id = None
            await session.commit()
            return stale_announce_message_id

    async def _load_row_data(self) -> ScheduleRowData | None:
        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                return None
            return ScheduleRowData(
                state=_state_from_row(row),
                announce_message_id=row.announce_message_id,
                updated_by_user_id=row.updated_by_user_id,
            )

    async def _row(self, session: AsyncSession) -> PollAutoSchedule | None:
        return await session.scalar(
            select(PollAutoSchedule)
            .where(PollAutoSchedule.environment == self._settings.app_env)
            .where(PollAutoSchedule.chat_id == self._settings.telegram_target_chat_id)
            .where(PollAutoSchedule.thread_id == self._settings.telegram_target_thread_id)
        )


def _state_from_row(row: PollAutoSchedule) -> AutoScheduleState:
    return AutoScheduleState(
        enabled=row.enabled if row.enabled is not None else False,
        creation_weekday=row.creation_weekday if row.creation_weekday is not None else 4,
        creation_time=row.creation_time or "14:00",
        announce_lead_minutes=(
            row.announce_lead_minutes if row.announce_lead_minutes is not None else 120
        ),
        skip_week_start=row.skip_week_start,
        last_announced_week_start=row.last_announced_week_start,
        last_created_week_start=row.last_created_week_start,
    )
