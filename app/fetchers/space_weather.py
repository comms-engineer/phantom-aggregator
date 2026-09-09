from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class SpaceWeatherFetcher(BaseFetcher):
    """Fetches and normalizes NOAA SWPC space weather telemetry."""

    name = "space_weather"

    def __init__(
        self,
        raw_dir: Path | None = None,
        *,
        k_index_url: str,
        solar_flux_url: str,
        forecast_url: str,
        name: str | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.k_index_url = k_index_url
        self.solar_flux_url = solar_flux_url
        self.forecast_url = forecast_url
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            k_index_body, solar_flux_body, forecast_body = await self._fetch_all(session)

        normalized = self._normalize_metrics(k_index_body, solar_flux_body, forecast_body)
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "NOAA SWPC",
            "normalized": normalized,
            "raw": {
                "k_index": k_index_body,
                "solar_flux": solar_flux_body,
                "forecast": forecast_body,
            },
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> Any:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()

    async def _fetch_all(self, session: aiohttp.ClientSession) -> tuple[Any, Any, Any]:
        return await asyncio.gather(
            self._fetch_json(session, self.k_index_url),
            self._fetch_json(session, self.solar_flux_url),
            self._fetch_json(session, self.forecast_url),
        )

    def _normalize_metrics(self, k_index_body: Any, solar_flux_body: Any, forecast_body: Any) -> dict[str, Any]:
        k_index = self._extract_latest_int(k_index_body, ("k_index", "kp"))
        sfi = self._extract_latest_int(solar_flux_body, ("flux", "f10.7", "sfi", "observed_flux"))
        geomagnetic_storm = self._extract_geomagnetic_storm(forecast_body)
        updated = self._extract_latest_time(k_index_body, ("time_tag", "date", "time"))

        return {
            "sfi": sfi,
            "k_index": k_index,
            "geomagnetic_storm": geomagnetic_storm,
            "updated": updated,
        }

    def _extract_latest_time(self, body: Any, candidate_fields: tuple[str, ...]) -> str:
        records = self._table_to_records(body)
        if not records:
            return datetime.now(UTC).isoformat()

        latest = records[-1]
        for field in candidate_fields:
            if field in latest and latest[field] is not None:
                return str(latest[field])
        return datetime.now(UTC).isoformat()

    def _extract_latest_int(self, body: Any, candidate_fields: tuple[str, ...]) -> int:
        records = self._table_to_records(body)
        if not records:
            return 0

        for record in reversed(records):
            value = self._get_first_numeric_value(record, candidate_fields)
            if value is not None:
                return int(round(value))
        return 0

    def _extract_geomagnetic_storm(self, forecast_body: Any) -> str:
        records = self._table_to_records(forecast_body)
        if not records:
            return "UNKNOWN"

        scale_rank = {"G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5}
        highest_scale, highest_rank = "NONE", 0

        for record in records:
            window = str(record.get("observed", "")).lower()
            if window not in ("predicted", "estimated"):  # skip pure history rows
                continue
            scale = record.get("noaa_scale")
            if not scale:
                continue
            rank = scale_rank.get(str(scale).upper(), 0)
            if rank > highest_rank:
                highest_rank, highest_scale = rank, str(scale).upper()

        return highest_scale

    def _table_to_records(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, list):
            if isinstance(body, dict):
                return [body]
            return []
        if not body:
            return []

        if isinstance(body[0], dict):
            return [row for row in body if isinstance(row, dict)]

        if isinstance(body[0], list) and len(body) >= 2:
            headers = [str(item) for item in body[0]]
            return [dict(zip(headers, row)) for row in body[1:] if isinstance(row, list)]

        return []

    def _get_first_numeric_value(self, record: dict[str, Any], candidate_fields: tuple[str, ...]) -> float | None:
        for field in candidate_fields:
            for key, value in record.items():
                if field.lower() in key.lower():
                    numeric = self._to_float(value)
                    if numeric is not None:
                        return numeric
        return None

    def _to_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None
