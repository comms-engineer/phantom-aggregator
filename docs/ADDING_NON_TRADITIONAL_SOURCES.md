# Adding Non-Traditional Sources

This guide explains how to add localized or non-traditional feeds (highway notices, utility status pages, local alerts) using an external LLM plus a built-in importer.

## 1) Collect raw source data

1. Open the source page or endpoint you want to ingest.
2. Copy one representative raw payload:
   - full HTML source, or
   - JSON/API response body, or
   - RSS/XML/text payload.
3. Keep enough content to show repeated records and timestamps.

## 2) Generate a custom fetcher with an external LLM

1. Open `/home/runner/work/phantom-aggregator/phantom-aggregator/templates/SOURCE_ADAPTER_PROMPT.md`.
2. Copy the template into Gemini, ChatGPT, or Claude.
3. Fill placeholders:
   - `SOURCE_ID` (example: `custom_traffic`)
   - `DISPLAY_NAME`
   - `CATEGORY` (`weather`, `space`, `news`, `cyber`, `maritime`)
   - `SOURCE_URL`
   - raw payload in `RAW_SOURCE_DATA`.
4. Ask the LLM to produce the two required fenced outputs (Python + JSON).

## 3) Save the generated Python module locally

1. Copy the generated Python block into a local file, for example:

   ```bash
   /tmp/custom_traffic.py
   ```

2. Optional: keep the generated JSON snippet nearby for review.

## 4) Import and register it in phantom-aggregator

Run:

```bash
python cli/import_custom_fetcher.py --file /tmp/custom_traffic.py --name custom_traffic
```

What this command does:
- validates the file syntax and fetcher class shape
- confirms one `BaseFetcher` subclass with `fetch()` and `save_raw()`
- copies the module into `/app/fetchers/custom/` (repo path: `app/fetchers/custom/`)
- appends a valid source entry to `/app/config/sources.json` (repo path: `config/sources.json`)

Useful optional flags:

```bash
python cli/import_custom_fetcher.py \
  --file /tmp/custom_traffic.py \
  --name custom_traffic \
  --display-name "Regional Traffic Alerts" \
  --category weather \
  --url "https://traffic.example.gov/advisories" \
  --poll-interval-mins 30 \
  --nomadnet-page weather.page
```

## 5) Start ingesting immediately

Once imported, the source is available through the normal dynamic source workflow and will be picked up on the next sync cycle.

You can verify registration with:

```bash
python cli/manage_sources.py list
```
