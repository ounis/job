"""Orchestration services tying providers + AI + DB + PDF together.

  - ensure_profile : parse the CV once (re-parse only when the file changes)
  - run_scan       : query providers, score new jobs, persist; keep applied/
                     ignored jobs sticky so they're omitted from fresh matching
                     but still visible with their status.
  - apply_to_job   : generate tailored CV + letter, render PDFs, store payload,
                     mark applied, and return the application URL to open.
"""
from __future__ import annotations

from . import db
from .ai import operations as ai_ops
from .ai.cv_reader import extract_text, hash_file
from .config import get_settings
from .logging_setup import get_logger
from datetime import datetime, timezone

from .models import CVProfile, EventType, JobEvent, JobStatus, ScoredJob
from .pdf.render import render_cv_pdf, render_letter_pdf
from .providers import get_providers

log = get_logger("app.services")


class ProfileError(RuntimeError):
    pass


def ensure_profile(force: bool = False) -> CVProfile:
    settings = get_settings()
    cv_path = settings.cv_full_path
    if not cv_path.exists():
        log.error("CV not found at %s", cv_path)
        raise ProfileError(
            f"CV not found at {cv_path}. Set CV_PATH in .env and place your CV there."
        )

    current_hash = hash_file(cv_path)
    if not force:
        existing = db.load_profile()
        if existing and db.load_profile_hash() == current_hash:
            log.info("Using cached CV profile (unchanged: %s)", cv_path.name)
            return existing

    log.info("Parsing CV %s (force=%s, ai_enabled=%s)", cv_path.name, force, settings.ai_enabled)
    raw = extract_text(cv_path)
    log.debug("Extracted %d chars of CV text", len(raw))
    if not raw.strip():
        log.error("No text extracted from CV %s", cv_path)
        raise ProfileError("Could not extract any text from the CV file.")
    profile = ai_ops.parse_cv(raw)
    db.save_profile(profile, current_hash)
    log.info(
        "Parsed profile: seniority=%s, %.1f yrs, %d skills, %d titles",
        profile.seniority.value, profile.years_experience,
        len(profile.skills), len(profile.titles),
    )
    log.debug("Skills: %s", ", ".join(profile.skills))
    return profile


def _is_excluded(posting, settings) -> bool:
    """True if a posting matches any configured exclusion (case-insensitive
    substring match). Covers company, location, title, description and source."""
    def _hit(haystack: str, needles: list[str]) -> bool:
        h = (haystack or "").lower()
        return any(n in h for n in needles)

    if _hit(posting.company, settings.exclude_companies_list):
        return True
    if _hit(posting.location, settings.exclude_locations_list):
        return True
    if _hit(posting.title, settings.exclude_title_keywords_list):
        return True
    if _hit(posting.description, settings.exclude_description_keywords_list):
        return True
    if _hit(posting.source, settings.exclude_sources_list):
        return True
    return False


async def run_scan(force_profile: bool = False) -> dict:
    """Run a full scan. Returns a small summary dict for the UI."""
    settings = get_settings()
    log.info("=== Scan started (force_profile=%s) ===", force_profile)
    profile = ensure_profile(force=force_profile)
    keywords = ai_ops.derive_keywords(profile)
    locations = settings.location_list
    log.info("Search keywords: %s", ", ".join(keywords) or "(none)")
    log.info(
        "Search config: locations=%s distance=%dkm max_results=%d min_relevance=%d",
        locations, settings.search_distance_km,
        settings.max_results, settings.min_relevance,
    )

    seen = db.get_seen_keys()
    log.debug("%d jobs already tracked in DB", len(seen))
    providers = get_providers()
    configured = [p.name for p in providers if p.is_configured()]
    unconfigured = [p.name for p in providers if not p.is_configured()]
    log.info("Active providers: configured=%s unconfigured=%s", configured, unconfigured)
    if not configured:
        log.warning("No providers are configured — scan will return no jobs. "
                    "Check API keys in .env (e.g. RAPIDAPI_KEY / ADZUNA_*).")

    fetched = 0
    new_scored = 0
    skipped_seen = 0

    for provider in providers:
        if not provider.is_configured():
            log.info("Skipping provider %r (not configured)", provider.name)
            continue
        # Query the provider once per configured location, then merge + de-dupe
        # by job key so the same posting from two locations isn't double-counted.
        merged: dict[str, object] = {}
        for loc in locations:
            log.info("Querying provider %r for location %r ...", provider.name, loc)
            try:
                loc_postings = await provider.search(
                    keywords=keywords,
                    location=loc,
                    distance_km=settings.search_distance_km,
                    limit=settings.max_results,
                    profile=profile,
                )
            except Exception:
                log.exception("Provider %r failed for location %r", provider.name, loc)
                continue
            log.info("Provider %r @ %r returned %d postings", provider.name, loc, len(loc_postings))
            for p in loc_postings:
                merged.setdefault(p.key, p)
        postings = list(merged.values())
        log.info("Provider %r merged %d unique postings across %d location(s)",
                 provider.name, len(postings), len(locations))

        # Apply user exclusions (companies, locations, title/description
        # keywords, sources). Dropped jobs are neither scored nor stored.
        before = len(postings)
        postings = [p for p in postings if not _is_excluded(p, settings)]
        dropped = before - len(postings)
        if dropped:
            log.info("Excluded %d posting(s) from %r by user filters", dropped, provider.name)
        fetched += len(postings)

        # Collect the new (unseen) postings for this provider first.
        fresh = []
        for posting in postings:
            if posting.key in seen:
                log.debug("Skip already-seen %s (%s)", posting.key, posting.title)
                skipped_seen += 1
                continue
            fresh.append(posting)
            seen.add(posting.key)

        # Token-saving pre-filter: when AI is enabled and ai_prefilter is on,
        # cheaply heuristic-score every fresh posting, then spend AI scoring
        # tokens only on the top N candidates. The rest keep their heuristic
        # score. This avoids one AI call per job (the dominant scan cost).
        ai_budget = _ai_score_budget(profile, fresh, settings)

        for posting in fresh:
            if posting.key in ai_budget:
                relevance = ai_ops.score_relevance(profile, posting)
            else:
                relevance = ai_ops._score_fallback(profile, posting)
            log.debug(
                "Scored %d | %s @ %s | matched=%s (ai=%s)",
                relevance.score, posting.title, posting.company,
                relevance.matched_skills, posting.key in ai_budget,
            )
            db.upsert_job(
                ScoredJob(posting=posting, status=JobStatus.NEW, relevance=relevance)
            )
            new_scored += 1

    log.info(
        "=== Scan done: fetched=%d scored_new=%d skipped_seen=%d ===",
        fetched, new_scored, skipped_seen,
    )
    return {
        "keywords": keywords,
        "seniority": profile.seniority.value,
        "providers": [p.name for p in providers if p.is_configured()],
        "fetched": fetched,
        "new_scored": new_scored,
        "skipped_seen": skipped_seen,
    }


def _ai_score_budget(profile: CVProfile, fresh: list, settings) -> set[str]:
    """Decide which postings get a (costly) AI relevance score.

    Returns a set of posting keys. When prefiltering is off or AI is disabled,
    behavior matches the old path: AI-score everything (or nothing, resp.).
    Otherwise rank by the cheap heuristic and pick the top N.
    """
    if not settings.ai_enabled or not settings.ai_score:
        return set()  # pure heuristic scoring; no AI tokens spent during scan
    if not settings.ai_prefilter or settings.ai_score_top_n <= 0:
        return {p.key for p in fresh}

    ranked = sorted(
        fresh,
        key=lambda p: ai_ops._score_fallback(profile, p).score,
        reverse=True,
    )
    top = ranked[: settings.ai_score_top_n]
    log.info(
        "AI-scoring top %d of %d fresh jobs (heuristic pre-filter); "
        "remaining keep heuristic score",
        len(top), len(fresh),
    )
    return {p.key for p in top}


def prepare_application(key: str, language: str = "de") -> dict:
    """Generate tailored documents + payload for a job and mark it 'prepared'.

    `language` controls the output language of the CV + letter ("de" default,
    "en" for English). This does NOT submit anything. Returns a dict with the
    application URL, generated artifact paths and the ai_generated flag.
    """
    settings = get_settings()
    log.info("Preparing application for job %s (language=%s) ...", key, language)
    job = db.get_job(key)
    if job is None:
        log.error("Prepare failed: job not found %s", key)
        raise ProfileError(f"Job not found: {key}")

    profile = ensure_profile()
    posting = job.posting
    # generate_cv / generate_letter never raise now: they fall back to plain,
    # non-AI drafts when OpenAI is unavailable and report ai_used=False so we
    # can warn the user the documents are not AI-tailored.
    log.info("Generating CV for %r @ %r", posting.title, posting.company)
    cv_text, cv_ai = ai_ops.generate_cv(profile, posting, language)
    log.debug("Generated CV (%d chars, ai=%s)", len(cv_text), cv_ai)
    log.info("Generating motivation letter")
    letter_text, letter_ai = ai_ops.generate_letter(profile, posting, language)
    log.debug("Generated letter (%d chars, ai=%s)", len(letter_text), letter_ai)
    ai_used = cv_ai and letter_ai
    if not ai_used:
        log.warning(
            "Prepare for %s used NON-AI fallback documents (cv_ai=%s letter_ai=%s)",
            key, cv_ai, letter_ai,
        )

    cv_pdf = render_cv_pdf(cv_text, posting)
    letter_pdf = render_letter_pdf(letter_text, posting, profile)
    log.info("Rendered PDFs: %s | %s", cv_pdf.name, letter_pdf.name)

    payload = {
        "job_key": key,
        "job_title": posting.title,
        "company": posting.company,
        "location": posting.location,
        "application_url": posting.url,
        "candidate_name": profile.full_name,
        "candidate_email": profile.email,
        "relevance_score": job.relevance.score,
        "keywords_used": ai_ops.derive_keywords(profile),
        "ai_generated": ai_used,
        "language": language,
    }

    db.save_application(
        key=key,
        generated_cv=cv_text,
        motivation_letter=letter_text,
        payload=payload,
        cv_pdf_path=str(cv_pdf),
        letter_pdf_path=str(letter_pdf),
    )
    log.info("Prepared %s and stored artifacts (status=prepared)", key)

    return {
        "url": posting.url,
        "cv_pdf": str(cv_pdf),
        "letter_pdf": str(letter_pdf),
        "ai_generated": ai_used,
    }


def mark_job_applied(key: str) -> str:
    """Confirm the user submitted a prepared application; records the date."""
    ts = db.mark_applied(key)
    if ts is None:
        log.error("Mark-applied failed: job not found %s", key)
        raise ProfileError(f"Job not found: {key}")
    log.info("Marked %s as applied at %s", key, ts)
    return ts


def unmark_job_applied(key: str) -> None:
    """Move an applied job back to 'prepared' (clears the application date)."""
    db.unmark_applied(key)
    log.info("Reverted %s from applied back to prepared", key)


def fetch_bundesagentur_description(refnr: str) -> str:
    """Fetch a Bundesagentur job's full description on demand."""
    from .providers.bundesagentur import BundesagenturProvider
    return BundesagenturProvider().fetch_description(refnr)


def forget_job(key: str) -> bool:
    """Permanently delete a job, its events/notes, and any generated PDFs."""
    # Remove generated documents from disk first (paths from the stored app).
    app = db.get_application(key)
    if app:
        _delete_generated_files(app.get("cv_pdf_path"), app.get("letter_pdf_path"))
    removed = db.delete_job(key)
    log.info("Forgot job %s (existed=%s)", key, removed)
    return removed


def _delete_generated_files(*paths: str | None) -> None:
    """Delete generated PDF files, but only within data/generated/ for safety."""
    from pathlib import Path
    from .config import GENERATED_DIR

    gen_root = GENERATED_DIR.resolve()
    for p in paths:
        if not p:
            continue
        try:
            fp = Path(p).resolve()
            if gen_root in fp.parents and fp.exists():
                fp.unlink()
                log.info("Deleted generated file %s", fp.name)
        except OSError as e:
            log.warning("Could not delete generated file %s: %s", p, e)


def ignore_job(key: str) -> None:
    db.set_status(key, JobStatus.IGNORED)


def unignore_job(key: str) -> None:
    db.set_status(key, JobStatus.NEW)


# ----------------------------------------------------------------------------
# Status / outcome, events, notes
# ----------------------------------------------------------------------------
def set_job_status(key: str, status: JobStatus) -> None:
    """Change a job's status (e.g. interviewing/accepted/rejected/no_response).

    If moving to 'applied' and no application date is recorded yet, stamp one.
    """
    if status == JobStatus.APPLIED and (db.get_job(key) and not db.get_job(key).applied_at):
        db.mark_applied(key)
        return
    db.set_status(key, status)
    log.info("Set status of %s -> %s", key, status.value)


def add_event(
    key: str, event_type: str, starts_at: str,
    title: str = "", location: str = "", notes: str = "",
) -> int:
    et = EventType(event_type) if event_type in EventType._value2member_map_ else EventType.OTHER
    eid = db.add_event(JobEvent(
        job_key=key, event_type=et, starts_at=starts_at,
        title=title, location=location, notes=notes,
    ))
    log.info("Added %s event to %s at %s", et.value, key, starts_at)
    return eid


def delete_event(event_id: int) -> None:
    db.delete_event(event_id)


def add_note(key: str, body: str) -> int:
    return db.add_note(key, body.strip())


def delete_note(note_id: int) -> None:
    db.delete_note(note_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_events(events: list[JobEvent]) -> dict:
    """Split events into upcoming and past by comparing starts_at to now.

    Events come sorted ascending. Compare on the first 19 chars
    (YYYY-MM-DDTHH:MM:SS) so a naive local datetime-local value and the ISO
    'now' line up lexically. Past is returned most-recent-first.
    """
    now19 = datetime.now().isoformat()[:19]
    upcoming, past = [], []
    for e in events:
        (upcoming if (e.starts_at or "")[:19] >= now19 else past).append(e)
    past.reverse()
    return {"upcoming": upcoming, "past": past}
