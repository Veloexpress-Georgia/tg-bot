from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://veloexpress:veloexpress@localhost:5432/veloexpress"

    telegram_bot_token: str = Field(default="", repr=False)
    telegram_admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    telegram_target_chat_id: int | None = None
    telegram_target_thread_id: int | None = None
    telegram_pin_poll: bool = True

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
