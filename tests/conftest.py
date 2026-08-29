import pytest

_SETTINGS_ENV_VARS = (
    "APP_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ADMIN_IDS",
    "TELEGRAM_TARGET_CHAT_ID",
    "TELEGRAM_TARGET_THREAD_ID",
    "TELEGRAM_PAYMENTS_THREAD_ID",
    "TELEGRAM_PIN_POLL",
    "SCHEDULE_TIMEZONE",
    "PAYMENT_PRICE_GEL",
    "BOOKING_DEADLINE_TIME",
    "PAYMENTS_VIA_PRIVATE_CHAT",
    "APP_HEALTH_HEARTBEAT_FILE",
    "APP_HEALTH_HEARTBEAT_INTERVAL_SECONDS",
    "APP_HEALTH_MAX_AGE_SECONDS",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
