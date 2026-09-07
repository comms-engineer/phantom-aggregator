from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.fetchers.cyber_kev import CyberKevFetcher
from app.fetchers.rss_news import RssNewsFetcher
from app.fetchers.space_weather import SpaceWeatherFetcher
from app.renderers.nomadnet import NomadNetRenderer
from app.services.llm_enrichment import LLMEnricher

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phantom-aggregator")

scheduler = AsyncIOScheduler()
renderer = NomadNetRenderer()
llm_enricher = LLMEnricher()
space_weather_fetcher = SpaceWeatherFetcher()
cyber_kev_fetcher = CyberKevFetcher()
rss_news_fetcher = RssNewsFetcher()


async def _run_pipeline(
    *,
    fetcher: Any,
    context_type: str,
    page_name: str,
    render_fn: Any,
) -> dict[str, Any]:
    """Fetch, save raw data, summarize, and render a NomadNet page."""

    payload = await fetcher.fetch()
    await fetcher.save_raw(payload)
    raw_text = json.dumps(payload, ensure_ascii=False)
    summary = await llm_enricher.summarize_content(raw_text=raw_text, context_type=context_type)
    page_content = render_fn(payload, summary)
    page_path = renderer.write_page(page_name, page_content)
    logger.info("Updated %s page: %s", context_type, page_path)
    return payload


async def run_space_weather_pipeline() -> None:
    try:
        await _run_pipeline(
            fetcher=space_weather_fetcher,
            context_type="space_weather",
            page_name="space.page",
            render_fn=renderer.render_space_weather,
        )
        renderer.write_index()
    except Exception:
        logger.exception("Space weather pipeline failed")


async def run_cyber_kev_pipeline() -> None:
    try:
        await _run_pipeline(
            fetcher=cyber_kev_fetcher,
            context_type="cyber_threats",
            page_name="cyber.page",
            render_fn=renderer.render_cyber_kev,
        )
        renderer.write_index()
    except Exception:
        logger.exception("CISA KEV pipeline failed")


async def run_rss_pipeline() -> None:
    try:
        payload = await _run_pipeline(
            fetcher=rss_news_fetcher,
            context_type="news_and_alerts",
            page_name="news.page",
            render_fn=renderer.render_news,
        )
        weather_text = json.dumps(payload.get("items", []), ensure_ascii=False)
        weather_summary = await llm_enricher.summarize_content(
            raw_text=weather_text,
            context_type="weather_alerts",
        )
        weather_page = renderer.render_weather(payload, weather_summary)
        weather_path = renderer.write_page("weather.page", weather_page)
        logger.info("Updated weather page: %s", weather_path)
        renderer.write_index()
    except Exception:
        logger.exception("RSS pipeline failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    settings.nomadnet_dir.mkdir(parents=True, exist_ok=True)

    scheduler.add_job(
        run_space_weather_pipeline,
        trigger="interval",
        minutes=settings.space_weather_interval_minutes,
        max_instances=1,
        coalesce=True,
        id="space_weather",
    )
    scheduler.add_job(
        run_rss_pipeline,
        trigger="interval",
        minutes=settings.rss_interval_minutes,
        max_instances=1,
        coalesce=True,
        id="rss_news",
    )
    scheduler.add_job(
        run_cyber_kev_pipeline,
        trigger="interval",
        minutes=settings.cyber_kev_interval_minutes,
        max_instances=1,
        coalesce=True,
        id="cyber_kev",
    )
    scheduler.start()

    await asyncio.gather(
        run_space_weather_pipeline(),
        run_rss_pipeline(),
        run_cyber_kev_pipeline(),
    )
    renderer.write_index()
    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="phantom-aggregator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/pages/{name}", response_class=PlainTextResponse)
async def get_page(name: str) -> str:
    page_path = settings.nomadnet_dir / name
    if not page_path.exists() or not page_path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return page_path.read_text(encoding="utf-8")


@app.get("/raw/{name}")
async def get_raw(name: str) -> dict:
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
