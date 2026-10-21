import json

import pytest

from eventkit.checkin_runtime import (
    CheckinRecord,
    CheckinRegistry,
    DuplicateCheckinError,
    UnknownCheckinCodeError,
    merge_event_logs,
    parse_event_log,
    render_checkin_app,
    serialize_event_log,
)


def test_duplicate_checkins_and_unknown_codes_are_rejected_unless_allowed():
    registry = CheckinRegistry("event-1", {"A-001": "person-1"})

    registry.check_in(checkin_code="A-001", operator="door-1")
    assert registry.is_checked_in("person-1")

    with pytest.raises(DuplicateCheckinError):
        registry.check_in("person-1", operator="door-2")
    with pytest.raises(UnknownCheckinCodeError):
        registry.check_in(checkin_code="NOT-A-CODE", operator="door-2")

    registry.check_in(
        checkin_code="NOT-A-CODE",
        operator="door-2",
        allow_unknown_code=True,
    )
    assert registry.is_checked_in(
        checkin_code="NOT-A-CODE",
        allow_unknown_code=True,
    )


def test_manual_override_is_a_reasoned_correction_and_is_logged():
    registry = CheckinRegistry("event-1", {"A-001": "person-1"})
    registry.check_in(checkin_code="A-001", operator="door-1")

    correction = registry.manual_override(
        "person-1",
        operator="lead",
        checked_in=False,
        reason="attendee left before the duplicate scan was resolved",
    )

    assert correction.action == "manual_override"
    assert correction.override_action == "check_out"
    assert correction.reason == "attendee left before the duplicate scan was resolved"
    assert not registry.is_checked_in("person-1")
    assert registry.records[-1] == correction


def test_jsonl_serialization_is_deterministic_and_round_trips():
    records = [
        CheckinRecord(
            event_id="event-1",
            attendee_id="person-2",
            action="check_in",
            operator="door-2",
            sequence=2,
            device_id="device-b",
        ),
        CheckinRecord(
            event_id="event-1",
            checkin_code="A-001",
            action="check_in",
            operator="door-1",
            sequence=1,
            device_id="device-a",
        ),
    ]

    first = serialize_event_log(records)
    second = serialize_event_log(reversed(records))

    assert first == second
    assert first.endswith("\n")
    assert first.splitlines()[0].startswith('{"action":"check_in"')
    assert parse_event_log(first) == tuple(sorted(records, key=lambda item: item.sequence))

    decoded = [json.loads(line) for line in first.splitlines()]
    assert all(item["schema_version"] == 1 for item in decoded)
    assert "sequence" in decoded[0]


def test_device_logs_merge_independently_of_argument_order_and_deduplicate():
    device_a = CheckinRegistry("event-1", {"A-001": "person-1"}, device_id="a")
    device_b = CheckinRegistry("event-1", {"B-001": "person-2"}, device_id="b")
    device_a.check_in(checkin_code="A-001", operator="door-a")
    device_b.check_in(checkin_code="B-001", operator="door-b")

    merged_ab = merge_event_logs(device_a.export_log(), device_b.export_log())
    merged_ba = merge_event_logs(device_b.export_log(), device_a.export_log())

    assert merged_ab == merged_ba
    assert len(parse_event_log(merged_ab)) == 2
    assert [record.device_id for record in parse_event_log(merged_ab)] == ["a", "b"]

    restored = CheckinRegistry(
        "event-1",
        {"A-001": "person-1", "B-001": "person-2"},
    )
    restored.import_log(merged_ab)
    assert restored.is_checked_in("person-1")
    assert restored.is_checked_in("person-2")
    assert restored.import_log(merged_ab) == ()


def test_html_app_is_searchable_self_contained_and_email_safe_by_default():
    registry = CheckinRegistry("event-1", {"A-001": "person-1"})
    registry.check_in(checkin_code="A-001", operator="door-1")
    public_attendees = [
        {
            "attendee_id": "person-1",
            "checkin_code": "A-001",
            "name": "Ada </script><img src='external'>",
            "email": "private@example.test",
        }
    ]

    public_html = render_checkin_app(
        registry,
        title="Offline Event",
        public_attendees=public_attendees,
    )
    private_html = render_checkin_app(
        registry,
        title="Offline Event",
        public_attendees=public_attendees,
        private_data={"person-1": {"email": "private@example.test"}},
    )

    assert public_html == render_checkin_app(
        registry,
        title="Offline Event",
        public_attendees=public_attendees,
    )
    assert b"private@example.test" not in public_html
    assert b"private@example.test" in private_html
    assert b"type=\"search\"" in public_html
    assert b"event-data" in public_html
    assert b"<script src=" not in public_html
    assert b"http://" not in public_html
    assert b"https://" not in public_html
    assert b"</script><img" not in public_html
    assert b"<img" not in public_html
