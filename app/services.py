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
from .models import CVProfile, JobStatus, ScoredJob
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


async def run_scan(force_profile: bool = False) -> dict:
    """Run a full scan. Returns a small summary dict for the UI."""
    settings = get_settings()
    log.info("=== Scan started (force_profile=%s) ===", force_profile)
    profile = ensure_profile(force=force_profile)
    keywords = ai_ops.derive_keywords(profile)
    log.info("Search keywords: %s", ", ".join(keywords) or "(none)")
    log.info(
        "Search config: location=%r distance=%dkm max_results=%d min_relevance=%d",
        settings.search_location, settings.search_distance_km,
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
        log.info("Querying provider %r ...", provider.name)
        try:
            postings = await provider.search(
                keywords=keywords,
                location=settings.search_location,
                distance_km=settings.search_distance_km,
                limit=settings.max_results,
                profile=profile,
            )
        except Exception:
            log.exception("Provider %r failed", provider.name)
            continue
        log.info("Provider %r returned %d postings", provider.name, len(postings))
        fetched += len(postings)
        for posting in postings:
            if posting.key in seen:
                log.debug("Skip already-seen %s (%s)", posting.key, posting.title)
                skipped_seen += 1
                continue
            relevance = ai_ops.score_relevance(profile, posting)
            log.debug(
                "Scored %d | %s @ %s | matched=%s",
                relevance.score, posting.title, posting.company,
                relevance.matched_skills,
            )
            db.upsert_job(
                ScoredJob(posting=posting, status=JobStatus.NEW, relevance=relevance)
            )
            seen.add(posting.key)
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


def apply_to_job(key: str) -> dict:
    """Generate tailored documents + payload for a job and mark it applied.

    Returns dict with the application URL and generated artifact paths.
    """
    settings = get_settings()
    log.info("Applying to job %s ...", key)
    job = db.get_job(key)
    if job is None:
        log.error("Apply failed: job not found %s", key)
        raise ProfileError(f"Job not found: {key}")
    if not settings.ai_enabled:
        log.error("Apply blocked: OPENAI_API_KEY not set")
        raise ProfileError(
            "Generating a tailored CV and letter requires OPENAI_API_KEY in .env."
        )

    profile = ensure_profile()
    posting = job.posting
    try:
        log.info("Generating tailored CV for %r @ %r", posting.title, posting.company)
        cv_text = ai_ops.generate_cv(profile, posting)
        log.debug("Generated CV (%d chars)", len(cv_text))
        log.info("Generating motivation letter")
        letter_text = ai_ops.generate_letter(profile, posting)
        log.debug("Generated letter (%d chars)", len(letter_text))
    except ai_ops.AIUnavailable as e:
        log.error("Apply failed for %s: %s", key, e)
        raise ProfileError(str(e)) from e

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
    }

    db.save_application(
        key=key,
        generated_cv=cv_text,
        motivation_letter=letter_text,
        payload=payload,
        cv_pdf_path=str(cv_pdf),
        letter_pdf_path=str(letter_pdf),
    )
    log.info("Marked %s as applied and stored artifacts", key)

    return {
        "url": posting.url,
        "cv_pdf": str(cv_pdf),
        "letter_pdf": str(letter_pdf),
    }


def ignore_job(key: str) -> None:
    db.set_status(key, JobStatus.IGNORED)


def unignore_job(key: str) -> None:
    db.set_status(key, JobStatus.NEW)
