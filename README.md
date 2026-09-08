# phantom-aggregator

`phantom-aggregator` pulls public feeds, normalizes them, and renders low-bandwidth NomadNet pages for field use.

It runs as a FastAPI service with scheduled sync jobs. Source definitions are loaded from JSON, so you can add or disable feeds without changing application code.

## What it does

- Fetches RSS, Atom, JSON API, and plain-text sources
- Supports parser-specific handlers for NOAA SWPC and CISA KEV
- Optionally summarizes snapshots with a local LLM endpoint
- Optionally extracts full article pages from feed links
- Renders NomadNet pages plus an index page
- Captures system telemetry and renders a system status page
- Evaluates snapshots for critical events and can send LXMF alerts
- Cleans old raw snapshots on a daily retention schedule

## Repository layout

- `app/` service code
- `cli/` source management and custom fetcher import tools
- `config/sources.json` dynamic source definitions
- `data/raw/` raw source snapshots and extracted article JSON
- `data/nomadnet/` rendered NomadNet pages and extracted article pages
- `docs/` operator guides

## Requirements

- Python 3.11+
- Network access to configured source URLs
- Optional local LLM server if LLM summarization is enabled
- Optional LXMF toolchain if alert dispatch is enabled

## Quick start (local)

```bash
git clone https://github.com/comms-engineer/phantom-aggregator.git
cd phantom-aggregator
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

Service listens on `http://0.0.0.0:8080`.

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build -d
```

The container mounts:

- `./data` -> `/app/data`
- `./config` -> `/app/config`

## Runtime flow

On startup, the service:

1. Creates runtime directories under `DATA_DIR` and `CONFIG_DIR`
2. Schedules two jobs:
   - `sync_sources` every `SOURCE_SYNC_INTERVAL_SECONDS`
   - storage cleanup once per day
3. Forces an initial sync
4. Exposes HTTP endpoints for health, sources, rendered pages, and raw snapshots

During each sync cycle, the service:

1. Reloads `/app/config/sources.json` if it changed
2. Refreshes sources that are due by `poll_interval_mins`
3. Optionally enriches feed items with extracted full articles
4. Summarizes payloads (LLM or fallback text)
5. Saves raw snapshots
6. Evaluates critical alerts
7. Captures system telemetry
8. Renders NomadNet pages and index

## HTTP API

- `GET /health` -> service health status
- `GET /sources` -> current parsed source document
- `GET /pages/{name}` -> rendered NomadNet page content
- `GET /raw/{name}` -> raw snapshot JSON for a source ID

## Source configuration

Source definitions live in `/app/config/sources.json` (repo path: `config/sources.json`).

Supported values:

- `type`: `rss`, `atom`, `json_api`, `text_feed`
- `category`: `weather`, `space`, `news`, `cyber`, `maritime`
- `nomadnet_page`: must end with `.page` or `.mu`

Each source entry includes:

- `id` (lowercase letters, numbers, underscore)
- `name`
- `type`
- `category`
- `url`
- `enabled`
- `poll_interval_mins`
- `llm_summarize`
- `nomadnet_page`
- `fetch_full_articles` (optional override)
- `max_articles_per_feed` (optional override)
- `options` (optional parser/custom settings)

### Built-in parser options

- `options.parser = "space_weather_swpc"`
  - requires `options.solar_flux_url`
  - requires `options.forecast_url`
- `options.parser = "cisa_kev"`

### Custom fetcher option

Set:

- `options.custom_fetcher = "app.fetchers.custom.<module>:<ClassName>"`

The runtime imports the class dynamically and uses it as a `BaseFetcher` implementation.

## CLI tools

### List configured sources

```bash
python cli/manage_sources.py list
```

### Validate a candidate source URL

```bash
python cli/manage_sources.py validate --url "https://example.org/feed.xml" --type rss
```

### Add a source

```bash
python cli/manage_sources.py add \
  --type rss \
  --category news \
  --name "Example News" \
  --url "https://example.org/feed.xml"
```

### Toggle source state

```bash
python cli/manage_sources.py toggle --id example_news
```

### Import a custom fetcher

```bash
python cli/import_custom_fetcher.py --file /tmp/custom_source.py --name custom_source
```

Import flow validates the Python file, copies it to `app/fetchers/custom/`, and appends a source entry to `config/sources.json`.

## Full-article extraction

When enabled for a source, feed items with `link`/`url` are enriched with:

- `article_uid`
- `article_page` path under `articles/<article_uid>.mu`

Stored outputs:

- raw article JSON: `data/raw/articles/*.json`
- rendered article pages: `data/nomadnet/articles/*.mu`

Extraction uses `trafilatura` and request throttling via `ARTICLE_REQUEST_DELAY_SECONDS`.

## Critical alerting

The alert service checks snapshots for:

- space weather K-index threshold breaches
- keyword hits in KEV and feed item content

If `LXMF_ALERTING_ENABLED=true`, it dispatches messages using `LXMF_ALERT_COMMAND` to each destination in `LXMF_ALERT_DESTINATIONS`.

## System telemetry

Each sync cycle also captures:

- CPU usage and temperature
- memory usage
- storage usage for the data directory
- internet reachability check
- mesh interface status

Outputs:

- raw JSON snapshot: `data/raw/system_health.json`
- NomadNet page: `data/nomadnet/system.page`

## Configuration reference

Environment variables are loaded from `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Application log level |
| `DATA_DIR` | `/app/data` | Runtime data root |
| `CONFIG_DIR` | `/app/config` | Runtime config root |
| `REQUEST_TIMEOUT_SECONDS` | `20` | HTTP timeout for source fetches |
| `LLM_TIMEOUT_SECONDS` | `10` | HTTP timeout for LLM requests |
| `SOURCE_SYNC_INTERVAL_SECONDS` | `60` | Scheduler interval for source refresh checks |
| `RAW_DATA_MAX_AGE_DAYS` | `14` | Retention period for raw snapshots |
| `FETCH_FULL_ARTICLES` | `true` | Global default for article extraction |
| `MAX_ARTICLES_PER_FEED` | `5` | Global cap on extracted articles per source refresh |
| `ARTICLE_REQUEST_DELAY_SECONDS` | `2` | Delay between article extraction requests |
| `SYSTEM_HEALTH_PAGE_NAME` | `system.page` | Output filename for telemetry page |
| `SYSTEM_HEALTH_SNAPSHOT_NAME` | `system_health` | Raw snapshot basename for telemetry JSON |
| `CRITICAL_ALERT_K_INDEX_THRESHOLD` | `5` | Threshold for geomagnetic storm alerts |
| `CRITICAL_ALERT_KEYWORDS` | built-in keyword tuple | Keywords for critical alert matching |
| `LXMF_ALERTING_ENABLED` | `false` | Enable or disable LXMF dispatch |
| `LXMF_ALERT_DESTINATIONS` | `()` | Destination hashes for LXMF alerts |
| `LXMF_ALERT_COMMAND` | `()` | Command template for LXMF dispatch |
| `MESH_INTERFACE_NAMES` | built-in tuple | Interface name tokens treated as mesh links |
| `INTERNET_CONNECTIVITY_HOST` | `1.1.1.1` | Host used for reachability check |
| `INTERNET_CONNECTIVITY_PORT` | `53` | Port used for reachability check |
| `ENABLE_LLM_SUMMARIZATION` | `false` | Global switch for LLM summarization |
| `LLM_ENDPOINT` | `http://host.docker.internal:11434/api/generate` | LLM HTTP endpoint |
| `LLM_MODEL` | `qwen2.5:7b` | LLM model name |
| `NOMADNET_LINE_LIMIT` | `80` | Wrapping width limit |
| `NOMADNET_MAX_LINES` | `80` | Max rendered lines per page |

Tuple-style settings can be provided as JSON arrays in `.env`.

## Operations notes

- Source config changes are picked up automatically on the next sync cycle.
- A source can be disabled without removing it from config.
- The cleaner preserves the latest snapshot in each source group and deletes older expired files.
- Corrupt source snapshots are skipped during rendering instead of crashing the service.

## Related docs

- `docs/SOURCE_DISCOVERY_GUIDE.md`
- `docs/ADDING_NON_TRADITIONAL_SOURCES.md`
- `templates/SOURCE_ADAPTER_PROMPT.md`

## To Add

- localized services such as pulsepoint, watchduty, etc
- AI-based assessment of news trends to identify crises and spin up event tracking pages
- methodology for users to scope LLM work based on specific location, situations, and PIRs
- find publicly available intelligence analysis guides to help guide LLMs. 
- ingestion of telegram channels
- export of key data elements to CoT tracks distributed over TAK
- ???
