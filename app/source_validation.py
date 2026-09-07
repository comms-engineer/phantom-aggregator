from __future__ import annotations

import json
from typing import Any

import feedparser
import requests

from app.source_config import SourceType


def validate_source_url(url: str, source_type: SourceType | None = None, timeout: int = 15) -> dict[str, Any]:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "phantom-aggregator-source-validator/1.0"},
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    body = response.content

    if source_type == "json_api":
        parsed = response.json()
        return _json_result(url, parsed, content_type)
    if source_type == "text_feed":
        return _text_result(url, response.text, content_type)
    if source_type in {"rss", "atom"}:
        return _feed_result(url, body, content_type, expected_type=source_type)

    try:
        parsed = response.json()
        return _json_result(url, parsed, content_type)
    except (requests.JSONDecodeError, json.JSONDecodeError, ValueError):
        pass

    parsed_feed = feedparser.parse(body)
    if parsed_feed.entries or parsed_feed.feed:
        detected = "atom" if "atom" in str(parsed_feed.version).lower() else "rss"
        return _feed_result(url, body, content_type, expected_type=detected)

    return _text_result(url, response.text, content_type)


def _feed_result(url: str, body: bytes, content_type: str, expected_type: str) -> dict[str, Any]:
    parsed = feedparser.parse(body)
    if not parsed.entries and not parsed.feed:
        raise ValueError(f"{url} did not parse as a valid {expected_type.upper()} feed")
    return {
        "ok": True,
        "detected_type": "atom" if "atom" in str(parsed.version).lower() else expected_type,
        "content_type": content_type,
        "title": parsed.feed.get("title", "untitled-feed"),
        "item_count": len(parsed.entries),
        "message": "Feed parsed successfully.",
    }


def _json_result(url: str, payload: Any, content_type: str) -> dict[str, Any]:
    parser = None
    message = "JSON endpoint is readable."
    if isinstance(payload, dict) and isinstance(payload.get("vulnerabilities"), list):
        parser = "cisa_kev"
        message = "JSON endpoint is readable and matches the CISA KEV structure."
    elif isinstance(payload, list) and payload and isinstance(payload[0], list):
        parser = "space_weather_swpc"
        message = "JSON endpoint is readable and looks compatible with NOAA SWPC table data."

    return {
        "ok": True,
        "detected_type": "json_api",
        "content_type": content_type,
        "top_level": type(payload).__name__,
        "message": message,
        "parser": parser,
    }


def _text_result(url: str, text: str, content_type: str) -> dict[str, Any]:
    cleaned_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not cleaned_lines:
        raise ValueError(f"{url} returned an empty text payload")
    return {
        "ok": True,
        "detected_type": "text_feed",
        "content_type": content_type,
        "line_count": len(cleaned_lines),
        "preview": cleaned_lines[:3],
        "message": "Plain-text feed is reachable.",
    }
