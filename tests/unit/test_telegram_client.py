from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import EditMessageMedia, SendPoll, StopPoll
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

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


class CardBot:
    def __init__(self, error: str | None = None) -> None:
        self.kwargs: dict[str, object] = {}
        self.error = error

    async def send_photo(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return type("Message", (), {"message_id": 71})()

    async def edit_message_media(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error:
            raise TelegramBadRequest(
                method=EditMessageMedia(
                    chat_id=-100123,
                    message_id=71,
                    media=cast(InputMediaPhoto, kwargs["media"]),
                ),
                message=self.error,
            )
        return object()


class UnpinningBot:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def unpin_chat_message(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return object()


class StoppingBot:
    def __init__(self, error: str | None = None) -> None:
        self.kwargs: dict[str, object] = {}
        self.error = error

    async def stop_poll(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error:
            raise TelegramBadRequest(
                method=StopPoll(chat_id=-100123, message_id=42), message=self.error
            )
        return object()


@pytest.mark.parametrize(
    "error_text, expected",
    [
        (None, True),
        ("Bad Request: poll has already been closed", True),
        ("Bad Request: message to stop poll not found", True),
        ("Bad Request: not enough rights", False),
    ],
)
async def test_stop_poll_is_safe_to_retry(error_text: str | None, expected: bool) -> None:
    bot = StoppingBot(error_text)
    client = AiogramTelegramClient(cast(Bot, bot))

    assert await client.stop_poll(chat_id=-100123, message_id=42) is expected
    assert bot.kwargs == {"chat_id": -100123, "message_id": 42}


async def test_stop_poll_keeps_api_failure_retryable() -> None:
    class ForbiddenStoppingBot:
        async def stop_poll(self, **kwargs: object) -> object:
            raise TelegramForbiddenError(
                method=StopPoll(chat_id=-100123, message_id=42), message="Forbidden"
            )

    client = AiogramTelegramClient(cast(Bot, ForbiddenStoppingBot()))
    assert await client.stop_poll(chat_id=-100123, message_id=42) is False


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
@pytest.mark.parametrize("caption", ["Availability", ""])
async def test_card_is_sent_with_caption_and_can_replace_a_text_message(caption: str) -> None:
    bot = CardBot()
    client = AiogramTelegramClient(cast(Bot, bot))

    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="I paid", callback_data="pay:paid:20261004")]]
    )
    sent = await client.send_availability_card(
        chat_id=-100123,
        message_thread_id=7,
        image=b"png",
        caption=caption,
        reply_markup=markup,
    )
    assert sent.message_id == 71
    assert isinstance(bot.kwargs["photo"], BufferedInputFile)
    assert bot.kwargs["caption"] == caption
    assert bot.kwargs["reply_markup"] == markup
    assert bot.kwargs["parse_mode"] == "HTML"

    assert await client.edit_availability_card(
        chat_id=-100123, message_id=71, image=b"new png", caption=caption, reply_markup=markup
    )
    media = cast(InputMediaPhoto, bot.kwargs["media"])
    assert isinstance(media.media, BufferedInputFile)
    assert media.caption == caption
    assert bot.kwargs["reply_markup"] == markup


@pytest.mark.parametrize(
    "error_text, expected",
    [
        ("Bad Request: message is not modified", True),
        ("Bad Request: message to edit not found", False),
    ],
)
async def test_card_edit_distinguishes_noop_from_deleted_message(
    error_text: str, expected: bool
) -> None:
    bot = CardBot(error_text)
    client = AiogramTelegramClient(cast(Bot, bot))
    assert (
        await client.edit_availability_card(
            chat_id=-100123, message_id=71, image=b"png", caption="Availability"
        )
        is expected
    )


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
