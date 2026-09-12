"""High-level AI operations built on top of AIClient.

  - parse_cv          : raw CV text -> structured CVProfile (+ seniority)
  - derive_keywords   : profile -> search keywords
  - score_relevance   : profile + job -> RelevanceResult (0-100)
  - generate_cv       : profile + job -> tailored CV text
  - generate_letter   : profile + job -> motivation letter text

Each AI call has a lightweight non-AI fallback so scanning/scoring still works
(with cruder results) when no API key is configured. Generation of tailored
documents requires the AI key.
"""
from __future__ import annotations

import re

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import CVProfile, JobPosting, RelevanceResult, Seniority
from .client import get_ai_client

log = get_logger("app.ai")


class AIUnavailable(RuntimeError):
    """Raised when an AI call fails and there is no usable fallback."""

# ----------------------------------------------------------------------------
# CV parsing
# ----------------------------------------------------------------------------
_PARSE_SYSTEM = """You are an expert technical recruiter and resume parser.
Extract a structured profile from the CV text. Respond with a JSON object with
these keys:
  full_name, email, phone, location, headline, summary,
  seniority (one of: intern, junior, mid, senior, lead, unknown),
  years_experience (number),
  skills (array of strings),
  titles (array of past/target job titles),
  experience (array of {company, title, period, highlights}),
  education (array of {institution, degree, period}),
  languages (array of strings).
Infer seniority from years of experience, scope, and titles. Be accurate."""


def parse_cv(raw_text: str) -> CVProfile:
    settings = get_settings()
    if not settings.ai_enabled:
        return _parse_cv_fallback(raw_text)

    try:
        client = get_ai_client()
        data = client.complete_json(
            _PARSE_SYSTEM,
            f"CV TEXT:\n\n{raw_text[:settings.ai_parse_cv_chars]}",
        )
    except Exception as e:
        log.warning("AI CV parse failed (%s); using heuristic fallback", _brief(e))
        return _parse_cv_fallback(raw_text)
    seniority = _coerce_seniority(data.get("seniority"))
    return CVProfile(
        full_name=data.get("full_name", ""),
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        location=data.get("location", ""),
        headline=data.get("headline", ""),
        summary=data.get("summary", ""),
        seniority=seniority,
        years_experience=_as_float(data.get("years_experience")),
        skills=_as_str_list(data.get("skills")),
        titles=_as_str_list(data.get("titles")),
        experience=data.get("experience") or [],
        education=data.get("education") or [],
        languages=_as_str_list(data.get("languages")),
        raw_text=raw_text,
    )


def _parse_cv_fallback(raw_text: str) -> CVProfile:
    """Heuristic CV parse for when AI is unavailable.

    Works for ANY role (not just tech): it derives titles/skills/headline from
    the CV's own text — the headline line, an explicit "Skills" section, and
    detected role titles — instead of relying on a fixed tech-skills list.
    """
    email = ""
    m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", raw_text)
    if m:
        email = m.group(0)

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    years = 0.0
    ym = re.search(r"(\d+)\+?\s*(?:years|jahre)", raw_text, re.IGNORECASE)
    if ym:
        years = float(ym.group(1))

    headline = _guess_headline(lines)
    titles = _guess_titles(raw_text)
    skills = _guess_skills(raw_text)

    return CVProfile(
        headline=headline,
        email=email,
        summary=raw_text[:400],
        seniority=_seniority_from_years(years),
        years_experience=years,
        skills=skills,
        titles=titles,
        raw_text=raw_text,
    )


# Role words seen across many fields — used to detect job titles in any CV.
_TITLE_WORDS = (
    "engineer", "developer", "manager", "analyst", "consultant", "designer",
    "architect", "administrator", "specialist", "coordinator", "executive",
    "representative", "lead", "director", "officer", "accountant", "nurse",
    "teacher", "recruiter", "scientist", "technician", "assistant", "advisor",
    "sales", "marketing", "account", "business development", "customer success",
    "product", "project", "operations", "support", "success",
)


def _guess_headline(lines: list[str]) -> str:
    """The headline is usually one of the first non-name lines (a role phrase)."""
    for ln in lines[:6]:
        low = ln.lower()
        if "@" in ln or re.search(r"\d{3,}", ln):
            continue  # skip contact/phone lines
        if any(w in low for w in _TITLE_WORDS):
            return ln[:120]
    return ""


def _guess_titles(raw_text: str) -> list[str]:
    """Extract role titles by scanning lines that contain common role words."""
    titles: list[str] = []
    for ln in raw_text.splitlines():
        # Strip control chars (e.g. stray bullet glyphs) and whitespace.
        s = re.sub(r"[\x00-\x1f\x7f]", " ", ln).strip()
        low = s.lower()
        if not s or len(s) > 80:
            continue
        # Skip skill-list lines (comma-dense) and obvious bullet points.
        if s.count(",") >= 2 or s[:1] in "•·-*":
            continue
        if any(w in low for w in _TITLE_WORDS):
            # Take the part before a separator (— , | @ ( etc.) as the title.
            title = re.split(r"[—\-|@(•·,]", s, 1)[0].strip(" ,:")
            # A title is a short phrase, not a sentence.
            if 3 <= len(title) <= 45 and len(title.split()) <= 6 and title not in titles:
                titles.append(title)
        if len(titles) >= 6:
            break
    return titles


def _guess_skills(raw_text: str) -> list[str]:
    """Prefer an explicit Skills section; fall back to known common skills."""
    skills: list[str] = []
    # 1) Grab the "Skills" section content if present.
    m = re.search(
        r"(?is)\bskills?\b[:\n](.{0,400}?)(?:\n\s*\n|\n[A-Z][a-z]+\s*\n|$)",
        raw_text,
    )
    if m:
        chunk = m.group(1)
        parts = re.split(r"[,\n;•·|]", chunk)
        for p in parts:
            t = p.strip(" .-\t")
            if 2 <= len(t) <= 30 and not t.lower().startswith(("skill",)):
                skills.append(t)
    # 2) Always also include any recognized common (tech) skills found anywhere.
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9+.#]{1,20}\b", raw_text)
    for t in tokens:
        if t.lower() in _COMMON_SKILLS and t not in skills:
            skills.append(t)
    # De-dupe case-insensitively, keep order, cap the list.
    seen = set()
    out = []
    for s in skills:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out[:20]


# ----------------------------------------------------------------------------
# Keywords
# ----------------------------------------------------------------------------
def derive_keywords(profile: CVProfile, limit: int = 5) -> list[str]:
    settings = get_settings()
    if settings.keyword_list:
        return settings.keyword_list
    # Prefer target titles, then top skills.
    kws: list[str] = []
    for t in profile.titles:
        if t and t not in kws:
            kws.append(t)
    for s in profile.skills:
        if s and s not in kws:
            kws.append(s)
    if not kws and profile.headline:
        kws.append(profile.headline)
    return kws[:limit]


# ----------------------------------------------------------------------------
# Relevance scoring
# ----------------------------------------------------------------------------
_SCORE_SYSTEM = """You are a career-matching engine. Given a candidate profile
and a job posting, rate how well the candidate fits the role. Consider skills,
seniority alignment, titles and domain. Respond with JSON:
  { "score": <int 0-100>, "reasons": "<one short paragraph>",
    "matched_skills": [...], "missing_skills": [...] }
Penalize seniority mismatch (e.g. a junior applying to a lead role)."""


def score_relevance(profile: CVProfile, job: JobPosting) -> RelevanceResult:
    settings = get_settings()
    if not settings.ai_enabled:
        return _score_fallback(profile, job)

    user = (
        f"CANDIDATE:\n"
        f"Seniority: {profile.seniority.value}\n"
        f"Years: {profile.years_experience}\n"
        f"Titles: {', '.join(profile.titles)}\n"
        f"Skills: {', '.join(profile.skills)}\n\n"
        f"JOB:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Description: {job.description[:settings.ai_score_desc_chars]}"
    )
    try:
        client = get_ai_client()
        data = client.complete_json(_SCORE_SYSTEM, user)
    except Exception as e:
        log.warning("AI scoring failed (%s); using heuristic fallback", _brief(e))
        return _score_fallback(profile, job)
    return RelevanceResult(
        score=_clamp_score(data.get("score")),
        reasons=data.get("reasons", ""),
        matched_skills=_as_str_list(data.get("matched_skills")),
        missing_skills=_as_str_list(data.get("missing_skills")),
    )


def _score_fallback(profile: CVProfile, job: JobPosting) -> RelevanceResult:
    text = f"{job.title} {job.description}".lower()
    matched = [s for s in profile.skills if s.lower() in text]
    if profile.skills:
        ratio = len(matched) / max(1, len(profile.skills))
    else:
        ratio = 0.0
    # title overlap bonus
    title_bonus = 0
    for t in profile.titles:
        if t and t.lower() in job.title.lower():
            title_bonus = 25
            break
    score = min(100, int(ratio * 75) + title_bonus)
    return RelevanceResult(
        score=score,
        reasons="Heuristic score (no AI key): skill/title keyword overlap.",
        matched_skills=matched,
        missing_skills=[],
    )


# ----------------------------------------------------------------------------
# Tailored document generation (requires AI)
# ----------------------------------------------------------------------------
# Supported output languages for generated documents. German is the default.
LANGUAGES = {"de": "German", "en": "English"}
DEFAULT_LANGUAGE = "de"


def _language_name(language: str) -> str:
    return LANGUAGES.get((language or DEFAULT_LANGUAGE).lower(), LANGUAGES[DEFAULT_LANGUAGE])


_CV_SYSTEM = """You are an expert resume writer. Rewrite the candidate's CV to
best fit the target job while staying strictly truthful — never invent
experience, skills, employers or dates. You may re-order, re-emphasize and
re-phrase real content to highlight relevance. Output a clean, ATS-friendly
plain-text CV with clear sections (Contact, Summary, Skills, Experience,
Education, Languages). No markdown fences.
Write the ENTIRE CV in {lang}, regardless of the language of the source CV or
the job posting."""


def generate_cv(
    profile: CVProfile, job: JobPosting, language: str = DEFAULT_LANGUAGE
) -> tuple[str, bool]:
    """Return (cv_text, ai_used) with the document written in `language`.

    Uses AI when available; on any AI failure (no key, quota, rate limit) falls
    back to a plain, non-tailored CV built from the profile. ai_used=False tells
    callers to warn the user the document was NOT AI-tailored.
    """
    settings = get_settings()
    lang = _language_name(language)
    if not settings.ai_enabled:
        log.info("generate_cv: AI disabled — using non-AI fallback CV (%s)", lang)
        return _cv_fallback(profile, job), False

    user = (
        f"TARGET JOB:\nTitle: {job.title}\nCompany: {job.company}\n"
        f"Description: {job.description[:settings.ai_gen_desc_chars]}\n\n"
        f"CANDIDATE CV (source of truth):\n{profile.raw_text[:settings.ai_gen_cv_chars]}"
    )
    try:
        return get_ai_client().complete_text(_CV_SYSTEM.format(lang=lang), user), True
    except Exception as e:
        log.warning("generate_cv: AI failed (%s) — using non-AI fallback", _brief(e))
        return _cv_fallback(profile, job), False


def _cv_fallback(profile: CVProfile, job: JobPosting) -> str:
    """Build a plain, honest CV from the parsed profile — no invented content.

    The non-AI status is surfaced in the web UI (via the ai_generated flag),
    not inside the document, so the PDF stays clean and usable as-is.
    """
    lines: list[str] = []
    if profile.full_name:
        lines.append(profile.full_name)
    contact = " | ".join(
        p for p in (profile.email, profile.phone, profile.location) if p
    )
    if contact:
        lines.append(contact)
    if profile.headline:
        lines.append(profile.headline)
    lines.append("")
    lines.append(f"Target role: {job.title} at {job.company}".strip())
    lines.append("")
    if profile.summary:
        lines += ["SUMMARY", profile.summary, ""]
    if profile.skills:
        lines += ["SKILLS", ", ".join(profile.skills), ""]
    if profile.experience:
        lines.append("EXPERIENCE")
        for e in profile.experience:
            if not isinstance(e, dict):
                continue
            head = " — ".join(
                str(e.get(k, "")).strip()
                for k in ("title", "company", "period")
                if e.get(k)
            )
            if head:
                lines.append(head)
            hl = e.get("highlights")
            if isinstance(hl, list):
                lines += [f"  - {h}" for h in hl if h]
            elif hl:
                lines.append(f"  - {hl}")
        lines.append("")
    if profile.education:
        lines.append("EDUCATION")
        for ed in profile.education:
            if not isinstance(ed, dict):
                continue
            row = " — ".join(
                str(ed.get(k, "")).strip()
                for k in ("degree", "institution", "period")
                if ed.get(k)
            )
            if row:
                lines.append(row)
        lines.append("")
    if profile.languages:
        lines += ["LANGUAGES", ", ".join(profile.languages), ""]
    # Fall back to raw CV text if the structured profile is sparse (no skills,
    # experience, education or languages extracted).
    if len(lines) <= 4 and profile.raw_text:
        lines += ["", profile.raw_text]
    return "\n".join(lines).strip()


_LETTER_SYSTEM = """You are an expert career coach writing a concise, sincere
motivation letter (cover letter). Keep it to 3-4 short paragraphs. Reference the
specific role and company, connect the candidate's real, relevant experience to
the role's needs, and close with a call to action. Stay truthful — do not invent
facts. Output only the letter body text.
Write the ENTIRE letter in {lang}, regardless of the language of the CV or the
job posting."""


def generate_letter(
    profile: CVProfile, job: JobPosting, language: str = DEFAULT_LANGUAGE
) -> tuple[str, bool]:
    """Return (letter_text, ai_used) written in `language`. Falls back to a
    plain template when AI is unavailable, same contract as generate_cv."""
    settings = get_settings()
    lang = _language_name(language)
    if not settings.ai_enabled:
        log.info("generate_letter: AI disabled — using non-AI fallback letter (%s)", lang)
        return _letter_fallback(profile, job, language), False

    user = (
        f"TARGET JOB:\nTitle: {job.title}\nCompany: {job.company}\n"
        f"Location: {job.location}\n"
        f"Description: {job.description[:settings.ai_gen_desc_chars]}\n\n"
        f"CANDIDATE:\nName: {profile.full_name}\n"
        f"Summary: {profile.summary}\n"
        f"Skills: {', '.join(profile.skills)}\n"
        f"Experience highlights:\n{profile.raw_text[:settings.ai_gen_letter_cv_chars]}"
    )
    try:
        return get_ai_client().complete_text(_LETTER_SYSTEM.format(lang=lang), user), True
    except Exception as e:
        log.warning("generate_letter: AI failed (%s) — using non-AI fallback", _brief(e))
        return _letter_fallback(profile, job, language), False


def _letter_fallback(profile: CVProfile, job: JobPosting, language: str = DEFAULT_LANGUAGE) -> str:
    """Plain, honest motivation letter template filled from the profile.

    Provides a German template by default (or when language='de'), English
    otherwise, so the non-AI fallback still respects the chosen language.
    """
    name = profile.full_name or "the candidate"
    company = job.company or "your company"
    role = job.title or "the advertised role"
    top_skills = ", ".join(profile.skills[:6]) if profile.skills else "my background"
    if (language or DEFAULT_LANGUAGE).lower() == "de":
        return _letter_fallback_de(profile, job, name, company, role, top_skills)
    body = (
        f"Dear Hiring Team at {company},\n\n"
        f"I am writing to express my interest in the {role} position. Based on my "
        f"experience and skills ({top_skills}), I believe I can contribute "
        f"effectively to your team.\n\n"
    )
    if profile.summary:
        body += profile.summary.strip() + "\n\n"
    body += (
        "I would welcome the opportunity to discuss how my background fits this "
        f"role. Thank you for your consideration.\n\nSincerely,\n{name}"
    )
    return body


def _letter_fallback_de(profile, job, name, company, role, top_skills) -> str:
    """German plain-template fallback letter."""
    body = (
        f"Sehr geehrtes Team von {company},\n\n"
        f"hiermit bewerbe ich mich auf die Position als {role}. Mit meiner "
        f"Erfahrung und meinen Kenntnissen ({top_skills}) bin ich überzeugt, "
        f"einen wertvollen Beitrag zu Ihrem Team leisten zu können.\n\n"
    )
    if profile.summary:
        body += profile.summary.strip() + "\n\n"
    body += (
        "Über die Gelegenheit zu einem persönlichen Gespräch würde ich mich sehr "
        f"freuen. Vielen Dank für Ihre Zeit und Berücksichtigung.\n\n"
        f"Mit freundlichen Grüßen,\n{name}"
    )
    return body


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
_COMMON_SKILLS = {
    "python", "java", "javascript", "typescript", "react", "node", "nodejs",
    "go", "golang", "rust", "c", "c++", "c#", "sql", "postgres", "postgresql",
    "mysql", "mongodb", "aws", "azure", "gcp", "docker", "kubernetes", "k8s",
    "terraform", "django", "flask", "fastapi", "spring", "kafka", "redis",
    "graphql", "rest", "git", "linux", "html", "css", "vue", "angular",
    "pandas", "numpy", "pytorch", "tensorflow", "ml", "nlp", "spark",
}


def _brief(e: Exception) -> str:
    """Short one-line description of an exception for logs."""
    return f"{type(e).__name__}: {str(e)[:200]}"


def _ai_error_message(e: Exception) -> str:
    """User-facing message for AI failures during document generation."""
    text = str(e).lower()
    if "insufficient_quota" in text or "credit" in text or "quota" in text:
        return (
            "OpenAI request failed: your API key has no remaining credits/quota. "
            "Add credits at https://platform.openai.com/settings/organization/billing/ "
            "or use a key with available quota."
        )
    if "invalid_api_key" in text or "incorrect api key" in text or "401" in text:
        return "OpenAI request failed: the API key appears invalid. Check OPENAI_API_KEY in .env."
    if "rate limit" in text or "429" in text:
        return "OpenAI request failed: rate limited. Wait a moment and try again."
    return f"OpenAI request failed: {str(e)[:200]}"


def _coerce_seniority(value) -> Seniority:
    try:
        return Seniority(str(value).lower())
    except Exception:
        return Seniority.UNKNOWN


def _seniority_from_years(years: float) -> Seniority:
    if years <= 0:
        return Seniority.UNKNOWN
    if years < 2:
        return Seniority.JUNIOR
    if years < 5:
        return Seniority.MID
    if years < 9:
        return Seniority.SENIOR
    return Seniority.LEAD


def _as_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_str_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if x]


def _clamp_score(v) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return 0
