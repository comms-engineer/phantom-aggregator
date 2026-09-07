from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.source_config import SourceConfigLoader, SourceDefinition, SourceType, default_page_for_category, load_sources_document, slugify_source_id
from app.source_validation import validate_source_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage phantom-aggregator sources.json")
    parser.add_argument("--config", default=str(settings.sources_config_path), help="Path to sources.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List configured sources")

    validate_parser = subparsers.add_parser("validate", help="Validate a source URL")
    validate_parser.add_argument("--url", required=True)
    validate_parser.add_argument("--type", choices=["rss", "atom", "json_api", "text_feed"])

    toggle_parser = subparsers.add_parser("toggle", help="Enable or disable a source")
    toggle_parser.add_argument("--id", required=True)

    add_parser = subparsers.add_parser("add", help="Validate and append a source")
    add_parser.add_argument("--id", help="Optional explicit source id")
    add_parser.add_argument("--type", required=True, choices=["rss", "atom", "json_api", "text_feed"])
    add_parser.add_argument("--category", required=True, choices=["weather", "space", "news", "cyber", "maritime"])
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--url", required=True)
    add_parser.add_argument("--poll-interval-mins", type=int, default=60)
    add_parser.add_argument("--nomadnet-page", help="Override output page name")
    add_parser.add_argument("--disable", action="store_true", help="Add the source in a disabled state")
    add_parser.add_argument("--no-llm-summarize", action="store_true", help="Disable summarization for this source")
    return parser


def command_list(config_path: Path) -> int:
    document = load_sources_document(config_path)
    rows = [
        [
            source.id,
            "enabled" if source.enabled else "disabled",
            source.type,
            source.category,
            str(source.poll_interval_mins),
            source.nomadnet_page,
            source.name,
            source.url,
        ]
        for source in document.sources
    ]
    headers = ["id", "state", "type", "category", "poll", "page", "name", "url"]
    print(_format_table(headers, rows))
    return 0


def command_validate(url: str, source_type: SourceType | None) -> int:
    result = validate_source_url(url, source_type)
    print(json.dumps(result, indent=2))
    return 0


def command_toggle(config_path: Path, source_id: str) -> int:
    loader = SourceConfigLoader(config_path)
    document = loader.load(force=True)
    for index, source in enumerate(document.sources):
        if source.id != source_id:
            continue
        document.sources[index] = source.model_copy(update={"enabled": not source.enabled})
        loader.save(document)
        state = "enabled" if document.sources[index].enabled else "disabled"
        print(f"{source_id} is now {state}")
        return 0
    raise SystemExit(f"Unknown source id: {source_id}")


def command_add(args: argparse.Namespace, config_path: Path) -> int:
    validation = validate_source_url(args.url, args.type)
    loader = SourceConfigLoader(config_path)
    document = loader.load(force=True)

    source_id = args.id or slugify_source_id(args.name)
    if any(source.id == source_id for source in document.sources):
        raise SystemExit(f"Source id already exists: {source_id}")

    source = SourceDefinition(
        id=source_id,
        name=args.name,
        type=args.type,
        category=args.category,
        url=args.url,
        enabled=not args.disable,
        poll_interval_mins=args.poll_interval_mins,
        llm_summarize=not args.no_llm_summarize,
        nomadnet_page=args.nomadnet_page or default_page_for_category(args.category),
        options=_build_options(args.type, validation),
    )
    document.sources.append(source)
    loader.save(document)

    print(f"Added source {source.id}")
    print(json.dumps(validation, indent=2))
    return 0


def _build_options(source_type: SourceType, validation: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    parser = validation.get("parser")
    if source_type == "json_api" and isinstance(parser, str):
        options["parser"] = parser
    return options


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    def render_row(values: list[str]) -> str:
        return "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(values))

    if not rows:
        return "No sources configured."
    separator = "  ".join("-" * width for width in widths)
    output = [render_row(headers), separator]
    output.extend(render_row(row) for row in rows)
    return "\n".join(output)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config_path = Path(args.config).resolve()

    try:
        if args.command == "list":
            return command_list(config_path)
        if args.command == "validate":
            return command_validate(args.url, args.type)
        if args.command == "toggle":
            return command_toggle(config_path, args.id)
        if args.command == "add":
            return command_add(args, config_path)
        parser.error(f"Unsupported command: {args.command}")
    except Exception as exc:  # pragma: no cover
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
