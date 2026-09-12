"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Two distinct roots so the app works both from source and as a frozen exe:
#
#   BUNDLE_DIR : where read-only assets (templates, static) live. When frozen
#                by PyInstaller these are unpacked to sys._MEIPASS; from source
#                it's the package parent.
#   ROOT_DIR   : where user-editable, writable runtime data lives (.env, the
#                CV, the SQLite db, generated PDFs). When frozen this is the
#                folder that CONTAINS the exe, so users can drop their .env and
#                CV right next to job.exe.
_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    ROOT_DIR = BUNDLE_DIR

DATA_DIR = ROOT_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"

# CV formats we can extract text from (see app/ai/cv_reader.py).
SUPPORTED_CV_SUFFIXES = (".pdf", ".docx", ".txt", ".md")


def discover_cv_files() -> list[Path]:
    """Return supported CV files found in the data/ dir, newest first.

    Generated artifacts (data/generated/) are excluded. Used to propose a list
    of CVs when none is configured or the configured one is missing.
    """
    if not DATA_DIR.exists():
        return []
    files = [
        p
        for p in DATA_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_CV_SUFFIXES
    ]
    # Newest first so the most recently added CV is the natural default.
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files

# Bundled asset locations (used by the web layer).
TEMPLATES_DIR = BUNDLE_DIR / "app" / "templates"
STATIC_DIR = BUNDLE_DIR / "app" / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Adzuna
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    # JSearch (RapidAPI) — broad aggregator (Google for Jobs)
    rapidapi_key: str = ""
    # Free tier caps num_pages at 1; raise only if your plan allows more.
    jsearch_max_pages: int = 1
    active_providers: str = "jsearch"

    # Search
    search_location: str = "Germany"
    search_distance_km: int = 50
    search_keywords: str = ""
    max_results: int = 50

    # App
    cv_path: str = "data/cv.pdf"
    min_relevance: int = 40
    log_level: str = "INFO"

    # AI token budget controls. Lower = cheaper. Char limits truncate the text
    # sent to the model; max_tokens caps the response length.
    #   - Scoring is the biggest spender (runs once per new job). Use the cheap
    #     heuristic for pre-filtering and only spend AI tokens on the top matches.
    ai_score: bool = True               # False = never AI-score during scan (cheapest)
    ai_prefilter: bool = True           # heuristic-score first, AI-score only top N
    ai_score_top_n: int = 10            # how many jobs to AI-score per scan (0 = all)
    ai_score_desc_chars: int = 1500     # job description chars sent when scoring
    ai_parse_cv_chars: int = 8000       # CV chars sent when parsing the profile
    ai_gen_desc_chars: int = 2500       # job description chars sent when generating
    ai_gen_cv_chars: int = 8000         # CV chars sent when generating a tailored CV
    ai_gen_letter_cv_chars: int = 3500  # CV chars sent when generating a letter
    ai_max_tokens_json: int = 700       # response cap for JSON calls (parse/score)
    ai_max_tokens_text: int = 1200      # response cap for text calls (cv/letter)

    @property
    def provider_list(self) -> list[str]:
        return [p.strip() for p in self.active_providers.split(",") if p.strip()]

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.search_keywords.split(",") if k.strip()]

    @property
    def cv_full_path(self) -> Path:
        """Resolve the configured CV path; fall back to a detected CV.

        If CV_PATH points at a missing file (or is blank), auto-pick the newest
        supported CV found in data/ so the app works without manual config.
        """
        configured = Path(self.cv_path) if self.cv_path else None
        if configured is not None:
            resolved = configured if configured.is_absolute() else ROOT_DIR / configured
            if resolved.exists():
                return resolved
        detected = discover_cv_files()
        if detected:
            return detected[0]
        # Nothing found: return the configured path (or default) so callers can
        # surface a clear "not found" error pointing at the expected location.
        fallback = configured or Path(self.cv_path or "data/cv.pdf")
        return fallback if fallback.is_absolute() else ROOT_DIR / fallback

    @property
    def detected_cvs(self) -> list[Path]:
        return discover_cv_files()

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "jobs.db"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()


ENV_FILE = ROOT_DIR / ".env"


def _relativize(path: Path) -> str:
    """Store paths inside the project as relative for a portable .env."""
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT_DIR.resolve()))
    except ValueError:
        return str(path)


def set_cv_path(path: Path) -> None:
    """Persist the chosen CV path to CV_PATH in .env and refresh settings.

    Rewrites (or appends) the CV_PATH line and clears the cached Settings so the
    next get_settings() picks up the change.
    """
    value = _relativize(path)
    new_line = f"CV_PATH={value}"

    lines: list[str] = []
    found = False
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("CV_PATH="):
                lines[i] = new_line
                found = True
                break
    if not found:
        lines.append(new_line)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    get_settings.cache_clear()
