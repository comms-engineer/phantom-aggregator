from __future__ import annotations

import io
import json
import re
import zipfile
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


class WildfiresFetcher(BaseFetcher):
    """Fetches and normalizes active wildfire incident updates from RSS."""

    name = "wildfires"

    def __init__(
        self,
        raw_dir: Path | None = None,
        source_url: str | None = None,
        url: str | None = None,
        name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.feed_url = (source_url or url or "").strip()
        if not self.feed_url:
            raise ValueError("source_url or url is required for WildfiresFetcher")
        self.options = options or {}
        defaults = [
            "https://firms.modaps.eosdis.nasa.gov/api/kml_fire_footprints/usa_contiguous_and_hawaii/24h/c6.1/FirespotArea_usa_contiguous_and_hawaii_c6.1_24h.kmz",
            "https://firms.modaps.eosdis.nasa.gov/api/kml_fire_footprints/usa_contiguous_and_hawaii/24h/suomi-npp-viirs-c2/FirespotArea_usa_contiguous_and_hawaii_suomi-npp-viirs-c2_24h.kmz",
            "https://firms.modaps.eosdis.nasa.gov/api/kml_fire_footprints/usa_contiguous_and_hawaii/24h/noaa-20-viirs-c2/FirespotArea_usa_contiguous_and_hawaii_noaa-20-viirs-c2_24h.kmz",
            "https://firms.modaps.eosdis.nasa.gov/api/kml_fire_footprints/usa_contiguous_and_hawaii/24h/noaa-21-viirs-c2/FirespotArea_usa_contiguous_and_hawaii_noaa-21-viirs-c2_24h.kmz",
            "https://firms.modaps.eosdis.nasa.gov/api/kml_fire_footprints/usa_contiguous_and_hawaii/24h/landsat/FirespotArea_usa_contiguous_and_hawaii_landsat_24h.kmz",
        ]
        firms_urls = self.options.get("firms_urls", defaults)
        self.firms_urls = [str(item).strip() for item in firms_urls if str(item).strip()]
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            feed_task = self._fetch_text(session, self.feed_url)
            firms_tasks = [self._fetch_firms_summary(session, url) for url in self.firms_urls]
            feed_body, *firms_entries = await asyncio.gather(feed_task, *firms_tasks)

        incident_items = self._parse_feed(feed_body)
        firms_items = [item for entry in firms_entries for item in self._build_firms_items(entry)]
        items = self._correlate_and_dedupe(incident_items + firms_items)
        items.sort(key=self._sort_key, reverse=True)

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "InciWeb + NASA FIRMS",
            "url": self.feed_url,
            "firms_urls": self.firms_urls,
            "item_count": len(items),
            "items": items[:120],
            "raw": {
                "inciweb": feed_body,
                "firms": firms_entries,
            },
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

    async def _fetch_firms_summary(self, session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.read()
        except Exception:
            return {"url": url, "sensor": self._sensor_name(url), "feature_count": 0, "error": "fetch_failed"}

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
                if not names:
                    return {"url": url, "sensor": self._sensor_name(url), "feature_count": 0, "error": "missing_kml"}
                root = ET.fromstring(archive.read(names[0]))
                count = len(root.findall(".//{*}Placemark"))
                return {"url": url, "sensor": self._sensor_name(url), "feature_count": count, "error": None}
        except Exception:
            return {"url": url, "sensor": self._sensor_name(url), "feature_count": 0, "error": "invalid_kmz"}

    def _build_firms_items(self, summary: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(summary, dict):
            return []
        count = int(summary.get("feature_count", 0) or 0)
        if count <= 0:
            return []
        sensor = str(summary.get("sensor") or "NASA FIRMS").strip()
        severity = "critical" if count >= 100 else "high" if count >= 25 else "medium"
        rank = 6 if severity == "critical" else 5 if severity == "high" else 4
        return [{
            "title": f"NASA FIRMS active fire detections ({sensor})",
            "published": datetime.now(UTC).isoformat(),
            "source": "NASA FIRMS",
            "link": str(summary.get("url", "")),
            "body": f"{count} wildfire hotspot features were detected in the last 24 hours by {sensor}.",
            "fire_name": sensor,
            "event_type": "wildfire_hotspot",
            "severity": severity,
            "severity_rank": rank,
            "incident_group": "wildfire",
            "firms_sensor": sensor,
            "firms_hotspot_count": count,
        }]

    def _sensor_name(self, url: str) -> str:
        basename = url.rsplit("/", 1)[-1]
        if "landsat" in url:
            return "Landsat"
        if "noaa-21" in url:
            return "NOAA-21 VIIRS"
        if "noaa-20" in url:
            return "NOAA-20 VIIRS"
        if "suomi-npp" in url:
            return "S-NPP VIIRS"
        if "c6.1" in url:
            return "MODIS"
        return basename

    def _correlate_and_dedupe(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        incident_entries: list[dict[str, Any]] = []
        standalone: list[dict[str, Any]] = []

        for item in items:
            if str(item.get("event_type", "")).lower() == "wildfire_hotspot":
                standalone.append(item)
                continue
            incident_entries.append(item)

        merged: list[dict[str, Any]] = []
        for incident in incident_entries:
            merged.append(incident)

        for hotspot in standalone:
            matched = None
            hotspot_time = self._parse_date(hotspot.get("published"))
            for incident in incident_entries:
                incident_time = self._parse_date(incident.get("published"))
                if abs((hotspot_time - incident_time).total_seconds()) <= 7 * 24 * 60 * 60:
                    matched = incident
                    break
            if matched is None:
                merged.append(hotspot)
                continue
            self._merge_correlated_item(matched, hotspot)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in merged:
            marker = f"{str(item.get('title',''))}|{str(item.get('published',''))}|{str(item.get('link',''))}"
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped

    def _incident_key(self, item: dict[str, Any]) -> str | None:
        return None

    def _merge_correlated_item(self, base: dict[str, Any], item: dict[str, Any]) -> None:
        if item.get("event_type") == "wildfire_hotspot":
            base["firms_hotspot_count"] = int(base.get("firms_hotspot_count", 0) or 0) + int(item.get("firms_hotspot_count", 0) or 0)
            base["firms_sensors"] = sorted({
                *str(base.get("firms_sensors", "")).split("|"),
                *([str(item.get("firms_sensor", ""))] if item.get("firms_sensor") else []),
            })
            base["firms_sensors"] = "|".join(part for part in base["firms_sensors"] if part)
            if int(item.get("severity_rank", 0) or 0) > int(base.get("severity_rank", 0) or 0):
                base["severity_rank"] = int(item.get("severity_rank", 0) or 0)
                base["severity"] = item.get("severity", base.get("severity", "medium"))
            if not base.get("link"):
                base["link"] = item.get("link", "")
            base["body"] = f"{base.get('body', '')} | {item.get('body', '')}".strip(" | ")
            return

        if item.get("severity_rank", 0) and item.get("severity_rank", 0) > base.get("severity_rank", 0):
            base["severity_rank"] = item.get("severity_rank", 0)
            base["severity"] = item.get("severity", base.get("severity", "medium"))

    def _parse_feed(self, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item") + root.findall(".//{*}entry")

        items: list[dict[str, Any]] = []
        for entry in entries:
            title = self._first_text(entry, "title") or "Wildfire Incident"
            body = self._strip_html(self._first_text(entry, "description", "summary", "content") or "")
            published = self._first_text(entry, "pubDate", "published", "updated") or ""
            link = self._extract_link(entry)

            acres = self._extract_float(body, r"([\d,]+(?:\.\d+)?)\s*acres")
            containment = self._extract_float(body, r"([\d]{1,3}(?:\.\d+)?)\s*%\s*contain")
            evacuation = self._extract_evacuation(body)
            agency = self._extract_agency(body)
            fire_name = self._extract_fire_name(title)
            severity, rank = self._severity(body, containment, evacuation)

            items.append(
                {
                    "title": title,
                    "published": published,
                    "source": self._source_name(self.feed_url),
                    "link": link,
                    "body": body,
                    "fire_name": fire_name,
                    "event_type": "wildfire",
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "wildfire",
                    "acres_burned": acres,
                    "containment_pct": containment,
                    "evacuation_notice": evacuation,
                    "lead_agency": agency,
                }
            )
        return items

    def _severity(self, body: str, containment: float | None, evacuation: str) -> tuple[str, int]:
        lowered = body.lower()
        if evacuation and evacuation != "none":
            return "critical", 6
        if "extreme fire behavior" in lowered or "structures threatened" in lowered:
            return "high", 5
        if containment is not None and containment < 20:
            return "high", 5
        if containment is not None and containment < 50:
            return "medium", 4
        return "info", 3

    def _extract_evacuation(self, body: str) -> str:
        lowered = body.lower()
        if "mandatory evacuation" in lowered:
            return "mandatory"
        if "evacuation order" in lowered:
            return "order"
        if "evacuation warning" in lowered:
            return "warning"
        return "none"

    def _extract_agency(self, body: str) -> str:
        match = re.search(r"(?:agency|lead|incident command)\s*[:\-]\s*([A-Za-z0-9 ,.\-/]+)", body, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _extract_fire_name(self, title: str) -> str:
        match = re.search(r"([A-Za-z0-9'\- ]+?)\s+(?:fire|incident)\b", title, flags=re.IGNORECASE)
        if not match:
            return title
        return match.group(1).strip().title()

    def _extract_float(self, text: str, pattern: str) -> float | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        candidate = match.group(1).replace(",", "").strip()
        try:
            return float(candidate)
        except ValueError:
            return None

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

    def _source_name(self, feed_url: str) -> str:
        return urlparse(feed_url).netloc or "wildfire-source"
