"""Offline event-day check-in log and registry primitives.

The module deliberately has no dependency on the rest of :mod:`eventkit` so it
can be used by an event-day tool without a network connection.  A
``CheckinRecord`` is an immutable log entry.  ``CheckinRegistry`` applies
those entries to an in-memory state and can export the append-only JSONL log
that produced that state.

The public HTML renderer accepts public attendee data separately from
``private_data``.  Email-like fields are removed from public attendee data;
private data is included only when the caller explicitly supplies it under
that named argument.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import escape
import json
from typing import Any


SCHEMA_VERSION = 1
"""Current on-disk event record schema version."""

ACTIONS = (
    "check_in",
    "check_out",
    "session_check_in",
    "session_check_out",
    "manual_override",
)
"""Actions accepted by :class:`CheckinRecord`."""

_TRANSITION_ACTIONS = frozenset(ACTIONS[:-1])
_CHECK_IN_ACTIONS = frozenset({"check_in", "session_check_in"})
_CHECK_OUT_ACTIONS = frozenset({"check_out", "session_check_out"})
_PRIVATE_PUBLIC_KEY_PARTS = frozenset(
    {
        "email",
        "emails",
        "emailaddress",
        "emailaddresses",
        "email_address",
        "email_addresses",
    }
)


class CheckinRuntimeError(ValueError):
    """Base error for invalid or conflicting check-in operations."""


class DuplicateCheckinError(CheckinRuntimeError):
    """Raised when a person is checked in twice for the same scope."""


class UnknownCheckinCodeError(CheckinRuntimeError):
    """Raised when a record uses a code not authorized by the registry."""


class InvalidTransitionError(CheckinRuntimeError):
    """Raised when a check-out has no corresponding active check-in."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _normalize_action(value: object, field: str = "action") -> str:
    action = _text(value, field).lower().replace(" ", "_")
    if action not in ACTIONS:
        allowed = ", ".join(ACTIONS)
        raise ValueError(f"{field} must be one of: {allowed}")
    return action


@dataclass(frozen=True, slots=True)
class CheckinRecord:
    """One deterministic, append-only event-day log record.

    ``attendee_id`` is the stable attendee/person identifier.  A record may
    instead use ``checkin_code``; a registry resolves a known code to its
    attendee ID.  Supplying both is useful when a code is being scanned and
    the caller has already resolved it.

    ``manual_override`` records require ``reason`` and ``override_action``.
    The latter states whether the correction sets the event or session state
    to checked in or checked out.  It is intentionally part of the record so
    importing a log never depends on an implicit toggle.
    """

    event_id: str
    action: str
    operator: str
    sequence: int
    attendee_id: str | None = None
    checkin_code: str | None = None
    schema_version: int = SCHEMA_VERSION
    session_id: str | None = None
    reason: str | None = None
    device_id: str | None = None
    override_action: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "operator", _text(self.operator, "operator"))
        object.__setattr__(self, "action", _normalize_action(self.action))

        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ValueError("sequence must be an integer")
        if self.sequence < 1:
            raise ValueError("sequence must be greater than zero")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION}"
            )

        attendee_id = _optional_text(self.attendee_id, "attendee_id")
        checkin_code = _optional_text(self.checkin_code, "checkin_code")
        session_id = _optional_text(self.session_id, "session_id")
        reason = _optional_text(self.reason, "reason")
        device_id = _optional_text(self.device_id, "device_id")
        override_action = (
            None
            if self.override_action is None
            else _normalize_action(self.override_action, "override_action")
        )

        if attendee_id is None and checkin_code is None:
            raise ValueError("attendee_id or checkin_code is required")
        if self.action.startswith("session_") and session_id is None:
            raise ValueError("session_id is required for session actions")
        if self.action in _TRANSITION_ACTIONS and override_action is not None:
            raise ValueError("override_action is only valid for manual_override")
        if self.action == "manual_override":
            if reason is None:
                raise ValueError("manual_override requires a reason")
            if override_action not in _TRANSITION_ACTIONS:
                raise ValueError(
                    "manual_override requires override_action to be a check-in action"
                    " or check-out action"
                )
            if override_action.startswith("session_") and session_id is None:
                raise ValueError(
                    "session_id is required for a session manual override"
                )
            if not override_action.startswith("session_") and session_id is not None:
                raise ValueError(
                    "session_id is only valid for a session manual override"
                )

        object.__setattr__(self, "attendee_id", attendee_id)
        object.__setattr__(self, "checkin_code", checkin_code)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "override_action", override_action)

    @property
    def person_id(self) -> str | None:
        """Alias for callers whose domain calls attendees ``person`` records."""

        return self.attendee_id

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible representation of this record.

        Optional fields are omitted when absent, keeping JSONL compact while
        making their optional nature explicit in the schema.
        """

        data: dict[str, object] = {
            "action": self.action,
            "attendee_id": self.attendee_id,
            "event_id": self.event_id,
            "operator": self.operator,
            "schema_version": self.schema_version,
            "sequence": self.sequence,
        }
        if self.attendee_id is None:
            del data["attendee_id"]
        if self.checkin_code is not None:
            data["checkin_code"] = self.checkin_code
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.reason is not None:
            data["reason"] = self.reason
        if self.device_id is not None:
            data["device_id"] = self.device_id
        if self.override_action is not None:
            data["override_action"] = self.override_action
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CheckinRecord":
        """Build a validated record from a decoded JSON object."""

        if not isinstance(data, Mapping):
            raise ValueError("event log record must be a JSON object")
        attendee_id = data.get("attendee_id")
        if attendee_id is None:
            attendee_id = data.get("person_id")
        if "schema_version" not in data:
            raise ValueError("event log record is missing schema_version")
        return cls(
            event_id=data.get("event_id"),  # type: ignore[arg-type]
            action=data.get("action"),  # type: ignore[arg-type]
            operator=data.get("operator"),  # type: ignore[arg-type]
            sequence=data.get("sequence"),  # type: ignore[arg-type]
            attendee_id=attendee_id,  # type: ignore[arg-type]
            checkin_code=data.get("checkin_code"),  # type: ignore[arg-type]
            schema_version=data.get("schema_version"),  # type: ignore[arg-type]
            session_id=data.get("session_id"),  # type: ignore[arg-type]
            reason=data.get("reason"),  # type: ignore[arg-type]
            device_id=data.get("device_id"),  # type: ignore[arg-type]
            override_action=data.get("override_action"),  # type: ignore[arg-type]
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _record_sort_key(record: CheckinRecord) -> tuple[object, ...]:
    return (
        record.event_id,
        record.sequence,
        record.device_id or "",
        _canonical_json(record.to_dict()),
    )


def serialize_event_record(record: CheckinRecord) -> str:
    """Serialize one record as canonical JSON without a trailing newline."""

    if not isinstance(record, CheckinRecord):
        raise TypeError("record must be a CheckinRecord")
    return _canonical_json(record.to_dict())


def serialize_event_log(records: Iterable[CheckinRecord]) -> str:
    """Serialize records as deterministic, newline-delimited JSON.

    Records are ordered by event, sequence, device, and canonical content.
    Thus exporting the same records from different iterable types produces
    identical bytes.  The returned string has one final newline when nonempty.
    """

    normalized = tuple(records)
    if not all(isinstance(record, CheckinRecord) for record in normalized):
        raise TypeError("records must contain CheckinRecord values")
    lines = [
        serialize_event_record(record)
        for record in sorted(normalized, key=_record_sort_key)
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def parse_event_log(data: str | bytes) -> tuple[CheckinRecord, ...]:
    """Parse and validate deterministic JSONL event records."""

    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("event log must be UTF-8 JSONL") from exc
    if not isinstance(data, str):
        raise TypeError("event log must be str or bytes")

    records: list[CheckinRecord] = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"event log line {line_number} is blank")
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"event log line {line_number} is not valid JSON") from exc
        try:
            records.append(CheckinRecord.from_dict(decoded))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid event log record on line {line_number}: {exc}") from exc
    return tuple(records)


def _records_from_source(
    source: str | bytes | Iterable[CheckinRecord],
) -> tuple[CheckinRecord, ...]:
    if isinstance(source, (str, bytes)):
        return parse_event_log(source)
    records = tuple(source)
    if not all(isinstance(record, CheckinRecord) for record in records):
        raise TypeError("log sources must contain CheckinRecord values")
    return records


def merge_event_records(
    *logs: str | bytes | Iterable[CheckinRecord],
) -> tuple[CheckinRecord, ...]:
    """Merge device logs into a deterministic, de-duplicated record tuple.

    Device-local sequence values are retained.  Ties are resolved by device
    ID and canonical record content, so argument order does not affect the
    result.  All logs must belong to the same event.
    """

    gathered: list[CheckinRecord] = []
    for log in logs:
        gathered.extend(_records_from_source(log))
    event_ids = {record.event_id for record in gathered}
    if len(event_ids) > 1:
        raise ValueError("cannot merge logs from different events")

    unique: dict[str, CheckinRecord] = {
        serialize_event_record(record): record for record in gathered
    }
    return tuple(sorted(unique.values(), key=_record_sort_key))


def merge_event_logs(*logs: str | bytes | Iterable[CheckinRecord]) -> str:
    """Return the deterministic JSONL result of merging device logs."""

    return serialize_event_log(merge_event_records(*logs))


def _choose_identity(
    attendee_id: str | None,
    person_id: str | None,
) -> str | None:
    if attendee_id is not None and person_id is not None and attendee_id != person_id:
        raise ValueError("attendee_id and person_id must identify the same person")
    return attendee_id if attendee_id is not None else person_id


class CheckinRegistry:
    """In-memory event-day state backed by immutable check-in records.

    ``known_codes`` may be a mapping from code to attendee/person ID, or an
    iterable of codes (where each code is also its identity).  Unknown codes
    are rejected by default.  They can be enabled explicitly for a registry
    with ``allow_unknown_codes=True`` or for one operation with
    ``allow_unknown_code=True``.
    """

    def __init__(
        self,
        event_id: str,
        known_codes: Mapping[str, str] | Iterable[str] = (),
        *,
        allow_unknown_codes: bool = False,
        device_id: str | None = None,
    ) -> None:
        self.event_id = _text(event_id, "event_id")
        self.allow_unknown_codes = bool(allow_unknown_codes)
        self.device_id = _optional_text(device_id, "device_id")
        self._known_codes: dict[str, str] = {}
        if isinstance(known_codes, Mapping):
            pairs = known_codes.items()
        else:
            pairs = ((code, code) for code in known_codes)
        for code, attendee_id in pairs:
            code_text = _text(code, "checkin_code")
            attendee_text = _text(attendee_id, "attendee_id")
            self._known_codes[code_text] = attendee_text

        self._checked_in: set[tuple[str, str | None]] = set()
        self._records: list[CheckinRecord] = []
        self._record_keys: set[str] = set()
        self._next_sequence = 1

    @property
    def records(self) -> tuple[CheckinRecord, ...]:
        """Records applied to this registry, in local append order."""

        return tuple(self._records)

    @property
    def known_codes(self) -> dict[str, str]:
        """A copy of the code-to-attendee mapping used for validation."""

        return dict(self._known_codes)

    def register_code(self, checkin_code: str, attendee_id: str) -> None:
        """Explicitly authorize a code for future records."""

        code = _text(checkin_code, "checkin_code")
        attendee = _text(attendee_id, "attendee_id")
        existing = self._known_codes.get(code)
        if existing is not None and existing != attendee:
            raise ValueError(f"checkin code {code!r} is already assigned")
        self._known_codes[code] = attendee

    def _resolve_record_identity(
        self,
        record: CheckinRecord,
        *,
        allow_unknown_code: bool = False,
    ) -> tuple[str, str | None]:
        if record.checkin_code is not None:
            known_attendee = self._known_codes.get(record.checkin_code)
            if known_attendee is None and not (
                allow_unknown_code or self.allow_unknown_codes
            ):
                raise UnknownCheckinCodeError(
                    f"unknown check-in code {record.checkin_code!r}; "
                    "pass allow_unknown_code=True to authorize it"
                )
            if known_attendee is not None:
                if record.attendee_id is not None and record.attendee_id != known_attendee:
                    raise CheckinRuntimeError(
                        f"check-in code {record.checkin_code!r} belongs to "
                        f"{known_attendee!r}, not {record.attendee_id!r}"
                    )
                return known_attendee, record.checkin_code
        if record.attendee_id is not None:
            return record.attendee_id, record.checkin_code
        # A code-only record is safe here because unknown-code authorization
        # was checked above and known codes have already returned their ID.
        assert record.checkin_code is not None
        return record.checkin_code, record.checkin_code

    def _target_key(
        self,
        identity: str,
        record: CheckinRecord,
    ) -> tuple[str, str | None]:
        action = record.override_action if record.action == "manual_override" else record.action
        session_id = record.session_id if action and action.startswith("session_") else None
        return identity, session_id

    def apply(
        self,
        record: CheckinRecord | Mapping[str, object],
        *,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Validate and apply one record, returning the applied record.

        A direct duplicate check-in is rejected.  Manual overrides are the
        explicit correction path and always carry a reason in the record.
        Reapplying an identical record is also rejected by ``apply``; bulk
        import is idempotent for identical lines and skips those lines before
        applying the remaining records.
        """

        if not isinstance(record, CheckinRecord):
            record = CheckinRecord.from_dict(record)
        if record.event_id != self.event_id:
            raise ValueError(
                f"record belongs to event {record.event_id!r}, "
                f"not {self.event_id!r}"
            )
        identity, _ = self._resolve_record_identity(
            record,
            allow_unknown_code=allow_unknown_code,
        )
        record_key = serialize_event_record(record)
        if record_key in self._record_keys:
            raise DuplicateCheckinError("the event record has already been applied")

        target = self._target_key(identity, record)
        action = record.override_action if record.action == "manual_override" else record.action
        if action in _CHECK_IN_ACTIONS:
            if target in self._checked_in and record.action != "manual_override":
                raise DuplicateCheckinError(
                    f"{identity!r} is already checked in for this scope"
                )
            self._checked_in.add(target)
        elif action in _CHECK_OUT_ACTIONS:
            if record.action != "manual_override" and target not in self._checked_in:
                raise InvalidTransitionError(
                    f"{identity!r} is not checked in for this scope"
                )
            self._checked_in.discard(target)
        else:  # pragma: no cover - CheckinRecord validates this invariant.
            raise ValueError(f"unsupported action {action!r}")

        self._records.append(record)
        self._record_keys.add(record_key)
        self._next_sequence = max(self._next_sequence, record.sequence + 1)
        return record

    def _append(
        self,
        action: str,
        attendee_id: str | None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        session_id: str | None = None,
        reason: str | None = None,
        allow_unknown_code: bool = False,
        override_action: str | None = None,
    ) -> CheckinRecord:
        resolved_id = _choose_identity(attendee_id, person_id)
        record = CheckinRecord(
            event_id=self.event_id,
            attendee_id=resolved_id,
            checkin_code=checkin_code,
            action=action,
            operator=operator,
            sequence=self._next_sequence,
            schema_version=SCHEMA_VERSION,
            session_id=session_id,
            reason=reason,
            device_id=self.device_id,
            override_action=override_action,
        )
        return self.apply(record, allow_unknown_code=allow_unknown_code)

    def check_in(
        self,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Append an event-level check-in record."""

        return self._append(
            "check_in",
            attendee_id,
            person_id=person_id,
            checkin_code=checkin_code,
            operator=operator,
            allow_unknown_code=allow_unknown_code,
        )

    def check_out(
        self,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Append an event-level check-out record."""

        return self._append(
            "check_out",
            attendee_id,
            person_id=person_id,
            checkin_code=checkin_code,
            operator=operator,
            allow_unknown_code=allow_unknown_code,
        )

    def session_check_in(
        self,
        session_id: str,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Append a session-scoped check-in record."""

        return self._append(
            "session_check_in",
            attendee_id,
            person_id=person_id,
            checkin_code=checkin_code,
            operator=operator,
            session_id=session_id,
            allow_unknown_code=allow_unknown_code,
        )

    def session_check_out(
        self,
        session_id: str,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Append a session-scoped check-out record."""

        return self._append(
            "session_check_out",
            attendee_id,
            person_id=person_id,
            checkin_code=checkin_code,
            operator=operator,
            session_id=session_id,
            allow_unknown_code=allow_unknown_code,
        )

    def manual_override(
        self,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        operator: str,
        checked_in: bool,
        reason: str,
        session_id: str | None = None,
        allow_unknown_code: bool = False,
    ) -> CheckinRecord:
        """Append a reasoned correction that sets the requested state."""

        action = "session_check_in" if checked_in and session_id else "check_in"
        if not checked_in:
            action = "session_check_out" if session_id else "check_out"
        return self._append(
            "manual_override",
            attendee_id,
            person_id=person_id,
            checkin_code=checkin_code,
            operator=operator,
            session_id=session_id,
            reason=reason,
            allow_unknown_code=allow_unknown_code,
            override_action=action,
        )

    def is_checked_in(
        self,
        attendee_id: str | None = None,
        *,
        person_id: str | None = None,
        checkin_code: str | None = None,
        session_id: str | None = None,
        allow_unknown_code: bool = False,
    ) -> bool:
        """Return current event or session state for an attendee."""

        resolved_id = _choose_identity(attendee_id, person_id)
        if checkin_code is not None:
            code = _text(checkin_code, "checkin_code")
            known_id = self._known_codes.get(code)
            if known_id is None and not (allow_unknown_code or self.allow_unknown_codes):
                raise UnknownCheckinCodeError(f"unknown check-in code {code!r}")
            if known_id is not None:
                if resolved_id is not None and resolved_id != known_id:
                    raise CheckinRuntimeError("check-in code and attendee_id do not match")
                resolved_id = known_id
            elif resolved_id is None:
                resolved_id = code
        if resolved_id is None:
            raise ValueError("attendee_id, person_id, or checkin_code is required")
        return (resolved_id, session_id) in self._checked_in

    def snapshot(self) -> tuple[dict[str, object], ...]:
        """Return deterministic, non-private current attendee state rows."""

        identities: set[str] = set(self._known_codes.values())
        for record in self._records:
            identity, _ = self._resolve_record_identity(record, allow_unknown_code=True)
            identities.add(identity)

        codes_by_identity: dict[str, list[str]] = {}
        for code, identity in self._known_codes.items():
            codes_by_identity.setdefault(identity, []).append(code)

        rows: list[dict[str, object]] = []
        for identity in sorted(identities, key=str.casefold):
            sessions = sorted(
                session_id
                for current_identity, session_id in self._checked_in
                if current_identity == identity and session_id is not None
            )
            row: dict[str, object] = {
                "attendee_id": identity,
                "checked_in": (identity, None) in self._checked_in,
                "session_ids": sessions,
            }
            codes = sorted(codes_by_identity.get(identity, ()))
            if codes:
                row["checkin_code"] = codes[0]
                if len(codes) > 1:
                    row["checkin_codes"] = codes
            rows.append(row)
        return tuple(rows)

    def export_log(self) -> str:
        """Export this registry's records as deterministic JSONL."""

        return serialize_event_log(self._records)

    def import_log(
        self,
        data: str | bytes | Iterable[CheckinRecord],
        *,
        allow_unknown_code: bool = False,
    ) -> tuple[CheckinRecord, ...]:
        """Apply a log, skipping exact lines already present.

        Imported records are applied in deterministic merge order.  A failed
        import leaves the registry unchanged.
        """

        records = merge_event_records(data)
        if any(record.event_id != self.event_id for record in records):
            raise ValueError("imported log contains a record for another event")

        old_checked_in = set(self._checked_in)
        old_records = list(self._records)
        old_record_keys = set(self._record_keys)
        old_next_sequence = self._next_sequence
        applied: list[CheckinRecord] = []
        try:
            for record in records:
                if serialize_event_record(record) in self._record_keys:
                    continue
                applied.append(
                    self.apply(record, allow_unknown_code=allow_unknown_code)
                )
        except Exception:
            self._checked_in = old_checked_in
            self._records = old_records
            self._record_keys = old_record_keys
            self._next_sequence = old_next_sequence
            raise
        return tuple(applied)

    def render_html(
        self,
        *,
        title: str = "Offline event check-in",
        public_attendees: Mapping[str, Mapping[str, object]]
        | Iterable[Mapping[str, object]]
        | None = None,
        private_data: object | None = None,
    ) -> bytes:
        """Render this registry as a searchable, self-contained HTML app.

        ``public_attendees`` may contain names and other public display data;
        email-like keys are removed recursively.  ``private_data`` is kept in
        a separate payload field and is included only when explicitly passed.
        """

        return render_checkin_app(
            self,
            title=title,
            public_attendees=public_attendees,
            private_data=private_data,
        )


def _is_private_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _PRIVATE_PUBLIC_KEY_PARTS or normalized.startswith("email_")


def _public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if not _is_private_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("public attendee data must contain JSON-compatible values")


def _normalize_public_attendees(
    public_attendees: Mapping[str, Mapping[str, object]]
    | Iterable[Mapping[str, object]]
    | None,
) -> list[dict[str, object]]:
    if public_attendees is None:
        return []
    values: list[Mapping[str, object]] = []
    if isinstance(public_attendees, Mapping):
        for identity, value in public_attendees.items():
            if not isinstance(value, Mapping):
                raise TypeError("public attendee mapping values must be mappings")
            item = dict(value)
            item.setdefault("attendee_id", identity)
            values.append(item)
    else:
        values = list(public_attendees)

    normalized: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError("public attendees must contain mappings")
        clean = _public_value(value)
        assert isinstance(clean, dict)
        if "attendee_id" not in clean and "person_id" in clean:
            clean["attendee_id"] = clean.pop("person_id")
        if "attendee_id" not in clean and "checkin_code" not in clean:
            raise ValueError("public attendees need attendee_id or checkin_code")
        normalized.append(clean)
    normalized.sort(
        key=lambda item: _canonical_json(
            {
                "attendee_id": item.get("attendee_id", ""),
                "checkin_code": item.get("checkin_code", ""),
                "data": item,
            }
        )
    )
    return normalized


def _merge_public_attendees(
    registry: CheckinRegistry,
    public_attendees: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_identity: dict[str, dict[str, object]] = {}
    for row in registry.snapshot():
        identity = str(row["attendee_id"])
        by_identity[identity] = dict(row)

    for attendee in public_attendees:
        identity = attendee.get("attendee_id")
        if identity is None and attendee.get("checkin_code") is not None:
            identity = registry.known_codes.get(str(attendee["checkin_code"]), str(attendee["checkin_code"]))
            attendee = {**attendee, "attendee_id": identity}
        if identity is None:
            raise ValueError("public attendee needs attendee_id or checkin_code")
        identity_text = str(identity)
        current = by_identity.setdefault(
            identity_text,
            {"attendee_id": identity_text, "checked_in": False, "session_ids": []},
        )
        current.update(attendee)
    return [by_identity[key] for key in sorted(by_identity, key=str.casefold)]


def _html_payload(value: object) -> str:
    """Encode JSON for a data script element without HTML/script breakouts."""

    encoded = _canonical_json(value)
    return (
        encoded.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("/", "\\u002f")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_checkin_app(
    registry: CheckinRegistry,
    *,
    title: str = "Offline event check-in",
    public_attendees: Mapping[str, Mapping[str, object]]
    | Iterable[Mapping[str, object]]
    | None = None,
    private_data: object | None = None,
) -> bytes:
    """Render a searchable, offline check-in app/data payload.

    The only attendee data placed in the public ``attendees`` payload is the
    supplied public data plus registry state.  Email fields are stripped from
    that data.  If ``private_data`` is not ``None``, it is placed under the
    separate ``private_data`` payload key exactly because the caller explicitly
    opted into including it.
    """

    if not isinstance(registry, CheckinRegistry):
        raise TypeError("registry must be a CheckinRegistry")
    title_text = _text(title, "title")
    attendees = _merge_public_attendees(
        registry,
        _normalize_public_attendees(public_attendees),
    )
    payload: dict[str, object] = {
        "attendees": attendees,
        "event_id": registry.event_id,
        "records": [record.to_dict() for record in sorted(registry.records, key=_record_sort_key)],
        "schema_version": SCHEMA_VERSION,
    }
    if private_data is not None:
        # Validate now so rendering never produces a half-valid app.  Keep it
        # separate from public attendee rows to make the API boundary visible.
        _canonical_json(private_data)
        payload["private_data"] = private_data

    document = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title_text, quote=True)}</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
    body {{ margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #17202a; background: #fff; }}
    h1 {{ margin-bottom: .25rem; color: #1e3a5f; }}
    .summary {{ color: #536471; }}
    #search {{ box-sizing: border-box; font: inherit; margin: 1rem 0; padding: .65rem .75rem; width: 100%; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #b7c2cc; padding: .6rem .7rem; text-align: left; }}
    th {{ background: #e9f0f5; color: #1e3a5f; }}
    .status {{ font-weight: 600; }}
    .empty {{ color: #536471; padding: 1.5rem; text-align: center; }}
  </style>
</head>
<body>
  <h1>{escape(title_text, quote=True)}</h1>
  <p class="summary">Offline check-in data. Search by attendee, code, or public display details.</p>
  <label for="operator">Operator</label>
  <input id="operator" type="text" autocomplete="off" placeholder="Staff name or station">
  <label for="search">Search attendees</label>
  <input id="search" type="search" autocomplete="off" placeholder="Search">
  <p><button id="export" type="button">Export event log</button> <span id="saved">Changes stay on this device until exported.</span></p>
  <table>
    <thead><tr><th>Attendee</th><th>Check-in code</th><th>Status</th><th>Sessions</th><th>Action</th></tr></thead>
    <tbody id="attendees"></tbody>
  </table>
  <script id="event-data" type="application/json">{_html_payload(payload)}</script>
  <script>
    (() => {{
      const data = JSON.parse(document.getElementById("event-data").textContent);
      const rows = Array.isArray(data.attendees) ? data.attendees : [];
      const search = document.getElementById("search");
      const operator = document.getElementById("operator");
      const body = document.getElementById("attendees");
      const storageKey = "eventkit-checkin:" + data.event_id;
      let records = Array.isArray(data.records) ? data.records.slice() : [];
      try {{
        const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
        if (Array.isArray(saved)) records = records.concat(saved);
      }} catch (_) {{}}
      const state = new Map();
      rows.forEach((row) => state.set(String(row.attendee_id || row.checkin_code || ""), {{...row}}));
      records.forEach((record) => {{
        const key = String(record.attendee_id || record.checkin_code || "");
        const row = state.get(key);
        if (row) row.checked_in = record.action === "check_in" || record.action === "session_check_in";
      }});
      const cell = (value) => {{
        const element = document.createElement("td");
        element.textContent = value == null ? "" : String(value);
        return element;
      }};
      const save = () => {{
        try {{ localStorage.setItem(storageKey, JSON.stringify(records)); }} catch (_) {{}}
      }};
      const append = (row, checkedIn) => {{
        const key = String(row.attendee_id || row.checkin_code || "");
        const current = state.get(key);
        if (!current || Boolean(current.checked_in) === checkedIn) return;
        const name = operator.value.trim();
        if (!name) {{ operator.focus(); return; }}
        records.push({{
          action: checkedIn ? "check_in" : "check_out",
          attendee_id: row.attendee_id || null,
          checkin_code: row.checkin_code || null,
          event_id: data.event_id,
          operator: name,
          schema_version: data.schema_version,
          sequence: records.reduce((max, item) => Math.max(max, Number(item.sequence) || 0), 0) + 1,
          device_id: "browser"
        }});
        current.checked_in = checkedIn;
        save();
        render();
      }};
      const render = () => {{
        const term = search.value.trim().toLowerCase();
        body.replaceChildren();
        const visible = rows.filter((row) => !term || JSON.stringify(state.get(String(row.attendee_id || row.checkin_code || "")) || row).toLowerCase().includes(term));
        if (!visible.length) {{
          const row = document.createElement("tr");
          const empty = cell("No matching attendees.");
          empty.colSpan = 5;
          empty.className = "empty";
          row.appendChild(empty);
          body.appendChild(row);
          return;
        }}
        visible.forEach((row) => {{
          const current = state.get(String(row.attendee_id || row.checkin_code || "")) || row;
          const tableRow = document.createElement("tr");
          tableRow.appendChild(cell(current.name || current.attendee_id || ""));
          tableRow.appendChild(cell(current.checkin_code || ""));
          const status = cell(current.checked_in ? "Checked in" : "Not checked in");
          status.className = "status";
          tableRow.appendChild(status);
          tableRow.appendChild(cell((current.session_ids || []).join(", ")));
          const action = document.createElement("td");
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = current.checked_in ? "Undo check-in" : "Check in";
          button.addEventListener("click", () => append(current, !current.checked_in));
          action.appendChild(button);
          tableRow.appendChild(action);
          body.appendChild(tableRow);
        }});
      }};
      search.addEventListener("input", render);
      document.getElementById("export").addEventListener("click", () => {{
        const text = records.map((record) => JSON.stringify(record)).join("\n") + (records.length ? "\n" : "");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([text], {{type: "application/jsonl"}}));
        link.download = data.event_id + "-checkin.log";
        link.click();
        URL.revokeObjectURL(link.href);
      }});
      render();
    }})();
  </script>
</body>
</html>
'''
    return document.encode("utf-8")


render_offline_checkin_app = render_checkin_app


__all__ = [
    "ACTIONS",
    "SCHEMA_VERSION",
    "CheckinRecord",
    "CheckinRegistry",
    "CheckinRuntimeError",
    "DuplicateCheckinError",
    "InvalidTransitionError",
    "UnknownCheckinCodeError",
    "merge_event_logs",
    "merge_event_records",
    "parse_event_log",
    "render_checkin_app",
    "render_offline_checkin_app",
    "serialize_event_log",
    "serialize_event_record",
]
