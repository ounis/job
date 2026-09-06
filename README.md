# Job Hunter

A local web app that scans job boards for roles that fit your CV (Germany-wide,
configurable), scores them by relevance to your seniority and skills, and — for
the jobs you pick — generates a tailored CV and motivation letter as PDFs. It
tracks which jobs you've applied to or ignored so they don't clutter future
scans, and stores the exact payload used for each application.

## How it works

1. You drop your CV into `data/` and point `.env` at it.
2. **Scan** parses your CV once (AI), infers seniority + skills, derives search
   keywords, queries the job provider(s), and scores every new job 0–100.
3. The dashboard lists matches ordered by relevance, with status badges.
4. Tick the jobs you want and hit **Apply to selected**. For each one the app
   generates a tailored CV + motivation letter (PDF), stores the payload, marks
   it `applied`, and gives you the posting link to submit.
5. Applied/ignored jobs are kept out of fresh matching but stay visible with an
   obvious status badge.

> **On "applying":** No individual job platform offers a legitimate one-click
> auto-submit API. So the Apply action prepares your tailored documents and
> records everything, then you submit on the posting page. Honest and
> ToS-safe. The provider layer is pluggable, so if you later get access to an
> API that *does* submit, it drops into `app/providers/`.

## Stack

- **FastAPI + Uvicorn** web app, server-rendered (Jinja2) — no frontend build.
- **SQLite** for tracking (single file at `data/jobs.db`).
- **OpenAI** for CV parsing, seniority inference, relevance scoring, and
  document generation (behind a swappable `AIClient`).
- **Adzuna API** for job data (Germany), with a `bundesagentur` provider stub
  ready to enable. Providers are pluggable.
- **ReportLab** for PDF generation (pure Python — no system deps on Windows).

## Setup (Windows)

```bat
:: 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

:: 2. Install dependencies
pip install -r requirements.txt

:: 3. Configure
copy .env.example .env
:: then edit .env (see below)

:: 4. Put your CV in data\ (pdf, docx, txt or md) and set CV_PATH in .env

:: 5. Run
python run.py
```

Open http://127.0.0.1:8000 and click **Run scan**.

### Setup (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # edit it
python run.py
```

## Configuration (`.env`)

| Key | What it does |
| --- | --- |
| `OPENAI_API_KEY` | Enables AI parsing/scoring and CV+letter generation. Without it, scanning still works with a cruder keyword-based score, but applying is disabled. |
| `OPENAI_MODEL` | Default `gpt-4o-mini`. |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Free credentials from https://developer.adzuna.com/. Required to fetch jobs. |
| `ACTIVE_PROVIDERS` | Comma list: `adzuna`, `bundesagentur`. |
| `SEARCH_LOCATION` | `Germany` = country-wide, or a city like `Berlin`. |
| `SEARCH_DISTANCE_KM` | Radius around the location. |
| `SEARCH_KEYWORDS` | Comma list. Leave empty to auto-derive from your CV. |
| `MAX_RESULTS` | Jobs pulled per scan. |
| `CV_PATH` | Path to your CV file (e.g. `data/cv.pdf`). |
| `MIN_RELEVANCE` | Minimum score (0–100) for a job to show as a match. |

### Getting API keys

- **Adzuna** (job data): register at https://developer.adzuna.com/ → free
  `app_id` + `app_key`.
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
    adzuna.py        Adzuna (Germany)
    bundesagentur.py stub, ready to enable
  pdf/render.py      CV + letter -> PDF (ReportLab)
  templates/         dashboard + job detail
  static/            css + js
data/                sqlite db, your CV, generated/ PDFs
run.py               entry point
```

## Tracking model

Every job seen is stored with a status: `new`, `applied`, or `ignored`. A
re-scan never demotes an `applied`/`ignored` job back to `new`, so your
decisions stick. Applied jobs also store the generated CV text, the motivation
letter text, the PDF paths, and the full application payload — viewable on the
job detail page.

## Notes

- Generated PDFs live in `data/generated/` and are downloadable from the UI.
- The AI is instructed to stay truthful — it re-emphasizes and rephrases your
  real experience for each role but is told never to invent facts. Review every
  generated document before submitting.
- Adding a new job source = implement `JobProvider` in `app/providers/` and add
  it to the registry in `app/providers/__init__.py`.
