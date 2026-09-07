from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseFetcher(ABC):
    """Base interface for all feed fetchers."""

    name: str

    @abstractmethod
    async def fetch(self) -> dict[str, Any]:
        """Fetch and normalize source data into a standard dictionary."""

    @abstractmethod
    async def save_raw(self, payload: dict[str, Any]) -> None:
        """Persist the normalized payload as raw JSON."""
