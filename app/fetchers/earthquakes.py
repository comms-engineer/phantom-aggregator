from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class EarthquakesFetcher(BaseFetcher):
    """Fetches and normalizes USGS earthquake hazard telemetry."""

    name = "earthquakes"

    def __init__(
        self,
        raw_dir: Path | None = None,
        source_url: str | None = None,
        url: str | None = None,
        name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.source_url = (source_url or url or "").strip()
        if not self.source_url:
            raise ValueError("source_url or url is required for EarthquakesFetcher")
        self.options = options or {}
        self.min_magnitude = float(self.options.get("min_magnitude", 4.5))
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.source_url) as response:
                response.raise_for_status()
                body = await response.json()

        features = body.get("features", []) if isinstance(body, dict) else []
        items: list[dict[str, Any]] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
            geometry = feature.get("geometry", {}) if isinstance(feature.get("geometry"), dict) else {}
            coordinates = geometry.get("coordinates", []) if isinstance(geometry.get("coordinates"), list) else []

            magnitude = self._to_float(properties.get("mag"))
            if magnitude is None or magnitude < self.min_magnitude:
                continue

            event_time = self._epoch_ms_to_iso(properties.get("time"))
            depth_km = self._to_float(coordinates[2]) if len(coordinates) > 2 else None
            tsunami = bool(int(properties.get("tsunami", 0) or 0))
            place = str(properties.get("place", "Unknown location")).strip()
            title = str(properties.get("title", f"M{magnitude:.1f} earthquake")).strip()

            severity, rank = self._severity(magnitude, tsunami)
            items.append(
                {
                    "title": title,
                    "published": event_time,
                    "source": "USGS Earthquake Hazards",
                    "link": str(properties.get("url", "")).strip(),
                    "body": (
                        f"Magnitude {magnitude:.1f} at {place}. "
                        f"Depth: {depth_km:.1f} km. Tsunami flag: {'yes' if tsunami else 'no'}."
                    ),
                    "event_type": "earthquake",
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "earthquake",
                    "magnitude": magnitude,
                    "depth_km": depth_km,
                    "location": place,
                    "tsunami": tsunami,
                    "felt_reports": int(properties.get("felt", 0) or 0),
                }
            )

        items.sort(key=self._sort_key, reverse=True)
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self.source_url,
            "min_magnitude": self.min_magnitude,
            "item_count": len(items),
            "items": items[:200],
            "raw": body,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _severity(self, magnitude: float, tsunami: bool) -> tuple[str, int]:
        if tsunami or magnitude >= 7.5:
            return "critical", 6
        if magnitude >= 6.5:
            return "high", 5
        if magnitude >= 5.5:
            return "medium", 4
        return "info", 3

    def _sort_key(self, item: dict[str, Any]) -> tuple[int, datetime, float]:
        rank = int(item.get("severity_rank", 0) or 0)
        published = self._parse_date(item.get("published"))
        magnitude = float(item.get("magnitude", 0.0) or 0.0)
        return (rank, published, magnitude)

    def _parse_date(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=UTC)

    def _epoch_ms_to_iso(self, value: Any) -> str:
        numeric = self._to_float(value)
        if numeric is None:
            return ""
        return datetime.fromtimestamp(numeric / 1000, tz=UTC).isoformat()

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None
