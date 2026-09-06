"""Adzuna job search provider (Germany).

Adzuna offers a free REST API (https://developer.adzuna.com/). Register for an
app id + key and put them in .env. This is a legitimate API — no scraping, no
ToS violation. Each posting includes a redirect/application URL.

Endpoint:
  GET https://api.adzuna.com/v1/api/jobs/de/search/{page}
    ?app_id=...&app_key=...&what=...&where=...&distance=...
    &results_per_page=...&sort_by=relevance&content-type=application/json
"""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..models import CVProfile, JobPosting
from .base import JobProvider

API_BASE = "https://api.adzuna.com/v1/api/jobs/de/search"


class AdzunaProvider(JobProvider):
    name = "adzuna"

    def __init__(self) -> None:
        settings = get_settings()
        self.app_id = settings.adzuna_app_id
        self.app_key = settings.adzuna_app_key

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    async def search(
        self,
        keywords: list[str],
        location: str,
        distance_km: int,
        limit: int,
        profile: CVProfile | None = None,
    ) -> list[JobPosting]:
        if not self.is_configured():
            return []

        what = " ".join(keywords).strip()
        # "Germany" means country-wide: Adzuna treats an empty `where` as all of DE.
        where = "" if location.strip().lower() in ("", "germany", "deutschland") else location

        results_per_page = min(50, max(1, limit))
        pages_needed = (limit + results_per_page - 1) // results_per_page

        postings: list[JobPosting] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(1, pages_needed + 1):
                params = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": results_per_page,
                    "sort_by": "relevance",
                    "content-type": "application/json",
                }
                if what:
                    params["what"] = what
                if where:
                    params["where"] = where
                    params["distance"] = distance_km

                resp = await client.get(f"{API_BASE}/{page}", params=params)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("results", []):
                    postings.append(self._normalize(item))
                    if len(postings) >= limit:
                        return postings

                if not data.get("results"):
                    break
        return postings

    def _normalize(self, item: dict) -> JobPosting:
        company = (item.get("company") or {}).get("display_name", "")
        loc = (item.get("location") or {}).get("display_name", "")
        return JobPosting(
            provider=self.name,
            external_id=str(item.get("id", "")),
            title=item.get("title", "").strip(),
            company=company,
            location=loc,
            description=item.get("description", ""),
            url=item.get("redirect_url", ""),
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            created=item.get("created"),
        )
