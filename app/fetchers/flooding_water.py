from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import aiohttp
from dateutil.parser import isoparse

from app.config import settings
from app.fetchers.base import BaseFetcher


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        return unescape(" ".join(chunk.strip() for chunk in self._chunks if chunk.strip()))


class FloodingWaterFetcher(BaseFetcher):
    """Fetches river gauge exceedances and flood warning headlines."""

    name = "flooding_water"

    def __init__(
        self,
        raw_dir: Path | None = None,
        source_url: str | None = None,
        url: str | None = None,
        name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.rss_url = (source_url or url or "").strip()
        if not self.rss_url:
            raise ValueError("source_url or url is required for FloodingWaterFetcher")
        self.options = options or {}
        self.usgs_url = str(
            self.options.get(
                "usgs_gauge_url",
                "https://waterservices.usgs.gov/nwis/iv/?format=json&parameterCd=00065&siteStatus=all",
            )
        )
        self.flood_stage_ft = float(self.options.get("flood_stage_ft", 10.0))
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            gauge_body, rss_body = await asyncio.gather(
                self._fetch_json(session, self.usgs_url),
                self._fetch_text(session, self.rss_url),
            )

        gauge_items = self._parse_gauge_exceedances(gauge_body)
        rss_items = self._parse_rss_alerts(rss_body)
        items = sorted(gauge_items + rss_items, key=self._sort_key, reverse=True)

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "USGS + NOAA/NWPS",
            "rss_url": self.rss_url,
            "usgs_url": self.usgs_url,
            "flood_stage_ft": self.flood_stage_ft,
            "item_count": len(items),
            "items": items[:120],
            "raw": {
                "usgs": gauge_body,
                "rss": rss_body,
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

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

    def _parse_gauge_exceedances(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []
        value = body.get("value", {})
        if not isinstance(value, dict):
            return []
        series = value.get("timeSeries", [])
        if not isinstance(series, list):
            return []

        items: list[dict[str, Any]] = []
        for stream in series:
            if not isinstance(stream, dict):
                continue
            latest = self._latest_gauge_observation(stream)
            if not latest:
                continue
            level_ft = latest.get("level_ft")
            if level_ft is None or level_ft < self.flood_stage_ft:
                continue

            site = stream.get("sourceInfo", {}) if isinstance(stream.get("sourceInfo"), dict) else {}
            site_name = str(site.get("siteName", "USGS Gauge")).strip()
            site_code = ""
            site_codes = site.get("siteCode", []) if isinstance(site.get("siteCode"), list) else []
            if site_codes and isinstance(site_codes[0], dict):
                site_code = str(site_codes[0].get("value", "")).strip()
            site_url = f"https://waterdata.usgs.gov/monitoring-location/{site_code}" if site_code else ""

            severity = "critical" if level_ft >= self.flood_stage_ft + 5 else "high"
            rank = 6 if severity == "critical" else 5
            items.append(
                {
                    "title": f"River gauge above flood stage: {site_name}",
                    "published": latest.get("timestamp", ""),
                    "source": "USGS Water Data",
                    "link": site_url,
                    "body": f"Gauge height {level_ft:.2f} ft exceeds configured flood stage {self.flood_stage_ft:.2f} ft.",
                    "event_type": "river_gauge_exceedance",
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "flooding",
                    "river_level_ft": level_ft,
                    "flood_stage_ft": self.flood_stage_ft,
                    "site_code": site_code,
                }
            )
        return items

    def _latest_gauge_observation(self, stream: dict[str, Any]) -> dict[str, Any] | None:
        values = stream.get("values", [])
        if not isinstance(values, list):
            return None
        for block in values:
            if not isinstance(block, dict):
                continue
            observations = block.get("value", [])
            if not isinstance(observations, list) or not observations:
                continue
            latest = observations[-1]
            if not isinstance(latest, dict):
                continue
            level = self._to_float(latest.get("value"))
            timestamp = str(latest.get("dateTime", "")).strip()
            if level is None:
                continue
            return {"level_ft": level, "timestamp": timestamp}
        return None

    def _parse_rss_alerts(self, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item") + root.findall(".//{*}entry")

        items: list[dict[str, Any]] = []
        for entry in entries:
            title = self._first_text(entry, "title") or "Flood Alert"
            body = self._strip_html(self._first_text(entry, "description", "summary", "content") or "")
            published = self._first_text(entry, "pubDate", "published", "updated") or ""
            link = self._extract_link(entry)
            lowered = f"{title} {body}".lower()
            if not any(keyword in lowered for keyword in ("flood", "high water", "flash flood", "river")):
                continue

            severity = "critical" if "flash flood warning" in lowered else "high"
            rank = 6 if severity == "critical" else 5
            items.append(
                {
                    "title": title,
                    "published": published,
                    "source": urlparse(self.rss_url).netloc or "nwps",
                    "link": link,
                    "body": body,
                    "event_type": "flood_warning" if "warning" in lowered else "flood_advisory",
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "flooding",
                }
            )
        return items

    def _first_text(self, element: ET.Element, *candidate_tags: str) -> str | None:
        tags = {candidate.lower() for candidate in candidate_tags}
        for child in element.iter():
            if self._local_name(child.tag).lower() in tags:
                text = " ".join("".join(child.itertext()).split())
                if text:
                    return text
        return None

    def _extract_link(self, entry: ET.Element) -> str:
        for child in list(entry):
            if self._local_name(child.tag).lower() != "link":
                continue
            href = child.attrib.get("href", "").strip()
            if href:
                return href
            value = "".join(child.itertext()).strip()
            if value:
                return value
        return ""

    def _strip_html(self, value: str) -> str:
        parser = _HTMLStripper()
        parser.feed(value)
        text = parser.text()
        parser.close()
        return " ".join(text.split())

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _sort_key(self, item: dict[str, Any]) -> tuple[int, datetime, str]:
        rank = int(item.get("severity_rank", 0) or 0)
        published = self._parse_date(item.get("published"))
        return (rank, published, str(item.get("title", "")))

    def _parse_date(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = isoparse(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return datetime(1970, 1, 1, tzinfo=UTC)

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
