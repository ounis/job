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

# Host/port are overridable via env so the same entry point works for the local
# Windows exe (localhost) and a headless server like the Pi (HOST=0.0.0.0 to be
# reachable on the LAN). Defaults keep the exe's original localhost behavior.
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
# The browser we auto-open (exe only) should always point at a reachable address,
# never 0.0.0.0.
URL = f"http://{'127.0.0.1' if HOST in ('0.0.0.0', '::') else HOST}:{PORT}"


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
