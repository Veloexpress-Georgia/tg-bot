from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+asyncpg://veloexpress:veloexpress@localhost:5432/veloexpress"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = ""
    postgres_db: str = "veloexpress"
    postgres_user: str = "veloexpress"
    postgres_password: str = Field(default="veloexpress", repr=False)
    postgres_host: str = "db"

    telegram_bot_token: str = Field(default="", repr=False)
    telegram_admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    telegram_target_chat_id: int | None = None
    telegram_target_thread_id: int | None = None
    telegram_pin_poll: bool = True
    schedule_timezone: str = "Asia/Tbilisi"

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: Any) -> tuple[int, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return tuple(int(part.strip()) for part in value.split(",") if part.strip())
        if isinstance(value, int):
            return (value,)
        return tuple(int(item) for item in value)

    @field_validator("telegram_target_chat_id", "telegram_target_thread_id", mode="before")
    @classmethod
    def parse_optional_int(cls, value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    @model_validator(mode="after")
    def resolve_database_url(self) -> Settings:
        if not self.database_url:
            self.database_url = (
                _internal_database_url(self) if self.app_env == "production" else LOCAL_DATABASE_URL
            )
        if self.app_env == "production" and _is_local_database_url(self.database_url):
            msg = "DATABASE_URL must point to a production Postgres host when APP_ENV=production."
            raise ValueError(msg)
        return self


def _internal_database_url(settings: Settings) -> str:
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:5432/{settings.postgres_db}"
    )


def _is_local_database_url(database_url: str) -> bool:
    return "@localhost" in database_url or "@127.0.0.1" in database_url or "@::1" in database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
