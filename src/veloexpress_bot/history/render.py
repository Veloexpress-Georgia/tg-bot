import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from veloexpress_bot.history.service import Statistics
from veloexpress_bot.payments.myday import MyDayDraft

PAGE_SIZE = 8


def render_statistics(
    view: Statistics, *, personal: bool, page: int = 0, admin_user_id: int | None = None
) -> MyDayDraft:
    prefix = "rstats" if personal else "stats"
    period = {"30d": "Last 30 days", "year": "This year", "all": "All recorded history"}[
        view.period
    ]
    lines = ["📊 My statistics" if personal else "📊 Lift statistics", period, ""]
    if personal:
        rider = view.riders[0] if view.riders else None
        lines.append(f"{rider.days if rider else 0} days · {rider.lifts if rider else 0} lifts")
        lines.append(f"{rider.guests if rider else 0} guest seats")
        if rider and rider.last_day:
            lines.append(f"Last recorded day: {rider.last_day:%d %b %Y}")
    else:
        lines.extend(
            (
                f"{view.days} days · {view.lifts} lifts",
                f"{sum(r.days > 0 for r in view.riders)} riders · "
                f"{sum(r.days for r in view.riders)} rider-days",
                f"{view.seats} seats · {view.guests} guest · {view.manual} manual",
                f"Occupancy: {view.seats / view.capacity:.0%}"
                if view.capacity
                else "Occupancy: no recorded lifts",
            )
        )
    lines.extend(
        (
            "",
            f"Reported: {view.received_gel} GEL",
            f"Reversed reports: {view.reversed_gel} GEL",
            f"Reported total: {view.net_gel} GEL",
        )
    )
    if view.first_record:
        lines.extend(
            (
                "",
                f"Recorded results from {view.first_record:%d %b %Y} · through {view.end:%d %b %Y}",
            )
        )
    else:
        lines.extend(("", "No recorded rides in this period."))
    if view.backfilled_days:
        lines.append(f"⚠️ {view.backfilled_days} days reconstructed later.")
    lines.append("Based on seats at day close; attendance is not checked.")
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=(
                    f"astats:{key}:{admin_user_id}:{page}"
                    if admin_user_id is not None
                    else f"{prefix}:{key}:0"
                ),
            )
            for key, label in (("30d", "30 days"), ("year", "Year"), ("all", "All"))
        ]
    ]
    if not personal and view.riders:
        pages = (len(view.riders) - 1) // PAGE_SIZE + 1
        page = min(max(page, 0), pages - 1)
        lines.extend(("", f"Riders · {page + 1}/{pages}"))
        for rider in view.riders[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
            label = html.escape(rider.label)
            lines.append(f"{label} — {rider.days} days · {rider.lifts} lifts · {rider.net_gel} GEL")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=rider.label[:40] or f"Rider {rider.user_id}",
                        callback_data=f"astats:{view.period}:{rider.user_id}:{page}",
                    )
                ]
            )
        pager = []
        if page:
            pager.append(
                InlineKeyboardButton(
                    text="← Previous", callback_data=f"stats:{view.period}:{page - 1}"
                )
            )
        if page + 1 < pages:
            pager.append(
                InlineKeyboardButton(text="Next →", callback_data=f"stats:{view.period}:{page + 1}")
            )
        if pager:
            rows.append(pager)
    if admin_user_id is not None:
        label = view.riders[0].label if view.riders else f"Rider {admin_user_id}"
        lines[0] = f"📊 {html.escape(label)}"
        rows.append(
            [InlineKeyboardButton(text="⬅️ Riders", callback_data=f"stats:{view.period}:{page}")]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📜 Days", callback_data="guest:season" if personal else "mon:history"
                )
            ]
        )
    return MyDayDraft(
        text="\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
