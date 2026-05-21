from datetime import date

from veloexpress_bot.bot.handlers import (
    DUPLICATE_POLL_TEXT,
    SetupStateData,
    _cached_allow_recreate,
    _completion_text,
    _menu_message_ids_for_cleanup,
    _poll_setups_from_state,
    _setup_message_ids_for_cleanup,
    _setup_message_ids_from_menu_state,
    _setup_text,
    _setup_text_for_conflicts,
    _should_notify_completion,
    _should_recreate_existing,
    _should_send_recreate_report,
    _store_setup_state,
)
from veloexpress_bot.polls.defaults import StartLocation


def test_completion_text_surfaces_pin_and_cleanup_outcomes() -> None:
    expected = (
        "Polls created. Message IDs: 42, 43; not pinned; "
        "cleaned 1 setup message(s), failed to clean 2."
    )
    assert (
        _completion_text(
            message_ids=(42, 43),
            pinned=False,
            deleted_count=1,
            failed_count=2,
        )
        == expected
    )


def test_completion_notice_is_only_needed_for_non_ideal_outcomes() -> None:
    assert _should_notify_completion(pinned=True, failed_count=0) is False
    assert _should_notify_completion(pinned=False, failed_count=0) is True
    assert _should_notify_completion(pinned=True, failed_count=1) is True


def test_setup_cleanup_includes_admin_command_message() -> None:
    assert _setup_message_ids_for_cleanup(
        command_message_id=10,
        setup_message_id=11,
    ) == (10, 11)


def test_menu_cleanup_includes_admin_command_message() -> None:
    assert _menu_message_ids_for_cleanup(
        command_message_id=10,
        menu_message_id=11,
    ) == (10, 11)


def test_setup_started_from_menu_inherits_start_command_cleanup() -> None:
    assert _setup_message_ids_from_menu_state(
        data={"menu_message_ids": [10, 11]},
        setup_message_id=11,
    ) == (10, 11)


def test_setup_started_from_stale_menu_cleans_current_message() -> None:
    assert _setup_message_ids_from_menu_state(
        data={},
        setup_message_id=11,
    ) == (11,)


def test_recreate_report_is_only_sent_when_votes_are_present() -> None:
    assert (
        _should_send_recreate_report(
            "Recreated existing polls.\n\nTracked votes before recreate:\n\n"
            "2026-05-23:\n- No tracked votes; the poll may predate vote tracking.\n\n"
            "No tracked rider votes were found."
        )
        is False
    )
    assert (
        _should_send_recreate_report(
            "Recreated existing polls.\n\nTracked votes before recreate:\n\n"
            "2026-05-23:\n- 🚲 10:00 · Дом Юстиции / Justice hall: @stas"
        )
        is True
    )


def test_setup_text_only_lists_selected_days() -> None:
    text = _setup_text(
        (date(2026, 5, 16), date(2026, 5, 17)),
        StartLocation.JUSTICE_HALL,
        (),
        selected_service_dates=(date(2026, 5, 17),),
    )

    assert "Sunday 17.05.2026" in text
    assert "Saturday 16.05.2026" not in text


def test_setup_text_for_conflicts_surfaces_recreate_context_immediately() -> None:
    setup_state = SetupStateData(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        first_lift_location=StartLocation.JUSTICE_HALL,
        cancelled_lift_times=(),
    )

    text = _setup_text_for_conflicts(setup_state=setup_state, allow_recreate=True)

    assert DUPLICATE_POLL_TEXT in text


def test_poll_setups_from_state_uses_selected_days_only() -> None:
    setup_state = SetupStateData(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 17),),
        first_lift_location=StartLocation.VAKE,
        cancelled_lift_times=("15:30",),
    )

    setups = _poll_setups_from_state(setup_state=setup_state, created_by_user_id=10)

    assert [setup.service_date for setup in setups] == [date(2026, 5, 17)]
    assert setups[0].created_by_user_id == 10
    assert setups[0].first_lift_location == StartLocation.VAKE
    assert setups[0].cancelled_lift_times == ("15:30",)


def test_stale_recreate_button_falls_back_to_normal_create() -> None:
    assert _should_recreate_existing(requested=True, has_conflicts=True) is True
    assert _should_recreate_existing(requested=True, has_conflicts=False) is False
    assert _should_recreate_existing(requested=False, has_conflicts=True) is False


def test_cached_allow_recreate_requires_explicit_true() -> None:
    assert _cached_allow_recreate({"allow_recreate": True}) is True
    assert _cached_allow_recreate({"allow_recreate": False}) is False
    assert _cached_allow_recreate({}) is False


class FakeState:
    def __init__(self) -> None:
        self.state: object | None = None
        self.data: dict[str, object] = {}

    async def set_state(self, state: object) -> None:
        self.state = state

    async def update_data(self, **kwargs: object) -> None:
        self.data.update(kwargs)


async def test_store_setup_state_uses_serializable_menu_data() -> None:
    setup_state = SetupStateData(
        service_dates=(date(2026, 5, 16), date(2026, 5, 17)),
        selected_service_dates=(date(2026, 5, 16),),
        first_lift_location=StartLocation.JUSTICE_HALL,
        cancelled_lift_times=("15:30",),
        setup_view="times",
    )
    state = FakeState()

    await _store_setup_state(state=state, setup_state=setup_state)  # type: ignore[arg-type]

    assert state.data["service_dates"] == ["2026-05-16", "2026-05-17"]
    assert state.data["selected_service_dates"] == ["2026-05-16"]
    assert state.data["first_lift_location"] == "justice_hall"
    assert state.data["cancelled_lift_times"] == ["15:30"]
    assert state.data["setup_view"] == "times"
    assert state.data["setup_message_ids"] == []
    assert state.data["allow_recreate"] is False
