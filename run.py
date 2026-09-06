"""Entry point for both source and the packaged Windows exe.

Source:  python run.py
Exe:     double-click job.exe (built by PyInstaller)

Starts the web app at http://127.0.0.1:8000 and, when running as a frozen exe,
opens the default browser to it.
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _open_browser() -> None:
    # Small delay so the server is listening before the tab opens.
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()


def main() -> None:
    frozen = getattr(sys, "frozen", False)
    if frozen:
        _open_browser()
        print(f"Job Hunter is running at {URL}  (close this window to stop)")
    # uvicorn log level follows LOG_LEVEL (access log on for verbosity).
    uvicorn_level = os.getenv("LOG_LEVEL", "info").lower()
    # reload must be False when frozen (no source files to watch).
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level=uvicorn_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
