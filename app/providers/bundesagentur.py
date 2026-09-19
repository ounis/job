"""Bundesagentur fuer Arbeit (Jobsuche) provider.

The German Federal Employment Agency exposes a public Jobsuche API — the largest
single source of vacancies in Germany, free to use. This provider queries the
current v6 endpoint and normalizes results into JobPosting.

API contract (see https://github.com/bundesAPI/jobsuche-api):
  Endpoint : GET .../pc/v6/jobs
  Auth     : header "X-API-Key: jobboerse-jobsuche" (public client id).
  Params   : was, wo, umkreis, size, page, angebotsart=1, pav=false.
  Response : { "ergebnisliste": [ {...} ], "maxErgebnisse": N }
             Each item (verified against the live v6 API):
               stellenangebotsTitel, firma, referenznummer,
               stellenlokationen[].adresse{ort,region}, hauptberuf,
               datumErsteVeroeffentlichung, externeUrl (sometimes).

The client id is configurable (BUNDESAGENTUR_API_KEY) and defaults to the public
value.
"""
from __future__ import annotations

import base64

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting
from .base import JobProvider

log = get_logger("app.providers.bundesagentur")

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
API_URL = f"{BASE}/pc/v6/jobs"
# Detail endpoint returns the full description; refnr is base64-encoded here
# (unlike the website URL, which uses the raw refnr).
DETAILS_URL = f"{BASE}/pc/v4/jobdetails/{{code}}"
DEFAULT_API_KEY = "jobboerse-jobsuche"
# Detail page: the website uses the raw (un-encoded) reference number.
# (Base64 encoding is only for the REST /jobdetails API endpoint, not the site.)
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
# A browser-like UA; the agency's edge rejects default python-httpx UAs.
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

        log.info("GET %s was=%r wo=%r umkreis=%s", API_URL, was, wo or "(DE-wide)", distance_km)
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            try:
                resp = await client.get(API_URL, params=params)
            except httpx.HTTPError as e:
                log.warning("Bundesagentur request error: %s", e)
                return []
            log.info("Bundesagentur HTTP %s", resp.status_code)
            if resp.status_code == 403:
                log.warning(
                    "Bundesagentur 403. Body: %s",
                    (resp.text or "")[:200].replace("\n", " "),
                )
                return []
            resp.raise_for_status()
            data = resp.json()

        raw = data.get("ergebnisliste", []) or []
        log.debug("Bundesagentur returned %d raw items (max %s)",
                  len(raw), data.get("maxErgebnisse"))
        postings: list[JobPosting] = []
        for item in raw:
            postings.append(self._normalize(item))
            if len(postings) >= limit:
                break
        log.info("Bundesagentur normalized %d postings (limit %d)", len(postings), limit)
        return postings

    def fetch_description(self, refnr: str) -> str:
        """Fetch the full job description for a reference number (on demand).

        The v6 search response has no description, so we call the detail endpoint
        (base64-encoded refnr) which returns 'stellenangebotsBeschreibung'.
        Returns '' on any failure.
        """
        if not refnr:
            return ""
        code = base64.b64encode(refnr.encode("utf-8")).decode("ascii")
        headers = {"X-API-Key": self.api_key, "User-Agent": _UA, "Accept": "application/json"}
        try:
            resp = httpx.get(DETAILS_URL.format(code=code), headers=headers, timeout=20)
            if resp.status_code != 200:
                log.info("Bundesagentur detail HTTP %s for %s", resp.status_code, refnr)
                return ""
            return (resp.json().get("stellenangebotsBeschreibung") or "").strip()
        except Exception as e:
            log.info("Bundesagentur detail fetch failed for %s: %s", refnr, e)
            return ""

    def _normalize(self, item: dict) -> JobPosting:
        # Location comes from the first Stellenlokation's address.
        loks = item.get("stellenlokationen") or []
        adr = (loks[0].get("adresse") if loks and isinstance(loks[0], dict) else {}) or {}
        city = adr.get("ort") or ""
        region = adr.get("region") or ""
        location = ", ".join(p for p in (city, region) if p) or "Deutschland"

        refnr = item.get("referenznummer") or ""
        # externeUrl (employer's own posting) wins; otherwise link to the
        # agency detail page using the raw reference number.
        if item.get("externeUrl"):
            url = item["externeUrl"]
        elif refnr:
            url = DETAIL_URL.format(refnr=refnr)
        else:
            url = ""

        return JobPosting(
            provider=self.name,
            external_id=str(refnr),
            title=(item.get("stellenangebotsTitel") or item.get("hauptberuf") or "").strip(),
            company=item.get("firma") or "",
            location=location,
            # The search response has no full description; the occupation is the
            # best short summary available without a second detail call.
            description=item.get("hauptberuf") or "",
            url=url,
            created=item.get("datumErsteVeroeffentlichung"),
        )
