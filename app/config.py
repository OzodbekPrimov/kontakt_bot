from __future__ import annotations

from pathlib import Path

from pydantic import Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded only from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    admin_ids: list[PositiveInt] = Field(default_factory=list, alias="ADMIN_IDS")
    lookup_enabled: bool = Field(default=False, alias="LOOKUP_ENABLED")
    lookup_api_id: PositiveInt | None = Field(default=None, alias="LOOKUP_API_ID")
    lookup_api_hash: SecretStr | None = Field(default=None, alias="LOOKUP_API_HASH")
    lookup_session: SecretStr | None = Field(default=None, alias="LOOKUP_SESSION")
    lookup_session_file: Path | None = Field(default=None, alias="LOOKUP_SESSION_FILE")
    lookup_open_access: bool = Field(default=True, alias="LOOKUP_OPEN_ACCESS")
    lookup_free_limit: int = Field(default=5, ge=0, alias="LOOKUP_FREE_LIMIT")

    @model_validator(mode="after")
    def require_lookup_credentials(self):
        if self.lookup_enabled and not (
            self.lookup_api_id
            and self.lookup_api_hash
            and (self.lookup_session or self.lookup_session_file)
        ):
            raise ValueError(
                "LOOKUP_ENABLED requires API credentials and a user session or session file"
            )
        return self
