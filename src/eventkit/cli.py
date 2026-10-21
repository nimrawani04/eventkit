"""Command-line entry point for the eventkit roster slice."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .agenda import render_agenda, verify_agenda_html
from .checkin import build_checkin_entries, build_checkin_file
from .checkin_runtime import CheckinRegistry
from .certificate_pipeline import build_certificate_bundle
from .calendar import render_ics, verify_ics
from .certificates import certificate_filename
from .clean import clean_event, format_clean_plan
from .dashboard import DashboardData, render_dashboard, verify_dashboard_html
from .codes import (
    canonicalize_code,
    format_certificate_code,
    manifest_from_json,
    secret_from_environment,
    verify as verify_manifest,
)
from .config import ConfigError, load_config
from .csvio import read_csv
from .emails import (
    DEFAULT_HTML_TEMPLATE,
    DEFAULT_TEXT_TEMPLATE,
    build_email_drafts,
    render_email_draft,
    render_mailmerge_csv,
    verify_email_draft,
)
from .impact import ImpactRun, render_impact_preview
from .risk import render_risk_report
from .verify_page import render_verify_page
from .model import RosterResult
from .output import format_plan, plan_or_write_roster
from .credentials import (
    derive_signing_key,
    issue_revocation,
    manifest_from_json as signed_manifest_from_json,
    revocation_to_dict,
    verify_credential,
    verify_manifest as verify_signed_manifest,
)
from .review import (
    ResolutionLedger,
    ReviewError,
    assert_public_privacy,
    parse_ledger,
    render_ledger_bytes,
    render_review_report,
    redact_email_pii,
    unresolved_issues,
    validate_decisions,
)
from .schedule import parse_schedule
from .validate import validate_roster


_SAMPLE_EVENT = """[event]
id = "demo-summit-2026"
name = "Demo Summit 2026"
organizer = "Campus Tech Club"
utc_offset = "+05:30"

[input]
registrations = "data/registrations.csv"
schedule = "data/schedule.csv"

[columns]
name = "Full Name"
email = "Email Address"
phone = "Phone"
institution = "College"

[output]
dir = "kit"
steps = ["roster", "schedule"]
"""

_SAMPLE_FULL_EVENT = """[event]
id = "demo-summit-2026"
name = "Demo Summit 2026"
organizer = "Campus Tech Club"
utc_offset = "+05:30"

[input]
registrations = "data/registrations.csv"
schedule = "data/schedule.csv"
attendance = "data/attendance.csv"

[columns]
name = "Full Name"
email = "Email Address"
phone = "Phone"
institution = "College"

[certificates]
title = "Certificate of Participation"
issue_to = "attended"
manifest_names = true

[output]
dir = "kit"
steps = ["roster", "checkin", "schedule", "certificates", "emails"]
"""

_SAMPLE_REGISTRATIONS = """Full Name,Email Address,Phone,College,Notes
Alice Example,alice@example.com,+1 202 555 0100,Demo University,ready
Missing Email,,+1 202 555 0101,Demo University,needs contact
Invalid Email,bad-address,+1 202 555 0102,Demo University,check address
First Person,first@example.com,202-555-0199,Demo University,original
Exact Later,FIRST@EXAMPLE.COM,202-555-0103,Demo University,duplicate
Phone Match,phone@example.com,202 555 0199,Demo University,possible duplicate
Short Phone,short@example.com,123-4567,Demo University,too short
Domain Typo,typo@gmial.com,+1 202 555 0108,Demo University,check domain
"""

_SAMPLE_SCHEDULE = """start,end,title,room,speaker
2026-05-01 09:00,2026-05-01 10:00,Opening,Hall 1,Ada
2026-05-01 10:00,2026-05-01 11:00,Workshop,Hall 1,Bea
2026-05-01 10:30,2026-05-01 11:30,Room clash,Hall 1,Chris
bad,2026-05-01 12:00,Bad time,Hall 2,
"""

_SAMPLE_ATTENDANCE = """email,role
alice@example.com,Participant
phone@example.com,Volunteer
unknown@example.com,Participant
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eventkit",
        description="Turn event registration CSVs into an explained roster kit.",
    )
    parser.add_argument("--version", action="version", version=f"eventkit {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="write a sample event and deliberate CSV problems")
    init.add_argument("dir", nargs="?", default=".", help="directory to initialize")
    init.add_argument("--full", action="store_true", help="include attendance and certificate/email steps")

    run = subparsers.add_parser("run", help="validate registrations and write the roster kit")
    run.add_argument("event_toml", help="path to event.toml")
    run.add_argument("--dry-run", action="store_true", help="show file operations without writing")
    run.add_argument("--strict", action="store_true", help="return 1 when error-level issues exist")
    run.add_argument("--out", help="override the output directory")

    verify = subparsers.add_parser("verify", help="verify a certificate code offline")
    verify.add_argument("code", help="certificate code to verify")
    verify.add_argument("--manifest", required=True, help="path to manifest.json")

    clean = subparsers.add_parser("clean", help="safely remove unchanged generated files")
    clean.add_argument("event_toml", help="path to event.toml")
    clean.add_argument("--dry-run", action="store_true", help="show cleanup without deleting")

    diff = subparsers.add_parser("diff", help="preview the impact of input changes")
    diff.add_argument("event_toml", help="path to event.toml")
    diff.add_argument("--include-names", action="store_true", help="include attendee names in the local preview")

    review = subparsers.add_parser("review", help="show the human-review state for an event")
    review.add_argument("event_toml", help="path to event.toml")
    review.add_argument("--public", action="store_true", help="redact email-shaped values")

    revoke = subparsers.add_parser("revoke", help="append a signed certificate revocation")
    revoke.add_argument("event_toml", help="path to event.toml")
    revoke.add_argument("code", help="certificate code to revoke")
    revoke.add_argument("--reason", required=True, help="human-readable revocation reason")
    revoke.add_argument("--out", help="relative revocations JSON path; defaults to configured path or revocations.json")

    return parser


def _write_if_missing(path: Path, content: str) -> str:
    if path.exists():
        return f"keep     {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return f"create   {path}"


def _init(directory: str, *, full: bool = False) -> int:
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    actions = [
        _write_if_missing(root / "event.toml", _SAMPLE_FULL_EVENT if full else _SAMPLE_EVENT),
        _write_if_missing(root / "data" / "registrations.csv", _SAMPLE_REGISTRATIONS),
        _write_if_missing(root / "data" / "schedule.csv", _SAMPLE_SCHEDULE),
    ]
    if full:
        actions.append(_write_if_missing(root / "data" / "attendance.csv", _SAMPLE_ATTENDANCE))
    print("\n".join(actions))
    print(f"Next: eventkit run {root / 'event.toml'} --dry-run")
    return 0


def _load_review_ledger(config) -> ResolutionLedger:
    if config.review_path is None or not config.review_path.is_file():
        return ResolutionLedger()
    try:
        return parse_ledger(config.review_path.read_bytes())
    except ReviewError as exc:
        raise ConfigError(f"Could not read review ledger {config.review_path}: {exc}") from exc


def _public_dashboard_attendees(result: RosterResult) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "name": row.name,
            "institution": row.institution,
            "status": row.status.value,
            "issues": row.issue_ids,
        }
        for row in result.rows
    )


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.event_toml)
    if not config.registrations_path.is_file():
        raise ConfigError(f"Registration CSV not found: {config.registrations_path}")
    table = read_csv(config.registrations_path)
    result = validate_roster(table, columns=config.columns)
    review_ledger = _load_review_ledger(config)
    extra_files: dict[str, bytes] = {}
    unresolved: list[str] = []
    verified: list[str] = []
    summary: list[str] = []
    schedule_result = None
    bundle = None
    drafts = ()
    schedule_errors = 0
    schedule_clashes = 0
    attendance_issues = 0
    certificate_count: int | None = None
    checkin_entries = ()
    if "schedule" in config.steps:
        if config.schedule_path is None:
            raise ConfigError(
                "Schedule step is enabled but input.schedule is not configured"
            )
        if not config.schedule_path.is_file():
            raise ConfigError(f"Schedule CSV not found: {config.schedule_path}")
        schedule_result = parse_schedule(
            read_csv(config.schedule_path),
            config.utc_offset,
        )
        schedule_errors = len(schedule_result.errors)
        schedule_clashes = len(schedule_result.issues_by_code("SC02")) // 2 + len(schedule_result.issues_by_code("SC03")) // 2
        result = RosterResult(
            result.rows,
            result.issues + tuple(schedule_result.issues),
            result.extra_columns,
        )
        summary.extend(
            (
                f"Schedule sessions: {len(schedule_result.sessions)}",
                f"Schedule exclusions: {len(schedule_result.excluded_row_numbers)}",
                f"Schedule clashes: {len(schedule_result.issues_by_code('SC02')) // 2 + len(schedule_result.issues_by_code('SC03')) // 2}",
            )
        )
        ics_content = render_ics(
            schedule_result,
            config.event_id,
            event_name=config.name,
        )
        agenda_content = render_agenda(schedule_result, event_name=config.name)
        try:
            verify_ics(ics_content, schedule_result)
            verified.append("Schedule ICS parses and contains all valid sessions.")
        except ValueError as exc:
            unresolved.append(f"Schedule ICS post-check failed: {exc}")
        try:
            verify_agenda_html(agenda_content, schedule_result)
            verified.append("Agenda HTML parses and contains all valid sessions.")
        except ValueError as exc:
            unresolved.append(f"Agenda HTML post-check failed: {exc}")
        extra_files["schedule.ics"] = ics_content
        extra_files["agenda.html"] = agenda_content
    if "checkin" in config.steps:
        checkin_content, checkin_entries, checkin_verified = build_checkin_file(
            config.event_id,
            config.name,
            result,
        )
        extra_files["checkin.html"] = checkin_content
        verified.append(checkin_verified)
    if "checkin-app" in config.steps:
        if not checkin_entries:
            checkin_entries = build_checkin_entries(
                config.event_id,
                result,
                secret_from_environment(),
            )
        known_codes = {
            entry.checkin_code: f"R-ROW-{entry.row_number:04d}"
            for entry in checkin_entries
        }
        registry = CheckinRegistry(config.event_id, known_codes, device_id="eventkit")
        public_attendees = [
            {
                "attendee_id": f"R-ROW-{entry.row_number:04d}",
                "checkin_code": entry.checkin_code,
                "name": entry.name,
                "institution": entry.institution,
            }
            for entry in checkin_entries
        ]
        extra_files["checkin-app.html"] = registry.render_html(
            title=config.name,
            public_attendees=public_attendees,
        )
        extra_files["checkin.log"] = registry.export_log().encode("utf-8")
        verified.append("Offline check-in app is self-contained and starts with an empty event log.")
    if "certificates" in config.steps:
        attendance_table = None
        if config.attendance_path is not None:
            if not config.attendance_path.is_file():
                raise ConfigError(f"Attendance CSV not found: {config.attendance_path}")
            attendance_table = read_csv(config.attendance_path)
        bundle = build_certificate_bundle(
            config,
            result,
            attendance_table=attendance_table,
        )
        extra_files.update(bundle.files)
        summary.append(f"Certificates: {len(bundle.issued)}")
        if bundle.skipped_reason:
            unresolved.append(bundle.skipped_reason)
        unresolved.extend(bundle.verification_issues)
        verified.extend(bundle.verified)
        if bundle.attendance is not None and bundle.attendance.issues:
            unresolved.extend(issue.message for issue in bundle.attendance.issues)
            result = RosterResult(
                result.rows,
                result.issues + bundle.attendance.issues,
                result.extra_columns,
            )
        if bundle.attendance is not None:
            attendance_issues = len(bundle.attendance.issues)
            summary.extend(
                (
                    f"Attendance rows: {bundle.attendance.rows_read}",
                    f"Certificate recipients: {len(bundle.issued)}",
                    f"Attendance issues: {len(bundle.attendance.issues)}",
                )
            )
        certificate_count = len(bundle.issued)
    if "emails" in config.steps:
        if "certificates" not in config.steps:
            raise ConfigError("Email drafts require the certificates step")
        if bundle is None or not bundle.issued:
            drafts = ()
        else:
            drafts = tuple(
                render_email_draft(
                    certificate.name or "Attendee",
                    certificate.email,
                    config.name,
                    certificate.code,
                    text_template=config.email_text or DEFAULT_TEXT_TEMPLATE,
                    html_template=config.email_html or DEFAULT_HTML_TEMPLATE,
                    subject_template=config.email_subject,
                    certificate=(
                        bundle.files.get(
                            f"certificates/{certificate_filename(certificate.name or '', certificate.code)}"
                        )
                    ),
                )
                for certificate in bundle.issued
            )
        for draft in drafts:
            extra_files[f"emails/{draft.filename}"] = draft.message
            try:
                verify_email_draft(
                    draft,
                    expected_email=draft.email,
                    expected_attachment=draft.has_certificate,
                )
            except ValueError as exc:
                unresolved.append(f"Email draft post-check failed: {exc}")
            else:
                verified.append(f"Email draft parses: {draft.filename}.")
        extra_files["mailmerge.csv"] = render_mailmerge_csv(drafts)
        verified.append("Mail-merge CSV matches the generated email drafts.")
        summary.extend(
            (
                f"Email drafts: {len(drafts)}",
                f"Email attachments: {sum(1 for draft in drafts if draft.has_certificate)}",
            )
        )
    if "certificates" in config.steps and "manifest.json" in extra_files:
        manifest = manifest_from_json(extra_files["manifest.json"].decode("utf-8"))
        extra_files["verify.html"] = render_verify_page(manifest.as_dict())
        verified.append("Offline verification page embeds the public manifest.")

    review_open = tuple(result.issues)
    if review_ledger.records:
        try:
            validate_decisions(review_ledger, result.issues)
            review_open = unresolved_issues(result.issues, review_ledger)
        except ReviewError as exc:
            raise ConfigError(f"Review decisions do not match this run: {exc}") from exc
        resolved_messages = {
            issue.message
            for issue in result.issues
            if issue not in review_open
        }
        unresolved = [item for item in unresolved if item not in resolved_messages]
        unresolved.extend(issue.message for issue in review_open)
    if config.review_path is not None:
        extra_files["review.md"] = render_review_report(
            result.issues,
            review_ledger,
            audience=config.privacy_profile,
        ).encode("utf-8")
        verified.append("Human-review report records resolved and unresolved findings.")

    strict_errors = tuple(
        issue for issue in (review_open if review_ledger.records else result.errors)
        if issue.level.value == "error"
    )
    if config.privacy_profile == "public":
        unresolved = [redact_email_pii(item) for item in unresolved]
    extra_files["risk.md"] = render_risk_report(
        result,
        schedule_errors=schedule_errors,
        schedule_clashes=schedule_clashes,
        attendance_issues=attendance_issues,
        certificates=certificate_count,
        unresolved=unresolved,
        audience=config.privacy_profile,
    )
    verified.append("Risk report summarizes all enabled-step unresolved conditions.")
    deliverables = {
        "roster.csv": True,
        "issues.md": True,
        "report.md": True,
        "audit.jsonl": True,
        **{path: True for path in extra_files},
    }
    deliverables["dashboard.html"] = True
    dashboard_content = render_dashboard(
        DashboardData(
            event_title=config.name,
            organizer=config.organizer,
            status="Needs review" if unresolved or strict_errors else "Ready",
            status_detail=(
                "Review the unresolved findings before publishing."
                if unresolved or strict_errors
                else "All enabled output checks completed."
            ),
            metrics={
                "registered": len(result.rows),
                "included": len(result.included_rows),
                "excluded": len(result.excluded_rows),
                "warnings": len(result.warnings),
                "errors": len(result.errors),
                "sessions": len(schedule_result.sessions) if schedule_result is not None else 0,
                "certificates": len(bundle.issued) if bundle is not None else 0,
                "drafts": len(drafts),
            },
            risk_summary=(
                "See risk.md for the deterministic operational risk summary."
                if unresolved or strict_errors
                else "No unresolved findings were recorded for this run."
            ),
            unresolved=unresolved,
            attendees=(
                _public_dashboard_attendees(result)
                if config.privacy_profile == "public"
                else result
            ),
            schedule=schedule_result,
            certificates=bundle,
            deliverables=deliverables,
        )
    )
    try:
        verify_dashboard_html(dashboard_content)
        verified.append("Offline dashboard HTML parses and contains all run sections.")
    except ValueError as exc:
        unresolved.append(f"Dashboard post-check failed: {exc}")
    extra_files["dashboard.html"] = dashboard_content
    output = plan_or_write_roster(
        config,
        result,
        dry_run=args.dry_run,
        output_dir=args.out,
        extra_files=extra_files,
        extra_unresolved=unresolved,
        extra_verified=verified,
        extra_summary=summary,
    )
    mode = "Dry run" if args.dry_run else "Run"
    print(
        f"{mode}: {len(result.rows)} rows, "
        f"{len(result.included_rows)} included, "
        f"{len(result.excluded_rows)} excluded, "
        f"{len(result.errors)} errors, {len(result.warnings)} warnings"
    )
    print(format_plan(output))
    if args.dry_run:
        print("No files written.")
    if args.strict and strict_errors:
        return 1
    return 0


def _verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Could not read manifest {args.manifest}: {exc}") from exc
    try:
        signed_manifest = signed_manifest_from_json(manifest_text)
    except (ReviewError, ValueError, TypeError):
        signed_manifest = None
    if signed_manifest is not None:
        if not verify_signed_manifest(signed_manifest):
            print("INVALID")
            return 1
        try:
            code = canonicalize_code(args.code)
        except ValueError:
            code = ""
        matching = next(
            (
                credential
                for credential in signed_manifest.credentials
                if canonicalize_code(credential.code) == code
            ),
            None,
        )
        if matching is None or not verify_credential(
            matching,
            signed_manifest.public_key,
            event_id=signed_manifest.event_id,
            revocations=signed_manifest.revocations,
        ):
            print("INVALID")
            return 1
        print("VALID")
        print(f"Event: {signed_manifest.event_id}")
        print(f"Role: {matching.role}")
        if matching.holder is not None:
            print(f"Name: {matching.holder}")
        print(f"Issuer: {signed_manifest.issuer_key_id}")
        return 0
    try:
        manifest = manifest_from_json(manifest_text)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"Manifest is not a supported eventkit manifest: {args.manifest}") from exc
    result = verify_manifest(args.code, manifest)
    if not result.valid:
        print("INVALID")
        return 1
    print("VALID")
    print(f"Event: {result.event_name} ({result.event_id})")
    if result.role is not None:
        print(f"Role: {result.role}")
    if result.name is not None:
        print(f"Name: {result.name}")
    return 0


def _review(args: argparse.Namespace) -> int:
    config = load_config(args.event_toml)
    if not config.registrations_path.is_file():
        raise ConfigError(f"Registration CSV not found: {config.registrations_path}")
    result = validate_roster(read_csv(config.registrations_path), columns=config.columns)
    ledger = _load_review_ledger(config)
    if ledger.records:
        try:
            validate_decisions(ledger, result.issues)
        except ReviewError as exc:
            raise ConfigError(f"Review decisions do not match this run: {exc}") from exc
    report = render_review_report(
        result.issues,
        ledger,
        audience="public" if args.public else config.privacy_profile,
    )
    print(report, end="")
    return 0


def _revoke(args: argparse.Namespace) -> int:
    config = load_config(args.event_toml)
    secret = secret_from_environment()
    root = config.path.parent.resolve()
    configured = config.revocations_path
    target = Path(args.out).expanduser() if args.out else (
        configured if configured is not None else root / "revocations.json"
    )
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConfigError("Revocations output must stay inside the event directory") from exc
    existing: list[dict[str, object]] = []
    if target.exists():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Could not read existing revocations file: {exc}") from exc
        values = raw.get("revocations", []) if isinstance(raw, dict) else raw
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ConfigError("Revocations file must contain a list or a revocations list")
        existing = list(values)
    try:
        normalized_code = format_certificate_code(canonicalize_code(args.code))
    except ValueError as exc:
        raise ConfigError(f"Invalid certificate code: {args.code}") from exc
    if any(item.get("code") == normalized_code for item in existing):
        raise ConfigError(f"Certificate code is already revoked: {normalized_code}")
    revocation = issue_revocation(
        config.event_id,
        normalized_code,
        derive_signing_key(secret),
        reason=args.reason,
    )
    existing.append(revocation_to_dict(revocation))
    rendered = json.dumps(
        {"event_id": config.event_id, "revocations": existing},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="")
    print(f"Created signed revocation in {target}")
    return 0


def _clean(args: argparse.Namespace) -> int:
    result = clean_event(args.event_toml, dry_run=args.dry_run)
    print(format_clean_plan(result))
    if args.dry_run:
        print("No files removed.")
    return 0


def _diff(args: argparse.Namespace) -> int:
    config = load_config(args.event_toml)
    if not config.registrations_path.is_file():
        raise ConfigError(f"Registration CSV not found: {config.registrations_path}")
    current = validate_roster(read_csv(config.registrations_path), columns=config.columns)
    previous = None
    previous_roster = config.output_dir / "roster.csv"
    if previous_roster.is_file():
        previous = validate_roster(read_csv(previous_roster))
    print(
        render_impact_preview(
            ImpactRun(roster=current),
            ImpactRun(roster=previous) if previous is not None else None,
            include_names=args.include_names,
        ),
        end="",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.dir, full=args.full)
        if args.command == "run":
            return _run(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "clean":
            return _clean(args)
        if args.command == "diff":
            return _diff(args)
        if args.command == "review":
            return _review(args)
        if args.command == "revoke":
            return _revoke(args)
        parser.print_help()
        return 2
    except (
        ConfigError,
        FileNotFoundError,
        OSError,
        UnicodeError,
        ValueError,
        ReviewError,
        csv.Error,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
