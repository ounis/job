"""JSearch (RapidAPI) job search provider.

JSearch aggregates Google for Jobs, which itself pulls from LinkedIn, Indeed,
Glassdoor, ZipRecruiter, company career pages and more — so a single call spans
many boards. This is the broadest single source wired into the app.

Requires a RapidAPI key (subscribe to JSearch on RapidAPI; free tier available):
  https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch

Endpoint:
  GET https://jsearch.p.rapidapi.com/search-v2
    headers: x-rapidapi-key, x-rapidapi-host
    params : query, page, num_pages, country, date_posted
    NOTE: /search-v2 nests results under data.jobs (the older /search returned
          data as a flat list). _normalize handles the item shape either way.
"""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting
from .base import JobProvider

log = get_logger("app.providers.jsearch")

API_URL = "https://jsearch.p.rapidapi.com/search-v2"
API_HOST = "jsearch.p.rapidapi.com"
RESULTS_PER_PAGE = 10  # JSearch returns ~10 per page


class JSearchProvider(JobProvider):
    name = "jsearch"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.rapidapi_key
        self.max_pages = settings.jsearch_max_pages

    def is_configured(self) -> bool:
        return bool(self.api_key)

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

        # JSearch takes a free-text query. Encode the location into the query
        # since it has no separate distance param; "Germany" stays country-wide.
        loc = location.strip()
        terms = " ".join(keywords).strip() or "jobs"
        if loc and loc.lower() not in ("germany", "deutschland"):
            query = f"{terms} in {loc}, Germany"
        else:
            query = f"{terms} in Germany"

        num_pages = max(1, (limit + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
        # Clamp to the plan's page limit (free tier = 1) to avoid errors/quota
        # waste from requesting more pages than the subscription allows.
        max_pages = max(1, self.max_pages)
        if num_pages > max_pages:
            log.info("Clamping num_pages %d -> %d (jsearch_max_pages)", num_pages, max_pages)
            num_pages = max_pages
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": API_HOST,
        }
        params = {
            "query": query,
            "page": "1",
            "num_pages": str(num_pages),
            "country": "de",
            "date_posted": "month",
        }

        log.info("GET %s query=%r country=de num_pages=%d", API_URL, query, num_pages)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(API_URL, headers=headers, params=params)
            log.info("JSearch HTTP %s", resp.status_code)
            resp.raise_for_status()
            data = resp.json()

        # /search-v2 nests results under data.jobs (a dict); older /search
        # returned data as a list directly. Handle both.
        payload = data.get("data")
        if isinstance(payload, dict):
            raw = payload.get("jobs", []) or []
        else:
            raw = payload or []
        log.debug("JSearch returned %d raw items", len(raw))
        postings: list[JobPosting] = []
        for item in raw:
            postings.append(self._normalize(item))
            if len(postings) >= limit:
                break
        log.info("JSearch normalized %d postings (limit %d)", len(postings), limit)
        return postings

    def _normalize(self, item: dict) -> JobPosting:
        city = item.get("job_city") or ""
        country = item.get("job_country") or ""
        location = ", ".join(p for p in (city, country) if p)
        return JobPosting(
            provider=self.name,
            external_id=str(item.get("job_id", "")),
            title=(item.get("job_title") or "").strip(),
            company=item.get("employer_name") or "",
            location=location,
            description=item.get("job_description") or "",
            url=item.get("job_apply_link") or "",
            salary_min=item.get("job_min_salary"),
            salary_max=item.get("job_max_salary"),
            created=item.get("job_posted_at_datetime_utc"),
        )
