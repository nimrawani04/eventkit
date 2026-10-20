from eventkit.codes import IssuedCertificate
from eventkit.csvio import read_csv_text
from eventkit.impact import ImpactRun, compare_runs, pseudonymous_id
from eventkit.output import FileAction
from eventkit.schedule import parse_schedule
from eventkit.validate import validate_roster


def _roster(*rows: str):
    return validate_roster(
        read_csv_text(
            "name,email,phone,institution\n" + "\n".join(rows) + "\n"
        )
    )


def _certificate(email: str, code: str, role: str = "Participant"):
    return IssuedCertificate(code=code, email=email, role=role, name="Private Name")


def test_preview_is_deterministic_for_the_same_runs():
    prior = ImpactRun(
        roster=_roster("Ada Lovelace,ada@example.com,12345678,University"),
        schedule=parse_schedule(
            "start,end,title,room,speaker\n"
            "2026-05-01 09:00,2026-05-01 10:00,Opening,Hall,Ada\n"
        ),
        certificates=(_certificate("ada@example.com", "OLD-CODE"),),
        files=(FileAction("roster.csv", "unchanged", 10, "a" * 64),),
    )
    current = ImpactRun(
        roster=_roster(
            "Ada Lovelace,ada@example.com,12345678,University",
            "Grace Hopper,grace@example.com,12345679,Institute",
        ),
        schedule=parse_schedule(
            "start,end,title,room,speaker\n"
            "2026-05-01 09:00,2026-05-01 10:00,Opening,Hall,Ada\n"
        ),
        certificates=(
            _certificate("ada@example.com", "OLD-CODE"),
            _certificate("grace@example.com", "NEW-CODE"),
        ),
        files=(FileAction("roster.csv", "update", 20, "b" * 64),),
    )

    first = compare_runs(current, prior).render_markdown()
    second = compare_runs(current, prior).render_markdown()

    assert first == second
    assert "R-" in first
    assert "grace@example.com" not in first


def test_no_change_output_is_explicit():
    run = ImpactRun(
        roster=_roster("Ada Lovelace,ada@example.com,12345678,University"),
        schedule=parse_schedule(
            "start,end,title,room,speaker\n"
            "2026-05-01 09:00,2026-05-01 10:00,Opening,Hall,Ada\n"
        ),
        certificates=(_certificate("ada@example.com", "CODE-0001"),),
        files=(FileAction("roster.csv", "unchanged", 10, "a" * 64),),
    )

    preview = compare_runs(run, run)
    output = preview.render_markdown()

    assert not preview.has_changes
    assert output.count("No changes.") == 4
    assert "unchanged: `roster.csv`" in output


def test_roster_add_and_remove_are_reported_without_email_addresses():
    previous = ImpactRun(
        roster=_roster(
            "Ada Lovelace,ada@example.com,12345678,University",
            "Grace Hopper,grace@example.com,12345679,Institute",
        )
    )
    current = ImpactRun(
        roster=_roster(
            "Ada Lovelace,ada@example.com,12345678,University",
            "Katherine Johnson,katherine@example.com,12345680,Lab",
        )
    )

    preview = compare_runs(current, previous)
    output = preview.render_markdown()

    assert [item.kind for item in preview.registrations.added] == ["added"]
    assert [item.kind for item in preview.registrations.removed] == ["removed"]
    assert preview.registrations.added[0].current_row == 3
    assert preview.registrations.removed[0].previous_row == 3
    assert "Added — row 3" in output
    assert "Removed — previous row 3" in output
    assert "grace@example.com" not in output
    assert "katherine@example.com" not in output
    assert "Katherine Johnson" not in output


def test_schedule_clash_summary_reports_unique_room_and_speaker_pairs():
    previous = ImpactRun(
        schedule=parse_schedule(
            "start,end,title,room,speaker\n"
            "2026-05-01 09:00,2026-05-01 10:00,First,Hall,Ada\n"
        )
    )
    current = ImpactRun(
        schedule=parse_schedule(
            "start,end,title,room,speaker\n"
            "2026-05-01 09:00,2026-05-01 11:00,First,Hall,Ada\n"
            "2026-05-01 10:00,2026-05-01 12:00,Second,Hall,Ada\n"
        )
    )

    preview = compare_runs(current, previous)
    output = preview.render_markdown()

    assert len(preview.schedule.clashes) == 2
    assert len(preview.schedule.clashes_added) == 2
    assert "Current clashes:" in output
    assert "1 room clash (SC02; rows 2 and 3)" in output
    assert "1 speaker clash (SC03; rows 2 and 3)" in output


def test_privacy_uses_stable_pseudonyms_and_names_are_opt_in():
    previous = ImpactRun(roster=_roster("Ada Lovelace,ada@example.com,12345678,University"))
    current = ImpactRun(roster=_roster("Ada Byron,ada@example.com,12345678,University"))

    private_output = compare_runs(current, previous).render_markdown()
    named_output = compare_runs(current, previous, include_names=True).render_markdown()

    assert "ada@example.com" not in private_output
    assert "Ada Lovelace" not in private_output
    assert "Ada Byron" not in private_output
    assert pseudonymous_id(" ADA@EXAMPLE.COM ") in private_output
    assert "Ada Byron" in named_output
    assert "ada@example.com" not in named_output
