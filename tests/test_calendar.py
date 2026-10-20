import hashlib

from eventkit.calendar import event_uid, parse_ics, render_ics, verify_ics
from eventkit.schedule import parse_schedule


def _schedule():
    title = 'A very long title with comma, semicolon; slash \\ and <tag> ' + "x" * 80
    return parse_schedule(
        "start,end,title,room,speaker\n"
        f'2026-05-01 09:30,2026-05-01 10:30,"{title}",Room,Speaker\n',
        utc_offset="+05:30",
    )


def test_ics_is_deterministic_utc_escaped_and_folded():
    result = _schedule()
    first = render_ics(result, "event-2026", event_name="Event, <One>")
    second = render_ics("event-2026", result, event_name="Event, <One>")

    assert first == second
    assert first.endswith(b"\r\n")
    assert b"\n" not in first.replace(b"\r\n", b"")
    assert b"SUMMARY:A very long title with comma\\, semicolon\\; slash" in first
    assert b"slash \\\\ and <tag>" in first
    assert b"X-WR-CALNAME:Event\\, <One>" in first
    assert b"DTSTART:20260501T040000Z" in first
    assert all(len(line) <= 75 for line in first.split(b"\r\n") if line)
    assert any(line.startswith(b" ") for line in first.split(b"\r\n"))

    session = result.sessions[0]
    expected_uid = hashlib.sha256(
        f"event-2026|{session.title}|{session.start.isoformat()}".encode("utf-8")
    ).hexdigest()
    assert event_uid("event-2026", session) == expected_uid
    parsed = verify_ics(first, result)
    assert len(parsed) == 1
    assert parsed[0]["UID"] == expected_uid
    assert parsed[0]["DTSTAMP"] == parsed[0]["DTSTART"]


def test_ics_post_parser_unfolds_long_properties():
    result = _schedule()
    content = render_ics(result, event_id="event-2026")
    parsed = parse_ics(content)

    assert parsed[0]["SUMMARY"].startswith("A very long title with comma\\,")
    assert len(parsed[0]["SUMMARY"]) > 75
