from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from veloexpress_bot.db.base import Base


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

    messages: Mapped[list[PollMessage]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class PollMessage(Base):
    __tablename__ = "poll_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("poll_batch.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    poll_id: Mapped[str | None] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_status: Mapped[str] = mapped_column(String(64), default="not_attempted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    batch: Mapped[PollBatch] = relationship(back_populates="messages")
