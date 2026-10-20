from pathlib import Path

from eventkit import RowStatus, read_csv, read_csv_text, validate_roster


FIXTURE = Path(__file__).parent / "fixtures" / "roster-validation" / "roster.csv"


def _result():
    return validate_roster(read_csv(FIXTURE))


def test_roster_validation_preserves_source_order_and_row_numbers():
    result = _result()

    assert [row.name for row in result.rows] == [
        "Missing email",
        "Invalid email",
        "First Person",
        "Exact Later",
        "Phone Match One",
        "Phone Match Two",
        "Short Phone",
        "Domain Typo",
        "Long Phone",
    ]
    assert [row.row_number for row in result.rows] == list(range(2, 11))
    assert [issue.row_number for issue in result.issues] == [2, 3, 5, 6, 7, 8, 9, 10]


def test_missing_and_invalid_email_are_excluded_with_ek01_and_ek02():
    result = _result()

    assert result.rows[0].issue_ids == ("EK01",)
    assert result.rows[0].status is RowStatus.EXCLUDED
    assert result.rows[1].issue_ids == ("EK02",)
    assert result.rows[1].status is RowStatus.EXCLUDED


def test_exact_duplicate_keeps_first_row_and_excludes_later_row():
    result = _result()

    first, later = result.rows[2:4]

    assert first.issue_ids == ()
    assert first.status is RowStatus.OK
    assert later.issue_ids == ("EK03",)
    assert later.status is RowStatus.EXCLUDED
    duplicate = next(issue for issue in result.issues if issue.code == "EK03")
    assert duplicate.row_number == later.row_number
    assert duplicate.related_row_number == first.row_number


def test_possible_duplicate_same_phone_is_a_warning_on_both_rows():
    result = _result()

    first, second = result.rows[4:6]

    assert first.issue_ids == ("EK04",)
    assert second.issue_ids == ("EK04",)
    assert first.status is RowStatus.OK
    assert second.status is RowStatus.OK
    related_rows = {
        (issue.row_number, issue.related_row_number)
        for issue in result.issues
        if issue.code == "EK04"
    }
    assert related_rows == {(6, 7), (7, 6)}


def test_short_and_long_phones_get_ek05_without_excluding_rows():
    result = _result()

    short_row = result.rows[6]
    long_row = result.rows[8]

    assert short_row.issue_ids == ("EK05",)
    assert long_row.issue_ids == ("EK05",)
    assert short_row.status is RowStatus.OK
    assert long_row.status is RowStatus.OK
    issues = [issue for issue in result.issues if issue.code == "EK05"]
    assert {issue.row_number for issue in issues} == {8, 10}
    assert {issue.message for issue in issues} == {
        "Phone has 7 digits; expected between 8 and 15.",
        "Phone has 16 digits; expected between 8 and 15.",
    }


def test_common_domain_typo_gets_ek06_suggestion_without_auto_fixing_email():
    result = _result()

    row = result.rows[7]

    assert row.issue_ids == ("EK06",)
    assert row.status is RowStatus.OK
    assert row.attendee.email == "typo@gmial.com"
    assert row.attendee.normalized_email == "typo@gmial.com"
    issue = next(issue for issue in result.issues if issue.code == "EK06")
    assert issue.suggestion == "Check the spelling; did you mean gmail.com?"


def test_result_retains_extra_columns_and_reports_quoted_empty_name():
    table = read_csv_text(
        'name,email,notes\n"",person@example.com,"kept for report"\n'
    )

    result = validate_roster(table)

    assert result.extra_columns == ("notes",)
    assert result.rows[0].issue_ids == ("EK01",)
    assert result.rows[0].row_number == 2


def test_near_duplicate_name_is_found_past_large_group_boundary():
    rows = [
        {"row_number": 2, "name": "Alice Example", "email": "alice@example.com", "institution": "Same University"},
        {"row_number": 3, "name": "Xlice Example", "email": "xlice@example.com", "institution": "Same University"},
    ]
    rows.extend(
        {
            "row_number": index + 2,
            "name": f"Registrant {index:03d}",
            "email": f"registrant{index}@example.com",
            "institution": "Same University",
        }
        for index in range(249)
    )

    result = validate_roster(rows)

    near_duplicate_issues = [
        issue
        for issue in result.issues
        if issue.code == "EK04" and issue.related_row_number in {2, 3}
    ]
    assert {(issue.row_number, issue.related_row_number) for issue in near_duplicate_issues} >= {
        (2, 3),
        (3, 2),
    }
