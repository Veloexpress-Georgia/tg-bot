"""Private admin flow sharing the miniapp's previews and business operations."""

from datetime import date, timedelta
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.bot.handlers import _admin_private_message, _show_frozen, _show_menu
from veloexpress_bot.config import Settings
from veloexpress_bot.polls.bikepark import normalize_start_time, render_bike_park_poll
from veloexpress_bot.polls.extraday import extra_day_kind_row
from veloexpress_core.commands import CommandInput, Operations
from veloexpress_core.lifts import PollPostingService

router = Router(name="bike_park")


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


@router.callback_query(F.data.startswith("park:"))
async def handle_bike_park(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    poll_service: PollPostingService,
    operations: Operations,
) -> None:
    message = await _admin_private_message(callback, settings)
    if message is None:
        return
    parts = (callback.data or "").split(":", 2)
    action = parts[1]
    value = parts[2] if len(parts) > 2 else ""
    park = poll_service.bike_park
    today = park.now().date()
    data = await state.get_data()
    rows: list[list[InlineKeyboardButton]] = []
    try:
        if action == "menu":
            await state.clear()
            await _show_menu(message, poll_service, callback.from_user.id)
            await callback.answer()
            return
        if action == "new":
            await state.update_data(
                park_date=None, park_time="13:00", park_replace=None, park_spec=None
            )
            text = "🚵 Bike park\n\nChoose a date. Starts at Vake · minimum 6 riders."
            choices = [today + timedelta(days=i) for i in range(8)]
            rows = [
                [button(f"{day:%a %d %b}", f"park:date:{day}") for day in choices[i : i + 2]]
                for i in range(0, 8, 2)
            ]
        elif action in {"open", "cancel"}:
            selected = date.fromisoformat(value)
            parks = await park.list(service_date=selected)
            if not parks:
                raise ValueError("No active bike park poll on this date.")
            current = parks[0]
            if action == "cancel":
                spec = CommandInput(
                    request_id=uuid4(), action="cancel_bike_park", service_date=selected
                )
                preview = await operations.preview(spec)
                await state.update_data(
                    park_spec=spec.model_copy(
                        update={"expected_digest": preview["digest"]}
                    ).model_dump(mode="json")
                )
                text = preview["details"]
                rows = [
                    [button("🚫 Confirm cancellation", "park:post")],
                    [button("⬅️ Back", f"park:open:{selected}")],
                ]
            else:
                text = render_bike_park_poll(
                    selected, current.start_time, current.price_gel
                ).question
                text += f"\n\n{len(current.riders)} signed up · minimum 6"
                text += "\n" + "\n".join(r.label for r in current.riders)
                if current.started:
                    text += "\n\nSession has started. The actual rider count is checked on site."
                if current.status != "posted":
                    text += (
                        "\n\nPublication was not confirmed. "
                        "Check the group before cancelling and creating another poll."
                    )
                rows.append([button("🔄 Refresh", f"park:open:{selected}")])
                if current.poll_url:
                    rows.append([InlineKeyboardButton(text="Open poll", url=current.poll_url)])
                if not current.started:
                    rows.append([button("🚫 Cancel bike park", f"park:cancel:{selected}")])
        elif action == "post":
            if not data.get("park_spec"):
                raise ValueError("Open the preview again.")
            spec = CommandInput.model_validate(data["park_spec"])
            result = await operations.execute(
                spec, actor_user_id=callback.from_user.id, identity={}
            )
            await state.update_data(park_spec=None)
            text = str(result["message"])
            if result.get("report"):
                text += "\n\n" + str(result["report"])
            if spec.action == "bike_park":
                rows = [[button("🚵 Participants", f"park:open:{spec.service_date}")]]
        else:
            selected = (
                date.fromisoformat(value)
                if action == "date"
                else date.fromisoformat(str(data.get("park_date", "")))
            )
            start = str(data.get("park_time") or "13:00")
            replacement = data.get("park_replace") if action != "date" else None
            if action == "time":
                hour, minute = (int(part) for part in start.split(":"))
                minutes = (hour * 60 + minute + int(value)) % 1440
                start = f"{minutes // 60:02d}:{minutes % 60:02d}"
            elif action == "replace":
                replacement = value or None
            start = normalize_start_time(start)
            await state.update_data(
                park_date=selected.isoformat(),
                park_time=start,
                park_replace=replacement,
                park_spec=None,
            )
            values = await operations.runtime.terms.values()
            text = render_bike_park_poll(selected, start, values.bike_park_price_gel).question
            if action == "preview":
                spec = CommandInput(
                    request_id=uuid4(),
                    action="bike_park",
                    service_date=selected,
                    start_time=start,
                    replace_lift_time=replacement,
                )
                preview = await operations.preview(spec)
                await state.update_data(
                    park_spec=spec.model_copy(
                        update={"expected_digest": preview["digest"]}
                    ).model_dump(mode="json")
                )
                text = preview["details"]
                rows = [[button("🚀 Publish poll", "park:post")], [button("⬅️ Back", "park:edit")]]
            else:
                rows = [
                    extra_day_kind_row(selected, bike_park=True),
                    [
                        button("−30 min", "park:time:-30"),
                        button(f"🕒 {start}", "park:edit"),
                        button("+30 min", "park:time:30"),
                    ],
                    [button("−5 min", "park:time:-5"), button("+5 min", "park:time:5")],
                ]
                day = next(
                    (d for d in await poll_service.status_days() if d.service_date == selected),
                    None,
                )
                active = [lift for lift in day.lifts if not lift.cancelled] if day else []
                last = (
                    max(active, key=lambda lift: tuple(map(int, lift.time.split(":"))))
                    if active
                    else None
                )
                if day and last and not last.running and not day.past:
                    text += "\n\n" + (
                        f"Replace lift {replacement}"
                        if replacement
                        else "Add separately from ordinary lifts."
                    )
                    rows.append(
                        [
                            button(
                                "Add separately"
                                if replacement
                                else f"Replace last lift · {last.time}",
                                "park:replace:" if replacement else f"park:replace:{last.time}",
                            )
                        ]
                    )
                rows += [
                    [button("📅 Change date", "menu:extra_day")],
                    [button("Preview", "park:preview")],
                ]
        rows.append([button("☰ Menu", "park:menu")])
        await _show_frozen(
            message,
            poll_service,
            callback.from_user.id,
            "bike_park",
            text,
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer()
    except ValueError as error:
        await callback.answer(str(error)[:190], show_alert=True)
