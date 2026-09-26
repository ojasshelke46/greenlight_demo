from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: a blank line copied from .env.example means "unset", not "empty string".
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    trueforge_base_url: str = "http://localhost:8790"
    trueforge_agent_name: str = "greenlight"
    greenlight_api_key: str
    frontend_origin: str
    github_bot_token: str
    github_bot_login: str = "greenlight-agent"
    ledger_path: str
    # Owned by the fleet and policy features; None means the feature's own default.
    fleet_max_repos: int | None = None
    policy_file_path: str | None = None

    tfy_gateway_base_url: str | None = None
    tfy_gateway_api_key: str | None = None
    tfy_trace_base_url: str | None = None
    inr_per_usd: float = 83.0
    ledger_hmac_key: str | None = None

    # .env.example lists these with no value; an empty line means "not set", not "".
    @field_validator(
        "tfy_gateway_base_url", "tfy_gateway_api_key", "tfy_trace_base_url", "inr_per_usd", "ledger_hmac_key",
        mode="before",
    )
    @classmethod
    def _empty_is_default(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            raise PydanticUseDefault()
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
