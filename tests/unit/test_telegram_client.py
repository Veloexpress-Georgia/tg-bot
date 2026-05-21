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
