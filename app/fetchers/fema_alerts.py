from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
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


class FemaAlertsFetcher(BaseFetcher):
    """Fetches FEMA/IPAWS alerts and FEMA disaster declaration summaries."""

    name = "fema_alerts"

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
            raise ValueError("source_url or url is required for FemaAlertsFetcher")
        if "$filter" not in self.feed_url.lower():
            cutoff = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
            separator = "&" if "?" in self.feed_url else "?"
            self.feed_url = f"{self.feed_url}{separator}$filter=sent ge '{cutoff}'&$orderby=sent desc&$top=200"
        self.options = options or {}
        self.declarations_api_url = str(
            self.options.get(
                "declarations_api_url",
                "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
                "?$orderby=declarationDate%20desc&$top=100",
            )
        )
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            alerts_body, declarations_body = await asyncio.gather(
                self._fetch_json(session, self.feed_url),
                self._fetch_json(session, self.declarations_api_url),
            )

        alert_items = self._parse_ipaws_alerts(alerts_body)
        declaration_items = self._parse_declarations(declarations_body)
        items = sorted(alert_items + declaration_items, key=self._sort_key, reverse=True)

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "FEMA",
            "url": self.feed_url,
            "declarations_api_url": self.declarations_api_url,
            "item_count": len(items),
            "summary": {
                "active_declarations": len(declaration_items),
                "active_alerts": len(alert_items),
            },
            "items": items,
            "raw": {
                "alerts_feed": alerts_body,
                "declarations": declarations_body,
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

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> Any:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()

    def _parse_alert_feed(self, xml_text: str, feed_url: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item") + root.findall(".//{*}entry") + root.findall(".//{*}alert")

        items: list[dict[str, Any]] = []
        for entry in entries:
            title = self._first_text(entry, "title", "headline", "event") or "FEMA Alert"
            body = self._strip_html(self._first_text(entry, "description", "summary", "instruction", "note") or "")
            published = self._first_text(entry, "pubDate", "published", "updated", "sent", "effective") or ""
            link = self._extract_link(entry)
            status = self._first_text(entry, "status", "msgType") or ""
            event_type = self._first_text(entry, "event", "category") or "civil-emergency"
            severity, rank = self._severity_from_text(f"{title} {body} {status} {event_type}")
            items.append(
                {
                    "title": title,
                    "published": published,
                    "source": self._source_name(feed_url),
                    "link": link,
                    "body": body,
                    "event_type": event_type,
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_alert",
                }
            )
        return items[:100]

    def _parse_ipaws_alerts(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []

        records = body.get("IpawsArchivedAlerts")
        if not isinstance(records, list):
            return []

        # IPAWS emits one CAP record per targeted NWS zone for what is
        # operationally a single warning (e.g. one hurricane warning
        # rebroadcast separately for each affected zone). Group by the
        # shared headline/event/issue-time so these render as one item
        # listing all affected areas instead of N near-duplicate entries.
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        group_areas: dict[tuple[str, str, str], list[str]] = {}

        for record in records:
            if not isinstance(record, dict):
                continue

            infos = record.get("info")
            info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {}

            headline = str(info.get("headline") or info.get("event") or "FEMA Alert").strip()
            body_text = self._strip_html(str(info.get("description") or info.get("instruction") or ""))
            published = str(record.get("sent") or info.get("effective") or "")
            event_type = str(info.get("event") or "civil-emergency")
            severity, rank = self._severity_from_text(f"{headline} {body_text} {event_type}")

            areas = info.get("area")
            area_desc = ""
            if isinstance(areas, list) and areas and isinstance(areas[0], dict):
                area_desc = str(areas[0].get("areaDesc", "")).strip()

            key = (headline, event_type, published)
            if key not in grouped:
                grouped[key] = {
                    "title": headline,
                    "published": published,
                    "source": "FEMA IPAWS",
                    "link": "",
                    "body": body_text,
                    "event_type": event_type,
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_alert",
                }
                group_areas[key] = []
            if area_desc and area_desc not in group_areas[key]:
                group_areas[key].append(area_desc)

        items: list[dict[str, Any]] = []
        for key, entry in grouped.items():
            areas = group_areas[key]
            if areas:
                shown = areas[:5]
                area_suffix = ", ".join(shown)
                if len(areas) > 5:
                    area_suffix += f", +{len(areas) - 5} more areas"
                entry["title"] = f"{entry['title']} ({area_suffix})"
            items.append(entry)

        items.sort(key=self._sort_key, reverse=True)
        return items[:100]

    def _parse_declarations(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []
        records = body.get("DisasterDeclarationsSummaries")
        if not isinstance(records, list):
            return []

        active_records = [
            record
            for record in records
            if isinstance(record, dict)
            and str(record.get("declarationType", "")).strip()
            and str(record.get("declarationDate", "")).strip()
        ]

        parsed_items: list[dict[str, Any]] = []
        for record in active_records[:60]:
            incident_type = str(record.get("incidentType", "Unknown Incident")).strip()
            state = str(record.get("state", "Unknown")).strip()
            declaration_type = str(record.get("declarationType", "Disaster Declaration")).strip()
            title = f"{declaration_type}: {incident_type} ({state})"
            major = str(record.get("declarationType", "")).upper() in {"DR", "EM"}
            severity = "high" if major else "medium"
            rank = 5 if major else 4
            body_text = self._compose_declaration_body(record)
            parsed_items.append(
                {
                    "title": title,
                    "published": str(record.get("declarationDate", "")),
                    "source": "fema.gov declarations",
                    "link": self._record_link(record),
                    "body": body_text,
                    "event_type": incident_type.lower().replace(" ", "_"),
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_declaration",
                }
            )

        parsed_items.sort(key=self._sort_key, reverse=True)
        return parsed_items[:30]

    def _compose_declaration_body(self, record: dict[str, Any]) -> str:
        lines: list[str] = []
        for key in ("designatedArea", "declarationTitle", "incidentBeginDate", "incidentEndDate"):
            value = str(record.get(key, "")).strip()
            if value:
                lines.append(f"{key}: {value}")
        return " | ".join(lines)

    def _record_link(self, record: dict[str, Any]) -> str:
        declaration_id = str(record.get("disasterNumber", "")).strip()
        if not declaration_id:
            return ""
        return f"https://www.fema.gov/disaster/{declaration_id}"

    def _extract_link(self, entry: ET.Element) -> str:
        for child in list(entry):
            local = self._local_name(child.tag).lower()
            if local != "link":
                continue
            href = child.attrib.get("href", "").strip()
            if href:
                return href
            text_link = "".join(child.itertext()).strip()
            if text_link:
                return text_link
        return ""

    def _first_text(self, element: ET.Element, *candidate_tags: str) -> str | None:
        tags = {candidate.lower() for candidate in candidate_tags}
        for child in element.iter():
            local = self._local_name(child.tag).lower()
            if local in tags:
                text = " ".join("".join(child.itertext()).split())
                if text:
                    return text
        return None

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _strip_html(self, value: str) -> str:
        parser = _HTMLStripper()
        parser.feed(value)
        text = parser.text()
        parser.close()
        return " ".join(text.split())

    def _severity_from_text(self, text: str) -> tuple[str, int]:
        haystack = text.lower()
        critical_terms = (
            "evacuate",
            "evacuation",
            "immediate danger",
            "civil emergency",
            "state of emergency",
            "mandatory",
        )
        high_terms = ("warning", "major", "catastrophic", "emergency", "disaster")
        medium_terms = ("watch", "advisory", "statement")

        if any(term in haystack for term in critical_terms):
            return "critical", 6
        if any(term in haystack for term in high_terms):
            return "high", 5
        if any(term in haystack for term in medium_terms):
            return "medium", 4
        return "info", 3

    def _sort_key(self, item: dict[str, Any]) -> tuple[int, datetime, str]:
        rank = int(item.get("severity_rank", 0) or 0)
        published = self._parse_date(item.get("published"))
        title = str(item.get("title", ""))
        return (rank, published, title)

    def _parse_date(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = isoparse(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            match = re.search(r"\d{4}-\d{2}-\d{2}", str(value))
            if not match:
                return datetime(1970, 1, 1, tzinfo=UTC)
            try:
                parsed = isoparse(match.group(0))
                return parsed.replace(tzinfo=UTC)
            except ValueError:
                return datetime(1970, 1, 1, tzinfo=UTC)

    def _source_name(self, feed_url: str) -> str:
        host = urlparse(feed_url).netloc
        return host or "fema"from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
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


class FemaAlertsFetcher(BaseFetcher):
    """Fetches FEMA/IPAWS alerts and FEMA disaster declaration summaries."""

    name = "fema_alerts"

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
            raise ValueError("source_url or url is required for FemaAlertsFetcher")
        if "$filter" not in self.feed_url.lower():
            cutoff = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
            separator = "&" if "?" in self.feed_url else "?"
            self.feed_url = f"{self.feed_url}{separator}$filter=sent ge '{cutoff}'&$orderby=sent desc&$top=200"
        self.options = options or {}
        self.declarations_api_url = str(
            self.options.get(
                "declarations_api_url",
                "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
                "?$orderby=declarationDate%20desc&$top=100",
            )
        )
        self.name = name or self.__class__.name

    async def fetch(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=settings.request_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            alerts_body, declarations_body = await asyncio.gather(
                self._fetch_json(session, self.feed_url),
                self._fetch_json(session, self.declarations_api_url),
            )

        alert_items = self._parse_ipaws_alerts(alerts_body)
        declaration_items = self._parse_declarations(declarations_body)
        items = sorted(alert_items + declaration_items, key=self._sort_key, reverse=True)

        return {
            "fetched_at": datetime.now(UTC).isoformat(),
            "source": "FEMA",
            "url": self.feed_url,
            "declarations_api_url": self.declarations_api_url,
            "item_count": len(items),
            "summary": {
                "active_declarations": len(declaration_items),
                "active_alerts": len(alert_items),
            },
            "items": items,
            "raw": {
                "alerts_feed": alerts_body,
                "declarations": declarations_body,
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

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str) -> Any:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()

    def _parse_alert_feed(self, xml_text: str, feed_url: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        entries = root.findall(".//item") + root.findall(".//{*}entry") + root.findall(".//{*}alert")

        items: list[dict[str, Any]] = []
        for entry in entries:
            title = self._first_text(entry, "title", "headline", "event") or "FEMA Alert"
            body = self._strip_html(self._first_text(entry, "description", "summary", "instruction", "note") or "")
            published = self._first_text(entry, "pubDate", "published", "updated", "sent", "effective") or ""
            link = self._extract_link(entry)
            status = self._first_text(entry, "status", "msgType") or ""
            event_type = self._first_text(entry, "event", "category") or "civil-emergency"
            severity, rank = self._severity_from_text(f"{title} {body} {status} {event_type}")
            items.append(
                {
                    "title": title,
                    "published": published,
                    "source": self._source_name(feed_url),
                    "link": link,
                    "body": body,
                    "event_type": event_type,
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_alert",
                }
            )
        return items[:100]

    def _parse_ipaws_alerts(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []

        records = body.get("IpawsArchivedAlerts")
        if not isinstance(records, list):
            return []

        items: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue

            infos = record.get("info")
            info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else {}

            title = str(info.get("headline") or info.get("event") or "FEMA Alert").strip()
            body_text = self._strip_html(str(info.get("description") or info.get("instruction") or ""))
            published = str(record.get("sent") or info.get("effective") or "")
            event_type = str(info.get("event") or "civil-emergency")
            severity, rank = self._severity_from_text(f"{title} {body_text} {event_type}")

            areas = info.get("area")
            area_desc = ""
            if isinstance(areas, list) and areas and isinstance(areas[0], dict):
                area_desc = str(areas[0].get("areaDesc", "")).strip()

            items.append(
                {
                    "title": f"{title} ({area_desc})" if area_desc else title,
                    "published": published,
                    "source": "FEMA IPAWS",
                    "link": "",
                    "body": body_text,
                    "event_type": event_type,
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_alert",
                }
            )
        return items[:100]

    def _parse_declarations(self, body: Any) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return []
        records = body.get("DisasterDeclarationsSummaries")
        if not isinstance(records, list):
            return []

        active_records = [
            record
            for record in records
            if isinstance(record, dict)
            and str(record.get("declarationType", "")).strip()
            and str(record.get("declarationDate", "")).strip()
        ]

        parsed_items: list[dict[str, Any]] = []
        for record in active_records[:60]:
            incident_type = str(record.get("incidentType", "Unknown Incident")).strip()
            state = str(record.get("state", "Unknown")).strip()
            declaration_type = str(record.get("declarationType", "Disaster Declaration")).strip()
            title = f"{declaration_type}: {incident_type} ({state})"
            major = str(record.get("declarationType", "")).upper() in {"DR", "EM"}
            severity = "high" if major else "medium"
            rank = 5 if major else 4
            body_text = self._compose_declaration_body(record)
            parsed_items.append(
                {
                    "title": title,
                    "published": str(record.get("declarationDate", "")),
                    "source": "fema.gov declarations",
                    "link": self._record_link(record),
                    "body": body_text,
                    "event_type": incident_type.lower().replace(" ", "_"),
                    "severity": severity,
                    "severity_rank": rank,
                    "incident_group": "fema_declaration",
                }
            )

        parsed_items.sort(key=self._sort_key, reverse=True)
        return parsed_items[:30]

    def _compose_declaration_body(self, record: dict[str, Any]) -> str:
        lines: list[str] = []
        for key in ("designatedArea", "declarationTitle", "incidentBeginDate", "incidentEndDate"):
            value = str(record.get(key, "")).strip()
            if value:
                lines.append(f"{key}: {value}")
        return " | ".join(lines)

    def _record_link(self, record: dict[str, Any]) -> str:
        declaration_id = str(record.get("disasterNumber", "")).strip()
        if not declaration_id:
            return ""
        return f"https://www.fema.gov/disaster/{declaration_id}"

    def _extract_link(self, entry: ET.Element) -> str:
        for child in list(entry):
            local = self._local_name(child.tag).lower()
            if local != "link":
                continue
            href = child.attrib.get("href", "").strip()
            if href:
                return href
            text_link = "".join(child.itertext()).strip()
            if text_link:
                return text_link
        return ""

    def _first_text(self, element: ET.Element, *candidate_tags: str) -> str | None:
        tags = {candidate.lower() for candidate in candidate_tags}
        for child in element.iter():
            local = self._local_name(child.tag).lower()
            if local in tags:
                text = " ".join("".join(child.itertext()).split())
                if text:
                    return text
        return None

    def _local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _strip_html(self, value: str) -> str:
        parser = _HTMLStripper()
        parser.feed(value)
        text = parser.text()
        parser.close()
        return " ".join(text.split())

    def _severity_from_text(self, text: str) -> tuple[str, int]:
        haystack = text.lower()
        critical_terms = (
            "evacuate",
            "evacuation",
            "immediate danger",
            "civil emergency",
            "state of emergency",
            "mandatory",
        )
        high_terms = ("warning", "major", "catastrophic", "emergency", "disaster")
        medium_terms = ("watch", "advisory", "statement")

        if any(term in haystack for term in critical_terms):
            return "critical", 6
        if any(term in haystack for term in high_terms):
            return "high", 5
        if any(term in haystack for term in medium_terms):
            return "medium", 4
        return "info", 3

    def _sort_key(self, item: dict[str, Any]) -> tuple[int, datetime, str]:
        rank = int(item.get("severity_rank", 0) or 0)
        published = self._parse_date(item.get("published"))
        title = str(item.get("title", ""))
        return (rank, published, title)

    def _parse_date(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = isoparse(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            match = re.search(r"\d{4}-\d{2}-\d{2}", str(value))
            if not match:
                return datetime(1970, 1, 1, tzinfo=UTC)
            try:
                parsed = isoparse(match.group(0))
                return parsed.replace(tzinfo=UTC)
            except ValueError:
                return datetime(1970, 1, 1, tzinfo=UTC)

    def _source_name(self, feed_url: str) -> str:
        host = urlparse(feed_url).netloc
        return host or "fema"
