"""SQLite persistence layer.

Two tables:
  - profile : stores the parsed CV profile (single row, id=1) + a hash of the
              source CV so we know when to re-parse.
  - jobs    : every job we've seen, its normalized fields, relevance score,
              status (new/applied/ignored) and, once applied, the generated
              CV text, motivation letter text and the full application payload.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .config import get_settings
from .models import (
    CVProfile,
    JobPosting,
    JobStatus,
    RelevanceResult,
    ScoredJob,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    cv_hash       TEXT,
    data          TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    key            TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    title          TEXT NOT NULL,
    company        TEXT,
    location       TEXT,
    description    TEXT,
    url            TEXT,
    salary_min     REAL,
    salary_max     REAL,
    created        TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    relevance      INTEGER NOT NULL DEFAULT 0,
    relevance_data TEXT,
    -- populated when applied:
    generated_cv       TEXT,
    motivation_letter  TEXT,
    application_payload TEXT,
    cv_pdf_path        TEXT,
    letter_pdf_path    TEXT,
    first_seen_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_relevance ON jobs(relevance DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------------
def save_profile(profile: CVProfile, cv_hash: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO profile (id, cv_hash, data, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cv_hash = excluded.cv_hash,
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (cv_hash, profile.model_dump_json(), _now()),
        )


def load_profile() -> Optional[CVProfile]:
    with connect() as conn:
        row = conn.execute("SELECT data FROM profile WHERE id = 1").fetchone()
    if not row:
        return None
    return CVProfile.model_validate_json(row["data"])


def load_profile_hash() -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT cv_hash FROM profile WHERE id = 1").fetchone()
    return row["cv_hash"] if row else None


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------
def upsert_job(job: ScoredJob) -> None:
    """Insert a newly scanned job, or refresh its fields/relevance.

    A job that is already applied/ignored keeps its status; we never demote it
    back to 'new'. This is what makes applied/ignored jobs stick across scans.
    """
    p = job.posting
    with connect() as conn:
        existing = conn.execute(
            "SELECT status FROM jobs WHERE key = ?", (p.key,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE jobs SET
                    title = ?, company = ?, location = ?, description = ?,
                    url = ?, salary_min = ?, salary_max = ?, created = ?,
                    relevance = ?, relevance_data = ?, updated_at = ?
                WHERE key = ?
                """,
                (
                    p.title, p.company, p.location, p.description, p.url,
                    p.salary_min, p.salary_max, p.created,
                    job.relevance.score, job.relevance.model_dump_json(),
                    _now(), p.key,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO jobs (
                    key, provider, external_id, title, company, location,
                    description, url, salary_min, salary_max, created,
                    status, relevance, relevance_data,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.key, p.provider, p.external_id, p.title, p.company,
                    p.location, p.description, p.url, p.salary_min, p.salary_max,
                    p.created, job.status.value, job.relevance.score,
                    job.relevance.model_dump_json(), _now(), _now(),
                ),
            )


def get_seen_keys() -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT key FROM jobs").fetchall()
    return {r["key"] for r in rows}


def get_job_status(key: str) -> Optional[JobStatus]:
    with connect() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE key = ?", (key,)).fetchone()
    return JobStatus(row["status"]) if row else None


def _row_to_scored(row: sqlite3.Row) -> ScoredJob:
    posting = JobPosting(
        provider=row["provider"],
        external_id=row["external_id"],
        title=row["title"],
        company=row["company"] or "",
        location=row["location"] or "",
        description=row["description"] or "",
        url=row["url"] or "",
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        created=row["created"],
    )
    relevance = RelevanceResult()
    if row["relevance_data"]:
        try:
            relevance = RelevanceResult.model_validate_json(row["relevance_data"])
        except Exception:
            relevance = RelevanceResult(score=row["relevance"])
    return ScoredJob(
        posting=posting,
        status=JobStatus(row["status"]),
        relevance=relevance,
    )


def list_jobs(
    statuses: Optional[list[JobStatus]] = None,
    min_relevance: int = 0,
) -> list[ScoredJob]:
    query = "SELECT * FROM jobs WHERE relevance >= ?"
    params: list = [min_relevance]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        query += f" AND status IN ({placeholders})"
        params.extend(s.value for s in statuses)
    query += " ORDER BY relevance DESC, updated_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_scored(r) for r in rows]


def get_job(key: str) -> Optional[ScoredJob]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
    return _row_to_scored(row) if row else None


def set_status(key: str, status: JobStatus) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE key = ?",
            (status.value, _now(), key),
        )


def save_application(
    key: str,
    generated_cv: str,
    motivation_letter: str,
    payload: dict,
    cv_pdf_path: str,
    letter_pdf_path: str,
) -> None:
    """Persist everything used for an application and mark the job applied."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs SET
                status = 'applied',
                generated_cv = ?,
                motivation_letter = ?,
                application_payload = ?,
                cv_pdf_path = ?,
                letter_pdf_path = ?,
                updated_at = ?
            WHERE key = ?
            """,
            (
                generated_cv, motivation_letter, json.dumps(payload),
                cv_pdf_path, letter_pdf_path, _now(), key,
            ),
        )


def get_application(key: str) -> Optional[dict]:
    """Return the stored application artifacts for an applied job."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT generated_cv, motivation_letter, application_payload,
                   cv_pdf_path, letter_pdf_path
            FROM jobs WHERE key = ?
            """,
            (key,),
        ).fetchone()
    if not row or row["application_payload"] is None:
        return None
    return {
        "generated_cv": row["generated_cv"],
        "motivation_letter": row["motivation_letter"],
        "payload": json.loads(row["application_payload"]),
        "cv_pdf_path": row["cv_pdf_path"],
        "letter_pdf_path": row["letter_pdf_path"],
    }


def counts_by_status() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
        ).fetchall()
    return {r["status"]: r["c"] for r in rows}
