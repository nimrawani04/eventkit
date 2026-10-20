"""Deterministic, self-contained HTML dashboard rendering for eventkit runs.

The dashboard deliberately has no relationship with the command line or the
output writer.  Callers can pass the small :class:`DashboardData` container,
plain mappings, or the model objects already produced by eventkit.  Keeping
the renderer independent makes it useful for a future report step without
making the existing pipeline depend on a browser-facing presentation layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from html.parser import HTMLParser
import re
import unicodedata
from typing import Any


@dataclass(frozen=True, slots=True)
class DashboardAttendee:
    """A display-ready attendee row accepted by :func:`render_dashboard`."""

    name: str
    email: str = ""
    status: str = "Included"
    issues: tuple[str, ...] = ()
    institution: str = ""


@dataclass(frozen=True, slots=True)
class DashboardSession:
    """A display-ready schedule row accepted by the dashboard."""

    title: str
    start: str = ""
    end: str = ""
    room: str = ""
    speaker: str = ""
    clashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DashboardCertificate:
    """A certificate row and its local SVG link."""

    code: str
    name: str = ""
    role: str = ""
    href: str = ""


@dataclass(frozen=True, slots=True)
class DashboardData:
    """Input data for the offline dashboard.

    The collection fields intentionally accept model objects and mappings in
    addition to the display dataclasses above.  This keeps the public helper
    convenient for callers that already have ``RosterRow``, ``Session``, or
    ``IssuedCertificate`` instances.  Explicit metric values win over values
    inferred from the supplied collections.
    """

    event_title: str = "eventkit run"
    organizer: str = ""
    status: str = "Ready"
    status_detail: str = ""
    event_name: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    registered: int | str | None = None
    included: int | str | None = None
    excluded: int | str | None = None
    warnings: int | str | None = None
    errors: int | str | None = None
    sessions: int | str | None = None
    certificates: int | str | None = None
    drafts: int | str | None = None
    risk_summary: str = ""
    unresolved: Iterable[Any] = ()
    attendees: Iterable[Any] = ()
    schedule: Any = ()
    certificate_rows: Any = ()
    certificates_data: Any = None
    deliverables: Any = ()


_METRIC_LABELS: tuple[tuple[str, str], ...] = (
    ("registered", "Registered"),
    ("included", "Included"),
    ("excluded", "Excluded"),
    ("warnings", "Warnings"),
    ("errors", "Errors"),
    ("sessions", "Sessions"),
    ("certificates", "Certificates"),
    ("drafts", "Drafts"),
)

_DELIVERABLES: tuple[tuple[str, str, str], ...] = (
    ("roster.csv", "Roster", "Clean registration roster"),
    ("issues.md", "Issues", "Registration findings"),
    ("checkin.html", "Check-in", "Printable arrival sheet"),
    ("schedule.ics", "Schedule", "Calendar schedule"),
    ("agenda.html", "Agenda", "Session agenda"),
    ("manifest.json", "Manifest", "Certificate verification manifest"),
    ("verify.html", "Verify", "Offline certificate checker"),
    ("risk.md", "Risk", "Risk and unresolved findings"),
    ("report.md", "Report", "Run report"),
    ("mailmerge.csv", "Mail merge", "Certificate mail-merge data"),
    ("emails/", "Emails", "Draft email messages"),
)

_DELIVERABLE_LOOKUP: dict[str, tuple[str, str, str]] = {}
for _path, _label, _description in _DELIVERABLES:
    _DELIVERABLE_LOOKUP[_path.casefold().rstrip("/")] = (
        _path,
        _label,
        _description,
    )
    _DELIVERABLE_LOOKUP[_label.casefold().rstrip("/")] = (
        _path,
        _label,
        _description,
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from a mapping or an object without invoking serialization."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _html(value: Any, default: str = "") -> str:
    return escape(_text(value, default), quote=True)


def _sequence(value: Any, *container_names: str) -> tuple[Any, ...]:
    """Turn result objects, mappings, and iterables into stable row tuples."""

    if value is None:
        return ()
    for name in container_names:
        nested = _field(value, name, None)
        if nested is not None and nested is not value:
            return _sequence(nested)
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _issue_code(issue: Any) -> str:
    if isinstance(issue, str):
        return issue
    return _text(_field(issue, "code", _field(issue, "id", _field(issue, "issue_id", ""))))


def _issue_values(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(
            item
            for item in (_issue_code(value),)
            if item
        )
    try:
        result = tuple(_issue_code(item) for item in value)
    except TypeError:
        result = (_issue_code(value),)
    return tuple(item for item in result if item)


def _attendee_rows(value: Any) -> tuple[DashboardAttendee, ...]:
    rows = _sequence(value, "rows", "entries", "roster")
    normalized: list[DashboardAttendee] = []
    for row in rows:
        attendee = _field(row, "attendee", row)
        status = _text(_field(row, "status", "Included"))
        status_key = status.casefold().replace("_", " ").strip()
        if status_key in {"ok", "included", "include", "registered", "clear"}:
            status = "Included"
        elif status_key in {"excluded", "exclude", "rejected"}:
            status = "Excluded"
        issues = _field(row, "issue_ids", None)
        if issues is None:
            issues = _field(row, "issues", ())
        normalized.append(
            DashboardAttendee(
                name=_text(_field(attendee, "name", "")),
                email=_text(_field(attendee, "email", "")),
                status=status,
                issues=_issue_values(issues),
                institution=_text(_field(attendee, "institution", "")),
            )
        )
    return tuple(normalized)


def _format_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)


def _session_rows(value: Any) -> tuple[DashboardSession, ...]:
    rows = _sequence(value, "sessions", "rows", "valid_sessions")
    normalized: list[DashboardSession] = []
    for row in rows:
        start = _field(row, "start", _field(row, "start_local", ""))
        end = _field(row, "end", _field(row, "end_local", ""))
        clashes = _field(row, "clashes", None)
        if clashes is None:
            clashes = _field(row, "issue_ids", _field(row, "issues", ()))
        clash_values = _issue_values(clashes)
        if not clash_values and _field(row, "has_clash", False):
            clash_values = ("Clash",)
        normalized.append(
            DashboardSession(
                title=_text(_field(row, "title", "")),
                start=_format_datetime(start),
                end=_format_datetime(end),
                room=_text(_field(row, "room", "")),
                speaker=_text(_field(row, "speaker", "")),
                clashes=clash_values,
            )
        )
    return tuple(normalized)


def _slug_fragment(value: str, *, lowercase: bool = False) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    fragment = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    return fragment.lower() if lowercase else fragment


def _certificate_href(name: str, code: str, supplied: Any) -> str:
    if supplied:
        return _local_href(_text(supplied), "#certificate")
    name_slug = _slug_fragment(name, lowercase=True) or "attendee"
    code_slug = _slug_fragment(code) or "certificate"
    return f"certificates/{name_slug}-{code_slug}.svg"


def _certificate_rows(value: Any) -> tuple[DashboardCertificate, ...]:
    # CertificateBundle exposes its issued recipients through ``issued``.
    rows = _sequence(value, "issued", "certificates", "rows")
    normalized: list[DashboardCertificate] = []
    for row in rows:
        code = _text(_field(row, "code", ""))
        name = _text(_field(row, "name", ""))
        role = _text(_field(row, "role", ""))
        supplied = _field(row, "href", _field(row, "svg", _field(row, "path", "")))
        normalized.append(
            DashboardCertificate(
                code=code,
                name=name,
                role=role,
                href=_certificate_href(name, code, supplied),
            )
        )
    return tuple(normalized)


def _local_href(value: str, fallback: str = "#") -> str:
    """Keep generated links local even when a caller supplies a bad URL."""

    candidate = value.strip()
    if (
        not candidate
        or candidate.startswith(("/", "\\"))
        or "//" in candidate
        or ":" in candidate.split("/", 1)[0]
        or any(ord(character) < 0x20 for character in candidate)
    ):
        return fallback
    return candidate


def _known_deliverable(path: str) -> tuple[str, str, str]:
    normalized = path.casefold().rstrip("/")
    return _DELIVERABLE_LOOKUP.get(
        normalized,
        (path, path.rsplit("/", 1)[-1] or path, "Generated event deliverable"),
    )


def _deliverable_rows(value: Any) -> tuple[tuple[str, str, str], ...]:
    """Normalize common ``{path: present}`` and ``[path]`` representations."""

    if value is None:
        return ()
    records: list[tuple[str, str, str]] = []
    if isinstance(value, Mapping):
        iterable: Iterable[tuple[Any, Any]] = value.items()
        for raw_key, raw_value in iterable:
            key = _text(raw_key)
            path = key
            label_override = ""
            if isinstance(raw_value, Mapping):
                path = _text(raw_value.get("path", raw_value.get("href", key)))
                label_override = _text(raw_value.get("label", raw_value.get("name", "")))
            elif isinstance(raw_value, str):
                key_lookup = _DELIVERABLE_LOOKUP.get(key.casefold().rstrip("/"))
                if key_lookup and raw_value.casefold().rstrip("/") not in _DELIVERABLE_LOOKUP:
                    path = raw_value
                elif "." in raw_value or "/" in raw_value:
                    path = raw_value
                else:
                    label_override = raw_value
            elif not raw_value:
                continue
            known_path, label, description = _known_deliverable(path)
            if label_override:
                label = label_override
            records.append((_local_href(known_path), label, description))
    else:
        values = _sequence(value)
        for item in values:
            if isinstance(item, Mapping):
                path = _text(item.get("path", item.get("href", "")))
                if not path or item.get("present", True) is False:
                    continue
                known_path, label, description = _known_deliverable(path)
                records.append(
                    (_local_href(known_path), _text(item.get("label", label)), description)
                )
            else:
                path = _text(item)
                if path:
                    known_path, label, description = _known_deliverable(path)
                    records.append((_local_href(known_path), label, description))
    # Sort by the stable product order first, then by path for custom files.
    order = {path: index for index, (path, _, _) in enumerate(_DELIVERABLES)}
    records.sort(key=lambda item: (order.get(item[0], len(order)), item[0], item[1]))
    deduplicated: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in records:
        if item[0] not in seen:
            seen.add(item[0])
            deduplicated.append(item)
    return tuple(deduplicated)


def _metric_value(
    source: Any,
    metrics: Mapping[str, Any],
    key: str,
    fallback: int,
) -> Any:
    direct = _field(source, key, None)
    scalar = isinstance(direct, (int, float, str, bool))
    if direct is not None and scalar:
        return direct
    aliases = {
        "registered": ("registered", "registrations", "rows_read"),
        "included": ("included", "ok", "rows_included"),
        "excluded": ("excluded", "rows_excluded"),
        "warnings": ("warnings", "warning_count"),
        "errors": ("errors", "error_count"),
        "sessions": ("sessions", "session_count"),
        "certificates": ("certificates", "certificate_count"),
        "drafts": ("drafts", "draft_count", "emails"),
    }[key]
    for alias in aliases:
        alias_value = _field(source, alias, None)
        if isinstance(alias_value, (int, float, str, bool)):
            return alias_value
    folded = {_text(name).casefold(): value for name, value in metrics.items()}
    for alias in aliases:
        if alias in folded:
            return folded[alias]
    return fallback


def _unresolved_items(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    values = (value,) if isinstance(value, str) else _sequence(value)
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("message", item.get("detail", item.get("text", item)))
        text = _text(item)
        if text:
            result.append(text)
    return tuple(result)


def _status_class(status: str) -> str:
    lowered = status.casefold()
    if any(word in lowered for word in ("error", "fail", "blocked")):
        return "status status-danger"
    if any(word in lowered for word in ("warning", "review", "partial")):
        return "status status-warning"
    return "status status-good"


def _render_attendees(rows: tuple[DashboardAttendee, ...]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="empty-cell">No attendee records in this run.</td></tr>'
    rendered: list[str] = []
    for row in rows:
        issues = (
            " ".join(
                f'<span class="badge badge-warning">{_html(issue)}</span>'
                for issue in row.issues
            )
            if row.issues
            else '<span class="muted">None</span>'
        )
        rendered.append(
            "        <tr>\n"
            f"          <td><strong>{_html(row.name, '—')}</strong>"
            f"<span class=\"subtle\">{_html(row.institution)}</span></td>\n"
            f"          <td>{_html(row.email, '—')}</td>\n"
            f'          <td><span class="badge {"badge-good" if row.status == "Included" else "badge-muted"}">{_html(row.status)}</span></td>\n'
            f"          <td class=\"issues-cell\">{issues}</td>\n"
            "        </tr>"
        )
    return "\n".join(rendered)


def _render_schedule(rows: tuple[DashboardSession, ...]) -> str:
    if not rows:
        return '<tr><td colspan="5" class="empty-cell">No schedule sessions in this run.</td></tr>'
    rendered: list[str] = []
    for row in rows:
        clash_badges = (
            " ".join(
                f'<span class="badge badge-danger">Clash {_html(clash)}</span>'
                for clash in row.clashes
            )
            if row.clashes
            else '<span class="badge badge-good">Clear</span>'
        )
        time = " — ".join(part for part in (row.start, row.end) if part) or "—"
        rendered.append(
            "        <tr>\n"
            f"          <td class=\"time-cell\">{_html(time)}</td>\n"
            f"          <td><strong>{_html(row.title, 'Untitled session')}</strong></td>\n"
            f"          <td>{_html(row.room, '—')}</td>\n"
            f"          <td>{_html(row.speaker, '—')}</td>\n"
            f"          <td class=\"issues-cell\">{clash_badges}</td>\n"
            "        </tr>"
        )
    return "\n".join(rendered)


def _render_certificates(rows: tuple[DashboardCertificate, ...]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="empty-cell">No certificates issued in this run.</td></tr>'
    rendered: list[str] = []
    for row in rows:
        rendered.append(
            "        <tr>\n"
            f"          <td><code>{_html(row.code, '—')}</code></td>\n"
            f"          <td>{_html(row.name, 'Private recipient')}</td>\n"
            f"          <td>{_html(row.role, '—')}</td>\n"
            f'          <td><a class="table-link" href="{_html(_local_href(row.href, '#certificate'))}">View SVG <span aria-hidden="true">↗</span></a></td>\n'
            "        </tr>"
        )
    return "\n".join(rendered)


def _render_deliverables(rows: tuple[tuple[str, str, str], ...]) -> str:
    if not rows:
        return '<p class="empty-state">No deliverable files were recorded for this run.</p>'
    return "\n".join(
        f'          <a class="deliverable-card" href="{_html(path)}">'
        f'<span class="file-mark" aria-hidden="true">✦</span>'
        f'<span><strong>{_html(label)}</strong><small>{_html(description)}</small>'
        f'<code>{_html(path)}</code></span><span class="card-arrow" aria-hidden="true">↗</span></a>'
        for path, label, description in rows
    )


def _render_metrics(values: Mapping[str, Any]) -> str:
    cards: list[str] = []
    for key, label in _METRIC_LABELS:
        tone = "metric-risk" if key in {"warnings", "errors", "excluded"} else ""
        cards.append(
            f'          <article class="metric-card {tone}" data-metric="{key}">'
            f'<span class="metric-label">{_html(label)}</span>'
            f'<strong class="metric-value">{_html(values[key])}</strong></article>'
        )
    return "\n".join(cards)


def render_dashboard(
    data: DashboardData | Mapping[str, Any] | None = None,
    *,
    event_title: str | None = None,
    event_name: str | None = None,
    organizer: str | None = None,
    status: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    risk_summary: str | None = None,
    unresolved: Iterable[Any] | None = None,
    attendees: Iterable[Any] | None = None,
    schedule: Any = None,
    certificates: Any = None,
    deliverables: Any = None,
) -> bytes:
    """Render an offline event dashboard as deterministic UTF-8 bytes.

    ``data`` may be :class:`DashboardData`, a mapping with matching keys, or
    omitted when the explicit keyword arguments are sufficient.  All links
    are local relative paths and all caller-provided text is HTML escaped.
    """

    source: Any = data if data is not None else DashboardData()
    title = event_title if event_title is not None else _field(source, "event_name", None)
    if title is None:
        title = _field(source, "event_title", _field(source, "title", "eventkit run"))
    if event_name is not None:
        title = event_name
    organizer_value = organizer if organizer is not None else _field(source, "organizer", "")
    status_value = status if status is not None else _field(source, "status", _field(source, "run_status", "Ready"))
    status_detail = _field(source, "status_detail", "")
    metric_values = metrics if metrics is not None else _field(source, "metrics", {})
    metric_values = metric_values if isinstance(metric_values, Mapping) else {}

    attendee_value = attendees if attendees is not None else _field(source, "attendees", ())
    attendee_rows = _attendee_rows(attendee_value)
    schedule_value = schedule if schedule is not None else _field(source, "schedule", ())
    session_field = _field(source, "sessions", None)
    if schedule is None and not isinstance(session_field, (int, float, str, bool, type(None))):
        schedule_value = session_field
    schedule_rows = _session_rows(schedule_value)
    certificate_value = certificates if certificates is not None else _field(source, "certificate_rows", None)
    if certificate_value is None:
        certificate_value = _field(source, "certificates_data", ())
    certificate_field = _field(source, "certificates", None)
    if certificates is None and not isinstance(
        certificate_field, (int, float, str, bool, type(None))
    ):
        certificate_value = certificate_field
    certificate_rows = _certificate_rows(certificate_value)
    unresolved_value = unresolved if unresolved is not None else _field(source, "unresolved", ())
    unresolved_rows = _unresolved_items(unresolved_value)
    deliverable_value = deliverables if deliverables is not None else _field(source, "deliverables", ())
    deliverable_rows = _deliverable_rows(deliverable_value)

    included_fallback = sum(row.status == "Included" for row in attendee_rows)
    excluded_fallback = sum(row.status == "Excluded" for row in attendee_rows)
    fallbacks = {
        "registered": len(attendee_rows),
        "included": included_fallback,
        "excluded": excluded_fallback,
        "warnings": 0,
        "errors": 0,
        "sessions": len(schedule_rows),
        "certificates": len(certificate_rows),
        "drafts": 0,
    }
    values = {
        key: _metric_value(source, metric_values, key, fallbacks[key])
        for key, _ in _METRIC_LABELS
    }
    risk = risk_summary if risk_summary is not None else _field(source, "risk_summary", "")
    risk_text = _text(risk)
    if not risk_text:
        risk_text = (
            "Human review is needed for the unresolved findings below."
            if unresolved_rows
            else "No unresolved findings were recorded for this run."
        )
    unresolved_markup = (
        "\n".join(f"            <li>{_html(item)}</li>" for item in unresolved_rows)
        if unresolved_rows
        else '            <li class="clear-item">No unresolved items recorded.</li>'
    )
    status_text = _text(status_value, "Ready")
    status_class = _status_class(status_text)
    title_text = _text(title, "eventkit run")
    organizer_text = _text(organizer_value)
    detail_markup = (
        f'<p class="run-detail">{_html(status_detail)}</p>' if status_detail else ""
    )
    organizer_markup = (
        f'<p class="organizer">Prepared by {_html(organizer_text)}</p>'
        if organizer_text
        else ""
    )

    document = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#101a2b">
  <title>{_html(title_text)} — eventkit dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --navy: #101a2b;
      --navy-soft: #19263b;
      --ink: #172235;
      --muted: #596474;
      --line: #d9d6cf;
      --ivory: #f6f3ec;
      --surface: #fffdf8;
      --champagne: #b08a4b;
      --champagne-soft: #efe4cf;
      --danger: #9a302b;
      --danger-soft: #f7e5e0;
      --warning: #765214;
      --warning-soft: #f7eedc;
      --good: #256047;
      --good-soft: #e5f0e9;
      --shadow: 0 18px 50px rgba(16, 26, 43, .10);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--ivory);
      color: var(--ink);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; min-width: 320px; background: var(--ivory); color: var(--ink); line-height: 1.55; }}
    a {{ color: inherit; }}
    button, a {{ -webkit-tap-highlight-color: transparent; }}
    .shell {{ min-height: 100vh; }}
    .hero {{ background: var(--navy); color: #f9f5ec; padding: 3.2rem max(1.25rem, calc((100vw - 76rem) / 2)); }}
    .hero-inner {{ max-width: 76rem; margin: 0 auto; }}
    .eyebrow {{ color: #e4c996; font-size: .72rem; font-weight: 750; letter-spacing: .18em; text-transform: uppercase; }}
    h1, h2, h3 {{ font-family: Georgia, "Times New Roman", serif; font-weight: 400; letter-spacing: -.025em; }}
    h1 {{ font-size: clamp(2.45rem, 6vw, 5.2rem); line-height: .98; margin: .75rem 0 1rem; max-width: 48rem; }}
    .organizer {{ color: #c8d0dc; margin: 0; font-size: 1rem; }}
    .hero-meta {{ align-items: center; display: flex; flex-wrap: wrap; gap: .75rem 1.25rem; margin-top: 2.25rem; }}
    .status {{ border: 1px solid currentColor; border-radius: 999px; display: inline-flex; align-items: center; gap: .45rem; font-size: .74rem; font-weight: 750; letter-spacing: .08em; padding: .35rem .7rem; text-transform: uppercase; }}
    .status::before {{ background: currentColor; border-radius: 50%; content: ""; height: .42rem; width: .42rem; }}
    .status-good {{ color: #bfe2cb; }}
    .status-warning {{ color: #f1d18e; }}
    .status-danger {{ color: #f0aaa2; }}
    .run-detail {{ color: #bac5d3; margin: 0; }}
    main {{ margin: 0 auto; max-width: 76rem; padding: 0 max(1.25rem, calc((100vw - 76rem) / 2)) 5rem; }}
    .tabs {{ background: var(--surface); border-bottom: 1px solid var(--line); display: flex; gap: .2rem; margin: 0 -1.25rem; overflow-x: auto; padding: .7rem 1.25rem 0; position: sticky; top: 0; z-index: 5; }}
    .tab {{ background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: .84rem; font-weight: 700; padding: .8rem .95rem 1rem; white-space: nowrap; }}
    .tab:hover, .tab:focus-visible {{ color: var(--ink); }}
    .tab:focus-visible {{ outline: 3px solid var(--champagne-soft); outline-offset: -3px; }}
    .tab.is-active {{ border-bottom-color: var(--champagne); color: var(--ink); }}
    .panel {{ padding-top: 3rem; }}
    .section-heading {{ align-items: end; display: flex; justify-content: space-between; gap: 1rem; margin-bottom: 1.25rem; }}
    h2 {{ font-size: clamp(1.8rem, 3vw, 2.7rem); margin: 0; }}
    h3 {{ font-size: 1.3rem; margin: 0; }}
    .section-kicker {{ color: var(--champagne); font-size: .7rem; font-weight: 800; letter-spacing: .16em; margin: 0 0 .35rem; text-transform: uppercase; }}
    .section-intro {{ color: var(--muted); margin: .2rem 0 0; max-width: 38rem; }}
    .metrics {{ display: grid; gap: .8rem; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 0 2.8rem; }}
    .metric-card {{ background: var(--surface); border: 1px solid var(--line); border-top: 3px solid var(--champagne); min-height: 8.4rem; padding: 1.15rem 1.2rem; }}
    .metric-card.metric-risk {{ border-top-color: #c48b53; }}
    .metric-label {{ color: var(--muted); display: block; font-size: .74rem; font-weight: 750; letter-spacing: .1em; text-transform: uppercase; }}
    .metric-value {{ display: block; font-family: Georgia, "Times New Roman", serif; font-size: 2.5rem; font-weight: 400; line-height: 1; margin-top: 1.35rem; }}
    .summary-grid {{ display: grid; gap: 1rem; grid-template-columns: 1.35fr .65fr; margin-bottom: 2rem; }}
    .summary-card {{ background: var(--surface); border: 1px solid var(--line); padding: 1.45rem; }}
    .summary-card.risk-card {{ border-left: 4px solid var(--champagne); }}
    .summary-card.unresolved-card {{ border-left: 4px solid var(--danger); }}
    .summary-card p {{ color: var(--muted); margin: .55rem 0 0; }}
    .summary-card ul {{ color: var(--muted); margin: .75rem 0 0; padding-left: 1.15rem; }}
    .summary-card li + li {{ margin-top: .45rem; }}
    .clear-item {{ color: var(--good); list-style: "✓  "; }}
    .table-wrap {{ background: var(--surface); border: 1px solid var(--line); overflow-x: auto; }}
    table {{ border-collapse: collapse; min-width: 46rem; width: 100%; }}
    th, td {{ border-bottom: 1px solid #e8e4dc; padding: .95rem 1rem; text-align: left; vertical-align: top; }}
    th {{ background: #f0ede5; color: var(--muted); font-size: .7rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    tbody tr:hover {{ background: #fcfaf4; }}
    td strong {{ display: block; font-weight: 750; }}
    .subtle {{ color: var(--muted); display: block; font-size: .82rem; margin-top: .18rem; }}
    .time-cell {{ color: var(--ink); font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .issues-cell {{ min-width: 9rem; }}
    .row-number {{ color: var(--muted); font-size: .78rem; text-align: right; }}
    .badge {{ border-radius: 999px; display: inline-block; font-size: .7rem; font-weight: 800; letter-spacing: .03em; margin: .1rem .2rem .1rem 0; padding: .22rem .5rem; white-space: nowrap; }}
    .badge-good {{ background: var(--good-soft); color: var(--good); }}
    .badge-warning {{ background: var(--warning-soft); color: var(--warning); }}
    .badge-danger {{ background: var(--danger-soft); color: var(--danger); }}
    .badge-muted {{ background: #e9e7e1; color: var(--muted); }}
    .muted, .empty-cell {{ color: var(--muted); }}
    .empty-cell {{ padding: 3rem 1rem; text-align: center; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size: .85em; }}
    .table-link {{ color: var(--good); font-size: .84rem; font-weight: 750; text-decoration-color: #b6d3c3; text-underline-offset: .2em; }}
    .table-link:hover, .table-link:focus-visible {{ color: var(--ink); }}
    .deliverables-grid {{ display: grid; gap: .8rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .deliverable-card {{ align-items: center; background: var(--surface); border: 1px solid var(--line); display: flex; gap: .75rem; min-height: 7.25rem; padding: 1.1rem; text-decoration: none; transition: border-color .15s ease, transform .15s ease; }}
    .deliverable-card:hover, .deliverable-card:focus-visible {{ border-color: var(--champagne); transform: translateY(-2px); }}
    .deliverable-card:focus-visible {{ outline: 3px solid var(--champagne-soft); outline-offset: 2px; }}
    .file-mark {{ align-items: center; background: var(--champagne-soft); color: var(--warning); display: flex; flex: 0 0 2.2rem; font-size: 1.1rem; height: 2.2rem; justify-content: center; }}
    .deliverable-card strong, .deliverable-card small, .deliverable-card code {{ display: block; }}
    .deliverable-card strong {{ font-size: .92rem; }}
    .deliverable-card small {{ color: var(--muted); font-size: .77rem; margin: .16rem 0 .4rem; }}
    .deliverable-card code {{ color: var(--champagne); font-size: .7rem; overflow-wrap: anywhere; }}
    .card-arrow {{ color: var(--champagne); font-size: 1.1rem; margin-left: auto; align-self: flex-start; }}
    .empty-state {{ background: var(--surface); border: 1px dashed var(--line); color: var(--muted); padding: 2.25rem; text-align: center; }}
    footer {{ border-top: 1px solid var(--line); color: var(--muted); font-size: .76rem; margin-top: 4rem; padding-top: 1.2rem; }}
    [hidden] {{ display: none !important; }}
    @media (max-width: 46rem) {{
      .hero {{ padding-bottom: 2.5rem; padding-top: 2.5rem; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .summary-grid, .deliverables-grid {{ grid-template-columns: 1fr; }}
      .panel {{ padding-top: 2.25rem; }}
      .section-heading {{ align-items: start; flex-direction: column; }}
    }}
    @media print {{
      :root {{ background: #fff; }}
      body {{ background: #fff; color: #000; font-size: 10pt; }}
      .hero {{ background: #fff; border-bottom: 2px solid #000; color: #000; padding: 1rem 0; }}
      .eyebrow, .organizer, .run-detail, .section-intro, footer, .tabs {{ color: #000; }}
      .tabs {{ display: none; }}
      main {{ padding: 0; }}
      .panel, .tab-panel[hidden] {{ display: block !important; padding-top: 1.5rem; }}
      .metrics {{ grid-template-columns: repeat(4, 1fr); }}
      .metric-card, .summary-card, .table-wrap, .deliverable-card {{ break-inside: avoid; box-shadow: none; }}
      .deliverables-grid {{ grid-template-columns: repeat(3, 1fr); }}
      .deliverable-card {{ color: #000; min-height: auto; }}
      a {{ text-decoration: none; }}
      a[href]::after {{ content: " (" attr(href) ")"; color: #444; font-size: .8em; }}
      .card-arrow, .file-mark {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="shell" id="dashboard">
    <header class="hero">
      <div class="hero-inner">
        <div class="eyebrow">eventkit / offline run dashboard</div>
        <h1>{_html(title_text)}</h1>
        {organizer_markup}
        <div class="hero-meta">
          <span class="{status_class}" role="status">{_html(status_text)}</span>
          {detail_markup}
        </div>
      </div>
    </header>
    <main>
      <nav class="tabs" aria-label="Dashboard sections" role="tablist">
        <button id="overview-tab" class="tab is-active" type="button" role="tab" aria-selected="true" aria-controls="overview" data-tab="overview">Overview</button>
        <button id="attendees-tab" class="tab" type="button" role="tab" aria-selected="false" aria-controls="attendees" data-tab="attendees">Attendees</button>
        <button id="schedule-tab" class="tab" type="button" role="tab" aria-selected="false" aria-controls="schedule" data-tab="schedule">Schedule</button>
        <button id="certificates-tab" class="tab" type="button" role="tab" aria-selected="false" aria-controls="certificates" data-tab="certificates">Certificates</button>
        <button id="deliverables-tab" class="tab" type="button" role="tab" aria-selected="false" aria-controls="deliverables" data-tab="deliverables">Deliverables</button>
      </nav>
      <section class="tab-panel panel" id="overview" role="tabpanel" aria-labelledby="overview-tab">
        <div class="section-heading">
          <div><p class="section-kicker">At a glance</p><h2>Run overview</h2><p class="section-intro">A quiet, local view of what eventkit prepared and what deserves a second look.</p></div>
        </div>
        <div class="metrics">
{_render_metrics(values)}
        </div>
        <div class="summary-grid">
          <article class="summary-card risk-card"><p class="section-kicker">Risk posture</p><h3>What needs attention</h3><p>{_html(risk_text)}</p></article>
          <article class="summary-card unresolved-card"><p class="section-kicker">Human review</p><h3>{_html(len(unresolved_rows))} unresolved</h3><ul>
{unresolved_markup}
          </ul></article>
        </div>
      </section>
      <section class="tab-panel panel" id="attendees" role="tabpanel" aria-labelledby="attendees-tab" hidden>
        <div class="section-heading"><div><p class="section-kicker">Registration</p><h2>Attendees</h2><p class="section-intro">Every registration is represented with its final status and issue codes.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Status</th><th>Issues</th></tr></thead><tbody>
{_render_attendees(attendee_rows)}
        </tbody></table></div>
      </section>
      <section class="tab-panel panel" id="schedule" role="tabpanel" aria-labelledby="schedule-tab" hidden>
        <div class="section-heading"><div><p class="section-kicker">Programme</p><h2>Schedule</h2><p class="section-intro">Sessions are shown in local event time; room and speaker clashes are called out.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Time</th><th>Session</th><th>Room</th><th>Speaker</th><th>Checks</th></tr></thead><tbody>
{_render_schedule(schedule_rows)}
        </tbody></table></div>
      </section>
      <section class="tab-panel panel" id="certificates" role="tabpanel" aria-labelledby="certificates-tab" hidden>
        <div class="section-heading"><div><p class="section-kicker">Recognition</p><h2>Certificates</h2><p class="section-intro">Issued codes stay legible here, with each local SVG one click away.</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>Role</th><th>Certificate</th></tr></thead><tbody>
{_render_certificates(certificate_rows)}
        </tbody></table></div>
      </section>
      <section class="tab-panel panel" id="deliverables" role="tabpanel" aria-labelledby="deliverables-tab" hidden>
        <div class="section-heading"><div><p class="section-kicker">The event kit</p><h2>Deliverables</h2><p class="section-intro">Open any generated file directly from this self-contained, network-free dashboard.</p></div></div>
        <div class="deliverables-grid">
{_render_deliverables(deliverable_rows)}
        </div>
      </section>
      <footer>Generated by eventkit · This dashboard is self-contained and works offline.</footer>
    </main>
  </div>
  <script>
    (() => {{
      const tabs = Array.from(document.querySelectorAll('[data-tab]'));
      const panels = Array.from(document.querySelectorAll('.tab-panel'));
      const activate = (name) => {{
        tabs.forEach((tab) => {{
          const selected = tab.dataset.tab === name;
          tab.classList.toggle('is-active', selected);
          tab.setAttribute('aria-selected', String(selected));
        }});
        panels.forEach((panel) => {{ panel.hidden = panel.id !== name; }});
      }};
      tabs.forEach((tab) => tab.addEventListener('click', () => activate(tab.dataset.tab)));
    }})();
  </script>
</body>
</html>
'''
    return document.encode("utf-8")


class _DashboardParser(HTMLParser):
    """Minimal structural parser used by the dashboard post-check."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.external_urls: list[str] = []
        self.script_sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        for name, value in attrs:
            if value is None:
                continue
            if name.casefold() == "src":
                self.script_sources.append(value)
            if re.search(r"(?:https?:)?//", value, flags=re.IGNORECASE):
                self.external_urls.append(value)


def verify_dashboard_html(content: bytes | str) -> None:
    """Raise ``ValueError`` unless *content* is a complete offline dashboard."""

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"dashboard is not valid UTF-8: {exc}") from exc
    elif isinstance(content, str):
        text = content
    else:
        raise ValueError("dashboard must be UTF-8 bytes or text")
    parser = _DashboardParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"dashboard is not valid HTML: {exc}") from exc
    required_ids = ("overview", "attendees", "schedule", "certificates", "deliverables")
    missing = [f'id="{identifier}"' for identifier in required_ids if f'id="{identifier}"' not in text]
    if missing:
        raise ValueError(f"dashboard is missing sections: {', '.join(missing)}")
    if parser.script_sources or parser.external_urls:
        raise ValueError("dashboard must not contain external URLs or script sources")
    if "@media print" not in text or "@media (max-width" not in text:
        raise ValueError("dashboard is missing responsive or print styles")
    if "data-tab" not in text or "addEventListener" not in text:
        raise ValueError("dashboard is missing inline tab navigation")
    if text.count("<style>") != 1 or text.count("<script>") != 1:
        raise ValueError("dashboard must contain exactly one inline style and script")
    if "Registered" not in text or "Certificates" not in text or "Drafts" not in text:
        raise ValueError("dashboard is missing required metric cards")


__all__ = [
    "DashboardAttendee",
    "DashboardCertificate",
    "DashboardData",
    "DashboardSession",
    "render_dashboard",
    "verify_dashboard_html",
]
