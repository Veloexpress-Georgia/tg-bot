from datetime import date

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    LiftRider,
    decode_monitor_date,
    decode_monitor_time,
    render_booking_monitor,
    render_lift_detail,
)


def _button_map(markup) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {button.callback_data: button.text for row in markup.inline_keyboard for button in row}


def test_booking_monitor_renders_day_tabs_counts_and_controls() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(
                    BookingLiftStatus(time="8:30", vote_count=6, manual_count=1),
                    BookingLiftStatus(time="10:00", vote_count=9, manual_count=0),
                ),
                paid_rider_count=3,
                booked_rider_count=9,
                expected_gel=60,
            ),
            BookingMonitorDay(
                service_date=date(2026, 7, 19),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=2),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "📊 Booking monitor · Sat, 18 Jul" in draft.text
    assert "Running 2 of 2 · 16 seats · paid 3/9 · 60 GEL in" in draft.text
    assert "8:30 — 7/10 · 3 left · 1 manual" in draft.text
    assert "10:00 — 9/10 · 1 left" in draft.text
    assert draft.reply_markup is not None
    assert [button.text for button in draft.reply_markup.inline_keyboard[0]] == [
        "✅ Sat 18",
        "Sun 19",
    ]
    assert [button.callback_data for button in draft.reply_markup.inline_keyboard[1]] == [
        "mon:sub:20260718:0830",
        "mon:info:20260718:0830",
        "mon:add:20260718:0830",
    ]


def test_booking_monitor_falls_back_to_first_day_and_shows_waitlist() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 19),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=2),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "Sun, 19 Jul" in draft.text
    assert "10:00 — 12/10 · waitlist +2 · 2 manual" in draft.text


def test_booking_monitor_shows_cancelled_lift_and_cancel_day_control() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(
                    BookingLiftStatus(time="8:30", vote_count=6, manual_count=0),
                    BookingLiftStatus(time="10:00", vote_count=0, manual_count=0, cancelled=True),
                ),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "8:30 — 6/10 · 4 left" in draft.text
    assert "10:00 — ❌ cancelled" in draft.text
    assert draft.reply_markup is not None
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:info:20260718:1000"] == "❌ 10:00 · cancelled"
    assert buttons["mon:cancelday:20260718"].startswith("🚫 Cancel all")


def test_render_lift_detail_toggles_cancel_and_restore() -> None:
    active = render_lift_detail(
        service_date=date(2026, 7, 18),
        lift=BookingLiftStatus(time="8:30", vote_count=2, manual_count=1),
        riders=(
            LiftRider(telegram_user_id=10, label="@stas", paid=True),
            LiftRider(telegram_user_id=11, label="Anna"),
        ),
    )
    assert "🚲 8:30 · Sat, 18 Jul" in active.text
    assert "Total: 3/10" in active.text
    assert "Paid: 1/2" in active.text
    assert "@stas, Anna" in active.text
    active_buttons = _button_map(active.reply_markup)
    assert active_buttons["mon:cancel:20260718:0830"] == "🚫 Cancel lift"
    assert active_buttons["mon:back:20260718"] == "⬅️ Back"
    # Tapping a rider records a payment taken outside Telegram.
    assert active_buttons["mon:paid:20260718:10"] == "✓ @stas"
    assert active_buttons["mon:paid:20260718:11"] == "Anna"

    cancelled = render_lift_detail(
        service_date=date(2026, 7, 18),
        lift=BookingLiftStatus(time="8:30", vote_count=2, manual_count=1, cancelled=True),
        riders=(LiftRider(telegram_user_id=10, label="@stas"),),
    )
    assert "❌ Cancelled." in cancelled.text
    cancelled_buttons = _button_map(cancelled.reply_markup)
    assert cancelled_buttons["mon:restore:20260718:0830"] == "♻️ Restore lift"


def test_booking_monitor_empty_state_and_callback_decoding() -> None:
    draft = render_booking_monitor((), selected_service_date=None)

    assert draft.text == "📊 Booking monitor\n\nNo active lift polls."
    assert draft.reply_markup is None
    assert decode_monitor_date("20260718") == date(2026, 7, 18)
    assert decode_monitor_time("0830") == "8:30"
    assert decode_monitor_time("1000") == "10:00"
