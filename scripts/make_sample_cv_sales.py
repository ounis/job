"""Generate a realistic fake Sales CV at data/cv-sales.pdf for testing.

Sales / Account Management is one of the highest-volume job categories (many
openings across companies), which makes it a good CV for exercising the scan
flow with plenty of matches. All data is fictional.

Run:  python scripts/make_sample_cv_sales.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).resolve().parent.parent / "data" / "cv-sales.pdf"

NAME = "Alex Beispiel"
CONTACT = "alex.beispiel@example.com · +49 160 9876543 · Munich, Germany"

SUMMARY = (
    "Results-driven Account Manager with 6 years of B2B sales experience across "
    "SaaS and services. Consistently exceeds quota through consultative selling, "
    "pipeline discipline and strong customer relationships. Seeking an Account "
    "Manager, Sales Manager or Business Development role in Germany."
)

SKILLS = (
    "B2B Sales, Account Management, Business Development, Lead Generation, "
    "Salesforce, HubSpot CRM, Cold Outreach, Negotiation, Upselling, "
    "Cross-selling, Pipeline Management, Forecasting, SaaS, Key Account, "
    "Customer Success, German, English"
)

EXPERIENCE = [
    ("Senior Account Manager — CloudWerk GmbH, Munich (2021–present)", [
        "Managed a portfolio of 40+ key accounts worth €3.2M ARR; grew renewals "
        "to 94% and expanded upsell revenue by 28% year over year.",
        "Exceeded annual quota four quarters running (avg. 118% attainment).",
        "Built the outbound playbook adopted across a 6-person sales team.",
    ]),
    ("Account Executive — Vertrieb24 AG, Frankfurt (2019–2021)", [
        "Closed new-business deals across DACH mid-market; €1.1M new ARR in 2020.",
        "Ran full sales cycle from prospecting to close using Salesforce.",
    ]),
    ("Sales Development Representative — StartVertrieb UG, Berlin (2018–2019)", [
        "Booked 25+ qualified meetings per month via cold calls and email.",
        "Consistently top-2 SDR by pipeline generated.",
    ]),
]

EDUCATION = [
    "B.A. Business Administration — LMU Munich (2014–2017)",
]

LANGUAGES = "German (native), English (fluent), Spanish (basic)"


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
