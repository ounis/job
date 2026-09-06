"""Pluggable job-provider interface.

Every data source implements JobProvider.search() and returns a list of
normalized JobPosting objects. Adding a new source (including the future
third-party API) is a matter of dropping a new module here and registering it
in providers/__init__.py's get_providers().
"""
from __future__ import annotations

import abc

from ..models import CVProfile, JobPosting


class JobProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def search(
        self,
        keywords: list[str],
        location: str,
        distance_km: int,
        limit: int,
        profile: CVProfile | None = None,
    ) -> list[JobPosting]:
        """Return normalized postings matching the query."""
        raise NotImplementedError

    def is_configured(self) -> bool:
        """Whether the provider has the credentials/config it needs."""
        return True
