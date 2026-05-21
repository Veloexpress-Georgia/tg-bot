from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from veloexpress_bot.db.base import Base

ACTIVE_POLL_BATCH_STATUSES = ("posting", "posted", "sent_unconfirmed")


class PollBatch(Base):
    __tablename__ = "poll_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    service_day: Mapped[str] = mapped_column(String(32))
    created_by_user_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    first_lift_location: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    idempotency_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    superseded_by_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("poll_batch.id", ondelete="SET NULL")
    )

    messages: Mapped[list[PollMessage]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        foreign_keys="PollMessage.batch_id",
    )


class PollMessage(Base):
    __tablename__ = "poll_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("poll_batch.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    poll_id: Mapped[str | None] = mapped_column(Text)
    message_kind: Mapped[str] = mapped_column(String(32), default="poll", server_default="poll")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_status: Mapped[str] = mapped_column(String(64), default="not_attempted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    batch: Mapped[PollBatch] = relationship(back_populates="messages")


class PollOptionSnapshot(Base):
    __tablename__ = "poll_option_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("poll_batch.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    poll_id: Mapped[str] = mapped_column(Text, index=True)
    option_index: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    lift_time: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PollVote(Base):
    __tablename__ = "poll_vote"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[str] = mapped_column(Text, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(Text)
    option_ids: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PollVoteEvent(Base):
    __tablename__ = "poll_vote_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[str] = mapped_column(Text, index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("poll_batch.id", ondelete="SET NULL"))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(Text)
    old_option_ids: Mapped[str] = mapped_column(Text, default="")
    new_option_ids: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


Index("ix_poll_vote_event_batch_id", PollVoteEvent.batch_id)
Index("ix_poll_vote_event_user", PollVoteEvent.telegram_user_id)


Index(
    "uq_poll_vote_poll_user",
    PollVote.poll_id,
    PollVote.telegram_user_id,
    unique=True,
)


Index(
    "uq_poll_batch_active_service_day",
    PollBatch.environment,
    PollBatch.chat_id,
    func.coalesce(PollBatch.thread_id, 0),
    PollBatch.service_date,
    unique=True,
    postgresql_where=PollBatch.status.in_(ACTIVE_POLL_BATCH_STATUSES),
    sqlite_where=PollBatch.status.in_(ACTIVE_POLL_BATCH_STATUSES),
)
