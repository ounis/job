"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of the "app" package.
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"


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
    active_providers: str = "adzuna"

    # Search
    search_location: str = "Germany"
    search_distance_km: int = 50
    search_keywords: str = ""
    max_results: int = 50

    # App
    cv_path: str = "data/cv.pdf"
    min_relevance: int = 40

    @property
    def provider_list(self) -> list[str]:
        return [p.strip() for p in self.active_providers.split(",") if p.strip()]

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.search_keywords.split(",") if k.strip()]

    @property
    def cv_full_path(self) -> Path:
        p = Path(self.cv_path)
        return p if p.is_absolute() else ROOT_DIR / p

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
