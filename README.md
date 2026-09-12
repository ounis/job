# Job Hunter

A local web app that scans job boards for roles that fit your CV (Germany-wide,
configurable), scores them by relevance to your seniority and skills, and — for
the jobs you pick — generates a tailored CV and motivation letter as PDFs. It
tracks which jobs you've applied to or ignored so they don't clutter future
scans, and stores the exact payload used for each application.

## How it works

1. You drop one or more CVs into `data/`. The app auto-detects them and lets you
   pick the active CV from a dropdown on the dashboard.
2. **Scan** parses your CV once (AI when a key is set, otherwise a keyword
   heuristic), infers seniority + skills, derives search keywords, queries the
   job provider(s), and scores every new job 0–100.
3. The dashboard lists matches ordered by relevance, with status badges.
4. Tick the jobs you want and hit **Prepare selected**. For each one the app
   generates a tailored CV + motivation letter (PDF), stores the payload, and
   marks it `prepared` — it does **not** submit anything.
5. Review the prepared documents, open the posting to submit, then click
   **Mark as applied** to record the application (with a date). You can also
   **Regenerate** documents (e.g. after adding an AI key) or move a job back
   from applied/ignored.
6. Prepared/applied/ignored jobs are kept out of fresh matching but stay visible
   with an obvious status badge.

> **On "applying":** No individual job platform offers a legitimate one-click
> auto-submit API. So the app prepares your tailored documents and records
> everything; you submit on the posting page and mark it applied. Honest and
> ToS-safe. The provider layer is pluggable, so if you later get access to an
> API that *does* submit, it drops into `app/providers/`.

### Status lifecycle

`new` → `prepared` → `applied`, plus `ignored`. Preparing generates documents;
marking applied stamps the application date; you can unmark applied (back to
`prepared`) or regenerate at any point. Each prepared/applied job is tagged
**AI** or **non-AI** depending on whether OpenAI was available when its
documents were generated.

### Working without an OpenAI key

Everything works without AI, just with cruder results:
- **Scanning/scoring** falls back to a keyword heuristic (derives keywords and
  skills from your CV text — works for any field, not just tech).
- **Preparing** still produces a plain, non-tailored CV + letter from your
  profile, clearly tagged **non-AI** in the UI. Add a key and **Regenerate** to
  upgrade them to AI-tailored versions.

## Stack

- **FastAPI + Uvicorn** web app, server-rendered (Jinja2) — no frontend build.
- **SQLite** for tracking (single file at `data/jobs.db`).
- **OpenAI** for CV parsing, seniority inference, relevance scoring, and
  document generation (behind a swappable `AIClient`).
- **JSearch (RapidAPI)** as the default job source — a broad aggregator that
  pulls from Google for Jobs (LinkedIn, Indeed, Glassdoor, company pages, ...).
  **Adzuna** (also an aggregator) and a `bundesagentur` stub are included too.
  Providers are pluggable and results from multiple are merged + de-duplicated.
- **ReportLab** for PDF generation (pure Python — no system deps on Windows).

## Download the Windows build (no Python needed)

Grab `job.exe` from the [Releases page](https://github.com/ounis/job/releases).
Put it in a folder, drop your `.env` and CV next to it, then double-click it —
it starts the app and opens your browser at http://127.0.0.1:8000. Close the
console window to stop it.

```
some-folder\
  job.exe
  .env            <- your config (copy from .env.example)
  data\
    cv.pdf        <- your CV
```

The exe is built automatically on every `vX.Y.Z` tag by a GitHub Actions
workflow on a Windows runner (`.github/workflows/build-windows.yml`) and
attached to the matching release.

> **First-launch warning:** the exe is unsigned, so Windows SmartScreen may show
> an "unknown publisher" prompt the first time you run it. Click **More info →
> Run anyway**. Removing this prompt requires a code-signing certificate.

## Run from source

```bat
:: 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 2. Install dependencies
pip install -r requirements.txt

:: 3. Configure
copy .env.example .env
:: then edit .env (see below)

:: 4. Put your CV(s) in data\ (pdf, docx, txt or md) — auto-detected; pick the
::    active one from the dashboard. CV_PATH defaults to the data\ folder.

:: 5. Run
python run.py
```

Open http://127.0.0.1:8000 and click **Run scan**.

### Build the exe yourself

```bat
pip install pyinstaller
pyinstaller job.spec
:: -> dist\job.exe
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # edit it
python run.py
```

## Configuration

You can edit all of these from the **Settings** page in the web UI (changes save
to `.env` and take effect immediately, no restart), or edit `.env` directly.

| Key | What it does |
| --- | --- |
| `OPENAI_API_KEY` | Enables AI parsing/scoring and AI-tailored CV+letter generation. Without it, scanning uses a keyword heuristic and preparing produces plain non-AI drafts. |
| `OPENAI_MODEL` | Default `gpt-4o-mini` (cheapest). |
| `RAPIDAPI_KEY` | Key for JSearch (RapidAPI) — the default, broadest source. Subscribe (free tier) at https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch. |
| `JSEARCH_MAX_PAGES` | Pages fetched per JSearch query (~10 results/page). Free tier = `1`. Raise only if your plan allows more. |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Free credentials from https://developer.adzuna.com/ (alternative/additional source). |
| `ACTIVE_PROVIDERS` | Comma list: `jsearch`, `adzuna`, `bundesagentur`. Run several — results merge + de-dupe. |
| `SEARCH_LOCATION` | `Germany` = country-wide, or a city like `Berlin`. |
| `SEARCH_DISTANCE_KM` | Radius around the location. |
| `SEARCH_KEYWORDS` | Comma list. Leave empty to auto-derive from your CV. |
| `MAX_RESULTS` | Jobs pulled per scan. |
| `CV_PATH` | A **directory** (default `data`) to auto-scan for CVs, or a specific file to pin one. |
| `MIN_RELEVANCE` | Minimum score (0–100) to show a job as a match. Use ~`20` with the no-AI heuristic; ~`40` once AI scoring is on. |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Logs also mirror to `data/job.log` (rotating, fresh each run). |

### AI usage & cost controls

The heaviest OpenAI cost is scoring (one call per new job), so these let you cap
spend. All are editable from the Settings page.

| Key | What it does |
| --- | --- |
| `AI_SCORE` | `false` = never AI-score during scan (free heuristic only, zero tokens). Tokens are then spent only when preparing documents. |
| `AI_PREFILTER` | Heuristic-rank all jobs first, then AI-score only the top N. |
| `AI_SCORE_TOP_N` | How many jobs get an AI score per scan when prefiltering. |
| `AI_MAX_TOKENS_TEXT` | Response cap for CV/letter generation. |
| `AI_MAX_TOKENS_JSON` | Response cap for CV parsing and scoring. |
| `AI_*_CHARS` | Truncation limits on the CV/job text sent to the model. |

### Getting API keys

- **JSearch** (broad aggregator, default): subscribe at
  https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch → copy your RapidAPI key.
- **Adzuna** (optional additional source): register at
  https://developer.adzuna.com/ → free `app_id` + `app_key`.
- **OpenAI** (AI): https://platform.openai.com/ → API key.

## Project layout

```
app/
  main.py            FastAPI app + routes
  config.py          settings from .env
  models.py          shared domain models
  db.py              SQLite persistence (jobs + profile)
  services.py        scan + apply orchestration
  ai/
    cv_reader.py     extract text from pdf/docx/txt/md
    client.py        swappable AI client (OpenAI)
    operations.py    parse CV, score, generate CV + letter
  providers/
    base.py          JobProvider interface
    jsearch.py       JSearch / RapidAPI (default, broad aggregator)
    adzuna.py        Adzuna (Germany)
    bundesagentur.py stub, ready to enable
  pdf/render.py      CV + letter -> PDF (ReportLab)
  logging_setup.py   console + rotating file logging (data/job.log)
  templates/         dashboard, job detail, settings, ignored
  static/            css + js
data/                sqlite db, your CV(s), generated/ PDFs, job.log
run.py               entry point
```

## Tracking model

Every job seen is stored with a status: `new`, `prepared`, `applied`, or
`ignored`. A re-scan never demotes a `prepared`/`applied`/`ignored` job back to
`new`, so your decisions stick. Prepared/applied jobs also store the generated
CV text, the motivation letter text, the PDF paths, the full application
payload, and (once applied) the application date — all viewable on the job
detail page. Ignored jobs get their own page where you can restore them, and the
dashboard has a **Clear results** action to drop current matches before a fresh
scan (kept-status jobs are preserved).

## Notes

- Generated PDFs live in `data/generated/` and are downloadable from the UI.
- The AI is instructed to stay truthful — it re-emphasizes and rephrases your
  real experience for each role but is told never to invent facts. Review every
  generated document before submitting.
- Adding a new job source = implement `JobProvider` in `app/providers/` and add
  it to the registry in `app/providers/__init__.py`.
