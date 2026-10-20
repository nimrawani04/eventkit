from pathlib import Path
import json
import re
import shutil
import pytest
from xml.etree import ElementTree

from eventkit.cli import main


def test_no_command_is_a_usage_error():
    assert main([]) == 2


def test_init_and_dry_run_are_safe_and_repeatable(tmp_path, capsys):
    demo = tmp_path / "demo"

    assert main(["init", str(demo)]) == 0
    assert (demo / "event.toml").is_file()
    assert (demo / "data" / "registrations.csv").is_file()
    assert (demo / "data" / "schedule.csv").is_file()

    assert main(["run", str(demo / "event.toml"), "--dry-run"]) == 0
    assert not (demo / "kit").exists()
    assert "No files written." in capsys.readouterr().out

    assert main(["run", str(demo / "event.toml")]) == 0
    output = demo / "kit"
    assert {path.name for path in output.iterdir()} == {
        "roster.csv",
        "issues.md",
        "schedule.ics",
        "agenda.html",
        "risk.md",
        "report.md",
        "audit.jsonl",
    }

    assert main(["run", str(demo / "event.toml")]) == 0
    assert "unchanged=7" in capsys.readouterr().out


def test_strict_returns_one_but_still_writes_explanations(tmp_path):
    demo = tmp_path / "demo"
    assert main(["init", str(demo)]) == 0

    assert main(["run", str(demo / "event.toml"), "--strict"]) == 1
    assert (demo / "kit" / "roster.csv").is_file()
    assert "EK01" in (demo / "kit" / "issues.md").read_text(encoding="utf-8")


def test_edited_output_is_kept_and_reported(tmp_path, capsys):
    demo = tmp_path / "demo"
    assert main(["init", str(demo)]) == 0
    assert main(["run", str(demo / "event.toml")]) == 0

    roster = demo / "kit" / "roster.csv"
    roster.write_text("edited by organizer\n", encoding="utf-8")

    assert main(["run", str(demo / "event.toml")]) == 0
    output = capsys.readouterr().out
    assert "keep      roster.csv" in output
    assert roster.read_text(encoding="utf-8") == "edited by organizer\n"


def _certificate_event(
    root: Path,
    *,
    attendance: bool = True,
    steps: str = '["roster", "certificates"]',
) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "data" / "registrations.csv").write_text(
        "name,email,phone,institution\n"
        "Ada Lovelace,ada@example.com,12345678,University\n"
        ",excluded@example.com,12345679,University\n",
        encoding="utf-8",
    )
    input_block = 'registrations = "data/registrations.csv"\n'
    if attendance:
        (root / "data" / "attendance.csv").write_text(
            "email,role\nada@example.com,Speaker\nunknown@example.com,Participant\n",
            encoding="utf-8",
        )
        input_block += 'attendance = "data/attendance.csv"\n'
    path = root / "event.toml"
    path.write_text(
        "[event]\n"
        "id = \"certificate-demo\"\n"
        "name = \"Certificate Demo\"\n"
        "organizer = \"Campus Club\"\n\n"
        "[input]\n"
        + input_block
        + "\n[certificates]\n"
        "title = \"Certificate of Participation\"\n"
        "issue_to = \"attended\"\n"
        "manifest_names = true\n\n"
        "[output]\n"
        "dir = \"kit\"\n"
        f"steps = {steps}\n",
        encoding="utf-8",
    )
    return path


def _schedule_event(root: Path, *, steps: str = '["roster", "schedule"]') -> Path:
    (root / "data").mkdir(parents=True)
    (root / "data" / "registrations.csv").write_text(
        "name,email,phone,institution\nAda Lovelace,ada@example.com,12345678,University\n",
        encoding="utf-8",
    )
    (root / "data" / "schedule.csv").write_text(
        "start,end,title,room,speaker\n"
        '2026-05-01 09:30,2026-05-01 10:30,"Opening <&>",Hall 1,Ada\n'
        "2026-05-01 10:30,2026-05-01 11:00,Touching,Hall 1,Ada\n"
        "2026-05-01 10:45,2026-05-01 11:30,Clash,Hall 1,Ada\n"
        "bad,2026-05-01 12:00,Bad,Hall 2,Bea\n",
        encoding="utf-8",
    )
    path = root / "event.toml"
    path.write_text(
        "[event]\n"
        "id = \"schedule-demo\"\n"
        "name = \"Schedule Demo\"\n"
        "organizer = \"Campus Club\"\n"
        "utc_offset = \"+05:30\"\n\n"
        "[input]\n"
        "registrations = \"data/registrations.csv\"\n"
        "schedule = \"data/schedule.csv\"\n\n"
        "[output]\n"
        "dir = \"kit\"\n"
        f"steps = {steps}\n",
        encoding="utf-8",
    )
    return path


def test_certificate_run_writes_svg_manifest_and_verify_result(tmp_path, monkeypatch, capsys):
    event = _certificate_event(tmp_path)
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")

    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    first_run = {
        path.relative_to(kit): path.read_bytes()
        for path in kit.rglob("*")
        if path.is_file()
    }
    manifest_data = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["certificate_count"] == 1
    assert "ada@example.com" not in (kit / "manifest.json").read_text(encoding="utf-8")
    certificate = next((kit / "certificates").glob("*.svg"))
    ElementTree.parse(certificate)
    assert (kit / "verify.html").is_file()
    assert (kit / "risk.md").is_file()
    report = (kit / "report.md").read_text(encoding="utf-8")
    assert "Every generated certificate SVG parses as XML." in report
    assert "Every generated certificate code verifies against the manifest." in report
    code_hash = manifest_data["certificates"][0]["code_sha256"]
    assert code_hash

    from eventkit.codes import canonicalize_code, generate_certificate_code

    code = generate_certificate_code(
        "certificate-demo", "ada@example.com", "Speaker", "certificate-test-secret"
    )
    assert main(["verify", code.lower(), "--manifest", str(kit / "manifest.json")]) == 0
    assert "VALID" in capsys.readouterr().out
    canonical = canonicalize_code(code)
    changed = ("0" if canonical[0] != "0" else "1") + canonical[1:]
    assert main(["verify", changed, "--manifest", str(kit / "manifest.json")]) == 1
    assert "INVALID" in capsys.readouterr().out

    assert main(["run", str(event)]) == 0
    second_run = {
        path.relative_to(kit): path.read_bytes()
        for path in kit.rglob("*")
        if path.is_file()
    }
    assert second_run == first_run


def test_certificate_step_without_attendance_is_skipped(tmp_path, monkeypatch, capsys):
    event = _certificate_event(tmp_path, attendance=False)

    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    assert not (kit / "manifest.json").exists()
    assert "attendance CSV was provided" in (kit / "report.md").read_text(encoding="utf-8")
    assert "Certificates skipped" in capsys.readouterr().out


def test_certificate_step_requires_secret_when_attendance_is_available(tmp_path, monkeypatch):
    event = _certificate_event(tmp_path)
    monkeypatch.delenv("EVENTKIT_SECRET", raising=False)

    assert main(["run", str(event)]) == 2
    assert not (tmp_path / "kit").exists()


def test_edited_audit_is_kept(tmp_path, monkeypatch):
    event = _certificate_event(tmp_path)
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")
    assert main(["run", str(event)]) == 0

    audit = tmp_path / "kit" / "audit.jsonl"
    original = audit.read_bytes()
    audit.write_bytes(original + b'{"edited":true}\n')

    assert main(["run", str(event)]) == 0
    assert audit.read_bytes() == original + b'{"edited":true}\n'


def test_symlinked_certificate_directory_is_refused(tmp_path, monkeypatch):
    event = _certificate_event(tmp_path)
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")
    assert main(["run", str(event)]) == 0

    certificates = tmp_path / "kit" / "certificates"
    external = tmp_path / "external"
    shutil.rmtree(certificates)
    external.mkdir()
    try:
        certificates.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert main(["run", str(event)]) == 2
    assert not list(external.iterdir())


def test_stale_unchanged_certificate_is_removed_safely(tmp_path, monkeypatch):
    event = _certificate_event(tmp_path)
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")
    assert main(["run", str(event)]) == 0
    assert list((tmp_path / "kit" / "certificates").glob("*.svg"))

    (tmp_path / "data" / "attendance.csv").write_text(
        "email,role\nunknown@example.com,Participant\n", encoding="utf-8"
    )
    assert main(["run", str(event)]) == 0
    assert not list((tmp_path / "kit" / "certificates").glob("*.svg"))


def test_checkin_and_certificate_steps_work_together_end_to_end(tmp_path, monkeypatch):
    event = _certificate_event(
        tmp_path,
        steps='["roster", "checkin", "certificates"]',
    )
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")

    assert main(["run", str(event), "--dry-run"]) == 0
    assert not (tmp_path / "kit").exists()
    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    checkin = (kit / "checkin.html").read_text(encoding="utf-8")
    assert "Ada Lovelace" in checkin
    assert "excluded@example.com" not in checkin
    assert "@media print" in checkin
    audit = (kit / "audit.jsonl").read_text(encoding="utf-8")
    assert '"step":"checkin"' in audit
    assert '"step":"certificates"' in audit
    report = (kit / "report.md").read_text(encoding="utf-8")
    assert "Check-in sheet parses and matches the included roster." in report
    assert (kit / "risk.md").is_file()


def test_checkin_step_requires_secret_before_writing(tmp_path, monkeypatch):
    event = _certificate_event(tmp_path, steps='["roster", "checkin"]')
    monkeypatch.delenv("EVENTKIT_SECRET", raising=False)

    assert main(["run", str(event)]) == 2
    assert not (tmp_path / "kit").exists()


def test_schedule_run_writes_ics_agenda_and_reports_clashes(tmp_path, capsys):
    event = _schedule_event(tmp_path)

    assert main(["run", str(event), "--dry-run"]) == 0
    assert not (tmp_path / "kit").exists()

    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    ics = (kit / "schedule.ics").read_bytes()
    agenda = (kit / "agenda.html").read_text(encoding="utf-8")
    issues = (kit / "issues.md").read_text(encoding="utf-8")
    report = (kit / "report.md").read_text(encoding="utf-8")
    assert b"DTSTART:20260501T040000Z" in ics
    assert b"SUMMARY:Opening <&>" in ics
    assert "Opening &lt;&amp;&gt;" in agenda
    assert "Clash: SC02" in agenda
    assert "SC01" in issues and "SC02" in issues and "SC03" in issues
    assert "Schedule ICS parses and contains all valid sessions." in report
    assert "Agenda HTML parses and contains all valid sessions." in report
    assert '"step":"schedule"' in (kit / "audit.jsonl").read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "schedule.ics" in output and "agenda.html" in output


def test_schedule_strict_returns_one_for_schedule_errors(tmp_path):
    event = _schedule_event(tmp_path)

    assert main(["run", str(event), "--strict"]) == 1
    assert (tmp_path / "kit" / "schedule.ics").is_file()


def test_full_roster_checkin_schedule_certificate_flow_is_repeatable(tmp_path, monkeypatch):
    event = _certificate_event(
        tmp_path,
        steps='["roster", "checkin", "schedule", "certificates"]',
    )
    (tmp_path / "data" / "schedule.csv").write_text(
        "start,end,title,room,speaker\n"
        "2026-05-01 09:00,2026-05-01 10:00,Opening,Hall 1,Ada\n",
        encoding="utf-8",
    )
    event.write_text(
        event.read_text(encoding="utf-8").replace(
            'attendance = "data/attendance.csv"\n',
            'attendance = "data/attendance.csv"\nschedule = "data/schedule.csv"\n',
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")

    assert main(["run", str(event), "--dry-run"]) == 0
    assert not (tmp_path / "kit").exists()
    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    expected = {
        "roster.csv",
        "issues.md",
        "checkin.html",
        "schedule.ics",
        "agenda.html",
        "risk.md",
        "verify.html",
        "manifest.json",
        "certificates",
        "report.md",
        "audit.jsonl",
    }
    actual = {path.name for path in kit.iterdir()}
    assert actual == expected
    first = {path.relative_to(kit): path.read_bytes() for path in kit.rglob("*") if path.is_file()}

    assert main(["run", str(event)]) == 0
    second = {path.relative_to(kit): path.read_bytes() for path in kit.rglob("*") if path.is_file()}
    assert first == second


def test_email_drafts_and_offline_verify_page_are_generated(tmp_path, monkeypatch):
    event = _certificate_event(
        tmp_path,
        steps='["roster", "certificates", "emails"]',
    )
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")

    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    drafts = list((kit / "emails").glob("*.eml"))
    assert len(drafts) == 1
    draft = drafts[0].read_text(encoding="utf-8")
    assert "X-Unsent: 1" in draft
    assert "Date:" not in draft
    assert (kit / "mailmerge.csv").is_file()

    page = (kit / "verify.html").read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/json" id="manifest">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert match is not None
    embedded = json.loads(match.group(1))
    assert embedded["certificate_count"] == 1
    assert "ada@example.com" not in page


def test_disabling_email_step_removes_unchanged_email_outputs(tmp_path, monkeypatch):
    event = _certificate_event(
        tmp_path,
        steps='["roster", "certificates", "emails"]',
    )
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")
    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    assert list((kit / "emails").glob("*.eml"))

    event.write_text(
        event.read_text(encoding="utf-8").replace(
            '["roster", "certificates", "emails"]',
            '["roster", "certificates"]',
        ),
        encoding="utf-8",
    )
    assert main(["run", str(event)]) == 0
    assert not (kit / "mailmerge.csv").exists()
    assert not list((kit / "emails").glob("*.eml"))


def test_clean_cli_removes_only_the_generated_kit(tmp_path, monkeypatch, capsys):
    event = _certificate_event(
        tmp_path,
        steps='["roster", "certificates", "emails"]',
    )
    monkeypatch.setenv("EVENTKIT_SECRET", "certificate-test-secret")
    assert main(["run", str(event)]) == 0
    kit = tmp_path / "kit"
    (kit / "notes.txt").write_text("keep me\n", encoding="utf-8")

    assert main(["clean", str(event), "--dry-run"]) == 0
    assert (kit / "manifest.json").exists()
    assert "No files removed." in capsys.readouterr().out

    assert main(["clean", str(event)]) == 0
    assert not (kit / "roster.csv").exists()
    assert not (kit / "manifest.json").exists()
    assert not (kit / "audit.jsonl").exists()
    assert (kit / "notes.txt").exists()


def test_diff_command_reports_privacy_safe_registration_changes(tmp_path, capsys):
    event = _schedule_event(tmp_path, steps='["roster"]')
    assert main(["run", str(event)]) == 0
    registrations = tmp_path / "data" / "registrations.csv"
    registrations.write_text(
        "name,email,phone,institution\n"
        "Ada Lovelace,ada@example.com,12345678,University\n"
        "Grace Hopper,grace@example.com,12345679,Institute\n",
        encoding="utf-8",
    )

    assert main(["diff", str(event)]) == 0
    output = capsys.readouterr().out
    assert "Added" in output
    assert "grace@example.com" not in output
