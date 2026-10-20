"""Deterministic RFC5545-ish calendar rendering and post-checking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable

from .schedule import ScheduleResult, Session


def _sessions(value: ScheduleResult | Iterable[Session]) -> tuple[Session, ...]:
    if isinstance(value, ScheduleResult):
        return tuple(value.sessions)
    return tuple(value)


def _event_sort_key(session: Session) -> tuple[Any, ...]:
    return (
        session.start_utc,
        session.end_utc,
        session.title,
        session.room,
        session.row_number,
    )


def event_uid(event_id: str, session: Session) -> str:
    """Return the stable SHA-256 UID for one event."""

    source = "|".join((str(event_id), session.title, session.start.isoformat()))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def escape_ical_text(value: str) -> str:
    """Escape an RFC5545 text value without changing its meaning."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _split_octets(value: str, limit: int) -> tuple[str, str]:
    used = 0
    split_at = 0
    for index, character in enumerate(value):
        size = len(character.encode("utf-8"))
        if used + size > limit:
            break
        used += size
        split_at = index + 1
    if split_at == 0:
        raise ValueError("cannot fold an individual character larger than the octet limit")
    return value[:split_at], value[split_at:]


def fold_ical_line(value: str) -> tuple[str, ...]:
    """Fold one logical line so every physical line is at most 75 octets."""

    if not value:
        return ("",)
    chunks: list[str] = []
    remaining = value
    first = True
    while remaining:
        chunk, remaining = _split_octets(remaining, 75 if first else 74)
        chunks.append(chunk if first else f" {chunk}")
        first = False
    return tuple(chunks)


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_ics(
    schedule: ScheduleResult | Iterable[Session],
    event_id: str | ScheduleResult | Iterable[Session],
    event_name: str = "Event schedule",
) -> bytes:
    """Render valid sessions as deterministic UTF-8 ICS bytes with CRLF."""

    # Accept the natural ``render_ics(event_id, schedule)`` order as well as
    # the renderer-first order used by the rest of eventkit's helpers.
    if isinstance(schedule, str) and not isinstance(event_id, str):
        schedule, event_id = event_id, schedule
    if not isinstance(event_id, str):
        raise TypeError("event_id must be a string")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//eventkit//Schedule//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{escape_ical_text(event_name)}",
    ]
    for session in sorted(_sessions(schedule), key=_event_sort_key):
        start = _utc_timestamp(session.start_utc)
        end = _utc_timestamp(session.end_utc)
        lines.extend(
            (
                "BEGIN:VEVENT",
                f"UID:{event_uid(event_id, session)}",
                f"DTSTAMP:{start}",
                f"DTSTART:{start}",
                f"DTEND:{end}",
                f"SUMMARY:{escape_ical_text(session.title)}",
                f"LOCATION:{escape_ical_text(session.room)}",
            )
        )
        if session.speaker:
            lines.append(f"DESCRIPTION:{escape_ical_text('Speaker: ' + session.speaker)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    physical_lines: list[str] = []
    for line in lines:
        physical_lines.extend(fold_ical_line(line))
    return ("\r\n".join(physical_lines) + "\r\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class ParsedIcs:
    """The unfolded event properties returned by :func:`parse_ics`."""

    events: tuple[dict[str, str], ...]

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> dict[str, str]:
        return self.events[index]


def _decode_ics(content: bytes | str) -> str:
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"ICS is not valid UTF-8: {exc}") from exc
    else:
        text = content
    if not isinstance(text, str):
        raise TypeError("ICS content must be bytes or str")
    if not text.endswith("\r\n"):
        raise ValueError("ICS must end with CRLF")
    body = text[:-2]
    if "\n" in body.replace("\r\n", "") or "\r" in body.replace("\r\n", ""):
        raise ValueError("ICS must use CRLF line endings")
    for line in body.split("\r\n"):
        if len(line.encode("utf-8")) > 75:
            raise ValueError("ICS physical lines must be no more than 75 octets")
    return body


def parse_ics(content: bytes | str) -> ParsedIcs:
    """Unfold and structurally parse generated ICS content.

    The parser intentionally handles the small RFC5545 subset emitted by this
    project.  It returns event property dictionaries with uppercase property
    names and escaped property values, making it useful for run post-checks.
    """

    body = _decode_ics(content)
    physical = body.split("\r\n")
    logical: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")):
            if not logical:
                raise ValueError("ICS continuation line has no preceding property")
            logical[-1] += line[1:]
        else:
            if not line:
                raise ValueError("ICS contains an empty line")
            logical.append(line)

    if not logical or logical[0] != "BEGIN:VCALENDAR" or logical[-1] != "END:VCALENDAR":
        raise ValueError("ICS must be enclosed by VCALENDAR")
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in logical[1:-1]:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("ICS contains nested VEVENT blocks")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise ValueError("ICS closes a VEVENT that was not opened")
            events.append(current)
            current = None
            continue
        if current is None:
            continue
        if ":" not in line:
            raise ValueError(f"ICS property is missing a colon: {line!r}")
        name, value = line.split(":", 1)
        property_name = name.split(";", 1)[0].upper()
        current[property_name] = value
    if current is not None:
        raise ValueError("ICS has an unclosed VEVENT")
    return ParsedIcs(tuple(events))


def verify_ics(
    content: bytes | str,
    expected_events: int | Iterable[Session] | ScheduleResult | None = None,
) -> ParsedIcs:
    """Raise ``ValueError`` unless ICS structure and event properties are valid."""

    parsed = parse_ics(content)
    if expected_events is not None:
        if isinstance(expected_events, int):
            expected_count = expected_events
        elif isinstance(expected_events, ScheduleResult):
            expected_count = len(expected_events.sessions)
        else:
            expected_count = len(tuple(expected_events))
        if len(parsed) != expected_count:
            raise ValueError("ICS event count does not match the schedule")
    required = {"UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "LOCATION"}
    for event in parsed:
        missing = sorted(required - event.keys())
        if missing:
            raise ValueError(f"ICS event is missing required properties: {', '.join(missing)}")
        for property_name in ("DTSTAMP", "DTSTART", "DTEND"):
            value = event[property_name]
            try:
                datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            except ValueError as exc:
                raise ValueError(f"ICS {property_name} is not a UTC timestamp") from exc
    return parsed


render_schedule_ics = render_ics
parse_ical = parse_ics
verify_schedule_ics = verify_ics


__all__ = [
    "ParsedIcs",
    "escape_ical_text",
    "event_uid",
    "fold_ical_line",
    "parse_ical",
    "parse_ics",
    "render_ics",
    "render_schedule_ics",
    "verify_ics",
    "verify_schedule_ics",
]
