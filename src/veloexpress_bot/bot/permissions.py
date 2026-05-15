from veloexpress_bot.config import Settings


def is_admin(user_id: int | None, settings: Settings) -> bool:
    return user_id is not None and user_id in settings.telegram_admin_ids
