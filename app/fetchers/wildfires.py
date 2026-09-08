from __future__ import annotations

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
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.feed_url) as response:
                response.raise_for_status()
                body = await response.text()

        items = self._parse_feed(body)
        items.sort(key=self._sort_key, reverse=True)

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": self._source_name(self.feed_url),
            "url": self.feed_url,
            "item_count": len(items),
            "items": items[:120],
            "raw": body,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
