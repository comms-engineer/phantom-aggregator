from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class SpaceWeatherFetcher(BaseFetcher):
    """Fetches and normalizes NOAA SWPC planetary K-index data."""

    name = "space_weather"

    def __init__(self, raw_dir: Path | None = None, source_url: str | None = None) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.source_url = source_url or settings.space_weather_url

    async def fetch(self) -> dict[str, Any]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(self.source_url) as response:
                response.raise_for_status()
                body = await response.json()

        if not isinstance(body, list) or len(body) < 2:
            raise ValueError("Unexpected NOAA SWPC response format")

        headers = body[0]
        entries = body[1:]
        if not isinstance(headers, list):
            raise ValueError("Missing header row in NOAA SWPC response")

        records: list[dict[str, Any]] = []
        for row in entries[-20:]:
            if isinstance(row, list):
                records.append(dict(zip(headers, row)))

        latest = records[-1] if records else {}
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self.source_url,
            "record_count": len(records),
            "latest": latest,
            "records": records,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
