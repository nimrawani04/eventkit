"""Command-line entry point for the eventkit roster slice."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import __version__
from .agenda import render_agenda, verify_agenda_html
from .checkin import build_checkin_file
from .certificate_pipeline import build_certificate_bundle
from .calendar import render_ics, verify_ics
from .codes import manifest_from_json, verify as verify_manifest
from .config import ConfigError, load_config
from .csvio import read_csv
from .model import RosterResult
from .output import format_plan, plan_or_write_roster
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eventkit",
        description="Turn event registration CSVs into an explained roster kit.",
    )
    parser.add_argument("--version", action="version", version=f"eventkit {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="write a sample event and deliberate CSV problems")
    init.add_argument("dir", nargs="?", default=".", help="directory to initialize")

    run = subparsers.add_parser("run", help="validate registrations and write the roster kit")
    run.add_argument("event_toml", help="path to event.toml")
    run.add_argument("--dry-run", action="store_true", help="show file operations without writing")
    run.add_argument("--strict", action="store_true", help="return 1 when error-level issues exist")
    run.add_argument("--out", help="override the output directory")

    verify = subparsers.add_parser("verify", help="verify a certificate code offline")
    verify.add_argument("code", help="certificate code to verify")
    verify.add_argument("--manifest", required=True, help="path to manifest.json")

    return parser


def _write_if_missing(path: Path, content: str) -> str:
    if path.exists():
        return f"keep     {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return f"create   {path}"


def _init(directory: str) -> int:
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    actions = [
        _write_if_missing(root / "event.toml", _SAMPLE_EVENT),
        _write_if_missing(root / "data" / "registrations.csv", _SAMPLE_REGISTRATIONS),
        _write_if_missing(root / "data" / "schedule.csv", _SAMPLE_SCHEDULE),
    ]
    print("\n".join(actions))
    print(f"Next: eventkit run {root / 'event.toml'} --dry-run")
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.event_toml)
    if not config.registrations_path.is_file():
        raise ConfigError(f"Registration CSV not found: {config.registrations_path}")
    table = read_csv(config.registrations_path)
    result = validate_roster(table, columns=config.columns)
    extra_files: dict[str, bytes] = {}
    unresolved: list[str] = []
    verified: list[str] = []
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
        result = RosterResult(
            result.rows,
            result.issues + tuple(schedule_result.issues),
            result.extra_columns,
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
        checkin_content, _, checkin_verified = build_checkin_file(
            config.event_id,
            config.name,
            result,
        )
        extra_files["checkin.html"] = checkin_content
        verified.append(checkin_verified)
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
        if bundle.skipped_reason:
            unresolved.append(bundle.skipped_reason)
        unresolved.extend(bundle.verification_issues)
        verified.extend(bundle.verified)
        if bundle.attendance is not None and bundle.attendance.issues:
            result = RosterResult(
                result.rows,
                result.issues + bundle.attendance.issues,
                result.extra_columns,
            )
    output = plan_or_write_roster(
        config,
        result,
        dry_run=args.dry_run,
        output_dir=args.out,
        extra_files=extra_files,
        extra_unresolved=unresolved,
        extra_verified=verified,
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
    if args.strict and result.errors:
        return 1
    return 0


def _verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    try:
        manifest = manifest_from_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Could not read manifest {args.manifest}: {exc}") from exc
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.dir)
        if args.command == "run":
            return _run(args)
        if args.command == "verify":
            return _verify(args)
        parser.print_help()
        return 2
    except (ConfigError, FileNotFoundError, OSError, UnicodeError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
