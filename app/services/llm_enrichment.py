from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)

# Ollama on this CPU-only host processes one generation at a time; sending concurrent
# requests causes all of them to queue and blow past even generous client timeouts.
_llm_request_semaphore = asyncio.Semaphore(1)

# CPU-bound prompt eval/generation is slow (~i5-6500T, no GPU); keep inputs and outputs
# bounded so a summarization call reliably finishes instead of timing out.
_MAX_INPUT_CHARS = 1400
_MAX_OUTPUT_TOKENS = 220

# Shared rule for every category: ground the output in the input, never invent
# urgency, timelines, or recommended actions the data doesn't support. This is
# what stops the model from writing "assess vulnerabilities" / "within 24 hours"
# style filler on feeds that don't actually contain any elevated risk.
_BASE_RULES = (
    "Only state facts that are explicitly present in the input below. "
    "Do not invent dates, deadlines, timeframes, or recommended actions that "
    "are not directly supported by the input. "
    "If nothing in the input indicates elevated risk or required action, say so "
    "plainly instead of manufacturing urgency. "
    "Keep bullets concise and plain text, no markdown."
)

# Per-category framing. Each one tells the model what this feed actually is and
# what a useful bullet looks like for it, instead of forcing every feed through
# an "urgent/actionable/operational" lens that only fits emergency sources.
_CATEGORY_PROMPTS: dict[str, str] = {
    "cyber": (
        "You are summarizing a vulnerability disclosure feed (e.g. CISA KEV) for a "
        "field operator. For each entry mentioned in the input, note the CVE ID, "
        "affected vendor/product, and why it matters (e.g. actively exploited, "
        "remote code execution) only if that detail is present in the input. "
        "Do not assign your own urgency/impact ratings unless the source data "
        "labels them that way."
    ),
    "space": (
        "You are summarizing space weather telemetry (K-index, solar flux, storm "
        "risk level) for a field operator. Report the actual reported values and "
        "the stated storm risk level. Only mention operational impacts (HF comms, "
        "GPS, satellite drag) if the reported K-index or storm risk level in the "
        "input indicates a real elevated condition — a low/none reading should be "
        "summarized as routine, not padded with hypothetical precautions."
    ),
    "news": (
        "You are summarizing general news headlines for a field operator's daily "
        "brief. Give a neutral, factual digest of what each headline is about. "
        "This is informational only — do not convert headlines into security "
        "action items, threat assessments, or recommended operational responses "
        "unless the article is explicitly about a direct, immediate threat to the "
        "reader's own operations."
    ),
    "weather": (
        "You are summarizing a weather/disaster alert feed (FEMA, NHC, wildfire, "
        "flood, or earthquake data) for a field operator. Prioritize the actual "
        "alert level, location, and stated instructions from the input. Do not "
        "add advice beyond what the source alert itself states."
    ),
    "maritime": (
        "You are summarizing a maritime advisory feed for a field operator. "
        "Report the reported conditions, affected zones, and any stated advisory "
        "level from the input without adding speculative guidance."
    ),
}

_DEFAULT_CATEGORY_PROMPT = (
    "You are summarizing a field information feed for a low-bandwidth terminal. "
    "Extract the key factual points from the input."
)


class LLMEnricher:
    """Optional local LLM summarization helper with graceful fallback."""

    def __init__(
        self,
        enabled: bool | None = None,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> None:
        self.enabled = settings.enable_llm_summarization if enabled is None else enabled
        self.endpoint = settings.llm_endpoint if endpoint is None else endpoint
        self.model = settings.llm_model if model is None else model
        self.timeout_seconds = settings.llm_timeout_seconds

    async def summarize_content(self, raw_text: str, context_type: str) -> str:
        input_text = (raw_text or "").strip()
        if not input_text:
            return "No content available."
        if not self.enabled:
            return self.fallback_text(input_text)
        input_text = input_text[:_MAX_INPUT_CHARS]

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with _llm_request_semaphore:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    payload = self._request_payload(input_text, context_type)
                    async with session.post(self.endpoint, json=payload) as response:
                        response.raise_for_status()
                        body = await response.json()
            summary = self._extract_response_text(body)
            summary = self._trim_to_complete_sentence(summary)
            return summary or self.fallback_text(input_text)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning("LLM enrichment failed for %s: %s", context_type, exc)
            return self.fallback_text(input_text)

    def _request_payload(self, input_text: str, context_type: str) -> dict[str, Any]:
        category_prompt = _CATEGORY_PROMPTS.get(context_type, _DEFAULT_CATEGORY_PROMPT)
        system_prompt = f"{category_prompt}\n\n{_BASE_RULES}"
        user_prompt = f"Input:\n{input_text}\n\nOutput 3 to 5 bullet points."

        if self.endpoint.rstrip("/").endswith("/api/generate"):
            return {
                "model": self.model,
                "prompt": f"{system_prompt}\n\n{user_prompt}",
                "stream": False,
                "options": {"num_predict": _MAX_OUTPUT_TOKENS},
            }

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }

    def _extract_response_text(self, body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        if isinstance(body.get("response"), str):
            return body["response"].strip()
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"].strip()
                if isinstance(first.get("text"), str):
                    return first["text"].strip()
        return ""

    def _trim_to_complete_sentence(self, text: str) -> str:
        """Avoid rendering a bullet list that cuts off mid-sentence because the
        model hit the token cap. Trims back to the last bullet/sentence that
        actually finished."""
        if not text:
            return text
        stripped = text.rstrip()
        # If it already ends cleanly, keep it.
        if stripped.endswith((".", "!", "?", ":")):
            return stripped
        # Otherwise cut back to the last sentence-ending punctuation found.
        match = list(re.finditer(r"[.!?](?:\s|$)", stripped))
        if match:
            end = match[-1].end()
            return stripped[:end].rstrip()
        return stripped

    def fallback_text(self, text: str, limit: int = 1800) -> str:
        truncated = " ".join(text.split())
        if len(truncated) <= limit:
            return truncated
        return f"{truncated[:limit].rstrip()}..."
