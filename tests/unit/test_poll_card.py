from datetime import date
from io import BytesIO

from PIL import Image

from veloexpress_bot.polls.card import (
    BLUE,
    PURPLE,
    _breakdown,
    _seat_segments,
    _short_summary,
    _state,
    availability_caption,
    render_availability_card,
)
from veloexpress_bot.polls.render import GuestParty, LiftAvailability, WaitlistRider


def test_availability_card_renders_dynamic_lifts_as_a_telegram_sized_png() -> None:
    lifts = (
        LiftAvailability(time="8:30", seat_count=1),
        LiftAvailability(
            time="10:00",
            seat_count=12,
            capacity=10,
            manual_count=2,
            guests=(GuestParty(1, "@rider", 1),),
            waitlist=(WaitlistRider(2, "Иван"), WaitlistRider(3, "Nika")),
        ),
        LiftAvailability(time="11:45", seat_count=0, cancelled=True),
    )

    card = render_availability_card(date(2026, 9, 26), lifts)

    with Image.open(BytesIO(card)) as image:
        assert image.format == "PNG"
        assert image.width == 1000
        assert image.height > 650
    assert len(card) < 10_000_000
    assert card == render_availability_card(date(2026, 9, 26), lifts)


def test_underfilled_lifts_are_quiet_after_deadline_and_sources_are_separate() -> None:
    underfilled = LiftAvailability(time="8:30", seat_count=3)
    assert _state(underfilled, deadline_passed=False)[0] == "NEEDS 2 MORE"
    assert _state(underfilled, deadline_passed=True)[0] == "BELOW MINIMUM"
    assert (
        _state(underfilled, deadline_passed=True)[2]
        != _state(LiftAvailability(time="10:00", seat_count=5), deadline_passed=True)[2]
    )

    mixed = LiftAvailability(
        time="10:00",
        seat_count=6,
        manual_count=2,
        guests=(GuestParty(1, "@rider", 1),),
    )
    assert [label for label, _ in _breakdown(mixed)] == [
        "EXTERNAL +2",
        "GUESTS +1",
        "VOTES 3",
    ]
    assert render_availability_card(date(2026, 9, 26), (underfilled,)) != (
        render_availability_card(date(2026, 9, 26), (underfilled,), deadline_passed=True)
    )


def test_bar_colors_show_reserved_external_and_guest_seats_before_telegram_votes() -> None:
    lift = LiftAvailability(
        time="10:00",
        seat_count=5,
        manual_count=2,
        guests=(GuestParty(1, "Guest", 1),),
    )
    assert _seat_segments(lift) == ((2, "#75BAF9"), (1, "#C5A6FF"), (2, "#61D6A3"))
    card = render_availability_card(date(2026, 9, 26), (lift,))
    with Image.open(BytesIO(card)) as image:
        # The first row starts at y=270; its bar is at y=370.
        assert image.getpixel((160, 378)) == (117, 186, 249)
        assert image.getpixel((280, 378)) == (197, 166, 255)
        assert image.getpixel((415, 378)) == (97, 214, 163)
        assert image.getpixel((700, 378)) == (53, 70, 91)
        # Seats are separate cells, and the minimum sits in a wider gap with a tick.
        assert image.getpixel((155, 378)) == (29, 43, 62)
        assert image.getpixel((500, 378)) == (243, 247, 255)
        assert image.getpixel((495, 378)) == (29, 43, 62)

    waiting = LiftAvailability(
        time="15:30",
        seat_count=12,
        manual_count=2,
        guests=(GuestParty(1, "Guest", 1),),
    )
    # The 2 waiting Telegram votes do not fill seats or change the source bar.
    assert _seat_segments(waiting) == ((2, "#75BAF9"), (1, "#C5A6FF"), (7, "#61D6A3"))
    assert _breakdown(waiting)[-1][0] == "VOTES 7"


def test_lift_states_do_not_borrow_seat_source_colors() -> None:
    full = LiftAvailability(time="13:30", seat_count=10)
    waitlisted = LiftAvailability(
        time="15:30", seat_count=12, waitlist=(WaitlistRider(2, "@a"), WaitlistRider(3, "@b"))
    )
    for lift in (full, waitlisted):
        assert _state(lift, deadline_passed=True)[1] not in {BLUE, PURPLE}
    # Votes alone need no pill: the count beside the time already says it.
    assert _breakdown(waitlisted) == ()


def test_below_minimum_summary_counts_sources_in_plain_english() -> None:
    lift = LiftAvailability(time="11:45", seat_count=3, guests=(GuestParty(1, "@rider", 1),))
    assert _short_summary(lift) == "1 guest · 2 TG votes  ·  needs 2 more"
    assert _short_summary(LiftAvailability(time="8:30", seat_count=1)) == (
        "1 TG vote  ·  needs 4 more"
    )
    assert _short_summary(LiftAvailability(time="8:30", seat_count=0)) == (
        "No bookings  ·  needs 5 more"
    )


def test_caption_preserves_clickable_names_when_possible_and_caps_long_boards() -> None:
    short = '🚐 Availability\n<a href="tg://user?id=1">Иван</a>'
    assert availability_caption(short) == short

    long = "🚐 Availability\n" + "\n".join(f"{index}: " + "Rider " * 30 for index in range(30))
    caption = availability_caption(long)
    assert len(caption) <= 1024
    assert caption.endswith("… More details in the image.")
