"""Compact private-card controls for choosing a rider and their new position."""

from datetime import date
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.bookings.render import BookingMonitorDraft
from veloexpress_core.booking_order import payment_label

PAGE_SIZE = 8


def render_booking_order(payload: dict[str, Any]) -> BookingMonitorDraft:
    token = payload["token"]
    view = payload["view"]
    riders = view["riders"]
    page = payload.get("page", 0)
    mode = payload.get("mode", "list")
    source = payload.get("source")
    day = date.fromisoformat(payload["date"])
    time = payload["time"]
    back = f"mon:lift:{day:%Y%m%d}:{time.replace(':', '').zfill(4)}"
    lines = [f"↕️ Booking order · {time} · {day:%d %b}", ""]
    rows: list[list[InlineKeyboardButton]] = []
    if mode == "confirm":
        lines.append(payload["preview"]["details"])
        rows.append([InlineKeyboardButton(text="✅ Save order", callback_data=f"ord:save:{token}")])
        rows.append(
            [InlineKeyboardButton(text="⬅️ Change position", callback_data=f"ord:list:{token}")]
        )
    else:
        available = view["available_seats"]
        lines.append("Choose a rider." if mode == "list" else "Choose who they should go before.")
        lines.append("Offline and guest seats remain reserved.")
        lines.append("✅ payment reported · 💵 cash on site · ▫️ not reported")
        if mode == "target":
            label = next(r["label"] for r in riders if r["user_id"] == source)
            lines.extend(["", f"Moving: {label}"])
        lines.append("")
        options = [(i, r) for i, r in enumerate(riders) if mode == "list" or r["user_id"] != source]
        for index, rider in options[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
            if index == available:
                lines.append("⏳ Waitlist")
            status = "🎟" if index < available else "⏳"
            payment = payment_label(rider)
            lines.append(f"{status} {index + 1}. {rider['label']} · {payment}")
            action = "rider" if mode == "list" else "before"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{('💵' if rider['cash'] else '✅') if rider['paid'] else '▫️'} "
                        f"{index + 1}. {rider['label']}"[:80],
                        callback_data=f"ord:{action}:{token}:{rider['user_id']}",
                    )
                ]
            )
        navigation = []
        if page:
            navigation.append(
                InlineKeyboardButton(
                    text="← Previous", callback_data=f"ord:page:{token}:{page - 1}"
                )
            )
        if len(options) > (page + 1) * PAGE_SIZE:
            navigation.append(
                InlineKeyboardButton(text="Next →", callback_data=f"ord:page:{token}:{page + 1}")
            )
        if navigation:
            rows.append(navigation)
        if mode == "target":
            position = view["previous_positions"].get(str(source))
            if position is not None:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"↩️ Restore previous position · {position}",
                            callback_data=f"ord:restore:{token}",
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Move to the end", callback_data=f"ord:before:{token}:0"
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⬅️ Choose another rider", callback_data=f"ord:list:{token}"
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="⬅️ Lift", callback_data=back)])
    return BookingMonitorDraft(
        text="\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
