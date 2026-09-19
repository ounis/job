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

import asyncio

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
        # Effective queries already fetched during this scan. Several configured
        # locations collapse to the same JSearch free-text query (e.g. "online"
        # and "remote", or "deutschland" and "germany"), so we skip duplicates
        # to avoid wasting the limited API quota (free tier is easily rate-
        # limited to 429). One provider instance lives for one scan.
        self._queried: set[str] = set()
        # Set once the API returns 429 (quota/rate limit). Further queries this
        # scan are skipped — retrying just wastes time and stays rate-limited.
        self._rate_limited = False

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def warnings(self) -> list[str]:
        if self._rate_limited:
            return [
                "JSearch hit its rate limit (HTTP 429) during this scan, so some "
                "locations were skipped. Results may be incomplete. Lower "
                "JSEARCH_MAX_PAGES or the number of search locations, or upgrade "
                "your RapidAPI plan."
            ]
        return []

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
        if self._rate_limited:
            log.info("Skipping JSearch query for %r — already rate-limited this scan", location)
            return []

        # JSearch takes a free-text query. Encode the location into the query
        # since it has no separate distance param.
        loc = location.strip()
        low = loc.lower()
        # Use the PRIMARY keyword only. Joining all derived keywords into one
        # long phrase makes JSearch's free-text match return nothing (it treats
        # the whole string as one query). The first keyword is the main title.
        terms = (keywords[0].strip() if keywords else "") or "jobs"
        if not loc or low in ("germany", "deutschland"):
            query = f"{terms} in Germany"
        elif low in ("remote", "remote work", "homeoffice", "home office", "online"):
            # Work-arrangement keywords, not a place: search remote roles in DE.
            query = f"{terms} remote in Germany"
        else:
            query = f"{terms} in {loc}, Germany"

        # Skip a query we already ran this scan (multiple locations map to the
        # same free-text query). Saves API calls and avoids 429 rate limits.
        if query in self._queried:
            log.info("Skipping duplicate JSearch query %r (location %r)", query, location)
            return []
        self._queried.add(query)

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
        # JSearch can be slow/flaky — retry a couple of times on timeouts.
        data = None
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(1, 4):
                try:
                    resp = await client.get(API_URL, headers=headers, params=params)
                    log.info("JSearch HTTP %s (attempt %d)", resp.status_code, attempt)
                    if resp.status_code == 429:
                        # Rate limited / quota exhausted. Don't retry and don't
                        # query further locations this scan; log cleanly.
                        self._rate_limited = True
                        log.warning(
                            "JSearch rate-limited (HTTP 429) — skipping remaining "
                            "queries this scan. Lower JSEARCH_MAX_PAGES / SEARCH_LOCATION "
                            "count or upgrade the plan."
                        )
                        return []
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    if attempt == 3:
                        log.warning("JSearch failed after %d attempts: %s", attempt, e)
                        return []
                    await asyncio.sleep(1.5 * attempt)
        if data is None:
            return []

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
            url=_best_apply_link(item),
            salary_min=item.get("job_min_salary"),
            salary_max=item.get("job_max_salary"),
            created=item.get("job_posted_at_datetime_utc"),
        )


def _best_apply_link(item: dict) -> str:
    """Pick the most reliable apply URL from a JSearch item.

    JSearch aggregates Google for Jobs, so a single posting can carry several
    links of varying quality. Aggregator links (StepStone, LinkedIn, ...) often
    rotate, expire, or block non-browser access, so prefer a direct-employer
    link when JSearch flags one. Order of preference:

      1. apply_options entry marked is_direct (employer's own site / ATS)
      2. any apply_options entry with a link
      3. job_apply_link (the primary link)
      4. job_google_link (Google for Jobs — always reachable, good fallback)

    Returns "" when nothing usable is present; the render layer then falls back
    to a title+company web search so the user can still find the role.
    """
    options = item.get("apply_options")
    if isinstance(options, list):
        direct = [o for o in options if isinstance(o, dict) and o.get("is_direct")]
        for opt in (*direct, *options):
            if isinstance(opt, dict):
                link = (opt.get("apply_link") or "").strip()
                if link:
                    return link
    for field in ("job_apply_link", "job_google_link"):
        link = (item.get(field) or "").strip()
        if link:
            return link
    return ""
