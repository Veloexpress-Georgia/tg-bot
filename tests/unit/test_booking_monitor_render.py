from datetime import UTC, date, datetime

from veloexpress_bot.bookings.render import (
    BookingLiftStatus,
    BookingMonitorDay,
    LiftDayAudit,
    LiftDayAuditLift,
    LiftDayAuditSeat,
    LiftHistory,
    LiftHistoryDay,
    LiftRider,
    LiftTrend,
    MonitorGuest,
    MonitorLateExit,
    MonitorRider,
    MonitorWaitlistRider,
    TrendRow,
    decode_monitor_date,
    decode_monitor_time,
    render_admin_menu,
    render_all_riders,
    render_booking_monitor,
    render_bumped_report,
    render_cancel_day_confirmation,
    render_cancel_lift_confirmation,
    render_lift_day_audit,
    render_lift_detail,
    render_lift_history,
    render_lift_trend,
    render_refund_reports,
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

    assert "📊 Sat, 18 Jul" in draft.text
    # Two short lines, not one with six figures in it. Reaching the rider minimum
    # and being paid for are different states and stay on different halves.
    assert "2 of 2 lifts running · 0 funded" in draft.text
    assert "Reported 60 of 240 GEL" in draft.text
    assert "🚐 8:30 · 7/10 seats · 0/5 paid · 1 manual" in draft.text
    assert "🚐 10:00 · 9/10 seats · 0/5 paid" in draft.text
    # The old header invited both of these readings, and both were wrong.
    assert "claimed" not in draft.text
    assert "Needs attention" not in draft.text
    assert draft.reply_markup is not None
    assert [button.text for button in draft.reply_markup.inline_keyboard[0]] == [
        "✅ Sat 18",
        "Sun 19",
    ]
    buttons = _button_map(draft.reply_markup)
    # Lifts open straight from the day; there is no card in between any more.
    assert buttons["mon:lift:20260718:0830"] == "8:30 · 7/10"
    assert buttons["mon:lift:20260718:1000"] == "10:00 · 9/10"
    assert buttons["mon:all:20260718"] == "👥 All riders"
    assert buttons["mon:cancelday:20260718"] == "🚫 Cancel day"
    assert buttons["mon:menu"] == "☰ Menu"
    assert not any(data.startswith(("mon:manage:", "mon:add:", "mon:sub:")) for data in buttons)


def test_booking_monitor_counts_a_waitlist_apart_from_seats() -> None:
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
    # Twelve bookings, ten seats. `12/10` read as twelve people in a ten-seat van.
    assert "🚐 10:00 · 10/10 seats · 2 waiting · 0/5 paid · 2 manual" in draft.text
    assert "12/10" not in draft.text


def test_booking_monitor_opens_a_cancelled_lift_too() -> None:
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
    assert "🚐 8:30 · 6/10 seats" in draft.text
    assert "❌ Cancelled: 10:00" in draft.text
    assert draft.reply_markup is not None
    buttons = _button_map(draft.reply_markup)
    # Restoring a cancelled lift means opening it, so it keeps its button.
    assert buttons["mon:lift:20260718:1000"] == "❌ 10:00 · 0/10"
    assert buttons["mon:cancelday:20260718"] == "🚫 Cancel day"


def test_a_day_with_nothing_running_says_so_instead_of_counting_to_zero() -> None:
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(BookingLiftStatus(time="8:30", vote_count=2, manual_count=0),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert "Nothing running yet · 2 seats booked" in draft.text
    assert "💤 Not filled: 8:30" in draft.text


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

    # No total above them: one rider in two categories used to be counted twice,
    # and a waitlist read as a fault.
    assert "Needs attention" not in draft.text
    assert "🔴 No payment reported: @anna 15 GEL" in draft.text
    # The cash has already changed hands and the ledger counts it as received.
    assert "💵 Reported in cash: @vitaly 30 GEL" in draft.text
    assert "Cash to collect" not in draft.text
    assert "⏳ Waitlist: @giorgi 10:00 #1" in draft.text
    assert "⏰ Left after deadline: @stas 10:00 · 21:14" in draft.text
    assert "👥 Guests: @vitaly +1 (10:00)" in draft.text


def test_a_long_handle_does_not_turn_a_category_into_a_paragraph() -> None:
    """Eight ordinary handles fit on a phone line; three long ones do not."""
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 8, 22),
                lifts=(BookingLiftStatus(time="10:00", vote_count=10, manual_count=0),),
                unpaid_riders=(
                    MonitorRider(1, "@a_really_long_telegram_handle_here", amount_gel=30),
                    MonitorRider(2, "@another_fairly_long_handle", amount_gel=15),
                    MonitorRider(3, "@nika", amount_gel=15),
                ),
            ),
        ),
        selected_service_date=date(2026, 8, 22),
    )

    line = next(line for line in draft.text.splitlines() if line.startswith("🔴"))
    assert "@a_really_long_telegram_handle_here 30 GEL" in line
    assert line.endswith("more")
    assert len(line) <= 80


def test_all_riders_shows_every_lift_in_one_read_only_card() -> None:
    saturday = BookingMonitorDay(
        service_date=date(2026, 8, 22),
        lifts=(
            BookingLiftStatus(time="8:30", vote_count=6, manual_count=0),
            BookingLiftStatus(time="10:00", vote_count=6, manual_count=0),
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

    assert "8:30 — 6/10" in draft.text
    assert "✅ @anna" in draft.text
    assert "🔴 @nika" in draft.text
    assert "10:00 — 6/10" in draft.text
    assert "💵 @vitaly · +1 guest" in draft.text
    assert "⏳ @giorgi" in draft.text
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:day:20260822"] == "⬅️ Back"
    assert not any(data.startswith("mon:paid:") for data in buttons)


def test_all_riders_drops_the_money_verdict_on_a_lift_that_will_not_leave() -> None:
    """A lift short of the minimum has no paid/unpaid answer worth printing.

    Day money is movable until the deadline, so marking a rider paid on a lift
    nobody is taking — and unpaid on the one they are — read as a contradiction.
    """
    saturday = BookingMonitorDay(
        service_date=date(2026, 8, 22),
        lifts=(BookingLiftStatus(time="8:30", vote_count=1, manual_count=0),),
    )
    draft = render_all_riders(
        (saturday,),
        selected_service_date=saturday.service_date,
        rosters=((saturday.lifts[0], (LiftRider(1, "@anna", paid=True),)),),
    )

    assert "8:30 — 1/10 · needs 4 more" in draft.text
    assert "💤 @anna" in draft.text
    assert "✅ @anna" not in draft.text


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
        refund_preview="Refund estimate: @anna 15 GEL",
    )
    assert "⚠️ Cancel 10:00" in lift_confirmation.text
    assert "10 seats · 2 riders from the poll" in lift_confirmation.text
    assert "1 added by hand · 2 guests" in lift_confirmation.text
    assert "1 of them reported a payment" in lift_confirmation.text
    # Read before deciding, so the estimate is on the screen rather than promised.
    assert "Refund estimate: @anna 15 GEL" in lift_confirmation.text
    lift_buttons = _button_map(lift_confirmation.reply_markup)
    assert lift_buttons["mon:docancel:20260822:1000"] == "🚫 Confirm cancel 10:00"
    assert lift_buttons["mon:lift:20260822:1000"] == "Keep lift"

    day_confirmation = render_cancel_day_confirmation(
        BookingMonitorDay(
            service_date=service_date,
            lifts=(lift,),
            paid_rider_count=1,
            expected_gel=30,
        )
    )
    assert "⚠️ Cancel the whole day" in day_confirmation.text
    assert "1 lift · 10 seats" in day_confirmation.text
    assert "1 rider reported 30 GEL" in day_confirmation.text
    day_buttons = _button_map(day_confirmation.reply_markup)
    assert day_buttons["mon:docancelday:20260822"] == "🚫 Confirm cancel whole day"
    assert day_buttons["mon:day:20260822"] == "Keep day"


def test_lift_card_reads_and_acts_on_one_screen() -> None:
    card = render_lift_detail(
        service_date=date(2026, 7, 18),
        lift=BookingLiftStatus(time="8:30", vote_count=5, manual_count=1),
        riders=(
            LiftRider(telegram_user_id=10, label="@stas", paid=True),
            LiftRider(telegram_user_id=11, label="Anna"),
        ),
    )
    assert "🚲 8:30 · Sat, 18 Jul" in card.text
    assert "6/10 seats · 0/5 paid" in card.text
    assert "5 from the poll · 1 added by hand" in card.text
    assert "Payment reported by 1 of 2" in card.text
    assert "✅ @stas" in card.text
    assert "🔴 Anna" in card.text
    buttons = _button_map(card.reply_markup)
    # Seeing who is on the lift and adding the rider next to you is one screen.
    assert buttons["mon:add:20260718:0830"] == "➕ Offline seat"
    assert buttons["mon:sub:20260718:0830"] == "➖ Offline seat"
    assert buttons["mon:cancel:20260718:0830"] == "🚫 Cancel lift"
    assert buttons["mon:day:20260718"] == "⬅️ Back"
    # Who paid is worth reading; recording it for them is not the bot's job.
    assert not any(
        data.startswith(("mon:paid:", "mon:cashreceived:", "mon:liftmoney:")) for data in buttons
    )


def test_a_cancelled_lift_offers_restore_instead_of_seats() -> None:
    card = render_lift_detail(
        service_date=date(2026, 7, 18),
        lift=BookingLiftStatus(time="8:30", vote_count=2, manual_count=1, cancelled=True),
        riders=(),
    )
    buttons = _button_map(card.reply_markup)
    assert "❌ Cancelled." in card.text
    assert buttons["mon:restore:20260718:0830"] == "♻️ Restore lift"
    assert not any(data.startswith(("mon:add:", "mon:sub:")) for data in buttons)


def test_menu_opens_with_what_is_live_and_the_three_places_to_go() -> None:
    draft = render_admin_menu(
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

    assert draft.text.splitlines() == [
        "☰ Veloexpress admin",
        "",
        "Sat, 1 Aug · 1 of 2 running · 0 funded",
        "Sun, 2 Aug · 2 seats booked, nothing running yet",
        "⏰ Next polls open Fri 14:00.",
    ]
    buttons = _button_map(draft.reply_markup)
    assert buttons["menu:weekend_plan"] == "📋 Weekend"
    assert buttons["menu:extra_day"] == "➕ Extra lift day"
    assert buttons["mon:history"] == "📜 History"
    assert buttons["stats:30d:0"] == "📈 Statistics"
    assert buttons["menu:service_defaults"] == "⚙️ Settings"
    assert buttons["mon:refunds"] == "🧾 Refunds"
    # Back to the day, named rather than called "back": it is a destination.
    assert buttons["mon:day:20260801"] == "📊 Sat 1"


def test_menu_says_so_when_there_is_nothing_on() -> None:
    draft = render_admin_menu((), schedule_line="⏰ Auto-posting is off.")

    assert "No lift polls are open." in draft.text
    assert "⏰ Auto-posting is off." in draft.text
    # No day to go back to, but planning and the past are still one tap away.
    buttons = _button_map(draft.reply_markup)
    assert "menu:weekend_plan" in buttons
    assert "mon:history" in buttons
    assert not any(data.startswith("mon:day:") for data in buttons)


def test_a_quiet_week_turns_the_monitor_into_the_way_in() -> None:
    """Midweek there is nothing to monitor. Left as a bare "no polls" line the card
    was a dead end with no buttons — /start was the only way out of it."""
    draft = render_booking_monitor(
        (),
        selected_service_date=None,
        schedule_line="⏰ Next polls open Thu 18:00.",
        history=LiftHistory(
            days=(
                LiftHistoryDay(
                    service_date=date(2026, 8, 23),
                    ran_count=3,
                    lift_count=5,
                    seat_count=28,
                    paid_gel=420,
                ),
                LiftHistoryDay(
                    service_date=date(2026, 8, 22),
                    ran_count=3,
                    lift_count=5,
                    seat_count=26,
                    paid_gel=390,
                ),
                LiftHistoryDay(
                    service_date=date(2026, 8, 16),
                    ran_count=4,
                    lift_count=5,
                    seat_count=33,
                    paid_gel=495,
                ),
            ),
            total_days=3,
            total_ran=10,
            total_seats=87,
            total_gel=1305,
        ),
    )

    assert "☰ Veloexpress admin" in draft.text
    assert "⏰ Next polls open Thu 18:00." in draft.text
    # Both days of the weekend just gone, not the one before it.
    assert "Last lifts · 22–23 Aug" in draft.text
    assert "🚐 6 lifts · 54 seats · 810 GEL" in draft.text
    buttons = _button_map(draft.reply_markup)
    assert buttons["menu:weekend_plan"] == "📋 Weekend"
    assert buttons["menu:extra_day"] == "➕ Extra lift day"
    assert buttons["mon:history"] == "📜 History"


def test_a_first_quiet_week_still_reaches_planning() -> None:
    draft = render_booking_monitor(
        (),
        selected_service_date=None,
        schedule_line="⏰ Auto-posting is off — post from 📋 Weekend.",
        history=LiftHistory(days=(), total_days=0, total_ran=0, total_seats=0, total_gel=0),
    )

    buttons = _button_map(draft.reply_markup)
    assert buttons["menu:weekend_plan"] == "📋 Weekend"
    assert "Last lifts" not in draft.text


def test_lift_history_lists_the_days_and_the_season_under_them() -> None:
    draft = render_lift_history(
        LiftHistory(
            days=(
                LiftHistoryDay(
                    service_date=date(2026, 8, 23),
                    ran_count=3,
                    lift_count=5,
                    seat_count=28,
                    paid_gel=420,
                ),
            ),
            total_days=12,
            total_ran=47,
            total_seats=380,
            total_gel=5700,
        )
    )

    assert "Sun, 23 Aug · 3/5 lifts · 28 seats · 420 GEL" in draft.text
    assert "All time · 12 days · 47 lifts · 380 seats · 5700 GEL" in draft.text
    assert _button_map(draft.reply_markup)["mon:menu"] == "⬅️ Back"


def test_callback_decoding_round_trips() -> None:
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
    assert "🚐 8:30 · 10/10 seats · full · 0/5 paid · 1 manual" in draft.text


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


def test_the_day_card_carries_one_way_out_of_the_day() -> None:
    """Refunds and history hang off the menu now, not off every day card."""
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 7, 18),
                lifts=(BookingLiftStatus(time="8:30", vote_count=7, manual_count=0),),
            ),
        ),
        selected_service_date=date(2026, 7, 18),
    )

    assert draft.reply_markup is not None
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:menu"] == "☰ Menu"
    assert "mon:refunds" not in buttons


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


def _history_day(service_date: date, **overrides: object) -> LiftHistoryDay:
    fields: dict[str, object] = {
        "service_date": service_date,
        "ran_count": 3,
        "lift_count": 5,
        "seat_count": 28,
        "paid_gel": 420,
    }
    fields.update(overrides)
    return LiftHistoryDay(**fields)  # type: ignore[arg-type]


def test_a_live_week_can_still_reach_the_history() -> None:
    """It used to hang off the quiet-week card alone — unreachable on exactly the
    weeks an admin is in the bot."""
    draft = render_booking_monitor(
        (
            BookingMonitorDay(
                service_date=date(2026, 8, 29),
                lifts=(BookingLiftStatus(time="8:30", vote_count=6, manual_count=0),),
            ),
        ),
        selected_service_date=date(2026, 8, 29),
        history=LiftHistory(
            days=(_history_day(date(2026, 8, 23)),),
            total_days=1,
            total_ran=3,
            total_seats=28,
            total_gel=420,
        ),
    )

    assert _button_map(draft.reply_markup)["mon:menu"] == "☰ Menu"


def test_lift_history_opens_each_day_and_offers_the_older_page() -> None:
    draft = render_lift_history(
        LiftHistory(
            days=(_history_day(date(2026, 8, 23)), _history_day(date(2026, 8, 22))),
            total_days=12,
            total_ran=47,
            total_seats=380,
            total_gel=5700,
            older_before=date(2026, 8, 22),
            has_newer=True,
        )
    )

    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:past:20260823"] == "Sun 23"
    assert buttons["mon:past:20260822"] == "Sat 22"
    assert buttons["mon:history:20260822"] == "📅 Older"
    assert buttons["mon:history:0"] == "⏮ Latest"
    assert buttons["mon:trend"] == "🕑 By time"
    # A page is a page; the season line still counts the whole season.
    assert "All time · 12 days · 47 lifts · 380 seats · 5700 GEL" in draft.text


def test_a_period_carries_through_history_and_back_out_of_it() -> None:
    """Opening the days of a year and pressing back used to land on 30 days."""
    draft = render_lift_history(
        LiftHistory(
            days=(_history_day(date(2026, 8, 23)), _history_day(date(2026, 8, 22))),
            total_days=12,
            total_ran=47,
            total_seats=380,
            total_gel=5700,
            older_before=date(2026, 8, 22),
            has_newer=True,
        ),
        period="year",
    )

    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:past:20260823:year"] == "Sun 23"
    assert buttons["mon:history:20260822:year"] == "📅 Older"
    assert buttons["mon:history:0:year"] == "⏮ Latest"
    assert buttons["stats:year:0"] == "📈 Statistics"


def test_a_finished_day_reads_like_the_day_card_without_the_controls() -> None:
    draft = render_lift_day_audit(
        LiftDayAudit(
            service_date=date(2026, 8, 22),
            lifts=(
                LiftDayAuditLift(
                    lift_time="8:30",
                    ran=True,
                    seats=9,
                    capacity=10,
                    covered_seats=8,
                    manual_seats=1,
                    guest_seats=0,
                    riders=(LiftDayAuditSeat(label="@anna", seats=1, guests=0, covered_seats=1),),
                ),
                LiftDayAuditLift(
                    lift_time="10:00",
                    ran=False,
                    seats=3,
                    capacity=10,
                    covered_seats=0,
                    manual_seats=0,
                    guest_seats=0,
                ),
            ),
            price_gel=15,
            received_gel=120,
            refunded_gel=15,
        ),
        period="year",
    )

    assert "🗓 Sat, 22 Aug" in draft.text
    # Same shape as today's card: what ran, then the money, then a block per lift.
    assert "1 of 2 lifts ran · 9 seats" in draft.text
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:history:0:year"] == "⬅️ Back to history"
    # Money went back on this day, so the list of who is owed is reachable here.
    assert buttons["mon:refunds"] == "🧾 Refunds"
    # Nothing that would change a finished day.
    assert not any(
        data.startswith(("mon:add:", "mon:sub:", "mon:cancel", "mon:restore:")) for data in buttons
    )


def test_past_refunds_read_as_records_one_page_at_a_time() -> None:
    """Five stored reports used to arrive as five messages under the card.

    They read as fresh cancellations because nothing said when they were
    written, and they pushed the admin's card out of sight.
    """
    draft = render_refund_reports(
        (
            ("13 Sep 2026, 02:01", "💸 Sun, 13 Sep cancelled"),
            ("05 Sep 2026, 19:20", "💸 15:30 · Sat, 5 Sep cancelled — who paid:"),
        ),
        page=1,
    )

    assert "🧾 Refunds · 2/2" in draft.text
    assert "Written 05 Sep 2026, 19:20" in draft.text
    assert "the day may have moved since" in draft.text
    assert "💸 15:30 · Sat, 5 Sep cancelled" in draft.text
    # One report per page, so the other one is not on this screen.
    assert "Sun, 13 Sep" not in draft.text
    buttons = _button_map(draft.reply_markup)
    assert buttons["mon:refunds:0"] == "⏮ Newer"
    assert "mon:refunds:2" not in buttons
    assert buttons["mon:menu"] == "⬅️ Menu"


def test_a_page_past_the_end_lands_on_the_last_report() -> None:
    """A stale pager button must not open an empty screen."""
    draft = render_refund_reports((("05 Sep 2026, 19:20", "💸 one"),), page=9)

    assert "🧾 Refunds · 1/1" in draft.text
    assert "💸 one" in draft.text


def test_no_refunds_yet_is_a_screen_not_a_loose_message() -> None:
    draft = render_refund_reports(())

    assert "No cancellation has had money in it yet." in draft.text
    assert _button_map(draft.reply_markup)["mon:menu"] == "⬅️ Menu"


def test_the_newest_page_of_history_offers_no_way_forward() -> None:
    draft = render_lift_history(
        LiftHistory(
            days=(_history_day(date(2026, 8, 23)),),
            total_days=1,
            total_ran=3,
            total_seats=28,
            total_gel=420,
        )
    )

    buttons = _button_map(draft.reply_markup)
    assert "⏮ Latest" not in buttons.values()
    assert "📅 Older" not in buttons.values()


def test_history_flags_a_day_it_wrote_down_late() -> None:
    draft = render_lift_history(
        LiftHistory(
            days=(_history_day(date(2026, 8, 23), reconstructed=True),),
            total_days=1,
            total_ran=3,
            total_seats=28,
            total_gel=420,
        )
    )

    assert "Sun, 23 Aug · 3/5 lifts · 28 seats · 420 GEL ⚠️" in draft.text


def test_the_day_audit_names_the_van_and_who_had_not_paid() -> None:
    draft = render_lift_day_audit(
        LiftDayAudit(
            service_date=date(2026, 8, 23),
            lifts=(
                LiftDayAuditLift(
                    lift_time="8:30",
                    ran=True,
                    seats=9,
                    capacity=10,
                    covered_seats=8,
                    manual_seats=1,
                    guest_seats=1,
                    riders=(
                        LiftDayAuditSeat(label="@stas", seats=2, guests=1, covered_seats=2),
                        LiftDayAuditSeat(label="@misho", seats=1, guests=0, covered_seats=0),
                    ),
                ),
                LiftDayAuditLift(
                    lift_time="13:30",
                    ran=False,
                    seats=3,
                    capacity=10,
                    covered_seats=0,
                    manual_seats=0,
                    guest_seats=0,
                ),
            ),
            price_gel=15,
            received_gel=120,
            refunded_gel=30,
        )
    )

    assert "🗓 Sun, 23 Aug" in draft.text
    assert "15 GEL per seat · 120 GEL received · 30 GEL refunded" in draft.text
    assert "🚐 8:30 · ran · 9/10 · 8 paid · 1 manual · 1 guest" in draft.text
    assert "✅ @stas · +1 guest" in draft.text
    assert "🔴 @misho" in draft.text
    assert "💤 13:30 · did not run · 3/10 · 0 paid" in draft.text
    assert _button_map(draft.reply_markup)["mon:history"] == "⬅️ Back to history"


def test_the_day_audit_says_when_its_figures_were_reconstructed() -> None:
    draft = render_lift_day_audit(
        LiftDayAudit(
            service_date=date(2026, 8, 23),
            lifts=(),
            price_gel=15,
            received_gel=0,
            refunded_gel=0,
            reconstructed=True,
        )
    )

    assert "⚠️ Written down late — figures reconstructed from votes." in draft.text


def test_the_trend_names_a_departure_that_never_runs() -> None:
    draft = render_lift_trend(
        LiftTrend(
            by_lift=(
                TrendRow(label="8:30", days_ran=12, days_offered=14, total_seats=89),
                TrendRow(label="15:30", days_ran=0, days_offered=14, total_seats=0),
            ),
            by_weekday=(TrendRow(label="Sat", days_ran=7, days_offered=7, total_seats=140),),
            since=date(2026, 7, 25),
            until=date(2026, 8, 23),
        )
    )

    assert "25 Jul – 23 Aug" in draft.text
    assert "8:30 — ran 12 of 14 · 7.4 seats avg" in draft.text
    assert "15:30 — never ran in 14" in draft.text
    assert "Sat — ran 7 of 7 · 20.0 seats avg" in draft.text
