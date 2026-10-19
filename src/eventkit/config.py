"""Configuration loading for the eventkit command line."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


class ConfigError(ValueError):
    """An expected configuration or input error."""


@dataclass(frozen=True, slots=True)
class EventConfig:
    """Validated settings shared by the eventkit pipeline steps."""

    path: Path
    event_id: str
    name: str
    organizer: str
    utc_offset: str
    registrations_path: Path
    attendance_path: Path | None
    columns: dict[str, str]
    output_dir: Path
    steps: tuple[str, ...]
    certificate_title: str
    certificate_issue_to: str
    manifest_names: bool


def _text(value: Any, *, field: str, required: bool = True) -> str:
    if value is None:
        if required:
            raise ConfigError(f"Missing required config value: {field}")
        return ""
    text = str(value).strip()
    if required and not text:
        raise ConfigError(f"Config value cannot be empty: {field}")
    return text


def _relative_path(root: Path, value: Any, *, field: str) -> Path:
    raw = _text(value, field=field)
    path = Path(raw)
    if path.is_absolute():
        raise ConfigError(f"Config path must be relative to event.toml: {field}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ConfigError(f"Config path escapes its event directory: {field}") from exc
    return resolved


def load_config(path: str | Path) -> EventConfig:
    """Load and validate an ``event.toml`` file.

    Paths are resolved relative to the directory containing the config.  The
    Optional attendance input is left as ``None`` when it is not configured,
    allowing an attended-only certificate run to explain why it was skipped.
    """

    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc

    event = data.get("event")
    inputs = data.get("input")
    columns = data.get("columns", {})
    output = data.get("output")
    if not isinstance(event, dict) or not isinstance(inputs, dict) or not isinstance(output, dict):
        raise ConfigError("event.toml needs [event], [input], and [output] tables")
    if not isinstance(columns, dict):
        raise ConfigError("[columns] must be a table")

    steps_value = output.get("steps", ["roster"])
    if not isinstance(steps_value, list) or not all(isinstance(step, str) for step in steps_value):
        raise ConfigError("[output].steps must be a list of strings")
    steps = tuple(step.strip().lower() for step in steps_value if step.strip())
    unsupported = sorted(set(steps) - {"roster", "certificates"})
    if unsupported:
        joined = ", ".join(unsupported)
        raise ConfigError(
            f"Unsupported step(s) in this build: {joined}; currently available: roster, certificates"
        )
    if "roster" not in steps:
        raise ConfigError("[output].steps must include 'roster' for this build")

    root = config_path.parent
    registration_path = _relative_path(
        root, inputs.get("registrations"), field="input.registrations"
    )
    attendance_value = inputs.get("attendance")
    attendance_path = (
        _relative_path(root, attendance_value, field="input.attendance")
        if attendance_value is not None
        else None
    )
    output_dir = _relative_path(root, output.get("dir", "kit"), field="output.dir")

    certificates = data.get("certificates", {})
    if not isinstance(certificates, dict):
        raise ConfigError("[certificates] must be a table")
    issue_to = _text(
        certificates.get("issue_to", "attended"),
        field="certificates.issue_to",
    ).lower()
    if issue_to not in {"attended", "all", "registered"}:
        raise ConfigError(
            "certificates.issue_to must be 'attended', 'all', or 'registered'"
        )
    manifest_names = certificates.get("manifest_names", True)
    if not isinstance(manifest_names, bool):
        raise ConfigError("certificates.manifest_names must be true or false")

    normalized_columns: dict[str, str] = {}
    for canonical in ("name", "email", "phone", "institution"):
        value = columns.get(canonical, canonical)
        normalized_columns[canonical] = _text(
            value, field=f"columns.{canonical}", required=False
        ) or canonical

    return EventConfig(
        path=config_path,
        event_id=_text(event.get("id"), field="event.id"),
        name=_text(event.get("name"), field="event.name"),
        organizer=_text(event.get("organizer", ""), field="event.organizer", required=False),
        utc_offset=_text(event.get("utc_offset", "+00:00"), field="event.utc_offset", required=False) or "+00:00",
        registrations_path=registration_path,
        attendance_path=attendance_path,
        columns=normalized_columns,
        output_dir=output_dir,
        steps=steps,
        certificate_title=_text(
            certificates.get("title", "Certificate of Participation"),
            field="certificates.title",
        ),
        certificate_issue_to=issue_to,
        manifest_names=manifest_names,
    )
