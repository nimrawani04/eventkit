from eventkit.agenda import render_agenda, verify_agenda_html
from eventkit.schedule import parse_schedule


def test_agenda_groups_by_local_day_escapes_text_and_marks_clashes():
    result = parse_schedule(
        "start,end,title,room,speaker\n"
        '2026-05-01 09:00,2026-05-01 10:30,"Opening <&>",Hall <A>,Ada & Co\n'
        '2026-05-01 10:00,2026-05-01 11:00,"Second",Hall <A>,Bea\n'
        '2026-05-02 09:00,2026-05-02 10:00,"Next day",Hall B,\n',
    )
    first = render_agenda(result, event_name="Event <One> & Friends")
    second = render_agenda(result, event_name="Event <One> & Friends")
    text = first.decode("utf-8")

    assert first == second
    assert text.count('class="day"') == 2
    assert 'data-date="2026-05-01"' in text
    assert 'data-date="2026-05-02"' in text
    assert "Opening &lt;&amp;&gt;" in text
    assert "Hall &lt;A&gt;" in text
    assert "Ada &amp; Co" in text
    assert text.count('class="session clash"') == 2
    assert text.count("Clash: SC02") == 2
    verify_agenda_html(first, result)


def test_empty_agenda_has_deterministic_empty_state():
    content = render_agenda([], event_name="Empty")

    assert b"No scheduled sessions." in content
    verify_agenda_html(content, 0)
