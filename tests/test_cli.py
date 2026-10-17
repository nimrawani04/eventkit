from pathlib import Path

from eventkit.cli import main


def test_init_and_dry_run_are_safe_and_repeatable(tmp_path, capsys):
    demo = tmp_path / "demo"

    assert main(["init", str(demo)]) == 0
    assert (demo / "event.toml").is_file()
    assert (demo / "data" / "registrations.csv").is_file()

    assert main(["run", str(demo / "event.toml"), "--dry-run"]) == 0
    assert not (demo / "kit").exists()
    assert "No files written." in capsys.readouterr().out

    assert main(["run", str(demo / "event.toml")]) == 0
    output = demo / "kit"
    assert {path.name for path in output.iterdir()} == {
        "roster.csv", "issues.md", "report.md", "audit.jsonl"
    }

    assert main(["run", str(demo / "event.toml")]) == 0
    assert "unchanged=4" in capsys.readouterr().out


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
