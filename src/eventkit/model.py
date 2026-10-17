"""Small, serialization-friendly types used by the roster pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class IssueLevel(str, Enum):
    """The severity levels used by the first roster milestone."""

    ERROR = "error"
    WARNING = "warning"


class RowStatus(str, Enum):
    """Whether a source row can be used in the clean roster."""

    OK = "ok"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class Attendee:
    """A registration row, retaining source values and normalized values.

    ``row_number`` is the physical line at which the CSV record starts.  The
    four source fields are kept exactly as supplied so a later writer can
    explain or display the original registration.  Normalized fields are
    populated by :func:`eventkit.normalize.normalize_attendee`.
    """

    row_number: int
    name: str
    email: str
    phone: str = ""
    institution: str = ""
    normalized_name: str = ""
    normalized_email: str = ""
    normalized_phone: str = ""
    normalized_institution: str = ""

    @property
    def line_number(self) -> int:
        """Alias useful when an attendee came directly from a CSV row."""

        return self.row_number

    @property
    def has_phone(self) -> bool:
        """Whether the source supplied a non-blank phone value."""

        return bool(self.phone.strip())


@dataclass(frozen=True, slots=True)
class Issue:
    """One actionable validation finding for one roster row."""

    code: str
    level: IssueLevel
    row_number: int
    message: str
    suggestion: str = ""
    related_row_number: int | None = None
    field: str | None = None

    @property
    def issue_id(self) -> str:
        """Alias matching the terminology used in ``issues.md``."""

        return self.code

    def as_dict(self) -> dict[str, Any]:
        """Return a mapping ready for a report or template."""

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
class RosterRow:
    """An attendee together with its final status and issue identifiers."""

    attendee: Attendee
    status: RowStatus
    issue_ids: tuple[str, ...] = ()

    @property
    def row_number(self) -> int:
        return self.attendee.row_number

    @property
    def line_number(self) -> int:
        return self.attendee.line_number

    @property
    def name(self) -> str:
        return self.attendee.name

    @property
    def email(self) -> str:
        return self.attendee.email

    @property
    def phone(self) -> str:
        return self.attendee.phone

    @property
    def institution(self) -> str:
        return self.attendee.institution

    def as_dict(self, *, include_normalized: bool = False) -> dict[str, Any]:
        """Return columns suitable for a future ``roster.csv`` writer."""

        result: dict[str, Any] = {
            "row": self.row_number,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "institution": self.institution,
            "status": self.status.value,
            "issues": ",".join(self.issue_ids),
        }
        if include_normalized:
            result.update(
                {
                    "normalized_name": self.attendee.normalized_name,
                    "normalized_email": self.attendee.normalized_email,
                    "normalized_phone": self.attendee.normalized_phone,
                    "normalized_institution": self.attendee.normalized_institution,
                }
            )
        return result


@dataclass(frozen=True, slots=True)
class RosterResult:
    """All source rows and findings produced by roster validation.

    ``rows`` and ``issues`` are tuples in source order.  Excluded rows remain
    present in ``rows`` so no source registration is silently lost.
    """

    rows: tuple[RosterRow, ...]
    issues: tuple[Issue, ...]
    extra_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "extra_columns", tuple(self.extra_columns))

    @property
    def roster(self) -> tuple[RosterRow, ...]:
        """Alias for callers thinking of the result as a roster."""

        return self.rows

    @property
    def entries(self) -> tuple[RosterRow, ...]:
        """Alias for callers using the term roster entries."""

        return self.rows

    @property
    def included_rows(self) -> tuple[RosterRow, ...]:
        return tuple(row for row in self.rows if row.status is RowStatus.OK)

    @property
    def excluded_rows(self) -> tuple[RosterRow, ...]:
        return tuple(row for row in self.rows if row.status is RowStatus.EXCLUDED)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.level is IssueLevel.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.level is IssueLevel.WARNING)

    def roster_dicts(self, *, include_normalized: bool = False) -> tuple[dict[str, Any], ...]:
        """Return all rows as stable, writer-friendly dictionaries."""

        return tuple(
            row.as_dict(include_normalized=include_normalized) for row in self.rows
        )

    def issues_by_level(self) -> dict[str, tuple[Issue, ...]]:
        """Group findings by the headings used by a future issues report."""

        return {
            IssueLevel.ERROR.value: self.errors,
            IssueLevel.WARNING.value: self.warnings,
        }
