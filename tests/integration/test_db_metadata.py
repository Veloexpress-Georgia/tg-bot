from veloexpress_bot.db import models  # noqa: F401
from veloexpress_bot.db.base import Base


def test_poll_tables_are_registered() -> None:
    assert {"poll_batch", "poll_message"} <= set(Base.metadata.tables)
