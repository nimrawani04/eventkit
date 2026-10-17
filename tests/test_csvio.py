from pathlib import Path

from eventkit import read_csv_text


FIXTURE = Path(__file__).parent / "fixtures" / "messy-roster" / "roster.csv"


def test_csv_reader_handles_bom_quoted_multiline_fields_and_physical_lines():
    text = "\ufeff" + FIXTURE.read_text(encoding="utf-8")

    table = read_csv_text(text)

    assert table.headers == ("name", "email", "notes")
    assert table.header_line_number == 1
    assert [row.line_number for row in table.rows] == [2, 5]
    assert [row.end_line_number for row in table.rows] == [3, 5]
    assert table.rows[0].values == (
        "Alice Example",
        "alice@example.com",
        'Attendee said, "hello"\non two lines',
    )
    assert table.rows[1].as_dict(table.headers) == {
        "name": "Bob Example",
        "email": "bob@example.com",
        "notes": "single line",
    }


def test_quoted_empty_record_is_not_treated_as_a_blank_line():
    table = read_csv_text('name,email,extra\n"",person@example.com,value\n')

    assert len(table.rows) == 1
    assert table.rows[0].line_number == 2
    assert table.dict_rows_with_metadata()[0]["_line"] == 2
    assert table.dict_rows_with_metadata()[0]["_end_line"] == 2
