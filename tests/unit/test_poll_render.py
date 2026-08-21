from datetime import date

import pytest

from veloexpress_bot.polls.defaults import CHECK_ANSWERS_OPTION, PaymentTerms, StartLocation
from veloexpress_bot.polls.render import (
    LiftAvailability,
    PollRenderInput,
    WaitlistRider,
    render_availability_status,
    render_poll,
    render_poll_notice,
)


def test_render_poll_uses_ru_en_weekend_template() -> None:
    draft = render_poll(PollRenderInput(service_date=date(2026, 5, 16)))

    assert draft.question == "🚐 Saturday · May 16"
    assert draft.options[-1] == CHECK_ANSWERS_OPTION
    assert draft.options[0] == "🚲 8:30"
    assert draft.options[1] == "🚲 10:00"
    assert draft.options[-1] == "👀 Check answers"
    assert draft.is_anonymous is False
    assert draft.allows_multiple_answers is True


TERMS = PaymentTerms(price_gel=15, deadline_time="20:00", link="https://pay.example")


def test_render_poll_notice_explains_dynamic_first_lift_location() -> None:
    assert render_poll_notice(StartLocation.JUSTICE_HALL, terms=TERMS).startswith(
        "📍 The day's first running lift departs from Justice Hall. "
        "All later lifts depart from Vake Park."
    )


def test_render_poll_notice_states_the_money_rules_in_order() -> None:
    assert render_poll_notice(StartLocation.VAKE, terms=TERMS).splitlines() == [
        "📍 All lifts: Vake Park.",
        "",
        "💳 15 GEL per seat.",
        # The old notice said "prepay after voting", which is now simply wrong.
        "A lift runs from 5 riders. Wait for the ✅ message, then pay — "
        "nothing to pay before that.",
        "Book, change or cancel free until 20:00 the day before.",
        "💵 Cash is fine — settle with Misho and tap 💵 Cash on the payments board.",
        '🔗 <a href="https://pay.example">Where to pay</a>',
    ]


def test_the_poll_notice_omits_the_link_when_none_is_configured() -> None:
    notice = render_poll_notice(
        StartLocation.VAKE,
        terms=PaymentTerms(price_gel=15, deadline_time="20:00"),
    )

    assert "🔗" not in notice
    assert "15 GEL per seat." in notice


def test_availability_names_the_waitlist_where_the_seats_are_counted() -> None:
    """ "waitlist +2" is a riddle without names. The board is edited in place, and a
    Telegram edit sends no notification, so naming informs without pinging."""
    status = render_availability_status(
        date(2026, 8, 1),
        (
            LiftAvailability(
                time="8:30",
                seat_count=12,
                waitlist=(
                    WaitlistRider(telegram_user_id=11, label="@konstantin"),
                    WaitlistRider(telegram_user_id=12, label="Ivan"),
                ),
            ),
            LiftAvailability(time="10:00", seat_count=6),
        ),
    )

    lines = status.splitlines()
    assert lines[2] == "8:30 — <b>12/10</b> · waitlist +2"
    assert lines[3] == (
        '    ⏳ <a href="tg://user?id=11">@konstantin</a> <a href="tg://user?id=12">Ivan</a>'
    )
    # A lift with nobody waiting stays a single line.
    assert lines[4] == "10:00 — <b>6/10</b> · 4 left"


def test_render_availability_status_shows_counts_and_state_tags() -> None:
    status = render_availability_status(
        date(2026, 5, 16),
        (
            LiftAvailability(time="8:30", seat_count=3),
            LiftAvailability(time="10:00", seat_count=6),
            LiftAvailability(time="11:45", seat_count=10),
            LiftAvailability(time="15:30", seat_count=12),
        ),
    )

    assert "🚐 Availability · Sat, 16 May" in status
    # Below the minimum reads "needs N more"; at/above, remaining seats.
    assert "8:30 — <b>3/10</b> · needs 2 more" in status
    assert "10:00 — <b>6/10</b> · 4 left" in status
    assert "11:45 — <b>10/10</b> · full" in status
    assert "15:30 — <b>12/10</b> · waitlist +2" in status
    assert "🟢" not in status and "🔴" not in status
    assert "<pre>" not in status


def test_render_poll_can_cancel_lifts() -> None:
    draft = render_poll(
        PollRenderInput(
            service_date=date(2026, 5, 16),
            cancelled_lift_times=("11:45", "15:30"),
        )
    )

    assert "11:45" not in "\n".join(draft.options)
    assert "15:30" not in "\n".join(draft.options)
    assert "8:30" in draft.options[0]
    assert "10:00" in draft.options[1]
    assert "13:30" in draft.options[2]


def test_render_poll_requires_at_least_one_lift() -> None:
    with pytest.raises(ValueError, match="At least one lift"):
        render_poll(
            PollRenderInput(
                service_date=date(2026, 5, 16),
                cancelled_lift_times=("8:30", "10:00", "11:45", "13:30", "15:30"),
            )
        )


def test_availability_explains_seats_the_poll_cannot_show() -> None:
    """Guests and manual bookings hold seats but cast no vote, so the board reads
    higher than the poll. One footer line says why, instead of a number on every
    lift line."""
    status = render_availability_status(
        date(2026, 8, 23),
        (
            LiftAvailability(time="8:30", seat_count=0),
            LiftAvailability(time="10:00", seat_count=9, guest_count=1),
            LiftAvailability(time="11:45", seat_count=11, manual_count=1, guest_count=1),
            LiftAvailability(time="13:30", seat_count=7, cancelled=True, guest_count=1),
        ),
    )

    lines = status.splitlines()
    assert lines[-1] == "🎟 Booked outside the poll: +1 at 10:00, +2 at 11:45"
    assert lines[-2] == ""
    # The lift lines themselves stay untouched.
    assert "10:00 — <b>9/10</b> · 1 left" in status


def test_availability_has_no_off_poll_note_when_everyone_voted() -> None:
    status = render_availability_status(
        date(2026, 8, 23),
        (
            LiftAvailability(time="8:30", seat_count=3),
            LiftAvailability(time="10:00", seat_count=6),
        ),
    )

    assert "🎟" not in status
    assert status.splitlines()[-1] == "10:00 — <b>6/10</b> · 4 left"
