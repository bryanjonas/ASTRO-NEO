"""Configuration for the mock NINA service."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    port: int = 1888
    data_dir: Path = Path("/data")
    exposure_seconds: float = 5.0
    min_alt_deg: float = 5.0
    fail_rate: float = 0.0
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="MOCK_NINA_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    def ensure_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_paths()

__all__ = ["settings", "Settings"]
