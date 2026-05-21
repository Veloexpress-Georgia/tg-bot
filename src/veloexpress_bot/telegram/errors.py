class TelegramPollPostError(RuntimeError):
    def __init__(self, message: str, *, telegram_message: str | None = None) -> None:
        super().__init__(message)
        self.telegram_message = telegram_message


class TelegramTargetForbiddenError(TelegramPollPostError):
    pass
