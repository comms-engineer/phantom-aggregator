# Source Discovery Guide

`phantom-aggregator` now reads source definitions from `/app/config/sources.json`, so operators and AI assistants can register new feeds without editing application code or restarting the container.

## What Makes a Good Source

- Publicly reachable over direct HTTP(S)
- No JavaScript rendering, login wall, or Cloudflare bot challenge
- Lightweight payloads, ideally under 2 MB per request
- Structured output:
  - RSS 2.0
  - Atom XML
  - Plain JSON from a public API
  - Plain-text feeds for compact operational notices

## Manual Discovery Workflow

1. Start with the publisher's homepage, newsroom, status page, or data portal.
2. Look for common feed patterns such as:
   - `feed.xml`
   - `rss.xml`
   - `/rss`
   - `/atom`
   - `/feed`
   - `/api`
3. Check headers before committing to a source:

   ```bash
   curl -I https://example.org/feed.xml
   ```

4. Prefer sources with:
   - `content-type` showing XML, JSON, or plain text
   - small `content-length`
   - stable hostnames and predictable update cadence
5. Run a quick compatibility test:

   ```bash
   python cli/manage_sources.py validate --url https://example.org/feed.xml --type rss
   ```

## AI-Assisted Discovery Prompts

Use prompts like these with Gemini, Copilot, or another assistant:

- `Find 10 low-bandwidth RSS or Atom feeds for Pacific weather alerts and maritime notices. Only include direct public feed URLs.`
- `List public JSON threat-intelligence or vulnerability feeds that do not require authentication and return lightweight structured responses.`
- `Find plain-text or RSS feeds for African regional news that are suitable for polling every 60 to 360 minutes over constrained links.`
- `Suggest public space weather, disaster, and infrastructure-status feeds for field operations. Include the feed URL, format, and why it is bandwidth-friendly.`

When using AI-generated suggestions, always verify them yourself with `manage_sources.py validate` before adding them.

## Compatibility Checklist

Before registering a source, confirm:

- Direct public access works with `curl` or `requests`
- No browser automation is required
- Response body is preferably smaller than 2 MB
- Format is valid RSS, Atom, JSON, or plain text
- Update frequency matches the configured `poll_interval_mins`
- The source belongs on the intended `nomadnet_page`

## Registering a New Source

List current sources:

```bash
python cli/manage_sources.py list
```

Add a new source after validation:

```bash
python cli/manage_sources.py add \
  --type rss \
  --category news \
  --name "BBC World" \
  --url "https://feeds.bbci.co.uk/news/world/rss.xml"
```

Disable or re-enable a source:

```bash
python cli/manage_sources.py toggle --id bbc_world
```

## `sources.json` Notes

Each source entry includes:

- `id`: stable machine-friendly identifier
- `name`: human-friendly label
- `type`: `rss`, `atom`, `json_api`, or `text_feed`
- `category`: `weather`, `space`, `news`, `cyber`, or `maritime`
- `url`: primary endpoint
- `enabled`: include or exclude the source without deleting it
- `poll_interval_mins`: desired refresh interval
- `llm_summarize`: whether to generate a summary
- `nomadnet_page`: target output page

Specialized JSON sources may also include an `options` object for parser-specific fields, such as NOAA Space Weather's auxiliary endpoints.
