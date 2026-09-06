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
