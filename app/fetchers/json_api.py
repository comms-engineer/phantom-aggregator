from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class JsonApiFetcher(BaseFetcher):
    """Fetches a generic public JSON API endpoint."""

    name = "json_api"

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
                body = await response.json()

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self.source_name,
            "url": self.url,
            "preview": self._preview(body),
            "raw": body,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _preview(self, body: Any) -> dict[str, Any]:
        if isinstance(body, dict):
            keys = list(body.keys())[:10]
            return {"top_level": "dict", "keys": keys}
        if isinstance(body, list):
            return {
                "top_level": "list",
                "length": len(body),
                "sample": body[:3],
            }
        return {"top_level": type(body).__name__, "sample": str(body)[:500]}
