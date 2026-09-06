"""Centralized, verbose logging configuration.

Level is controlled by LOG_LEVEL in .env (default INFO; set DEBUG for the most
verbose output, including per-job scoring and outbound request details).

Call configure_logging() once at startup. Modules get a logger via
get_logger(__name__).
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if something already configured the root.
    root.handlers.clear()
    root.addHandler(handler)

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
    logging.getLogger("app").info("Logging configured at level %s", level_name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
