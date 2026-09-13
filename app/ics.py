"""Build iCalendar (.ics) documents from job events.

Produces a minimal but valid VCALENDAR/VEVENT stream that Apple Calendar,
Google Calendar and Outlook can import. Datetimes are emitted as floating local
times (no TZID) since events are entered via a browser datetime-local field.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import EVENT_TYPE_LABELS, EventType, JobEvent


def _fold(line: str) -> str:
    """Fold long content lines to <=75 octets per RFC 5545 (simple char-based)."""
    if len(line) <= 75:
        return line
    out, rest = [line[:75]], line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def _esc(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _to_ical_dt(value: str) -> str:
    """Convert an ISO/datetime-local string to iCal local format YYYYMMDDTHHMMSS."""
    s = (value or "").strip()
    # Keep only the date+time portion, drop timezone/fractional bits.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if not m:
        # Fall back to now if unparseable, so the file stays valid.
        now = datetime.now()
        return now.strftime("%Y%m%dT%H%M%S")
    y, mo, d, h, mi = m.groups()
    return f"{y}{mo}{d}T{h}{mi}00"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events: list[JobEvent], job_titles: dict[str, str] | None = None) -> str:
    """Return a VCALENDAR string for the given events.

    job_titles maps job_key -> title so a global export can name each event's
    job. Per-job exports can pass a single-entry map or None.
    """
    job_titles = job_titles or {}
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Job Hunter//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for ev in events:
        et_label = EVENT_TYPE_LABELS.get(ev.event_type, "Event")
        job_title = job_titles.get(ev.job_key, "")
        summary_bits = [b for b in (et_label, ev.title, job_title) if b]
        summary = " — ".join(summary_bits) or "Event"
        uid = f"{ev.id or _stamp()}-{ev.job_key}@job-hunter"
        dt = _to_ical_dt(ev.starts_at)

        lines.append("BEGIN:VEVENT")
        lines.append(_fold(f"UID:{uid}"))
        lines.append(f"DTSTAMP:{_stamp()}")
        lines.append(f"DTSTART:{dt}")
        lines.append(_fold(f"SUMMARY:{_esc(summary)}"))
        if ev.location:
            lines.append(_fold(f"LOCATION:{_esc(ev.location)}"))
        desc_bits = [b for b in (ev.notes, job_title) if b]
        if desc_bits:
            lines.append(_fold(f"DESCRIPTION:{_esc(' | '.join(desc_bits))}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
