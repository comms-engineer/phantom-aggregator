from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"
    data_dir: Path = Path("/app/data")
    config_dir: Path = Path("/app/config")
    request_timeout_seconds: int = 20
    llm_timeout_seconds: int = 10
    source_sync_interval_seconds: int = 60

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

    @property
    def sources_config_path(self) -> Path:
        return self.config_dir / "sources.json"


settings = Settings()
