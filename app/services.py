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
from .models import CVProfile, JobStatus, ScoredJob
from .pdf.render import render_cv_pdf, render_letter_pdf
from .providers import get_providers


class ProfileError(RuntimeError):
    pass


def ensure_profile(force: bool = False) -> CVProfile:
    settings = get_settings()
    cv_path = settings.cv_full_path
    if not cv_path.exists():
        raise ProfileError(
            f"CV not found at {cv_path}. Set CV_PATH in .env and place your CV there."
        )

    current_hash = hash_file(cv_path)
    if not force:
        existing = db.load_profile()
        if existing and db.load_profile_hash() == current_hash:
            return existing

    raw = extract_text(cv_path)
    if not raw.strip():
        raise ProfileError("Could not extract any text from the CV file.")
    profile = ai_ops.parse_cv(raw)
    db.save_profile(profile, current_hash)
    return profile


async def run_scan(force_profile: bool = False) -> dict:
    """Run a full scan. Returns a small summary dict for the UI."""
    settings = get_settings()
    profile = ensure_profile(force=force_profile)
    keywords = ai_ops.derive_keywords(profile)

    seen = db.get_seen_keys()
    providers = get_providers()

    fetched = 0
    new_scored = 0
    skipped_seen = 0

    for provider in providers:
        if not provider.is_configured():
            continue
        postings = await provider.search(
            keywords=keywords,
            location=settings.search_location,
            distance_km=settings.search_distance_km,
            limit=settings.max_results,
            profile=profile,
        )
        fetched += len(postings)
        for posting in postings:
            if posting.key in seen:
                # Already tracked (possibly applied/ignored). Skip re-scoring;
                # upsert_job would preserve its status anyway, but we save AI calls.
                skipped_seen += 1
                continue
            relevance = ai_ops.score_relevance(profile, posting)
            db.upsert_job(
                ScoredJob(posting=posting, status=JobStatus.NEW, relevance=relevance)
            )
            seen.add(posting.key)
            new_scored += 1

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
    job = db.get_job(key)
    if job is None:
        raise ProfileError(f"Job not found: {key}")
    if not settings.ai_enabled:
        raise ProfileError(
            "Generating a tailored CV and letter requires OPENAI_API_KEY in .env."
        )

    profile = ensure_profile()
    posting = job.posting

    cv_text = ai_ops.generate_cv(profile, posting)
    letter_text = ai_ops.generate_letter(profile, posting)

    cv_pdf = render_cv_pdf(cv_text, posting)
    letter_pdf = render_letter_pdf(letter_text, posting, profile)

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

    return {
        "url": posting.url,
        "cv_pdf": str(cv_pdf),
        "letter_pdf": str(letter_pdf),
    }


def ignore_job(key: str) -> None:
    db.set_status(key, JobStatus.IGNORED)


def unignore_job(key: str) -> None:
    db.set_status(key, JobStatus.NEW)
