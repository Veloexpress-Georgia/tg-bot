from datetime import date

from veloexpress_bot.payments.myday import (
    CASH_LINK_PREFIX,
    PAID_LINK_PREFIX,
    DeepLinkIntent,
    RiderCardView,
    RiderDayView,
    RiderLiftRow,
    RiderSeason,
    RiderSeasonDay,
    decode_guest_date,
    decode_guest_time,
    deep_link,
    encode_guest_date,
    parse_deep_link,
    render_rider_card,
    render_rider_season,
)

SATURDAY = date(2026, 8, 1)
SUNDAY = date(2026, 8, 2)


def _buttons(markup) -> list[list[tuple[str, str | None]]]:  # type: ignore[no-untyped-def]
    return [
        [(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard
    ]


def _card(*days: RiderDayView, selected: date | None = None) -> RiderCardView:
    return RiderCardView(days=days, price_gel=15, selected_service_date=selected)


def test_one_lift_keeps_the_card_to_a_single_row() -> None:
    """The simplest case gets the simplest card: no "with me" wording to read."""
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", guests=0, seats_left=4),),
            )
        )
    )

    assert "8:30 — riding · 4 seats left" in draft.text
    assert _buttons(draft.reply_markup) == [
        [(" ", "guest:noop"), ("8:30 · 0", "guest:noop"), ("➕", "guest:add:20260801:0830")]
    ]


def test_several_lifts_offer_one_tap_for_a_guest_riding_along() -> None:
    """Bringing someone for the whole day is the common case, so it is one button."""
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(
                    RiderLiftRow(lift_time="8:30", guests=1, seats_left=2),
                    RiderLiftRow(lift_time="10:00", guests=1, seats_left=0),
                ),
                due_now_gel=60,
                due_all_gel=60,
            )
        )
    )

    assert "Your total: 60 GEL." in draft.text
    assert "10:00 — riding · 1 guest · full" in draft.text
    rows = _buttons(draft.reply_markup)
    assert rows[0] == [("💸 Pay 60", "guest:pay:20260801"), ("💵 Cash 60", "guest:cash:20260801")]
    assert rows[1] == [
        ("➖ Guest leaves", "guest:allsub:20260801"),
        ("➕ Guest rides with me", "guest:all:20260801"),
    ]
    # A full lift offers no plus: the card never sells a seat that is gone.
    assert rows[3] == [("➖", "guest:sub:20260801:1000"), ("10:00 · 1", "guest:noop")]


def test_an_unfilled_lift_offers_settling_the_whole_day() -> None:
    """Cash is the reason this exists: handing money over twice means finding Misho
    twice, so the rider can close the whole day in one go."""
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", guests=0, seats_left=4),),
                pending_lift_times=("10:00", "13:30"),
                due_now_gel=15,
                due_all_gel=45,
            )
        )
    )

    assert "Not filled yet: 10:00, 13:30." in draft.text
    assert "Due now 15 GEL · whole day 45 GEL." in draft.text
    assert _buttons(draft.reply_markup)[1] == [
        ("💸 Pay all · 45", "guest:payall:20260801"),
        ("💵 Cash all · 45", "guest:cashall:20260801"),
    ]


def test_nothing_pending_means_no_whole_day_button() -> None:
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", guests=0, seats_left=4),),
                due_now_gel=15,
                due_all_gel=15,
            )
        )
    )

    # Paying "everything" would be the same as paying now, so the button would lie.
    assert "payall" not in str(_buttons(draft.reply_markup))
    assert "Your total: 15 GEL." in draft.text


def test_a_rider_with_no_booking_gets_no_controls() -> None:
    draft = render_rider_card(_card())

    assert "not booked on any lift yet" in draft.text
    assert draft.reply_markup is None


def test_a_waitlisted_rider_is_told_the_queue_and_asked_for_nothing() -> None:
    """The only place a rider can learn their position; the board shows names, not order."""
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", seats_left=0, waitlist_position=2),),
                due_now_gel=15,
                due_all_gel=15,
            )
        )
    )

    assert "8:30 — ⏳ waitlist, 2nd in line" in draft.text
    assert "nothing to pay until one frees up" in draft.text
    # No money buttons: they hold no seat, so there is nothing to settle.
    assert draft.reply_markup is None or "guest:pay" not in str(_buttons(draft.reply_markup))


def test_paid_in_full_is_shown_as_settled() -> None:
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", seats_left=3),),
                due_now_gel=15,
                due_all_gel=15,
                paid_gel=15,
            )
        )
    )

    assert "Paid 15 GEL ✅" in draft.text
    # Nothing outstanding, so no pay button — but Undo stays reachable.
    buttons = str(_buttons(draft.reply_markup))
    assert "guest:pay" not in buttons
    assert "guest:undo:20260801" in buttons


def test_the_weekend_is_one_card_with_day_tabs() -> None:
    """Two cards would mean two chances to read a stale amount with a live button."""
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(RiderLiftRow(lift_time="8:30", seats_left=3),),
            ),
            RiderDayView(
                service_date=SUNDAY,
                rows=(RiderLiftRow(lift_time="10:00", seats_left=1),),
            ),
            selected=SUNDAY,
        )
    )

    assert "Sun, 2 Aug" in draft.text
    assert "10:00 — riding · 1 seats left" in draft.text
    assert _buttons(draft.reply_markup)[-1] == [
        ("Sat 1", "guest:day:20260801"),
        ("✅ Sun 2", "guest:day:20260802"),
    ]


def test_a_fully_booked_day_says_so_instead_of_offering_seats() -> None:
    draft = render_rider_card(
        _card(
            RiderDayView(
                service_date=SATURDAY,
                rows=(
                    RiderLiftRow(lift_time="8:30", guests=0, seats_left=0),
                    RiderLiftRow(lift_time="10:00", guests=0, seats_left=0),
                ),
            )
        )
    )

    assert _buttons(draft.reply_markup)[0] == [("All lifts full", "guest:noop")]


def test_the_deep_link_round_trips_through_the_start_payload() -> None:
    link = deep_link(bot_username="veloexpress_bot", service_date=SATURDAY)

    assert link == "https://t.me/veloexpress_bot?start=guests-20260801"
    assert parse_deep_link(link.split("start=")[1]) == DeepLinkIntent(SATURDAY)
    assert parse_deep_link("nonsense") is None
    assert parse_deep_link("guests-notadate") is None


def test_the_payment_links_carry_the_method_through_the_payload() -> None:
    """ "I paid" is the rider asserting money moved, so the link says which kind."""
    paid = deep_link(bot_username="veloexpress_bot", service_date=SATURDAY, prefix=PAID_LINK_PREFIX)
    cash = deep_link(bot_username="veloexpress_bot", service_date=SATURDAY, prefix=CASH_LINK_PREFIX)

    assert paid.endswith("start=paid-20260801")
    assert cash.endswith("start=cash-20260801")
    assert parse_deep_link("paid-20260801") == DeepLinkIntent(SATURDAY, method="transfer")
    assert parse_deep_link("cash-20260801") == DeepLinkIntent(SATURDAY, method="cash")
    # Looking is not paying: the plain link carries no method at all.
    assert parse_deep_link("guests-20260801") == DeepLinkIntent(SATURDAY, method="")


def test_callback_encodings_round_trip() -> None:
    assert decode_guest_date(encode_guest_date(SATURDAY)) == SATURDAY
    assert decode_guest_time("0830") == "8:30"
    assert decode_guest_time("1000") == "10:00"


def test_a_rider_who_has_ridden_before_is_not_left_at_a_dead_end() -> None:
    """Midweek is exactly when somebody idly opens their card and finds nothing."""
    draft = render_rider_card(
        RiderCardView(days=(), price_gel=15, selected_service_date=None, has_history=True)
    )

    assert "not booked on any lift yet" in draft.text
    assert draft.reply_markup is not None
    assert ("📜 My past rides", "guest:season") in _buttons(draft.reply_markup)[0]


def test_my_past_rides_lists_the_days_and_the_season_under_them() -> None:
    draft = render_rider_season(
        RiderSeason(
            days=(
                RiderSeasonDay(
                    service_date=SATURDAY,
                    lift_times=("8:30", "13:30"),
                    seats=3,
                    paid_gel=45,
                ),
                RiderSeasonDay(
                    service_date=SUNDAY,
                    lift_times=("10:00",),
                    seats=1,
                    paid_gel=15,
                ),
            ),
            total_days=2,
            total_rides=3,
            total_seats=4,
            total_gel=60,
        )
    )

    assert "Sat, 1 Aug · 8:30, 13:30 · 3 seats · 45 GEL" in draft.text
    assert "Sun, 2 Aug · 10:00 · 1 seat · 15 GEL" in draft.text
    assert "2 days · 3 lifts · 4 seats · 60 GEL" in draft.text
    assert ("⬅️ Back", "guest:card") in _buttons(draft.reply_markup)[0]


def test_my_past_rides_before_the_first_one() -> None:
    draft = render_rider_season(
        RiderSeason(days=(), total_days=0, total_rides=0, total_seats=0, total_gel=0)
    )

    assert "No finished lift yet." in draft.text
