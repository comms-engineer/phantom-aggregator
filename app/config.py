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
    raw_data_max_age_days: int = 14
    system_health_page_name: str = "system.page"
    system_health_snapshot_name: str = "system_health"
    critical_alert_k_index_threshold: int = 5
    critical_alert_keywords: tuple[str, ...] = (
        "actively exploited",
        "critical",
        "emergency",
        "evacuation",
        "mass casualty",
        "ransomware",
        "remote code execution",
        "shelter in place",
        "state of emergency",
        "zero day",
        "zero-day",
    )
    lxmf_alerting_enabled: bool = False
    lxmf_alert_destinations: tuple[str, ...] = ()
    lxmf_alert_command: tuple[str, ...] = ()
    mesh_interface_names: tuple[str, ...] = (
        "ax25",
        "mesh",
        "packet",
        "reticulum",
        "rns",
        "rnode",
    )
    internet_connectivity_host: str = "1.1.1.1"
    internet_connectivity_port: int = 53

    enable_llm_summarization: bool = False
    llm_endpoint: str = "http://host.docker.internal:11434/api/generate"
    llm_model: str = "qwen2.5:7b"

    nomadnet_line_limit: int = 80
    nomadnet_max_lines: int = 80

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _resolve_runtime_dir(self, configured: Path, local_name: str) -> Path:
        local_path = self.project_root / local_name
        default_container_path = Path(f"/app/{local_name}")
        if configured != default_container_path:
            return configured
        if configured.exists() or not local_path.exists():
            return configured
        return local_path

    @property
    def raw_dir(self) -> Path:
        return self._resolve_runtime_dir(self.data_dir, "data") / "raw"

    @property
    def nomadnet_dir(self) -> Path:
        return self._resolve_runtime_dir(self.data_dir, "data") / "nomadnet"

    @property
    def runtime_data_dir(self) -> Path:
        return self._resolve_runtime_dir(self.data_dir, "data")

    @property
    def nomadnet_pages_dir(self) -> Path:
        return self.nomadnet_dir

    @property
    def sources_config_path(self) -> Path:
        return self._resolve_runtime_dir(self.config_dir, "config") / "sources.json"


settings = Settings()
