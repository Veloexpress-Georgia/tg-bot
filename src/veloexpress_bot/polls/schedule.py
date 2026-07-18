from collections.abc import Iterable
from typing import Literal

from veloexpress_bot.polls.defaults import DEFAULT_LIFTS

RangeBoundary = Literal["first", "last"]


def lift_range_from_cancelled(cancelled_lift_times: Iterable[str]) -> tuple[str, str]:
    cancelled = set(cancelled_lift_times)
    active = [lift.time for lift in DEFAULT_LIFTS if lift.time not in cancelled]
    if not active:
        msg = "At least one lift must remain."
        raise ValueError(msg)
    return active[0], active[-1]


def cancelled_lift_times_for_range(first_time: str, last_time: str) -> tuple[str, ...]:
    times = _lift_times()
    first_index = _time_index(first_time)
    last_index = _time_index(last_time)
    if first_index > last_index:
        msg = "First lift cannot be later than last lift."
        raise ValueError(msg)
    return tuple(time for index, time in enumerate(times) if not first_index <= index <= last_index)


def select_lift_range_boundary(
    cancelled_lift_times: Iterable[str],
    *,
    boundary: RangeBoundary,
    selected_time: str,
) -> tuple[str, ...]:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    selected_index = _time_index(selected_time)
    first_index = _time_index(first_time)
    last_index = _time_index(last_time)

    if boundary == "first":
        first_index = selected_index
        last_index = max(last_index, selected_index)
    else:
        last_index = selected_index
        first_index = min(first_index, selected_index)

    times = _lift_times()
    return cancelled_lift_times_for_range(times[first_index], times[last_index])


def enabled_lift_count(cancelled_lift_times: Iterable[str]) -> int:
    first_time, last_time = lift_range_from_cancelled(cancelled_lift_times)
    return _time_index(last_time) - _time_index(first_time) + 1


def normalize_cancelled_lift_times(cancelled_lift_times: Iterable[str]) -> tuple[str, ...]:
    return cancelled_lift_times_for_range(*lift_range_from_cancelled(cancelled_lift_times))


def suggested_cancelled_lift_times(
    history: Iterable[tuple[str, str]],
    *,
    default_cancelled_lift_times: Iterable[str],
    repetitions_required: int = 2,
) -> tuple[str, ...]:
    default_first, default_last = lift_range_from_cancelled(default_cancelled_lift_times)
    rows = tuple(history)
    first_time = _sticky_preference(
        (first for first, _ in rows),
        default=default_first,
        repetitions_required=repetitions_required,
    )
    last_time = _sticky_preference(
        (last for _, last in rows),
        default=default_last,
        repetitions_required=repetitions_required,
    )
    return cancelled_lift_times_for_range(first_time, last_time)


def _lift_times() -> tuple[str, ...]:
    return tuple(lift.time for lift in DEFAULT_LIFTS)


def _time_index(lift_time: str) -> int:
    try:
        return _lift_times().index(lift_time)
    except ValueError as error:
        msg = f"Unknown lift time: {lift_time}"
        raise ValueError(msg) from error


def _sticky_preference(
    values: Iterable[str],
    *,
    default: str,
    repetitions_required: int,
) -> str:
    if repetitions_required < 1:
        msg = "repetitions_required must be positive."
        raise ValueError(msg)

    current = default
    candidate: str | None = None
    candidate_count = 0
    for value in values:
        _time_index(value)
        if value == current:
            candidate = None
            candidate_count = 0
            continue
        if value == candidate:
            candidate_count += 1
        else:
            candidate = value
            candidate_count = 1
        if candidate_count >= repetitions_required:
            current = value
            candidate = None
            candidate_count = 0
    return current
