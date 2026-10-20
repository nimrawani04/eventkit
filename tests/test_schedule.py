from datetime import timezone

import pytest

from eventkit.model import IssueLevel
from eventkit.schedule import ScheduleInputError, parse_schedule, parse_utc_offset


def test_schedule_parses_local_time_with_configured_offset_and_preserves_rows():
    result = parse_schedule(
        "start,end,title,room,speaker\n"
        "2026-05-01 09:30,2026-05-01 10:30,Opening,Hall,Ada\n",
        utc_offset="+05:30",
    )

    session = result.sessions[0]
    assert session.row_number == 2
    assert session.start_local.utcoffset().total_seconds() == 5.5 * 60 * 60
    assert session.start_utc == session.start.astimezone(timezone.utc)
    assert session.start_utc.strftime("%Y-%m-%d %H:%M") == "2026-05-01 04:00"
    assert result.issues == ()


def test_schedule_requires_columns_and_valid_offsets():
    with pytest.raises(ScheduleInputError, match="missing required column"):
        parse_schedule("start,end,title\n2026-01-01 09:00,2026-01-01 10:00,Talk\n")
    with pytest.raises(ScheduleInputError, match="formatted"):
        parse_utc_offset("0530")
    with pytest.raises(ScheduleInputError, match="valid"):
        parse_utc_offset("+24:00")


def test_touching_room_and_speaker_boundaries_are_allowed():
    result = parse_schedule(
        "start,end,title,room,speaker\n"
        "2026-05-01 09:00,2026-05-01 10:00,First,Room 1,Ada\n"
        "2026-05-01 10:00,2026-05-01 11:00,Second,Room 1,Ada\n",
    )

    assert [session.row_number for session in result.sessions] == [2, 3]
    assert result.issues == ()
    assert all(not session.has_clash for session in result.sessions)


def test_overlapping_room_and_speaker_sessions_keep_both_flagged():
    result = parse_schedule(
        "start,end,title,room,speaker\n"
        "2026-05-01 09:00,2026-05-01 11:00,First,Room 1,Ada\n"
        "2026-05-01 10:00,2026-05-01 12:00,Second,Room 1,Ada\n"
        "2026-05-01 10:30,2026-05-01 11:30,Third,Room 2,Bea\n",
    )

    assert [(issue.code, issue.row_number, issue.related_row_number) for issue in result.issues] == [
        ("SC02", 2, 3),
        ("SC03", 2, 3),
        ("SC02", 3, 2),
        ("SC03", 3, 2),
    ]
    assert [session.issue_ids for session in result.sessions] == [("SC02", "SC03"), ("SC02", "SC03"), ()]
    assert all(session.has_clash for session in result.sessions[:2])
    assert all(issue.level is IssueLevel.ERROR for issue in result.issues)


def test_invalid_sessions_are_sc01_and_excluded_from_outputs():
    result = parse_schedule(
        "start,end,title,room,speaker\n"
        "bad,2026-05-01 10:00,Bad start,Room,Ada\n"
        "2026-05-01 11:00,2026-05-01 11:00,Zero duration,Room,Ada\n"
        "2026-05-01 12:00,2026-05-01 11:00,Backwards,Room,Ada\n"
        "2026-05-01 13:00,2026-05-01 14:00,Valid,Room,Ada\n",
    )

    assert [session.title for session in result.sessions] == ["Valid"]
    assert result.excluded_row_numbers == (2, 3, 4)
    assert [(issue.code, issue.row_number) for issue in result.issues] == [
        ("SC01", 2),
        ("SC01", 3),
        ("SC01", 4),
    ]
