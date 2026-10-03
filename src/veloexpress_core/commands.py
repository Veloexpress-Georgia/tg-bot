"""Durable requests executed by the bot's existing application modules."""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, select, update

from veloexpress_bot.config import Settings
from veloexpress_bot.db.locking import transaction_lock
from veloexpress_bot.db.models import AdminCommand
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS
from veloexpress_bot.polls.schedule import cancelled_lift_times_for_range, lift_range_from_cancelled
from veloexpress_core.lifts import PollSetup, SessionFactory
from veloexpress_core.runtime import Runtime

logger = logging.getLogger(__name__)
Action = Literal[
    "manual",
    "cancel_lift",
    "restore_lift",
    "cancel_day",
    "payment",
    "plan",
    "post",
    "extra",
    "terms",
    "schedule",
    "skip",
    "claim_payment",
    "guest",
    "undo_payment",
    "booking_order",
]
USER_ACTIONS = {"claim_payment", "guest", "undo_payment"}
CONFIRMED_ACTIONS = {"cancel_day", "cancel_lift", "post", "extra", "booking_order"}


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    action: Action
    service_date: date | None = None
    lift_time: str | None = None
    delta: int | None = None
    user_id: int | None = Field(default=None, gt=0)
    amount_gel: int | None = Field(default=None, gt=0, le=100000)
    method: Literal["cash", "transfer"] = "transfer"
    saturday_enabled: bool | None = None
    sunday_enabled: bool | None = None
    first_lift_time: str | None = None
    last_lift_time: str | None = None
    price_gel: int | None = Field(default=None, gt=0, le=10000)
    deadline_time: str | None = None
    enabled: bool | None = None
    creation_weekday: int | None = Field(default=None, ge=0, le=5)
    creation_time: str | None = None
    announce_lead_minutes: Literal[0, 60, 120, 180] | None = None
    expected_digest: str | None = None
    acknowledged: bool = False
    include_pending: bool = False
    ordered_user_ids: list[int] | None = Field(default=None, max_length=1000)
    restore_user_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_action(self) -> CommandInput:
        required = {
            "manual": ("service_date", "lift_time", "delta"),
            "cancel_lift": ("service_date", "lift_time"),
            "restore_lift": ("service_date", "lift_time"),
            "cancel_day": ("service_date",),
            "payment": ("service_date", "user_id", "amount_gel"),
            "plan": ("saturday_enabled", "sunday_enabled", "first_lift_time", "last_lift_time"),
            "extra": ("service_date", "first_lift_time", "last_lift_time"),
            "terms": ("price_gel", "deadline_time"),
            "schedule": ("enabled", "creation_weekday", "creation_time", "announce_lead_minutes"),
            "claim_payment": ("service_date",),
            "guest": ("service_date", "lift_time", "delta"),
            "undo_payment": ("service_date",),
            "post": (),
            "skip": (),
            "booking_order": ("service_date", "lift_time"),
        }
        if any(getattr(self, field) is None for field in required[self.action]):
            raise ValueError("Missing action parameters")
        if self.action in {"manual", "guest"} and self.delta not in {-1, 1}:
            raise ValueError("Seat adjustments must be +1 or -1")
        if self.action == "booking_order":
            if (self.ordered_user_ids is None) == (self.restore_user_id is None):
                raise ValueError("Choose a new order or one rider to restore")
            if self.ordered_user_ids is not None and (
                any(uid <= 0 for uid in self.ordered_user_ids)
                or len(set(self.ordered_user_ids)) != len(self.ordered_user_ids)
            ):
                raise ValueError("Booking order must contain unique positive user IDs")
        times = {lift.time for lift in DEFAULT_LIFTS}
        if self.lift_time is not None and self.lift_time not in times:
            raise ValueError("Unknown lift time")
        if self.action in {"plan", "extra"}:
            if self.first_lift_time not in times or self.last_lift_time not in times:
                raise ValueError("Unknown lift range")
            if list(times) and _minutes(str(self.first_lift_time)) > _minutes(
                str(self.last_lift_time)
            ):
                raise ValueError("First lift must precede last lift")
        if self.action == "plan" and not self.saturday_enabled and not self.sunday_enabled:
            raise ValueError("Enable at least one weekend day")
        for value in (self.deadline_time, self.creation_time):
            if value is not None:
                _minutes(value)
        return self


def _minutes(value: str) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour * 60 + minute
    except ValueError as error:
        raise ValueError("Time must use HH:MM") from error


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


class Operations:
    def __init__(self, *, settings: Settings, runtime: Runtime) -> None:
        self.settings = settings
        self.runtime = runtime

    async def planning_view(self) -> dict[str, Any]:
        week = self.runtime.planning.current_week_start()
        plan = await self.runtime.planning.load_plan(week)
        first, last = lift_range_from_cancelled(plan.cancelled_lift_times)
        posted = [d.service_date.isoformat() for d in await self.runtime.lifts.status_days()]
        schedule = await self.runtime.scheduler.schedule_state()
        return {
            "week_start": week,
            "saturday_enabled": plan.saturday_enabled,
            "sunday_enabled": plan.sunday_enabled,
            "first_lift_time": first,
            "last_lift_time": last,
            "posted_dates": posted,
            "schedule": asdict(schedule)
            if schedule
            else {
                "enabled": False,
                "creation_weekday": 4,
                "creation_time": "14:00",
                "announce_lead_minutes": 120,
                "skip_week_start": None,
            },
            "terms": asdict(await self.runtime.terms.values()),
            "lift_times": [lift.time for lift in DEFAULT_LIFTS],
        }

    async def preview(self, spec: CommandInput) -> dict[str, Any]:
        if spec.action == "booking_order":
            return await self.runtime.lifts.preview_booking_order(
                service_date=spec.service_date or date.min,
                lift_time=spec.lift_time or "",
                ordered_user_ids=spec.ordered_user_ids,
                restore_user_id=spec.restore_user_id,
            )
        if spec.action in {"cancel_lift", "cancel_day"}:
            days = await self.runtime.lifts.status_days()
            day = next((d for d in days if d.service_date == spec.service_date), None)
            if day is None or day.past:
                raise ValueError("This service day is no longer cancellable")
            if spec.action == "cancel_lift" and not any(
                lift.time == spec.lift_time and not lift.cancelled for lift in day.lifts
            ):
                raise ValueError("Lift is already cancelled or missing")
            report = await self.runtime.payments.cancellation_preview(
                service_date=day.service_date,
                cancelled_lift_time=spec.lift_time if spec.action == "cancel_lift" else None,
            )
            state = {"day": asdict(day), "report": report}
            target = f"lift {spec.lift_time}" if spec.action == "cancel_lift" else "all lifts"
            no_report = (
                "No payments have been reported for this day."
                if self.runtime.payments.enabled
                else "Payment tracking is disabled; refund estimates are unavailable."
            )
            return {
                "digest": _fingerprint(state),
                "details": report
                or f"Cancel {target} on {day.service_date.isoformat()}. {no_report}",
                "affected": day.booked_rider_count,
                "service_date": day.service_date,
            }
        if spec.action == "post":
            plan = await self.planning_view()
            return {
                "digest": _fingerprint(plan),
                "details": "Publish the planned weekend polls to the Telegram group.",
                "plan": plan,
            }
        if spec.action == "extra":
            today = datetime.now(UTC).astimezone(ZoneInfo(self.settings.schedule_timezone)).date()
            if spec.service_date is None or not today <= spec.service_date <= today + timedelta(
                days=7
            ):
                raise ValueError("Extra days must be within the next seven days")
            conflicts = await self.runtime.lifts.find_active_conflicts(
                (
                    PollSetup(
                        service_date=spec.service_date,
                        created_by_user_id=0,
                        cancelled_lift_times=cancelled_lift_times_for_range(
                            str(spec.first_lift_time), str(spec.last_lift_time)
                        ),
                    ),
                )
            )
            if conflicts:
                raise ValueError("This day already has polls")
            state = {
                "date": spec.service_date,
                "first": spec.first_lift_time,
                "last": spec.last_lift_time,
            }
            return {
                "digest": _fingerprint(state),
                "details": "Publish an extra service day to the Telegram group.",
            }
        raise ValueError("This operation needs no confirmation")

    async def execute(
        self, spec: CommandInput, *, actor_user_id: int, identity: dict[str, Any]
    ) -> dict[str, Any]:
        if (
            spec.action not in USER_ACTIONS
            and actor_user_id not in self.settings.telegram_admin_ids
        ):
            raise PermissionError("Administrator access was removed")
        if spec.action in {"manual", "restore_lift"}:
            day_view = next(
                (
                    d
                    for d in await self.runtime.lifts.status_days()
                    if d.service_date == spec.service_date
                ),
                None,
            )
            if day_view is None or day_view.past:
                raise ValueError("Finished service days cannot be edited")
        if spec.action in CONFIRMED_ACTIONS - {"booking_order"}:
            preview = await self.preview(spec)
            if preview["digest"] != spec.expected_digest:
                raise ValueError("Data changed. Open the confirmation again")
        rt = self.runtime
        day = spec.service_date or date.min
        lift_time = spec.lift_time or ""
        if spec.action == "booking_order":
            return await rt.lifts.save_booking_order(
                service_date=day,
                lift_time=lift_time,
                expected_digest=spec.expected_digest or "",
                admin_user_id=actor_user_id,
                ordered_user_ids=spec.ordered_user_ids,
                restore_user_id=spec.restore_user_id,
            )
        if spec.action == "manual":
            result = await rt.lifts.adjust_manual_booking(
                service_date=day,
                lift_time=lift_time,
                delta=spec.delta or 0,
                admin_user_id=actor_user_id,
            )
            return {"message": "Manual seats updated", "manual_count": result.manual_count}
        if spec.action == "restore_lift":
            await rt.lifts.restore_lift(
                service_date=day, lift_time=lift_time, admin_user_id=actor_user_id
            )
        elif spec.action in {"cancel_lift", "cancel_day"}:
            if spec.action == "cancel_lift":
                await rt.lifts.cancel_lift(
                    service_date=day, lift_time=lift_time, admin_user_id=actor_user_id
                )
            else:
                await rt.lifts.cancel_day(service_date=day, admin_user_id=actor_user_id)
            # The estimate reads committed bookings, excluding the cancelled lift.
            # Preserve it before a whole-day cancellation retires payment claims.
            report = await rt.payments.cancellation_report(
                service_date=day,
                cancelled_lift_time=lift_time if spec.action == "cancel_lift" else None,
            )
            if spec.action == "cancel_day":
                await rt.payments.forget_day(service_date=day)
            return {"message": "Cancelled", "report": report}
        elif spec.action == "payment":
            await rt.payments.record_admin_payment(
                service_date=day,
                telegram_user_id=spec.user_id or 0,
                amount_gel=spec.amount_gel or 0,
                method=spec.method,
                admin_user_id=actor_user_id,
                reference_key=f"web:{spec.request_id}",
            )
        elif spec.action == "plan":
            view = await self.planning_view()
            if spec.service_date is None or str(view["week_start"]) != day.isoformat():
                raise ValueError("Weekend changed. Reload planning")
            if any(
                d in view["posted_dates"]
                for d in (day.isoformat(), (day + timedelta(days=1)).isoformat())
            ):
                raise ValueError("Published weekend plans are protected")
            await rt.planning.save_plan(
                saturday_enabled=bool(spec.saturday_enabled),
                sunday_enabled=bool(spec.sunday_enabled),
                first_lift_time=str(spec.first_lift_time),
                last_lift_time=str(spec.last_lift_time),
                admin_user_id=actor_user_id,
            )
        elif spec.action == "post":
            result = await rt.planning.post_now(admin_user_id=actor_user_id)
            return {
                "message": "Polls published" if result.created_count else "Polls already published",
                "count": result.created_count,
            }
        elif spec.action == "extra":
            await rt.lifts.create_poll(
                PollSetup(
                    service_date=day,
                    created_by_user_id=actor_user_id,
                    cancelled_lift_times=cancelled_lift_times_for_range(
                        str(spec.first_lift_time), str(spec.last_lift_time)
                    ),
                ),
                pin_after_send=False,
            )
        elif spec.action == "terms":
            await rt.terms.set_values(
                price_gel=spec.price_gel or 0,
                deadline_time=str(spec.deadline_time),
                admin_user_id=actor_user_id,
            )
        elif spec.action == "schedule":
            await rt.scheduler.set_schedule(
                enabled=bool(spec.enabled),
                weekday=spec.creation_weekday or 0,
                creation_time=str(spec.creation_time),
                announce_lead_minutes=spec.announce_lead_minutes or 0,
                admin_user_id=actor_user_id,
            )
        elif spec.action == "skip":
            await rt.planning.toggle_skip(admin_user_id=actor_user_id)
        elif spec.action == "claim_payment":
            outcome = await rt.payments.claim(
                service_date=day,
                telegram_user_id=actor_user_id,
                username=identity.get("username"),
                full_name=identity.get("name", str(actor_user_id)),
                method=spec.method,
                acknowledged=spec.acknowledged,
                include_pending=spec.include_pending,
            )
            return {"message": outcome.text, "needs_confirmation": outcome.needs_confirmation}
        elif spec.action == "guest":
            message = await rt.payments.adjust_guest_seats(
                service_date=day,
                telegram_user_id=actor_user_id,
                lift_times=(lift_time,),
                delta=spec.delta or 0,
            )
            return {"message": message}
        elif spec.action == "undo_payment":
            return {
                "message": await rt.payments.undo(service_date=day, telegram_user_id=actor_user_id)
            }
        return {"message": "Saved"}


class CommandQueue:
    def __init__(self, *, settings: Settings, session_factory: SessionFactory) -> None:
        self.settings = settings
        self.sessions = session_factory

    def scope(self, query: Any) -> Any:
        return query.where(
            AdminCommand.environment == self.settings.app_env,
            AdminCommand.chat_id == self.settings.telegram_target_chat_id,
            AdminCommand.thread_id == self.settings.telegram_target_thread_id,
        )

    async def enqueue(
        self, spec: CommandInput, *, actor_user_id: int, identity: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = json.dumps(
            {"spec": spec.model_dump(mode="json"), "identity": identity or {}}, sort_keys=True
        )
        key = self.request_key(spec.request_id, actor_user_id)
        async with self.sessions() as session:
            await transaction_lock(session, f"web-command:{key}")
            row = await session.scalar(select(AdminCommand).where(AdminCommand.request_key == key))
            if row is not None:
                if json.loads(row.payload)["spec"] != spec.model_dump(mode="json"):
                    raise ValueError("Request id already used for a different action")
                return self.view(row)
            row = AdminCommand(
                environment=self.settings.app_env,
                chat_id=self.settings.telegram_target_chat_id or 0,
                thread_id=self.settings.telegram_target_thread_id,
                actor_user_id=actor_user_id,
                request_key=key,
                action=spec.action,
                payload=payload,
                status="pending",
            )
            session.add(row)
            await session.commit()
            return self.view(row)

    def request_key(self, request_id: UUID, actor_user_id: int) -> str:
        value = (
            f"{self.settings.app_env}:{self.settings.telegram_target_chat_id}:"
            f"{self.settings.telegram_target_thread_id}:{actor_user_id}:{request_id}"
        )
        return hashlib.sha256(value.encode()).hexdigest()

    async def existing(self, spec: CommandInput, *, actor_user_id: int) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(AdminCommand)).where(
                    AdminCommand.request_key == self.request_key(spec.request_id, actor_user_id)
                )
            )
            if row is None:
                return None
            stored = CommandInput.model_validate(json.loads(row.payload)["spec"])
            if stored.model_dump(exclude={"expected_digest"}) != spec.model_dump(
                exclude={"expected_digest"}
            ):
                raise ValueError("Request id already used for a different action")
            return self.view(row)

    async def get(self, command_id: int, *, actor_user_id: int) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(AdminCommand)).where(
                    AdminCommand.id == command_id, AdminCommand.actor_user_id == actor_user_id
                )
            )
            return self.view(row) if row else None

    async def recent(
        self, *, limit: int = 30, service_date: date | None = None
    ) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            query = self.scope(select(AdminCommand))
            if service_date is not None:
                # Payload contains the validated date; filter before limiting so
                # busy unrelated days cannot hide this day's pending operations.
                query = query.where(
                    AdminCommand.payload.contains(f'"service_date": "{service_date.isoformat()}"')
                )
                query = query.order_by(
                    case((AdminCommand.status.in_(("pending", "running", "review")), 0), else_=1)
                )
            rows = await session.scalars(query.order_by(AdminCommand.id.desc()).limit(limit))
            return [self.view(row) for row in rows]

    @staticmethod
    def view(row: AdminCommand) -> dict[str, Any]:
        payload = json.loads(row.payload)
        return {
            "id": row.id,
            "action": row.action,
            "service_date": payload.get("spec", {}).get("service_date"),
            "lift_time": payload.get("spec", {}).get("lift_time"),
            "actor_user_id": row.actor_user_id,
            "actor_name": payload.get("identity", {}).get("name", str(row.actor_user_id)),
            "status": row.status,
            "result": json.loads(row.result) if row.result else None,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
        }

    async def process_one(self, operations: Operations) -> bool:
        async with self.sessions() as session:
            row = await session.scalar(
                self.scope(select(AdminCommand))
                .where(AdminCommand.status == "pending")
                .order_by(AdminCommand.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return False
            row.status = "running"
            row.started_at = datetime.now(UTC)
            command_id, actor_id, payload = row.id, row.actor_user_id, json.loads(row.payload)
            await session.commit()
        status = "complete"
        try:
            result = await operations.execute(
                CommandInput.model_validate(payload["spec"]),
                actor_user_id=actor_id,
                identity=payload["identity"],
            )
        except (ValueError, PermissionError) as error:
            status, result = "failed", {"message": str(error)}
        except Exception:
            logger.exception("web_command_execution_uncertain", extra={"command_id": command_id})
            status, result = (
                "review",
                {
                    "message": (
                        "Execution may have changed data. "
                        "Check the day and Telegram before retrying."
                    )
                },
            )
        async with self.sessions() as session:
            await session.execute(
                update(AdminCommand)
                .where(AdminCommand.id == command_id)
                .values(
                    status=status,
                    result=json.dumps(result, default=str),
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        return True

    async def run(self, operations: Operations) -> None:
        # The bot is the only executor. A killed execution has an unknown outcome:
        # mark it for human review instead of repeating money/Telegram side effects.
        async with self.sessions() as session:
            await session.execute(
                self.scope(update(AdminCommand))
                .where(AdminCommand.status == "running")
                .values(
                    status="review",
                    result=json.dumps(
                        {"message": "Interrupted during restart; check the result before retrying."}
                    ),
                )
            )
            await session.commit()
        while True:
            try:
                if not await self.process_one(operations):
                    await asyncio.sleep(1)
            except Exception:
                logger.exception("web_command_worker_failed")
                await asyncio.sleep(5)
