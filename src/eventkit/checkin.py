"""Deterministic printable check-in sheet generation."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

from .codes import generate_checkin_codes, secret_from_environment
from .model import RosterResult, RowStatus


@dataclass(frozen=True, slots=True)
class CheckinEntry:
    """One included attendee on the printable check-in sheet."""

    row_number: int
    name: str
    institution: str
    normalized_name: str
    checkin_code: str


def build_checkin_entries(
    event_id: str,
    roster: RosterResult,
    secret: str,
    *,
    role: str = "Participant",
) -> tuple[CheckinEntry, ...]:
    """Create stable check-in entries from included roster rows.

    Excluded registrations never receive a check-in code.  Codes are issued
    in source order first so collision extension is deterministic, then the
    completed entries are sorted by normalized name, code, and source row.
    """

    included = [
        row
        for row in roster.rows
        if row.status is RowStatus.OK and row.attendee.normalized_email
    ]
    recipients = [
        {
            "email": row.attendee.normalized_email,
            "role": role,
            "name": row.attendee.name,
        }
        for row in included
    ]
    codes = generate_checkin_codes(event_id, recipients, secret)
    entries = [
        CheckinEntry(
            row_number=row.row_number,
            name=row.attendee.name,
            institution=row.attendee.institution,
            normalized_name=row.attendee.normalized_name,
            checkin_code=code,
        )
        for row, code in zip(included, codes)
    ]
    entries.sort(
        key=lambda entry: (
            entry.normalized_name.casefold(),
            entry.checkin_code,
            entry.row_number,
        )
    )
    return tuple(entries)


def render_checkin_html(
    entries: tuple[CheckinEntry, ...] | list[CheckinEntry],
    *,
    event_name: str = "Event check-in",
) -> bytes:
    """Render a deterministic, printer-friendly UTF-8 check-in sheet."""

    title = escape(event_name, quote=True)
    rows: list[str] = []
    for number, entry in enumerate(entries, start=1):
        rows.append(
            "        <tr>\n"
            f"          <td class=\"number\">{number}</td>\n"
            f"          <td>{escape(entry.name, quote=True)}</td>\n"
            f"          <td>{escape(entry.institution, quote=True)}</td>\n"
            f"          <td><code>{escape(entry.checkin_code, quote=True)}</code></td>\n"
            "          <td class=\"tick\"><span aria-label=\"Not checked in\">&#x2610;</span></td>\n"
            "        </tr>"
        )
    table_rows = "\n".join(rows) if rows else (
        "        <tr><td colspan=\"5\" class=\"empty\">No included attendees.</td></tr>"
    )
    document = f'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} — Check-in</title>
    <style>
      :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
      body {{ margin: 2rem auto; max-width: 70rem; color: #17202a; background: #fff; }}
      h1 {{ margin-bottom: .25rem; color: #1e3a5f; }}
      .subtitle {{ margin-top: 0; color: #536471; }}
      table {{ border-collapse: collapse; width: 100%; margin-top: 1.5rem; }}
      th, td {{ border: 1px solid #b7c2cc; padding: .6rem .7rem; text-align: left; }}
      th {{ background: #e9f0f5; color: #1e3a5f; }}
      .number, .tick {{ text-align: center; width: 4rem; }}
      code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 1rem; letter-spacing: .08em; }}
      .empty {{ text-align: center; color: #536471; padding: 2rem; }}
      @media print {{
        body {{ margin: 0; max-width: none; font-size: 10pt; }}
        h1 {{ font-size: 18pt; }}
        .subtitle {{ font-size: 10pt; }}
        table {{ margin-top: 1rem; }}
        tr {{ break-inside: avoid; }}
        th {{ background: #e9f0f5 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      }}
    </style>
  </head>
  <body>
    <h1>{title}</h1>
    <p class="subtitle">Print this sheet and tick the box when each attendee arrives.</p>
    <table>
      <thead>
        <tr><th class="number">No.</th><th>Name</th><th>Institution</th><th>Check-in code</th><th class="tick">Checked in</th></tr>
      </thead>
      <tbody>
{table_rows}
      </tbody>
    </table>
  </body>
</html>
'''
    return document.encode("utf-8")


class _CheckinParser(HTMLParser):
    """Small structural parser used by the report post-check."""

    def __init__(self) -> None:
        super().__init__()
        self.table_count = 0
        self.body_row_count = 0
        self._in_tbody = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.table_count += 1
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self.body_row_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_tbody = False


def verify_checkin_html(content: bytes, expected_entries: int) -> None:
    """Raise ``ValueError`` unless the generated sheet has the expected shape."""

    try:
        text = content.decode("utf-8")
        parser = _CheckinParser()
        parser.feed(text)
        parser.close()
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"check-in HTML is not valid UTF-8/HTML: {exc}") from exc
    if parser.table_count != 1:
        raise ValueError("check-in HTML must contain exactly one table")
    expected_rows = expected_entries if expected_entries else 1
    if parser.body_row_count != expected_rows:
        raise ValueError("check-in HTML row count does not match the roster")
    if "@media print" not in text or "Check-in code" not in text:
        raise ValueError("check-in HTML is missing print or code columns")


def build_checkin_file(
    event_id: str,
    event_name: str,
    roster: RosterResult,
) -> tuple[bytes, tuple[CheckinEntry, ...], str]:
    """Read the environment secret and build a checked, rendered sheet."""

    secret = secret_from_environment()
    entries = build_checkin_entries(event_id, roster, secret)
    content = render_checkin_html(entries, event_name=event_name)
    verify_checkin_html(content, len(entries))
    return content, entries, "Check-in sheet parses and matches the included roster."


__all__ = [
    "CheckinEntry",
    "build_checkin_entries",
    "build_checkin_file",
    "render_checkin_html",
    "verify_checkin_html",
]
