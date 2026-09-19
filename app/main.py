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

import asyncio
import datetime as _dt
import urllib.parse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, services
from .ics import build_ics
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
from .models import (
    EVENT_TYPE_LABELS,
    STATUS_LABELS,
    EventType,
    JobStatus,
)

configure_logging()
log = get_logger("app.main")

app = FastAPI(title="Job Hunter")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Expose the current search location to every template (read live so it always
# reflects the setting, e.g. after changing it on the Settings page).
templates.env.globals["search_location"] = lambda: get_settings().search_location
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Server-side guard against concurrent/duplicate actions. Because every action
# is a POST that navigates away and redirects back, a client-side button lock
# can't reliably stop a second trigger while the first is still running. This
# middleware serializes mutating POSTs: if one is in flight, a second POST is
# rejected with a friendly "action in progress" redirect instead of running
# concurrently. Read-only GETs (dashboard, calendar, logs stream, ...) are
# never blocked.
_action_in_progress = False


@app.middleware("http")
async def _serialize_actions(request: Request, call_next):
    global _action_in_progress
    if request.method != "POST":
        return await call_next(request)

    if _action_in_progress:
        # Another action is running — reject this one rather than run in parallel.
        ref = request.headers.get("referer", "")
        target = "/"
        from urllib.parse import urlsplit as _urlsplit
        if ref:
            p = _urlsplit(ref).path
            if p.startswith("/") and not p.startswith("//"):
                target = p
        sep = "&" if "?" in target else "?"
        msg = urllib.parse.quote("An action is already in progress — please wait.")
        return RedirectResponse(url=f"{target}{sep}msg={msg}", status_code=303)

    _action_in_progress = True
    try:
        return await call_next(request)
    finally:
        _action_in_progress = False


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
    # Concurrency is handled globally by the _serialize_actions middleware.
    # If a CV was chosen in the scan form, switch to it before scanning. A
    # switch always forces a re-parse so the new CV's profile is used.
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
            _f("AI_PROVIDER", "ai_provider", "select", "AI provider",
               "openai = cloud (needs key); ollama = local, free, no key.",
               options=["openai", "ollama"]),
            _f("OPENAI_API_KEY", "openai_api_key", "secret", "OpenAI API key",
               "Used when AI provider is 'openai'. Without any AI, the app falls "
               "back to a free heuristic."),
            _f("OPENAI_MODEL", "openai_model", "text", "OpenAI model",
               "e.g. gpt-4o-mini (cheapest). Used when provider is 'openai'."),
            _f("OLLAMA_BASE_URL", "ollama_base_url", "text", "Ollama URL",
               "Local Ollama server. Default http://localhost:11434/v1."),
            _f("OLLAMA_MODEL", "ollama_model", "text", "Ollama model",
               "Must be pulled first (e.g. `ollama pull llama3.1`)."),
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
            _f("SEARCH_LOCATION", "search_location", "text", "Location(s)",
               "'Germany' for country-wide, or a comma list of cities / "
               "'remote', e.g. 'remote, Berlin, Munich'. Each is searched and "
               "results merge + de-duplicate."),
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
        "name": "Exclusions",
        "desc": "Comma-separated, case-insensitive. A job is dropped during a "
                "scan if it matches ANY of these (substring match).",
        "fields": [
            _f("EXCLUDE_COMPANIES", "exclude_companies", "text", "Exclude companies",
               "Employer name contains any of these (e.g. a recruiter, a past employer)."),
            _f("EXCLUDE_LOCATIONS", "exclude_locations", "text", "Exclude locations",
               "City/region contains any of these (e.g. München, Hamburg)."),
            _f("EXCLUDE_TITLE_KEYWORDS", "exclude_title_keywords", "text", "Exclude title keywords",
               "Job title contains any of these (e.g. Werkstudent, Praktikum, intern)."),
            _f("EXCLUDE_DESCRIPTION_KEYWORDS", "exclude_description_keywords", "text",
               "Exclude description keywords",
               "Description contains any of these (e.g. Zeitarbeit, PHP)."),
            _f("EXCLUDE_SOURCES", "exclude_sources", "text", "Exclude sources",
               "Job board name contains any of these (e.g. Glassdoor)."),
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


@app.post("/jobs/{key:path}/status")
def set_status_route(key: str, request: Request, status: str = Form(...)):
    try:
        st = JobStatus(status)
    except ValueError:
        return _redirect_back(request, "Unknown status.")
    services.set_job_status(key, st)
    return _redirect_back(request, f"Status set to {STATUS_LABELS.get(st, status)}.")


@app.post("/jobs/{key:path}/events/add")
def add_event_route(
    key: str, request: Request,
    event_type: str = Form(...), starts_at: str = Form(...),
    title: str = Form(""), location: str = Form(""), notes: str = Form(""),
):
    if not starts_at.strip():
        return _redirect_back(request, "Event needs a date/time.")
    services.add_event(key, event_type, starts_at.strip(), title.strip(),
                       location.strip(), notes.strip())
    return _redirect_back(request, "Event added.")


@app.post("/jobs/{key:path}/events/{event_id}/delete")
def delete_event_route(key: str, event_id: int, request: Request):
    services.delete_event(event_id)
    return _redirect_back(request, "Event removed.")


@app.post("/jobs/{key:path}/notes/add")
def add_note_route(key: str, request: Request, body: str = Form(...)):
    if not body.strip():
        return _redirect_back(request, "Note is empty.")
    services.add_note(key, body)
    return _redirect_back(request, "Note added.")


@app.post("/jobs/{key:path}/notes/{note_id}/delete")
def delete_note_route(key: str, note_id: int, request: Request):
    services.delete_note(note_id)
    return _redirect_back(request, "Note removed.")


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    # Filters via query params: status (repeatable), type (repeatable), when.
    qp = request.query_params
    sel_statuses = [JobStatus(s) for s in qp.getlist("status") if s in JobStatus._value2member_map_]
    sel_types = [EventType(t) for t in qp.getlist("type") if t in EventType._value2member_map_]
    when = qp.get("when", "all")  # all | upcoming | past

    rows = db.list_all_events(
        statuses=sel_statuses or None,
        event_types=sel_types or None,
    )
    now19 = _dt.datetime.now().isoformat()[:19]
    if when == "upcoming":
        rows = [r for r in rows if (r["event"].starts_at or "")[:19] >= now19]
    elif when == "past":
        rows = [r for r in rows if (r["event"].starts_at or "")[:19] < now19]
        rows.reverse()

    return templates.TemplateResponse(
        request, "calendar.html",
        {
            "rows": rows,
            "statuses": list(JobStatus),
            "status_labels": STATUS_LABELS,
            "event_types": list(EventType),
            "event_type_labels": EVENT_TYPE_LABELS,
            "sel_statuses": [s.value for s in sel_statuses],
            "sel_types": [t.value for t in sel_types],
            "when": when,
            "flash": qp.get("msg"),
        },
    )


def _ics_response(ics: str, filename: str) -> Response:
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/calendar.ics")
def calendar_ics(request: Request):
    """Export all events (respecting the same filters as /calendar) as .ics."""
    qp = request.query_params
    sel_statuses = [JobStatus(s) for s in qp.getlist("status") if s in JobStatus._value2member_map_]
    sel_types = [EventType(t) for t in qp.getlist("type") if t in EventType._value2member_map_]
    when = qp.get("when", "all")

    rows = db.list_all_events(statuses=sel_statuses or None, event_types=sel_types or None)
    now19 = _dt.datetime.now().isoformat()[:19]
    if when == "upcoming":
        rows = [r for r in rows if (r["event"].starts_at or "")[:19] >= now19]
    elif when == "past":
        rows = [r for r in rows if (r["event"].starts_at or "")[:19] < now19]

    events = [r["event"] for r in rows]
    titles = {r["job_key"]: r["job_title"] for r in rows}
    return _ics_response(build_ics(events, titles), "job-hunter-calendar.ics")


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    return templates.TemplateResponse(request, "logs.html", {})


def _log_file():
    from .config import DATA_DIR
    return DATA_DIR / "job.log"


@app.get("/logs/stream")
async def logs_stream(request: Request):
    """Server-Sent Events stream of data/job.log — sends the tail, then follows.

    Emits each log line as an SSE 'data:' event. The browser renders and colors
    them. Stops when the client disconnects.
    """
    path = _log_file()

    async def gen():
        # Send the last ~200 lines first for immediate context.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-200:]
            for line in tail:
                yield f"data: {line.rstrip()}\n\n"
        except FileNotFoundError:
            yield "data: (log file not created yet)\n\n"
            tail = []

        # Follow the file for new lines.
        pos = None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)  # end
                pos = f.tell()
        except FileNotFoundError:
            pos = 0

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, 2)
                        end = f.tell()
                        if pos is not None and end < pos:
                            # File was truncated (fresh run) — restart from top.
                            pos = 0
                        f.seek(pos or 0)
                        new = f.read()
                        pos = f.tell()
                    if new:
                        for line in new.splitlines():
                            yield f"data: {line}\n\n"
                    else:
                        yield ": keep-alive\n\n"
                except FileNotFoundError:
                    yield ": waiting for log file\n\n"
                await asyncio.sleep(1.0)
        except (asyncio.CancelledError, GeneratorExit):
            # Client navigated away / reconnected — normal SSE teardown, not an
            # error. Exit quietly so it doesn't surface as an ASGI exception.
            return

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/jobs/{key:path}/calendar.ics")
def job_calendar_ics(key: str):
    job = db.get_job(key)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    events = db.list_events_for_job(key)
    titles = {key: job.posting.title}
    safe = key.replace(":", "-").replace("/", "-")
    return _ics_response(build_ics(events, titles), f"job-{safe}.ics")


@app.post("/jobs/{key:path}/forget")
def forget(key: str, request: Request):
    services.forget_job(key)
    # The job no longer exists; if the request came from its detail page, send
    # to the dashboard instead of a now-404 URL.
    ref = request.headers.get("referer", "")
    if key in ref:
        return _redirect_with_msg("Job forgotten (permanently deleted).")
    return _redirect_back(request, "Job forgotten (permanently deleted).")


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
    events = services.split_events(db.list_events_for_job(key))
    notes = db.list_notes_for_job(key)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "application": application,
            "events": events,
            "notes": notes,
            "statuses": list(JobStatus),
            "status_labels": STATUS_LABELS,
            "event_types": list(EventType),
            "event_type_labels": EVENT_TYPE_LABELS,
        },
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
