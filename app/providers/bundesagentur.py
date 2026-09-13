"""Bundesagentur fuer Arbeit (Jobsuche) provider.

The German Federal Employment Agency exposes a public Jobsuche API — the largest
single source of vacancies in Germany, free to use. This provider queries it and
normalizes results into JobPosting.

API contract (see https://github.com/bundesAPI/jobsuche-api):
  Endpoint : GET .../pc/v6/jobs  (falls back to /pc/v4/jobs)
  Auth     : header "X-API-Key: jobboerse-jobsuche" (public client id).
  Params   : was, wo, umkreis, size, page, angebotsart=1, pav=false.
  Response : { "stellenangebote": [ {...} ], "maxErgebnisse": N }
             (v6 items use "referenznummer"; v4 items use "refnr".)

The client id is configurable (BUNDESAGENTUR_API_KEY) and defaults to the public
value.
"""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting
from .base import JobProvider

log = get_logger("app.providers.bundesagentur")

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
# Try v6 first (current), fall back to v4 if it 404s.
API_URLS = [f"{BASE}/pc/v6/jobs", f"{BASE}/pc/v4/jobs"]
# Public client id published by the agency for the Jobsuche frontend.
DEFAULT_API_KEY = "jobboerse-jobsuche"
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
# A browser-like UA; some edges reject default httpx/python UAs.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class BundesagenturProvider(JobProvider):
    name = "bundesagentur"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = getattr(settings, "bundesagentur_api_key", "") or DEFAULT_API_KEY

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
        was = " ".join(keywords).strip() or "Jobs"
        wo = location.strip()
        if wo.lower() in ("germany", "deutschland"):
            wo = ""

        params = {
            "was": was,
            "angebotsart": "1",   # 1 = Arbeit (regular jobs)
            "pav": "false",
            "size": str(max(1, min(limit, 100))),
            "page": "1",
        }
        if wo:
            params["wo"] = wo
            params["umkreis"] = str(distance_km)

        headers = {
            "X-API-Key": self.api_key,
            "User-Agent": _UA,
            "Accept": "application/json",
        }

        data = None
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            for url in API_URLS:
                log.info("GET %s was=%r wo=%r umkreis=%s", url, was, wo or "(DE-wide)", distance_km)
                try:
                    resp = await client.get(url, params=params)
                except httpx.HTTPError as e:
                    log.warning("Bundesagentur request error on %s: %s", url, e)
                    continue
                log.info("Bundesagentur HTTP %s (%s)", resp.status_code, url)
                if resp.status_code == 404:
                    # Endpoint version not found — try the next one.
                    continue
                if resp.status_code == 403:
                    # Not necessarily geo — often a temporary WAF/rate block or a
                    # changed auth requirement. Log the body to aid debugging.
                    log.warning(
                        "Bundesagentur 403 on %s. Body: %s", url,
                        (resp.text or "")[:200].replace("\n", " "),
                    )
                    return []
                resp.raise_for_status()
                data = resp.json()
                break

        if data is None:
            log.warning("Bundesagentur: no endpoint responded successfully.")
            return []

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
        # v6 uses "referenznummer"; v4 uses "refnr".
        refnr = item.get("referenznummer") or item.get("refnr") or ""
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
