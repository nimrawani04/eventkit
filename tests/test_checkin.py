from eventkit.checkin import (
    build_checkin_entries,
    render_checkin_html,
    verify_checkin_html,
)
from eventkit.csvio import read_csv_text
from eventkit.validate import validate_roster


SECRET = "checkin-test-secret"


def _roster():
    table = read_csv_text(
        "name,email,phone,institution\n"
        'Zoe & Co,zoe@example.com,12345678,"North <College>"\n'
        "Ada Lovelace,ada@example.com,12345679,South College\n"
        "First,first@example.com,12345681,South College\n"
        "Duplicate,FIRST@example.com,12345680,South College\n"
    )
    return validate_roster(table)


def test_checkin_omits_excluded_rows_and_sorts_by_normalized_name():
    entries = build_checkin_entries("event-2026", _roster(), SECRET)

    assert [entry.name for entry in entries] == ["Ada Lovelace", "First", "Zoe & Co"]
    assert all(entry.checkin_code for entry in entries)
    assert len({entry.checkin_code for entry in entries}) == 3
    assert all(entry.name != "Duplicate" for entry in entries)


def test_checkin_html_is_escaped_printable_and_deterministic():
    entries = build_checkin_entries("event-2026", _roster(), SECRET)
    first = render_checkin_html(entries, event_name="Event <One> & Friends")
    second = render_checkin_html(entries, event_name="Event <One> & Friends")

    assert first == second
    assert b"@media print" in first
    assert b"Check-in code" in first
    assert b"Event &lt;One&gt; &amp; Friends" in first
    assert b"North &lt;College&gt;" in first
    assert b"Zoe &amp; Co" in first
    verify_checkin_html(first, len(entries))


def test_empty_checkin_sheet_has_a_valid_empty_state():
    content = render_checkin_html([], event_name="Empty Event")

    assert b"No included attendees." in content
    verify_checkin_html(content, 0)
