"""Swappable AI client abstraction.

The rest of the app depends only on AIClient.complete_json() / complete_text().
Today it's backed by OpenAI; swapping to another provider means writing one new
subclass and returning it from get_ai_client().
"""
from __future__ import annotations

import abc
import json
from typing import Any

from ..config import get_settings


class AIClient(abc.ABC):
    @abc.abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return a parsed JSON object from the model."""

    @abc.abstractmethod
    def complete_text(self, system: str, user: str) -> str:
        """Return free-form text from the model."""


class OpenAIClient(AIClient):
    """Client for OpenAI or any OpenAI-compatible server (e.g. Ollama).

    base_url=None -> OpenAI cloud. Set base_url to target a compatible server.
    """

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        from openai import OpenAI

        settings = get_settings()
        self._model = model
        # Ollama ignores the key but the SDK requires a non-empty string.
        self._client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self._max_tokens_json = settings.ai_max_tokens_json
        self._max_tokens_text = settings.ai_max_tokens_text

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=self._max_tokens_json,
        )
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)

    def complete_text(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=self._max_tokens_text,
        )
        return (resp.choices[0].message.content or "").strip()


def get_ai_client() -> AIClient:
    settings = get_settings()
    provider = settings.ai_provider.lower()

    if provider == "ollama":
        # Local, OpenAI-compatible server — no key required.
        return OpenAIClient(
            api_key="",
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
        )

    # Default: OpenAI cloud.
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env, or set AI_PROVIDER=ollama."
        )
    return OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)
