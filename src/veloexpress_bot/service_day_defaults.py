from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from veloexpress_bot.config import Settings
from veloexpress_bot.db.models import ServiceDayDefaults

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# A 5 GEL step could only ever reach multiples of five: off a 15 GEL seat it was
# a 33% jump, and 16 or 18 were not reachable at all.
PRICE_STEP_GEL = 1
DEADLINE_STEP_MINUTES = 30


@dataclass(frozen=True)
class ServiceDayDefaultValues:
    price_gel: int
    deadline_time: str
    timezone: str


@dataclass(frozen=True)
class ServiceDayDefaultsCard:
    text: str
    reply_markup: InlineKeyboardMarkup


class ServiceDayDefaultsStore:
    """Runtime defaults for future days; published-day terms never pass this seam."""

    def __init__(self, *, settings: Settings, session_factory: SessionFactory) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def values(self) -> ServiceDayDefaultValues:
        row = await self._load_row()
        return ServiceDayDefaultValues(
            price_gel=row.price_gel if row is not None else self._settings.payment_price_gel,
            deadline_time=(
                row.deadline_time if row is not None else self._settings.booking_deadline_time
            ),
            timezone=self._settings.schedule_timezone,
        )

    async def adjust_price(self, delta: int, *, admin_user_id: int) -> ServiceDayDefaultValues:
        if delta not in {-PRICE_STEP_GEL, PRICE_STEP_GEL}:
            msg = f"Price changes must be {PRICE_STEP_GEL} GEL."
            raise ValueError(msg)
        current = await self.values()
        price = current.price_gel + delta
        if price <= 0:
            msg = "Price must stay greater than zero."
            raise ValueError(msg)
        await self._save(
            price_gel=price,
            deadline_time=current.deadline_time,
            admin_user_id=admin_user_id,
        )
        return ServiceDayDefaultValues(price, current.deadline_time, current.timezone)

    async def adjust_deadline(
        self, delta_minutes: int, *, admin_user_id: int
    ) -> ServiceDayDefaultValues:
        if delta_minutes not in {-DEADLINE_STEP_MINUTES, DEADLINE_STEP_MINUTES}:
            msg = f"Deadline changes must be {DEADLINE_STEP_MINUTES} minutes."
            raise ValueError(msg)
        current = await self.values()
        minutes = (_time_minutes(current.deadline_time) + delta_minutes) % (24 * 60)
        deadline = f"{minutes // 60:02d}:{minutes % 60:02d}"
        await self._save(
            price_gel=current.price_gel,
            deadline_time=deadline,
            admin_user_id=admin_user_id,
        )
        return ServiceDayDefaultValues(current.price_gel, deadline, current.timezone)

    async def card(self) -> ServiceDayDefaultsCard:
        values = await self.values()
        return render_service_day_defaults(values)

    async def _load_row(self) -> ServiceDayDefaults | None:
        if self._settings.telegram_target_chat_id is None:
            return None
        async with self._session_factory() as session:
            return await session.scalar(
                select(ServiceDayDefaults)
                .where(ServiceDayDefaults.environment == self._settings.app_env)
                .where(ServiceDayDefaults.chat_id == self._settings.telegram_target_chat_id)
                .where(ServiceDayDefaults.thread_id == self._settings.telegram_target_thread_id)
            )

    async def _save(
        self,
        *,
        price_gel: int,
        deadline_time: str,
        admin_user_id: int,
    ) -> None:
        chat_id = self._settings.telegram_target_chat_id
        if chat_id is None:
            msg = "TELEGRAM_TARGET_CHAT_ID is required to edit service-day defaults."
            raise ValueError(msg)
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ServiceDayDefaults)
                .where(ServiceDayDefaults.environment == self._settings.app_env)
                .where(ServiceDayDefaults.chat_id == chat_id)
                .where(ServiceDayDefaults.thread_id == self._settings.telegram_target_thread_id)
                .with_for_update()
            )
            if row is None:
                row = ServiceDayDefaults(
                    environment=self._settings.app_env,
                    chat_id=chat_id,
                    thread_id=self._settings.telegram_target_thread_id,
                    price_gel=price_gel,
                    deadline_time=deadline_time,
                    updated_by_user_id=admin_user_id,
                )
                session.add(row)
            else:
                row.price_gel = price_gel
                row.deadline_time = deadline_time
                row.updated_by_user_id = admin_user_id
                row.updated_at = datetime.now(UTC)
            await session.commit()


def render_service_day_defaults(values: ServiceDayDefaultValues) -> ServiceDayDefaultsCard:
    return ServiceDayDefaultsCard(
        text=(
            "⚙️ Settings\n\n"
            f"💳 Price: {values.price_gel} GEL per seat\n"
            f"⏰ Booking deadline: {values.deadline_time} the evening before each lift day\n"
            f"🌍 Timezone: {values.timezone}\n\n"
            # Said in full, because the two scopes are easy to confuse and the
            # wrong reading is the expensive one.
            "These apply to lift days published from now on.\n"
            "Days already published keep the price and deadline they opened with."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"➖ {PRICE_STEP_GEL} GEL",
                        callback_data="defaults:price:sub",
                    ),
                    InlineKeyboardButton(
                        text=f"➕ {PRICE_STEP_GEL} GEL",
                        callback_data="defaults:price:add",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"◀️ {DEADLINE_STEP_MINUTES} min",
                        callback_data="defaults:deadline:sub",
                    ),
                    InlineKeyboardButton(
                        text=f"{DEADLINE_STEP_MINUTES} min ▶️",
                        callback_data="defaults:deadline:add",
                    ),
                ],
                # When polls open is a setting too, and looking for it under
                # planning is only obvious once you know where it lives.
                [
                    InlineKeyboardButton(
                        text="⏰ When polls open",
                        callback_data="plan:schedule",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Menu", callback_data="defaults:menu")],
            ]
        ),
    )


def _time_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", maxsplit=1))
    return hour * 60 + minute
