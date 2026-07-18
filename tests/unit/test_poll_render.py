from datetime import date

import pytest

from veloexpress_bot.polls.defaults import CHECK_ANSWERS_OPTION, StartLocation
from veloexpress_bot.polls.render import (
    LiftAvailability,
    PollRenderInput,
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


def test_render_poll_notice_explains_dynamic_first_lift_location() -> None:
    assert render_poll_notice(StartLocation.JUSTICE_HALL) == (
        "📍 The day's first running lift departs from Justice Hall. "
        "All later lifts depart from Vake Park.\n\n"
        "💳 Please prepay after voting."
    )


def test_render_poll_notice_supports_future_vake_only_override() -> None:
    assert render_poll_notice(StartLocation.VAKE) == (
        "📍 All lifts: Vake Park.\n\n💳 Please prepay after voting."
    )


def test_render_availability_status_shows_free_full_and_waitlisted_lifts() -> None:
    status = render_availability_status(
        date(2026, 5, 16),
        (
            LiftAvailability(time="8:30", voter_count=0),
            LiftAvailability(time="10:00", voter_count=8, manual_count=2),
            LiftAvailability(time="11:45", voter_count=9),
            LiftAvailability(time="13:30", voter_count=10),
            LiftAvailability(time="15:30", voter_count=12),
        ),
    )

    assert "🚐 Availability · Sat, 16 May" in status
    assert "🟢 8:30 — 0/10" in status
    assert "🟡 10:00 — 8/10 · 2 manual" in status
    assert "🟡 11:45 — 9/10" in status
    assert "🔴 13:30 — 10/10" in status
    assert "🔴 15:30 — 12/10 · waitlist +2" in status
    assert "Available ·" not in status
    assert status.endswith("🔴 15:30 — 12/10 · waitlist +2")


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
