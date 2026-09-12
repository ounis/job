"""Bundesagentur fuer Arbeit (Jobsuche) provider.

The German Federal Employment Agency exposes a public Jobsuche API — the largest
single source of vacancies in Germany, free to use. This provider queries it and
normalizes results into JobPosting.

API contract (see https://github.com/bundesAPI/jobsuche-api):
  Endpoint : GET https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs
  Auth     : header "X-API-Key" with a public client id ("jobboerse-jobsuche").
  Params   : was (keywords), wo (location), umkreis (radius km), size, page.
  Response : { "stellenangebote": [ {...} ], "maxErgebnisse": N }

The client id is configurable (BUNDESAGENTUR_API_KEY) and defaults to the public
value. The endpoint is IP-restricted to Germany/EU by the agency, so it may
return 403 from other regions — run the app from a German connection if so.
"""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting
from .base import JobProvider

log = get_logger("app.providers.bundesagentur")

API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
# Public client id published by the agency for the Jobsuche frontend.
DEFAULT_API_KEY = "jobboerse-jobsuche"
# Detail page pattern for building a human-facing apply/view link from a refnr.
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"


class BundesagenturProvider(JobProvider):
    name = "bundesagentur"

    def __init__(self) -> None:
        settings = get_settings()
        # Falls back to the public client id so it works out of the box.
        self.api_key = getattr(settings, "bundesagentur_api_key", "") or DEFAULT_API_KEY

    def is_configured(self) -> bool:
        # Always configured: it uses a public client id by default.
        return bool(self.api_key)

    async def search(
        self,
        keywords: list[str],
        location: str,
        distance_km: int,
        limit: int,
        profile: CVProfile | None = None,
    ) -> list[JobPosting]:
        was = " ".join(keywords).strip() or "Jobs"
        # "Germany" isn't a valid 'wo'; leave it blank for country-wide.
        wo = location.strip()
        if wo.lower() in ("germany", "deutschland"):
            wo = ""

        params = {
            "was": was,
            "size": str(max(1, min(limit, 100))),
            "page": "1",
        }
        if wo:
            params["wo"] = wo
            params["umkreis"] = str(distance_km)

        headers = {"X-API-Key": self.api_key}
        log.info("GET %s was=%r wo=%r umkreis=%s", API_URL, was, wo or "(DE-wide)", distance_km)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(API_URL, headers=headers, params=params)
            log.info("Bundesagentur HTTP %s", resp.status_code)
            if resp.status_code == 403:
                log.warning(
                    "Bundesagentur returned 403 — the API is geo-restricted to "
                    "Germany/EU. Run from a German connection to use this source."
                )
                return []
            resp.raise_for_status()
            data = resp.json()

        raw = data.get("stellenangebote", []) or []
        log.debug("Bundesagentur returned %d raw items (max %s)",
                  len(raw), data.get("maxErgebnisse"))
        postings: list[JobPosting] = []
        for item in raw:
            postings.append(self._normalize(item))
            if len(postings) >= limit:
                break
        log.info("Bundesagentur normalized %d postings (limit %d)", len(postings), limit)
        return postings

    def _normalize(self, item: dict) -> JobPosting:
        arbeitsort = item.get("arbeitsort") or {}
        city = arbeitsort.get("ort") or ""
        region = arbeitsort.get("region") or ""
        location = ", ".join(p for p in (city, region) if p) or "Deutschland"
        refnr = item.get("refnr") or ""
        # externeUrl points to the employer's posting when present; otherwise
        # build the agency's own detail page from the reference number.
        url = item.get("externeUrl") or (DETAIL_URL.format(refnr=refnr) if refnr else "")
        return JobPosting(
            provider=self.name,
            external_id=str(refnr),
            title=(item.get("titel") or item.get("beruf") or "").strip(),
            company=item.get("arbeitgeber") or "",
            location=location,
            description=item.get("beruf") or "",
            url=url,
            created=item.get("aktuelleVeroeffentlichungsdatum"),
        )
