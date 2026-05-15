from veloexpress_bot.config import Settings


def test_settings_parses_admin_ids_from_csv() -> None:
    settings = Settings(
        _env_file=None,
        telegram_admin_ids="10, 20,30",
        telegram_bot_token="token",
    )

    assert settings.telegram_admin_ids == (10, 20, 30)


def test_settings_treats_empty_optional_telegram_ids_as_none() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="token",
        telegram_target_chat_id="",
        telegram_target_thread_id="",
    )

    assert settings.telegram_target_chat_id is None
    assert settings.telegram_target_thread_id is None
