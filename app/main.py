from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.fetchers.space_weather import SpaceWeatherFetcher
from app.renderers.nomadnet import NomadNetRenderer

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phantom-aggregator")

scheduler = AsyncIOScheduler()
fetcher = SpaceWeatherFetcher()
renderer = NomadNetRenderer()


async def run_space_weather_pipeline() -> None:
    """Fetch, store, and render space weather outputs."""

    try:
        payload = await fetcher.fetch()
        await fetcher.save_raw(payload)

        page = renderer.render_space_weather(payload)
        page_path = renderer.write_page("space_weather.txt", page)
        logger.info("Updated space weather page: %s", page_path)
    except Exception:
        logger.exception("Space weather pipeline failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    settings.nomadnet_pages_dir.mkdir(parents=True, exist_ok=True)

    scheduler.add_job(
        run_space_weather_pipeline,
        trigger="interval",
        minutes=settings.fetch_interval_minutes,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    await run_space_weather_pipeline()
    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="phantom-aggregator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/pages/{name}", response_class=PlainTextResponse)
async def get_page(name: str) -> str:
    page_path = settings.nomadnet_pages_dir / name
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
