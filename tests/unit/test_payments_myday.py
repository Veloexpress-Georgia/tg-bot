from datetime import date

from veloexpress_bot.payments.myday import (
    GuestLiftRow,
    MyDayView,
    decode_guest_date,
    decode_guest_time,
    deep_link,
    encode_guest_date,
    parse_deep_link,
    render_my_day_card,
)

SATURDAY = date(2026, 8, 1)


def _buttons(markup) -> list[list[tuple[str, str | None]]]:  # type: ignore[no-untyped-def]
    return [
        [(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard
    ]


def test_one_lift_keeps_the_form_to_a_single_row() -> None:
    """The simplest case gets the simplest form: no "with me" wording to read."""
    draft = render_my_day_card(
        MyDayView(
            service_date=SATURDAY,
            price_gel=15,
            rows=(GuestLiftRow(lift_time="8:30", guests=0, seats_left=4),),
        )
    )

    assert "8:30 — 0 guests · 4 seats left" in draft.text
    assert _buttons(draft.reply_markup) == [
        [(" ", "guest:noop"), ("8:30 · 0", "guest:noop"), ("➕", "guest:add:20260801:0830")]
    ]


def test_several_lifts_offer_one_tap_for_a_guest_riding_along() -> None:
    """Bringing someone for the whole day is the common case, so it is one button."""
    draft = render_my_day_card(
        MyDayView(
            service_date=SATURDAY,
            price_gel=15,
            rows=(
                GuestLiftRow(lift_time="8:30", guests=1, seats_left=2),
                GuestLiftRow(lift_time="10:00", guests=1, seats_left=0),
            ),
            due_now_gel=60,
            due_all_gel=60,
        )
    )

    assert "Your total: 60 GEL." in draft.text
    assert "10:00 — 1 guest · full" in draft.text
    rows = _buttons(draft.reply_markup)
    assert rows[0] == [
        ("➖ Guest leaves", "guest:allsub:20260801"),
        ("➕ Guest rides with me", "guest:all:20260801"),
    ]
    # A full lift offers no plus: the form never sells a seat that is gone.
    assert rows[2] == [("➖", "guest:sub:20260801:1000"), ("10:00 · 1", "guest:noop")]


def test_an_unfilled_lift_offers_settling_the_whole_day() -> None:
    """Cash is the reason this exists: handing money over twice means finding Misho
    twice, so the rider can close the whole day in one go."""
    draft = render_my_day_card(
        MyDayView(
            service_date=SATURDAY,
            price_gel=15,
            rows=(GuestLiftRow(lift_time="8:30", guests=0, seats_left=4),),
            pending_lift_times=("10:00", "13:30"),
            due_now_gel=15,
            due_all_gel=45,
        )
    )

    assert "Not filled yet: 10:00, 13:30." in draft.text
    assert "Due now 15 GEL · whole day 45 GEL." in draft.text
    assert _buttons(draft.reply_markup)[0] == [
        ("💸 Pay all · 45", "guest:payall:20260801"),
        ("💵 Cash all · 45", "guest:cashall:20260801"),
    ]


def test_nothing_pending_means_no_whole_day_button() -> None:
    draft = render_my_day_card(
        MyDayView(
            service_date=SATURDAY,
            price_gel=15,
            rows=(GuestLiftRow(lift_time="8:30", guests=0, seats_left=4),),
            due_now_gel=15,
            due_all_gel=15,
        )
    )

    # Paying "everything" would be the same as paying now, so the button would lie.
    assert "payall" not in str(_buttons(draft.reply_markup))
    assert "Your total: 15 GEL." in draft.text


def test_a_rider_with_no_running_lift_gets_no_controls() -> None:
    draft = render_my_day_card(MyDayView(service_date=SATURDAY, price_gel=15))

    assert "not booked on any running lift" in draft.text
    assert draft.reply_markup is None


def test_a_fully_booked_day_says_so_instead_of_offering_seats() -> None:
    draft = render_my_day_card(
        MyDayView(
            service_date=SATURDAY,
            price_gel=15,
            rows=(
                GuestLiftRow(lift_time="8:30", guests=0, seats_left=0),
                GuestLiftRow(lift_time="10:00", guests=0, seats_left=0),
            ),
        )
    )

    assert _buttons(draft.reply_markup)[0] == [("All lifts full", "guest:noop")]


def test_the_deep_link_round_trips_through_the_start_payload() -> None:
    link = deep_link(bot_username="veloexpress_bot", service_date=SATURDAY)

    assert link == "https://t.me/veloexpress_bot?start=guests-20260801"
    assert parse_deep_link(link.split("start=")[1]) == SATURDAY
    assert parse_deep_link("nonsense") is None
    assert parse_deep_link("guests-notadate") is None


def test_callback_encodings_round_trip() -> None:
    assert decode_guest_date(encode_guest_date(SATURDAY)) == SATURDAY
    assert decode_guest_time("0830") == "8:30"
    assert decode_guest_time("1000") == "10:00"
