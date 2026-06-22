import logging
from asyncio import Task, create_task, sleep
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import cast

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PollAnswer

from veloexpress_bot.bot.keyboards import setup_keyboard, start_menu_keyboard
from veloexpress_bot.bot.permissions import is_admin
from veloexpress_bot.bot.states import PollSetupStates
from veloexpress_bot.config import Settings
from veloexpress_bot.polls.defaults import (
    DEFAULT_CANCELLED_LIFT_TIMES,
    DEFAULT_LIFTS,
    LOCATION_LABELS,
    StartLocation,
)
from veloexpress_bot.polls.service import (
    DuplicatePollError,
    PollCreationResult,
    PollPostingService,
    PollSetup,
)
from veloexpress_bot.telegram.errors import TelegramPollPostError, TelegramTargetForbiddenError

router = Router(name="admin_poll_setup")
logger = logging.getLogger(__name__)

ADMIN_ONLY_TEXT = "Admins only."
DUPLICATE_POLL_ALERT = "Poll already exists. Recreate requires explicit confirmation."
DUPLICATE_POLL_TEXT = (
    "One or more selected days already have active polls. Cancel this setup or recreate "
    "the existing polls. Recreate will report tracked votes before deleting old polls."
)
EXPIRED_SETUP_TEXT = "This poll setup expired. Run /create_lift_poll again."
POLL_POST_FAILED_ALERT = "Could not create poll. Check logs and try again."
POLL_POST_FAILED_TEXT = "Could not create poll. The setup is still open; check logs and try again."
POLL_TARGET_FORBIDDEN_ALERT = "Bot cannot post to target chat. Re-add it or update chat ID."
POLL_TARGET_FORBIDDEN_TEXT = (
    "Could not create poll: Telegram rejected the target chat. The bot was likely removed "
    "from the configured group/topic. Add it back or update TELEGRAM_TARGET_CHAT_ID, "
    "then try again."
)
POLL_SETUP_CANCELLED_TEXT = "Poll setup cancelled."
STALE_SETUP_ALERT = "This setup expired. Run /create_lift_poll again."
TELEGRAM_NOT_MODIFIED_TEXT = "message is not modified"
SETUP_EDIT_DEBOUNCE_SECONDS = 0.75
START_TEXT = (
    "🚐 Veloexpress Bot\n\n"
    "I help admins create weekend lift polls and keep Telegram operations natural."
)


@dataclass(frozen=True)
class SetupStateData:
    service_dates: tuple[date, ...]
    selected_service_dates: tuple[date, ...]
    first_lift_location: StartLocation
    cancelled_lift_times: tuple[str, ...]
    setup_view: str = "main"


@dataclass(frozen=True)
class PendingSetupEdit:
    message: Message
    text: str
    setup_state: SetupStateData
    allow_recreate: bool


SetupEditKey = tuple[int, int]
_pending_setup_edits: dict[SetupEditKey, PendingSetupEdit] = {}
_setup_edit_tasks: dict[SetupEditKey, Task[None]] = {}
_setup_edit_blocked_until: dict[SetupEditKey, float] = {}


@router.message(Command("start"))
async def show_start_menu(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    user_is_admin = is_admin(message.from_user.id if message.from_user else None, settings)
    admin_text = "\n\nChoose an action below." if user_is_admin else "\n\nAdmins only."
    sent = await message.answer(
        f"{START_TEXT}{admin_text}",
        reply_markup=start_menu_keyboard(is_admin=user_is_admin),
    )
    if user_is_admin:
        await state.update_data(
            menu_message_ids=list(
                _menu_message_ids_for_cleanup(
                    command_message_id=message.message_id,
                    menu_message_id=sent.message_id,
                )
            )
        )


@router.message(Command("create_lift_poll"))
async def create_lift_poll(
    message: Message,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await message.answer("This command is only available to Veloexpress admins.")
        return

    await _open_poll_setup_from_message(
        message=message,
        state=state,
        poll_service=poll_service,
    )


@router.callback_query(F.data == "menu:create_lift_poll")
async def create_lift_poll_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return
    message = _accessible_message(callback)
    if message is None:
        await callback.answer("Open /start again.", show_alert=True)
        return

    await _open_poll_setup_from_menu(
        callback=callback,
        message=message,
        state=state,
        poll_service=poll_service,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:cancel")
async def cancel_start_menu(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return
    message = _accessible_message(callback)
    if message is None:
        await callback.answer("Menu closed.")
        return

    data = await state.get_data()
    menu_message_ids = tuple(int(item) for item in _string_items(data.get("menu_message_ids", [])))
    if not menu_message_ids:
        menu_message_ids = (message.message_id,)

    await callback.answer("Menu closed.")
    await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=menu_message_ids,
    )
    await _clear_menu_message_ids(state=state)


async def _open_poll_setup_from_message(
    *,
    message: Message,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    service_dates = _upcoming_weekend_dates()
    first_lift_location = StartLocation.JUSTICE_HALL
    cancelled_lift_times: tuple[str, ...] = DEFAULT_CANCELLED_LIFT_TIMES
    setup_state = SetupStateData(
        service_dates=service_dates,
        selected_service_dates=service_dates,
        first_lift_location=first_lift_location,
        cancelled_lift_times=cancelled_lift_times,
    )
    await _store_setup_state(state=state, setup_state=setup_state)
    allow_recreate = await _has_active_conflicts(
        setup_state=setup_state,
        created_by_user_id=message.from_user.id if message.from_user else 0,
        poll_service=poll_service,
    )
    sent = await message.answer(
        _setup_text_for_conflicts(setup_state=setup_state, allow_recreate=allow_recreate),
        reply_markup=setup_keyboard(
            service_dates=service_dates,
            selected_service_dates=service_dates,
            cancelled_lift_times=cancelled_lift_times,
            first_lift_location=first_lift_location,
            allow_recreate=allow_recreate,
            view=setup_state.setup_view,
        ),
    )
    await state.update_data(
        setup_message_ids=list(
            _setup_message_ids_for_cleanup(
                command_message_id=message.message_id,
                setup_message_id=sent.message_id,
            )
        ),
        allow_recreate=allow_recreate,
    )


async def _open_poll_setup_from_menu(
    *,
    callback: CallbackQuery,
    message: Message,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    service_dates = _upcoming_weekend_dates()
    setup_state = SetupStateData(
        service_dates=service_dates,
        selected_service_dates=service_dates,
        first_lift_location=StartLocation.JUSTICE_HALL,
        cancelled_lift_times=DEFAULT_CANCELLED_LIFT_TIMES,
    )
    await _store_setup_state(state=state, setup_state=setup_state)
    allow_recreate = await _has_active_conflicts(
        setup_state=setup_state,
        created_by_user_id=callback.from_user.id,
        poll_service=poll_service,
    )
    await message.edit_text(
        _setup_text_for_conflicts(setup_state=setup_state, allow_recreate=allow_recreate),
        reply_markup=setup_keyboard(
            service_dates=setup_state.service_dates,
            selected_service_dates=setup_state.selected_service_dates,
            cancelled_lift_times=setup_state.cancelled_lift_times,
            first_lift_location=setup_state.first_lift_location,
            allow_recreate=allow_recreate,
            view=setup_state.setup_view,
        ),
    )
    data = await state.get_data()
    setup_message_ids = _setup_message_ids_from_menu_state(
        data=data,
        setup_message_id=message.message_id,
    )
    await state.update_data(
        setup_message_ids=list(setup_message_ids),
        menu_message_ids=[],
        allow_recreate=allow_recreate,
    )


async def _store_setup_state(*, state: FSMContext, setup_state: SetupStateData) -> None:
    await state.set_state(PollSetupStates.editing)
    await state.update_data(
        service_dates=[service_date.isoformat() for service_date in setup_state.service_dates],
        selected_service_dates=[
            service_date.isoformat() for service_date in setup_state.selected_service_dates
        ],
        first_lift_location=setup_state.first_lift_location.value,
        cancelled_lift_times=list(setup_state.cancelled_lift_times),
        setup_view=setup_state.setup_view,
        setup_message_ids=[],
        allow_recreate=False,
    )


@router.callback_query(PollSetupStates.editing, F.data.startswith("day:toggle:"))
async def toggle_service_day(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    callback_data = callback.data or ""
    service_date = date.fromisoformat(callback_data.removeprefix("day:toggle:"))
    data = await state.get_data()
    selected_dates = set(_string_items(data.get("selected_service_dates", data["service_dates"])))

    if service_date.isoformat() in selected_dates:
        if len(selected_dates) == 1:
            await callback.answer("At least one day must remain.", show_alert=True)
            return
        selected_dates.remove(service_date.isoformat())
    else:
        selected_dates.add(service_date.isoformat())

    await state.update_data(selected_service_dates=sorted(selected_dates))
    await _refresh_setup(callback, state, poll_service, refresh_conflicts=True)


@router.callback_query(PollSetupStates.editing, F.data == "first:toggle")
async def toggle_first_location(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    data = await state.get_data()
    current = StartLocation(data["first_lift_location"])
    next_location = (
        StartLocation.VAKE if current == StartLocation.JUSTICE_HALL else StartLocation.JUSTICE_HALL
    )
    await state.update_data(first_lift_location=next_location.value)
    await _refresh_setup(callback, state, poll_service)


@router.callback_query(PollSetupStates.editing, F.data.startswith("lift:toggle:"))
async def toggle_lift(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    lift_time = callback.data.removeprefix("lift:toggle:") if callback.data else ""
    data = await state.get_data()
    cancelled = set(data.get("cancelled_lift_times", []))
    active_count = len(DEFAULT_LIFTS) - len(cancelled)

    if lift_time in cancelled:
        cancelled.remove(lift_time)
    elif active_count > 1:
        cancelled.add(lift_time)
    else:
        await callback.answer("At least one lift must remain.", show_alert=True)
        return

    await state.update_data(cancelled_lift_times=sorted(cancelled))
    await _refresh_setup(callback, state, poll_service)


@router.callback_query(PollSetupStates.editing, F.data.in_({"view:main", "view:times"}))
async def switch_setup_view(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    setup_view = "times" if callback.data == "view:times" else "main"
    await state.update_data(setup_view=setup_view)
    await _refresh_setup(callback, state, poll_service)


@router.callback_query(PollSetupStates.editing, F.data == "poll:cancel")
async def cancel_setup(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
) -> None:
    data = await state.get_data()
    setup_message_ids = tuple(int(item) for item in data.get("setup_message_ids", []))
    await state.clear()
    await callback.answer(POLL_SETUP_CANCELLED_TEXT)
    if message := _accessible_message(callback):
        cleanup = await poll_service.cleanup_setup_messages(
            chat_id=message.chat.id,
            message_ids=setup_message_ids,
        )
        if cleanup.failed_count:
            await message.edit_text(POLL_SETUP_CANCELLED_TEXT)


@router.callback_query(
    PollSetupStates.editing,
    F.data.in_({"poll:confirm", "poll:recreate_confirm"}),
)
async def confirm_setup(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return

    data = await state.get_data()
    setup_state = _setup_state_data(data)
    recreate_existing = callback.data == "poll:recreate_confirm"
    setups = _poll_setups_from_state(
        setup_state=setup_state,
        created_by_user_id=callback.from_user.id,
    )

    conflicts = await poll_service.find_active_conflicts(setups)

    if conflicts and not recreate_existing:
        if message := _accessible_message(callback):
            await message.edit_text(
                _setup_text(
                    setup_state.service_dates,
                    setup_state.first_lift_location,
                    setup_state.cancelled_lift_times,
                    selected_service_dates=setup_state.selected_service_dates,
                )
                + f"\n\n{DUPLICATE_POLL_TEXT}",
                reply_markup=setup_keyboard(
                    service_dates=setup_state.service_dates,
                    selected_service_dates=setup_state.selected_service_dates,
                    cancelled_lift_times=setup_state.cancelled_lift_times,
                    first_lift_location=setup_state.first_lift_location,
                    allow_recreate=True,
                ),
            )
        await callback.answer(
            DUPLICATE_POLL_ALERT,
            show_alert=True,
        )
        return

    recreate_existing = _should_recreate_existing(
        requested=recreate_existing,
        has_conflicts=bool(conflicts),
    )

    try:
        recreate_result = None
        report_text: str | None = None
        if recreate_existing:
            recreate_result = await poll_service.recreate_polls(setups)
            results = list(recreate_result.created)
            report_text = recreate_result.report_text
        else:
            results = [
                await poll_service.create_poll(
                    setup,
                    include_notice=index == 0,
                    pin_after_send=False,
                )
                for index, setup in enumerate(setups)
            ]
            results = list(await poll_service.pin_created_results(tuple(results)))
    except DuplicatePollError:
        await callback.answer("One of these polls was already posted.", show_alert=True)
        return
    except TelegramTargetForbiddenError:
        logger.exception("Poll creation failed because target chat rejected the bot")
        await callback.answer(POLL_TARGET_FORBIDDEN_ALERT, show_alert=True)
        if message := _accessible_message(callback):
            await message.answer(POLL_TARGET_FORBIDDEN_TEXT)
        return
    except TelegramPollPostError:
        logger.exception("Poll creation failed while sending Telegram poll")
        await callback.answer(POLL_POST_FAILED_ALERT, show_alert=True)
        if message := _accessible_message(callback):
            await message.answer(POLL_POST_FAILED_TEXT)
        return

    setup_message_ids = tuple(int(item) for item in data.get("setup_message_ids", []))
    await state.clear()
    await callback.answer(f"Polls created: {_message_ids_text(results)}.")
    if message := _accessible_message(callback):
        cleanup = await poll_service.cleanup_setup_messages(
            chat_id=message.chat.id,
            message_ids=setup_message_ids,
        )
        completion_text = _completion_text(
            message_ids=tuple(result.message_id for result in results),
            pinned=any(result.pinned for result in results),
            deleted_count=cleanup.deleted_count,
            failed_count=cleanup.failed_count,
        )
        logger.info(
            "Poll setup completed",
            extra={
                "message_ids": _message_ids_text(results),
                "pinned": any(result.pinned for result in results),
                "deleted_count": cleanup.deleted_count,
                "failed_count": cleanup.failed_count,
            },
        )
        if _should_notify_completion(
            pinned=any(result.pinned for result in results),
            failed_count=cleanup.failed_count,
        ):
            await message.answer(completion_text)
        if report_text and _should_send_recreate_report(report_text):
            await message.answer(report_text)
        if recreate_result:
            recreate_cleanup = await poll_service.cleanup_recreated_polls(recreate_result)
            if recreate_cleanup.failed_count:
                await message.answer(
                    "Replacement polls were created, but some old poll messages could not be "
                    "deleted. Check the chat and remove stale polls manually."
                )


@router.poll_answer()
async def track_poll_answer(answer: PollAnswer, poll_service: PollPostingService) -> None:
    if answer.user is None:
        return
    await poll_service.track_poll_answer(
        poll_id=answer.poll_id,
        telegram_user_id=answer.user.id,
        username=answer.user.username,
        full_name=answer.user.full_name,
        option_ids=tuple(answer.option_ids),
    )


@router.callback_query(
    (F.data == "first:toggle")
    | F.data.startswith("day:toggle:")
    | F.data.startswith("lift:toggle:")
    | F.data.startswith("view:")
    | F.data.startswith("poll:")
)
async def handle_stale_setup_callback(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return

    message = _accessible_message(callback)
    if message is None:
        await callback.answer(
            STALE_SETUP_ALERT,
            show_alert=True,
        )
        return

    cleanup = await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=(message.message_id,),
    )

    if callback.data == "poll:cancel":
        await callback.answer("Expired setup removed.")
    else:
        await callback.answer(
            STALE_SETUP_ALERT,
            show_alert=True,
        )

    if cleanup.failed_count:
        await message.edit_text(EXPIRED_SETUP_TEXT)

    if callback.data == "poll:cancel":
        return

    logger.info(
        "Removed stale poll setup message",
        extra={"message_id": message.message_id, "callback_data": callback.data},
    )


async def _refresh_setup(
    callback: CallbackQuery,
    state: FSMContext,
    poll_service: PollPostingService,
    *,
    refresh_conflicts: bool = False,
) -> None:
    data = await state.get_data()
    setup_state = _setup_state_data(data)
    if refresh_conflicts:
        allow_recreate = await _has_active_conflicts(
            setup_state=setup_state,
            created_by_user_id=callback.from_user.id,
            poll_service=poll_service,
        )
        await state.update_data(allow_recreate=allow_recreate)
    else:
        allow_recreate = _cached_allow_recreate(data)
    if message := _accessible_message(callback):
        await _schedule_setup_message_edit(
            message=message,
            text=_setup_text_for_conflicts(
                setup_state=setup_state,
                allow_recreate=allow_recreate,
            ),
            setup_state=setup_state,
            allow_recreate=allow_recreate,
        )
    await callback.answer()


async def _schedule_setup_message_edit(
    *,
    message: Message,
    text: str,
    setup_state: SetupStateData,
    allow_recreate: bool,
) -> None:
    key = _setup_edit_key(message)
    now = perf_counter()
    blocked_until = _setup_edit_blocked_until.get(key, 0.0)
    if now < blocked_until:
        logger.debug(
            "poll_setup_edit_skipped_flood_cooldown retry_after_ms=%s chat_id=%s message_id=%s",
            int((blocked_until - now) * 1000),
            message.chat.id,
            message.message_id,
            extra={
                "retry_after_ms": int((blocked_until - now) * 1000),
                "chat_id": message.chat.id,
                "message_id": message.message_id,
            },
        )
        return

    _pending_setup_edits[key] = PendingSetupEdit(
        message=message,
        text=text,
        setup_state=setup_state,
        allow_recreate=allow_recreate,
    )
    task = _setup_edit_tasks.get(key)
    if task is None or task.done():
        task = create_task(_flush_setup_message_edit_after_debounce(key))
        _setup_edit_tasks[key] = task
    await task


async def _flush_setup_message_edit_after_debounce(key: SetupEditKey) -> None:
    try:
        await sleep(SETUP_EDIT_DEBOUNCE_SECONDS)
        pending = _pending_setup_edits.pop(key, None)
        if pending is None:
            return
        await _edit_setup_message(
            message=pending.message,
            text=pending.text,
            setup_state=pending.setup_state,
            allow_recreate=pending.allow_recreate,
        )
    finally:
        _setup_edit_tasks.pop(key, None)


async def _edit_setup_message(
    *,
    message: Message,
    text: str,
    setup_state: SetupStateData,
    allow_recreate: bool,
) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=setup_keyboard(
                service_dates=setup_state.service_dates,
                selected_service_dates=setup_state.selected_service_dates,
                cancelled_lift_times=setup_state.cancelled_lift_times,
                first_lift_location=setup_state.first_lift_location,
                allow_recreate=allow_recreate,
                view=setup_state.setup_view,
            ),
        )
    except TelegramRetryAfter as error:
        _setup_edit_blocked_until[_setup_edit_key(message)] = perf_counter() + error.retry_after
        logger.warning(
            "poll_setup_edit_flood_control retry_after=%s chat_id=%s message_id=%s",
            error.retry_after,
            message.chat.id,
            message.message_id,
            extra={
                "retry_after": error.retry_after,
                "chat_id": message.chat.id,
                "message_id": message.message_id,
            },
        )
    except TelegramBadRequest as error:
        if TELEGRAM_NOT_MODIFIED_TEXT in str(error).lower():
            logger.debug(
                "poll_setup_edit_not_modified chat_id=%s message_id=%s",
                message.chat.id,
                message.message_id,
                extra={"chat_id": message.chat.id, "message_id": message.message_id},
            )
            return
        raise


def _setup_edit_key(message: Message) -> SetupEditKey:
    return message.chat.id, message.message_id


async def _has_active_conflicts(
    *,
    setup_state: SetupStateData,
    created_by_user_id: int,
    poll_service: PollPostingService,
) -> bool:
    setups = _poll_setups_from_state(
        setup_state=setup_state,
        created_by_user_id=created_by_user_id,
    )
    started_at = perf_counter()
    conflicts = await poll_service.find_active_conflicts(setups)
    duration_ms = int((perf_counter() - started_at) * 1000)
    logger.info(
        "poll_setup_conflict_check_done duration_ms=%s service_dates=%s conflict_count=%s",
        duration_ms,
        ",".join(setup.service_date.isoformat() for setup in setups),
        len(conflicts),
        extra={
            "duration_ms": duration_ms,
            "service_dates": tuple(setup.service_date.isoformat() for setup in setups),
            "conflict_count": len(conflicts),
        },
    )
    return bool(conflicts)


def _cached_allow_recreate(data: dict[str, object]) -> bool:
    return data.get("allow_recreate") is True


def _setup_text_for_conflicts(*, setup_state: SetupStateData, allow_recreate: bool) -> str:
    text = _setup_text(
        setup_state.service_dates,
        setup_state.first_lift_location,
        setup_state.cancelled_lift_times,
        selected_service_dates=setup_state.selected_service_dates,
    )
    if allow_recreate:
        return f"{text}\n\n{DUPLICATE_POLL_TEXT}"
    return text


def _poll_setups_from_state(
    *,
    setup_state: SetupStateData,
    created_by_user_id: int,
) -> tuple[PollSetup, ...]:
    return tuple(
        PollSetup(
            service_date=service_date,
            created_by_user_id=created_by_user_id,
            first_lift_location=setup_state.first_lift_location,
            cancelled_lift_times=setup_state.cancelled_lift_times,
        )
        for service_date in setup_state.selected_service_dates
    )


def _should_recreate_existing(*, requested: bool, has_conflicts: bool) -> bool:
    return requested and has_conflicts


def _setup_message_ids_for_cleanup(
    *,
    command_message_id: int,
    setup_message_id: int,
) -> tuple[int, ...]:
    return command_message_id, setup_message_id


def _menu_message_ids_for_cleanup(
    *,
    command_message_id: int,
    menu_message_id: int,
) -> tuple[int, ...]:
    return command_message_id, menu_message_id


def _setup_message_ids_from_menu_state(
    *,
    data: dict[str, object],
    setup_message_id: int,
) -> tuple[int, ...]:
    menu_message_ids = tuple(int(item) for item in _string_items(data.get("menu_message_ids", [])))
    if not menu_message_ids:
        return (setup_message_id,)
    return tuple(dict.fromkeys((*menu_message_ids, setup_message_id)))


async def _clear_menu_message_ids(*, state: FSMContext) -> None:
    await state.update_data(menu_message_ids=[])


def _setup_state_data(data: dict[str, object]) -> SetupStateData:
    return SetupStateData(
        service_dates=tuple(
            date.fromisoformat(item) for item in _string_items(data["service_dates"])
        ),
        selected_service_dates=_selected_service_dates(data),
        first_lift_location=StartLocation(str(data["first_lift_location"])),
        cancelled_lift_times=tuple(_string_items(data.get("cancelled_lift_times", []))),
        setup_view=str(data.get("setup_view", "main")),
    )


def _selected_service_dates(data: dict[str, object]) -> tuple[date, ...]:
    raw_dates = data.get("selected_service_dates", data["service_dates"])
    return tuple(date.fromisoformat(item) for item in _string_items(raw_dates))


def _setup_text(
    service_dates: tuple[date, ...],
    first_lift_location: StartLocation,
    cancelled_lift_times: tuple[str, ...],
    *,
    selected_service_dates: tuple[date, ...] | None = None,
) -> str:
    cancelled_set = set(cancelled_lift_times)
    enabled = ", ".join(lift.time for lift in DEFAULT_LIFTS if lift.time not in cancelled_set)
    cancelled = ", ".join(cancelled_lift_times) if cancelled_lift_times else "—"
    selected_dates = selected_service_dates or service_dates
    dates = " + ".join(
        f"{_service_day_name(item).title()} {item:%d.%m.%Y}" for item in selected_dates
    )
    return (
        f"🚐 Lift poll setup · {dates}\n"
        f"📍 First lift: {LOCATION_LABELS[first_lift_location].format()}\n"
        f"🕓 Enabled times: {enabled or '—'}\n"
        f"🚫 Cancelled lifts: {cancelled}"
    )


def _upcoming_weekend_dates(today: date | None = None) -> tuple[date, date]:
    saturday = _next_saturday(today)
    return saturday, saturday + timedelta(days=1)


def _next_saturday(today: date | None = None) -> date:
    current = today or datetime.now(UTC).date()
    days_until_saturday = (5 - current.weekday()) % 7
    if days_until_saturday == 0:
        return current
    return current + timedelta(days=days_until_saturday)


def _service_day_name(service_date: date) -> str:
    return "sunday" if service_date.weekday() == 6 else "saturday"


def _completion_text(
    *,
    message_ids: tuple[int, ...],
    pinned: bool,
    deleted_count: int,
    failed_count: int,
) -> str:
    pin_text = "pinned" if pinned else "not pinned"
    cleanup_text = f"cleaned {deleted_count} setup message(s)"
    if failed_count:
        cleanup_text += f", failed to clean {failed_count}"
    return (
        f"Polls created. Message IDs: {_message_ids_text(message_ids)}; {pin_text}; {cleanup_text}."
    )


def _should_notify_completion(*, pinned: bool, failed_count: int) -> bool:
    return failed_count > 0 or not pinned


def _should_send_recreate_report(report_text: str) -> bool:
    return "No tracked rider votes were found." not in report_text


def _accessible_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


def _string_items(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _message_ids_text(results_or_ids: Sequence[int] | Sequence[PollCreationResult]) -> str:
    if all(isinstance(item, int) for item in results_or_ids):
        ids = cast(Sequence[int], results_or_ids)
    else:
        ids = tuple(
            result.message_id for result in cast(Sequence[PollCreationResult], results_or_ids)
        )
    return ", ".join(str(item) for item in ids)
