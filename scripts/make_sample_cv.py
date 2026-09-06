"""Generate a realistic fake CV at data/cv.pdf for testing the scan/apply flow.

All data is fictional. Run:  python scripts/make_sample_cv.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "data" / "cv.pdf"

NAME = "Jordan Muster"
CONTACT = "jordan.muster@example.com · +49 151 2345678 · Berlin, Germany"

SUMMARY = (
    "Senior Backend Engineer with 7 years of experience building and scaling "
    "distributed services in Python and Go. Comfortable owning systems end to "
    "end: API design, data modeling, cloud infrastructure, observability and "
    "on-call. Looking for a senior or lead backend role in Germany."
)

SKILLS = (
    "Python, Go, FastAPI, Django, PostgreSQL, Redis, Kafka, Docker, Kubernetes, "
    "AWS, Terraform, gRPC, REST, CI/CD, Prometheus, Grafana"
)

EXPERIENCE = [
    ("Senior Backend Engineer — Contorion GmbH, Berlin (2021–present)", [
        "Led the redesign of the order-processing platform (Python/FastAPI), "
        "cutting p95 latency by 40%.",
        "Introduced event-driven flows on Kafka; owned schema governance and "
        "consumer reliability.",
        "Mentored 3 engineers and ran the on-call rotation for 12 services.",
    ]),
    ("Backend Engineer — Zalando SE, Berlin (2018–2021)", [
        "Built internal REST/gRPC services on AWS (ECS, RDS, SQS) with Terraform.",
        "Improved deploy frequency from weekly to daily via CI/CD pipelines.",
    ]),
    ("Software Developer — TechStart UG, Munich (2017–2018)", [
        "Developed a Django monolith and its first API extraction.",
    ]),
]

EDUCATION = [
    "M.Sc. Computer Science — Technical University of Munich (2015–2017)",
    "B.Sc. Computer Science — University of Stuttgart (2012–2015)",
]

LANGUAGES = "German (native), English (fluent), French (basic)"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = getSampleStyleSheet()
    name = ParagraphStyle("Name", parent=base["Title"], fontSize=20, spaceAfter=2)
    contact = ParagraphStyle("Contact", parent=base["Normal"], fontSize=9.5,
                             textColor="#555555", spaceAfter=10)
    h = ParagraphStyle("H", parent=base["Heading2"], fontSize=12,
                       textColor="#1a4d8f", spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("B", parent=base["Normal"], fontSize=10.5, leading=15)
    bullet = ParagraphStyle("Bul", parent=body, leftIndent=12, bulletIndent=2)

    flow = [Paragraph(NAME, name), Paragraph(CONTACT, contact)]
    flow += [Paragraph("Summary", h), Paragraph(SUMMARY, body)]
    flow += [Paragraph("Skills", h), Paragraph(SKILLS, body)]

    flow.append(Paragraph("Experience", h))
    for role, points in EXPERIENCE:
        flow.append(Paragraph(f"<b>{role}</b>", body))
        for p in points:
            flow.append(Paragraph(f"• {p}", bullet))
        flow.append(Spacer(1, 6))

    flow.append(Paragraph("Education", h))
    for e in EDUCATION:
        flow.append(Paragraph(e, body))

    flow += [Paragraph("Languages", h), Paragraph(LANGUAGES, body)]

    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    doc.build(flow)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
