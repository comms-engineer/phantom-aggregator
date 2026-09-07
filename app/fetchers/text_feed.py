from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class TextFeedFetcher(BaseFetcher):
    """Fetches a plain-text endpoint and captures a lightweight preview."""

    name = "text_feed"

    def __init__(self, url: str, source_name: str, raw_dir: Path | None = None, name: str | None = None) -> None:
        self.url = url
        self.source_name = source_name
        self.raw_dir = raw_dir or settings.raw_dir
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.url) as response:
                response.raise_for_status()
                body = await response.text()

        lines = [line.strip() for line in body.splitlines() if line.strip()]
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self.source_name,
            "url": self.url,
            "line_count": len(lines),
            "lines": lines[:25],
            "body": body[:5000],
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
