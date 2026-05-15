import pytest
from pydantic import ValidationError

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


def test_settings_rejects_localhost_database_in_production() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must point to a production"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://veloexpress:veloexpress@localhost:5432/veloexpress",
            telegram_bot_token="token",
        )


def test_settings_derives_internal_database_url_in_production() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        postgres_db="veloexpress",
        postgres_user="veloexpress",
        postgres_password="secret",
        postgres_host="db",
        telegram_bot_token="token",
    )

    assert settings.database_url == "postgresql+asyncpg://veloexpress:secret@db:5432/veloexpress"


def test_settings_allows_service_database_host_in_production() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://veloexpress:veloexpress@postgres:5432/veloexpress",
        telegram_bot_token="token",
    )

    assert settings.database_url.endswith("@postgres:5432/veloexpress")
