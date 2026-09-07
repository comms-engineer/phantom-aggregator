from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.fetchers.cyber_kev import CyberKevFetcher
from app.fetchers.json_api import JsonApiFetcher
from app.fetchers.rss_news import RssNewsFetcher
from app.fetchers.space_weather import SpaceWeatherFetcher
from app.fetchers.text_feed import TextFeedFetcher
from app.renderers.nomadnet import NomadNetRenderer
from app.services.llm_enrichment import LLMEnricher
from app.source_config import SourceConfigLoader, SourceDefinition

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phantom-aggregator")

scheduler = AsyncIOScheduler()
renderer = NomadNetRenderer()
llm_enricher = LLMEnricher()
source_loader = SourceConfigLoader(settings.sources_config_path)
last_runs: dict[str, datetime] = {}
sync_lock = asyncio.Lock()


def _raw_snapshot_path(source_id: str) -> Path:
    return settings.raw_dir / f"{source_id}.json"


def _build_fetcher(source: SourceDefinition) -> Any:
    parser = source.options.get("parser")
    if parser == "space_weather_swpc":
        return SpaceWeatherFetcher(
            raw_dir=settings.raw_dir,
            name=source.id,
            k_index_url=source.url,
            solar_flux_url=source.options["solar_flux_url"],
            forecast_url=source.options["forecast_url"],
        )
    if parser == "cisa_kev":
        return CyberKevFetcher(raw_dir=settings.raw_dir, source_url=source.url, name=source.id)
    if source.type in {"rss", "atom"}:
        return RssNewsFetcher(raw_dir=settings.raw_dir, feeds=[source.url], name=source.id)
    if source.type == "text_feed":
        return TextFeedFetcher(url=source.url, source_name=source.name, raw_dir=settings.raw_dir, name=source.id)
    return JsonApiFetcher(url=source.url, source_name=source.name, raw_dir=settings.raw_dir, name=source.id)


async def _summarize_source(source: SourceDefinition, payload: dict[str, Any]) -> str:
    raw_text = json.dumps(payload, ensure_ascii=False)
    if source.llm_summarize:
        return await llm_enricher.summarize_content(raw_text=raw_text, context_type=source.category)
    return llm_enricher.fallback_text(raw_text)


async def _refresh_source(source: SourceDefinition) -> None:
    fetcher = _build_fetcher(source)
    payload = await fetcher.fetch()
    summary = await _summarize_source(source, payload)
    snapshot = {
        "source": source.model_dump(mode="json"),
        "fetched_at": payload.get("fetched_at", datetime.now(UTC).isoformat()),
        "summary": summary,
        "payload": payload,
    }
    await fetcher.save_raw(snapshot)
    last_runs[source.id] = datetime.now(UTC)
    logger.info("Updated source %s -> %s", source.id, _raw_snapshot_path(source.id))


def _load_snapshot(source_id: str) -> dict[str, Any] | None:
    snapshot_path = _raw_snapshot_path(source_id)
    if not snapshot_path.exists() or not snapshot_path.is_file():
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Ignoring corrupt raw snapshot for source %s", source_id)
        return None


def _is_due(source: SourceDefinition, now: datetime) -> bool:
    last_run = last_runs.get(source.id)
    if last_run is None:
        snapshot = _load_snapshot(source.id)
        if snapshot:
            fetched_at = snapshot.get("fetched_at")
            try:
                last_run = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
                last_runs[source.id] = last_run
            except ValueError:
                last_run = None
    if last_run is None:
        return True
    return now - last_run >= timedelta(minutes=source.poll_interval_mins)


def _cleanup_stale_pages(active_pages: set[str]) -> None:
    for page_path in settings.nomadnet_dir.glob("*.page"):
        if page_path.name not in active_pages:
            page_path.unlink(missing_ok=True)


def _render_pages() -> None:
    document = source_loader.load()
    grouped: dict[str, list[tuple[SourceDefinition, dict[str, Any] | None]]] = defaultdict(list)
    for source in document.enabled_sources():
        grouped[source.nomadnet_page].append((source, _load_snapshot(source.id)))

    active_pages = set(grouped.keys())
    _cleanup_stale_pages(active_pages)

    for page_name, snapshots in grouped.items():
        renderer.write_page(page_name, renderer.render_dynamic_page(page_name, snapshots))
    renderer.write_dynamic_index(document.enabled_sources())


async def sync_sources(*, force: bool = False) -> None:
    async with sync_lock:
        document = source_loader.load()
        now = datetime.now(UTC)
        due_sources = [source for source in document.enabled_sources() if force or _is_due(source, now)]

        if due_sources:
            results = await asyncio.gather(*[_refresh_source(source) for source in due_sources], return_exceptions=True)
            for source, result in zip(due_sources, results, strict=False):
                if isinstance(result, Exception):
                    logger.error("Source refresh failed for %s: %s", source.id, result)

        _render_pages()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    settings.nomadnet_dir.mkdir(parents=True, exist_ok=True)
    settings.config_dir.mkdir(parents=True, exist_ok=True)

    scheduler.add_job(
        sync_sources,
        trigger="interval",
        seconds=settings.source_sync_interval_seconds,
        max_instances=1,
        coalesce=True,
        id="dynamic_sources",
    )
    scheduler.start()

    await sync_sources(force=True)
    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="phantom-aggregator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sources")
async def get_sources() -> dict[str, Any]:
    return source_loader.load().model_dump(mode="json")


@app.get("/pages/{name}", response_class=PlainTextResponse)
async def get_page(name: str) -> str:
    page_path = settings.nomadnet_dir / name
    if not page_path.exists() or not page_path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return page_path.read_text(encoding="utf-8")


@app.get("/raw/{name}")
async def get_raw(name: str) -> dict[str, Any]:
    raw_path = settings.raw_dir / f"{name}.json"
    if not raw_path.exists() or not raw_path.is_file():
        raise HTTPException(status_code=404, detail="Raw feed not found")
    try:
        return json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Corrupt raw data") from exc


async def _serve() -> None:
    import uvicorn

    config = uvicorn.Config(app=app, host="0.0.0.0", port=8080, log_level=settings.log_level.lower())
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(_serve())
