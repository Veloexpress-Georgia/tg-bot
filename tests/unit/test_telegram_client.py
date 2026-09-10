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
        "parse_mode": None,
    }


@pytest.mark.asyncio
async def test_unpin_message_targets_specific_poll() -> None:
    bot = UnpinningBot()
    client = AiogramTelegramClient(cast(Bot, bot))

    unpinned = await client.unpin_message(chat_id=-100123, message_id=42)

    assert unpinned is True
    assert bot.kwargs == {"chat_id": -100123, "message_id": 42}


@pytest.mark.parametrize(
    "error_text, expected",
    [
        (None, True),
        ("Bad Request: message is not modified", True),
        ("Bad Request: message to edit not found", True),
        ("Bad Request: message can't be edited", False),
        ("Bad Request: chat not found", False),
    ],
)
async def test_clear_keyboard_handles_missing_messages_and_retries_errors(
    error_text: str | None,
    expected: bool,
) -> None:
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageReplyMarkup

    class KeyboardBot:
        async def edit_message_reply_markup(self, **kwargs: object) -> object:
            assert kwargs == {"chat_id": -100123, "message_id": 42, "reply_markup": None}
            if error_text is not None:
                raise TelegramBadRequest(
                    method=EditMessageReplyMarkup(chat_id=-100123, message_id=42),
                    message=error_text,
                )
            return object()

    client = AiogramTelegramClient(cast(Bot, KeyboardBot()))
    assert await client.clear_keyboard(chat_id=-100123, message_id=42) is expected


async def test_transient_edit_failure_is_not_reported_as_a_missing_message() -> None:
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import EditMessageText

    from veloexpress_bot.telegram.errors import TelegramPollPostError

    class OfflineBot:
        async def edit_message_text(self, **kwargs: object) -> object:
            raise TelegramNetworkError(
                method=EditMessageText(chat_id=1, message_id=42, text="test"), message="offline"
            )

    with pytest.raises(TelegramPollPostError):
        await AiogramTelegramClient(cast(Bot, OfflineBot())).edit_text(
            chat_id=1, message_id=42, text="test"
        )


async def test_failed_poll_probe_is_not_evidence_of_deletion() -> None:
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import EditMessageReplyMarkup

    class OfflineBot:
        async def edit_message_reply_markup(self, **kwargs: object) -> object:
            raise TelegramNetworkError(
                method=EditMessageReplyMarkup(chat_id=1, message_id=42), message="offline"
            )

    assert await AiogramTelegramClient(cast(Bot, OfflineBot())).message_exists(
        chat_id=1, message_id=42
    )
