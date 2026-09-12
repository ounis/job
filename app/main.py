"""FastAPI web application.

Routes:
  GET  /                 -> dashboard: matched jobs (relevance-ordered) with
                            status badges + checkboxes; applied/ignored sections.
  POST /scan             -> run a scan, then redirect back to dashboard.
  POST /apply            -> apply to the selected jobs (checkboxes): generate
                            tailored CV + letter PDFs, store payload, mark applied.
  POST /jobs/{key}/ignore    -> mark a job ignored.
  POST /jobs/{key}/unignore  -> move an ignored job back to new.
  GET  /jobs/{key}       -> job detail + (if applied) generated artifacts.
  GET  /files/...        -> download generated PDFs.
"""
from __future__ import annotations

import urllib.parse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, services
from pathlib import Path

from .config import (
    GENERATED_DIR,
    STATIC_DIR,
    TEMPLATES_DIR,
    discover_cv_files,
    get_settings,
    set_cv_path,
)
from .logging_setup import configure_logging, get_logger
from .models import JobStatus

configure_logging()
log = get_logger("app.main")

app = FastAPI(title="Job Hunter")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    s = get_settings()
    log.info("Job Hunter starting up")
    log.info("Providers=%s location=%r ai_enabled=%s log_level=%s",
             s.provider_list, s.search_location, s.ai_enabled, s.log_level)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    matched = db.list_jobs(statuses=[JobStatus.NEW], min_relevance=settings.min_relevance)
    applied = db.list_jobs(statuses=[JobStatus.APPLIED])
    ignored = db.list_jobs(statuses=[JobStatus.IGNORED])
    profile = db.load_profile()
    counts = db.counts_by_status()

    active_cv = settings.cv_full_path
    detected_cvs = [
        {"name": p.name, "path": str(p.resolve()), "active": p.resolve() == active_cv.resolve()}
        for p in discover_cv_files()
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "matched": matched,
            "applied": applied,
            "ignored": ignored,
            "profile": profile,
            "counts": counts,
            "settings": settings,
            "ai_enabled": settings.ai_enabled,
            "detected_cvs": detected_cvs,
            "active_cv_name": active_cv.name if active_cv.exists() else None,
            "flash": request.query_params.get("msg"),
        },
    )


@app.post("/scan")
async def scan(force_profile: bool = Form(False)):
    try:
        summary = await services.run_scan(force_profile=force_profile)
    except services.ProfileError as e:
        return _redirect_with_msg(str(e))
    except Exception as e:
        log.exception("Scan failed unexpectedly")
        return _redirect_with_msg(f"Scan failed: {type(e).__name__}: {str(e)[:200]}")
    msg = (
        f"Scan done. Fetched {summary['fetched']}, "
        f"scored {summary['new_scored']} new, "
        f"skipped {summary['skipped_seen']} already-seen. "
        f"Keywords: {', '.join(summary['keywords']) or '(none)'}."
    )
    return _redirect_with_msg(msg)


@app.post("/apply")
def apply(keys: list[str] = Form(default=[])):
    if not keys:
        return _redirect_with_msg("No jobs selected.")
    applied, errors = [], []
    used_fallback = False
    for key in keys:
        try:
            result = services.apply_to_job(key)
            applied.append(key)
            if not result.get("ai_generated", True):
                used_fallback = True
        except services.ProfileError as e:
            errors.append(str(e))
            break  # config errors will repeat; stop early
    msg = f"Applied to {len(applied)} job(s)."
    if used_fallback:
        msg += (
            " WARNING: OpenAI was unavailable — documents are plain, NON-AI "
            "drafts (not tailored). Add OpenAI credit and re-apply for tailored versions."
        )
    if errors:
        msg += f" Stopped: {errors[0]}"
    return _redirect_with_msg(msg)


@app.post("/cv/select")
def select_cv(cv: str = Form(...)):
    # Only allow selecting a file that discovery actually found, so an arbitrary
    # path can't be written into .env via this endpoint.
    choice = Path(cv).resolve()
    detected = {p.resolve() for p in discover_cv_files()}
    if choice not in detected:
        return _redirect_with_msg("Unknown CV file selected.")
    set_cv_path(choice)
    log.info("Active CV set to %s", choice.name)
    return _redirect_with_msg(f"Active CV set to {choice.name}. Run a scan to parse it.")


@app.post("/jobs/{key:path}/ignore")
def ignore(key: str):
    services.ignore_job(key)
    return _redirect_with_msg("Job ignored.")


@app.post("/jobs/{key:path}/unignore")
def unignore(key: str):
    services.unignore_job(key)
    return _redirect_with_msg("Job restored.")


@app.get("/jobs/{key:path}", response_class=HTMLResponse)
def job_detail(request: Request, key: str):
    job = db.get_job(key)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    application = db.get_application(key)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"job": job, "application": application},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    path = STATIC_DIR / "favicon.ico"
    if not path.exists():
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(path, media_type="image/x-icon")


@app.get("/files/{name}")
def download(name: str):
    # Only serve files from the generated dir.
    path = (GENERATED_DIR / name).resolve()
    if GENERATED_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name, media_type="application/pdf")


def _redirect_with_msg(msg: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/?msg={urllib.parse.quote(msg)}", status_code=303
    )
