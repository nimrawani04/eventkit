"""Deterministic roster renderers and a small audited file writer."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Iterable

from .config import EventConfig
from .model import IssueLevel, RosterResult


@dataclass(frozen=True, slots=True)
class FileAction:
    """The planned or completed operation for one output file."""

    relative_path: str
    action: str
    size: int
    sha256: str
    step: str = "roster"


@dataclass(frozen=True, slots=True)
class OutputResult:
    """Results of planning or writing the current roster kit."""

    output_dir: Path
    actions: tuple[FileAction, ...]
    unresolved: tuple[str, ...]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def render_roster_csv(result: RosterResult) -> bytes:
    """Render every source row, including excluded rows, as deterministic CSV."""

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("row", "name", "email", "phone", "institution", "status", "issues"))
    for row in result.rows:
        writer.writerow(
            (
                row.row_number,
                row.name,
                row.email,
                row.phone,
                row.institution,
                row.status.value,
                ",".join(row.issue_ids),
            )
        )
    return stream.getvalue().encode("utf-8")


def _issue_bullet(issue: object) -> str:
    # ``Issue`` is intentionally duck-typed here to keep this renderer small
    # and usable with the immutable model without a serialization layer.
    related = (
        f" (related row {issue.related_row_number})"
        if issue.related_row_number is not None
        else ""
    )
    suggestion = f" Suggested fix: {issue.suggestion}" if issue.suggestion else ""
    return (
        f"- Row {issue.row_number} — **{issue.code}**{related}: "
        f"{issue.message}{suggestion}"
    )


def render_issues_md(result: RosterResult) -> bytes:
    """Render grouped issue explanations with source row numbers."""

    lines = ["# Registration issues", "", "Every source row remains in `roster.csv`; excluded rows are marked there.", ""]
    for level, title in ((IssueLevel.ERROR, "Errors"), (IssueLevel.WARNING, "Warnings")):
        lines.extend((f"## {title}", ""))
        issues = [issue for issue in result.issues if issue.level is level]
        if issues:
            lines.extend(_issue_bullet(issue) for issue in issues)
        else:
            lines.append("None.")
        lines.append("")

    lines.extend(("## Ignored registration columns", ""))
    if result.extra_columns:
        lines.append("The following input columns were ignored: " + ", ".join(f"`{column}`" for column in result.extra_columns) + ".")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def render_report(
    result: RosterResult,
    actions: Iterable[FileAction],
    unresolved: Iterable[str],
    verified: Iterable[str] = (),
    summary: Iterable[str] = (),
) -> bytes:
    """Render the first-slice report with explicit verified/unresolved parts."""

    actions = tuple(actions)
    unresolved_items = list(unresolved)
    errors = len(result.errors)
    warnings = len(result.warnings)
    lines = [
        "# eventkit report",
        "",
        "## Summary",
        "",
        f"- Rows read: {len(result.rows)}",
        f"- Rows included: {len(result.included_rows)}",
        f"- Rows excluded: {len(result.excluded_rows)}",
        f"- Errors: {errors}",
        f"- Warnings: {warnings}",
        f"- Files planned: {len(actions) + 2}",
    ]
    lines.extend(f"- {item}" for item in summary)
    lines.extend(("", "## Verified", ""))
    kept_paths = {item.relative_path for item in actions if item.action == "keep"}
    if not ({"roster.csv", "issues.md"} & kept_paths):
        lines.extend(
            (
                "- Registration CSV was read with BOM-safe, physical-line tracking.",
                "- Every source row is represented in the generated roster.",
                "- Roster and issue output is deterministic for the same inputs.",
            )
        )
    verified_items = list(verified)
    if "checkin.html" in kept_paths:
        verified_items = [
            item for item in verified_items if not item.startswith("Check-in sheet")
        ]
    if {"schedule.ics", "agenda.html"} & kept_paths:
        verified_items = [
            item
            for item in verified_items
            if not item.startswith("Schedule") and not item.startswith("Agenda")
        ]
    if "mailmerge.csv" in kept_paths or any(path.startswith("emails/") for path in kept_paths):
        verified_items = [
            item for item in verified_items
            if not item.startswith("Email draft")
            and not item.startswith("Mail-merge CSV")
        ]
    if "verify.html" in kept_paths:
        verified_items = [
            item for item in verified_items
            if not item.startswith("Offline verification page")
        ]
    if "risk.md" in kept_paths:
        verified_items = [
            item for item in verified_items
            if not item.startswith("Risk report")
        ]
    if "dashboard.html" in kept_paths:
        verified_items = [
            item for item in verified_items
            if not item.startswith("Offline dashboard")
        ]
    if any(
        path == "manifest.json" or path.startswith("certificates/")
        for path in kept_paths
    ):
        verified_items = [
            item
            for item in verified_items
            if not item.startswith("Every generated certificate")
        ]
    lines.extend(f"- {item}" for item in verified_items)
    lines.extend(("", "## Unresolved", ""))
    if unresolved_items:
        lines.extend(f"- {item}" for item in unresolved_items)
    elif result.issues:
        lines.append("- See `issues.md` for the registration issues that need human review.")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _safe_target(root: Path, relative_path: str) -> Path:
    """Return an output target only when no path component is a symlink."""

    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Output path escapes the output directory: {relative_path}")
    target = root / path
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Output path contains a symlink: {relative_path}")
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Output path escapes the output directory: {relative_path}") from exc
    return target


def _read_audit(path: Path) -> tuple[dict[str, str], bool]:
    """Read prior file hashes and validate the audit record checksum."""

    if not path.is_file():
        return {}, False
    records: dict[str, str] = {}
    try:
        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not values or not isinstance(values[-1], dict):
            return {}, False
        checksum = values[-1].get("audit_sha256")
        if not isinstance(checksum, str):
            return {}, False
        prefix = "".join(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values[:-1]
        ).encode("utf-8")
        if not hmac.compare_digest(hashlib.sha256(prefix).hexdigest(), checksum):
            return {}, False
        for value in values[:-1]:
            if isinstance(value, dict) and isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                records[value["path"]] = value["sha256"]
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {}, False
    return records, True


def _file_action(
    output_dir: Path,
    relative_path: str,
    content: bytes,
    previous_hashes: dict[str, str],
    *,
    step: str = "roster",
) -> FileAction:
    target = _safe_target(output_dir, relative_path)
    digest = _sha256(content)
    if not target.exists():
        action = "create"
    else:
        try:
            current = target.read_bytes()
        except OSError:
            action = "keep"
        else:
            current_digest = _sha256(current)
            if current_digest == digest:
                action = "unchanged"
            elif previous_hashes.get(relative_path) == current_digest:
                action = "update"
            else:
                action = "keep"
    return FileAction(relative_path, action, len(content), digest, step)


def _step_for_path(relative_path: str) -> str:
    """Map generated file paths to their pipeline step for the audit log."""

    if relative_path == "checkin.html":
        return "checkin"
    if relative_path in {"schedule.ics", "agenda.html"}:
        return "schedule"
    if relative_path == "mailmerge.csv" or relative_path.startswith("emails/"):
        return "emails"
    if relative_path == "verify.html":
        return "certificates"
    if relative_path == "risk.md":
        return "report"
    if relative_path == "dashboard.html":
        return "report"
    if relative_path == "manifest.json" or relative_path.startswith("certificates/"):
        return "certificates"
    if relative_path == "report.md":
        return "report"
    return "roster"


def _audit_bytes(input_digest: str, files: Iterable[FileAction]) -> bytes:
    records = [{"input_digest": input_digest}]
    records.extend(
        {
            "path": item.relative_path,
            "sha256": item.sha256,
            "size": item.size,
            "step": item.step,
        }
        for item in files
        if item.relative_path != "audit.jsonl" and item.action != "delete"
    )
    prefix = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")
    checksum = hashlib.sha256(prefix).hexdigest()
    return prefix + (json.dumps({"audit_sha256": checksum}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _input_digest(config: EventConfig) -> str:
    digest = hashlib.sha256()
    paths = [config.path, config.registrations_path]
    if config.attendance_path is not None:
        paths.append(config.attendance_path)
    if config.schedule_path is not None:
        paths.append(config.schedule_path)
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def plan_or_write_roster(
    config: EventConfig,
    result: RosterResult,
    *,
    dry_run: bool = False,
    output_dir: str | Path | None = None,
    extra_files: Mapping[str, bytes] | None = None,
    extra_unresolved: Iterable[str] = (),
    extra_verified: Iterable[str] = (),
    extra_summary: Iterable[str] = (),
) -> OutputResult:
    """Plan or write roster files and optional pipeline-step files."""

    root = (Path(output_dir) if output_dir is not None else config.output_dir).expanduser().resolve()
    _safe_target(root, "audit.jsonl")
    previous_hashes, audit_owned = _read_audit(root / "audit.jsonl")
    file_contents = {
        "roster.csv": render_roster_csv(result),
        "issues.md": render_issues_md(result),
    }
    for relative_path, content in (extra_files or {}).items():
        _safe_target(root, relative_path)
        if not isinstance(content, bytes):
            raise TypeError(f"Output content must be bytes: {relative_path}")
        file_contents[relative_path] = content
    first_actions = tuple(
        _file_action(
            root,
            name,
            content,
            previous_hashes,
            step=_step_for_path(name),
        )
        for name, content in file_contents.items()
    )
    # report.md is rendered immediately below, so treat it as current while
    # reconciling stale generated files. Otherwise every second run would see
    # the previous report as stale and make the report/audit non-deterministic.
    current_paths = set(file_contents) | {"report.md"}
    stale_actions: list[FileAction] = []
    if audit_owned:
        for relative_path, previous_hash in sorted(previous_hashes.items()):
            if relative_path in current_paths or relative_path == "audit.jsonl":
                continue
            if (
                relative_path != "manifest.json"
                and not relative_path.startswith("certificates/")
                and relative_path != "checkin.html"
                and relative_path not in {"schedule.ics", "agenda.html"}
                and relative_path != "mailmerge.csv"
                and not relative_path.startswith("emails/")
                and relative_path not in {"verify.html", "risk.md"}
                and relative_path != "dashboard.html"
                and relative_path not in {"report.md"}
            ):
                continue
            target = _safe_target(root, relative_path)
            if not target.exists():
                continue
            current_digest = _sha256(target.read_bytes())
            action = "delete" if current_digest == previous_hash else "keep"
            stale_actions.append(
                FileAction(
                    relative_path,
                    action,
                    target.stat().st_size,
                    previous_hash,
                    _step_for_path(relative_path),
                )
            )
    report_unresolved = list(extra_unresolved)
    report_unresolved.extend(
        f"Output file kept because it was edited or not owned: {item.relative_path}"
        for item in first_actions
        if item.action == "keep"
    )
    report_unresolved.extend(
        f"Generated file kept because it was edited: {item.relative_path}"
        for item in stale_actions
        if item.action == "keep"
    )
    report_content = render_report(
        result,
        first_actions + tuple(stale_actions),
        report_unresolved,
        extra_verified,
        extra_summary,
    )
    file_contents["report.md"] = report_content
    actions = list(
        _file_action(
            root,
            name,
            content,
            previous_hashes,
            step=_step_for_path(name),
        )
        for name, content in file_contents.items()
    )
    actions.extend(stale_actions)
    audit_content = _audit_bytes(_input_digest(config), actions)
    audit_path = root / "audit.jsonl"
    audit_digest = _sha256(audit_content)
    if not audit_path.exists():
        audit_operation = "create"
    else:
        try:
            current_audit_digest = _sha256(audit_path.read_bytes())
        except OSError:
            current_audit_digest = ""
        if current_audit_digest == audit_digest:
            audit_operation = "unchanged"
        elif audit_owned:
            audit_operation = "update"
        else:
            audit_operation = "keep"
    audit_action = FileAction("audit.jsonl", audit_operation, len(audit_content), audit_digest, "audit")
    actions.append(audit_action)

    unresolved = list(report_unresolved)
    unresolved.extend(
        f"Output file kept because it was edited or not owned: {item.relative_path}"
        for item in actions
        if item.action == "keep" and item.relative_path not in {entry.split(": ")[-1] for entry in report_unresolved}
    )

    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
        for relative_path, content in file_contents.items():
            action = next(item.action for item in actions if item.relative_path == relative_path)
            if action in {"create", "update"}:
                target = _safe_target(root, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        for item in actions:
            if item.action == "delete":
                _safe_target(root, item.relative_path).unlink()
        if audit_action.action in {"create", "update"}:
            _safe_target(root, "audit.jsonl").write_bytes(audit_content)

    return OutputResult(root, tuple(actions), tuple(dict.fromkeys(unresolved)))


def format_plan(output: OutputResult) -> str:
    """Format file operations for the CLI, including stable totals."""

    lines = [f"Output: {output.output_dir}"]
    for action in output.actions:
        lines.append(f"{action.action:9} {action.relative_path}")
    totals: dict[str, int] = {}
    for action in output.actions:
        totals[action.action] = totals.get(action.action, 0) + 1
    summary = ", ".join(f"{name}={totals[name]}" for name in sorted(totals))
    lines.append(f"Totals: {summary}")
    for item in output.unresolved:
        lines.append(f"Unresolved: {item}")
    return "\n".join(lines)


# The name reflects that the writer now accepts files from multiple pipeline
# steps while retaining the original public roster helper.
plan_or_write_kit = plan_or_write_roster
