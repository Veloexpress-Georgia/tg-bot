from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from veloexpress_bot.polls.autoschedule import (
    AutoScheduleState,
    creation_moment,
    decide_tick,
    decode_card_time,
    encode_card_time,
    next_announce_lead,
    next_pending_creation,
    render_schedule_announcement,
    render_schedule_card,
    skip_target_week,
    upcoming_service_week_start,
)

TBILISI = ZoneInfo("Asia/Tbilisi")
WEEK = date(2026, 7, 25)  # Saturday


def enabled_state(**overrides: object) -> AutoScheduleState:
    defaults: dict[str, object] = {
        "enabled": True,
        "creation_weekday": 4,
        "creation_time": "14:00",
        "announce_lead_minutes": 120,
    }
    defaults.update(overrides)
    return AutoScheduleState(**defaults)  # type: ignore[arg-type]


def at(day: int, hour: int, minute: int = 0, *, month: int = 7) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TBILISI)


def test_upcoming_service_week_start_returns_next_saturday() -> None:
    assert upcoming_service_week_start(date(2026, 7, 20)) == WEEK  # Monday
    assert upcoming_service_week_start(date(2026, 7, 24)) == WEEK  # Friday
    assert upcoming_service_week_start(WEEK) == WEEK  # Saturday itself
    assert upcoming_service_week_start(date(2026, 7, 26)) == date(2026, 8, 1)  # Sunday


def test_creation_moment_lands_on_configured_weekday_before_weekend() -> None:
    friday = creation_moment(WEEK, weekday=4, creation_time="14:00", zone=TBILISI)
    assert friday == at(24, 14)

    monday = creation_moment(WEEK, weekday=0, creation_time="09:00", zone=TBILISI)
    assert monday == at(20, 9)

    saturday = creation_moment(WEEK, weekday=5, creation_time="10:00", zone=TBILISI)
    assert saturday == at(25, 10)


def test_creation_moment_rejects_sunday() -> None:
    with pytest.raises(ValueError, match="weekday"):
        creation_moment(WEEK, weekday=6, creation_time="14:00", zone=TBILISI)


def test_decide_tick_walks_announce_then_create_lifecycle() -> None:
    state = enabled_state()

    assert decide_tick(state, at(23, 18)) is None  # Thursday evening
    assert decide_tick(state, at(24, 11, 59)) is None  # before announce window

    announce = decide_tick(state, at(24, 12, 30))
    assert announce is not None
    assert announce.kind == "announce"
    assert announce.service_week_start == WEEK
    assert announce.creation_at == at(24, 14)

    announced = enabled_state(last_announced_week_start=WEEK)
    assert decide_tick(announced, at(24, 13)) is None

    create = decide_tick(announced, at(24, 14, 0))
    assert create is not None
    assert create.kind == "create"

    created = enabled_state(last_announced_week_start=WEEK, last_created_week_start=WEEK)
    assert decide_tick(created, at(24, 15)) is None


def test_decide_tick_is_silent_when_disabled_or_announce_off() -> None:
    assert decide_tick(enabled_state(enabled=False), at(24, 14, 30)) is None
    assert decide_tick(enabled_state(announce_lead_minutes=0), at(24, 13)) is None

    create = decide_tick(enabled_state(announce_lead_minutes=0), at(24, 14))
    assert create is not None
    assert create.kind == "create"


def test_decide_tick_marks_skipped_week_without_announcing() -> None:
    state = enabled_state(skip_week_start=WEEK)

    assert decide_tick(state, at(24, 13)) is None

    action = decide_tick(state, at(24, 14, 5))
    assert action is not None
    assert action.kind == "mark_skipped"

    done = enabled_state(skip_week_start=WEEK, last_created_week_start=WEEK)
    assert decide_tick(done, at(24, 16)) is None


def test_next_pending_creation_skips_created_and_skipped_weeks() -> None:
    pending = next_pending_creation(enabled_state(), at(20, 10), zone=TBILISI)
    assert pending == (WEEK, at(24, 14))

    created = enabled_state(last_created_week_start=WEEK)
    pending = next_pending_creation(created, at(24, 15), zone=TBILISI)
    assert pending == (date(2026, 8, 1), at(31, 14))

    skipped = enabled_state(skip_week_start=WEEK)
    pending = next_pending_creation(skipped, at(20, 10), zone=TBILISI)
    assert pending is not None
    assert pending[0] == date(2026, 8, 1)


def test_skip_target_week_moves_forward_once_creation_passed() -> None:
    state = enabled_state()
    assert skip_target_week(state, at(24, 13), zone=TBILISI) == WEEK
    assert skip_target_week(state, at(24, 14, 5), zone=TBILISI) == date(2026, 8, 1)

    created = enabled_state(last_created_week_start=WEEK)
    assert skip_target_week(created, at(24, 15), zone=TBILISI) == date(2026, 8, 1)


def test_a_paused_schedule_has_nothing_to_skip() -> None:
    paused = replace(enabled_state(), enabled=False)

    assert skip_target_week(paused, at(24, 13), zone=TBILISI) is None


def test_next_announce_lead_cycles_choices() -> None:
    assert next_announce_lead(0) == 60
    assert next_announce_lead(60) == 120
    assert next_announce_lead(120) == 180
    assert next_announce_lead(180) == 0
    assert next_announce_lead(45) == 0


def test_card_time_encoding_round_trips() -> None:
    assert encode_card_time("14:00") == "1400"
    assert encode_card_time("9:30") == "0930"
    assert decode_card_time("1400") == "14:00"
    assert decode_card_time("0930") == "09:30"


def test_render_schedule_announcement_mentions_weekend_and_time() -> None:
    text = render_schedule_announcement(
        WEEK,
        creation_at=at(24, 14),
        announced_at=at(24, 12),
    )
    assert text == "📣 Lift polls for Sat 25 Jul + Sun 26 open today at 14:00. Get ready to vote!"

    early = render_schedule_announcement(
        WEEK,
        creation_at=at(24, 14),
        announced_at=at(23, 23, 30),
    )
    assert "on Friday at 14:00" in early


def test_render_schedule_card_shows_state_and_next_run() -> None:
    card = render_schedule_card(
        enabled_state(),
        timezone_label="Asia/Tbilisi",
        now=at(20, 10),
        zone=TBILISI,
        planned_lifts_label="8:30 → 13:30 · 4 lifts",
    )
    assert "✅ polls are created automatically" in card.text
    assert "Friday · 14:00 (Asia/Tbilisi)" in card.text
    assert "2h before" in card.text
    assert "Fri, 24 Jul · 14:00" in card.text
    assert "Sat 25 Jul + Sun 26" in card.text
    assert "🚲 Lifts: 8:30 → 13:30 · 4 lifts" in card.text
    assert "Create polls manually before the run" in card.text
    button_texts = [button.text for row in card.reply_markup.inline_keyboard for button in row]
    assert any("Skip Sat 25 Jul" in text for text in button_texts)
    assert "⏸ Pause" in button_texts


def test_render_schedule_card_paused_hides_next_run() -> None:
    card = render_schedule_card(
        enabled_state(enabled=False),
        timezone_label="Asia/Tbilisi",
        now=at(20, 10),
        zone=TBILISI,
    )
    assert "⏸ paused" in card.text
    assert "🚀 Next:" not in card.text
    button_texts = [button.text for row in card.reply_markup.inline_keyboard for button in row]
    assert "▶️ Enable" in button_texts
    # Skip suppresses one automatic run, so a paused card must not offer it.
    assert not any("Skip" in text for text in button_texts)


def test_render_schedule_card_pickers_mark_selection() -> None:
    day_card = render_schedule_card(
        enabled_state(),
        view="day",
        timezone_label="Asia/Tbilisi",
        now=at(20, 10),
        zone=TBILISI,
    )
    day_buttons = [button.text for row in day_card.reply_markup.inline_keyboard for button in row]
    assert "✅ Fri" in day_buttons

    time_card = render_schedule_card(
        enabled_state(),
        view="time",
        timezone_label="Asia/Tbilisi",
        now=at(20, 10),
        zone=TBILISI,
    )
    time_buttons = [button.text for row in time_card.reply_markup.inline_keyboard for button in row]
    assert "✅ 14:00" in time_buttons
