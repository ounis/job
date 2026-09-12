"""Shared domain models used across providers, AI, DB and the web layer."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    NEW = "new"
    PREPARED = "prepared"
    APPLIED = "applied"
    IGNORED = "ignored"


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
