"""Offline, deterministic previews of the impact of an event-kit run.

The preview is deliberately a pure comparison layer.  It accepts the result
objects already produced by the roster, schedule, attendance, certificate,
and output steps, but it does not call those steps or write anything.  A
caller can therefore build a preview during a dry run and decide later how it
should be integrated into a command or report.

Registration email addresses are comparison keys only.  They are converted
to short, stable SHA-256 identifiers before they can reach a public preview.
Names are omitted unless ``include_names=True`` is explicitly requested.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([\w.+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,})(?![\w.-])"
)
_HASH_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_REGISTRATION_FIELDS = ("name", "phone", "institution", "status", "issues")
_SCHEDULE_FIELDS = ("start", "end", "title", "room", "speaker")


@dataclass(frozen=True, slots=True)
class ImpactRun:
    """Inputs from one validated/generated run.

    The fields intentionally accept the project's existing result objects as
    well as simple iterables or mappings.  Keeping this small adapter object
    means a future CLI integration does not need to change the comparison
    model.  ``files`` may contain :class:`eventkit.output.FileAction` values,
    file metadata mappings, or a mapping of relative paths to bytes.
    """

    roster: Any = field(default=None, repr=False)
    schedule: Any = field(default=None, repr=False)
    attendance: Any = field(default=None, repr=False)
    certificates: Any = field(default=None, repr=False)
    files: Any = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class RegistrationChange:
    """One privacy-safe registration addition, removal, or change."""

    kind: str
    subject_id: str
    current_row: int | None = None
    previous_row: int | None = None
    changed_fields: tuple[str, ...] = ()
    name: str | None = None

    @property
    def subject(self) -> str:
        """Alias for callers that use a generic subject vocabulary."""

        return self.subject_id


@dataclass(frozen=True, slots=True)
class RegistrationDiff:
    """Deterministic registration changes between two roster snapshots."""

    added: tuple[RegistrationChange, ...] = ()
    removed: tuple[RegistrationChange, ...] = ()
    changed: tuple[RegistrationChange, ...] = ()
    current_count: int | None = None
    previous_count: int | None = None
    available: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


@dataclass(frozen=True, slots=True)
class ScheduleChange:
    """One privacy-safe schedule addition, removal, or change."""

    kind: str
    title: str
    current_row: int | None = None
    previous_row: int | None = None
    changed_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleClash:
    """One unique room or speaker clash pair."""

    code: str
    row_numbers: tuple[int, int]

    @property
    def kind(self) -> str:
        return "room" if self.code == "SC02" else "speaker"


@dataclass(frozen=True, slots=True)
class ScheduleDiff:
    """Schedule changes plus current and newly introduced clash pairs."""

    added: tuple[ScheduleChange, ...] = ()
    removed: tuple[ScheduleChange, ...] = ()
    changed: tuple[ScheduleChange, ...] = ()
    clashes: tuple[ScheduleClash, ...] = ()
    clashes_added: tuple[ScheduleClash, ...] = ()
    clashes_removed: tuple[ScheduleClash, ...] = ()
    current_count: int | None = None
    previous_count: int | None = None
    available: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.changed
            or self.clashes_added
            or self.clashes_removed
        )


@dataclass(frozen=True, slots=True)
class CertificateChange:
    """One certificate code or recipient change without email data."""

    kind: str
    subject_id: str
    current_code: str | None = None
    previous_code: str | None = None
    current_role: str | None = None
    previous_role: str | None = None

    @property
    def subject(self) -> str:
        return self.subject_id


@dataclass(frozen=True, slots=True)
class CertificateDiff:
    """Certificate count and code changes between two snapshots."""

    added: tuple[CertificateChange, ...] = ()
    removed: tuple[CertificateChange, ...] = ()
    changed: tuple[CertificateChange, ...] = ()
    current_count: int | None = None
    previous_count: int | None = None
    codes_available: bool = False
    available: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.changed
            or (
                self.current_count is not None
                and self.previous_count is not None
                and self.current_count != self.previous_count
            )
        )

    @property
    def added_codes(self) -> tuple[str, ...]:
        return tuple(change.current_code or change.subject_id for change in self.added)

    @property
    def removed_codes(self) -> tuple[str, ...]:
        return tuple(change.previous_code or change.subject_id for change in self.removed)


@dataclass(frozen=True, slots=True)
class FileChange:
    """One generated-file action shown by the preview."""

    relative_path: str
    action: str
    size: int | None = None
    sha256: str | None = None
    step: str = ""

    @property
    def path(self) -> str:
        return self.relative_path


@dataclass(frozen=True, slots=True)
class FileDiff:
    """Stable file actions for the current output plan."""

    actions: tuple[FileChange, ...] = ()

    @property
    def has_changes(self) -> bool:
        return any(action.action != "unchanged" for action in self.actions)


@dataclass(frozen=True, slots=True)
class ImpactPreview:
    """The structured result and renderer for an event impact comparison."""

    registrations: RegistrationDiff
    schedule: ScheduleDiff
    certificates: CertificateDiff
    files: FileDiff
    baseline_available: bool = False
    include_names: bool = False

    @property
    def has_changes(self) -> bool:
        return (
            self.registrations.has_changes
            or self.schedule.has_changes
            or self.certificates.has_changes
            or self.files.has_changes
        )

    def render_markdown(self) -> str:
        """Render a stable, human-readable Markdown preview."""

        lines = ["# Event impact preview", ""]
        if not self.baseline_available:
            lines.extend(
                (
                    "Baseline: not supplied; current-state summaries are shown where available.",
                    "",
                )
            )
        lines.extend(("## Registrations", ""))
        _render_registrations(lines, self.registrations)
        lines.extend(("", "## Schedule", ""))
        _render_schedule(lines, self.schedule)
        lines.extend(("", "## Certificates", ""))
        _render_certificates(lines, self.certificates)
        lines.extend(("", "## File actions", ""))
        _render_files(lines, self.files)
        return "\n".join(lines).rstrip() + "\n"

    def render_text(self) -> str:
        """Return the same deterministic content for text-oriented callers."""

        return self.render_markdown()

    def to_markdown(self) -> str:
        """Alias for :meth:`render_markdown`."""

        return self.render_markdown()

    def __str__(self) -> str:
        return self.render_markdown()


@dataclass(frozen=True, slots=True)
class _RegistrationRecord:
    key: tuple[str, int | str]
    subject_id: str
    row: int
    name: str
    snapshot: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    row: int
    start: str
    end: str
    title: str
    room: str
    speaker: str

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        return (self.title.casefold(), self.room.casefold(), self.speaker.casefold())


@dataclass(frozen=True, slots=True)
class _CertificateRecord:
    key: tuple[str, int | str]
    subject_id: str
    code: str | None
    role: str


def pseudonymous_id(value: str, *, namespace: str = "registration") -> str:
    """Return a stable non-reversible identifier for a private comparison key."""

    normalized = str(value).strip().casefold()
    if not normalized:
        raise ValueError("value must not be empty")
    digest = hashlib.sha256((namespace + "\0" + normalized).encode("utf-8")).hexdigest()
    prefix = {"registration": "R", "certificate": "C"}.get(namespace, "I")
    return f"{prefix}-{digest[:12].upper()}"


def _text(value: Any) -> str:
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):
        value = value.value
    return "" if value is None else str(value).strip()


def _safe_text(value: Any) -> str:
    """Remove email-shaped text and line breaks from rendered values."""

    text = _EMAIL_RE.sub("[redacted-email]", _text(value))
    return " ".join(text.split())


def _value(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _rows(value: Any, attribute: str = "rows") -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = value.get(attribute, value.get("entries", ()))
    else:
        value = getattr(value, attribute, value)
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    return tuple(value)


def _roster_records(value: Any) -> tuple[_RegistrationRecord, ...]:
    records: list[_RegistrationRecord] = []
    occurrences: Counter[str] = Counter()
    for position, row in enumerate(_rows(value)):
        attendee = _value(row, "attendee", row)
        row_number = _value(row, "row_number", _value(attendee, "row_number", position + 1))
        try:
            row_number = int(row_number)
        except (TypeError, ValueError):
            row_number = position + 1
        email = (
            _text(_value(attendee, "normalized_email", ""))
            or _text(_value(attendee, "email", ""))
        ).casefold()
        if email:
            identity: tuple[str, int | str] = ("email", email)
            occurrences[email] += 1
            suffix = occurrences[email]
            subject_id = pseudonymous_id(email)
            if suffix > 1:
                subject_id += f"-{suffix}"
            identity = ("email", f"{email}\0{suffix}")
        else:
            identity = ("row", row_number)
            subject_id = f"R-ROW-{row_number:04d}"
        normalized_name = _text(_value(attendee, "normalized_name", "")) or _text(
            _value(attendee, "name", "")
        )
        normalized_phone = _text(_value(attendee, "normalized_phone", "")) or _text(
            _value(attendee, "phone", "")
        )
        normalized_institution = _text(
            _value(attendee, "normalized_institution", "")
        ) or _text(_value(attendee, "institution", ""))
        status = _text(_value(row, "status", ""))
        issue_ids = _value(row, "issue_ids", ())
        if isinstance(issue_ids, str):
            issue_text = issue_ids
        else:
            issue_text = ",".join(sorted(_text(issue) for issue in issue_ids))
        snapshot = tuple(
            (
                field_name,
                value,
            )
            for field_name, value in (
                ("name", normalized_name),
                ("phone", normalized_phone),
                ("institution", normalized_institution),
                ("status", status),
                ("issues", issue_text),
            )
        )
        records.append(_RegistrationRecord(identity, subject_id, row_number, normalized_name, snapshot))
    return tuple(records)


def _registration_diff(
    current: Any,
    previous: Any,
    *,
    include_names: bool,
) -> RegistrationDiff:
    current_records = _roster_records(current)
    previous_records = _roster_records(previous)
    if current is None and previous is None:
        return RegistrationDiff()
    if previous is None:
        return RegistrationDiff(
            current_count=len(current_records),
            previous_count=None,
            available=False,
        )
    if current is None:
        return RegistrationDiff(
            current_count=None,
            previous_count=len(previous_records),
            available=True,
        )

    current_by_key = {record.key: record for record in current_records}
    previous_by_key = {record.key: record for record in previous_records}
    added: list[RegistrationChange] = []
    removed: list[RegistrationChange] = []
    changed: list[RegistrationChange] = []
    for key in sorted(current_by_key, key=str):
        record = current_by_key[key]
        if key not in previous_by_key:
            added.append(
                RegistrationChange(
                    "added",
                    record.subject_id,
                    current_row=record.row,
                    name=_safe_text(record.name) if include_names else None,
                )
            )
            continue
        prior = previous_by_key[key]
        before = dict(prior.snapshot)
        after = dict(record.snapshot)
        fields = tuple(field_name for field_name in _REGISTRATION_FIELDS if before.get(field_name) != after.get(field_name))
        if fields:
            changed.append(
                RegistrationChange(
                    "changed",
                    record.subject_id,
                    current_row=record.row,
                    previous_row=prior.row,
                    changed_fields=fields,
                    name=_safe_text(record.name) if include_names else None,
                )
            )
    for key in sorted(previous_by_key, key=str):
        if key not in current_by_key:
            record = previous_by_key[key]
            removed.append(
                RegistrationChange(
                    "removed",
                    record.subject_id,
                    previous_row=record.row,
                    name=_safe_text(record.name) if include_names else None,
                )
            )
    return RegistrationDiff(
        tuple(added),
        tuple(removed),
        tuple(changed),
        current_count=len(current_records),
        previous_count=len(previous_records),
        available=True,
    )


def _datetime_value(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _text(value)


def _session_records(value: Any) -> tuple[_SessionRecord, ...]:
    sessions = _rows(value, "sessions") if value is not None else ()
    records: list[_SessionRecord] = []
    for position, session in enumerate(sessions):
        row = _value(session, "row_number", position + 1)
        try:
            row = int(row)
        except (TypeError, ValueError):
            row = position + 1
        records.append(
            _SessionRecord(
                row=row,
                start=_datetime_value(_value(session, "start", _value(session, "start_local", ""))),
                end=_datetime_value(_value(session, "end", _value(session, "end_local", ""))),
                title=_text(_value(session, "title", "")),
                room=_text(_value(session, "room", "")),
                speaker=_text(_value(session, "speaker", "")),
            )
        )
    return tuple(records)


def _session_issues(value: Any, sessions: tuple[_SessionRecord, ...]) -> tuple[Any, ...]:
    issues = _value(value, "issues", ())
    if issues:
        return tuple(issues)
    if not sessions:
        return ()
    try:
        from .schedule import detect_clashes

        if hasattr(value, "sessions"):
            source = value.sessions
        elif isinstance(value, Mapping):
            source = value.get("sessions", ())
        elif isinstance(value, (tuple, list)):
            source = value
        else:
            source = ()
        return tuple(detect_clashes(source))
    except (AttributeError, TypeError, ValueError):
        return ()


def _clashes(value: Any, sessions: tuple[_SessionRecord, ...]) -> tuple[ScheduleClash, ...]:
    result: set[tuple[str, tuple[int, int]]] = set()
    for issue in _session_issues(value, sessions):
        code = _text(_value(issue, "code", ""))
        if code not in {"SC02", "SC03"}:
            continue
        row = _value(issue, "row_number", _value(issue, "row", 0))
        related = _value(issue, "related_row_number", 0)
        try:
            pair = tuple(sorted((int(row), int(related))))
        except (TypeError, ValueError):
            continue
        if pair[0] <= 0 or pair[1] <= 0 or pair[0] == pair[1]:
            continue
        result.add((code, (pair[0], pair[1])))
    return tuple(ScheduleClash(code, rows) for code, rows in sorted(result))


def _schedule_diff(current: Any, previous: Any) -> ScheduleDiff:
    current_records = _session_records(current)
    previous_records = _session_records(previous)
    current_clashes = _clashes(current, current_records)
    previous_clashes = _clashes(previous, previous_records)
    if current is None and previous is None:
        return ScheduleDiff()
    if previous is None:
        return ScheduleDiff(
            clashes=current_clashes,
            current_count=len(current_records),
            previous_count=None,
            available=False,
        )
    if current is None:
        return ScheduleDiff(
            clashes=(),
            clashes_removed=previous_clashes,
            current_count=None,
            previous_count=len(previous_records),
            available=True,
        )

    unmatched_previous = set(range(len(previous_records)))
    matches: list[tuple[_SessionRecord, _SessionRecord]] = []
    for record in current_records:
        candidate = next(
            (index for index in unmatched_previous if previous_records[index].row == record.row),
            None,
        )
        if candidate is None:
            candidates = [
                index
                for index in unmatched_previous
                if previous_records[index].fingerprint == record.fingerprint
            ]
            if len(candidates) == 1:
                candidate = candidates[0]
        if candidate is not None:
            unmatched_previous.remove(candidate)
            matches.append((record, previous_records[candidate]))
        else:
            matches.append((record, None))  # type: ignore[arg-type]

    added: list[ScheduleChange] = []
    changed: list[ScheduleChange] = []
    for record, prior in matches:
        if prior is None:
            added.append(ScheduleChange("added", _safe_text(record.title), current_row=record.row))
            continue
        before = (prior.start, prior.end, prior.title, prior.room, prior.speaker)
        after = (record.start, record.end, record.title, record.room, record.speaker)
        fields = tuple(
            field_name
            for field_name, before_value, after_value in zip(_SCHEDULE_FIELDS, before, after)
            if before_value != after_value
        )
        if fields:
            changed.append(
                ScheduleChange(
                    "changed",
                    _safe_text(record.title),
                    current_row=record.row,
                    previous_row=prior.row,
                    changed_fields=fields,
                )
            )
    removed = [
        ScheduleChange("removed", _safe_text(previous_records[index].title), previous_row=previous_records[index].row)
        for index in sorted(unmatched_previous, key=lambda item: previous_records[item].row)
    ]
    current_clash_keys = {(item.code, item.row_numbers) for item in current_clashes}
    previous_clash_keys = {(item.code, item.row_numbers) for item in previous_clashes}
    clashes_added = tuple(item for item in current_clashes if (item.code, item.row_numbers) not in previous_clash_keys)
    clashes_removed = tuple(item for item in previous_clashes if (item.code, item.row_numbers) not in current_clash_keys)
    added.sort(key=lambda item: (item.current_row or 0, item.title))
    changed.sort(key=lambda item: (item.current_row or 0, item.title))
    return ScheduleDiff(
        tuple(added),
        tuple(removed),
        tuple(changed),
        current_clashes,
        clashes_added,
        clashes_removed,
        current_count=len(current_records),
        previous_count=len(previous_records),
        available=True,
    )


def _certificate_items(value: Any) -> tuple[Any, ...] | None:
    if value is None:
        return None
    if isinstance(value, Mapping) and "certificates" in value:
        return tuple(value["certificates"])
    if hasattr(value, "certificates"):
        return tuple(value.certificates)
    if hasattr(value, "recipients"):
        return tuple(value.recipients)
    if isinstance(value, (str, bytes)):
        return (value,)
    return tuple(value)


def _certificate_records(value: Any) -> tuple[tuple[_CertificateRecord, ...], bool]:
    items = _certificate_items(value)
    if items is None:
        return (), False
    records: list[_CertificateRecord] = []
    occurrences: Counter[str] = Counter()
    for item in items:
        code_value = _value(item, "code", None)
        hash_value = _value(item, "code_sha256", None)
        email = _text(_value(item, "email", "")).casefold()
        role = _text(_value(item, "role", ""))
        if hash_value:
            display_code = f"hash:{_text(hash_value)[:12].lower()}"
            identity = ("code", _text(hash_value).lower())
            subject_id = f"C-{_text(hash_value)[:12].upper()}"
            codes_available = True
        elif code_value:
            display_code = _safe_text(code_value)
            codes_available = True
            if email:
                occurrences[email] += 1
                occurrence = occurrences[email]
                identity = ("email", f"{email}\0{occurrence}")
                subject_id = pseudonymous_id(email, namespace="certificate")
                if occurrence > 1:
                    subject_id += f"-{occurrence}"
            else:
                identity = ("code", display_code.casefold())
                subject_id = f"C-{hashlib.sha256(display_code.casefold().encode('utf-8')).hexdigest()[:12].upper()}"
        else:
            # Attendance recipients are useful for count comparisons even
            # when the caller did not provide the generated certificate codes.
            codes_available = False
            if email:
                occurrences[email] += 1
                occurrence = occurrences[email]
                identity = ("email", f"{email}\0{occurrence}")
                subject_id = pseudonymous_id(email)
                if occurrence > 1:
                    subject_id += f"-{occurrence}"
            else:
                identity = ("recipient", len(records))
                subject_id = f"C-ROW-{len(records) + 1:04d}"
            display_code = None
        records.append(_CertificateRecord(identity, subject_id, display_code, role))
    return tuple(records), any(record.code is not None for record in records)


def _certificate_diff(current: Any, previous: Any) -> CertificateDiff:
    current_records, current_codes = _certificate_records(current)
    previous_records, previous_codes = _certificate_records(previous)
    current_available = current is not None
    previous_available = previous is not None
    if not current_available and not previous_available:
        return CertificateDiff()
    if not previous_available:
        return CertificateDiff(
            current_count=len(current_records),
            previous_count=None,
            codes_available=current_codes,
            available=False,
        )
    if not current_available:
        return CertificateDiff(
            current_count=None,
            previous_count=len(previous_records),
            codes_available=previous_codes,
            available=True,
        )

    current_by_key = {record.key: record for record in current_records}
    previous_by_key = {record.key: record for record in previous_records}
    added: list[CertificateChange] = []
    removed: list[CertificateChange] = []
    changed: list[CertificateChange] = []
    for key in sorted(current_by_key, key=str):
        record = current_by_key[key]
        prior = previous_by_key.get(key)
        if prior is None:
            added.append(CertificateChange("added", record.subject_id, current_code=record.code, current_role=record.role))
            continue
        if record.code != prior.code or record.role != prior.role:
            changed.append(
                CertificateChange(
                    "changed",
                    record.subject_id,
                    current_code=record.code,
                    previous_code=prior.code,
                    current_role=record.role,
                    previous_role=prior.role,
                )
            )
    for key in sorted(previous_by_key, key=str):
        if key not in current_by_key:
            record = previous_by_key[key]
            removed.append(CertificateChange("removed", record.subject_id, previous_code=record.code, previous_role=record.role))
    return CertificateDiff(
        tuple(added),
        tuple(removed),
        tuple(changed),
        current_count=len(current_records),
        previous_count=len(previous_records),
        codes_available=current_codes or previous_codes,
        available=True,
    )


def _audit_hashes(source: Any) -> dict[str, str]:
    """Read only path/hash pairs from an audit mapping, JSONL string, or path."""

    if source is None:
        return {}
    raw: Any = source
    if isinstance(source, (str, Path)):
        try:
            is_json_text = isinstance(source, str) and source.lstrip().startswith(("{", "["))
            if isinstance(source, Path) or ("\n" not in source and not is_json_text):
                raw = Path(source).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return {}
    if isinstance(raw, Mapping):
        if "files" in raw:
            raw = raw["files"]
        elif "path" in raw and "sha256" in raw:
            raw = (raw,)
        else:
            result: dict[str, str] = {}
            for path, value in raw.items():
                path_text = _text(path)
                if isinstance(value, bytes):
                    digest = _hash_bytes(value)
                else:
                    digest = _text(value)
                    if not _HASH_RE.fullmatch(digest):
                        digest = _hash_bytes(digest.encode("utf-8")) if digest else ""
                if path_text and digest:
                    result[path_text] = digest.lower()
            return result
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw) if raw.lstrip().startswith(("[", "{")) else [json.loads(line) for line in raw.splitlines() if line.strip()]
        except (json.JSONDecodeError, TypeError):
            return {}
        raw = decoded if isinstance(decoded, list) else (decoded,)
    try:
        records = tuple(raw)
    except TypeError:
        return {}
    result: dict[str, str] = {}
    for record in records:
        if isinstance(record, Mapping):
            path = _text(record.get("path", record.get("relative_path", "")))
            digest = _text(record.get("sha256", ""))
        elif hasattr(record, "relative_path"):
            path = _text(getattr(record, "relative_path"))
            digest = _text(getattr(record, "sha256", ""))
        else:
            continue
        if path and _HASH_RE.fullmatch(digest):
            result[path] = digest.lower()
    return result


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_change(path: str, raw: Any, previous_hashes: Mapping[str, str]) -> tuple[FileChange, bool]:
    if isinstance(raw, Mapping):
        relative_path = _text(raw.get("relative_path", raw.get("path", path)))
        action = _text(raw.get("action", ""))
        size_value = raw.get("size")
        digest = _text(raw.get("sha256", "")) or None
        content = raw.get("content")
        if isinstance(content, bytes):
            digest = _hash_bytes(content)
            size_value = len(content)
        size = int(size_value) if size_value is not None else None
        step = _text(raw.get("step", ""))
    elif hasattr(raw, "relative_path"):
        relative_path = _text(getattr(raw, "relative_path"))
        action = _text(getattr(raw, "action", ""))
        size = getattr(raw, "size", None)
        size = int(size) if size is not None else None
        digest = _text(getattr(raw, "sha256", "")) or None
        step = _text(getattr(raw, "step", ""))
    else:
        relative_path = path
        action = ""
        step = ""
        if isinstance(raw, bytes):
            digest = _hash_bytes(raw)
            size = len(raw)
        elif isinstance(raw, str) and _HASH_RE.fullmatch(raw):
            digest = raw.lower()
            size = None
        else:
            encoded = _text(raw).encode("utf-8")
            digest = _hash_bytes(encoded)
            size = len(encoded)
    if not action:
        if relative_path not in previous_hashes:
            action = "create"
        elif digest == previous_hashes[relative_path]:
            action = "unchanged"
        else:
            action = "update"
    return FileChange(relative_path, action, size, digest, step), hasattr(raw, "relative_path") or isinstance(raw, Mapping) and bool(raw.get("action"))


def _file_diff(current: Any, previous_audit: Any) -> FileDiff:
    previous_hashes = _audit_hashes(previous_audit)
    if current is None:
        return FileDiff()
    if isinstance(current, Mapping):
        if "path" in current or "relative_path" in current:
            raw_items = (("", current),)
        else:
            raw_items = tuple(current.items())
    elif isinstance(current, (str, bytes)):
        raw_items = (("", current),)
    else:
        try:
            raw_items = tuple(("", item) for item in current)
        except TypeError:
            raw_items = (("", current),)
    actions: list[FileChange] = []
    explicit = False
    current_paths: set[str] = set()
    for path, raw in raw_items:
        action, was_explicit = _file_change(_text(path), raw, previous_hashes)
        actions.append(action)
        explicit = explicit or was_explicit
        current_paths.add(action.relative_path)
    if previous_hashes and not explicit:
        for path, digest in sorted(previous_hashes.items()):
            if path not in current_paths:
                actions.append(FileChange(path, "delete", sha256=digest))
    actions.sort(key=lambda item: (item.relative_path, item.action, item.step))
    return FileDiff(tuple(actions))


def build_impact_preview(
    current: ImpactRun,
    previous: ImpactRun | None = None,
    *,
    previous_audit: Any = None,
    include_names: bool = False,
) -> ImpactPreview:
    """Compare two runs and return a structured, privacy-safe preview.

    ``previous_audit`` is optional and is used for file hashes when the
    current run supplies file contents or metadata without explicit actions.
    It may be an ``audit.jsonl`` path, its JSONL text, a path-to-hash mapping,
    or an iterable of audit records.  Audit metadata cannot identify people,
    so it contributes file actions only.
    """

    if not isinstance(current, ImpactRun):
        current = ImpactRun(roster=current)
    if previous is not None and not isinstance(previous, ImpactRun):
        previous = ImpactRun(roster=previous)
    prior = previous or ImpactRun()
    baseline_available = previous is not None
    current_certificates = current.certificates
    if current_certificates is None and current.attendance is not None:
        current_certificates = current.attendance
    previous_certificates = prior.certificates
    if previous_certificates is None and prior.attendance is not None:
        previous_certificates = prior.attendance
    audit_source = previous_audit
    if audit_source is None and previous is not None:
        audit_source = prior.files
    return ImpactPreview(
        registrations=_registration_diff(current.roster, prior.roster, include_names=include_names),
        schedule=_schedule_diff(current.schedule, prior.schedule),
        certificates=_certificate_diff(current_certificates, previous_certificates),
        files=_file_diff(current.files, audit_source),
        baseline_available=baseline_available,
        include_names=include_names,
    )


def compare_runs(
    current: ImpactRun,
    previous: ImpactRun | None = None,
    *,
    previous_audit: Any = None,
    include_names: bool = False,
) -> ImpactPreview:
    """Readable alias for :func:`build_impact_preview`."""

    return build_impact_preview(
        current,
        previous,
        previous_audit=previous_audit,
        include_names=include_names,
    )


def render_impact_preview(
    current: ImpactRun,
    previous: ImpactRun | None = None,
    *,
    previous_audit: Any = None,
    include_names: bool = False,
) -> str:
    """Build and render a Markdown impact preview in one call."""

    return compare_runs(
        current,
        previous,
        previous_audit=previous_audit,
        include_names=include_names,
    ).render_markdown()


def _render_registrations(lines: list[str], diff: RegistrationDiff) -> None:
    if not diff.available:
        if diff.current_count is None:
            lines.append("No registration snapshot supplied.")
        else:
            lines.append(f"No prior registration snapshot supplied (current rows: {diff.current_count}).")
        return
    if not diff.has_changes:
        lines.append("No changes.")
        return
    lines.extend(
        (
            f"- Added: {len(diff.added)}",
            f"- Removed: {len(diff.removed)}",
            f"- Changed: {len(diff.changed)}",
        )
    )
    for change in diff.added:
        lines.append(_registration_line("Added", change))
    for change in diff.removed:
        lines.append(_registration_line("Removed", change))
    for change in diff.changed:
        lines.append(_registration_line("Changed", change))


def _registration_line(prefix: str, change: RegistrationChange) -> str:
    row = (
        f"row {change.current_row}"
        if prefix != "Removed"
        else f"previous row {change.previous_row}"
    )
    if prefix == "Changed":
        row = f"row {change.current_row} (previous row {change.previous_row})"
    name = f" — {change.name}" if change.name else ""
    fields = f" — fields: {', '.join(change.changed_fields)}" if change.changed_fields else ""
    return f"- {prefix} — {row} — {change.subject_id}{name}{fields}"


def _render_schedule(lines: list[str], diff: ScheduleDiff) -> None:
    if not diff.available:
        if diff.current_count is None:
            lines.append("No schedule snapshot supplied.")
        else:
            lines.append(f"No prior schedule snapshot supplied (current sessions: {diff.current_count}).")
    elif not diff.has_changes:
        lines.append("No changes.")
    else:
        lines.extend(
            (
                f"- Added sessions: {len(diff.added)}",
                f"- Removed sessions: {len(diff.removed)}",
                f"- Changed sessions: {len(diff.changed)}",
                f"- New clashes: {len(diff.clashes_added)}",
                f"- Resolved clashes: {len(diff.clashes_removed)}",
            )
        )
        for change in diff.added:
            lines.append(f"- Added session — row {change.current_row} — {change.title}")
        for change in diff.removed:
            lines.append(f"- Removed session — previous row {change.previous_row} — {change.title}")
        for change in diff.changed:
            lines.append(
                f"- Changed session — row {change.current_row} (previous row {change.previous_row})"
                f" — {change.title} — fields: {', '.join(change.changed_fields)}"
            )
        for clash in diff.clashes_added:
            lines.append(f"- New {_clash_line(clash)}")
        for clash in diff.clashes_removed:
            lines.append(f"- Resolved {_clash_line(clash)}")
    if diff.clashes:
        lines.append(f"- Current clashes: {_clash_summary(diff.clashes)}")
    elif diff.available:
        lines.append("- Current clashes: none.")


def _clash_line(clash: ScheduleClash) -> str:
    first, second = clash.row_numbers
    return f"{clash.kind} clash ({clash.code}; rows {first} and {second})."


def _clash_summary(clashes: Iterable[ScheduleClash]) -> str:
    groups: dict[str, list[ScheduleClash]] = {"SC02": [], "SC03": []}
    for clash in clashes:
        groups.setdefault(clash.code, []).append(clash)
    parts: list[str] = []
    for code in ("SC02", "SC03"):
        entries = groups.get(code, [])
        if not entries:
            continue
        pairs = ", ".join(f"{left} and {right}" for item in entries for left, right in (item.row_numbers,))
        kind = "room" if code == "SC02" else "speaker"
        noun = "clash" if len(entries) == 1 else "clashes"
        parts.append(f"{len(entries)} {kind} {noun} ({code}; rows {pairs})")
    return "; ".join(parts)


def _render_certificates(lines: list[str], diff: CertificateDiff) -> None:
    if not diff.available:
        if diff.current_count is None:
            lines.append("No certificate or attendance snapshot supplied.")
        else:
            lines.append(f"No prior certificate snapshot supplied (current certificates: {diff.current_count}).")
        if not diff.codes_available:
            lines.append("Certificate codes: unavailable in the supplied snapshot.")
        return
    count_text = f"Certificates: {diff.current_count} (previously {diff.previous_count})."
    lines.append(f"- {count_text}")
    if not diff.codes_available:
        lines.append("- Certificate codes: unavailable in the supplied snapshot.")
    if not diff.has_changes:
        lines.append("No changes.")
        return
    for change in diff.added:
        lines.append(f"- Added code — {_certificate_code(change.current_code, change.subject_id)}")
    for change in diff.removed:
        lines.append(f"- Removed code — {_certificate_code(change.previous_code, change.subject_id)}")
    for change in diff.changed:
        before = _certificate_code(change.previous_code, change.subject_id)
        after = _certificate_code(change.current_code, change.subject_id)
        role = ""
        if change.current_role != change.previous_role:
            role = f" — role: {_safe_text(change.previous_role)} -> {_safe_text(change.current_role)}"
        lines.append(f"- Changed code — {change.subject_id} — {before} -> {after}{role}")


def _certificate_code(code: str | None, subject_id: str) -> str:
    return _safe_text(code) if code else f"recipient {subject_id}"


def _render_files(lines: list[str], diff: FileDiff) -> None:
    if not diff.actions:
        lines.append("No file actions supplied.")
        return
    if not diff.has_changes:
        lines.append("- No changes.")
    for action in diff.actions:
        path = _safe_text(action.relative_path).replace("`", "'")
        lines.append(f"- {_safe_text(action.action)}: `{path}`")


build_preview = build_impact_preview
render_preview = render_impact_preview


__all__ = [
    "CertificateChange",
    "CertificateDiff",
    "FileChange",
    "FileDiff",
    "ImpactPreview",
    "ImpactRun",
    "RegistrationChange",
    "RegistrationDiff",
    "ScheduleChange",
    "ScheduleClash",
    "ScheduleDiff",
    "build_impact_preview",
    "build_preview",
    "compare_runs",
    "pseudonymous_id",
    "render_impact_preview",
    "render_preview",
]
