import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_migration_preserves_existing_price_and_downgrades():
    path = Path(__file__).parents[2] / "alembic/versions/20261029_0033_add_bike_park.py"
    spec = importlib.util.spec_from_file_location("bike_park_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE service_day_defaults "
                    "(id INTEGER PRIMARY KEY, price_gel INTEGER NOT NULL)"
                )
            )
            connection.execute(text("INSERT INTO service_day_defaults VALUES (1, 20)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.execute(
                    text("SELECT price_gel, bike_park_price_gel FROM service_day_defaults")
                ).one() == (20, 5)
                tables = inspect(connection).get_table_names()
                assert "bike_park_session" in tables and "bike_park_vote" in tables
                migration.downgrade()
                assert (
                    connection.execute(text("SELECT price_gel FROM service_day_defaults")).scalar()
                    == 20
                )
                assert "bike_park_session" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()
