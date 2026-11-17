from datetime import date

import pytest

from veloexpress_bot.polls.defaults import CHECK_ANSWERS_OPTION, PaymentTerms, StartLocation
from veloexpress_bot.polls.render import (
    LiftAvailability,
    PollRenderInput,
    partition_availability_lifts,
    render_poll,
    render_poll_notice,
)


def test_render_poll_uses_ru_en_weekend_template() -> None:
    draft = render_poll(PollRenderInput(service_date=date(2026, 5, 16)))

    assert draft.question == "🚐 Saturday, 16 May"
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


def test_render_poll_notice_keeps_only_booking_essentials_and_links() -> None:
    assert render_poll_notice(StartLocation.VAKE, terms=TERMS).splitlines() == [
        '📍 <a href="https://maps.app.goo.gl/nSNiv7GnNiQt5J64A">Opposite Vake Park</a>',
        "💳 <b>15 ₾ per seat.</b> Transfer when your lift has 5 seats, "
        "by <b>20:00 the evening before</b>. Booked later? Transfer right away.",
        "💵 Cash is paid to Misho on site on the lift day.",
        '<a href="https://pay.example">Where to pay</a> · '
        '<a href="https://t.me/c/2200954181/5">Rules &amp; bank details</a>',
    ]


def test_the_poll_notice_omits_the_link_when_none_is_configured() -> None:
    notice = render_poll_notice(
        StartLocation.VAKE,
        terms=PaymentTerms(price_gel=15, deadline_time="20:00"),
    )

    assert "Where to pay" not in notice
    assert "15 ₾ per seat" in notice
    assert notice.endswith('<a href="https://t.me/c/2200954181/5">Rules &amp; bank details</a>')


def test_short_poll_notice_uses_the_service_days_price_and_deadline() -> None:
    notice = render_poll_notice(
        StartLocation.VAKE, terms=PaymentTerms(price_gel=20, deadline_time="19:30")
    )
    assert "<b>20 ₾ per seat.</b>" in notice
    assert "<b>19:30 the evening before</b>" in notice


def test_after_deadline_availability_groups_viable_lifts_before_short_lifts() -> None:
    running, short, cancelled = partition_availability_lifts(
        (
            LiftAvailability(time="8:30", seat_count=1),
            LiftAvailability(time="10:00", seat_count=5),
            LiftAvailability(time="11:45", seat_count=3),
            LiftAvailability(time="13:30", seat_count=7),
            LiftAvailability(time="15:30", seat_count=0, cancelled=True),
        )
    )
    assert [lift.time for lift in running] == ["10:00", "13:30"]
    assert [lift.time for lift in short] == ["8:30", "11:45"]
    assert [lift.time for lift in cancelled] == ["15:30"]


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
