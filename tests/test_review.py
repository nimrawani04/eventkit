import pytest

from eventkit.model import Issue, IssueLevel
from eventkit.review import (
    LedgerParseError,
    LedgerValidationError,
    ResolutionLedger,
    ResolutionRecord,
    ReviewError,
    apply_decisions,
    check_privacy,
    find_email_pii,
    issue_status,
    make_record,
    parse_ledger,
    render_ledger,
    render_ledger_bytes,
    render_review_report,
    unresolved_issues,
    validate_decisions,
)


def _findings():
    return (
        Issue(
            code="EK06",
            level=IssueLevel.WARNING,
            row_number=4,
            message="Email domain looks misspelled.",
            field="email",
        ),
        Issue(
            code="EK05",
            level=IssueLevel.WARNING,
            row_number=3,
            message="Phone is too short.",
            field="phone",
        ),
        Issue(
            code="EK01",
            level=IssueLevel.ERROR,
            row_number=2,
            message="Name or email is missing.",
            field="email",
        ),
    )


def test_jsonl_round_trip_is_human_editable_and_canonical():
    records = [
        make_record("EK06", 4, "replace", "Corrected after checking the form.", "ana", field="email", value="person@example.com"),
        make_record("EK01", 2, "accept", "The blank value is intentional.", "zoe"),
        make_record("EK05", 3, "defer", "Need to ask the attendee.", "ana"),
        make_record("EK02", 8, "reject", "This finding came from an obsolete rule.", "zoe"),
    ]

    rendered = render_ledger(records)
    parsed = parse_ledger(rendered)

    assert parsed.records == tuple(sorted(records, key=lambda record: record.key))
    assert parse_ledger(render_ledger(parsed)) == parsed
    assert rendered.splitlines()[0] == '{"schema_version":1,"type":"eventkit-review-ledger"}'
    assert '"action":"replace"' in rendered


def test_invalid_records_and_stale_decisions_are_rejected():
    with pytest.raises(ReviewError, match="action must be one of"):
        make_record("EK01", 2, "ignore", "No reason", "reviewer")
    with pytest.raises(ReviewError, match="reason is required"):
        make_record("EK01", 2, "accept", "", "reviewer")
    with pytest.raises(ReviewError, match="reviewer is required"):
        make_record("EK01", 2, "accept", "A reason", "")
    with pytest.raises(ReviewError, match="replace decisions require"):
        make_record("EK01", 2, "replace", "Fix it", "reviewer", field="email")
    with pytest.raises(ReviewError, match="duplicate decision"):
        ResolutionLedger(
            (
                make_record("EK01", 2, "accept", "One", "reviewer"),
                make_record("EK01", 2, "reject", "Two", "reviewer"),
            )
        )

    stale = ResolutionLedger((make_record("EK99", 2, "accept", "No longer present", "reviewer"),))
    with pytest.raises(LedgerValidationError, match="do not match findings") as error:
        validate_decisions(stale, _findings())
    assert "EK99 row 2" in error.value.errors[0]

    malformed = '{"schema_version":1,"type":"eventkit-review-ledger"}\n{"action":"accept"}\n'
    with pytest.raises(LedgerParseError, match="missing required"):
        parse_ledger(malformed)


def test_unresolved_status_and_explicit_replacement_do_not_mutate_source():
    findings = _findings()
    ledger = ResolutionLedger(
        (
            make_record("EK01", 2, "accept", "Confirmed missing email is expected.", "reviewer"),
            make_record("EK05", 3, "defer", "Waiting for confirmation.", "reviewer"),
            make_record(
                "EK06",
                4,
                "replace",
                "Use the corrected address supplied by the owner.",
                "reviewer",
                field="email",
                value="corrected@example.com",
            ),
        )
    )
    rows = [
        {"row_number": 2, "email": ""},
        {"row_number": 3, "phone": "123"},
        {"row_number": 4, "email": "typo@example.con"},
    ]

    assert [item.row_number for item in unresolved_issues(findings, ledger)] == [3]
    assert issue_status(findings[0], ledger) == "resolved"
    assert issue_status(findings[1], ledger) == "unresolved"
    applied = apply_decisions(rows, findings, ledger)

    assert applied.rows[2]["email"] == "corrected@example.com"
    assert rows[2]["email"] == "typo@example.con"
    assert findings[0].message == "Email domain looks misspelled."
    assert [item.row_number for item in applied.unresolved] == [3]


def test_rendered_bytes_are_deterministic_and_report_has_resolved_sections():
    findings = _findings()
    ledger = ResolutionLedger(
        (
            make_record("EK01", 2, "reject", "Reviewed as a false positive.", "reviewer"),
        )
    )

    first = render_ledger_bytes(ledger)
    second = render_ledger_bytes(parse_ledger(first))
    report = render_review_report(findings, ledger)

    assert first == second
    assert report == render_review_report(findings, ledger)
    assert "## Unresolved" in report
    assert "## Resolved" in report
    assert "EK01" in report
    assert "EK05" in report


def test_public_private_privacy_check_and_public_report_redaction():
    text = "Contact person@example.com or backup@sub.example.org."

    assert find_email_pii(text) == ("person@example.com", "backup@sub.example.org")
    public = check_privacy(text, audience="public")
    private = check_privacy(text, audience="private")
    assert public.contains_pii is True
    assert public.safe is False
    assert private.contains_pii is True
    assert private.safe is True

    finding = Issue(
        code="EK01",
        level=IssueLevel.ERROR,
        row_number=2,
        message="Review person@example.com before publishing.",
    )
    ledger = ResolutionLedger((make_record("EK01", 2, "accept", "Done", "reviewer"),))
    report = render_review_report((finding,), ledger, audience="public")
    assert "person@example.com" not in report
    assert check_privacy(report, audience="public").safe is True
