import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import cast
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, ExceptionTypeFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardMarkup, Message, PollAnswer

from veloexpress_bot.bookings.render import (
    BUMPED_REPORT_PARSE_MODE,
    REFUND_REPORTS_PARSE_MODE,
    BookingMonitorDay,
    BookingMonitorDraft,
    decode_monitor_date,
    decode_monitor_time,
    render_bumped_report,
    render_cancel_lift_confirmation,
    render_posted_result,
    render_refund_reports,
)
from veloexpress_bot.bookings.screens import AdminScreen
from veloexpress_bot.bot.permissions import is_admin
from veloexpress_bot.bot.states import ExtraDayStates
from veloexpress_bot.config import Settings
from veloexpress_bot.history.render import render_statistics
from veloexpress_bot.history.service import HistoryStatistics, Period
from veloexpress_bot.payments.myday import (
    MY_DAY_PARSE_MODE,
    decode_guest_date,
    decode_guest_time,
    parse_deep_link,
)
from veloexpress_bot.payments.render import decode_board_date
from veloexpress_bot.payments.service import (
    CASH_METHOD,
    TRANSFER_METHOD,
    PaymentsService,
    StoredRefundReport,
)
from veloexpress_bot.polls.autoposter import PollAutoScheduler
from veloexpress_bot.polls.autoschedule import CardView
from veloexpress_bot.polls.extraday import (
    ExtraDayDraftState,
    ExtraDayView,
    render_extra_day_card,
)
from veloexpress_bot.polls.planner import WeekendPlanner
from veloexpress_bot.polls.schedule import (
    RangeBoundary,
    normalize_cancelled_lift_times,
    select_lift_range_boundary,
)
from veloexpress_bot.polls.service import (
    DuplicatePollError,
    PollPostingService,
    PollSetup,
)
from veloexpress_bot.polls.weekendplan import PlanCardView
from veloexpress_bot.service_day_defaults import (
    DEADLINE_STEP_MINUTES,
    PRICE_STEP_GEL,
    ServiceDayDefaultsStore,
)
from veloexpress_bot.telegram.errors import TelegramPollPostError, TelegramTargetForbiddenError

router = Router(name="admin_poll_setup")
logger = logging.getLogger(__name__)

ADMIN_ONLY_TEXT = "Admins only."
PRIVATE_ONLY_TEXT = "Open the bot in a private chat via /start."
STALE_SETUP_ALERT = "This menu is outdated. Run /start again."
STALE_BOARD_ALERT = "This payments board is outdated."
TELEGRAM_NOT_MODIFIED_TEXT = "message is not modified"

# Telegram invalidates a callback query a few seconds after the tap. Handlers ack
# last on purpose — the spinner is honest feedback while the work runs — so on a
# slow link the ack can arrive after the query died. The work itself already
# succeeded by then, so this deserves one log line, not a stack trace.
EXPIRED_CALLBACK_QUERY_TEXT = "query is too old"
POLL_POST_FAILED_TEXT = "Could not post polls. Check logs and try again."
POLL_TARGET_FORBIDDEN_TEXT = (
    "Could not post polls: Telegram rejected the target chat. The bot was likely removed "
    "from the configured group/topic. Add it back or update TELEGRAM_TARGET_CHAT_ID, "
    "then try again."
)
ALREADY_POSTED_ALERT = "Polls for this weekend are already posted."
# Only non-admins see this. Admins get live state instead of a greeting.
START_TEXT = "🚐 Veloexpress Bot"

# Riders who have already seen a payment warning for one day and method. The second
# tap on the same button is the acknowledgement. Kept in memory rather than the
# database: losing it on restart only shows one warning again.
_payment_warned: set[tuple[int, date, str]] = set()

PLAN_VIEWS: set[str] = {"main", "first", "last", "recreate"}
EXTRA_VIEWS: set[str] = {"date", "main", "first", "last"}
STATISTICS_PERIODS: set[str] = {"30d", "year", "all"}


@router.message(CommandStart(deep_link=True))
async def open_guest_form(
    message: Message,
    command: CommandObject,
    payments_service: PaymentsService,
) -> None:
    """Guest form, reached from the payments board. Open to every rider, not admins only.

    Tapping the deep link is pressing Start, which is how a rider who never opened
    the bot ends up with a private chat the bot may write to.
    """
    intent = parse_deep_link(command.args or "")
    if intent is None:
        await message.answer(STALE_BOARD_ALERT)
        return
    user = message.from_user
    if intent.method and user is not None:
        # "I paid" and "Cash" mean the money has moved, so arriving here settles
        # up — unless something needs saying first. A warning gets the card
        # instead of a claim: the board has to squeeze its warnings into a
        # 200-character toast and a second tap, while here there is room to lay
        # out which lifts filled, what is due now and what the whole day costs,
        # with the buttons underneath. Explaining beats charging quietly.
        await payments_service.claim(
            service_date=intent.service_date,
            telegram_user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            method=intent.method,
        )
    await payments_service.open_rider_card(
        telegram_user_id=user.id if user else 0,
        private_chat_id=message.chat.id,
        service_date=intent.service_date,
    )


@router.callback_query(F.data.startswith(("stats:", "rstats:", "astats:")))
async def handle_statistics(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    history_statistics: HistoryStatistics,
) -> None:
    mode = (callback.data or "").split(":", 1)[0]
    if mode == "rstats":
        message = _accessible_message(callback)
        if (
            message is None
            or message.chat.type != "private"
            or message.chat.id != callback.from_user.id
        ):
            await callback.answer(PRIVATE_ONLY_TEXT, show_alert=True)
            return
    else:
        message = await _admin_private_message(callback, settings)
        if message is None:
            return
    try:
        parts = (callback.data or "").split(":")
        if len(parts) != (4 if mode == "astats" else 3) or parts[1] not in STATISTICS_PERIODS:
            raise ValueError("Invalid statistics callback")
        period = cast(Period, parts[1])
        page = int(parts[-1])
        user_id = (
            int(parts[2])
            if mode == "astats"
            else callback.from_user.id
            if mode == "rstats"
            else None
        )
        if page < 0 or (user_id is not None and user_id <= 0):
            raise ValueError("Invalid statistics callback")
    except ValueError, IndexError:
        await callback.answer(STALE_BOARD_ALERT, show_alert=True)
        return
    view = await history_statistics.read(period=period, user_id=user_id)
    draft = render_statistics(
        view,
        personal=user_id is not None,
        page=page,
        admin_user_id=user_id if mode == "astats" else None,
    )
    if mode == "rstats":
        # A rider's own card. It lives in their chat, not on the admin card, so
        # there is no screen to remember.
        await _edit_card(message, draft.text, draft.reply_markup, parse_mode="HTML")
    else:
        await _show_frozen(
            message,
            poll_service,
            callback.from_user.id,
            "rider" if mode == "astats" else "stats",
            draft.text,
            draft.reply_markup,
            parse_mode="HTML",
            period=period,
            page=page,
            rider_id=user_id if mode == "astats" else None,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("guest:"))
async def handle_guest_form(
    callback: CallbackQuery,
    payments_service: PaymentsService,
) -> None:
    action, value = _split_callback(callback.data)
    if action == "noop":
        await callback.answer()
        return
    # Neither of these belongs to a day, so they are answered before the date
    # below is parsed — a season spans them all, and the card picks its own tab.
    if action == "season":
        draft = await payments_service.rider_season_card(telegram_user_id=callback.from_user.id)
        message = _accessible_message(callback)
        if message is not None:
            await _edit_card(message, draft.text, draft.reply_markup, parse_mode=MY_DAY_PARSE_MODE)
        await callback.answer()
        return
    if action == "card":
        await _refresh_rider_card(callback, payments_service, service_date=None)
        await callback.answer()
        return

    if action not in {
        "day",
        "payall",
        "cashall",
        "pay",
        "cash",
        "undo",
        "all",
        "allsub",
        "add",
        "sub",
    }:
        await callback.answer(STALE_BOARD_ALERT, show_alert=True)
        return

    parts = value.split(":")
    try:
        if len(parts) != (2 if action in {"add", "sub"} else 1):
            raise ValueError("Invalid guest callback")
        service_date = decode_guest_date(parts[0])
        if action in {"add", "sub"}:
            decode_guest_time(parts[1])
    except ValueError, IndexError:
        await callback.answer(STALE_BOARD_ALERT, show_alert=True)
        return

    user_id = callback.from_user.id
    if action == "day":
        # Tab switch: nothing changes but which day the card is showing.
        await _refresh_rider_card(callback, payments_service, service_date=service_date)
        await callback.answer()
        return
    if action in {"payall", "cashall", "pay", "cash"}:
        await _settle_from_card(
            callback,
            payments_service,
            service_date=service_date,
            method=CASH_METHOD if action in {"cashall", "cash"} else TRANSFER_METHOD,
            include_pending=action in {"payall", "cashall"},
        )
        return
    if action == "undo":
        notice = await payments_service.undo(
            service_date=service_date,
            telegram_user_id=user_id,
        )
        await _refresh_rider_card(callback, payments_service, service_date=service_date)
        await callback.answer(notice)
        return
    delta = -1 if action in {"sub", "allsub"} else 1
    if action in {"all", "allsub"}:
        lift_times = await payments_service.guest_lift_times(
            service_date=service_date,
            telegram_user_id=user_id,
            delta=delta,
        )
    else:
        lift_times = (decode_guest_time(parts[1]),)

    notice = await payments_service.adjust_guest_seats(
        service_date=service_date,
        telegram_user_id=user_id,
        lift_times=lift_times,
        delta=delta,
    )
    await _refresh_rider_card(callback, payments_service, service_date=service_date)
    await callback.answer(notice)


async def _settle_from_card(
    callback: CallbackQuery,
    payments_service: PaymentsService,
    *,
    service_date: date,
    method: str,
    include_pending: bool,
) -> None:
    """Settle from the private card, where the breakdown is already on screen.

    No two-tap warning applies here. The board's warnings exist because a group
    button charges an amount the rider cannot see the working for; the card is
    that working — it names the unfilled lifts and the waitlist above the
    button, so the tap is already informed.
    """
    user = callback.from_user
    outcome = await payments_service.claim(
        service_date=service_date,
        telegram_user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        method=method,
        acknowledged=True,
        include_pending=include_pending,
    )
    await _refresh_rider_card(callback, payments_service, service_date=service_date)
    await callback.answer(outcome.text)


async def _refresh_rider_card(
    callback: CallbackQuery,
    payments_service: PaymentsService,
    *,
    service_date: date | None,
) -> None:
    card = await payments_service.my_day_card(
        service_date=service_date,
        telegram_user_id=callback.from_user.id,
    )
    message = _accessible_message(callback)
    if message is not None:
        await _edit_card(message, card.text, card.reply_markup, parse_mode=MY_DAY_PARSE_MODE)


@router.message(Command("start"))
async def show_start_menu(
    message: Message,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if message.from_user is None or not is_admin(message.from_user.id, settings):
        await message.answer(f"{START_TEXT}\n\nAdmins only.")
        return

    if message.chat.type != "private":
        await message.answer(PRIVATE_ONLY_TEXT)
        return
    data = await state.get_data()
    stale = tuple(int(item) for item in _string_items(data.get("menu_message_ids", [])))
    await state.clear()
    # A day that is happening today beats one that is merely next: on a lift
    # morning that is the only tab worth opening on. With no active day at all
    # the same card renders as the menu, so /start is never a dead end.
    selected = _preferred_day(await poll_service.status_days(), today=_today(settings))
    message_id = await poll_service.open_booking_monitor(
        admin_user_id=message.from_user.id,
        private_chat_id=message.chat.id,
        selected_service_date=selected,
    )
    await state.update_data(menu_message_ids=[message_id])
    await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=tuple(dict.fromkeys((message.message_id, *stale))),
    )


def _preferred_day(days: tuple[BookingMonitorDay, ...], *, today: date) -> date | None:
    """Today if it is a lift day, otherwise the nearest day still ahead."""
    active = sorted(day.service_date for day in days if not day.past)
    if today in active:
        return today
    return active[0] if active else None


@router.message(Command("create_lift_poll"))
async def create_lift_poll(
    message: Message,
    settings: Settings,
    planner: WeekendPlanner,
    poll_service: PollPostingService,
    auto_scheduler: PollAutoScheduler,
) -> None:
    if not is_admin(message.from_user.id if message.from_user else None, settings):
        await message.answer("This command is only available to Veloexpress admins.")
        return
    if message.chat.type != "private":
        await message.answer(PRIVATE_ONLY_TEXT)
        return

    card = await planner.plan_card(schedule_label=await auto_scheduler.schedule_label())
    await message.answer(card.text, reply_markup=card.reply_markup)
    await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=(message.message_id,),
    )


@router.callback_query(F.data.in_({"menu:weekend_plan", "menu:create_lift_poll"}))
async def open_weekend_plan(
    callback: CallbackQuery,
    settings: Settings,
    planner: WeekendPlanner,
    auto_scheduler: PollAutoScheduler,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    card = await planner.plan_card(schedule_label=await auto_scheduler.schedule_label())
    await _edit_card(message, card.text, card.reply_markup)
    await callback.answer()


@router.callback_query(F.data == "menu:extra_day")
async def open_extra_day(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    cancelled = await poll_service.suggested_cancelled_lift_times()
    await state.set_state(ExtraDayStates.editing)
    await state.update_data(extra_date=None, extra_cancelled=list(cancelled))
    card = render_extra_day_card(
        ExtraDayDraftState(selected_date=None, cancelled_lift_times=cancelled),
        view="date",
        today=_today(settings),
    )
    await _edit_card(message, card.text, card.reply_markup)
    await callback.answer()


@router.callback_query(F.data.in_({"mon:menu", "mon:settings"}))
async def open_admin_menu(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    state: FSMContext,
) -> None:
    """Planning, the past and the settings, on the same card as the day.

    Opening this does not unregister the admin: the card stays theirs and keeps
    receiving the lift-day repost. What changes is that background refreshes
    leave it alone until they navigate back to a day.
    """
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    await state.clear()
    await state.update_data(menu_message_ids=[message.message_id])
    await _show_menu(message, poll_service, callback.from_user.id)
    await callback.answer()


# Older cards still carry this; it now lands on the day the admin is registered
# for rather than reposting a second monitor underneath the one they are reading.
@router.callback_query(F.data == "menu:booking_monitor")
async def open_booking_monitor(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    await callback.answer()
    await _show_monitor(message, poll_service, callback.from_user.id, None)


@router.callback_query(F.data == "menu:service_defaults")
async def open_service_day_defaults(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    service_day_defaults: ServiceDayDefaultsStore,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    card = await service_day_defaults.card()
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "settings",
        card.text,
        card.reply_markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("defaults:"))
async def handle_service_day_defaults(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    service_day_defaults: ServiceDayDefaultsStore,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    action, value = _split_callback(callback.data)
    if action == "menu":
        await _show_menu(message, poll_service, callback.from_user.id)
        await callback.answer()
        return
    try:
        if action == "price":
            await service_day_defaults.adjust_price(
                PRICE_STEP_GEL if value == "add" else -PRICE_STEP_GEL,
                admin_user_id=callback.from_user.id,
            )
        elif action == "deadline":
            await service_day_defaults.adjust_deadline(
                DEADLINE_STEP_MINUTES if value == "add" else -DEADLINE_STEP_MINUTES,
                admin_user_id=callback.from_user.id,
            )
        else:
            await callback.answer()
            return
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return
    card = await service_day_defaults.card()
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "settings",
        card.text,
        card.reply_markup,
    )
    await callback.answer("Saved. Days already published keep their own terms.")


@router.callback_query(F.data.startswith("mon:day:"))
async def select_booking_monitor_day(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    selected_date = decode_monitor_date((callback.data or "").removeprefix("mon:day:"))
    await callback.answer()
    await _show_monitor(message, poll_service, callback.from_user.id, selected_date)


@router.callback_query(F.data.startswith("mon:add:") | F.data.startswith("mon:sub:"))
async def adjust_manual_booking(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    action, compact_date, compact_time = (callback.data or "").split(":")[1:]
    delta = 1 if action == "add" else -1
    await callback.answer("Updating…")
    try:
        result = await poll_service.adjust_manual_booking(
            service_date=decode_monitor_date(compact_date),
            lift_time=decode_monitor_time(compact_time),
            delta=delta,
            admin_user_id=callback.from_user.id,
        )
    except ValueError as error:
        await message.answer(str(error))
        return

    await _show_lift(
        message,
        poll_service,
        callback.from_user.id,
        service_date=result.service_date,
        lift_time=result.lift_time,
    )

    if result.bumped:
        # The seat was taken from somebody. Said once, here, where the admin just
        # tapped — the monitor card itself only ever shows the current totals.
        await message.answer(
            render_bumped_report(
                service_date=result.service_date,
                lift_time=result.lift_time,
                riders=tuple(
                    (rider.label, rider.telegram_user_id, rider.paid) for rider in result.bumped
                ),
                price_gel=settings.payment_price_gel,
            ),
            parse_mode=BUMPED_REPORT_PARSE_MODE,
        )


# The middle "manage bookings" card is gone; old cards still link to it, and the
# day it named is where its buttons led anyway.
@router.callback_query(F.data.startswith(("mon:manage:", "mon:back:")))
async def open_day_from_retired_card(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    service_date = decode_monitor_date((callback.data or "").split(":")[2])
    await callback.answer()
    await _show_monitor(message, poll_service, callback.from_user.id, service_date)


@router.callback_query(F.data.startswith("mon:all:"))
async def open_all_riders(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    service_date = decode_monitor_date((callback.data or "").removeprefix("mon:all:"))
    draft = await poll_service.all_riders_view(selected_service_date=service_date)
    await _show_live(
        message,
        poll_service,
        callback.from_user.id,
        AdminScreen(name="riders", service_date=service_date),
        draft,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mon:history"))
async def open_lift_history(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    """A page of finished days: `mon:history[:<cursor>[:<period>]]`.

    The cursor is `0` for the newest page or the day an older page starts before.
    The period is the statistics span the admin came from, and it is only there
    so the way back lands on the figures they were reading.
    """
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    parts = (callback.data or "").split(":")[2:]
    cursor = parts[0] if parts else ""
    period = parts[1] if len(parts) > 1 and parts[1] in STATISTICS_PERIODS else None
    try:
        before = decode_monitor_date(cursor) if cursor and cursor != "0" else None
    except ValueError:
        before = None
    draft = await poll_service.lift_history_view(before=before, period=period)
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "history",
        draft.text,
        draft.reply_markup,
        before=before,
        period=period,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mon:past:"))
async def open_lift_day_audit(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    """One finished day in full, from what the bot wrote down the morning after."""
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    parts = (callback.data or "").split(":")[2:]
    service_date = decode_monitor_date(parts[0])
    period = parts[1] if len(parts) > 1 and parts[1] in STATISTICS_PERIODS else None
    draft = await poll_service.lift_day_audit_view(service_date=service_date, period=period)
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "audit",
        draft.text,
        draft.reply_markup,
        service_date=service_date,
        period=period,
    )
    await callback.answer()


@router.callback_query(F.data == "mon:trend")
async def open_lift_trend(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    draft = await poll_service.lift_trend_view()
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "trend",
        draft.text,
        draft.reply_markup,
    )
    await callback.answer()


# `mon:info:` and `mon:managelift:` were the old read-only and manage cards for a
# lift. Both are one card now, and old buttons in admin chats still point here.
@router.callback_query(F.data.startswith(("mon:lift:", "mon:info:", "mon:managelift:")))
async def open_lift_card(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    compact_date, compact_time = (callback.data or "").split(":")[2:4]
    service_date = decode_monitor_date(compact_date)
    shown = await _show_lift(
        message,
        poll_service,
        callback.from_user.id,
        service_date=service_date,
        lift_time=decode_monitor_time(compact_time),
    )
    if not shown:
        await callback.answer("This lift is no longer active.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("mon:refunds"))
async def show_past_refunds(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    payments_service: PaymentsService,
) -> None:
    """Past refund estimates, one per page, because the originals can be deleted.

    On the card rather than as loose messages: sending five stored reports into
    the chat buried the card under them and read as five fresh cancellations.
    """
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    cursor = (callback.data or "").removeprefix("mon:refunds").lstrip(":")
    page = int(cursor) if cursor.isdigit() else 0
    reports = await payments_service.recent_refund_reports()
    draft = render_refund_reports(
        tuple((_report_label(report, settings), report.text) for report in reports),
        page=page,
    )
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "refunds",
        draft.text,
        draft.reply_markup,
        parse_mode=REFUND_REPORTS_PARSE_MODE,
        page=page,
    )
    await callback.answer()


def _report_label(report: StoredRefundReport, settings: Settings) -> str:
    local = report.created_at.astimezone(ZoneInfo(settings.schedule_timezone))
    return f"{local:%d %b %Y, %H:%M}"


@router.callback_query(
    F.data.startswith(
        (
            "mon:cancel:",
            "mon:docancel:",
            "mon:restore:",
            "mon:cancelday:",
            "mon:docancelday:",
        )
    )
)
async def handle_lift_cancellation(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
    payments_service: PaymentsService,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    parts = (callback.data or "").split(":")
    action = parts[1]
    service_date = decode_monitor_date(parts[2])
    admin_user_id = callback.from_user.id

    if action == "cancelday":
        draft = await poll_service.cancel_day_confirmation_view(
            selected_service_date=service_date,
            # Read-only: the estimate an admin reads before deciding must not
            # file a refund report for a cancellation that may not happen.
            refund_preview=await payments_service.cancellation_preview(
                service_date=service_date,
            ),
        )
        if draft is None:
            await callback.answer("This day is no longer active.", show_alert=True)
            await _show_monitor(message, poll_service, admin_user_id, service_date)
            return
        await _show_frozen(
            message,
            poll_service,
            admin_user_id,
            "confirm_day",
            draft.text,
            draft.reply_markup,
            service_date=service_date,
        )
        await callback.answer()
        return
    if action == "docancelday":
        await poll_service.cancel_day(service_date=service_date, admin_user_id=admin_user_id)
        await callback.answer("Day cancelled.")
        await _show_monitor(message, poll_service, admin_user_id, None)
        await _report_refunds(message, payments_service, service_date=service_date)
        # Only after the report: it is the record, and the money is going back, so a
        # revived poll must not open holding payments the bot no longer has.
        await payments_service.forget_day(service_date=service_date)
        return

    lift_time = decode_monitor_time(parts[3])
    if action == "cancel":
        detail = await poll_service.lift_detail(service_date=service_date, lift_time=lift_time)
        if detail is None:
            await callback.answer("This lift is no longer active.", show_alert=True)
            await _show_monitor(message, poll_service, admin_user_id, service_date)
            return
        status, riders = detail
        draft = render_cancel_lift_confirmation(
            service_date=service_date,
            lift=status,
            riders=riders,
            refund_preview=await payments_service.cancellation_preview(
                service_date=service_date,
                cancelled_lift_time=lift_time,
            ),
        )
        await _show_frozen(
            message,
            poll_service,
            admin_user_id,
            "confirm_lift",
            draft.text,
            draft.reply_markup,
            service_date=service_date,
            lift_time=lift_time,
        )
        await callback.answer()
        return
    if action == "docancel":
        await poll_service.cancel_lift(
            service_date=service_date, lift_time=lift_time, admin_user_id=admin_user_id
        )
        await callback.answer("Lift cancelled.")
        await _show_monitor(message, poll_service, admin_user_id, service_date)
        await _report_refunds(
            message,
            payments_service,
            service_date=service_date,
            cancelled_lift_time=lift_time,
        )
        return

    await poll_service.restore_lift(
        service_date=service_date, lift_time=lift_time, admin_user_id=admin_user_id
    )
    await callback.answer("Lift restored.")
    shown = await _show_lift(
        message,
        poll_service,
        admin_user_id,
        service_date=service_date,
        lift_time=lift_time,
    )
    if not shown:
        await _show_monitor(message, poll_service, admin_user_id, service_date)


@router.callback_query(F.data.startswith("plan:"))
async def handle_weekend_plan_card(
    callback: CallbackQuery,
    settings: Settings,
    planner: WeekendPlanner,
    poll_service: PollPostingService,
    auto_scheduler: PollAutoScheduler,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    action, value = _split_callback(callback.data)

    if action == "schedule":
        card = await auto_scheduler.schedule_card()
        await _show_frozen(
            message,
            poll_service,
            callback.from_user.id,
            "schedule",
            card.text,
            card.reply_markup,
        )
        await callback.answer()
        return
    if action == "menu":
        await _show_menu(message, poll_service, callback.from_user.id)
        await callback.answer()
        return
    if action == "posted":
        await callback.answer(
            "Already posted. Use ♻️ Recreate polls to replace this weekend.",
            show_alert=True,
        )
        return

    view: PlanCardView = "main"
    answer_text: str | None = None
    try:
        if action == "view" and value in PLAN_VIEWS:
            view = cast(PlanCardView, value)
        elif action == "day":
            await planner.toggle_day(value, admin_user_id=callback.from_user.id)
        elif action in {"first", "last"}:
            await planner.set_range_boundary(
                cast(RangeBoundary, action),
                value,
                admin_user_id=callback.from_user.id,
            )
        elif action == "skip":
            await planner.toggle_skip(admin_user_id=callback.from_user.id)
        elif action == "post":
            answer_text, view = await _post_now(
                callback=callback,
                message=message,
                planner=planner,
                poll_service=poll_service,
            )
            if answer_text is None:
                return
        elif action == "recreate":
            answer_text = await _recreate_weekend(
                callback=callback,
                message=message,
                planner=planner,
            )
            if answer_text is None:
                return
        else:
            await callback.answer()
            return
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return

    card = await planner.plan_card(view=view)
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "plan",
        card.text,
        card.reply_markup,
    )
    await callback.answer(answer_text)


async def _post_now(
    *,
    callback: CallbackQuery,
    message: Message,
    planner: WeekendPlanner,
    poll_service: PollPostingService,
) -> tuple[str | None, PlanCardView]:
    try:
        result = await planner.post_now(admin_user_id=callback.from_user.id)
    except TelegramTargetForbiddenError:
        logger.exception("Weekend plan posting failed because target chat rejected the bot")
        await callback.answer()
        await message.answer(POLL_TARGET_FORBIDDEN_TEXT)
        return None, "main"
    except TelegramPollPostError:
        logger.exception("Weekend plan posting failed while sending Telegram poll")
        await callback.answer()
        await message.answer(POLL_POST_FAILED_TEXT)
        return None, "main"
    except DuplicatePollError:
        return ALREADY_POSTED_ALERT, "recreate"

    if result.already_posted:
        return ALREADY_POSTED_ALERT, "recreate"
    # The result replaces the planning card rather than flashing past it: the
    # plan screen left behind still reads as though nothing had been posted.
    draft = render_posted_result(
        result.service_dates,
        back_callback="menu:weekend_plan",
        back_text="📋 Weekend",
    )
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "posted",
        draft.text,
        draft.reply_markup,
    )
    poll_label = "poll" if result.created_count == 1 else "polls"
    await callback.answer(f"Posted {result.created_count} {poll_label}.")
    return None, "main"


async def _recreate_weekend(
    *,
    callback: CallbackQuery,
    message: Message,
    planner: WeekendPlanner,
) -> str | None:
    try:
        report_text, cleanup_failed_count = await planner.recreate(
            admin_user_id=callback.from_user.id
        )
    except DuplicatePollError:
        return "No active polls to recreate — use Post now."
    except TelegramTargetForbiddenError:
        logger.exception("Weekend recreate failed because target chat rejected the bot")
        await callback.answer()
        await message.answer(POLL_TARGET_FORBIDDEN_TEXT)
        return None
    except TelegramPollPostError:
        logger.exception("Weekend recreate failed while sending Telegram poll")
        await callback.answer()
        await message.answer(POLL_POST_FAILED_TEXT)
        return None

    if _should_send_recreate_report(report_text):
        await message.answer(report_text)
    if cleanup_failed_count:
        await message.answer(
            "Replacement polls were created, but some old poll messages could not be "
            "deleted. Check the chat and remove stale polls manually."
        )
    return "Polls recreated."


@router.callback_query(F.data.startswith("sched:"))
async def handle_poll_schedule_card(
    callback: CallbackQuery,
    settings: Settings,
    auto_scheduler: PollAutoScheduler,
    poll_service: PollPostingService,
    planner: WeekendPlanner,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    action, value = _split_callback(callback.data)

    if action == "plan":
        card = await planner.plan_card(schedule_label=await auto_scheduler.schedule_label())
        await _edit_card(message, card.text, card.reply_markup)
        await callback.answer()
        return
    if action == "menu":
        await _show_menu(message, poll_service, callback.from_user.id)
        await callback.answer()
        return

    view: CardView = "main"
    try:
        if action == "view" and value in {"main", "day", "time"}:
            view = cast(CardView, value)
        elif action == "toggle":
            await auto_scheduler.toggle_enabled(admin_user_id=callback.from_user.id)
        elif action == "day":
            await auto_scheduler.set_creation_weekday(
                int(value), admin_user_id=callback.from_user.id
            )
        elif action == "time":
            await auto_scheduler.set_creation_time(value, admin_user_id=callback.from_user.id)
        elif action == "announce":
            await auto_scheduler.cycle_announce_lead(admin_user_id=callback.from_user.id)
        elif action == "skip":
            await auto_scheduler.toggle_skip(admin_user_id=callback.from_user.id)
        else:
            await callback.answer()
            return
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return

    card = await auto_scheduler.schedule_card(view=view)
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "schedule",
        card.text,
        card.reply_markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("extra:"))
async def handle_extra_day_card(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
    auto_scheduler: PollAutoScheduler,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return

    action, value = _split_callback(callback.data)

    if action == "menu":
        await state.clear()
        await _show_menu(message, poll_service, callback.from_user.id)
        await callback.answer()
        return

    draft = await _extra_day_state(state, poll_service)
    view: ExtraDayView = "main"
    answer_text: str | None = None
    try:
        if action == "view" and value in EXTRA_VIEWS:
            view = cast(ExtraDayView, value)
        elif action == "date":
            draft = ExtraDayDraftState(
                selected_date=date.fromisoformat(value),
                cancelled_lift_times=draft.cancelled_lift_times,
            )
        elif action in {"first", "last"}:
            draft = ExtraDayDraftState(
                selected_date=draft.selected_date,
                cancelled_lift_times=select_lift_range_boundary(
                    draft.cancelled_lift_times,
                    boundary="first" if action == "first" else "last",
                    selected_time=value,
                ),
            )
        elif action == "post":
            answer_text = await _post_extra_day(
                callback=callback,
                message=message,
                state=state,
                poll_service=poll_service,
                draft=draft,
            )
            if answer_text is None:
                return
        else:
            await callback.answer()
            return
    except ValueError as error:
        await callback.answer(str(error), show_alert=True)
        return

    await state.set_state(ExtraDayStates.editing)
    await state.update_data(
        extra_date=draft.selected_date.isoformat() if draft.selected_date else None,
        extra_cancelled=list(draft.cancelled_lift_times),
    )
    card = render_extra_day_card(draft, view=view, today=_today(settings))
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "extra",
        card.text,
        card.reply_markup,
    )
    await callback.answer(answer_text)


async def _post_extra_day(
    *,
    callback: CallbackQuery,
    message: Message,
    state: FSMContext,
    poll_service: PollPostingService,
    draft: ExtraDayDraftState,
) -> str | None:
    if draft.selected_date is None:
        return "Pick a date first."

    setup = PollSetup(
        service_date=draft.selected_date,
        created_by_user_id=callback.from_user.id,
        cancelled_lift_times=draft.cancelled_lift_times,
    )
    try:
        await poll_service.create_poll(setup, include_notice=True, pin_after_send=False)
    except DuplicatePollError:
        # Not a dead end: the day exists, so offer it instead of the same form.
        await state.clear()
        await callback.answer("This day already has polls.")
        await _show_monitor(
            message,
            poll_service,
            callback.from_user.id,
            draft.selected_date,
        )
        return None
    except TelegramTargetForbiddenError:
        logger.exception("Extra day posting failed because target chat rejected the bot")
        await callback.answer()
        await message.answer(POLL_TARGET_FORBIDDEN_TEXT)
        return None
    except TelegramPollPostError:
        logger.exception("Extra day posting failed while sending Telegram poll")
        await callback.answer()
        await message.answer(POLL_POST_FAILED_TEXT)
        return None

    await state.clear()
    result = render_posted_result(
        (draft.selected_date,),
        back_callback="mon:menu",
        back_text="☰ Menu",
    )
    await _show_frozen(
        message,
        poll_service,
        callback.from_user.id,
        "posted",
        result.text,
        result.reply_markup,
    )
    await callback.answer("Posted extra day polls.")
    return None


async def _extra_day_state(
    state: FSMContext,
    poll_service: PollPostingService,
) -> ExtraDayDraftState:
    data = await state.get_data()
    raw_date = data.get("extra_date")
    raw_cancelled = data.get("extra_cancelled")
    if raw_cancelled is None:
        cancelled = await poll_service.suggested_cancelled_lift_times()
    else:
        cancelled = normalize_cancelled_lift_times(_string_items(raw_cancelled))
    return ExtraDayDraftState(
        selected_date=date.fromisoformat(str(raw_date)) if raw_date else None,
        cancelled_lift_times=cancelled,
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


@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_button(
    callback: CallbackQuery,
    payments_service: PaymentsService,
) -> None:
    """Payments board taps. Open to every rider, not just admins.

    Inline buttons in the group reach people who never opened a private chat
    with the bot, which is most of the group; the reply is a private toast so a
    shared keyboard can still give personal feedback.
    """
    action, value = _split_callback(callback.data)
    user = callback.from_user
    try:
        service_date = decode_board_date(value)
    except ValueError:
        await callback.answer(STALE_BOARD_ALERT, show_alert=True)
        return

    if action in {"paid", "cash"}:
        gesture = (user.id, service_date, action)
        outcome = await payments_service.claim(
            service_date=service_date,
            telegram_user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            method=CASH_METHOD if action == "cash" else TRANSFER_METHOD,
            acknowledged=gesture in _payment_warned,
        )
        if outcome.needs_confirmation:
            _payment_warned.add(gesture)
            await callback.answer(outcome.text, show_alert=True)
            return
        _payment_warned.discard(gesture)
        notice = outcome.text
    elif action == "undo":
        notice = await payments_service.undo(
            service_date=service_date,
            telegram_user_id=user.id,
        )
    else:
        notice = STALE_BOARD_ALERT

    await callback.answer(notice, show_alert=False)


@router.message(F.pinned_message)
async def cleanup_bot_pin_notice(
    message: Message,
    bot: Bot,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    if not _should_cleanup_bot_pin_notice(
        actor_user_id=message.from_user.id if message.from_user else None,
        bot_user_id=bot.id,
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
        settings=settings,
    ):
        return
    await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=(message.message_id,),
    )


# Registered after the narrower message handlers on purpose: this one matches any
# forum-topic message, and the first matching handler ends propagation.
@router.message(F.message_thread_id, F.from_user, ~F.pinned_message)
async def watch_payments_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    payments_service: PaymentsService,
) -> None:
    """Note that a rider wrote in the payments topic — who and when, nothing more.

    A rider who reported their own payment should not then get a second line
    posted about them by the bot.
    """
    actor = message.from_user
    if actor is None or not _is_payments_topic_post(
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
        actor_user_id=actor.id,
        bot_user_id=bot.id,
        settings=settings,
    ):
        return
    await payments_service.record_topic_post(
        telegram_user_id=actor.id,
        posted_at=message.date,
    )


def _is_payments_topic_post(
    *,
    chat_id: int,
    thread_id: int | None,
    actor_user_id: int,
    bot_user_id: int,
    settings: Settings,
) -> bool:
    return (
        settings.telegram_payments_thread_id is not None
        and chat_id == settings.telegram_target_chat_id
        and thread_id == settings.telegram_payments_thread_id
        and actor_user_id != bot_user_id
    )


@router.callback_query(
    F.data.startswith("day:toggle:")
    | F.data.startswith("lift:toggle:")
    | F.data.startswith("range:")
    | F.data.startswith("view:")
    | F.data.startswith("poll:")
)
async def handle_stale_setup_callback(
    callback: CallbackQuery,
    settings: Settings,
    poll_service: PollPostingService,
) -> None:
    """Old poll-setup messages may still carry buttons from the retired flow."""
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return

    message = _accessible_message(callback)
    if message is None:
        await callback.answer(STALE_SETUP_ALERT, show_alert=True)
        return

    await callback.answer(STALE_SETUP_ALERT, show_alert=True)
    await poll_service.cleanup_setup_messages(
        chat_id=message.chat.id,
        message_ids=(message.message_id,),
    )
    logger.info(
        "Removed stale poll setup message",
        extra={"message_id": message.message_id, "callback_data": callback.data},
    )


async def _admin_private_message(
    callback: CallbackQuery,
    settings: Settings,
) -> Message | None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer(ADMIN_ONLY_TEXT, show_alert=True)
        return None
    message = _accessible_message(callback)
    if message is None or message.chat.type != "private":
        await callback.answer(PRIVATE_ONLY_TEXT, show_alert=True)
        return None
    return message


async def _show_monitor(
    message: Message,
    poll_service: PollPostingService,
    admin_user_id: int,
    service_date: date | None,
) -> None:
    draft = await poll_service.booking_monitor_view(selected_service_date=service_date)
    await _show_live(
        message,
        poll_service,
        admin_user_id,
        AdminScreen(name="day", service_date=service_date),
        draft,
    )


async def _show_lift(
    message: Message,
    poll_service: PollPostingService,
    admin_user_id: int,
    *,
    service_date: date,
    lift_time: str,
) -> bool:
    """False when the lift is gone, so the caller can say so and fall back."""
    draft = await poll_service.lift_screen_view(
        service_date=service_date,
        lift_time=lift_time,
    )
    if draft is None:
        await _show_monitor(message, poll_service, admin_user_id, service_date)
        return False
    await _show_live(
        message,
        poll_service,
        admin_user_id,
        AdminScreen(name="lift", service_date=service_date, lift_time=lift_time),
        draft,
    )
    return True


async def _show_live(
    message: Message,
    poll_service: PollPostingService,
    admin_user_id: int,
    screen: AdminScreen,
    draft: BookingMonitorDraft,
) -> None:
    """Draw a screen the background refresh is allowed to keep up to date."""
    await _edit_card(message, draft.text, draft.reply_markup)
    await poll_service.record_screen(admin_user_id=admin_user_id, screen=screen)


async def _show_frozen(
    message: Message,
    poll_service: PollPostingService,
    admin_user_id: int,
    name: str,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    parse_mode: str | None = None,
    service_date: date | None = None,
    lift_time: str | None = None,
    period: str | None = None,
    page: int = 0,
    rider_id: int | None = None,
    before: date | None = None,
) -> None:
    """Draw a screen the admin chose, which background work must not replace.

    The registration itself is untouched: this card still belongs to them, and
    the next `/start`, day tab or lift-day repost picks it straight back up.
    """
    await _edit_card(message, text, reply_markup, parse_mode=parse_mode)
    await poll_service.record_screen(
        admin_user_id=admin_user_id,
        screen=AdminScreen(
            name=name,
            service_date=service_date,
            lift_time=lift_time,
            period=period,
            page=page,
            rider_id=rider_id,
            before=before,
        ),
    )


async def _report_refunds(
    message: Message,
    payments_service: PaymentsService,
    *,
    service_date: date,
    cancelled_lift_time: str | None = None,
) -> None:
    """A cancellation with money already in needs a refund list, not a toast."""
    report = await payments_service.cancellation_report(
        service_date=service_date,
        cancelled_lift_time=cancelled_lift_time,
    )
    if report is not None:
        await message.answer(report, parse_mode="HTML")


async def _show_menu(
    message: Message,
    poll_service: PollPostingService,
    admin_user_id: int,
) -> None:
    draft = await poll_service.menu_view()
    await _show_frozen(
        message,
        poll_service,
        admin_user_id,
        "menu",
        draft.text,
        draft.reply_markup,
    )


async def _edit_card(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    parse_mode: str | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as error:
        if TELEGRAM_NOT_MODIFIED_TEXT not in str(error).lower():
            raise


def _is_expired_callback_query(error: BaseException) -> bool:
    return EXPIRED_CALLBACK_QUERY_TEXT in str(error).lower()


@router.errors(ExceptionTypeFilter(TelegramBadRequest))
async def report_expired_callback_query(event: ErrorEvent) -> object:
    """Log a dead callback ack quietly; let every other Telegram error surface."""
    if not _is_expired_callback_query(event.exception):
        return UNHANDLED
    callback = event.update.callback_query
    logger.info(
        "callback_query_expired update_id=%s data=%s",
        event.update.update_id,
        callback.data if callback is not None else None,
        extra={
            "update_id": event.update.update_id,
            "callback_data": callback.data if callback is not None else None,
        },
    )
    return True


def _should_cleanup_bot_pin_notice(
    *,
    actor_user_id: int | None,
    bot_user_id: int,
    chat_id: int,
    thread_id: int | None,
    settings: Settings,
) -> bool:
    # Both topics the bot pins in: the lift topic for polls, the payments topic for
    # the board. Otherwise its own "pinned a message" notices pile up and later turn
    # into "pinned Deleted message".
    return (
        actor_user_id == bot_user_id
        and chat_id == settings.telegram_target_chat_id
        and thread_id in {settings.telegram_target_thread_id, settings.telegram_payments_thread_id}
    )


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


def _split_callback(data: str | None) -> tuple[str, str]:
    """Return (action, value); value keeps colons so lift times like 15:30 survive."""
    parts = (data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    value = ":".join(parts[2:])
    return action, value


def _today(settings: Settings) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(settings.schedule_timezone)).date()
