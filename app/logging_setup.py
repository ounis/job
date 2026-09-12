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
    file_handler = None
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

    # Route uvicorn's own loggers into our handlers too. Uvicorn sets up its
    # loggers with propagate=False and its own handlers, so without this its
    # startup and access lines show in the console but never reach job.log.
    _attach_uvicorn(handler, file_handler, level)

    _CONFIGURED = True
    logging.getLogger("app").info(
        "Logging configured at level %s (file: %s)", level_name, log_path
    )


# Handlers kept so we can (re)attach them to uvicorn's loggers, which are only
# created once uvicorn starts — after configure_logging() runs at import time.
_HANDLERS: list[logging.Handler] = []


def _attach_uvicorn(console, file_h, level) -> None:
    global _HANDLERS
    _HANDLERS = [h for h in (console, file_h) if h is not None]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        for h in _HANDLERS:
            lg.addHandler(h)
        lg.setLevel(level)
        lg.propagate = False  # we own its handlers now; avoid double logging


def attach_uvicorn_loggers() -> None:
    """Re-attach our handlers to uvicorn's loggers.

    Call this AFTER uvicorn has configured its logging (e.g. right after
    uvicorn.run sets up, via a startup hook), because uvicorn replaces handlers
    on its own loggers during initialization.
    """
    level = logging.getLogger().level
    console = next((h for h in _HANDLERS if isinstance(h, logging.StreamHandler)
                    and not isinstance(h, RotatingFileHandler)), None)
    file_h = next((h for h in _HANDLERS if isinstance(h, RotatingFileHandler)), None)
    _attach_uvicorn(console, file_h, level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
