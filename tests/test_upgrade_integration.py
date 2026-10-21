from __future__ import annotations

import json

from eventkit.cli import main
from eventkit.credentials import manifest_from_json, verify_manifest
from eventkit.review import make_record, render_ledger


def _event(root, *, steps: str, signed: bool = False, profile: str = "private", review: bool = False):
    (root / "data").mkdir(parents=True)
    (root / "data" / "registrations.csv").write_text(
        "name,email,phone,institution\n"
        "Ada Lovelace,ada@example.com,12345678,University\n"
        ",missing@example.com,12345679,University\n",
        encoding="utf-8",
    )
    (root / "data" / "attendance.csv").write_text(
        "email,role\nada@example.com,Speaker\n", encoding="utf-8"
    )
    review_line = 'review = "review.jsonl"\n' if review else ""
    event = root / "event.toml"
    event.write_text(
        '[event]\n'
        'id = "upgrade-event"\n'
        'name = "Upgrade Event"\n'
        'organizer = "Campus Club"\n\n'
        '[input]\n'
        'registrations = "data/registrations.csv"\n'
        'attendance = "data/attendance.csv"\n'
        + review_line
        + '\n[certificates]\n'
        'issue_to = "attended"\n'
        f'signed = {str(signed).lower()}\n\n'
        + ('revocations = "revocations.json"\n' if signed else '')
        + '\n[output]\n'
        f'profile = "{profile}"\n'
        'dir = "kit"\n'
        f'steps = {steps}\n',
        encoding="utf-8",
    )
    return event


def test_signed_credentials_are_generated_and_cli_verified(tmp_path, monkeypatch, capsys):
    event = _event(tmp_path, steps='["roster", "certificates"]', signed=True)
    monkeypatch.setenv("EVENTKIT_SECRET", "upgrade-test-secret")

    assert main(["run", str(event)]) == 0
    credentials_path = tmp_path / "kit" / "credentials.json"
    package = manifest_from_json(credentials_path.read_text(encoding="utf-8"))
    assert verify_manifest(package, event_id="upgrade-event")
    code = package.credentials[0].code

    assert main(["verify", code, "--manifest", str(credentials_path)]) == 0
    assert "Issuer:" in capsys.readouterr().out


def test_revoke_command_writes_a_signed_revocation_file(tmp_path, monkeypatch):
    event = _event(tmp_path, steps='["roster", "certificates"]', signed=True)
    monkeypatch.setenv("EVENTKIT_SECRET", "upgrade-test-secret")
    assert main(["run", str(event)]) == 0
    package = manifest_from_json(
        (tmp_path / "kit" / "credentials.json").read_text(encoding="utf-8")
    )

    assert main(
        [
            "revoke",
            str(event),
            package.credentials[0].code,
            "--reason",
            "duplicate issuance",
        ]
    ) == 0
    revocations = json.loads((tmp_path / "revocations.json").read_text(encoding="utf-8"))
    assert revocations["revocations"][0]["code"] == package.credentials[0].code
    assert main(["run", str(event)]) == 0
    assert main(
        [
            "verify",
            package.credentials[0].code,
            "--manifest",
            str(tmp_path / "kit" / "credentials.json"),
        ]
    ) == 1


def test_checkin_app_is_generated_as_an_offline_event_day_surface(tmp_path, monkeypatch):
    event = _event(tmp_path, steps='["roster", "checkin-app"]')
    monkeypatch.setenv("EVENTKIT_SECRET", "upgrade-test-secret")

    assert main(["run", str(event)]) == 0
    app = (tmp_path / "kit" / "checkin-app.html").read_text(encoding="utf-8")
    assert 'type="search"' in app
    assert "Export event log" in app
    assert "Check in" in app
    assert "ada@example.com" not in app


def test_public_profile_omits_email_and_review_can_resolve_errors(tmp_path, monkeypatch):
    event = _event(tmp_path, steps='["roster"]', profile="public", review=True)
    (tmp_path / "review.jsonl").write_text(
        render_ledger(
            [
                make_record(
                    "EK01",
                    3,
                    "accept",
                    "Organizer confirmed the blank field is intentional.",
                    "reviewer",
                )
            ]
        ),
        encoding="utf-8",
    )

    assert main(["run", str(event), "--strict"]) == 0
    kit = tmp_path / "kit"
    assert "ada@example.com" not in (kit / "roster.csv").read_text(encoding="utf-8")
    assert "email" not in (kit / "roster.csv").read_text(encoding="utf-8").splitlines()[0]
    assert (kit / "review.md").is_file()
    assert "Ready" in (kit / "dashboard.html").read_text(encoding="utf-8")
