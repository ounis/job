"""Shared domain models used across providers, AI, DB and the web layer."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _strip_surrogates(text: str) -> str:
    """Remove lone UTF-16 surrogate code points from a string.

    Some job feeds return text with unpaired surrogates (e.g. a broken emoji
    half like \\ud83c). Python/SQLite can't encode those to UTF-8, which crashed
    inserts. Round-tripping through UTF-8 with 'ignore' drops only the bad bytes.
    """
    if not text:
        return text
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore")


class JobStatus(str, Enum):
    NEW = "new"
    PREPARED = "prepared"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_RESPONSE = "no_response"
    WITHDRAWN = "withdrawn"
    IGNORED = "ignored"


# Statuses that represent an active/tracked application (kept out of fresh
# matching and shown with their own badge). Everything except plain "new".
STICKY_STATUSES = {
    JobStatus.PREPARED, JobStatus.APPLIED, JobStatus.INTERVIEWING,
    JobStatus.ACCEPTED, JobStatus.REJECTED, JobStatus.NO_RESPONSE,
    JobStatus.WITHDRAWN, JobStatus.IGNORED,
}

# Human-friendly labels for the UI.
STATUS_LABELS = {
    JobStatus.NEW: "New",
    JobStatus.PREPARED: "Prepared",
    JobStatus.APPLIED: "Applied",
    JobStatus.INTERVIEWING: "Interviewing",
    JobStatus.ACCEPTED: "Accepted",
    JobStatus.REJECTED: "Rejected",
    JobStatus.NO_RESPONSE: "No response",
    JobStatus.WITHDRAWN: "Withdrawn",
    JobStatus.IGNORED: "Ignored",
}


class EventType(str, Enum):
    PHONE_CALL = "phone_call"
    VIDEO_CALL = "video_call"
    ONSITE_INTERVIEW = "onsite_interview"
    TECHNICAL_INTERVIEW = "technical_interview"
    DEADLINE = "deadline"
    FOLLOW_UP = "follow_up"
    OTHER = "other"


EVENT_TYPE_LABELS = {
    EventType.PHONE_CALL: "Phone call",
    EventType.VIDEO_CALL: "Video call",
    EventType.ONSITE_INTERVIEW: "In-person interview",
    EventType.TECHNICAL_INTERVIEW: "Technical interview",
    EventType.DEADLINE: "Deadline",
    EventType.FOLLOW_UP: "Follow-up",
    EventType.OTHER: "Other",
}


class JobEvent(BaseModel):
    """A scheduled or past calendar event tied to a job (call, interview...)."""

    id: Optional[int] = None
    job_key: str
    event_type: EventType = EventType.OTHER
    starts_at: str  # ISO datetime (local, from a datetime-local input)
    title: str = ""
    location: str = ""
    notes: str = ""
    created_at: Optional[str] = None


class JobNote(BaseModel):
    """A timestamped free-text note attached to a job."""

    id: Optional[int] = None
    job_key: str
    body: str
    created_at: Optional[str] = None


class Seniority(str, Enum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"


class JobPosting(BaseModel):
    """A normalized job posting coming from any provider."""

    provider: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    created: Optional[str] = None

    @field_validator(
        "provider", "external_id", "title", "company", "location",
        "description", "url", "created", mode="before",
    )
    @classmethod
    def _clean_str(cls, v):
        # Strip lone surrogates from any incoming string so downstream encoding
        # (SQLite insert, PDF render) never blows up on malformed feed text.
        return _strip_surrogates(v) if isinstance(v, str) else v

    @property
    def key(self) -> str:
        """Stable unique key for dedupe + DB identity."""
        return f"{self.provider}:{self.external_id}"


class CVProfile(BaseModel):
    """Structured profile parsed from the user's CV."""

    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    headline: str = ""
    summary: str = ""
    seniority: Seniority = Seniority.UNKNOWN
    years_experience: float = 0
    skills: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    experience: list[dict] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    raw_text: str = ""


class RelevanceResult(BaseModel):
    score: int = 0  # 0-100
    reasons: str = ""
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)


class ScoredJob(BaseModel):
    posting: JobPosting
    status: JobStatus = JobStatus.NEW
    relevance: RelevanceResult = Field(default_factory=RelevanceResult)
    applied_at: Optional[str] = None  # ISO timestamp set when marked applied
    ai_generated: Optional[bool] = None  # whether prepared docs were AI-tailored
