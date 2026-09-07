from __future__ import annotations

import argparse
import ast
import py_compile
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.source_config import SourceConfigLoader, SourceDefinition, default_page_for_category, slugify_source_id


class FetcherValidationError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import an LLM-generated custom fetcher into phantom-aggregator")
    parser.add_argument("--file", required=True, help="Path to generated Python fetcher file")
    parser.add_argument("--name", required=True, help="Source id to register (example: custom_traffic)")
    parser.add_argument("--display-name", help="Human-friendly source name")
    parser.add_argument("--category", default="news", choices=["weather", "space", "news", "cyber", "maritime"])
    parser.add_argument("--url", help="Optional display URL, defaults to custom://<name>")
    parser.add_argument("--poll-interval-mins", default=60, type=int)
    parser.add_argument("--nomadnet-page", help="Optional NomadNet page override")
    parser.add_argument("--disable", action="store_true", help="Register source in disabled state")
    parser.add_argument("--no-llm-summarize", action="store_true", help="Disable summarization for this source")
    parser.add_argument("--config", default=str(settings.sources_config_path), help="Path to sources.json")
    return parser


def _base_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _validate_generated_fetcher(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise FetcherValidationError(f"Fetcher file not found: {path}")
    if path.suffix != ".py":
        raise FetcherValidationError("Fetcher file must be a .py file")

    py_compile.compile(str(path), doraise=True)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fetcher_classes: list[ast.ClassDef] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(_base_name(base) == "BaseFetcher" for base in node.bases):
            fetcher_classes.append(node)

    if len(fetcher_classes) != 1:
        raise FetcherValidationError("Fetcher file must define exactly one class that inherits BaseFetcher")

    fetcher_class = fetcher_classes[0]
    members = {
        node.name
        for node in fetcher_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "fetch" not in members:
        raise FetcherValidationError("Fetcher class must implement fetch()")
    if "save_raw" not in members:
        raise FetcherValidationError("Fetcher class must implement save_raw()")
    return fetcher_class.name


def _copy_fetcher(source_path: Path, source_id: str) -> Path:
    destination_dir = ROOT / "app" / "fetchers" / "custom"
    destination_dir.mkdir(parents=True, exist_ok=True)
    init_path = destination_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""Custom fetchers generated outside the repository."""\n', encoding="utf-8")

    destination = destination_dir / f"{source_id}.py"
    if destination.exists():
        raise FetcherValidationError(f"Custom fetcher already exists: {destination}")

    shutil.copy2(source_path, destination)
    return destination


def _register_source(args: argparse.Namespace, source_id: str, class_name: str) -> None:
    config_path = Path(args.config).resolve()
    loader = SourceConfigLoader(config_path)
    document = loader.load(force=True)
    if any(source.id == source_id for source in document.sources):
        raise FetcherValidationError(f"Source id already exists in config: {source_id}")

    source_name = args.display_name or source_id.replace("_", " ").title()
    source_url = args.url or f"custom://{source_id}"
    source = SourceDefinition(
        id=source_id,
        name=source_name,
        type="json_api",
        category=args.category,
        url=source_url,
        enabled=not args.disable,
        poll_interval_mins=args.poll_interval_mins,
        llm_summarize=not args.no_llm_summarize,
        nomadnet_page=args.nomadnet_page or default_page_for_category(args.category),
        options={"custom_fetcher": f"app.fetchers.custom.{source_id}:{class_name}"},
    )
    document.sources.append(source)
    loader.save(document)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    source_path = Path(args.file).resolve()
    source_id = slugify_source_id(args.name)
    try:
        class_name = _validate_generated_fetcher(source_path)
        destination = _copy_fetcher(source_path, source_id)
        _register_source(args, source_id, class_name)
        print(f"Imported custom fetcher: {source_id}")
        print(f"  class: {class_name}")
        print(f"  file: {destination}")
        print(f"  config: {Path(args.config).resolve()}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
