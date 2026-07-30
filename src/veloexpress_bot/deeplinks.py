"""Deep links into a private supergroup.

Private groups have no username, so links go through the `t.me/c/<id>` form where
`<id>` is the chat id with the `-100` supergroup prefix stripped.
"""

from __future__ import annotations

SUPERGROUP_PREFIX = "-100"


def topic_link(*, chat_id: int, thread_id: int | None) -> str | None:
    internal = _internal_chat_id(chat_id)
    if internal is None or thread_id is None:
        return None
    return f"https://t.me/c/{internal}/{thread_id}"


def message_link(*, chat_id: int, thread_id: int | None, message_id: int) -> str | None:
    internal = _internal_chat_id(chat_id)
    if internal is None:
        return None
    if thread_id is None:
        return f"https://t.me/c/{internal}/{message_id}"
    return f"https://t.me/c/{internal}/{thread_id}/{message_id}"


def _internal_chat_id(chat_id: int) -> str | None:
    text = str(chat_id)
    if not text.startswith(SUPERGROUP_PREFIX):
        # A plain group or a private chat has no shareable t.me/c link.
        return None
    return text.removeprefix(SUPERGROUP_PREFIX)
