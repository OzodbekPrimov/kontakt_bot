from __future__ import annotations

from pydantic import Field, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded only from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    admin_ids: list[PositiveInt] = Field(default_factory=list, alias="ADMIN_IDS")
