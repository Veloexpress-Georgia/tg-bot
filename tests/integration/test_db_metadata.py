from veloexpress_bot.db import models  # noqa: F401
from veloexpress_bot.db.base import Base


def test_poll_tables_are_registered() -> None:
    assert {
        "poll_batch",
        "poll_message",
        "poll_option_snapshot",
        "poll_vote",
        "poll_vote_event",
    } <= set(Base.metadata.tables)


def test_poll_vote_unique_index_matches_migration() -> None:
    vote_index_names = {index.name for index in Base.metadata.tables["poll_vote"].indexes}
    event_index_names = {index.name for index in Base.metadata.tables["poll_vote_event"].indexes}

    assert "uq_poll_vote_poll_user" in vote_index_names
    assert {
        "ix_poll_vote_event_poll_id",
        "ix_poll_vote_event_batch_id",
        "ix_poll_vote_event_user",
    } <= event_index_names
