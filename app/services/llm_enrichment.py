from __future__ import annotations

import logging
from typing import Any

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


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
            return self._fallback(input_text)

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = self._request_payload(input_text, context_type)
                async with session.post(self.endpoint, json=payload) as response:
                    response.raise_for_status()
                    body = await response.json()
            summary = self._extract_response_text(body)
            return summary or self._fallback(input_text)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning("LLM enrichment failed for %s: %s", context_type, exc)
            return self._fallback(input_text)

    def _request_payload(self, input_text: str, context_type: str) -> dict[str, Any]:
        system_prompt = (
            "You are a tactical intelligence assistant for low-bandwidth field terminals. "
            "Extract 3 to 5 high-priority actionable bullet points. "
            "Focus on immediate impacts, urgency, and operational relevance. "
            "Keep bullets concise and plain text."
        )
        user_prompt = f"Context: {context_type}\n\nInput:\n{input_text}\n\nOutput as bullet points."

        if self.endpoint.rstrip("/").endswith("/api/generate"):
            return {
                "model": self.model,
                "prompt": f"{system_prompt}\n\n{user_prompt}",
                "stream": False,
            }

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
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

    def _fallback(self, text: str, limit: int = 1800) -> str:
        truncated = " ".join(text.split())
        if len(truncated) <= limit:
            return truncated
        return f"{truncated[:limit].rstrip()}..."
