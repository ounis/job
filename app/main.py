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
    set_env_values,
)
from .logging_setup import attach_uvicorn_loggers, configure_logging, get_logger
from .models import JobStatus

configure_logging()
log = get_logger("app.main")

app = FastAPI(title="Job Hunter")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    # Uvicorn configured its own loggers just before this fires — re-attach our
    # console+file handlers so uvicorn's startup/access lines also land in job.log.
    attach_uvicorn_loggers()
    db.init_db()
    s = get_settings()
    log.info("Job Hunter starting up")
    log.info("Providers=%s location=%r ai_enabled=%s log_level=%s",
             s.provider_list, s.search_location, s.ai_enabled, s.log_level)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    matched = db.list_jobs(statuses=[JobStatus.NEW], min_relevance=settings.min_relevance)
    prepared = db.list_jobs(statuses=[JobStatus.PREPARED])
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
            "prepared": prepared,
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
async def scan(force_profile: bool = Form(False), cv: str = Form("")):
    # If a CV was chosen in the scan form, switch to it before scanning. A
    # switch always forces a re-parse so the new CV's profile is actually used.
    switched = False
    if cv:
        choice = Path(cv).resolve()
        detected = {p.resolve() for p in discover_cv_files()}
        if choice in detected and choice != get_settings().cv_full_path.resolve():
            set_cv_path(choice)
            switched = True
            log.info("Scan: switched active CV to %s", choice.name)
    try:
        summary = await services.run_scan(force_profile=force_profile or switched)
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


@app.post("/prepare")
def prepare(keys: list[str] = Form(default=[]), language: str = Form("de")):
    if not keys:
        return _redirect_with_msg("No jobs selected.")
    language = language if language in ("de", "en") else "de"
    prepared, errors = [], []
    used_fallback = False
    for key in keys:
        try:
            result = services.prepare_application(key, language)
            prepared.append(key)
            if not result.get("ai_generated", True):
                used_fallback = True
        except services.ProfileError as e:
            errors.append(str(e))
            break  # config errors will repeat; stop early
    msg = (
        f"Prepared {len(prepared)} application(s). Review the documents, then "
        "'Mark as applied' once you've submitted on the posting."
    )
    if used_fallback:
        msg += (
            " NOTE: OpenAI was unavailable, so these are plain, non-tailored "
            "drafts. Add OpenAI credit and re-prepare for tailored versions."
        )
    if errors:
        msg += f" Stopped: {errors[0]}"
    return _redirect_with_msg(msg)


@app.post("/jobs/{key:path}/mark-applied")
def mark_applied(key: str):
    try:
        ts = services.mark_job_applied(key)
    except services.ProfileError as e:
        return _redirect_with_msg(str(e))
    return _redirect_with_msg(f"Marked as applied on {ts[:10]}.")


@app.post("/jobs/{key:path}/unmark-applied")
def unmark_applied(key: str):
    services.unmark_job_applied(key)
    return _redirect_with_msg("Moved back to prepared (no longer marked applied).")


@app.post("/jobs/{key:path}/regenerate")
def regenerate(key: str, language: str = Form("de")):
    language = language if language in ("de", "en") else "de"
    try:
        result = services.prepare_application(key, language)
    except services.ProfileError as e:
        return _redirect_with_msg(str(e))
    msg = "Regenerated documents. Review them, then mark as applied when submitted."
    if not result.get("ai_generated", True):
        msg += (
            " NOTE: OpenAI was unavailable, so these are plain, non-tailored drafts."
        )
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
    # Immediately (re)parse the newly selected CV so the next scan uses it
    # without needing the "re-parse CV" checkbox. Don't fail the switch if
    # parsing hits an error (e.g. OpenAI down) — the heuristic still applies.
    try:
        services.ensure_profile(force=True)
        return _redirect_with_msg(
            f"Active CV set to {choice.name} and profile parsed. Run a scan for fresh matches."
        )
    except Exception as e:
        log.warning("CV switch: profile parse failed: %s", e)
        return _redirect_with_msg(
            f"Active CV set to {choice.name}. Run a scan (with 're-parse CV') to use it."
        )


# Editable settings, grouped for the settings page. Each field is a dict so we
# can carry optional affordances (min/max, options, unit) without huge tuples.
def _f(key, attr, typ, label, help="", **extra):
    return {"key": key, "attr": attr, "type": typ, "label": label,
            "help": help, **extra}

_SETTINGS_SPEC = [
    {
        "name": "Providers & API keys",
        "desc": "Credentials for the AI and job-search services. Keys are stored"
                " locally in your .env and never displayed after saving.",
        "fields": [
            _f("OPENAI_API_KEY", "openai_api_key", "secret", "OpenAI API key",
               "Enables AI CV parsing, scoring and tailored documents. Without "
               "it, the app falls back to a free heuristic."),
            _f("OPENAI_MODEL", "openai_model", "text", "OpenAI model",
               "e.g. gpt-4o-mini (cheapest). Only used when a key is set."),
            _f("RAPIDAPI_KEY", "rapidapi_key", "secret", "RapidAPI key (JSearch)",
               "The main job source. Free tier available on RapidAPI."),
            _f("ADZUNA_APP_ID", "adzuna_app_id", "text", "Adzuna app id",
               "Optional second job source (free)."),
            _f("ADZUNA_APP_KEY", "adzuna_app_key", "secret", "Adzuna app key",
               "Optional, pairs with the Adzuna app id."),
            _f("ACTIVE_PROVIDERS", "active_providers", "providers", "Active job sources",
               "Which sources to query. Results merge + de-duplicate.",
               options=[
                   {"value": "jsearch", "label": "JSearch (RapidAPI)", "available": True},
                   {"value": "adzuna", "label": "Adzuna", "available": True},
                   {"value": "bundesagentur", "label": "Bundesagentur (Germany-only API)",
                    "available": True},
               ]),
            _f("JSEARCH_MAX_PAGES", "jsearch_max_pages", "int", "JSearch pages per scan",
               "~10 results per page. Free tier allows 1.", min=1, max=20),
        ],
    },
    {
        "name": "Search",
        "desc": "What and where to search for.",
        "fields": [
            _f("SEARCH_LOCATION", "search_location", "text", "Location",
               "'Germany' for country-wide, or a city like 'Berlin'."),
            _f("SEARCH_DISTANCE_KM", "search_distance_km", "int", "Distance",
               "Radius around the location.", min=0, max=500, unit="km"),
            _f("SEARCH_KEYWORDS", "search_keywords", "text", "Keywords",
               "Comma-separated. Leave empty to auto-derive from your CV."),
            _f("MAX_RESULTS", "max_results", "int", "Max results per scan",
               "", min=1, max=200),
            _f("MIN_RELEVANCE", "min_relevance", "int", "Minimum relevance",
               "Hide matches scoring below this. Use ~20 with the free "
               "heuristic; ~40 once AI scoring is on.", min=0, max=100),
        ],
    },
    {
        "name": "AI usage & cost",
        "desc": "Control how many OpenAI tokens are spent. Lower = cheaper.",
        "fields": [
            _f("AI_SCORE", "ai_score", "bool", "Use AI to score jobs during scan",
               "Off = free heuristic only, zero tokens spent while scanning."),
            _f("AI_PREFILTER", "ai_prefilter", "bool", "Prefilter before AI scoring",
               "Cheaply rank first, then AI-score only the top N jobs."),
            _f("AI_SCORE_TOP_N", "ai_score_top_n", "int", "AI-score top N jobs",
               "How many jobs get an AI score per scan.", min=0, max=100),
            _f("AI_MAX_TOKENS_TEXT", "ai_max_tokens_text", "int",
               "Max tokens: CV & letter", "Caps document length.", min=200, max=4000),
            _f("AI_MAX_TOKENS_JSON", "ai_max_tokens_json", "int",
               "Max tokens: parse & score", "Caps analysis responses.", min=100, max=2000),
        ],
    },
    {
        "name": "App",
        "desc": "",
        "fields": [
            _f("LOG_LEVEL", "log_level", "select", "Log verbosity",
               "DEBUG is most verbose.",
               options=["DEBUG", "INFO", "WARNING", "ERROR"]),
        ],
    },
]

_SPEC_BY_KEY = {
    f["key"]: (f["attr"], f["type"])
    for group in _SETTINGS_SPEC
    for f in group["fields"]
}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    s = get_settings()
    groups = []
    for group in _SETTINGS_SPEC:
        rendered = []
        for f in group["fields"]:
            value = getattr(s, f["attr"])
            field = dict(f)
            field["value"] = value
            field["is_set"] = bool(value) if f["type"] == "secret" else None
            if f["type"] == "providers":
                field["selected"] = s.provider_list
            rendered.append(field)
        groups.append({"name": group["name"], "desc": group.get("desc", ""),
                       "fields": rendered})
    # A couple of at-a-glance status flags for the top of the page.
    status = {
        "ai_enabled": s.ai_enabled,
        "providers": s.provider_list,
        "active_cv": s.cv_full_path.name if s.cv_full_path.exists() else None,
    }
    return templates.TemplateResponse(
        request, "settings.html",
        {"groups": groups, "status": status,
         "flash": request.query_params.get("msg")},
    )


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    updates: dict[str, str] = {}
    errors: list[str] = []
    for key, (attr, typ) in _SPEC_BY_KEY.items():
        if typ == "bool":
            # Unchecked checkboxes are absent from the form.
            updates[key] = "true" if form.get(key) is not None else "false"
            continue
        if typ == "providers":
            # Multiple checkboxes with the same name -> comma list (order kept).
            selected = form.getlist(key)
            updates[key] = ",".join(selected)
            continue
        if key not in form:
            continue
        raw = str(form.get(key)).strip()
        if typ == "secret" and raw == "":
            # Blank secret = leave the existing value untouched.
            continue
        if typ == "int":
            try:
                int(raw)
            except ValueError:
                errors.append(f"{key} must be a whole number")
                continue
        updates[key] = raw

    if errors:
        return _redirect_with_msg("Not saved: " + "; ".join(errors), "/settings")

    set_env_values(updates)
    log.info("Settings updated via web UI: %s", ", ".join(sorted(updates)))
    return _redirect_with_msg("Settings saved.", "/settings")


@app.get("/ignored", response_class=HTMLResponse)
def ignored_page(request: Request):
    ignored = db.list_jobs(statuses=[JobStatus.IGNORED])
    return templates.TemplateResponse(
        request,
        "ignored.html",
        {"ignored": ignored, "flash": request.query_params.get("msg")},
    )


@app.post("/jobs/clear")
def clear_results():
    removed = db.clear_new_jobs()
    log.info("Cleared %d new job(s) from results", removed)
    return _redirect_with_msg(
        f"Cleared {removed} match(es). Run a scan to fetch fresh results."
    )


@app.post("/jobs/{key:path}/ignore")
def ignore(key: str, request: Request):
    services.ignore_job(key)
    return _redirect_back(request, "Job ignored.")


@app.post("/jobs/{key:path}/unignore")
def unignore(key: str, request: Request):
    services.unignore_job(key)
    return _redirect_back(request, "Job restored.")


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


def _redirect_with_msg(msg: str, target: str = "/") -> RedirectResponse:
    sep = "&" if "?" in target else "?"
    return RedirectResponse(
        url=f"{target}{sep}msg={urllib.parse.quote(msg)}", status_code=303
    )


def _redirect_back(request: Request, msg: str) -> RedirectResponse:
    """Redirect to the page the request came from (Referer), else the dashboard.

    Only same-origin relative paths are honored, so restoring from /ignored
    stays on /ignored while restoring elsewhere returns there.
    """
    ref = request.headers.get("referer", "")
    target = "/"
    if ref:
        path = urllib.parse.urlsplit(ref).path
        if path.startswith("/") and not path.startswith("//"):
            target = path
    sep = "&" if "?" in target else "?"
    return RedirectResponse(
        url=f"{target}{sep}msg={urllib.parse.quote(msg)}", status_code=303
    )
