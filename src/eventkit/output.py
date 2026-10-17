"""Deterministic roster renderers and a small audited file writer."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
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
        f"- Files planned: {len(actions) + 1}",
        "",
        "## Verified",
        "",
        "- Registration CSV was read with BOM-safe, physical-line tracking.",
        "- Every source row is represented in the generated roster.",
        "- Roster and issue output is deterministic for the same inputs.",
        "",
        "## Unresolved",
        "",
    ]
    if unresolved_items:
        lines.extend(f"- {item}" for item in unresolved_items)
    elif result.issues:
        lines.append("- See `issues.md` for the registration issues that need human review.")
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _read_audit(path: Path) -> dict[str, str]:
    """Read prior file hashes; malformed audit data means no ownership."""

    if not path.is_file():
        return {}
    records: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict) and isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                records[value["path"]] = value["sha256"]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return records


def _file_action(
    output_dir: Path,
    relative_path: str,
    content: bytes,
    previous_hashes: dict[str, str],
) -> FileAction:
    target = output_dir / relative_path
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
    return FileAction(relative_path, action, len(content), digest)


def _audit_bytes(input_digest: str, files: Iterable[FileAction]) -> bytes:
    records = [{"input_digest": input_digest}]
    records.extend(
        {
            "path": item.relative_path,
            "sha256": item.sha256,
            "size": item.size,
            "step": "roster",
        }
        for item in files
        if item.relative_path != "audit.jsonl"
    )
    return "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")


def _input_digest(config: EventConfig) -> str:
    digest = hashlib.sha256()
    for path in (config.path, config.registrations_path):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def plan_or_write_roster(
    config: EventConfig,
    result: RosterResult,
    *,
    dry_run: bool = False,
    output_dir: str | Path | None = None,
) -> OutputResult:
    """Plan or write roster files using deterministic ownership checks."""

    root = (Path(output_dir) if output_dir is not None else config.output_dir).expanduser().resolve()
    previous_hashes = _read_audit(root / "audit.jsonl")
    file_contents = {
        "roster.csv": render_roster_csv(result),
        "issues.md": render_issues_md(result),
    }
    first_actions = tuple(
        _file_action(root, name, content, previous_hashes)
        for name, content in file_contents.items()
    )
    report_unresolved = [
        f"Output file kept because it was edited or not owned: {item.relative_path}"
        for item in first_actions
        if item.action == "keep"
    ]
    report_content = render_report(result, first_actions, report_unresolved)
    file_contents["report.md"] = report_content
    actions = list(
        _file_action(root, name, content, previous_hashes)
        for name, content in file_contents.items()
    )
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
        elif previous_hashes:
            audit_operation = "update"
        else:
            audit_operation = "keep"
    audit_action = FileAction(
        "audit.jsonl",
        audit_operation,
        len(audit_content),
        audit_digest,
    )
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
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if audit_action.action in {"create", "update"}:
            (root / "audit.jsonl").write_bytes(audit_content)

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
