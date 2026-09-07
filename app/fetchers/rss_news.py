from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import aiohttp

from app.config import settings
from app.fetchers.base import BaseFetcher


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return unescape(" ".join(chunk.strip() for chunk in self._chunks if chunk.strip()))


class RssNewsFetcher(BaseFetcher):
    """Fetches and normalizes RSS/Atom feeds."""

    name = "rss_news"

    def __init__(self, raw_dir: Path | None = None, feeds: list[str] | None = None) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.feeds = feeds or settings.rss_feed_urls

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            feed_payloads = await asyncio.gather(*[self._fetch_feed(session, feed_url) for feed_url in self.feeds])

        items: list[dict[str, str]] = []
        feed_meta: list[dict[str, Any]] = []
        for payload in feed_payloads:
            feed_meta.append(
                {
                    "url": payload["url"],
                    "title": payload["title"],
                    "item_count": len(payload["items"]),
                }
            )
            items.extend(payload["items"])

        items.sort(key=lambda item: item.get("published", ""), reverse=True)
        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source_count": len(self.feeds),
            "feeds": feed_meta,
            "items": items,
        }

    async def save_raw(self, payload: dict[str, Any]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.raw_dir / f"{self.name}.json"
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _fetch_feed(self, session: aiohttp.ClientSession, feed_url: str) -> dict[str, Any]:
        async with session.get(feed_url) as response:
            response.raise_for_status()
            body = await response.text()

        root = ET.fromstring(body)
        if self._local_name(root.tag).lower() == "feed":
            return self._parse_atom(root, feed_url)
        return self._parse_rss(root, feed_url)

    def _parse_rss(self, root: ET.Element, feed_url: str) -> dict[str, Any]:
        channel = root.find("channel")
        if channel is None:
            return {"url": feed_url, "title": self._source_name(feed_url), "items": []}

        feed_title = self._extract_text(channel, ("title",)) or self._source_name(feed_url)
        items: list[dict[str, str]] = []
        for item in channel.findall("item"):
            title = self._extract_text(item, ("title",)) or "Untitled"
            published = self._extract_text(item, ("pubDate", "published", "updated")) or ""
            raw_body = self._extract_text(item, ("description", "content", "content:encoded")) or ""
            items.append(
                {
                    "title": title.strip(),
                    "published": published.strip(),
                    "source": feed_title.strip(),
                    "body": self._strip_html(raw_body),
                }
            )
        return {"url": feed_url, "title": feed_title, "items": items}

    def _parse_atom(self, root: ET.Element, feed_url: str) -> dict[str, Any]:
        feed_title = self._extract_text(root, ("title",)) or self._source_name(feed_url)
        items: list[dict[str, str]] = []
        for entry in root.findall(".//{*}entry"):
            title = self._extract_text(entry, ("title",)) or "Untitled"
            published = self._extract_text(entry, ("published", "updated")) or ""
            raw_body = self._extract_text(entry, ("content", "summary")) or ""
            items.append(
                {
                    "title": title.strip(),
                    "published": published.strip(),
                    "source": feed_title.strip(),
                    "body": self._strip_html(raw_body),
                }
            )
        return {"url": feed_url, "title": feed_title, "items": items}

    def _extract_text(self, element: ET.Element, candidate_tags: tuple[str, ...]) -> str | None:
        for child in list(element):
            local_name = self._local_name(child.tag).lower()
            for candidate in candidate_tags:
                if local_name == candidate.lower().replace("content:", ""):
                    return "".join(child.itertext()).strip()
        return None

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _strip_html(self, value: str) -> str:
        parser = _HTMLStripper()
        parser.feed(value)
        text = parser.get_text()
        parser.close()
        return " ".join(text.split())

    def _source_name(self, feed_url: str) -> str:
        host = urlparse(feed_url).netloc
        return host or "unknown-source"
