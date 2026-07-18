from datetime import date

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    decode_monitor_date,
    decode_monitor_time,
    render_booking_monitor,
)


def test_booking_monitor_renders_day_tabs_counts_and_controls() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(
                    BookingLiftStatus(time="8:30", vote_count=6, manual_count=1),
                    BookingLiftStatus(time="10:00", vote_count=9, manual_count=0),
                ),
            ),
            BookingMonitorDay(
                service_date=date(2026, 7, 19),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=2),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "📊 Booking monitor · Sat, 18 Jul" in draft.text
    assert "🟢 8:30 — 7/10 · 1 manual" in draft.text
    assert "🟡 10:00 — 9/10" in draft.text
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
    assert "🔴 10:00 — 12/10 · 2 manual · waitlist +2" in draft.text


def test_booking_monitor_empty_state_and_callback_decoding() -> None:
    draft = render_booking_monitor((), selected_service_date=None)

    assert draft.text == "📊 Booking monitor\n\nNo active lift polls."
    assert draft.reply_markup is None
    assert decode_monitor_date("20260718") == date(2026, 7, 18)
    assert decode_monitor_time("0830") == "8:30"
    assert decode_monitor_time("1000") == "10:00"
