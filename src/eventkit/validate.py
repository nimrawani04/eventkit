"""Roster normalization, de-duplication, and EK01-EK06 validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from typing import Any

from .csvio import CsvTable
from .model import Attendee, Issue, IssueLevel, RosterResult, RosterRow, RowStatus
from .normalize import (
    email_domain,
    email_domain_suggestion,
    is_valid_email,
    normalize_attendee,
)


DEFAULT_COLUMNS: dict[str, str] = {
    "name": "name",
    "email": "email",
    "phone": "phone",
    "institution": "institution",
}

_ISSUE_ORDER = {f"EK{number:02d}": number for number in range(1, 7)}


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _source_value(
    row: Mapping[str, Any],
    canonical_name: str,
    columns: Mapping[str, str] | None,
) -> str:
    source_name = (columns or DEFAULT_COLUMNS).get(canonical_name, canonical_name)
    if source_name in row:
        return _string(row[source_name])
    # A fallback is helpful when a partial column map only names required
    # fields and optional values retain their canonical names.
    if canonical_name in row:
        return _string(row[canonical_name])
    return ""


def attendee_from_mapping(
    row: Mapping[str, Any],
    *,
    row_number: int | None = None,
    columns: Mapping[str, str] | None = None,
) -> Attendee:
    """Build an :class:`Attendee` from canonical or mapped CSV columns.

    ``row_number`` normally comes from :class:`eventkit.csvio.CsvRow`; when it
    is omitted, common line-number keys in the mapping are checked.
    """

    if row_number is None:
        for key in ("row_number", "line_number", "row", "_line"):
            if key in row:
                row_number = int(row[key])
                break
    if row_number is None:
        raise ValueError("row_number is required for a roster mapping")

    return Attendee(
        row_number=row_number,
        name=_source_value(row, "name", columns),
        email=_source_value(row, "email", columns),
        phone=_source_value(row, "phone", columns),
        institution=_source_value(row, "institution", columns),
    )


def attendees_from_csv(
    table: CsvTable,
    *,
    columns: Mapping[str, str] | None = None,
) -> tuple[Attendee, ...]:
    """Convert a :class:`CsvTable` to attendees while preserving row order."""

    return tuple(
        attendee_from_mapping(
            row.as_dict(table.headers),
            row_number=row.line_number,
            columns=columns,
        )
        for row in table.rows
    )


def _append_issue(
    issue_lists: list[list[Issue]],
    index: int,
    *,
    code: str,
    level: IssueLevel,
    row_number: int,
    message: str,
    suggestion: str,
    related_row_number: int | None = None,
    field: str | None = None,
) -> None:
    issue_lists[index].append(
        Issue(
            code=code,
            level=level,
            row_number=row_number,
            message=message,
            suggestion=suggestion,
            related_row_number=related_row_number,
            field=field,
        )
    )


def _similar_name(left: str, right: str) -> float:
    return SequenceMatcher(None, left.casefold(), right.casefold(), autojunk=False).ratio()


def _pair_candidates(
    indexes: list[int],
    normalized_names: list[str],
) -> Iterable[tuple[int, int]]:
    """Yield every pair in an institution group.

    EK04 is defined in terms of a similarity threshold, so an unproven bucket
    heuristic can silently miss a required warning.  The groups are limited to
    otherwise eligible rows and this exact implementation keeps the rule
    correct at every group size.  ``normalized_names`` stays in the signature
    for compatibility with the earlier helper and for future proven indexing.
    """

    del normalized_names
    for position, left_index in enumerate(indexes):
        for right_index in indexes[position + 1 :]:
            yield left_index, right_index


def validate_roster(
    rows: Iterable[Attendee | Mapping[str, Any]] | CsvTable,
    *,
    columns: Mapping[str, str] | None = None,
) -> RosterResult:
    """Normalize and validate registration rows using EK01 through EK06.

    The returned result contains every input row in source order.  Error rows
    are marked ``excluded``; warning rows stay ``ok``.  Exact duplicates keep
    the first otherwise-eligible occurrence and exclude later eligible
    occurrences; malformed rows do not participate in de-duplication because
    they are already excluded by EK01 or EK02.  When the input is a
    :class:`CsvTable`, ignored headers are exposed as ``extra_columns``.
    """

    extra_columns: tuple[str, ...] = ()
    if isinstance(rows, CsvTable):
        mapped_columns = {
            (columns or DEFAULT_COLUMNS).get(name, name)
            for name in DEFAULT_COLUMNS
        }
        extra_columns = tuple(
            header for header in rows.headers if header not in mapped_columns
        )
        attendees = list(attendees_from_csv(rows, columns=columns))
    else:
        attendees = []
        for row in rows:
            if isinstance(row, Attendee):
                attendees.append(row)
            else:
                attendees.append(attendee_from_mapping(row, columns=columns))

    attendees = [normalize_attendee(attendee) for attendee in attendees]
    issue_lists: list[list[Issue]] = [[] for _ in attendees]

    first_email_row: dict[str, int] = {}
    eligible: list[int] = []
    for index, attendee in enumerate(attendees):
        if not attendee.normalized_name or not attendee.normalized_email:
            _append_issue(
                issue_lists,
                index,
                code="EK01",
                level=IssueLevel.ERROR,
                row_number=attendee.row_number,
                message="Name or email is missing.",
                suggestion="Provide both a name and an email address.",
                field="name" if not attendee.normalized_name else "email",
            )
            continue

        if not is_valid_email(attendee.normalized_email):
            _append_issue(
                issue_lists,
                index,
                code="EK02",
                level=IssueLevel.ERROR,
                row_number=attendee.row_number,
                message="Email address is not syntactically valid.",
                suggestion="Check the email address and enter it again.",
                field="email",
            )
            continue

        previous_index = first_email_row.get(attendee.normalized_email)
        if previous_index is not None:
            previous_row = attendees[previous_index].row_number
            _append_issue(
                issue_lists,
                index,
                code="EK03",
                level=IssueLevel.ERROR,
                row_number=attendee.row_number,
                message=(
                    f"Email is an exact duplicate of row {previous_row} "
                    "(case-insensitive)."
                ),
                suggestion="Keep the first registration or confirm which row to use.",
                related_row_number=previous_row,
                field="email",
            )
            continue

        first_email_row[attendee.normalized_email] = index
        eligible.append(index)

    # EK04 is only applied to rows that can otherwise be included.  This
    # avoids treating a malformed/excluded registration as a duplicate of a
    # usable one.
    phone_groups: dict[str, list[int]] = defaultdict(list)
    institution_groups: dict[str, list[int]] = defaultdict(list)
    normalized_names = [attendee.normalized_name for attendee in attendees]
    for index in eligible:
        attendee = attendees[index]
        phone_digits = attendee.normalized_phone.lstrip("+")
        if phone_digits:
            for previous_index in phone_groups[phone_digits]:
                previous = attendees[previous_index]
                if attendee.normalized_email != previous.normalized_email:
                    _append_possible_phone_issue(
                        issue_lists, index, previous_index, attendees
                    )
            phone_groups[phone_digits].append(index)

        institution = attendee.normalized_institution.casefold()
        if institution and attendee.normalized_name:
            institution_groups[institution].append(index)

        if attendee.has_phone:
            digit_count = len(phone_digits)
            if digit_count < 8 or digit_count > 15:
                _append_issue(
                    issue_lists,
                    index,
                    code="EK05",
                    level=IssueLevel.WARNING,
                    row_number=attendee.row_number,
                    message=(
                        f"Phone has {digit_count} digits; expected between 8 and 15."
                    ),
                    suggestion="Check the phone number and its country code.",
                    field="phone",
                )

        suggestion = email_domain_suggestion(attendee.normalized_email)
        if suggestion is not None:
            domain = email_domain(attendee.normalized_email)
            _append_issue(
                issue_lists,
                index,
                code="EK06",
                level=IssueLevel.WARNING,
                row_number=attendee.row_number,
                message=f"Email domain '{domain}' looks like '{suggestion}'.",
                suggestion=f"Check the spelling; did you mean {suggestion}?",
                field="email",
            )

    for indexes in institution_groups.values():
        for left_index, right_index in _pair_candidates(indexes, normalized_names):
            left = attendees[left_index]
            right = attendees[right_index]
            if left.normalized_email == right.normalized_email:
                continue
            similarity = _similar_name(left.normalized_name, right.normalized_name)
            if similarity < 0.92:
                continue
            _append_possible_name_issue(
                issue_lists,
                left_index,
                right_index,
                attendees,
                similarity,
            )

    result_rows: list[RosterRow] = []
    result_issues: list[Issue] = []
    for attendee, row_issues in zip(attendees, issue_lists):
        row_issues.sort(
            key=lambda issue: (
                _ISSUE_ORDER.get(issue.code, 99), issue.related_row_number or 0
            )
        )
        issue_ids = tuple(dict.fromkeys(issue.code for issue in row_issues))
        status = (
            RowStatus.EXCLUDED
            if any(issue.level is IssueLevel.ERROR for issue in row_issues)
            else RowStatus.OK
        )
        result_rows.append(RosterRow(attendee, status, issue_ids))
        result_issues.extend(row_issues)

    return RosterResult(
        tuple(result_rows),
        tuple(result_issues),
        extra_columns=extra_columns,
    )


def _append_possible_phone_issue(
    issue_lists: list[list[Issue]],
    current_index: int,
    previous_index: int,
    attendees: list[Attendee],
) -> None:
    current = attendees[current_index]
    previous = attendees[previous_index]
    _append_issue(
        issue_lists,
        current_index,
        code="EK04",
        level=IssueLevel.WARNING,
        row_number=current.row_number,
        message=(
            f"Possible duplicate of row {previous.row_number}: the phone number "
            "matches a different email."
        ),
        suggestion="Confirm whether these registrations belong to the same person.",
        related_row_number=previous.row_number,
        field="phone",
    )
    _append_issue(
        issue_lists,
        previous_index,
        code="EK04",
        level=IssueLevel.WARNING,
        row_number=previous.row_number,
        message=(
            f"Possible duplicate of row {current.row_number}: the phone number "
            "matches a different email."
        ),
        suggestion="Confirm whether these registrations belong to the same person.",
        related_row_number=current.row_number,
        field="phone",
    )


def _append_possible_name_issue(
    issue_lists: list[list[Issue]],
    left_index: int,
    right_index: int,
    attendees: list[Attendee],
    similarity: float,
) -> None:
    left = attendees[left_index]
    right = attendees[right_index]
    percentage = f"{similarity:.0%}"
    for current_index, related in ((left_index, right), (right_index, left)):
        current = attendees[current_index]
        _append_issue(
            issue_lists,
            current_index,
            code="EK04",
            level=IssueLevel.WARNING,
            row_number=current.row_number,
            message=(
                f"Possible duplicate of row {related.row_number}: names are "
                f"{percentage} similar at the same institution."
            ),
            suggestion="Confirm whether these registrations belong to the same person.",
            related_row_number=related.row_number,
            field="name",
        )


# A short alias keeps the main operation discoverable for callers that think
# of the module as a validator rather than a roster-specific pipeline.
validate = validate_roster
