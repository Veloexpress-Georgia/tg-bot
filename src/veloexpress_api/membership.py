"""Cabinet access is limited to current members of the configured Telegram chat.

The API process never starts the Telegram poller or scheduler; it asks the Bot
API `getChatMember` directly and remembers each answer for a few minutes so
ordinary page loads do not reach Telegram.

When Telegram cannot answer:
- signing in is refused (503), because membership was never confirmed;
- an existing session stays usable while its last confirmed membership is
  younger than the grace period, then it is refused (503) as well.
A definite "not a member" is always refused (403).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

logger = logging.getLogger(__name__)

MEMBERS_ONLY_CODE = "not_member"
MEMBERS_ONLY_DETAIL = "Only members of the VeloExpress chat can use the cabinet."
UNAVAILABLE_DETAIL = "Telegram cannot confirm chat membership right now. Try again shortly."

MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})
# Telegram answers these for a user id it cannot match to a chat participant.
NOT_PARTICIPANT_ERRORS = ("user not found", "participant_id_invalid", "member not found")


class MembershipUnavailable(Exception):
    """Telegram did not give a definite answer."""


class NotAMember(Exception):
    pass


MemberLookup = Callable[[int], Awaitable[bool]]


def is_member_status(status: str, is_member: bool | None = None) -> bool:
    if status in MEMBER_STATUSES:
        return True
    if status == "restricted":
        return bool(is_member)
    # "left", "kicked" and anything Telegram adds later.
    return False


def telegram_member_lookup(bot: Bot, chat_id: int | None, *, timeout: int = 5) -> MemberLookup:
    async def lookup(user_id: int) -> bool:
        if chat_id is None:
            raise MembershipUnavailable("TELEGRAM_TARGET_CHAT_ID is not configured")
        try:
            member = await bot.get_chat_member(
                chat_id=chat_id, user_id=user_id, request_timeout=timeout
            )
        except TelegramBadRequest as error:
            if any(text in error.message.lower() for text in NOT_PARTICIPANT_ERRORS):
                return False
            # "chat not found" and the like: a configuration problem, not an answer.
            logger.warning("cabinet_membership_lookup_rejected", extra={"user_id": user_id})
            raise MembershipUnavailable(error.message) from error
        except (TelegramAPIError, TimeoutError, OSError) as error:
            logger.warning("cabinet_membership_lookup_failed", extra={"user_id": user_id})
            raise MembershipUnavailable(str(error)) from error
        return is_member_status(member.status, getattr(member, "is_member", None))

    return lookup


@dataclass
class _Entry:
    member: bool
    checked_at: float
    confirmed_at: float | None


class MembershipGate:
    def __init__(
        self,
        lookup: MemberLookup,
        *,
        ttl: float = 600,
        grace: float = 3600,
        retry_after: float = 30,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        self._lookup = lookup
        self._ttl = ttl
        self._grace = grace
        self._retry_after = retry_after
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[int, _Entry] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def require(self, user_id: int, *, fresh: bool = False) -> None:
        """Return when `user_id` is a member; raise NotAMember or MembershipUnavailable.

        `fresh` skips the cache: signing in always asks Telegram.
        """
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(user_id)
            now = self._clock()
            if not fresh and entry is not None and now - entry.checked_at < self._ttl:
                if entry.member:
                    return
                raise NotAMember
            try:
                member = await self._lookup(user_id)
            except MembershipUnavailable:
                confirmed = entry.confirmed_at if entry else None
                if fresh or confirmed is None or now - confirmed >= self._grace:
                    raise
                # Keep serving the last confirmed answer, but do not ask
                # Telegram again on every request while it is down.
                self._store(
                    user_id,
                    _Entry(
                        member=True,
                        checked_at=now - self._ttl + self._retry_after,
                        confirmed_at=confirmed,
                    ),
                )
                return
            self._store(
                user_id,
                _Entry(
                    member=member,
                    checked_at=now,
                    confirmed_at=now if member else None,
                ),
            )
            if not member:
                raise NotAMember

    def _store(self, user_id: int, entry: _Entry) -> None:
        if len(self._entries) >= self._max_entries and user_id not in self._entries:
            now = self._clock()
            for key in [
                k
                for k, v in self._entries.items()
                if now - v.checked_at >= self._ttl
                and (v.confirmed_at is None or now - v.confirmed_at >= self._grace)
            ]:
                self._entries.pop(key, None)
                lock = self._locks.get(key)
                if lock is not None and not lock.locked():
                    self._locks.pop(key, None)
            if len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k].checked_at)
                self._entries.pop(oldest, None)
        self._entries[user_id] = entry
