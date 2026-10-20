from __future__ import annotations

from eventkit.dashboard import (
    DashboardData,
    render_dashboard,
    verify_dashboard_html,
)


def _dashboard() -> DashboardData:
    return DashboardData(
        event_title='Spring <Summit> & "Forum"',
        organizer="O'Reilly & Co.",
        status="Needs review",
        status_detail="Two findings remain for the coordinator.",
        metrics={
            "registered": 12,
            "included": 10,
            "excluded": 2,
            "warnings": 3,
            "errors": 1,
            "sessions": 4,
            "certificates": 8,
            "drafts": 8,
        },
        risk_summary="Review the room assignment before publishing the agenda.",
        unresolved=("Room overlap on row 4", "One certificate was skipped"),
        attendees=(
            {
                "name": 'Ada <Lovelace>',
                "email": 'ada@example.test"x',
                "institution": "Analytical Engine",
                "status": "ok",
                "issues": ("EK04",),
            },
            {
                "name": "Excluded Person",
                "email": "bad@example.test",
                "status": "excluded",
                "issues": ("EK02",),
            },
        ),
        schedule=(
            {
                "title": "Opening & welcome",
                "start": "2026-05-01 09:00",
                "end": "2026-05-01 10:00",
                "room": "Hall <A>",
                "speaker": "Ada",
                "issue_ids": ("SC02",),
            },
        ),
        certificate_rows=(
            {"code": "ABCD-1234-EFGH", "name": "Ada Lovelace", "role": "Speaker"},
        ),
        deliverables={
            "roster.csv": True,
            "issues.md": True,
            "checkin.html": True,
            "schedule.ics": True,
            "agenda.html": True,
            "manifest.json": True,
            "verify.html": True,
            "risk.md": True,
            "report.md": True,
            "mailmerge.csv": True,
            "emails/": True,
        },
    )


def test_dashboard_is_deterministic_and_verifies():
    first = render_dashboard(_dashboard())
    second = render_dashboard(_dashboard())

    assert first == second
    assert isinstance(first, bytes)
    verify_dashboard_html(first)


def test_dynamic_text_is_html_escaped_in_text_and_attributes():
    content = render_dashboard(_dashboard()).decode("utf-8")

    assert "Spring &lt;Summit&gt; &amp; &quot;Forum&quot;" in content
    assert "O&#x27;Reilly &amp; Co." in content
    assert "Ada &lt;Lovelace&gt;" in content
    assert "ada@example.test&quot;x" in content
    assert 'ada@example.test"x' not in content
    assert "Hall &lt;A&gt;" in content
    assert "<h1>Spring <Summit>" not in content


def test_required_sections_and_local_deliverable_links_are_present():
    content = render_dashboard(_dashboard()).decode("utf-8")

    for section in ("overview", "attendees", "schedule", "certificates", "deliverables"):
        assert f'id="{section}"' in content
    for path in (
        "roster.csv",
        "issues.md",
        "checkin.html",
        "schedule.ics",
        "agenda.html",
        "manifest.json",
        "verify.html",
        "risk.md",
        "report.md",
        "mailmerge.csv",
        "emails/",
    ):
        assert f'href="{path}"' in content
    assert 'href="certificates/ada-lovelace-ABCD-1234-EFGH.svg"' in content
    for label in (
        "Registered",
        "Included",
        "Excluded",
        "Warnings",
        "Errors",
        "Sessions",
        "Certificates",
        "Drafts",
    ):
        assert label in content
    assert "Clash SC02" in content


def test_dashboard_has_responsive_print_styles_inline_tabs_and_no_external_urls():
    content = render_dashboard(_dashboard()).decode("utf-8")

    assert "@media (max-width" in content
    assert "@media print" in content
    assert "data-tab=\"overview\"" in content
    assert "addEventListener" in content
    assert "<script src=" not in content
    assert "http://" not in content
    assert "https://" not in content
    assert "<link" not in content
