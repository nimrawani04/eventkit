"""Safe, deterministic cleanup of an eventkit output directory.

The output writer records the content hash of every generated file in
``audit.jsonl``.  This module is deliberately separate from the command-line
entry point so the CLI integration can expose the same plan in both normal and
dry-run modes without giving cleanup any additional filesystem authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import EventConfig, load_config
from .output import FileAction, OutputResult, _safe_target, format_plan


_AUDIT_NAME = "audit.jsonl"
_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class _AuditRecord:
    relative_path: str
    sha256: str
    size: int
    step: str


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (OSError, UnicodeError):
        return None


def _safe_audit_path(root: Path, relative_path: str) -> Path:
    """Validate an audited path before trusting any other audit field."""

    path = Path(relative_path)
    if not relative_path or path == Path(".") or path.name == _AUDIT_NAME:
        raise ValueError(f"Invalid audited output path: {relative_path!r}")
    return _safe_target(root, relative_path)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _parse_audit(
    root: Path,
    content: bytes,
) -> tuple[tuple[_AuditRecord, ...], bool]:
    """Parse and authenticate an existing audit file.

    A checksum-valid audit is considered owned only when it has the exact
    record shape produced by :func:`eventkit.output._audit_bytes`.  Being
    stricter here prevents a partially edited or hand-written audit from
    authorizing deletion.
    """

    try:
        text = content.decode("utf-8")
        values = [json.loads(line) for line in text.splitlines()]
    except (UnicodeError, json.JSONDecodeError):
        return (), False

    if len(values) < 2 or not all(isinstance(value, dict) for value in values):
        return (), False

    # Validate path safety even when the checksum is bad.  An edited audit
    # must never turn a traversal or symlink into an operation that is merely
    # reported as harmlessly unowned.
    for value in values:
        relative_path = value.get("path")
        if isinstance(relative_path, str):
            _safe_audit_path(root, relative_path)

    first = values[0]
    last = values[-1]
    if set(first) != {"input_digest"} or not _valid_digest(first.get("input_digest")):
        return (), False
    if set(last) != {"audit_sha256"} or not _valid_digest(last.get("audit_sha256")):
        return (), False

    prefix = "".join(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        for value in values[:-1]
    ).encode("utf-8")
    if not hmac.compare_digest(_sha256(prefix), last["audit_sha256"]):
        return (), False

    records: list[_AuditRecord] = []
    seen: set[str] = set()
    for value in values[1:-1]:
        if set(value) != {"path", "sha256", "size", "step"}:
            return (), False
        relative_path = value.get("path")
        size = value.get("size")
        step = value.get("step")
        if (
            not isinstance(relative_path, str)
            or not _valid_digest(value.get("sha256"))
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(step, str)
        ):
            return (), False
        _safe_audit_path(root, relative_path)
        if relative_path in seen:
            return (), False
        seen.add(relative_path)
        records.append(_AuditRecord(relative_path, value["sha256"].lower(), size, step))

    return tuple(records), True


def _audit_state(
    root: Path,
) -> tuple[tuple[_AuditRecord, ...], bool, bytes | None]:
    audit_path = _safe_target(root, _AUDIT_NAME)
    content = _read_bytes(audit_path)
    if content is None:
        return (), False, None
    records, owned = _parse_audit(root, content)
    return records, owned, content


def _file_action(root: Path, record: _AuditRecord) -> tuple[FileAction, str | None]:
    target = _safe_audit_path(root, record.relative_path)
    if not target.exists():
        return (
            FileAction(record.relative_path, "missing", 0, record.sha256, record.step),
            None,
        )

    try:
        if target.is_symlink() or not target.is_file():
            raise OSError("audited path is not a regular file")
        content = target.read_bytes()
    except OSError:
        return (
            FileAction(record.relative_path, "keep", record.size, record.sha256, record.step),
            f"Output file kept because it could not be safely inspected: {record.relative_path}",
        )

    if _sha256(content) != record.sha256:
        return (
            FileAction(record.relative_path, "keep", len(content), record.sha256, record.step),
            f"Output file kept because it was edited or not owned: {record.relative_path}",
        )
    return (
        FileAction(record.relative_path, "delete", len(content), record.sha256, record.step),
        None,
    )


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _unowned_actions(
    root: Path,
    owned_paths: set[str],
) -> tuple[FileAction, ...]:
    """Report files present in the kit that are absent from the audit."""

    if not root.is_dir():
        return ()

    found: list[FileAction] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        files.sort()
        symlink_directories = [name for name in directories if (current_path / name).is_symlink()]
        directories[:] = [name for name in directories if name not in symlink_directories]
        for name in (*files, *symlink_directories):
            path = current_path / name
            relative_path = _relative_path(root, path)
            if relative_path == _AUDIT_NAME or relative_path in owned_paths:
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                size = 0
            found.append(
                FileAction(relative_path, "keep", size, "", "unowned")
            )
    return tuple(found)


def _empty_directory_actions(
    root: Path,
    delete_paths: Iterable[str],
) -> tuple[FileAction, ...]:
    """Plan only empty parent directories of files that will be deleted."""

    candidates: set[Path] = set()
    for relative_path in delete_paths:
        target = _safe_audit_path(root, relative_path)
        parent = target.parent
        while parent != root:
            candidates.add(parent)
            parent = parent.parent

    planned_files = {_safe_audit_path(root, relative_path) for relative_path in delete_paths}
    planned_directories: set[Path] = set()
    actions: list[FileAction] = []
    ordered = sorted(candidates, key=lambda path: (-len(path.relative_to(root).parts), _relative_path(root, path)))
    for directory in ordered:
        _safe_target(root, _relative_path(root, directory))
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            entries = tuple(directory.iterdir())
        except OSError:
            continue
        remaining = {
            entry
            for entry in entries
            if entry not in planned_files and entry not in planned_directories
        }
        if remaining:
            continue
        planned_directories.add(directory)
        relative_path = _relative_path(root, directory)
        actions.append(FileAction(relative_path, "rmdir", 0, "", "clean"))
    return tuple(actions)


def _audit_action(
    root: Path,
    content: bytes | None,
    owned: bool,
) -> FileAction:
    if content is None:
        return FileAction(_AUDIT_NAME, "missing", 0, "", "audit")
    digest = _sha256(content)
    if owned:
        return FileAction(_AUDIT_NAME, "delete", len(content), digest, "audit")
    return FileAction(_AUDIT_NAME, "keep", len(content), digest, "audit")


def plan_clean(output_dir: str | Path) -> OutputResult:
    """Build a deterministic cleanup plan without changing the filesystem."""

    root = Path(output_dir).expanduser().resolve()
    records, owned, audit_content = _audit_state(root)

    unresolved: list[str] = []
    actions: list[FileAction] = []
    owned_paths = {record.relative_path for record in records}

    if owned:
        for record in sorted(records, key=lambda item: item.relative_path):
            action, issue = _file_action(root, record)
            actions.append(action)
            if issue is not None:
                unresolved.append(issue)
    else:
        if audit_content is None:
            unresolved.append("Audit log is missing; no audited files were removed.")
        else:
            unresolved.append("Audit log is malformed or edited; no audited files were removed.")

    unowned = _unowned_actions(root, owned_paths if owned else set())
    actions.extend(unowned)
    unresolved.extend(
        f"Output file kept because it was not audited: {item.relative_path}"
        for item in unowned
    )

    cleanup_paths = [
        item.relative_path
        for item in actions
        if item.action in {"delete", "missing"}
    ]
    actions.extend(_empty_directory_actions(root, cleanup_paths))
    actions.append(_audit_action(root, audit_content, owned))

    actions.sort(
        key=lambda item: (
            item.relative_path == _AUDIT_NAME,
            item.action == "rmdir",
            item.relative_path,
        )
    )
    # The audit must be the final operation in both normal and dry-run plans.
    audit = next(item for item in actions if item.relative_path == _AUDIT_NAME)
    actions = [item for item in actions if item.relative_path != _AUDIT_NAME]
    actions.append(audit)
    return OutputResult(root, tuple(actions), tuple(dict.fromkeys(unresolved)))


def _replace_action(
    actions: list[FileAction],
    relative_path: str,
    replacement: FileAction,
) -> None:
    for index, action in enumerate(actions):
        if action.relative_path == relative_path:
            actions[index] = replacement
            return


def _apply_clean(plan: OutputResult) -> OutputResult:
    actions = list(plan.actions)
    unresolved = list(plan.unresolved)
    root = plan.output_dir

    for item in plan.actions:
        if item.action != "delete" or item.relative_path == _AUDIT_NAME:
            continue
        target = _safe_target(root, item.relative_path)
        try:
            if target.is_symlink() or not target.is_file():
                raise OSError("target is no longer a regular file")
            content = target.read_bytes()
            if _sha256(content) != item.sha256:
                raise OSError("target was edited before cleanup")
            target.unlink()
        except OSError as exc:
            _replace_action(
                actions,
                item.relative_path,
                FileAction(item.relative_path, "keep", item.size, item.sha256, item.step),
            )
            unresolved.append(
                f"Output file kept because it changed during cleanup: {item.relative_path} ({exc})"
            )

    for item in plan.actions:
        if item.action != "rmdir":
            continue
        target = _safe_target(root, item.relative_path)
        try:
            if target.is_symlink() or not target.is_dir():
                raise OSError("target is no longer a directory")
            target.rmdir()
        except OSError as exc:
            _replace_action(
                actions,
                item.relative_path,
                FileAction(item.relative_path, "keep", 0, "", item.step),
            )
            unresolved.append(
                f"Generated directory kept because it was not empty or could not be removed: "
                f"{item.relative_path} ({exc})"
            )

    audit_item = next(item for item in plan.actions if item.relative_path == _AUDIT_NAME)
    if audit_item.action == "delete":
        audit_path = _safe_target(root, _AUDIT_NAME)
        try:
            current = audit_path.read_bytes()
            _, owned, _ = _audit_state(root)
            if not owned or _sha256(current) != audit_item.sha256:
                raise OSError("audit log was edited before cleanup")
            audit_path.unlink()
        except OSError as exc:
            _replace_action(
                actions,
                _AUDIT_NAME,
                FileAction(_AUDIT_NAME, "keep", audit_item.size, audit_item.sha256, audit_item.step),
            )
            unresolved.append(f"Audit log kept because it was not safely owned: {exc}")

    return OutputResult(root, tuple(actions), tuple(dict.fromkeys(unresolved)))


def clean_output(output_dir: str | Path, *, dry_run: bool = False) -> OutputResult:
    """Plan or apply safe cleanup for an already-resolved output directory."""

    plan = plan_clean(output_dir)
    return plan if dry_run else _apply_clean(plan)


def clean_config(config: EventConfig, *, dry_run: bool = False) -> OutputResult:
    """Plan or apply cleanup for a loaded event configuration."""

    return clean_output(config.output_dir, dry_run=dry_run)


def plan_or_clean(config: EventConfig, *, dry_run: bool = False) -> OutputResult:
    """Compatibility-shaped entry point matching the existing output writer."""

    return clean_config(config, dry_run=dry_run)


def clean_event(event_toml: str | Path, *, dry_run: bool = False) -> OutputResult:
    """Plan or apply cleanup for ``EVENT_TOML`` without reading its inputs."""

    return clean_config(load_config(event_toml), dry_run=dry_run)


def format_clean_plan(result: OutputResult) -> str:
    """Format clean actions using the existing deterministic plan format."""

    return format_plan(result)


__all__ = [
    "clean_config",
    "clean_event",
    "clean_output",
    "format_clean_plan",
    "plan_clean",
    "plan_or_clean",
]
