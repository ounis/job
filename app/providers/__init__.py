"""Provider registry.

Returns the active, configured providers based on ACTIVE_PROVIDERS in .env.
To add a new source (e.g. the third-party API), implement JobProvider in a new
module and register it in _REGISTRY.
"""
from __future__ import annotations

from ..config import get_settings
from .adzuna import AdzunaProvider
from .base import JobProvider
from .bundesagentur import BundesagenturProvider

_REGISTRY = {
    "adzuna": AdzunaProvider,
    "bundesagentur": BundesagenturProvider,
}


def get_providers() -> list[JobProvider]:
    settings = get_settings()
    providers: list[JobProvider] = []
    for name in settings.provider_list:
        cls = _REGISTRY.get(name)
        if cls is None:
            continue
        providers.append(cls())
    return providers
