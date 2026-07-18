from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendPoll

from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.telegram.client import AiogramTelegramClient
from veloexpress_bot.telegram.errors import TelegramTargetForbiddenError


class ForbiddenBot:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def send_poll(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        raise TelegramForbiddenError(
            method=SendPoll(
                chat_id=cast(int | str, kwargs["chat_id"]),
                question=str(kwargs["question"]),
                options=list(cast(tuple[str, ...], kwargs["options"])),
            ),
            message="Forbidden: bot was kicked from the supergroup chat",
        )


class EditingBot:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def edit_message_text(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return object()


class UnpinningBot:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def unpin_chat_message(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return object()


@pytest.mark.asyncio
async def test_send_poll_maps_forbidden_target_chat() -> None:
    bot = ForbiddenBot()
    client = AiogramTelegramClient(cast(Bot, bot))

    with pytest.raises(TelegramTargetForbiddenError) as error:
        await client.send_poll(
            chat_id=-100123,
            message_thread_id=7,
            draft=PollDraft(question="Question", options=("A", "B")),
        )

    assert error.value.telegram_message == "Forbidden: bot was kicked from the supergroup chat"
    assert bot.kwargs["chat_id"] == -100123
    assert bot.kwargs["message_thread_id"] == 7


@pytest.mark.asyncio
async def test_edit_text_updates_existing_telegram_message() -> None:
    bot = EditingBot()
    client = AiogramTelegramClient(cast(Bot, bot))

    updated = await client.edit_text(
        chat_id=-100123,
        message_id=42,
        text="Updated availability",
    )

    assert updated is True
    assert bot.kwargs == {
        "chat_id": -100123,
        "message_id": 42,
        "text": "Updated availability",
        "reply_markup": None,
    }


@pytest.mark.asyncio
async def test_unpin_message_targets_specific_poll() -> None:
    bot = UnpinningBot()
    client = AiogramTelegramClient(cast(Bot, bot))

    unpinned = await client.unpin_message(chat_id=-100123, message_id=42)

    assert unpinned is True
    assert bot.kwargs == {"chat_id": -100123, "message_id": 42}
