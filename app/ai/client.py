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
    def __init__(self) -> None:
        from openai import OpenAI

        settings = get_settings()
        self._model = settings.openai_model
        self._client = OpenAI(api_key=settings.openai_api_key)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
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
        )
        return (resp.choices[0].message.content or "").strip()


def get_ai_client() -> AIClient:
    settings = get_settings()
    if not settings.ai_enabled:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to .env to enable AI features."
        )
    return OpenAIClient()
