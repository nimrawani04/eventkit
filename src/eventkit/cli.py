"""Command-line entry point for the eventkit roster slice."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .csvio import read_csv
from .output import format_plan, plan_or_write_roster
from .validate import validate_roster


_SAMPLE_EVENT = """[event]
id = "demo-summit-2026"
name = "Demo Summit 2026"
organizer = "Campus Tech Club"
utc_offset = "+05:30"

[input]
registrations = "data/registrations.csv"

[columns]
name = "Full Name"
email = "Email Address"
phone = "Phone"
institution = "College"

[output]
dir = "kit"
steps = ["roster"]
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
    output = plan_or_write_roster(
        config,
        result,
        dry_run=args.dry_run,
        output_dir=args.out,
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


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.dir)
        if args.command == "run":
            return _run(args)
        parser.print_help()
        return 0
    except (ConfigError, FileNotFoundError, OSError, UnicodeError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
