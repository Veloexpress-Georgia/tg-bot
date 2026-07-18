from datetime import date, datetime
from zoneinfo import ZoneInfo

from veloexpress_bot.polls.extraday import ExtraDayDraftState, render_extra_day_card
from veloexpress_bot.polls.weekendplan import (
    DayPlanStatus,
    WeekendPlanView,
    render_weekend_plan_card,
)

TBILISI = ZoneInfo("Asia/Tbilisi")
WEEK = date(2026, 7, 25)


def plan_view(**overrides: object) -> WeekendPlanView:
    defaults: dict[str, object] = {
        "week_start": WEEK,
        "days": (
            DayPlanStatus(service_date=WEEK, enabled=True, posted=False),
            DayPlanStatus(service_date=date(2026, 7, 26), enabled=True, posted=False),
        ),
        "cancelled_lift_times": ("15:30",),
        "opens_at": datetime(2026, 7, 24, 14, 0, tzinfo=TBILISI),
        "auto_enabled": True,
        "skipped": False,
    }
    defaults.update(overrides)
    return WeekendPlanView(**defaults)  # type: ignore[arg-type]


def button_map(markup) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {button.callback_data: button.text for row in markup.inline_keyboard for button in row}


def test_plan_card_shows_lifts_and_auto_open_moment() -> None:
    card = render_weekend_plan_card(plan_view())

    assert "📋 Weekend plan · Sat 25 Jul + Sun 26" in card.text
    assert "🚲 Lifts: 8:30 → 13:30 · 4 lifts" in card.text
    assert "🕓 Opens: Fri, 24 Jul · 14:00 (auto)" in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["plan:day:sat"] == "✅ Sat 25"
    assert buttons["plan:day:sun"] == "✅ Sun 26"
    assert buttons["plan:post"] == "🚀 Post now"
    assert buttons["plan:skip"] == "⏭ Skip weekend"


def test_plan_card_marks_posted_days_and_offers_recreate() -> None:
    card = render_weekend_plan_card(
        plan_view(
            days=(
                DayPlanStatus(service_date=WEEK, enabled=True, posted=True),
                DayPlanStatus(service_date=date(2026, 7, 26), enabled=True, posted=True),
            ),
            opens_at=None,
        )
    )

    assert "✅ Polls are posted." in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["plan:posted"].startswith("📌")
    assert "plan:post" not in buttons
    assert buttons["plan:view:recreate"] == "♻️ Recreate polls"
    assert "plan:skip" not in buttons


def test_plan_card_partially_posted_keeps_post_now() -> None:
    card = render_weekend_plan_card(
        plan_view(
            days=(
                DayPlanStatus(service_date=WEEK, enabled=True, posted=True),
                DayPlanStatus(service_date=date(2026, 7, 26), enabled=True, posted=False),
            ),
        )
    )

    assert "✅ Sat 25: posted" in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["plan:post"] == "🚀 Post now"
    assert "plan:view:recreate" not in buttons


def test_plan_card_skipped_weekend_shows_unskip() -> None:
    card = render_weekend_plan_card(plan_view(skipped=True))

    assert "⏭ This weekend is skipped" in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["plan:skip"] == "↩️ Unskip weekend"


def test_plan_card_auto_off_hint() -> None:
    card = render_weekend_plan_card(plan_view(opens_at=None, auto_enabled=False))

    assert "Auto-posting is off" in card.text


def test_plan_card_recreate_view_requires_confirmation() -> None:
    card = render_weekend_plan_card(plan_view(), view="recreate")

    assert "Recreating deletes them" in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["plan:recreate"] == "♻️ Confirm recreate"
    assert buttons["plan:view:main"] == "⬅️ Back"


def test_plan_card_range_picker_marks_selection() -> None:
    card = render_weekend_plan_card(plan_view(), view="first")

    buttons = button_map(card.reply_markup)
    assert buttons["plan:first:8:30"] == "✅ 8:30"
    assert buttons["plan:first:15:30"] == "15:30"


def test_extra_day_card_date_picker_lists_week() -> None:
    card = render_extra_day_card(
        ExtraDayDraftState(selected_date=None, cancelled_lift_times=("15:30",)),
        view="date",
        today=date(2026, 7, 20),
    )

    buttons = button_map(card.reply_markup)
    assert buttons["extra:date:2026-07-20"] == "Mon 20 Jul"
    assert buttons["extra:date:2026-07-26"] == "Sun 26 Jul"
    assert len([key for key in buttons if key.startswith("extra:date:")]) == 7


def test_extra_day_card_main_view_offers_post() -> None:
    card = render_extra_day_card(
        ExtraDayDraftState(
            selected_date=date(2026, 7, 22),
            cancelled_lift_times=("15:30",),
        ),
        view="main",
        today=date(2026, 7, 20),
    )

    assert "➕ Extra lift day · Wed 22 Jul" in card.text
    assert "🚲 Lifts: 8:30 → 13:30 · 4 lifts" in card.text
    buttons = button_map(card.reply_markup)
    assert buttons["extra:post"] == "🚀 Post polls"
    assert buttons["extra:view:date"].startswith("📅 Wed 22 Jul")
