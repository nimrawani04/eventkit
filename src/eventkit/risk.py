"""Deterministic operational risk reports for an event kit."""

from __future__ import annotations

from collections.abc import Iterable

from .model import IssueLevel, RosterResult
from .review import redact_email_pii


def render_risk_report(
    roster: RosterResult,
    *,
    schedule_errors: int = 0,
    schedule_clashes: int = 0,
    attendance_issues: int = 0,
    certificates: int | None = None,
    unresolved: Iterable[str] = (),
    audience: str = "private",
) -> bytes:
    """Render a stable human-readable risk summary without personal data."""

    risks: list[tuple[str, str]] = []
    if roster.errors:
        risks.append(("high", f"{len(roster.errors)} roster error(s) exclude registrations."))
    if schedule_errors:
        risks.append(("high", f"{schedule_errors} schedule session error(s) exclude calendar entries."))
    if schedule_clashes:
        risks.append(("high", f"{schedule_clashes} schedule clash pair(s) need organizer review."))
    if attendance_issues:
        risks.append(("medium", f"{attendance_issues} attendance row(s) need review."))
    if roster.warnings:
        risks.append(("medium", f"{len(roster.warnings)} roster warning(s) need human review."))
    for item in unresolved:
        risks.append(("medium", item))
    if not risks:
        risks.append(("low", "No unresolved operational risks were reported by the enabled steps."))

    score = min(100, sum(50 if level == "high" else 20 if level == "medium" else 0 for level, _ in risks))
    lines = [
        "# Event risk report",
        "",
        f"Risk score: **{score}/100**",
        "",
        "## Counts",
        "",
        f"- Included registrations: {len(roster.included_rows)}",
        f"- Excluded registrations: {len(roster.excluded_rows)}",
        f"- Roster errors: {len(roster.errors)}",
        f"- Roster warnings: {len(roster.warnings)}",
        f"- Schedule errors: {schedule_errors}",
        f"- Schedule clashes: {schedule_clashes}",
        f"- Attendance issues: {attendance_issues}",
        f"- Certificates: {certificates if certificates is not None else 'not enabled'}",
        "",
        "## Risks",
        "",
    ]
    lines.extend(
        f"- **{level}** — "
        + (redact_email_pii(message) if audience == "public" else message)
        for level, message in risks
    )
    lines.append("")
    return "\n".join(lines).encode("utf-8")


__all__ = ["render_risk_report"]
