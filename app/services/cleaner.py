from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings


class StorageCleaner:
    """Removes expired raw payload files while preserving the latest snapshot state."""

    _TIMESTAMP_SUFFIX = re.compile(r"([._-])(?:\d{8}(?:t\d{6}z?)?|\d{4}-\d{2}-\d{2}(?:[t_]\d{2}[-:]?\d{2}[-:]?\d{2}z?)?)$", re.IGNORECASE)

    def __init__(self, raw_dir: Path | None = None, retention_days: int | None = None) -> None:
        self.raw_dir = raw_dir or settings.raw_dir
        self.retention_days = retention_days or settings.raw_data_max_age_days

    async def run(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._cleanup)

    def _cleanup(self) -> dict[str, Any]:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        json_files = [path for path in self.raw_dir.glob("*.json") if path.is_file()]
        retained_paths = self._latest_files_by_group(json_files)
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        deleted: list[str] = []

        for path in json_files:
            if path in retained_paths:
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified_at >= cutoff:
                continue
            path.unlink(missing_ok=True)
            deleted.append(path.name)

        return {
            "ran_at": datetime.now(UTC).isoformat(),
            "retention_days": self.retention_days,
            "cutoff": cutoff.isoformat(),
            "deleted_files": deleted,
            "retained_files": sorted(path.name for path in retained_paths),
        }

    def _latest_files_by_group(self, files: list[Path]) -> set[Path]:
        grouped: dict[str, list[Path]] = defaultdict(list)
        for path in files:
            grouped[self._group_key(path)].append(path)

        retained: set[Path] = set()
        for candidates in grouped.values():
            retained.add(max(candidates, key=lambda item: item.stat().st_mtime))
        return retained

    def _group_key(self, path: Path) -> str:
        stem = path.stem
        normalized = self._TIMESTAMP_SUFFIX.sub("", stem)
        return normalized or stem
