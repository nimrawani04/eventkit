from eventkit import filter_attendance, read_csv_text, validate_roster


def test_attendance_filters_unknown_and_excluded_registrations():
    registrations = read_csv_text(
        "name,email,phone,institution\n"
        "Ada Lovelace,ada@example.com,12345678,University\n"
        ",excluded@example.com,12345679,University\n"
    )
    attendance = read_csv_text(
        "email,role\n"
        "ada@example.com,Speaker\n"
        "missing@example.com,Participant\n"
        "excluded@example.com,Participant\n"
        ",Participant\n"
    )

    result = filter_attendance(attendance, validate_roster(registrations))

    assert [(item.email, item.role, item.name) for item in result.recipients] == [
        ("ada@example.com", "Speaker", "Ada Lovelace")
    ]
    assert [issue.code for issue in result.issues] == ["EK07", "EK07", "EK07"]
    assert [issue.row_number for issue in result.issues] == [3, 4, 5]
