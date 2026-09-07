from __future__ import annotations

import asyncio
import hashlib
import logging
import textwrap
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.source_config import SourceDefinition

logger = logging.getLogger("phantom-aggregator.alerter")


@dataclass(slots=True)
class AlertRecord:
    fingerprint: str
    message: str
    reason: str


class CriticalEventAlerter:
    """Evaluates snapshots for critical events and optionally dispatches LXMF alerts."""

    def __init__(
        self,
        *,
        k_index_threshold: int | None = None,
        keywords: tuple[str, ...] | None = None,
        lxmf_enabled: bool | None = None,
        lxmf_destinations: tuple[str, ...] | None = None,
        lxmf_command: tuple[str, ...] | None = None,
    ) -> None:
        self.k_index_threshold = k_index_threshold or settings.critical_alert_k_index_threshold
        self.keywords = tuple(keyword.strip().lower() for keyword in (keywords or settings.critical_alert_keywords) if keyword.strip())
        self.lxmf_enabled = settings.lxmf_alerting_enabled if lxmf_enabled is None else lxmf_enabled
        self.lxmf_destinations = lxmf_destinations or settings.lxmf_alert_destinations
        self.lxmf_command = lxmf_command or settings.lxmf_alert_command
        self._last_alert_by_key: dict[str, str] = {}

    async def process_snapshot(self, source: SourceDefinition, snapshot: dict[str, Any]) -> list[str]:
        alerts = self.evaluate_snapshot(source, snapshot)
        dispatched: list[str] = []
        for alert in alerts:
            dedupe_key = f"{source.id}:{alert.reason}"
            if self._last_alert_by_key.get(dedupe_key) == alert.fingerprint:
                continue
            self._last_alert_by_key[dedupe_key] = alert.fingerprint
            logger.warning("Critical alert triggered for %s: %s", source.id, alert.message)
            if self.lxmf_enabled:
                await self.dispatch_lxmf_alert(alert.message)
            dispatched.append(alert.message)
        return dispatched

    def evaluate_snapshot(self, source: SourceDefinition, snapshot: dict[str, Any]) -> list[AlertRecord]:
        payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
        parser = source.options.get("parser")

        if parser == "space_weather_swpc":
            alert = self._space_weather_alert(payload)
            return [alert] if alert else []
        if parser == "cisa_kev":
            alert = self._keyword_alert(payload.get("latest_five", []), source_name="KEV", source_id=source.id)
            return [alert] if alert else []
        if source.type in {"rss", "atom"}:
            alert = self._keyword_alert(payload.get("items", []), source_name=source.name, source_id=source.id)
            return [alert] if alert else []
        return []

    async def dispatch_lxmf_alert(self, message: str) -> None:
        if not self.lxmf_destinations:
            logger.info("LXMF alerting enabled but no destinations are configured")
            return
        if not self.lxmf_command:
            logger.info("LXMF alerting enabled but no command is configured")
            return

        await asyncio.gather(
            *[self._dispatch_to_destination(address=address, message=message) for address in self.lxmf_destinations]
        )

    def _space_weather_alert(self, payload: dict[str, Any]) -> AlertRecord | None:
        normalized = payload.get("normalized", {}) if isinstance(payload, dict) else {}
        try:
            k_index = int(normalized.get("k_index", 0))
        except (TypeError, ValueError):
            return None
        if k_index < self.k_index_threshold:
            return None

        updated = normalized.get("updated") or payload.get("fetched_at") or "unknown"
        message = self._format_message(f"ALERT GEO K{k_index} geomagnetic storm conditions detected at {updated}")
        return AlertRecord(
            fingerprint=self._fingerprint("space", f"{k_index}:{updated}"),
            message=message,
            reason="geomagnetic_storm",
        )

    def _keyword_alert(self, entries: Any, *, source_name: str, source_id: str) -> AlertRecord | None:
        if not isinstance(entries, list):
            return None

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            haystack = " ".join(str(entry.get(field, "")) for field in ("title", "body", "source", "cveID", "vendorProject", "vulnerabilityName"))
            haystack_lower = haystack.lower()
            for keyword in self.keywords:
                if keyword not in haystack_lower:
                    continue
                subject = entry.get("title") or entry.get("vulnerabilityName") or entry.get("cveID") or source_name
                prefix = "ALERT KEV" if source_id == "cisa_kev" else "ALERT NEWS"
                message = self._format_message(f"{prefix} {keyword}: {subject}")
                return AlertRecord(
                    fingerprint=self._fingerprint(source_id, f"{keyword}:{subject}"),
                    message=message,
                    reason=f"keyword:{keyword}",
                )
        return None

    async def _dispatch_to_destination(self, *, address: str, message: str) -> None:
        command = [part.format(address=address, message=message) for part in self.lxmf_command]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning("LXMF dispatch failed for %s: %s", address, stderr.decode("utf-8", errors="ignore").strip())

    def _fingerprint(self, prefix: str, value: str) -> str:
        return hashlib.sha256(f"{prefix}:{value}".encode("utf-8")).hexdigest()

    def _format_message(self, message: str) -> str:
        compact = " ".join(message.split())
        return textwrap.shorten(compact, width=160, placeholder="…")
