from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


SourceType = Literal["rss", "atom", "json_api", "text_feed"]
SourceCategory = Literal["weather", "space", "news", "cyber", "maritime"]

DEFAULT_PAGE_BY_CATEGORY: dict[SourceCategory, str] = {
    "weather": "weather.mu",
    "space": "space.mu",
    "news": "news.mu",
    "cyber": "cyber.mu",
    "maritime": "maritime.mu",
}


class SourceDefinition(BaseModel):
    id: str
    name: str
    type: SourceType
    category: SourceCategory
    url: str
    enabled: bool = True
    poll_interval_mins: int = Field(default=60, ge=1)
    llm_summarize: bool = True
    nomadnet_page: str
    fetch_full_articles: bool | None = None
    max_articles_per_feed: int | None = Field(default=None, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError("id must contain only lowercase letters, numbers, and underscores")
        return value

    @field_validator("nomadnet_page")
    @classmethod
    def validate_page(cls, value: str) -> str:
        if not value.endswith((".page", ".mu")):
            raise ValueError("nomadnet_page must end with .page or .mu")
        return value


class SourcesDocument(BaseModel):
    version: int = Field(default=1, ge=1)
    sources: list[SourceDefinition] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def validate_unique_ids(cls, value: list[SourceDefinition]) -> list[SourceDefinition]:
        ids = [source.id for source in value]
        duplicates = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate source ids: {', '.join(duplicates)}")
        return value

    def enabled_sources(self) -> list[SourceDefinition]:
        return [source for source in self.sources if source.enabled]


class SourceConfigLoader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: SourcesDocument | None = None
        self._mtime_ns: int | None = None

    def load(self, *, force: bool = False) -> SourcesDocument:
        stat = self.path.stat()
        if not force and self._cache is not None and self._mtime_ns == stat.st_mtime_ns:
            return self._cache

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._cache = SourcesDocument.model_validate(data)
        self._mtime_ns = stat.st_mtime_ns
        return self._cache

    def save(self, document: SourcesDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(document.model_dump(mode="json"), indent=2) + "\n"
        self.path.write_text(serialized, encoding="utf-8")
        self._cache = document
        self._mtime_ns = self.path.stat().st_mtime_ns


def slugify_source_id(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return normalized or "source"


def default_page_for_category(category: SourceCategory) -> str:
    return DEFAULT_PAGE_BY_CATEGORY[category]


def page_title(page_name: str, sources: list[SourceDefinition]) -> str:
    if sources:
        categories = {source.category for source in sources}
        if len(categories) == 1:
            category = next(iter(categories))
            return {
                "weather": "Weather Brief",
                "space": "Space Weather Brief",
                "news": "News Brief",
                "cyber": "Cyber Threat Brief",
                "maritime": "Maritime Brief",
            }[category]
    stem = page_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
    return f"{stem.title()} Brief"


def load_sources_document(path: Path) -> SourcesDocument:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SourcesDocument.model_validate(data)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Missing sources configuration: {path}") from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid sources configuration at {path}: {exc}") from exc
