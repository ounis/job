"""Render tailored CV and motivation letter text into PDF files (ReportLab).

Pure-Python, no system dependencies — installs and runs cleanly on Windows.
The AI produces plain text; we lay it out with sensible typography. Blank lines
separate paragraphs; short ALL-CAPS or title-case lines are treated as headings.
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from ..config import GENERATED_DIR
from ..models import CVProfile, JobPosting


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TitleX", parent=base["Title"], fontSize=18, spaceAfter=6,
        ),
        "heading": ParagraphStyle(
            "HeadingX", parent=base["Heading2"], fontSize=12,
            spaceBefore=12, spaceAfter=4, textColor="#1a4d8f",
        ),
        "body": ParagraphStyle(
            "BodyX", parent=base["Normal"], fontSize=10.5, leading=15,
        ),
        "body_just": ParagraphStyle(
            "BodyJust", parent=base["Normal"], fontSize=10.5, leading=15,
            alignment=TA_JUSTIFY,
        ),
        "muted": ParagraphStyle(
            "Muted", parent=base["Normal"], fontSize=9, textColor="#666666",
        ),
    }
    return styles


def _looks_like_heading(line: str) -> bool:
    s = line.strip().rstrip(":")
    if not s or len(s) > 40:
        return False
    if s.isupper():
        return True
    # Title Case short line with no sentence punctuation
    return s == s.title() and not re.search(r"[.!?]", s)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s or "doc")[:maxlen]


def _build(path: Path, flowables) -> None:
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    doc.build(flowables)


def render_cv_pdf(cv_text: str, job: JobPosting, out_dir: Path | None = None) -> Path:
    styles = _styles()
    flow = []
    for block in cv_text.split("\n"):
        line = block.rstrip()
        if not line.strip():
            flow.append(Spacer(1, 6))
            continue
        if _looks_like_heading(line):
            flow.append(Paragraph(_esc(line.strip().rstrip(":")), styles["heading"]))
        else:
            flow.append(Paragraph(_esc(line), styles["body"]))

    out_dir = out_dir or GENERATED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"cv-{_slug(job.company)}-{_slug(job.title)}.pdf"
    _build(path, flow)
    return path


# ----------------------------------------------------------------------------
# HTML-based CV rendering (nicer, styled output via xhtml2pdf)
# ----------------------------------------------------------------------------
_CV_CSS = """
@page { size: A4; margin: 1.4cm 1.7cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt;
       color: #2b3440; line-height: 1.5; }

/* Header: compact, photo as a small avatar beside the name block. The header
   table cells are borderless and vertically centered. */
.hdr-table { margin-bottom: 4pt; }
.hdr-photo-cell { width: 74pt; padding-right: 12pt; }
.photo { width: 70pt; }
.name { font-size: 21pt; font-weight: bold; color: #143c70; }
.headline { font-size: 11.5pt; color: #4a5568; margin-top: 2pt; }
.contact { font-size: 9pt; color: #5a6b7b; margin-top: 4pt; }
.rule { border-bottom: 2pt solid #1a4d8f; margin: 8pt 0 14pt; }

/* Sections — keep the title with the content that follows (no orphan titles). */
h2 { font-size: 12.5pt; color: #1a4d8f; font-weight: bold;
     border-bottom: 1pt solid #c9d6e8; padding-bottom: 3pt;
     margin: 14pt 0 7pt; page-break-after: avoid; -pdf-keep-with-next: true; }
h3 { font-size: 10.5pt; font-weight: bold; margin: 9pt 0 1pt; color: #243447;
     page-break-after: avoid; -pdf-keep-with-next: true; }
h3 + p, h3 + ul { page-break-before: avoid; }
p { margin: 0 0 6pt; text-align: justify; }
ul { margin: 3pt 0 9pt; }
li { margin: 2pt 0; line-height: 1.4; }
strong { color: #1c2430; }
"""


def render_cv_pdf_html(
    cv_body_html: str,
    job: JobPosting,
    profile: CVProfile,
    out_dir: Path | None = None,
) -> Path:
    """Render a polished CV PDF from AI-produced body HTML via xhtml2pdf.

    Builds a full styled document: a contact header (with optional photo from
    data/photo.jpg) plus the sanitized section HTML. Pure-Python, exe-safe.
    """
    from xhtml2pdf import pisa
    from ..config import DATA_DIR

    contact_bits = [b for b in (profile.email, profile.phone, profile.location) if b]
    contact = " · ".join(contact_bits)

    # Optional photo — embedded if a photo file exists in data/. Accept common
    # names (photo.*, pic.*, me.*) and extensions.
    photo_tag = ""
    photo_names = [f"{stem}.{ext}" for stem in ("photo", "pic", "me", "profile")
                   for ext in ("jpg", "jpeg", "png")]
    for name in photo_names:
        p = DATA_DIR / name
        if p.exists():
            photo_tag = f'<img class="photo" src="{p.resolve().as_uri()}" />'
            break

    details = (
        f'<div class="name">{_esc(profile.full_name or "")}</div>'
        + (f'<div class="headline">{_esc(profile.headline)}</div>' if profile.headline else "")
        + (f'<div class="contact">{_esc(contact)}</div>' if contact else "")
    )
    if photo_tag:
        header = (
            '<table class="hdr-table" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td class="hdr-photo-cell" valign="top">{photo_tag}</td>'
            f'<td valign="top">{details}</td>'
            "</tr></table>"
            '<div class="rule"></div>'
        )
    else:
        header = details + '<div class="rule"></div>'

    doc = (
        f"<html><head><style>{_CV_CSS}</style></head><body>"
        f"{header}"
        f"{cv_body_html}"
        "</body></html>"
    )

    out_dir = out_dir or GENERATED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"cv-{_slug(job.company)}-{_slug(job.title)}.pdf"
    with open(path, "wb") as f:
        status = pisa.CreatePDF(doc, dest=f)
    if status.err:
        raise RuntimeError("xhtml2pdf failed to render the CV")
    return path


def render_letter_pdf(
    letter_text: str,
    job: JobPosting,
    profile: CVProfile,
    out_dir: Path | None = None,
) -> Path:
    styles = _styles()
    flow = []

    # Header: candidate contact then company / role.
    if profile.full_name:
        flow.append(Paragraph(_esc(profile.full_name), styles["title"]))
    contact_bits = [b for b in (profile.email, profile.phone, profile.location) if b]
    if contact_bits:
        flow.append(Paragraph(_esc(" · ".join(contact_bits)), styles["muted"]))
    flow.append(Spacer(1, 12))

    subject = f"Application: {job.title}"
    if job.company:
        subject += f" — {job.company}"
    flow.append(Paragraph(_esc(subject), styles["heading"]))
    flow.append(Spacer(1, 6))

    for para in re.split(r"\n\s*\n", letter_text.strip()):
        text = " ".join(l.strip() for l in para.splitlines() if l.strip())
        if text:
            flow.append(Paragraph(_esc(text), styles["body_just"]))
            flow.append(Spacer(1, 8))

    out_dir = out_dir or GENERATED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"letter-{_slug(job.company)}-{_slug(job.title)}.pdf"
    _build(path, flow)
    return path
