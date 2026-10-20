"""Attendance input and certificate recipient selection."""

from __future__ import annotations

from dataclasses import dataclass

from .codes import CertificateRecipient
from .csvio import CsvTable
from .model import Issue, IssueLevel, RosterResult, RowStatus
from .normalize import normalize_email


@dataclass(frozen=True, slots=True)
class AttendanceResult:
    """Matched certificate recipients and attendance findings."""

    recipients: tuple[CertificateRecipient, ...]
    issues: tuple[Issue, ...]
    rows_read: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipients", tuple(self.recipients))
        object.__setattr__(self, "issues", tuple(self.issues))


def _header(table: CsvTable, wanted: str) -> str | None:
    wanted = wanted.casefold()
    return next((value for value in table.headers if value.strip().casefold() == wanted), None)


def filter_attendance(
    table: CsvTable,
    roster: RosterResult,
) -> AttendanceResult:
    """Match attendance emails to included roster rows in source order.

    Attendance rows that do not match, or match an excluded registration, get
    EK07 and never produce a certificate.  Missing roles default to
    ``Participant`` as specified by the PRD.
    """

    email_header = _header(table, "email")
    if email_header is None:
        raise ValueError("Attendance CSV must contain an email column")
    role_header = _header(table, "role")

    included: dict[str, object] = {}
    excluded: dict[str, object] = {}
    for row in roster.rows:
        email = row.attendee.normalized_email
        if not email:
            continue
        (included if row.status is RowStatus.OK else excluded)[email] = row

    recipients: list[CertificateRecipient] = []
    issues: list[Issue] = []
    seen_attendance: dict[str, tuple[int, str]] = {}
    for attendance_row in table.rows:
        values = attendance_row.as_dict(table.headers)
        raw_email = values.get(email_header, "")
        email = normalize_email(raw_email)
        if email and email in seen_attendance:
            previous_row, previous_role = seen_attendance[email]
            current_role = (values.get(role_header, "") if role_header else "").strip() or "Participant"
            issues.append(
                Issue(
                    code="EK08",
                    level=IssueLevel.WARNING,
                    row_number=attendance_row.line_number,
                    message=(
                        f"Attendance email is repeated from row {previous_row}"
                        + (
                            f" with role '{previous_role}' and role '{current_role}'."
                            if previous_role != current_role
                            else "."
                        )
                    ),
                    suggestion="Keep one attendance row or confirm the intended role.",
                    related_row_number=previous_row,
                    field="email",
                )
            )
        elif email:
            current_role = (values.get(role_header, "") if role_header else "").strip() or "Participant"
            seen_attendance[email] = (attendance_row.line_number, current_role)
        matched = included.get(email)
        excluded_match = excluded.get(email)
        if matched is None:
            reason = (
                "matches an excluded registration"
                if excluded_match is not None
                else "is not present in the registration roster"
            )
            issues.append(
                Issue(
                    code="EK07",
                    level=IssueLevel.WARNING,
                    row_number=attendance_row.line_number,
                    message=f"Attendance email '{raw_email.strip()}' {reason}.",
                    suggestion="Confirm the attendance email and registration status.",
                    field="email",
                )
            )
            continue
        role = (values.get(role_header, "") if role_header else "").strip() or "Participant"
        recipients.append(
            CertificateRecipient(
                email=matched.attendee.normalized_email,
                role=role,
                name=matched.attendee.name,
            )
        )

    return AttendanceResult(tuple(recipients), tuple(issues), len(table.rows))


def recipients_from_roster(roster: RosterResult, *, role: str = "Participant") -> tuple[CertificateRecipient, ...]:
    """Create certificate recipients for every included registration."""

    role = role.strip() or "Participant"
    return tuple(
        CertificateRecipient(
            email=row.attendee.normalized_email,
            role=role,
            name=row.attendee.name,
        )
        for row in roster.rows
        if row.status is RowStatus.OK and row.attendee.normalized_email
    )


__all__ = ["AttendanceResult", "filter_attendance", "recipients_from_roster"]
