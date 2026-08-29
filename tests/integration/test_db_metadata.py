from veloexpress_bot.db import models  # noqa: F401
from veloexpress_bot.db.base import Base


def test_poll_tables_are_registered() -> None:
    assert {
        "poll_batch",
        "poll_message",
        "poll_option_snapshot",
        "poll_vote",
        "poll_vote_event",
        "poll_schedule_history",
        "manual_booking_count",
        "admin_booking_monitor",
        "poll_auto_schedule",
        "poll_weekend_plan",
        "cancelled_lift",
        "service_day_terms",
        "service_day_defaults",
        "payment_entry",
        "worker_checkpoint",
        "telegram_outbox",
    } <= set(Base.metadata.tables)


def test_poll_schedule_history_has_one_row_per_week_and_target() -> None:
    index_names = {index.name for index in Base.metadata.tables["poll_schedule_history"].indexes}

    assert "uq_poll_schedule_history_scope_week" in index_names


def test_booking_monitor_tables_have_scoped_unique_indexes() -> None:
    manual_indexes = {index.name for index in Base.metadata.tables["manual_booking_count"].indexes}
    monitor_indexes = {
        index.name for index in Base.metadata.tables["admin_booking_monitor"].indexes
    }

    assert "uq_manual_booking_count_scope_date_time" in manual_indexes
    assert "uq_admin_booking_monitor_scope_admin" in monitor_indexes


def test_poll_auto_schedule_has_one_row_per_target() -> None:
    index_names = {index.name for index in Base.metadata.tables["poll_auto_schedule"].indexes}

    assert "uq_poll_auto_schedule_scope" in index_names


def test_poll_vote_unique_index_matches_migration() -> None:
    vote_columns = Base.metadata.tables["poll_vote"].columns
    vote_index_names = {index.name for index in Base.metadata.tables["poll_vote"].indexes}
    event_index_names = {index.name for index in Base.metadata.tables["poll_vote_event"].indexes}

    assert "option_booked_at" in vote_columns
    assert "uq_poll_vote_poll_user" in vote_index_names
    assert {
        "ix_poll_vote_event_poll_id",
        "ix_poll_vote_event_batch_id",
        "ix_poll_vote_event_user",
    } <= event_index_names


def test_money_tables_preserve_terms_and_append_entries() -> None:
    terms_indexes = {index.name for index in Base.metadata.tables["service_day_terms"].indexes}
    defaults_indexes = {
        index.name for index in Base.metadata.tables["service_day_defaults"].indexes
    }
    entry_indexes = {index.name for index in Base.metadata.tables["payment_entry"].indexes}
    claim_columns = Base.metadata.tables["payment_claim"].columns.keys()
    roster_columns = Base.metadata.tables["deadline_roster"].columns
    worker_indexes = {index.name for index in Base.metadata.tables["worker_checkpoint"].indexes}
    outbox_indexes = {index.name for index in Base.metadata.tables["telegram_outbox"].indexes}

    assert "uq_service_day_terms_scope_date" in terms_indexes
    assert "uq_service_day_defaults_scope" in defaults_indexes
    assert "ix_payment_entry_scope_date_user" in entry_indexes
    assert "uq_payment_entry_environment_reference" in entry_indexes
    assert {"amount_gel", "cash_amount_gel"} <= set(claim_columns)
    assert "covered_seats" in roster_columns
    assert "uq_worker_checkpoint_environment_name" in worker_indexes
    assert "uq_telegram_outbox_environment_operation" in outbox_indexes
