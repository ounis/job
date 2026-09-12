"""Centralized, verbose logging configuration.

Level is controlled by LOG_LEVEL in .env (default INFO; set DEBUG for the most
verbose output, including per-job scoring and outbound request details).

Call configure_logging() once at startup. Modules get a logger via
get_logger(__name__).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Rotating log file: keep the current file plus a few backups so it never grows
# without bound. Location comes from the app's writable data dir.
_LOG_FILE_NAME = "job.log"
_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
_LOG_BACKUP_COUNT = 3


def _log_file_path() -> Path:
    # Imported lazily to avoid a circular import (config imports nothing from us,
    # but keep the dependency direction one-way and resilient if that changes).
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / _LOG_FILE_NAME


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Console handler (unchanged behavior).
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if something already configured the root.
    root.handlers.clear()
    root.addHandler(handler)

    # File handler: mirror everything to a rotating logfile in data/.
    log_path = _log_file_path()
    # Start each run with a fresh logfile: truncate any existing content up front.
    # (RotatingFileHandler's rotation still applies within the run.)
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass
    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as e:  # e.g. read-only dir — keep console logging working
        root.warning("Could not open log file %s: %s", log_path, e)

    # Make our own namespace verbose; keep noisy third parties one notch quieter
    # unless the user explicitly asked for DEBUG.
    logging.getLogger("app").setLevel(level)
    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        for noisy in ("httpx", "httpcore", "openai"):
            logging.getLogger(noisy).setLevel(logging.DEBUG)

    _CONFIGURED = True
    logging.getLogger("app").info(
        "Logging configured at level %s (file: %s)", level_name, log_path
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
