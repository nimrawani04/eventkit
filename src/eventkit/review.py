"""Human-review resolution ledgers for validation findings.

The ledger is deliberately separate from the validation pipeline.  A review
record describes what a person decided about one finding; it does not rewrite
the input row or make a validator's finding disappear.  ``apply_decisions``
can make an explicitly requested replacement in a copied set of rows, while
the original objects remain untouched.

The on-disk format is deterministic JSONL.  The first line identifies the
format and its schema version; each following line is one review record.  JSON
was chosen over TOML here because it gives an unambiguous representation for
optional replacement values while remaining easy to edit one decision at a
time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, fields as dataclass_fields, is_dataclass, replace
import json
import re
from typing import Any, Literal


SCHEMA_VERSION = 1
LEDGER_TYPE = "eventkit-review-ledger"
VALID_ACTIONS = frozenset({"accept", "reject", "defer", "replace"})
Audience = Literal["public", "private"]


class ReviewError(ValueError):
    """Base error for malformed review records and ledgers."""


class LedgerParseError(ReviewError):
    """Raised when JSONL cannot be parsed as a review ledger."""


class LedgerValidationError(ReviewError):
    """Raised when decisions do not match the supplied issue findings."""

    def __init__(self, message: str, *, errors: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.errors = tuple(errors)


class PrivacyViolation(ReviewError):
    """Raised when text intended for public use contains email-shaped PII."""


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{name} is required")
    return value.strip()


def _row_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewError("row_number must be a positive integer")
    return value


def _canonical_code(value: Any) -> str:
    code = _required_text(value, "issue_code")
    return code.upper()


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    """One explicit decision for an ``(issue_code, row_number)`` key.

    ``replace`` requires both ``field`` and ``value``.  The value is applied
    only to a copied row by :func:`apply_decisions`; constructing a record does
    not alter any source data.
    """

    issue_code: str
    row_number: int
    action: str
    reason: str
    reviewer: str
    field: str | None = None
    value: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_code", _canonical_code(self.issue_code))
        object.__setattr__(self, "row_number", _row_number(self.row_number))

        action = _required_text(self.action, "action").lower()
        if action not in VALID_ACTIONS:
            allowed = ", ".join(sorted(VALID_ACTIONS))
            raise ReviewError(f"action must be one of {allowed}")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))
        object.__setattr__(self, "reviewer", _required_text(self.reviewer, "reviewer"))

        if self.field is not None:
            object.__setattr__(self, "field", _required_text(self.field, "field"))
        if self.value is not None and self.field is None:
            raise ReviewError("field is required when value is supplied")
        if action == "replace" and (self.field is None or self.value is None):
            raise ReviewError("replace decisions require both field and value")

    @property
    def key(self) -> tuple[str, int]:
        """The stable key used to match this record to a finding."""

        return self.issue_code, self.row_number

    @property
    def resolved(self) -> bool:
        """Whether the decision closes the finding for review purposes."""

        return self.action != "defer"

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON object for this record."""

        result: dict[str, Any] = {
            "action": self.action,
            "issue_code": self.issue_code,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "row_number": self.row_number,
        }
        if self.field is not None:
            result["field"] = self.field
        if self.value is not None:
            result["value"] = self.value
        return result


def _record_from_value(value: ResolutionRecord | Mapping[str, Any]) -> ResolutionRecord:
    if isinstance(value, ResolutionRecord):
        return value
    if not isinstance(value, Mapping):
        raise ReviewError("each ledger record must be a mapping")
    allowed = {
        "action",
        "issue_code",
        "reason",
        "reviewer",
        "row_number",
        "field",
        "value",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReviewError(f"unknown record field(s): {', '.join(map(str, unknown))}")
    required = {"issue_code", "row_number", "action", "reason", "reviewer"}
    missing = sorted(required - set(value))
    if missing:
        raise ReviewError(f"record is missing required field(s): {', '.join(missing)}")
    return ResolutionRecord(
        issue_code=value["issue_code"],
        row_number=value["row_number"],
        action=value["action"],
        reason=value["reason"],
        reviewer=value["reviewer"],
        field=value.get("field"),
        value=value.get("value"),
    )


@dataclass(frozen=True, slots=True)
class ResolutionLedger:
    """A unique, deterministically ordered collection of review records."""

    records: tuple[ResolutionRecord, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ReviewError(f"unsupported schema_version: {self.schema_version!r}")

        normalized = tuple(_record_from_value(record) for record in self.records)
        by_key: dict[tuple[str, int], ResolutionRecord] = {}
        for record in normalized:
            if record.key in by_key:
                code, row = record.key
                raise ReviewError(f"duplicate decision for {code} row {row}")
            by_key[record.key] = record
        ordered = tuple(by_key[key] for key in sorted(by_key))
        object.__setattr__(self, "records", ordered)

    def __iter__(self):
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def get(self, issue_code: str, row_number: int) -> ResolutionRecord | None:
        """Return a decision by key, or ``None`` when it is absent."""

        key = (_canonical_code(issue_code), _row_number(row_number))
        return next((record for record in self.records if record.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "records": [record.as_dict() for record in self.records],
        }


def make_record(
    issue_code: str,
    row_number: int,
    action: str,
    reason: str,
    reviewer: str,
    *,
    field: str | None = None,
    value: Any = None,
) -> ResolutionRecord:
    """Construct a validated :class:`ResolutionRecord` explicitly."""

    return ResolutionRecord(
        issue_code=issue_code,
        row_number=row_number,
        action=action,
        reason=reason,
        reviewer=reviewer,
        field=field,
        value=value,
    )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReviewError(f"value is not JSON serializable: {exc}") from exc


def render_ledger(
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
) -> str:
    """Render a ledger as deterministic, UTF-8-friendly JSONL text."""

    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    lines = [
        _canonical_json(
            {"schema_version": normalized.schema_version, "type": LEDGER_TYPE}
        )
    ]
    lines.extend(_canonical_json(record.as_dict()) for record in normalized.records)
    return "\n".join(lines) + "\n"


def render_ledger_bytes(
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
) -> bytes:
    """Render ledger bytes using UTF-8 and stable LF line endings."""

    return render_ledger(ledger).encode("utf-8")


def parse_ledger(source: str | bytes | bytearray) -> ResolutionLedger:
    """Parse deterministic JSONL ledger text.

    Blank lines and lines beginning with ``#`` are accepted for convenient
    human editing.  Rendered output never emits either kind of line.
    """

    if isinstance(source, (bytes, bytearray)):
        try:
            text = bytes(source).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LedgerParseError("ledger is not valid UTF-8") from exc
    elif isinstance(source, str):
        text = source.removeprefix("\ufeff")
    else:
        raise LedgerParseError("ledger source must be text or UTF-8 bytes")

    objects: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LedgerParseError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise LedgerParseError(f"line {line_number} must contain a JSON object")
        objects.append(value)

    if not objects:
        raise LedgerParseError("ledger is empty")
    header = objects[0]
    if header.get("type") != LEDGER_TYPE:
        raise LedgerParseError("first JSON object must be an eventkit review ledger header")
    if header.get("schema_version") != SCHEMA_VERSION:
        raise LedgerParseError(
            f"unsupported schema_version: {header.get('schema_version')!r}"
        )
    unknown_header = set(header) - {"schema_version", "type"}
    if unknown_header:
        raise LedgerParseError(
            f"unknown header field(s): {', '.join(sorted(map(str, unknown_header)))}"
        )

    records: list[ResolutionRecord] = []
    for index, value in enumerate(objects[1:], 2):
        try:
            records.append(_record_from_value(value))
        except ReviewError as exc:
            raise LedgerParseError(f"invalid record on line {index}: {exc}") from exc
    try:
        return ResolutionLedger(tuple(records), schema_version=header["schema_version"])
    except ReviewError as exc:
        raise LedgerParseError(str(exc)) from exc


load_ledger = parse_ledger


def _value(item: Any, *names: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        for name in names:
            if name in item:
                return item[name]
    else:
        for name in names:
            if hasattr(item, name):
                return getattr(item, name)
    return default


def _finding_key(finding: Any) -> tuple[str, int]:
    code = _value(finding, "issue_code", "code", "issue_id", "id")
    row = _value(finding, "row_number", "row", "line_number", "line")
    try:
        return _canonical_code(code), _row_number(row)
    except ReviewError as exc:
        raise LedgerValidationError(f"invalid issue finding: {exc}") from exc


def _finding_field(finding: Any) -> str | None:
    field = _value(finding, "field", default=None)
    return None if field is None else _required_text(field, "finding field")


def _findings_by_key(findings: Iterable[Any]) -> dict[tuple[str, int], Any]:
    by_key: dict[tuple[str, int], Any] = {}
    for finding in findings:
        key = _finding_key(finding)
        by_key.setdefault(key, finding)
    return by_key


def validate_decisions(
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
    findings: Iterable[Any],
) -> tuple[tuple[str, int], ...]:
    """Validate every decision key and optional field against issue findings.

    The returned keys are ordered like the ledger.  Invalid or stale records
    raise :class:`LedgerValidationError` rather than being ignored.
    """

    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    finding_map = _findings_by_key(findings)
    errors: list[str] = []
    for record in normalized.records:
        finding = finding_map.get(record.key)
        if finding is None:
            errors.append(
                f"decision {record.issue_code} row {record.row_number} has no matching finding"
            )
            continue
        finding_field = _finding_field(finding)
        if (
            record.field is not None
            and finding_field is not None
            and record.field != finding_field
        ):
            errors.append(
                f"decision {record.issue_code} row {record.row_number} targets field "
                f"{record.field!r}, but the finding targets {finding_field!r}"
            )
    if errors:
        raise LedgerValidationError("review decisions do not match findings", errors=errors)
    return tuple(record.key for record in normalized.records)


validate_ledger = validate_decisions


def unresolved_issues(
    findings: Iterable[Any],
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Return findings with no decision or with a deferred decision."""

    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    finding_list = tuple(findings)
    validate_decisions(normalized, finding_list)
    decisions = {record.key: record for record in normalized.records}
    return tuple(
        finding
        for finding in finding_list
        if (decision := decisions.get(_finding_key(finding))) is None
        or not decision.resolved
    )


def issue_status(finding: Any, ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]]) -> str:
    """Return ``"resolved"`` or ``"unresolved"`` for one finding."""

    key = _finding_key(finding)
    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    decision = next((record for record in normalized.records if record.key == key), None)
    return "resolved" if decision is not None and decision.resolved else "unresolved"


def _row_key(row: Any) -> tuple[str, int]:
    number = _value(row, "row_number", "row", "line_number", "line", "_line")
    if number is None:
        raise ReviewError("source row has no row_number")
    return "row", _row_number(number)


def _replace_value(row: Any, field: str, value: Any) -> Any:
    """Set one field on a copy of a row, preserving common row types."""

    if isinstance(row, Mapping):
        copied = dict(row)
        copied[field] = value
        return copied
    if is_dataclass(row):
        names = {item.name for item in dataclass_fields(row)}
        if field not in names:
            raise ReviewError(f"source row has no field {field!r}")
        return replace(row, **{field: value})
    if hasattr(row, "_replace"):
        try:
            return row._replace(**{field: value})
        except (AttributeError, ValueError) as exc:
            raise ReviewError(f"source row has no field {field!r}") from exc
    copied = deepcopy(row)
    if not hasattr(copied, field):
        raise ReviewError(f"source row has no field {field!r}")
    setattr(copied, field, value)
    return copied


@dataclass(frozen=True, slots=True)
class AppliedReview:
    """Result of applying decisions to copied rows."""

    rows: tuple[Any, ...]
    findings: tuple[Any, ...]
    decisions: tuple[ResolutionRecord, ...]
    unresolved: tuple[Any, ...]

    @property
    def source_rows(self) -> tuple[Any, ...]:
        """Alias emphasizing that ``rows`` are detached from source rows."""

        return self.rows


def apply_decisions(
    rows: Iterable[Any],
    findings: Iterable[Any],
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
) -> AppliedReview:
    """Apply explicit replacement decisions to copied rows.

    ``accept``, ``reject`` and ``defer`` affect review status only.  ``replace``
    updates the named field on the copied row identified by ``row_number``.
    Every replacement must match a finding and an actual source row.  The
    input row objects and the finding objects are never modified.
    """

    finding_list = tuple(findings)
    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    validate_decisions(normalized, finding_list)

    copied_rows = list(deepcopy(tuple(rows)))
    row_indexes: dict[int, int] = {}
    for index, row in enumerate(copied_rows):
        _, number = _row_key(row)
        if number in row_indexes:
            raise ReviewError(f"duplicate source row number: {number}")
        row_indexes[number] = index

    for decision in normalized.records:
        if decision.action != "replace":
            continue
        index = row_indexes.get(decision.row_number)
        if index is None:
            raise ReviewError(
                f"replacement {decision.issue_code} row {decision.row_number} "
                "has no matching source row"
            )
        copied_rows[index] = _replace_value(
            copied_rows[index], decision.field or "", decision.value
        )

    return AppliedReview(
        rows=tuple(copied_rows),
        findings=finding_list,
        decisions=normalized.records,
        unresolved=unresolved_issues(finding_list, normalized),
    )


apply_resolutions = apply_decisions


def _finding_message(finding: Any) -> str:
    message = _value(finding, "message", "description", default="")
    return str(message) if message is not None else ""


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _audience(value: str, public: bool | None) -> Audience:
    if public is not None:
        value = "public" if public else "private"
    normalized = value.lower().strip()
    if normalized not in {"public", "private"}:
        raise ReviewError("audience must be 'public' or 'private'")
    return normalized  # type: ignore[return-value]


def render_review_report(
    findings: Iterable[Any],
    ledger: ResolutionLedger | Iterable[ResolutionRecord | Mapping[str, Any]],
    *,
    title: str = "Human Review Resolution Report",
    audience: Audience = "private",
    public: bool | None = None,
) -> str:
    """Render a deterministic Markdown report of resolved and open findings.

    Public reports redact email-shaped strings.  Private reports retain the
    generated detail and can still be checked with :func:`check_privacy`.
    """

    finding_list = tuple(findings)
    normalized = ledger if isinstance(ledger, ResolutionLedger) else ResolutionLedger(tuple(ledger))
    validate_decisions(normalized, finding_list)
    selected_audience = _audience(audience, public)
    decisions = {record.key: record for record in normalized.records}

    unique_findings: dict[tuple[str, int], Any] = {}
    for finding in finding_list:
        unique_findings.setdefault(_finding_key(finding), finding)
    ordered = tuple(unique_findings[key] for key in sorted(unique_findings))
    open_findings = tuple(
        finding
        for finding in ordered
        if (decision := decisions.get(_finding_key(finding))) is None
        or not decision.resolved
    )
    resolved = tuple(
        (finding, decisions[_finding_key(finding)])
        for finding in ordered
        if _finding_key(finding) in decisions
        and decisions[_finding_key(finding)].resolved
    )

    lines = [
        f"# {_markdown_cell(title)}",
        "",
        f"- Schema version: `{SCHEMA_VERSION}`",
        f"- Findings: {len(ordered)}",
        f"- Resolved: {len(resolved)}",
        f"- Unresolved: {len(open_findings)}",
        "",
        "## Unresolved",
        "",
        "| Issue | Row | Finding |",
        "| --- | ---: | --- |",
    ]
    if open_findings:
        for finding in open_findings:
            code, row = _finding_key(finding)
            lines.append(
                f"| {_markdown_cell(code)} | {row} | {_markdown_cell(_finding_message(finding))} |"
            )
    else:
        lines.append("| — | — | None |")

    lines.extend(
        [
            "",
            "## Resolved",
            "",
            "| Issue | Row | Action | Field | Value | Reviewer | Reason |",
            "| --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    if resolved:
        for finding, decision in resolved:
            code, row = _finding_key(finding)
            rendered_value = "" if decision.value is None else _canonical_json(decision.value)
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(item)
                    for item in (
                        code,
                        row,
                        decision.action,
                        decision.field or "",
                        rendered_value,
                        decision.reviewer,
                        decision.reason,
                    )
                )
                + " |"
            )
    else:
        lines.append("| — | — | — | — | — | — | None |")

    report = "\n".join(lines) + "\n"
    return redact_email_pii(report) if selected_audience == "public" else report


# This intentionally recognizes ordinary email-shaped values without trying to
# validate every RFC edge case.  It is for leakage detection, not delivery.
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9-]|\.[A-Za-z])"
)


def find_email_pii(text: str) -> tuple[str, ...]:
    """Return unique email-shaped strings in first-seen order."""

    if not isinstance(text, str):
        raise ReviewError("text must be a string")
    return tuple(dict.fromkeys(match.group(0) for match in _EMAIL_PATTERN.finditer(text)))


def redact_email_pii(text: str, *, replacement: str = "[redacted-email]") -> str:
    """Replace email-shaped strings with a stable marker."""

    if not isinstance(text, str):
        raise ReviewError("text must be a string")
    if not isinstance(replacement, str) or not replacement:
        raise ReviewError("replacement must be a non-empty string")
    return _EMAIL_PATTERN.sub(replacement, text)


@dataclass(frozen=True, slots=True)
class PrivacyCheck:
    """The result of scanning generated text for email-shaped PII."""

    audience: Audience
    emails: tuple[str, ...]

    @property
    def contains_pii(self) -> bool:
        return bool(self.emails)

    @property
    def safe(self) -> bool:
        """Whether the text is safe for the selected audience."""

        return self.audience == "private" or not self.contains_pii

    @property
    def is_safe(self) -> bool:
        return self.safe


def check_privacy(
    text: str,
    *,
    audience: Audience = "public",
    public: bool | None = None,
) -> PrivacyCheck:
    """Scan generated text, rejecting email PII only for public output.

    Private output is allowed to contain emails but still reports them through
    ``emails`` and ``contains_pii`` so callers can audit both variants.
    """

    selected_audience = _audience(audience, public)
    return PrivacyCheck(selected_audience, find_email_pii(text))


privacy_check = check_privacy
check_generated_privacy = check_privacy


def assert_public_privacy(text: str) -> PrivacyCheck:
    """Raise :class:`PrivacyViolation` when public text contains an email."""

    result = check_privacy(text, audience="public")
    if not result.safe:
        raise PrivacyViolation(
            "public text contains email-shaped PII: " + ", ".join(result.emails)
        )
    return result


__all__ = [
    "AppliedReview",
    "LEDGER_TYPE",
    "LedgerParseError",
    "LedgerValidationError",
    "PrivacyCheck",
    "PrivacyViolation",
    "ResolutionLedger",
    "ResolutionRecord",
    "ReviewError",
    "SCHEMA_VERSION",
    "VALID_ACTIONS",
    "apply_decisions",
    "apply_resolutions",
    "assert_public_privacy",
    "check_generated_privacy",
    "check_privacy",
    "find_email_pii",
    "issue_status",
    "load_ledger",
    "make_record",
    "parse_ledger",
    "privacy_check",
    "redact_email_pii",
    "render_ledger",
    "render_ledger_bytes",
    "render_review_report",
    "unresolved_issues",
    "validate_decisions",
    "validate_ledger",
]
