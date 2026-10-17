"""Core data handling for eventkit.

The package currently contains the reusable roster-ingestion pieces.  The
command-line interface and file writers are intentionally left for later
milestones.
"""

from .config import ConfigError, EventConfig, load_config
from .csvio import CsvRow, CsvTable, read_csv, read_csv_text
from .output import FileAction, OutputResult, format_plan, plan_or_write_roster, render_issues_md, render_report, render_roster_csv
from .model import (
    Attendee,
    Issue,
    IssueLevel,
    RosterResult,
    RosterRow,
    RowStatus,
)
from .normalize import (
    COMMON_EMAIL_DOMAINS,
    email_domain,
    email_domain_suggestion,
    is_valid_email,
    likely_common_domain_typo,
    normalize_attendee,
    normalize_email,
    normalize_institution,
    normalize_name,
    normalize_phone,
)
from .validate import (
    attendees_from_csv,
    attendee_from_mapping,
    validate,
    validate_roster,
)

__all__ = [
    "Attendee",
    "COMMON_EMAIL_DOMAINS",
    "ConfigError",
    "CsvRow",
    "CsvTable",
    "EventConfig",
    "FileAction",
    "Issue",
    "IssueLevel",
    "OutputResult",
    "RosterResult",
    "RosterRow",
    "RowStatus",
    "attendee_from_mapping",
    "attendees_from_csv",
    "email_domain",
    "email_domain_suggestion",
    "is_valid_email",
    "likely_common_domain_typo",
    "normalize_attendee",
    "normalize_email",
    "normalize_institution",
    "normalize_name",
    "normalize_phone",
    "format_plan",
    "load_config",
    "plan_or_write_roster",
    "render_issues_md",
    "render_report",
    "render_roster_csv",
    "read_csv",
    "read_csv_text",
    "validate",
    "validate_roster",
]

__version__ = "0.1.0"
