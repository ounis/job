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
    # Bundesagentur (German Federal Employment Agency) — public client id by
    # default; the API is geo-restricted to Germany/EU.
    bundesagentur_api_key: str = ""
    active_providers: str = "jsearch"

    # Search
    search_location: str = "Germany"
    search_distance_km: int = 50
    search_keywords: str = ""
    max_results: int = 50

    # App
    # CV_PATH may be a directory (scan it for CVs and auto-pick / let the UI
    # choose) or a specific file (explicit override). Default: the data/ dir.
    cv_path: str = "data"
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
        """Resolve the active CV.

        CV_PATH can be:
          - a directory (default: "data") -> auto-pick the newest CV found in
            data/ (the UI can override the choice by writing a specific file);
          - a specific file -> use it if it exists, else fall back to discovery.
        If nothing is found, return the default file path so callers can surface
        a clear "not found" error pointing at the expected location.
        """
        configured = Path(self.cv_path) if self.cv_path else None
        if configured is not None:
            resolved = configured if configured.is_absolute() else ROOT_DIR / configured
            # A concrete, existing file wins (explicit selection/override).
            if resolved.is_file():
                return resolved
            # A directory (or missing path) means "discover a CV".
        detected = discover_cv_files()
        if detected:
            return detected[0]
        return DATA_DIR / "cv.pdf"

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


def set_env_values(values: dict[str, str]) -> None:
    """Upsert multiple KEY=value pairs in .env, then refresh cached settings.

    Existing lines for a key are rewritten in place (preserving surrounding
    comments and ordering); missing keys are appended. Clears the Settings cache
    so the next get_settings() reflects the change.
    """
    if not values:
        return

    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    remaining = dict(values)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    # Append any keys that weren't already present.
    for key, val in remaining.items():
        lines.append(f"{key}={val}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_settings.cache_clear()


def set_cv_path(path: Path) -> None:
    """Persist the chosen CV path to CV_PATH in .env and refresh settings."""
    set_env_values({"CV_PATH": _relativize(path)})
