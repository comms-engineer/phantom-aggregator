from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
from dateutil.parser import isoparse

from app.config import settings
from app.fetchers.base import BaseFetcher


class CyberKevFetcher(BaseFetcher):
    """Fetches and normalizes CISA Known Exploited Vulnerabilities data."""

    name = "cyber_kev"

    def __init__(self, raw_dir: Path | None = None, source_url: str | None = None, name: str | None = None) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        if not source_url:
            raise ValueError("source_url is required for CyberKevFetcher")
        self.source_url = source_url
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.source_url) as response:
                response.raise_for_status()
                body = await response.json()

        vulnerabilities = body.get("vulnerabilities", []) if isinstance(body, dict) else []
        recent = sorted(
            [entry for entry in vulnerabilities if isinstance(entry, dict)],
            key=lambda item: self._parse_date(item.get("dateAdded")),
            reverse=True,
        )[:5]

        normalized = [
            {
                "cveID": item.get("cveID", "n/a"),
                "vendorProject": item.get("vendorProject", "n/a"),
                "vulnerabilityName": item.get("vulnerabilityName", "n/a"),
                "dateAdded": item.get("dateAdded", "n/a"),
            }
            for item in recent
        ]

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self.source_url,
            "count_total": len(vulnerabilities),
            "latest_five": normalized,
            "raw": body,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _parse_date(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = isoparse(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return datetime(1970, 1, 1, tzinfo=UTC)
