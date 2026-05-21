import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

from veloexpress_bot.polls.render import PollDraft
from veloexpress_bot.polls.service import SentPollMessage
from veloexpress_bot.telegram.errors import TelegramPollPostError, TelegramTargetForbiddenError

logger = logging.getLogger(__name__)


class AiogramTelegramClient:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_poll(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        draft: PollDraft,
    ) -> SentPollMessage:
        try:
            message = await self._bot.send_poll(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                question=draft.question,
                options=list(draft.options),
                is_anonymous=draft.is_anonymous,
                allows_multiple_answers=draft.allows_multiple_answers,
            )
        except TelegramForbiddenError as error:
            logger.warning(
                "Telegram target chat rejected poll posting",
                extra={"chat_id": chat_id, "message_thread_id": message_thread_id},
            )
            raise TelegramTargetForbiddenError(
                "Telegram target chat rejected poll posting.",
                telegram_message=error.message,
            ) from error
        except TelegramAPIError as error:
            logger.exception(
                "Failed to send poll",
                extra={"chat_id": chat_id, "message_thread_id": message_thread_id},
            )
            raise TelegramPollPostError(
                "Telegram failed to create poll.",
                telegram_message=error.message,
            ) from error
        return SentPollMessage(
            message_id=message.message_id,
            poll_id=message.poll.id if message.poll else None,
        )

    async def pin_message(self, *, chat_id: int, message_id: int) -> bool:
        try:
            await self._bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message_id,
                disable_notification=True,
            )
        except TelegramAPIError:
            logger.exception("Failed to pin poll message", extra={"message_id": message_id})
            return False
        return True

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramAPIError:
            logger.exception("Failed to delete setup message", extra={"message_id": message_id})
            return False
        return True

    async def message_exists(self, *, chat_id: int, message_id: int) -> bool:
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as error:
            error_text = error.message.lower()
            if "message to edit not found" in error_text or "message not found" in error_text:
                return False
            if "message is not modified" in error_text or "message can't be edited" in error_text:
                return True
            logger.exception(
                "Failed to verify poll message existence",
                extra={"message_id": message_id},
            )
            return True
        except TelegramAPIError:
            logger.exception(
                "Failed to verify poll message existence",
                extra={"message_id": message_id},
            )
            return True
        return True
