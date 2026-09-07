from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"
    data_dir: Path = Path("/app/data")
    request_timeout_seconds: int = 20
    llm_timeout_seconds: int = 10

    space_weather_interval_minutes: int = 30
    rss_interval_minutes: int = 60
    cyber_kev_interval_minutes: int = 360

    space_weather_k_index_url: str = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    space_weather_solar_flux_url: str = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
    space_weather_forecast_url: str = (
        "https://services.swpc.noaa.gov/products/noaa-geomagnetic-storm-forecast.json"
    )

    cisa_kev_url: str = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    rss_feed_urls: list[str] = [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.cisa.gov/cybersecurity-advisories/all.xml",
    ]

    enable_llm_summarization: bool = False
    llm_endpoint: str = "http://host.docker.internal:11434/api/generate"
    llm_model: str = "qwen2.5:7b"

    nomadnet_line_limit: int = 80
    nomadnet_max_lines: int = 80

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def nomadnet_dir(self) -> Path:
        return self.data_dir / "nomadnet"

    @property
    def nomadnet_pages_dir(self) -> Path:
        return self.nomadnet_dir


settings = Settings()
