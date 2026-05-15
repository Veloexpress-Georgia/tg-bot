from veloexpress_bot.bot.permissions import is_admin
from veloexpress_bot.config import Settings


def test_is_admin_uses_allowlist() -> None:
    settings = Settings(_env_file=None, telegram_admin_ids=(11, 22), telegram_bot_token="token")

    assert is_admin(11, settings) is True
    assert is_admin(33, settings) is False
    assert is_admin(None, settings) is False
