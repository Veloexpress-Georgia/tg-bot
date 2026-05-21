import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from veloexpress_bot.bot.keyboards import setup_keyboard
from veloexpress_bot.bot.permissions import is_admin
from veloexpress_bot.bot.states import PollSetupStates
from veloexpress_bot.config import Settings
from veloexpress_bot.polls.defaults import DEFAULT_LIFTS, StartLocation
from veloexpress_bot.polls.service import DuplicatePollError, PollPostingService, PollSetup
from veloexpress_bot.telegram.errors import TelegramPollPostError, TelegramTargetForbiddenError

router = Router(name="admin_poll_setup")
logger = logging.getLogger(__name__)

ADMIN_ONLY_TEXT = "Admins only."
DUPLICATE_POLL_ALERT = "Poll already exists. Confirm again to create anyway."
DUPLICATE_POLL_TEXT = (
    "A poll with these settings is already stored. If you deleted it in Telegram, use "
    "Create anyway. If only one day already exists, disable that day and create the "
    "remaining poll."
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


@dataclass(frozen=True)
class SetupStateData:
    service_dates: tuple[date, ...]
    selected_service_dates: tuple[date, ...]
    first_lift_location: StartLocation
    cancelled_lift_times: tuple[str, ...]


@router.message(Command("start", "help"))
async def show_help(
    message: Message,
    settings: Settings,
) -> None:
    admin_text = (
        "\n\nAdmin command:\n"
        "/create_lift_poll - create a weekend lift poll with the standard Veloexpress template"
        if is_admin(message.from_user.id if message.from_user else None, settings)
        else "\n\nOnly Veloexpress admins can create lift polls."
    )
    await message.answer(
        "Veloexpress bot helps admins create weekend lift polls.\n"
        "It currently runs in polling mode and does not handle payments or balances."
        f"{admin_text}"
    )


@router.message(Command("create_lift_poll"))
async def create_lift_poll(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await message.answer("This command is only available to Veloexpress admins.")
        return

    service_dates = _upcoming_weekend_dates()
    first_lift_location = StartLocation.JUSTICE_HALL
    cancelled_lift_times: tuple[str, ...] = ()
    await state.set_state(PollSetupStates.editing)
    await state.update_data(
        service_dates=[service_date.isoformat() for service_date in service_dates],
        selected_service_dates=[service_date.isoformat() for service_date in service_dates],
        first_lift_location=first_lift_location.value,
        cancelled_lift_times=list(cancelled_lift_times),
        setup_message_ids=[],
    )
    sent = await message.answer(
        _setup_text(service_dates, first_lift_location, cancelled_lift_times),
        reply_markup=setup_keyboard(
            service_dates=service_dates,
            selected_service_dates=service_dates,
            cancelled_lift_times=cancelled_lift_times,
            first_lift_location=first_lift_location,
        ),
    )
    await state.update_data(
        setup_message_ids=list(
            _setup_message_ids_for_cleanup(
                chat_type=message.chat.type,
                command_message_id=message.message_id,
                setup_message_id=sent.message_id,
            )
        )
    )


@router.callback_query(PollSetupStates.editing, F.data.startswith("day:toggle:"))
async def toggle_service_day(callback: CallbackQuery, state: FSMContext) -> None:
    service_date = date.fromisoformat(callback.data.removeprefix("day:toggle:"))
    data = await state.get_data()
    selected_dates = set(data.get("selected_service_dates", data["service_dates"]))

    if service_date.isoformat() in selected_dates:
        if len(selected_dates) == 1:
            await callback.answer("At least one day must remain.", show_alert=True)
            return
        selected_dates.remove(service_date.isoformat())
    else:
        selected_dates.add(service_date.isoformat())

    await state.update_data(selected_service_dates=sorted(selected_dates))
    await _refresh_setup(callback, state)


@router.callback_query(PollSetupStates.editing, F.data == "first:toggle")
async def toggle_first_location(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = StartLocation(data["first_lift_location"])
    next_location = (
        StartLocation.VAKE if current == StartLocation.JUSTICE_HALL else StartLocation.JUSTICE_HALL
    )
    await state.update_data(first_lift_location=next_location.value)
    await _refresh_setup(callback, state)


@router.callback_query(PollSetupStates.editing, F.data.startswith("lift:toggle:"))
async def toggle_lift(callback: CallbackQuery, state: FSMContext) -> None:
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
    await _refresh_setup(callback, state)


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
    if callback.message:
        cleanup = await poll_service.cleanup_setup_messages(
            chat_id=callback.message.chat.id,
            message_ids=setup_message_ids,
        )
        if cleanup.failed_count:
            await callback.message.edit_text(POLL_SETUP_CANCELLED_TEXT)


@router.callback_query(PollSetupStates.editing, F.data.in_({"poll:confirm", "poll:confirm_force"}))
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
    allow_duplicate = callback.data == "poll:confirm_force"
    setups = tuple(
        PollSetup(
            service_date=service_date,
            created_by_user_id=callback.from_user.id,
            first_lift_location=setup_state.first_lift_location,
            cancelled_lift_times=setup_state.cancelled_lift_times,
        )
        for service_date in setup_state.selected_service_dates
    )

    has_existing_poll = False
    if not allow_duplicate:
        for setup in setups:
            if await poll_service.has_existing_active_poll(setup):
                has_existing_poll = True
                break

    if has_existing_poll:
        if callback.message:
            await callback.message.edit_text(
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
                    allow_duplicate=True,
                ),
            )
        await callback.answer(
            DUPLICATE_POLL_ALERT,
            show_alert=True,
        )
        return

    try:
        results = [
            await poll_service.create_poll(
                setup,
                allow_duplicate=allow_duplicate,
                pin_after_send=False,
            )
            for setup in setups
        ]
        results = [await poll_service.pin_created_poll(result) for result in results]
    except DuplicatePollError:
        await callback.answer("One of these polls was already posted.", show_alert=True)
        return
    except TelegramTargetForbiddenError:
        logger.exception("Poll creation failed because target chat rejected the bot")
        await callback.answer(POLL_TARGET_FORBIDDEN_ALERT, show_alert=True)
        if callback.message:
            await callback.message.answer(POLL_TARGET_FORBIDDEN_TEXT)
        return
    except TelegramPollPostError:
        logger.exception("Poll creation failed while sending Telegram poll")
        await callback.answer(POLL_POST_FAILED_ALERT, show_alert=True)
        if callback.message:
            await callback.message.answer(POLL_POST_FAILED_TEXT)
        return

    setup_message_ids = tuple(int(item) for item in data.get("setup_message_ids", []))
    await state.clear()
    await callback.answer(f"Polls created: {_message_ids_text(results)}.")
    if callback.message:
        cleanup = await poll_service.cleanup_setup_messages(
            chat_id=callback.message.chat.id,
            message_ids=setup_message_ids,
        )
        completion_text = _completion_text(
            message_ids=tuple(result.message_id for result in results),
            pinned=all(result.pinned for result in results),
            deleted_count=cleanup.deleted_count,
            failed_count=cleanup.failed_count,
        )
        logger.info(
            "Poll setup completed",
            extra={
                "message_ids": _message_ids_text(results),
                "all_pinned": all(result.pinned for result in results),
                "deleted_count": cleanup.deleted_count,
                "failed_count": cleanup.failed_count,
            },
        )
        if _should_notify_completion(
            pinned=all(result.pinned for result in results),
            failed_count=cleanup.failed_count,
        ):
            await callback.message.answer(completion_text)


@router.callback_query(
    (F.data == "first:toggle")
    | F.data.startswith("day:toggle:")
    | F.data.startswith("lift:toggle:")
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

    if not callback.message:
        await callback.answer(
            STALE_SETUP_ALERT,
            show_alert=True,
        )
        return

    cleanup = await poll_service.cleanup_setup_messages(
        chat_id=callback.message.chat.id,
        message_ids=(callback.message.message_id,),
    )

    if callback.data == "poll:cancel":
        await callback.answer("Expired setup removed.")
    else:
        await callback.answer(
            STALE_SETUP_ALERT,
            show_alert=True,
        )

    if cleanup.failed_count:
        await callback.message.edit_text(EXPIRED_SETUP_TEXT)

    if callback.data == "poll:cancel":
        return

    logger.info(
        "Removed stale poll setup message",
        extra={"message_id": callback.message.message_id, "callback_data": callback.data},
    )


async def _refresh_setup(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    setup_state = _setup_state_data(data)
    if callback.message:
        await callback.message.edit_text(
            _setup_text(
                setup_state.service_dates,
                setup_state.first_lift_location,
                setup_state.cancelled_lift_times,
                selected_service_dates=setup_state.selected_service_dates,
            ),
            reply_markup=setup_keyboard(
                service_dates=setup_state.service_dates,
                selected_service_dates=setup_state.selected_service_dates,
                cancelled_lift_times=setup_state.cancelled_lift_times,
                first_lift_location=setup_state.first_lift_location,
            ),
        )
    await callback.answer()


def _setup_message_ids_for_cleanup(
    *,
    chat_type: str,
    command_message_id: int,
    setup_message_id: int,
) -> tuple[int, ...]:
    if chat_type == "private":
        return (setup_message_id,)
    return command_message_id, setup_message_id


def _setup_state_data(data: dict[str, object]) -> SetupStateData:
    return SetupStateData(
        service_dates=tuple(date.fromisoformat(str(item)) for item in data["service_dates"]),
        selected_service_dates=_selected_service_dates(data),
        first_lift_location=StartLocation(str(data["first_lift_location"])),
        cancelled_lift_times=tuple(str(item) for item in data.get("cancelled_lift_times", [])),
    )


def _selected_service_dates(data: dict[str, object]) -> tuple[date, ...]:
    raw_dates = data.get("selected_service_dates", data["service_dates"])
    return tuple(date.fromisoformat(str(item)) for item in raw_dates)


def _setup_text(
    service_dates: tuple[date, ...],
    first_lift_location: StartLocation,
    cancelled_lift_times: tuple[str, ...],
    *,
    selected_service_dates: tuple[date, ...] | None = None,
) -> str:
    cancelled = ", ".join(cancelled_lift_times) if cancelled_lift_times else "none"
    selected_dates = selected_service_dates or service_dates
    dates = " + ".join(
        f"{_service_day_name(item).title()} {item:%d.%m.%Y}" for item in selected_dates
    )
    return (
        f"Lift poll setup for {dates}\n"
        f"First lift: {first_lift_location.value}\n"
        f"Cancelled lifts: {cancelled}"
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


def _message_ids_text(results_or_ids: object) -> str:
    if isinstance(results_or_ids, tuple):
        ids = results_or_ids
    else:
        ids = tuple(result.message_id for result in results_or_ids)
    return ", ".join(str(item) for item in ids)
