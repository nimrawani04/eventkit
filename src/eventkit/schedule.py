"""Schedule parsing and deterministic clash detection.

The schedule slice deliberately keeps its input and output model separate from
the roster model.  A session's datetimes are timezone-aware local datetimes;
the calendar renderer can therefore convert them to UTC without having to
re-parse the source CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
import re
from typing import Any, Iterable

from .csvio import CsvTable, read_csv, read_csv_text
from .model import IssueLevel


_LOCAL_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}\Z")
_UTC_OFFSET_RE = re.compile(r"(?P<sign>[+-])(?P<hour>\d{2}):(?P<minute>\d{2})\Z")
_CLASH_CODES = {"SC02", "SC03"}
_CODE_ORDER = {"SC01": 0, "SC02": 1, "SC03": 2}


class ScheduleInputError(ValueError):
    """An expected schedule input or configuration error."""


@dataclass(frozen=True, slots=True)
class Session:
    """One valid schedule row, retaining its source row number.

    ``start`` and ``end`` are local wall-clock values carrying the fixed
    offset supplied to :func:`parse_schedule`.  They remain in that local
    timezone for agenda rendering; ``start_utc`` and ``end_utc`` are provided
    for calendar writers.
    """

    row_number: int
    start: datetime
    end: datetime
    title: str
    room: str
    speaker: str = ""
    issue_ids: tuple[str, ...] = ()

    @property
    def line_number(self) -> int:
        """Alias for the physical CSV row number."""

        return self.row_number

    @property
    def start_local(self) -> datetime:
        """Return the local start datetime as parsed from the CSV."""

        return self.start

    @property
    def end_local(self) -> datetime:
        """Return the local end datetime as parsed from the CSV."""

        return self.end

    @property
    def start_utc(self) -> datetime:
        """Return the start converted to UTC."""

        return _as_aware(self.start).astimezone(timezone.utc)

    @property
    def end_utc(self) -> datetime:
        """Return the end converted to UTC."""

        return _as_aware(self.end).astimezone(timezone.utc)

    @property
    def has_clash(self) -> bool:
        """Whether this session has a room or speaker clash."""

        return bool(set(self.issue_ids) & _CLASH_CODES)


@dataclass(frozen=True, slots=True)
class ScheduleIssue:
    """One schedule validation or clash finding."""

    code: str
    level: IssueLevel
    row_number: int
    message: str
    suggestion: str = ""
    related_row_number: int | None = None
    field: str | None = None

    @property
    def issue_id(self) -> str:
        """Alias matching the roster issue terminology."""

        return self.code

    @property
    def line_number(self) -> int:
        """Alias for callers that use CSV line terminology."""

        return self.row_number

    @property
    def row(self) -> int:
        """Short alias useful in reports."""

        return self.row_number

    def as_dict(self) -> dict[str, Any]:
        """Return a stable report-friendly representation."""

        result: dict[str, Any] = {
            "id": self.code,
            "level": self.level.value,
            "row": self.row_number,
            "message": self.message,
            "suggestion": self.suggestion,
        }
        if self.related_row_number is not None:
            result["related_row"] = self.related_row_number
        if self.field is not None:
            result["field"] = self.field
        return result


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """Valid sessions and all issues found while reading a schedule."""

    sessions: tuple[Session, ...]
    issues: tuple[ScheduleIssue, ...]
    excluded_row_numbers: tuple[int, ...] = ()
    extra_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sessions", tuple(self.sessions))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "excluded_row_numbers", tuple(self.excluded_row_numbers))
        object.__setattr__(self, "extra_columns", tuple(self.extra_columns))

    @property
    def rows(self) -> tuple[Session, ...]:
        """Alias for callers using the input-row vocabulary."""

        return self.sessions

    @property
    def valid_sessions(self) -> tuple[Session, ...]:
        """Sessions eligible for calendar and agenda output."""

        return self.sessions

    @property
    def calendar_sessions(self) -> tuple[Session, ...]:
        """Explicit alias for sessions included in generated calendars."""

        return self.sessions

    @property
    def errors(self) -> tuple[ScheduleIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level is IssueLevel.ERROR)

    @property
    def warnings(self) -> tuple[ScheduleIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level is IssueLevel.WARNING)

    def issues_by_code(self, code: str) -> tuple[ScheduleIssue, ...]:
        """Return findings for one issue code in source order."""

        return tuple(issue for issue in self.issues if issue.code == code)


def _as_aware(value: datetime) -> datetime:
    """Make a datetime safe for conversion while retaining valid aware values."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_utc_offset(value: str | tzinfo | Any) -> timezone:
    """Validate a ``+HH:MM``/``-HH:MM`` offset and return its timezone.

    An ``EventConfig``-like object is accepted as a convenience, so callers
    can pass their existing config object without changing the config module.
    """

    if not isinstance(value, str):
        configured = getattr(value, "utc_offset", None)
        if configured is None:
            raise ScheduleInputError("utc_offset must be formatted as +HH:MM or -HH:MM")
        value = configured
    match = _UTC_OFFSET_RE.fullmatch(value.strip())
    if match is None:
        raise ScheduleInputError("utc_offset must be formatted as +HH:MM or -HH:MM")
    hours = int(match.group("hour"))
    minutes = int(match.group("minute"))
    if hours > 23 or minutes > 59:
        raise ScheduleInputError("utc_offset must be a valid +HH:MM or -HH:MM offset")
    delta = timedelta(hours=hours, minutes=minutes)
    if match.group("sign") == "-":
        delta = -delta
    return timezone(delta)


def _parse_local_datetime(value: str, offset: timezone) -> datetime | None:
    value = value.strip()
    if not _LOCAL_TIME_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=offset)


def _source_table(source: CsvTable | str | Any) -> CsvTable:
    if isinstance(source, CsvTable):
        return source
    if isinstance(source, str) and ("\n" in source or "\r" in source):
        return read_csv_text(source)
    try:
        return read_csv(source)
    except (OSError, TypeError, ValueError) as exc:
        raise ScheduleInputError(f"Could not read schedule CSV: {exc}") from exc


def _row_value(values: tuple[str, ...], index: int) -> str:
    return values[index] if index < len(values) else ""


def _invalid_time_issue(
    row_number: int,
    start_text: str,
    end_text: str,
    *,
    start_valid: bool,
    end_valid: bool,
) -> ScheduleIssue:
    invalid_fields = []
    if not start_valid:
        invalid_fields.append("start")
    if not end_valid:
        invalid_fields.append("end")
    fields = " and ".join(invalid_fields)
    return ScheduleIssue(
        code="SC01",
        level=IssueLevel.ERROR,
        row_number=row_number,
        message=(
            f"{fields.capitalize()} time must be YYYY-MM-DD HH:MM "
            f"(received start={start_text!r}, end={end_text!r})."
        ),
        suggestion="Correct the session time; excluded sessions are not added to the calendar.",
        field=fields if len(invalid_fields) == 1 else None,
    )


def _overlap(left: Session, right: Session) -> bool:
    """Return true only for a real overlap; touching endpoints are allowed."""

    return left.start < right.end and right.start < left.end


def _clash_records(sessions: list[Session]) -> list[ScheduleIssue]:
    records: list[ScheduleIssue] = []
    for left_index, left in enumerate(sessions):
        for right in sessions[left_index + 1 :]:
            if not _overlap(left, right):
                continue
            comparisons = (
                (
                    "SC02",
                    left.room.strip(),
                    "Room",
                    "Adjust one session's time or move it to another room.",
                ),
                (
                    "SC03",
                    left.speaker.strip(),
                    "Speaker",
                    "Adjust one of the speaker's session times.",
                ),
            )
            for code, left_value, label, suggestion in comparisons:
                right_value = getattr(right, "room" if code == "SC02" else "speaker").strip()
                if not left_value or left_value != right_value:
                    continue
                message = (
                    f"{label} {left_value!r} overlaps with session on row "
                    f"{right.row_number}."
                )
                records.extend(
                    (
                        ScheduleIssue(
                            code=code,
                            level=IssueLevel.ERROR,
                            row_number=left.row_number,
                            message=message,
                            suggestion=suggestion,
                            related_row_number=right.row_number,
                            field="room" if code == "SC02" else "speaker",
                        ),
                        ScheduleIssue(
                            code=code,
                            level=IssueLevel.ERROR,
                            row_number=right.row_number,
                            message=(
                                f"{label} {left_value!r} overlaps with session on row "
                                f"{left.row_number}."
                            ),
                            suggestion=suggestion,
                            related_row_number=left.row_number,
                            field="room" if code == "SC02" else "speaker",
                        ),
                    )
                )
    return records


def detect_clashes(sessions: Iterable[Session]) -> tuple[ScheduleIssue, ...]:
    """Detect pairwise room and speaker clashes in valid sessions."""

    records = _clash_records(list(sessions))
    return tuple(
        sorted(
            records,
            key=lambda issue: (
                issue.row_number,
                _CODE_ORDER.get(issue.code, 99),
                issue.related_row_number or 0,
            ),
        )
    )


def _add_clash_ids(
    sessions: list[Session], issues: Iterable[ScheduleIssue]
) -> list[Session]:
    issue_codes: dict[int, set[str]] = {session.row_number: set() for session in sessions}
    for issue in issues:
        issue_codes.setdefault(issue.row_number, set()).add(issue.code)
    return [
        replace(
            session,
            issue_ids=tuple(sorted(issue_codes.get(session.row_number, ()), key=_CODE_ORDER.get)),
        )
        for session in sessions
    ]


def parse_schedule(
    source: CsvTable | str | Any,
    utc_offset: str | Any = "+00:00",
) -> ScheduleResult:
    """Parse a schedule CSV and detect room and speaker clashes.

    ``source`` may be a path, CSV text, or an existing :class:`CsvTable`.
    Invalid time rows produce SC01 and are omitted from ``sessions``.  Valid
    sessions involved in SC02/SC03 remain in ``sessions`` with their issue IDs
    attached.
    """

    offset = parse_utc_offset(utc_offset)
    table = _source_table(source)
    normalized_headers = tuple(header.strip().lower() for header in table.headers)
    required = ("start", "end", "title", "room")
    missing = tuple(column for column in required if column not in normalized_headers)
    if missing:
        joined = ", ".join(missing)
        raise ScheduleInputError(f"Schedule CSV is missing required column(s): {joined}")

    indices = {header: index for index, header in enumerate(normalized_headers)}
    speaker_index = indices.get("speaker")
    extra_columns = tuple(
        header for header, normalized in zip(table.headers, normalized_headers)
        if normalized not in (*required, "speaker")
    )
    valid_sessions: list[Session] = []
    issues: list[ScheduleIssue] = []
    excluded: list[int] = []

    for row in table.rows:
        values = row.values
        start_text = _row_value(values, indices["start"])
        end_text = _row_value(values, indices["end"])
        start = _parse_local_datetime(start_text, offset)
        end = _parse_local_datetime(end_text, offset)
        if start is None or end is None:
            issues.append(
                _invalid_time_issue(
                    row.line_number,
                    start_text,
                    end_text,
                    start_valid=start is not None,
                    end_valid=end is not None,
                )
            )
            excluded.append(row.line_number)
            continue
        if end <= start:
            issues.append(
                ScheduleIssue(
                    code="SC01",
                    level=IssueLevel.ERROR,
                    row_number=row.line_number,
                    message="Session end must be after session start.",
                    suggestion="Use an end time later than the start time; this session is excluded.",
                    field="end",
                )
            )
            excluded.append(row.line_number)
            continue
        valid_sessions.append(
            Session(
                row_number=row.line_number,
                start=start,
                end=end,
                title=_row_value(values, indices["title"]),
                room=_row_value(values, indices["room"]),
                speaker=_row_value(values, speaker_index) if speaker_index is not None else "",
            )
        )

    clash_issues = detect_clashes(valid_sessions)
    issues.extend(clash_issues)
    valid_sessions = _add_clash_ids(valid_sessions, clash_issues)
    issues.sort(
        key=lambda issue: (
            issue.row_number,
            _CODE_ORDER.get(issue.code, 99),
            issue.related_row_number or 0,
        )
    )
    return ScheduleResult(
        sessions=tuple(valid_sessions),
        issues=tuple(issues),
        excluded_row_numbers=tuple(excluded),
        extra_columns=extra_columns,
    )


parse_schedule_csv = parse_schedule
validate_schedule = parse_schedule


def render_ics(*args: Any, **kwargs: Any) -> bytes:
    """Lazy convenience wrapper for :func:`eventkit.calendar.render_ics`."""

    from .calendar import render_ics as _render_ics

    return _render_ics(*args, **kwargs)


def render_agenda(*args: Any, **kwargs: Any) -> bytes:
    """Lazy convenience wrapper for :func:`eventkit.agenda.render_agenda`."""

    from .agenda import render_agenda as _render_agenda

    return _render_agenda(*args, **kwargs)


__all__ = [
    "ScheduleInputError",
    "ScheduleIssue",
    "ScheduleResult",
    "Session",
    "detect_clashes",
    "parse_schedule",
    "parse_schedule_csv",
    "parse_utc_offset",
    "render_agenda",
    "render_ics",
    "validate_schedule",
]
