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


class NhcHurricanesFetcher(BaseFetcher):
    """Fetches and normalizes NHC tropical cyclone advisories."""

    name = "nhc_hurricanes"

    def __init__(
        self,
        raw_dir: Path | None = None,
        source_url: str | None = None,
        url: str | None = None,
        name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.primary_feed_url = (source_url or url or "").strip()
        if not self.primary_feed_url:
            raise ValueError("source_url or url is required for NhcHurricanesFetcher")
        self.options = options or {}
        extra_urls = self.options.get("feed_urls", [])
        self.feed_urls = [self.primary_feed_url, *[str(item).strip() for item in extra_urls if str(item).strip()]]
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            feed_bodies = await asyncio.gather(*[self._fetch_text(session, feed_url) for feed_url in self.feed_urls])

        items: list[dict[str, Any]] = []
        for feed_url, body in zip(self.feed_urls, feed_bodies, strict=False):
            items.extend(self._parse_feed(feed_url, body))

        deduped = self._dedupe_items(items)
        deduped.sort(key=self._sort_key, reverse=True)

        active_storms = sorted(
            {
                item.get("storm_name", "Unknown")
                for item in deduped
                if isinstance(item.get("storm_name"), str) and item.get("storm_name")
            }
        )

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "NOAA National Hurricane Center",
            "feeds": self.feed_urls,
            "item_count": len(deduped),
            "active_storms": active_storms,
            "items": deduped[:120],
            "raw": {"feeds": dict(zip(self.feed_urls, feed_bodies, strict=False))},
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

    def _parse_feed(self, feed_url: str, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item") + root.findall(".//{*}entry")
        source_name = self._source_name(feed_url)

        items: list[dict[str, Any]] = []
        for entry in entries:
            title = self._first_text(entry, "title") or "NHC Advisory"
            body = self._strip_html(self._first_text(entry, "description", "summary", "content") or "")
            published = self._first_text(entry, "pubDate", "published", "updated") or ""
            link = self._extract_link(entry)
            storm_name = self._extract_storm_name(title, body)
            max_wind_kt = self._extract_numeric(body, r"(\d{2,3})\s*(?:kt|kts|knots)")
            max_wind_mph = self._extract_numeric(body, r"(\d{2,3})\s*mph")
            surge_ft = self._extract_numeric(body, r"(\d{1,2}(?:\.\d+)?)\s*ft\s*(?:above|of)?\s*(?:storm surge|inundation)")
            wind_probability = self._extract_numeric(body, r"(\d{1,3})\s*%\s*(?:chance|probability)")
            severity, rank = self._severity(title, body)
            event_type = self._event_type(title, body)

            items.append(
                {
                    "title": title,
                    "published": published,
                    "source": source_name,
                    "link": link,
                    "body": body,
                    "storm_name": storm_name,
                    "event_type": event_type,
                    "severity": severity,
                    "severity_rank": rank,
                    "forecast": {
                        "max_wind_kt": max_wind_kt,
                        "max_wind_mph": max_wind_mph,
                        "wind_probability_pct": wind_probability,
                        "surge_ft": surge_ft,
                    },
                    "incident_group": "hurricane",
                }
            )
        return items

    def _dedupe_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            key = f"{item.get('title', '')}|{item.get('published', '')}|{item.get('link', '')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

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

    def _extract_storm_name(self, title: str, body: str) -> str:
        combined = f"{title} {body}"
        match = re.search(
            r"\b(?:Hurricane|Tropical Storm|Tropical Depression|Subtropical Storm)\s+([A-Z][A-Za-z\-]+)",
            combined,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return match.group(1).upper()

    def _severity(self, title: str, body: str) -> tuple[str, int]:
        haystack = f"{title} {body}".lower()
        if "major hurricane" in haystack or "hurricane warning" in haystack:
            return "critical", 6
        if "hurricane watch" in haystack or "storm surge warning" in haystack:
            return "high", 5
        if "tropical storm warning" in haystack or "tropical storm watch" in haystack:
            return "high", 4
        return "medium", 3

    def _event_type(self, title: str, body: str) -> str:
        haystack = f"{title} {body}".lower()
        if "storm surge" in haystack:
            return "storm_surge"
        if "watch" in haystack:
            return "watch"
        if "warning" in haystack:
            return "warning"
        return "advisory"

    def _strip_html(self, value: str) -> str:
        parser = _HTMLStripper()
        parser.feed(value)
        text = parser.text()
        parser.close()
        return " ".join(text.split())

    def _extract_numeric(self, text: str, pattern: str) -> float | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1))
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

    def _source_name(self, feed_url: str) -> str:
        return urlparse(feed_url).netloc or "nhc"
