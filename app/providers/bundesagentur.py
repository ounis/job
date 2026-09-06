"""Bundesagentur fuer Arbeit (Jobsuche) provider — STUB.

The German Federal Employment Agency exposes a public Jobsuche API. It is a
legitimate, free, Germany-wide source and a good second backbone. This stub
implements the JobProvider interface so it can be flipped on later by adding
`bundesagentur` to ACTIVE_PROVIDERS and completing the request wiring below.

Left as a stub on purpose: the API requires a client id header that changes
occasionally, so we don't hard-code a possibly-stale value. Fill in `search`
when you want to enable it.
"""
from __future__ import annotations

from ..models import CVProfile, JobPosting
from .base import JobProvider


class BundesagenturProvider(JobProvider):
    name = "bundesagentur"

    def is_configured(self) -> bool:
        # Not wired yet — returns no results until implemented.
        return False

    async def search(
        self,
        keywords: list[str],
        location: str,
        distance_km: int,
        limit: int,
        profile: CVProfile | None = None,
    ) -> list[JobPosting]:
        # TODO: call https://rest.arbeitsagentur.de/jobboerse/jobsuche-service
        # and normalize results into JobPosting. Kept empty so the app runs
        # cleanly with only Adzuna active.
        return []
