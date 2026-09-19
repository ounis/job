"""AI-assisted web-search job provider.

An alternative source that does NOT depend on a dedicated jobs API (and so is
unaffected by JSearch quota/rate limits). It works in two steps:

  1. Query a general web-search API (Serper — google.serper.dev) for job-board
     results matching the keywords + location. This returns real, current SERP
     results as clean JSON (no scraping, no key-per-board).
  2. Hand the organic results (title, link, snippet) to the AI, which only
     STRUCTURES them into JobPosting fields. The AI never invents postings — it
     reshapes real search results, and any URL it returns that wasn't in the
     input is discarded (see ai.operations.structure_job_results).

Requires SERPER_API_KEY (free signup grants a one-time credit allowance, no
card). When unset, the provider reports is_configured() == False and is skipped,
exactly like Adzuna without its keys.

Note: the AI is used as a parser here, not a source of truth. With no AI backend
configured, a deterministic fallback maps the raw SERP results to postings.
"""
from __future__ import annotations

import hashlib

import httpx

from ..ai import operations as ai_ops
from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting
from .base import JobProvider

log = get_logger("app.providers.ai_web")

API_URL = "https://google.serper.dev/search"

# Job boards to bias the search toward individual postings (via an OR site:
# filter). Kept broad; the AI still filters out non-posting results.
_JOB_SITES = (
    "stepstone.de",
    "linkedin.com/jobs",
    "indeed.com",
    "xing.com/jobs",
    "join.com",
    "greenhouse.io",
    "lever.co",
)


import re
from urllib.parse import urlsplit

# Individual-posting URL signatures per board. A real posting URL matches one of
# these; anything else on that board is treated as a listing/category/search
# page and dropped. Boards not listed here are kept as-is (the AI still filters).
_POSTING_PATTERNS = {
    "stepstone.": re.compile(r"-inline\.html$|/stellenangebote--.*\d", re.I),
    "linkedin.": re.compile(r"/jobs/view/.*\d{6,}", re.I),
    "indeed.": re.compile(r"[?&]vjk=|/viewjob|/rc/clk", re.I),
    "xing.": re.compile(r"/jobs/[^/]+-\d+", re.I),
}

# Path fragments that mark a search/listing/category page on any board.
_LISTING_HINTS = re.compile(
    r"/(jobs|stellenangebote|stellen|suche|search|s|q|karriere|jobsuche)/?$"
    r"|/(jobs|stellen)/[a-z-]+/?$"          # e.g. /jobs/senior-qa-engineer
    r"|[?&](q|keywords|what|where|searchterm)=",
    re.I,
)


def _looks_like_listing(url: str) -> bool:
    """True if the URL is a search/category/listing page rather than a single job.

    Uses per-board posting signatures where known (a real posting must match),
    plus generic listing-path hints for everything else. Conservative: when a
    board is unknown and no listing hint matches, the URL is kept.
    """
    if not url:
        return True
    host = (urlsplit(url).hostname or "").lower()
    path_q = url[len(urlsplit(url).scheme) + 3 + len(host):]  # path + query

    for needle, posting_re in _POSTING_PATTERNS.items():
        if needle in host:
            # Known board: keep only if it looks like an individual posting.
            return not posting_re.search(url)

    # Unknown board: drop only if it clearly looks like a listing/search page.
    return bool(_LISTING_HINTS.search(path_q))


class AIWebProvider(JobProvider):
    name = "ai_web"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.serper_api_key
        self.num_results = max(1, settings.ai_web_results)

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

        primary = (keywords[0].strip() if keywords else "") or "jobs"
        loc = location.strip()
        low = loc.lower()
        if not loc or low in ("germany", "deutschland"):
            where = "Deutschland"
        elif low in ("remote", "remote work", "homeoffice", "home office", "online"):
            where = "remote Deutschland"
        else:
            where = f"{loc}, Deutschland"

        sites = " OR ".join(f"site:{s}" for s in _JOB_SITES)
        query = f"{primary} Stellenangebot {where} ({sites})"

        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        body = {
            "q": query,
            "gl": "de",   # country: Germany
            "hl": "de",   # interface language
            "num": min(self.num_results, 20),
        }

        log.info("POST %s q=%r num=%d", API_URL, query, body["num"])
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(API_URL, headers=headers, json=body)
                log.info("Serper HTTP %s", resp.status_code)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            log.warning("Serper request failed: %s", e)
            return []

        organic = data.get("organic") or []
        results = [
            {
                "title": (o.get("title") or "").strip(),
                "link": (o.get("link") or "").strip(),
                "snippet": (o.get("snippet") or "").strip(),
            }
            for o in organic
            if isinstance(o, dict) and (o.get("link") or "").strip()
        ]
        log.info("Serper returned %d organic result(s)", len(results))

        # Drop search/category/listing pages (e.g. stepstone.de/jobs/senior-qa-
        # engineer) — they aren't a specific posting and "Open posting" leads to
        # a generic list. Filtering here also saves the AI from structuring them.
        before = len(results)
        results = [r for r in results if not _looks_like_listing(r["link"])]
        if before - len(results):
            log.info("Dropped %d listing/category page(s)", before - len(results))
        if not results:
            return []

        # AI structures the real results into normalized job records.
        structured = ai_ops.structure_job_results(results, location_hint=where)
        log.info("Structured %d posting(s) from web results", len(structured))

        postings: list[JobPosting] = []
        for s in structured:
            postings.append(self._normalize(s))
            if len(postings) >= limit:
                break
        return postings

    def _normalize(self, item: dict) -> JobPosting:
        url = item.get("url", "")
        # SERP results have no stable id; derive one from the URL so the same
        # posting de-dupes across scans/locations.
        ext_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] if url else ""
        return JobPosting(
            provider=self.name,
            external_id=ext_id,
            title=item.get("title", ""),
            company=item.get("company", ""),
            location=item.get("location", ""),
            description=item.get("description", ""),
            url=url,
        )
