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
from .config import GENERATED_DIR, STATIC_DIR, TEMPLATES_DIR, get_settings
from .models import JobStatus

app = FastAPI(title="Job Hunter")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    matched = db.list_jobs(statuses=[JobStatus.NEW], min_relevance=settings.min_relevance)
    applied = db.list_jobs(statuses=[JobStatus.APPLIED])
    ignored = db.list_jobs(statuses=[JobStatus.IGNORED])
    profile = db.load_profile()
    counts = db.counts_by_status()

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
            "flash": request.query_params.get("msg"),
        },
    )


@app.post("/scan")
async def scan(force_profile: bool = Form(False)):
    try:
        summary = await services.run_scan(force_profile=force_profile)
    except services.ProfileError as e:
        return _redirect_with_msg(str(e))
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
    for key in keys:
        try:
            services.apply_to_job(key)
            applied.append(key)
        except services.ProfileError as e:
            errors.append(str(e))
            break  # config errors will repeat; stop early
    msg = f"Applied to {len(applied)} job(s)."
    if errors:
        msg += f" Stopped: {errors[0]}"
    return _redirect_with_msg(msg)


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
