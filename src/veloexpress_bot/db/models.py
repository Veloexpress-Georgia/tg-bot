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


class PollScheduleHistory(Base):
    __tablename__ = "poll_schedule_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_week_start: Mapped[date] = mapped_column(Date)
    first_lift_time: Mapped[str] = mapped_column(String(16))
    last_lift_time: Mapped[str] = mapped_column(String(16))
    created_by_user_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PollWeekendPlan(Base):
    __tablename__ = "poll_weekend_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_week_start: Mapped[date] = mapped_column(Date)
    saturday_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sunday_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    first_lift_time: Mapped[str] = mapped_column(String(16))
    last_lift_time: Mapped[str] = mapped_column(String(16))
    updated_by_user_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PollAutoSchedule(Base):
    __tablename__ = "poll_auto_schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    creation_weekday: Mapped[int] = mapped_column(Integer, default=4)
    creation_time: Mapped[str] = mapped_column(String(16), default="14:00")
    announce_lead_minutes: Mapped[int] = mapped_column(Integer, default=120)
    skip_week_start: Mapped[date | None] = mapped_column(Date)
    last_announced_week_start: Mapped[date | None] = mapped_column(Date)
    last_created_week_start: Mapped[date | None] = mapped_column(Date)
    announce_message_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_by_user_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ManualBookingCount(Base):
    __tablename__ = "manual_booking_count"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    lift_time: Mapped[str] = mapped_column(String(16))
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_by_user_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CancelledLift(Base):
    __tablename__ = "cancelled_lift"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    lift_time: Mapped[str] = mapped_column(String(16))
    cancelled_by_user_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LiftSignalState(Base):
    """One row per lift: which threshold notices the bot has already sent."""

    __tablename__ = "lift_signal_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    lift_time: Mapped[str] = mapped_column(String(16))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threshold_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undershoot_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undershoot_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    departure_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ServiceDayNotice(Base):
    """Day-level things the bot does once, so it never repeats one."""

    __tablename__ = "service_day_notice"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    deadline_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Kept so the reminder can be refreshed in place until the deadline, after which
    # it stops moving and stands as the final list.
    deadline_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # When the roster below was frozen. Set even for a day nobody booked, so an
    # empty roster is "nobody was on it" rather than "not captured yet".
    roster_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DeadlineRoster(Base):
    """Who held which seat when booking closed.

    Money stops moving at the deadline: a lift that had five riders then is
    running whatever happens later, and whoever was on it owes for it even if
    they drop out afterwards. Live votes cannot answer that — they only ever
    describe now — so the roster is written once and never updated.
    """

    __tablename__ = "deadline_roster"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    lift_time: Mapped[str] = mapped_column(String(16))
    # 0 for manual bookings: they occupy seats but have no Telegram identity, so
    # they count towards the lift running and are billed by Misho directly.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str] = mapped_column(Text, default="")
    # Total seats this row held, own seat plus guests; `guests` of that total.
    seats: Mapped[int] = mapped_column(Integer, default=1)
    guests: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PaymentClaim(Base):
    """A rider's own "I paid" for one service day, priced per seat.

    Granularity is rider + day, not rider + lift: a rider who booked two lifts
    may only ride one, so the seat count is declared, not derived from votes.
    """

    __tablename__ = "payment_claim"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(Text)
    # What the rider has settled for. Not what they owe: re-voting before the
    # deadline changes the bill, and conflating the two makes the bot lie about money.
    seats: Mapped[int] = mapped_column(Integer, default=1)
    # "cash" | "transfer" | NULL when the bot never learned how the money arrived.
    # Misho reconciles against his bank statement, so cash is the case worth naming.
    method: Mapped[str | None] = mapped_column(String(16))
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # The message the bot posted on the rider's behalf, so a seat change edits it
    # instead of adding another line to the payments topic.
    posted_message_id: Mapped[int | None] = mapped_column(BigInteger)
    verified_by_user_id: Mapped[int | None] = mapped_column(BigInteger)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class GuestSeat(Base):
    """Seats a rider takes for someone who has no Telegram vote of their own.

    Per lift, not per day: a seat is capacity, and capacity is what a single lift
    has ten of. Declared in the bot's private chat, where a form can show how many
    seats are actually left.
    """

    __tablename__ = "guest_seat"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    lift_time: Mapped[str] = mapped_column(String(16))
    host_user_id: Mapped[int] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PaymentsBoard(Base):
    """The live payments message for one service day, edited in place."""

    __tablename__ = "payments_board"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    service_date: Mapped[date] = mapped_column(Date)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PaymentsTopicPost(Base):
    """When a rider last wrote in the payments topic — user id and time only.

    The bot deliberately does not read what they wrote. Its only question is
    whether the rider already spoke for themselves, so it can stay quiet.
    """

    __tablename__ = "payments_topic_post"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    last_posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AdminBookingMonitor(Base):
    __tablename__ = "admin_booking_monitor"

    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_user_id: Mapped[int] = mapped_column(BigInteger)
    private_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    selected_service_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
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
    "uq_poll_schedule_history_scope_week",
    PollScheduleHistory.environment,
    PollScheduleHistory.chat_id,
    func.coalesce(PollScheduleHistory.thread_id, 0),
    PollScheduleHistory.service_week_start,
    unique=True,
)


Index(
    "uq_poll_weekend_plan_scope_week",
    PollWeekendPlan.environment,
    PollWeekendPlan.chat_id,
    func.coalesce(PollWeekendPlan.thread_id, 0),
    PollWeekendPlan.service_week_start,
    unique=True,
)


Index(
    "uq_poll_auto_schedule_scope",
    PollAutoSchedule.environment,
    PollAutoSchedule.chat_id,
    func.coalesce(PollAutoSchedule.thread_id, 0),
    unique=True,
)


Index(
    "uq_manual_booking_count_scope_date_time",
    ManualBookingCount.environment,
    ManualBookingCount.chat_id,
    func.coalesce(ManualBookingCount.thread_id, 0),
    ManualBookingCount.service_date,
    ManualBookingCount.lift_time,
    unique=True,
)


Index(
    "uq_cancelled_lift_scope_date_time",
    CancelledLift.environment,
    CancelledLift.chat_id,
    func.coalesce(CancelledLift.thread_id, 0),
    CancelledLift.service_date,
    CancelledLift.lift_time,
    unique=True,
)


Index(
    "uq_lift_signal_state_scope_date_time",
    LiftSignalState.environment,
    LiftSignalState.chat_id,
    func.coalesce(LiftSignalState.thread_id, 0),
    LiftSignalState.service_date,
    LiftSignalState.lift_time,
    unique=True,
)


Index(
    "uq_service_day_notice_scope_date",
    ServiceDayNotice.environment,
    ServiceDayNotice.chat_id,
    func.coalesce(ServiceDayNotice.thread_id, 0),
    ServiceDayNotice.service_date,
    unique=True,
)


Index(
    "uq_deadline_roster_scope_date_time_user",
    DeadlineRoster.environment,
    DeadlineRoster.chat_id,
    func.coalesce(DeadlineRoster.thread_id, 0),
    DeadlineRoster.service_date,
    DeadlineRoster.lift_time,
    DeadlineRoster.telegram_user_id,
    unique=True,
)


Index(
    "uq_payment_claim_scope_date_user",
    PaymentClaim.environment,
    PaymentClaim.chat_id,
    func.coalesce(PaymentClaim.thread_id, 0),
    PaymentClaim.service_date,
    PaymentClaim.telegram_user_id,
    unique=True,
)


Index(
    "uq_guest_seat_scope_date_time_host",
    GuestSeat.environment,
    GuestSeat.chat_id,
    func.coalesce(GuestSeat.thread_id, 0),
    GuestSeat.service_date,
    GuestSeat.lift_time,
    GuestSeat.host_user_id,
    unique=True,
)


Index(
    "uq_payments_board_scope_date",
    PaymentsBoard.environment,
    PaymentsBoard.chat_id,
    PaymentsBoard.service_date,
    unique=True,
)


Index(
    "uq_payments_topic_post_scope_user",
    PaymentsTopicPost.environment,
    PaymentsTopicPost.chat_id,
    PaymentsTopicPost.telegram_user_id,
    unique=True,
)


Index(
    "uq_admin_booking_monitor_scope_admin",
    AdminBookingMonitor.environment,
    AdminBookingMonitor.chat_id,
    func.coalesce(AdminBookingMonitor.thread_id, 0),
    AdminBookingMonitor.admin_user_id,
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
