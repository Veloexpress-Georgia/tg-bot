from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WEB_", extra="ignore")
    session_secret: str = Field(default="", repr=False)
    public_url: str = "http://localhost:5173"
    telegram_client_id: str = ""
    telegram_client_secret: str = Field(default="", repr=False)
    session_seconds: int = Field(default=43200, ge=300, le=86400)

    @model_validator(mode="after")
    def validate_url(self) -> WebSettings:
        parts = urlsplit(self.public_url)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.netloc
            or parts.username
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("WEB_PUBLIC_URL must be an origin, e.g. https://app.example.com")
        if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Public deployments require HTTPS")
        self.public_url = self.public_url.rstrip("/")
        return self

    @property
    def secure(self) -> bool:
        return self.public_url.startswith("https://")
