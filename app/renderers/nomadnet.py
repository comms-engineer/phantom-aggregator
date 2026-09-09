from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.source_config import SourceDefinition, page_title


class NomadNetRenderer:
    """Renders normalized feed payloads into lightweight NomadNet-friendly text."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or settings.nomadnet_dir

    def render_space_weather(self, payload: dict[str, Any], summary: str) -> str:
        normalized = payload.get("normalized", {}) if isinstance(payload, dict) else {}
        lines = [
            "== SPACE WEATHER BRIEF ==",
            "----------------------------------------",
            f"Updated: {normalized.get('updated', payload.get('fetched_at', 'unknown'))}",
            f"SFI: {normalized.get('sfi', 0)}",
            f"Planetary K-Index: {normalized.get('k_index', 0)}",
            f"Geomagnetic Storm Risk: {normalized.get('geomagnetic_storm', 'UNKNOWN')}",
            "",
            "Summary:",
            summary,
            "",
            "Source: NOAA SWPC",
        ]
        return self._normalize(lines)

    def render_cyber_kev(self, payload: dict[str, Any], summary: str) -> str:
        rows = payload.get("latest_five", []) if isinstance(payload, dict) else []
        lines = [
            "== CYBER THREAT BRIEF ==",
            "----------------------------------------",
            f"Catalog Size: {payload.get('count_total', 0)}",
            f"Updated: {payload.get('fetched_at', 'unknown')}",
            "",
            "Latest Known Exploited Vulnerabilities:",
        ]
        if not rows:
            lines.append("- No recent KEV entries available.")
        for entry in rows[:5]:
            lines.append(
                f"- {entry.get('cveID', 'n/a')} | {entry.get('vendorProject', 'n/a')} | "
                f"{entry.get('vulnerabilityName', 'n/a')} ({entry.get('dateAdded', 'n/a')})"
            )

        lines.extend(["", "Summary:", summary, "", "Source: CISA KEV"])
        return self._normalize(lines)

    def render_news(self, payload: dict[str, Any], summary: str) -> str:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        lines = [
            "== NEWS BRIEF ==",
            "----------------------------------------",
            f"Updated: {payload.get('fetched_at', 'unknown')}",
            f"Feed Count: {payload.get('source_count', 0)}",
            "",
            "Top Headlines:",
        ]
        if not items:
            lines.append("- No news items available.")
        for item in items[:10]:
            lines.append(f"- {item.get('title', 'Untitled')} [{item.get('source', 'unknown')}]")
            if item.get("published"):
                lines.append(f"  {item['published']}")
            brief = self._item_brief(item)
            if brief:
                lines.append(f"  Brief: {brief}")
            article_page = item.get("article_page")
            if article_page:
                lines.append(f"  _`[Read Full Article`{article_page}]`_")

        lines.extend(["", "Summary:", summary])
        return self._normalize(lines)

    def render_weather(self, payload: dict[str, Any], summary: str) -> str:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        weather_items = [
            item
            for item in items
            if any(
                keyword in f"{item.get('title', '')} {item.get('body', '')}".lower()
                for keyword in ("weather", "storm", "alert", "flood", "hurricane", "tornado")
            )
        ]
        lines = [
            "== WEATHER ALERT BRIEF ==",
            "----------------------------------------",
            f"Updated: {payload.get('fetched_at', 'unknown')}",
            "",
            "Recent Weather/Alert Headlines:",
        ]
        if not weather_items:
            lines.append("- No weather-related alerts detected in configured feeds.")
        for item in weather_items[:10]:
            lines.append(f"- {item.get('title', 'Untitled')} [{item.get('source', 'unknown')}]")
            if item.get("published"):
                lines.append(f"  {item['published']}")
            brief = self._item_brief(item)
            if brief:
                lines.append(f"  Brief: {brief}")
            article_page = item.get("article_page")
            if article_page:
                lines.append(f"  _`[Read Full Article`{article_page}]`_")
        lines.extend(["", "Summary:", summary])
        return self._normalize(lines)

    def render_index(self, pages: list[tuple[str, str]] | None = None) -> str:
        links = pages or [
            ("weather.mu", "Weather Alerts Brief"),
            ("space.mu", "Space Weather Brief"),
            ("cyber.mu", "Cyber Threat Brief"),
            ("news.mu", "News Brief"),
            (settings.system_health_page_name, "System Telemetry"),
        ]
        lines = [
            "== PHANTOM AGGREGATOR INDEX ==",
            "----------------------------------------",
            "",
        ]
        for page_name, title in links:
            lines.append(f"_`[{title}`{page_name}]`_")
        lines.extend(["", "Generated by phantom-aggregator"])
        return self._normalize(lines)

    def write_page(self, page_name: str, content: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / page_name
        path.write_text(content, encoding="utf-8")
        return path

    def write_index(self) -> Path:
        return self.write_page("index.mu", self.render_index())

    def write_dynamic_index(self, sources: list[SourceDefinition]) -> Path:
        pages: list[tuple[str, str]] = []
        seen: set[str] = set()
        grouped: dict[str, list[SourceDefinition]] = {}
        for source in sources:
            grouped.setdefault(source.nomadnet_page, []).append(source)
        for page_name in sorted(grouped):
            if page_name in seen:
                continue
            seen.add(page_name)
            pages.append((page_name, page_title(page_name, grouped[page_name])))
        pages.append((settings.system_health_page_name, "System Telemetry"))
        return self.write_page("index.mu", self.render_index(pages))

    def render_system_health(self, payload: dict[str, Any]) -> str:
        system = payload.get("system", {}) if isinstance(payload, dict) else {}
        cpu = system.get("cpu", {}) if isinstance(system, dict) else {}
        memory = system.get("memory", {}) if isinstance(system, dict) else {}
        storage = system.get("storage", {}) if isinstance(system, dict) else {}
        connectivity = system.get("connectivity", {}) if isinstance(system, dict) else {}
        internet = connectivity.get("internet", {}) if isinstance(connectivity, dict) else {}
        mesh = connectivity.get("mesh", {}) if isinstance(connectivity, dict) else {}
        lines = [
            "== SYSTEM TELEMETRY ==",
            "----------------------------------------",
            f"Updated: {payload.get('fetched_at', 'unknown')}",
            f"CPU Usage: {cpu.get('usage_percent', 0)}%",
            f"CPU Temp: {cpu.get('temperature_c', 'unavailable')} C",
            f"RAM Usage: {memory.get('used_percent', 0)}%",
            f"RAM Available: {memory.get('available_mb', 0)} MB",
            f"Storage Free ({storage.get('path', '/app/data')}): {storage.get('free_gb', 0)} GB",
            f"Storage Used: {storage.get('used_percent', 0)}%",
            f"Internet Reachable: {internet.get('reachable', False)}",
            f"Mesh Connected: {mesh.get('connected', False)}",
        ]
        mesh_interfaces = mesh.get("interfaces", []) if isinstance(mesh, dict) else []
        if mesh_interfaces:
            lines.append("Mesh Interfaces:")
            for interface in mesh_interfaces[:5]:
                lines.append(
                    f"- {interface.get('name', 'unknown')} | up={interface.get('is_up', False)} | "
                    f"addr={interface.get('has_address', False)}"
                )
        lines.extend(["", "Generated by phantom-aggregator"])
        return self._normalize(lines)

    def render_dynamic_page(
        self,
        page_name: str,
        source_snapshots: list[tuple[SourceDefinition, dict[str, Any] | None]],
    ) -> str:
        if self._is_emergency_page(page_name):
            return self._render_emergency_page(page_name, source_snapshots)
        defined_sources = [source for source, _ in source_snapshots]
        lines = [
            f"== {page_title(page_name, defined_sources).upper()} ==",
            "----------------------------------------",
            "",
        ]
        if not source_snapshots:
            lines.extend(["No configured sources for this page.", "", "Generated by phantom-aggregator"])
            return self._normalize(lines)

        for index, (source, snapshot) in enumerate(source_snapshots):
            lines.extend(self._render_source_section(source, snapshot))
            if index < len(source_snapshots) - 1:
                lines.extend(["", "----------------------------------------", ""])
        return self._normalize(lines)

    def _render_source_section(self, source: SourceDefinition, snapshot: dict[str, Any] | None) -> list[str]:
        lines = [
            f"[{source.name}]",
            f"Type: {source.type} | Category: {source.category}",
            f"URL: {source.url}",
        ]
        if not snapshot:
            lines.append("Status: awaiting first successful fetch")
            return lines

        payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
        summary = snapshot.get("summary", "No summary available.") if isinstance(snapshot, dict) else "No summary available."
        lines.append(f"Updated: {snapshot.get('fetched_at', payload.get('fetched_at', 'unknown'))}")

        parser = source.options.get("parser")
        if parser == "space_weather_swpc":
            normalized = payload.get("normalized", {}) if isinstance(payload, dict) else {}
            lines.extend(
                [
                    f"SFI: {normalized.get('sfi', 0)}",
                    f"Planetary K-Index: {normalized.get('k_index', 0)}",
                    f"Geomagnetic Storm Risk: {normalized.get('geomagnetic_storm', 'UNKNOWN')}",
                ]
            )
        elif parser == "cisa_kev":
            lines.append(f"Catalog Size: {payload.get('count_total', 0)}")
            lines.append("Latest Entries:")
            for entry in payload.get("latest_five", [])[:5]:
                lines.append(
                    f"- {entry.get('cveID', 'n/a')} | {entry.get('vendorProject', 'n/a')} | "
                    f"{entry.get('vulnerabilityName', 'n/a')} ({entry.get('dateAdded', 'n/a')})"
                )
        elif source.type in {"rss", "atom"}:
            items = payload.get("items", []) if isinstance(payload, dict) else []
            lines.append(f"Items Available: {len(items)}")
            lines.append("Headlines:")
            for item in items[:5]:
                lines.append(f"- {item.get('title', 'Untitled')}")
                if item.get("published"):
                    lines.append(f"  {item['published']}")
                brief = self._item_brief(item)
                if brief:
                    lines.append(f"  Brief: {brief}")
                article_page = item.get("article_page")
                if article_page:
                    lines.append(f"  _`[Read Full Article`{article_page}]`_")
        elif source.type == "text_feed":
            lines.append(f"Line Count: {payload.get('line_count', 0)}")
            lines.append("Preview:")
            for line in payload.get("lines", [])[:5]:
                lines.append(f"- {line}")
        else:
            preview = payload.get("preview", {}) if isinstance(payload, dict) else {}
            lines.append("Preview:")
            if isinstance(preview, dict):
                for key, value in list(preview.items())[:5]:
                    lines.append(f"- {key}: {value}")
            else:
                lines.append(f"- {preview}")

        lines.extend(["", "Summary:", summary])
        return lines

    def _is_emergency_page(self, page_name: str) -> bool:
        return page_name in {"disasters.mu", "emergencies.mu", "disasters.page", "emergencies.page"}

    def _render_emergency_page(
        self,
        page_name: str,
        source_snapshots: list[tuple[SourceDefinition, dict[str, Any] | None]],
    ) -> str:
        lines = [
            "== ACTIVE DISASTER & INCIDENT BRIEFS ==",
            "----------------------------------------",
        ]
        all_items: list[dict[str, Any]] = []
        source_status: list[str] = []
        for source, snapshot in source_snapshots:
            if not snapshot:
                source_status.append(f"- {source.name}: awaiting first successful fetch")
                continue
            payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
            source_status.append(f"- {source.name}: {snapshot.get('fetched_at', payload.get('fetched_at', 'unknown'))}")
            payload_items = payload.get("items", []) if isinstance(payload, dict) else []
            if not isinstance(payload_items, list):
                continue
            for item in payload_items:
                if not isinstance(item, dict):
                    continue
                merged = dict(item)
                merged.setdefault("source", source.name)
                all_items.append(merged)

        lines.append(f"Updated Sources: {len(source_snapshots)}")
        if source_status:
            lines.append("Source Status:")
            lines.extend(source_status[:10])
        lines.extend(["", "Priority Order: Critical Evacuations > Hurricane Watches > Fire Status", ""])

        if not all_items:
            lines.append("No active emergency incidents available.")
            lines.append("")
            lines.append("Generated by phantom-aggregator")
            return self._normalize(lines)

        sorted_items = sorted(all_items, key=self._emergency_sort_key, reverse=True)
        for item in sorted_items[:20]:
            lines.append(f"- [{str(item.get('severity', 'info')).upper()}] {item.get('title', 'Untitled Incident')}")
            lines.append(f"  Source: {item.get('source', 'unknown')}")
            published = item.get("published")
            if published:
                lines.append(f"  Updated: {published}")
            details = self._item_brief(item)
            if details:
                lines.append(f"  Details: {details}")
            article_page = item.get("article_page")
            if article_page:
                lines.append(f"  _`[Read Full Incident Brief`{article_page}]`_")
            lines.append("")

        lines.append("Generated by phantom-aggregator")
        return self._normalize(lines)

    def _emergency_sort_key(self, item: dict[str, Any]) -> tuple[int, datetime, str]:
        severity_map = {"critical": 6, "high": 5, "medium": 4, "info": 3}
        rank = int(item.get("severity_rank", severity_map.get(str(item.get("severity", "info")).lower(), 2)) or 2)
        event_type = str(item.get("event_type", "")).lower()
        if "evac" in event_type:
            rank += 3
        elif "hurricane" in event_type or "watch" in event_type:
            rank += 2
        elif "wildfire" in event_type or "fire" in event_type:
            rank += 1
        published = self._parse_timestamp(item.get("published"))
        return (rank, published, str(item.get("title", "")))

    def _parse_timestamp(self, value: Any) -> datetime:
        if not value:
            return datetime(1970, 1, 1, tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=UTC)

    def _normalize(self, lines: list[str]) -> str:
        max_width = min(max(settings.nomadnet_line_limit, 72), 80)
        normalized: list[str] = []
        for line in lines:
            if not line:
                normalized.append("")
                continue
            if self._is_micron_link(line):
                normalized.append(line)
                continue
            wrapped = textwrap.wrap(
                line,
                width=max_width,
                break_long_words=False,
                break_on_hyphens=False,
                replace_whitespace=False,
            )
            normalized.extend(wrapped or [""])
        return "\n".join(normalized[: settings.nomadnet_max_lines]).strip() + "\n"

    def _item_brief(self, item: dict[str, Any]) -> str:
        brief = " ".join(str(item.get("body", "")).split())
        if not brief:
            return ""
        if len(brief) <= 220:
            return brief
        return brief[:217].rstrip() + "..."

    def _is_micron_link(self, line: str) -> bool:
        stripped = line.lstrip()
        return stripped.startswith("_`[") and stripped.endswith("]`_")
