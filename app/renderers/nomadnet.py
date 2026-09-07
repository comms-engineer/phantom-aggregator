from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings


class NomadNetRenderer:
    """Renders normalized feed payloads into lightweight NomadNet-friendly text."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or settings.nomadnet_pages_dir

    def render_space_weather(self, payload: dict[str, Any]) -> str:
        latest = payload.get("latest") or {}
        lines = [
            "# SPACE WEATHER",
            f"Updated: {payload.get('fetched_at', 'unknown')}",
            f"Records: {payload.get('record_count', 0)}",
            "",
            f"Observed: {latest.get('time_tag', 'n/a')}",
            f"K-Index: {latest.get('k_index', 'n/a')}",
            f"A-Index: {latest.get('a_running_24hr', 'n/a')}",
            f"Station Count: {latest.get('station_count', 'n/a')}",
            f"NOAA Scale: {latest.get('noaa_scale', 'n/a')}",
            "",
            "Source: NOAA SWPC",
        ]
        return self._normalize(lines)

    def write_page(self, page_name: str, content: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / page_name
        path.write_text(content, encoding="utf-8")
        return path

    def _normalize(self, lines: list[str]) -> str:
        line_limit = settings.nomadnet_line_limit
        normalized = [line[:line_limit] for line in lines]
        return "\n".join(normalized[: settings.nomadnet_max_lines]).strip() + "\n"
