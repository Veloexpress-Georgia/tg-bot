import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_migration_preserves_existing_votes_and_can_be_downgraded() -> None:
    path = Path(__file__).parents[2] / "alembic/versions/20261003_0032_add_booking_order.py"
    spec = importlib.util.spec_from_file_location("booking_order_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text("CREATE TABLE poll_vote (id INTEGER PRIMARY KEY, option_ids TEXT)")
            )
            connection.execute(text("CREATE TABLE poll_option_snapshot (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO poll_vote VALUES (1, '0,1')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.execute(
                    text(
                        "SELECT option_ids, option_queue_ranks, "
                        "option_previous_orders FROM poll_vote"
                    )
                ).one() == ("0,1", "{}", "{}")
                assert "booking_order_change" in inspect(connection).get_table_names()
                connection.execute(text("INSERT INTO poll_option_snapshot VALUES (1)"))
                connection.execute(
                    text(
                        "INSERT INTO booking_order_change "
                        "(snapshot_id, environment, chat_id, service_date, lift_time, poll_id, "
                        "admin_user_id, action, before_order, after_order, created_at) "
                        "VALUES (1, 'test', 123, '2026-10-03', '8:30', 'poll', "
                        "1, 'reorder', '[100,101]', '[101,100]', '2026-10-03 12:00:00')"
                    )
                )
                connection.execute(text("DELETE FROM poll_option_snapshot WHERE id=1"))
                assert connection.execute(
                    text(
                        "SELECT snapshot_id, poll_id, before_order, after_order "
                        "FROM booking_order_change"
                    )
                ).one() == (None, "poll", "[100,101]", "[101,100]")
                migration.downgrade()
                assert (
                    connection.execute(text("SELECT option_ids FROM poll_vote")).scalar() == "0,1"
                )
                assert "booking_order_change" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()
