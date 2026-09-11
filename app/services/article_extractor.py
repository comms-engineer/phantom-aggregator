from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import trafilatura

from app.config import settings

logger = logging.getLogger("phantom-aggregator.article_extractor")

# File extensions that are never worth running HTML article extraction on.
# Custom fetchers (FIRMS KMZ, future GDACS/ASAM/GhostMaps sources, etc.) may
# put a non-HTML resource URL in an item's "link" field — trafilatura can't
# parse a zip/binary as an article, and trying just wastes the per-source
# extraction budget on a link that can never succeed.
_NON_ARTICLE_EXTENSIONS = {
    ".kmz", ".kml", ".zip", ".gz", ".tar",
    ".pdf", ".csv", ".json", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp3", ".mp4", ".wav",
}


def _looks_like_article_url(url: str) -> bool:
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    suffix = Path(path).suffix.lower()
    return suffix not in _NON_ARTICLE_EXTENSIONS


def article_uid_for_url(url: str) -> str:
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:12]
    return f"article_{digest}"


def extract_full_article(url: str) -> dict[str, Any]:
    article_uid = article_uid_for_url(url)
    response = requests.get(
        url,
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent": "phantom-aggregator/1.0"},
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type and not (content_type.startswith("text/") or "html" in content_type or "xml" in content_type):
        raise ValueError(f"Refusing to run article extraction on non-text content-type '{content_type}' for {url}")

    extracted = trafilatura.bare_extraction(response.text, url=url, with_metadata=True)
    if extracted is None:
        raise ValueError(f"Unable to extract article content from {url}")

    extracted_dict = extracted.as_dict() if hasattr(extracted, "as_dict") else dict(extracted)
    text_content = _clean_text(
        extracted_dict.get("text")
        or extracted_dict.get("raw_text")
        or trafilatura.extract(response.text, url=url, output_format="txt", include_formatting=False)
        or ""
    )
    if not text_content:
        raise ValueError(f"Article content was empty after extraction for {url}")

    title = _clean_text(extracted_dict.get("title") or "") or "Untitled Article"
    author = _clean_text(extracted_dict.get("author") or "")
    publish_date = _clean_text(extracted_dict.get("date") or "")

    return {
        "article_uid": article_uid,
        "url": url,
        "title": title,
        "author": author or None,
        "publish_date": publish_date or None,
        "text_content": text_content,
        "site_name": _clean_text(extracted_dict.get("sitename") or ""),
        "hostname": _clean_text(extracted_dict.get("hostname") or ""),
        "description": _clean_text(extracted_dict.get("description") or "") or None,
        "fetched_at": datetime.now(UTC).isoformat(),
        "content_type": response.headers.get("content-type", ""),
        "status_code": response.status_code,
        "extractor": "trafilatura",
    }


class ArticleExtractorService:
    _request_lock = asyncio.Lock()
    _last_request_monotonic = 0.0

    def __init__(
        self,
        raw_articles_dir: Path | None = None,
        nomadnet_articles_dir: Path | None = None,
    ) -> None:
        self.raw_articles_dir = raw_articles_dir or settings.raw_articles_dir
        self.nomadnet_articles_dir = nomadnet_articles_dir or settings.nomadnet_articles_dir

    async def enrich_items(
        self,
        items: list[dict[str, Any]],
        *,
        enabled: bool,
        max_articles: int,
    ) -> list[dict[str, Any]]:
        if not enabled or max_articles <= 0:
            return items

        enriched: list[dict[str, Any]] = []
        extracted_count = 0
        for item in items:
            updated = dict(item)
            link = str(updated.get("link") or updated.get("url") or "").strip()
            if link and not _looks_like_article_url(link):
                logger.debug("Skipping non-article link for extraction: %s", link)
                enriched.append(updated)
                continue
            if link and extracted_count < max_articles:
                article = await self._load_or_extract(link)
                if article:
                    updated["article_uid"] = article["article_uid"]
                    updated["article_page"] = f"articles/{article['article_uid']}.mu"
                    extracted_count += 1
            enriched.append(updated)
        return enriched

    async def _load_or_extract(self, url: str) -> dict[str, Any] | None:
        article_uid = article_uid_for_url(url)
        cached = self._load_cached_article(article_uid)
        if cached:
            return cached

        async with self._request_lock:
            elapsed = time.monotonic() - self._last_request_monotonic
            delay = max(0.0, float(settings.article_request_delay_seconds) - elapsed)
            if delay:
                await asyncio.sleep(delay)
            try:
                article = await asyncio.to_thread(extract_full_article, url)
            except Exception as exc:
                logger.warning("Full-article extraction failed for %s: %s", url, exc)
                self._last_request_monotonic = time.monotonic()
                return None
            self._last_request_monotonic = time.monotonic()

        await asyncio.to_thread(self._persist_article, article)
        return article

    def _load_cached_article(self, article_uid: str) -> dict[str, Any] | None:
        raw_path = self.raw_articles_dir / f"{article_uid}.json"
        page_path = self.nomadnet_articles_dir / f"{article_uid}.mu"
        if not raw_path.exists() or not page_path.exists():
            return None
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not payload.get("text_content"):
            return None
        return payload

    def _persist_article(self, article: dict[str, Any]) -> None:
        self.raw_articles_dir.mkdir(parents=True, exist_ok=True)
        self.nomadnet_articles_dir.mkdir(parents=True, exist_ok=True)

        article_uid = article["article_uid"]
        raw_path = self.raw_articles_dir / f"{article_uid}.json"
        page_path = self.nomadnet_articles_dir / f"{article_uid}.mu"

        raw_path.write_text(json.dumps(article, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        page_path.write_text(self._render_article_page(article), encoding="utf-8")

    def _render_article_page(self, article: dict[str, Any]) -> str:
        lines = [
            f"== {article.get('title', 'UNTITLED ARTICLE').upper()} ==",
            "----------------------------------------",
        ]
        if article.get("author"):
            lines.append(f"Author: {article['author']}")
        if article.get("publish_date"):
            lines.append(f"Published: {article['publish_date']}")
        lines.extend(
            [
                f"Source URL: {article.get('url', 'unknown')}",
                "",
            ]
        )

        paragraphs = [
            _clean_text(paragraph)
            for paragraph in str(article.get("text_content", "")).splitlines()
            if _clean_text(paragraph)
        ]
        if not paragraphs:
            paragraphs = ["Article text unavailable."]

        width = min(max(settings.nomadnet_line_limit, 72), 80)
        for paragraph in paragraphs:
            lines.extend(
                textwrap.wrap(
                    paragraph,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                    replace_whitespace=False,
                )
                or [paragraph]
            )
            lines.append("")

        lines.extend(["----------------------------------------", "Generated by phantom-aggregator"])
        return "\n".join(lines).strip() + "\n"


def _clean_text(value: str) -> str:
    paragraphs = [" ".join(block.split()) for block in re.split(r"\n\s*\n", str(value))]
    cleaned = "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
    return cleaned.strip()
