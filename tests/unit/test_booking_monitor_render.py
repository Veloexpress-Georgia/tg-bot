from datetime import UTC, date, datetime

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    LiftRider,
    MonitorGuest,
    MonitorLateExit,
    MonitorRider,
    MonitorWaitlistRider,
    decode_monitor_date,
    decode_monitor_time,
    render_all_riders,
    render_booking_management,
    render_booking_monitor,
    render_bumped_report,
    render_cancel_day_confirmation,
    render_cancel_lift_confirmation,
    render_lift_detail,
    render_start_status,
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
                owed_gel=240,
            ),
            BookingMonitorDay(
                service_date=date(2026, 7, 19),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=2),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "📊 Booking monitor · Sat, 18 Jul" in draft.text
    # Paid of owed, never a bare total: on its own the money figure sat next to a
    # seat count built from different rules and read as a contradiction.
    assert "Running 2 of 2 · 16 seats · claimed 3/9 · 60 of 240 GEL" in draft.text
    # Seats, not arithmetic: "3 left" is something the admin can read off 7/10.
    assert "🚐 8:30 · 7/10 · 1 manual" in draft.text
    assert "🚐 10:00 · 9/10" in draft.text
    assert draft.reply_markup is not None
    assert [button.text for button in draft.reply_markup.inline_keyboard[0]] == [
        "✅ Sat 18",
        "Sun 19",
    ]
    assert [button.callback_data for button in draft.reply_markup.inline_keyboard[1]] == [
        "mon:all:20260718"
    ]
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:all:20260718"] == "👥 All riders"
    assert buttons["mon:manage:20260718"] == "⚙️ Manage bookings"
    assert not any(data.startswith(("mon:add:", "mon:sub:")) for data in buttons)
    # One button per lift on top of "All riders" was the same list twice.
    assert not any(data.startswith("mon:info:") for data in buttons)


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
    assert "🚐 10:00 · 12/10 · waitlist +2 · 2 manual" in draft.text


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

    # 6 riders is above the minimum, so 8:30 is happening; 10:00 is named, not listed.
    assert "🚐 8:30 · 6/10" in draft.text
    assert "❌ Cancelled: 10:00" in draft.text
    assert draft.reply_markup is not None
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:manage:20260718"] == "⚙️ Manage bookings"

    management = render_booking_management(
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
    management_buttons = _button_map(management.reply_markup)
    assert management_buttons["mon:managelift:20260718:0830"] == "8:30 · 6/10"
    assert management_buttons["mon:managelift:20260718:1000"] == "❌ 10:00 · 0/10"
    assert not any(
        data.startswith(("mon:add:", "mon:sub:", "mon:restore:")) for data in management_buttons
    )
    assert management_buttons["mon:cancelday:20260718"].startswith("🚫 Cancel all")
    assert management_buttons["mon:back:20260718"] == "⬅️ Back to monitor"


def test_booking_monitor_puts_operational_attention_on_the_top_level() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 8, 22),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=0),),
                expected_gel=105,
                owed_gel=150,
                unpaid_riders=(MonitorRider(1, "@anna", amount_gel=15),),
                cash_pending=(MonitorRider(2, "@vitaly", amount_gel=30),),
                guests=(MonitorGuest(2, "@vitaly", "10:00", 1),),
                waitlist=(MonitorWaitlistRider(3, "@giorgi", "10:00", 1),),
                late_exits=(
                    MonitorLateExit(
                        4,
                        "@stas",
                        "10:00",
                        datetime(2026, 8, 21, 21, 14, tzinfo=UTC),
                    ),
                ),
            ),
        ),
        selected_service_date=date(2026, 8, 22),
    )

    assert "⚠️ Needs attention · 4" in draft.text
    assert "🔴 Unpaid: @anna 15 GEL" in draft.text
    assert "💵 Cash to collect: @vitaly 30 GEL" in draft.text
    assert "⏳ Waitlist: @giorgi 10:00 #1" in draft.text
    assert "⏰ Left after deadline: @stas 10:00 · 21:14" in draft.text
    assert "👥 Guests: @vitaly +1 (10:00)" in draft.text


def test_all_riders_shows_every_lift_in_one_read_only_card() -> None:
    saturday = BookingMonitorDay(
        service_date=date(2026, 8, 22),
        lifts=(
            BookingLiftStatus(time="8:30", vote_count=2, manual_count=0),
            BookingLiftStatus(time="10:00", vote_count=2, manual_count=0),
        ),
    )
    draft = render_all_riders(
        (saturday,),
        selected_service_date=saturday.service_date,
        rosters=(
            (
                saturday.lifts[0],
                (
                    LiftRider(1, "@anna", paid=True),
                    LiftRider(2, "@nika"),
                ),
            ),
            (
                saturday.lifts[1],
                (
                    LiftRider(3, "@vitaly", paid=True, cash=True, guests=1),
                    LiftRider(4, "@giorgi", waitlisted=True),
                ),
            ),
        ),
    )

    assert "8:30 — 2/10" in draft.text
    assert "✅ @anna" in draft.text
    assert "🔴 @nika" in draft.text
    assert "10:00 — 2/10" in draft.text
    assert "💵 @vitaly · +1 guest" in draft.text
    assert "⏳ @giorgi" in draft.text
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:back:20260822"] == "⬅️ Back to monitor"
    assert not any(data.startswith("mon:paid:") for data in buttons)


def test_destructive_cancellations_require_a_second_explicit_callback() -> None:
    service_date = date(2026, 8, 22)
    lift = BookingLiftStatus(
        time="10:00",
        vote_count=7,
        manual_count=1,
        guest_count=2,
    )
    lift_confirmation = render_cancel_lift_confirmation(
        service_date=service_date,
        lift=lift,
        riders=(
            LiftRider(1, "@anna", paid=True),
            LiftRider(2, "@nika"),
        ),
    )
    assert "⚠️ Cancel 10:00" in lift_confirmation.text
    assert "10 seats · 2 Telegram riders" in lift_confirmation.text
    lift_buttons = _button_map(lift_confirmation.reply_markup)
    assert lift_buttons["mon:docancel:20260822:1000"] == "🚫 Confirm cancel 10:00"
    assert lift_buttons["mon:managelift:20260822:1000"] == "Keep lift"

    day_confirmation = render_cancel_day_confirmation(
        BookingMonitorDay(
            service_date=service_date,
            lifts=(lift,),
            paid_rider_count=1,
            expected_gel=30,
        )
    )
    assert "⚠️ Cancel all" in day_confirmation.text
    day_buttons = _button_map(day_confirmation.reply_markup)
    assert day_buttons["mon:docancelday:20260822"] == "🚫 Confirm cancel whole day"
    assert day_buttons["mon:manage:20260822"] == "Keep day"


def test_render_lift_detail_reads_by_default_and_acts_only_under_manage() -> None:
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
    assert "✅ @stas" in active.text
    assert "🔴 Anna" in active.text
    active_buttons = _button_map(active.reply_markup)
    assert active_buttons["mon:back:20260718"] == "⬅️ Back"
    # Who paid is worth reading; recording it for them is not the bot's job.
    assert not any(
        data.startswith(("mon:paid:", "mon:cashreceived:", "mon:liftmoney:"))
        for data in active_buttons
    )

    management = render_lift_detail(
        service_date=date(2026, 7, 18),
        lift=BookingLiftStatus(time="8:30", vote_count=2, manual_count=1),
        riders=(),
        mode="manage",
    )
    management_buttons = _button_map(management.reply_markup)
    assert management_buttons["mon:add:20260718:0830"] == "➕ Manual"
    assert management_buttons["mon:sub:20260718:0830"] == "➖ Manual"
    assert management_buttons["mon:cancel:20260718:0830"] == "🚫 Cancel lift"
    assert management_buttons["mon:manage:20260718"] == "⬅️ Back"


def test_start_status_answers_instead_of_greeting() -> None:
    text = render_start_status(
        (
            BookingMonitorDay(
                service_date=date(2026, 8, 1),
                lifts=(
                    BookingLiftStatus(time="8:30", vote_count=6, manual_count=0),
                    BookingLiftStatus(time="10:00", vote_count=2, manual_count=0),
                ),
                paid_rider_count=3,
                booked_rider_count=8,
            ),
            BookingMonitorDay(
                service_date=date(2026, 8, 2),
                lifts=(BookingLiftStatus(time="8:30", vote_count=2, manual_count=0),),
            ),
        ),
        schedule_line="⏰ Next polls open Fri 14:00.",
    )

    assert text.splitlines() == [
        "🚐 Veloexpress",
        "",
        "Sat, 1 Aug · 1 of 2 lifts running · paid 3/8",
        "Sun, 2 Aug · 2 booked, nothing running yet",
        "",
        "⏰ Next polls open Fri 14:00.",
    ]


def test_start_status_says_so_when_there_is_nothing_on() -> None:
    text = render_start_status((), schedule_line="⏰ Auto-posting is off.")

    assert "No active lift polls." in text
    assert "⏰ Auto-posting is off." in text


def test_booking_monitor_empty_state_and_callback_decoding() -> None:
    draft = render_booking_monitor((), selected_service_date=None)

    assert draft.text == "📊 Booking monitor\n\nNo active lift polls."
    assert draft.reply_markup is None
    assert decode_monitor_date("20260718") == date(2026, 7, 18)
    assert decode_monitor_time("0830") == "8:30"
    assert decode_monitor_time("1000") == "10:00"


def test_guests_are_counted_like_any_other_seat() -> None:
    """The public board counted them and the monitor did not, so the two disagreed."""
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(
                    BookingLiftStatus(time="8:30", vote_count=7, manual_count=1, guest_count=2),
                ),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    # Guests are in the seat count and named in their own section; repeating them
    # on the lift line was the same fact three times.
    assert "🚐 8:30 · 10/10 · full · 1 manual" in draft.text


def test_a_day_that_already_ran_cannot_be_cancelled() -> None:
    """Refunding a trip people took is not a cancellation, it is a mistake."""
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(BookingLiftStatus(time="8:30", vote_count=7, manual_count=0),),
                past=True,
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert draft.reply_markup is not None
    assert "cancelday" not in str(_button_map(draft.reply_markup))
    # Still readable, and payments can still be marked: that is why it is here.
    assert "8:30" in draft.text


def test_past_refunds_are_reachable_once_any_exist() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(BookingLiftStatus(time="8:30", vote_count=7, manual_count=0),),
                has_refund_reports=True,
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert draft.reply_markup is not None
    assert _button_map(draft.reply_markup)["mon:refunds"] == "🧾 Past refunds"


def test_bumped_report_separates_the_refund_from_the_rest() -> None:
    text = render_bumped_report(
        service_date=date(2026, 8, 23),
        lift_time="11:45",
        riders=(("@egor", 11, True), ("Ivan", 12, False)),
        price_gel=15,
    )

    assert text.startswith("⚠️ 11:45 · Sun, 23 Aug — manual seat added.")
    assert 'tg://user?id=11">@egor</a> — paid, refund 15 GEL' in text
    assert 'tg://user?id=12">Ivan</a> — nothing paid' in text
    assert text.endswith("Refund 15 GEL.")


def test_bumped_report_says_when_no_money_is_owed() -> None:
    text = render_bumped_report(
        service_date=date(2026, 8, 23),
        lift_time="11:45",
        riders=(("Ivan", 12, False),),
        price_gel=15,
    )

    assert text.endswith("Nobody paid for those seats.")


def test_a_host_with_the_same_guest_all_day_is_named_once() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 8, 23),
                lifts=(
                    BookingLiftStatus(time="10:00", vote_count=8, manual_count=0, guest_count=1),
                ),
                guests=(
                    MonitorGuest(host_user_id=8, host_label="@vitaly", lift_time="10:00", count=1),
                    MonitorGuest(host_user_id=8, host_label="@vitaly", lift_time="11:45", count=1),
                ),
            ),
        ),
        selected_service_date=date(2026, 8, 23),
    )

    assert "👥 Guests: @vitaly +1 (10:00, 11:45)" in draft.text
