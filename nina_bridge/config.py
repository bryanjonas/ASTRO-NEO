"""Bridge service configuration."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeSettings(BaseSettings):
    """Runtime configuration for the bridge service."""

    nina_base_url: str = "http://mock-nina:1888/api"
    http_timeout: float = 15.0
    max_retries: int = 3
    require_weather_safe: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="NINA_BRIDGE_")


settings = BridgeSettings()

__all__ = ["settings", "BridgeSettings"]
