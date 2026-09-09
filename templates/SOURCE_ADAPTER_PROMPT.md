# SOURCE ADAPTER META-PROMPT (FOR EXTERNAL LLM USE)

Copy this entire prompt into Gemini, ChatGPT, or Claude, then replace the placeholders.

---

You are generating **one production-ready Python fetcher module** for the `phantom-aggregator` project.

## Inputs
- `SOURCE_ID`: `{{SOURCE_ID}}` (example: `custom_traffic`)
- `DISPLAY_NAME`: `{{DISPLAY_NAME}}`
- `CATEGORY`: `{{CATEGORY}}` (`weather`, `space`, `news`, `cyber`, or `maritime`)
- `SOURCE_URL`: `{{SOURCE_URL}}`
- `RAW_SOURCE_DATA`: paste raw HTML, JSON, RSS, text, or API response below:

```text
{{PASTE_RAW_SOURCE_DATA_HERE}}
```

## Your task
1. Analyze `RAW_SOURCE_DATA` and identify recurring records/events.
2. Generate exactly **one Python file** that defines exactly **one class** inheriting `BaseFetcher`.
3. Use `requests` and/or `BeautifulSoup` (from `bs4`) only when needed to extract key fields:
   - Title
   - Summary
   - Timestamp
   - Severity/Status
4. Normalize records to this shape:
   - `{"title": str, "timestamp": str, "category": str, "content": str, "metadata": dict}`
5. Keep text compact and bandwidth-friendly for local NomadNet display.
6. Ensure the class has:
   - `async def fetch(self) -> dict[str, Any]`
   - `async def save_raw(self, payload: dict[str, Any]) -> None`
7. Constructor compatibility requirement:
   - Accept these keyword arguments (with safe defaults where possible): `source_url`, `source_name`, `raw_dir`, `name`, `options`
8. The returned payload from `fetch()` must include:
   - `fetched_at` (ISO timestamp)
   - `source`
   - `items` (list of normalized dictionaries)
   - `preview` (small summary object/string)
9. Include robust error handling and fallbacks for missing fields.

## Output format requirements (strict)
Return exactly two fenced blocks and nothing else:

1. A `python` fenced block containing the complete module.
2. A `json` fenced block containing a single source entry for `config/sources.json`.

The JSON block must follow this structure:

```json
{
  "id": "{{SOURCE_ID}}",
  "name": "{{DISPLAY_NAME}}",
  "type": "json_api",
  "category": "{{CATEGORY}}",
  "url": "{{SOURCE_URL}}",
  "enabled": true,
  "poll_interval_mins": 60,
  "llm_summarize": true,
  "nomadnet_page": "{{CATEGORY}}.mu",
  "options": {
    "custom_fetcher": "app.fetchers.custom.{{SOURCE_ID}}:{{CLASS_NAME}}"
  }
}
```

## Constraints
- No external services besides the target source URL.
- No authentication assumptions.
- No markdown outside the two required fenced blocks.
- Python must be directly saveable as `{{SOURCE_ID}}.py` and importable without edits.
