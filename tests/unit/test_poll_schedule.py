import pytest

from veloexpress_bot.polls.schedule import (
    cancelled_lift_times_for_range,
    lift_range_from_cancelled,
    normalize_cancelled_lift_times,
    select_lift_range_boundary,
    suggested_cancelled_lift_times,
)


def test_range_selection_always_enables_contiguous_lifts() -> None:
    assert cancelled_lift_times_for_range("10:00", "13:30") == ("8:30", "15:30")
    assert lift_range_from_cancelled(("8:30", "15:30")) == ("10:00", "13:30")


def test_selecting_boundary_past_other_boundary_creates_single_lift() -> None:
    assert select_lift_range_boundary(
        ("15:30",),
        boundary="first",
        selected_time="15:30",
    ) == ("8:30", "10:00", "11:45", "13:30")
    assert select_lift_range_boundary(
        ("15:30",),
        boundary="last",
        selected_time="8:30",
    ) == ("10:00", "11:45", "13:30", "15:30")


def test_normalize_range_fills_legacy_gaps() -> None:
    assert normalize_cancelled_lift_times(("10:00", "11:45", "15:30")) == ("15:30",)


def test_schedule_suggestion_requires_two_repeated_changes() -> None:
    default_cancelled = ("15:30",)

    assert (
        suggested_cancelled_lift_times(
            (("10:00", "13:30"),),
            default_cancelled_lift_times=default_cancelled,
        )
        == default_cancelled
    )
    assert suggested_cancelled_lift_times(
        (("10:00", "13:30"), ("10:00", "13:30")),
        default_cancelled_lift_times=default_cancelled,
    ) == ("8:30", "15:30")


def test_schedule_suggestion_resets_unconfirmed_trend() -> None:
    assert suggested_cancelled_lift_times(
        (("10:00", "13:30"), ("8:30", "13:30"), ("10:00", "13:30")),
        default_cancelled_lift_times=("15:30",),
    ) == ("15:30",)


def test_range_rejects_unknown_or_reversed_times() -> None:
    with pytest.raises(ValueError, match="Unknown lift time"):
        cancelled_lift_times_for_range("09:00", "13:30")
    with pytest.raises(ValueError, match="First lift cannot"):
        cancelled_lift_times_for_range("13:30", "10:00")
