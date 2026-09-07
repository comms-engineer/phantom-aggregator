from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"
    data_dir: Path = Path("/app/data")
    fetch_interval_minutes: int = 15

    space_weather_url: str = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

    nomadnet_line_limit: int = 80
    nomadnet_max_lines: int = 80

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def nomadnet_pages_dir(self) -> Path:
        return self.data_dir / "nomadnet_pages"


settings = Settings()
