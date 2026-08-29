from typing import Any, cast

import pytest
from pydantic import ValidationError

from veloexpress_bot.config import Settings


def make_settings(**kwargs: object) -> Settings:
    settings_factory = cast(Any, Settings)
    return settings_factory(_env_file=None, **kwargs)


def test_settings_parses_admin_ids_from_csv() -> None:
    settings = make_settings(
        telegram_admin_ids="10, 20,30",
        telegram_bot_token="token",
    )

    assert settings.telegram_admin_ids == (10, 20, 30)


def test_settings_treats_empty_optional_telegram_ids_as_none() -> None:
    settings = make_settings(
        telegram_bot_token="token",
        telegram_target_chat_id="",
        telegram_target_thread_id="",
    )

    assert settings.telegram_target_chat_id is None
    assert settings.telegram_target_thread_id is None


def test_settings_rejects_localhost_database_in_production() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL must point to a production"):
        make_settings(
            app_env="production",
            database_url="postgresql+asyncpg://veloexpress:veloexpress@localhost:5432/veloexpress",
            telegram_bot_token="token",
        )


def test_settings_derives_internal_database_url_in_production() -> None:
    settings = make_settings(
        app_env="production",
        postgres_db="veloexpress",
        postgres_user="veloexpress",
        postgres_password="secret",
        postgres_host="db",
        telegram_bot_token="token",
    )

    assert settings.database_url == "postgresql+asyncpg://veloexpress:secret@db:5432/veloexpress"


def test_settings_allows_service_database_host_in_production() -> None:
    settings = make_settings(
        app_env="production",
        database_url="postgresql+asyncpg://veloexpress:veloexpress@postgres:5432/veloexpress",
        telegram_bot_token="token",
    )

    assert settings.database_url.endswith("@postgres:5432/veloexpress")


@pytest.mark.parametrize("price", [0, -1])
def test_settings_rejects_non_positive_payment_price(price: int) -> None:
    with pytest.raises(ValidationError, match="PAYMENT_PRICE_GEL"):
        make_settings(payment_price_gel=price)


@pytest.mark.parametrize("deadline", ["8", "24:00", "20:60", "later"])
def test_settings_rejects_invalid_booking_deadline(deadline: str) -> None:
    with pytest.raises(ValidationError, match="BOOKING_DEADLINE_TIME"):
        make_settings(booking_deadline_time=deadline)


def test_settings_normalizes_booking_deadline() -> None:
    assert make_settings(booking_deadline_time="8:05").booking_deadline_time == "08:05"


def test_settings_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="SCHEDULE_TIMEZONE"):
        make_settings(schedule_timezone="Mars/Olympus")
