import hashlib
import json

import pytest

from eventkit.clean import clean_event, format_clean_plan, plan_clean


def _event(root):
    event = root / "event.toml"
    (root / "data").mkdir(parents=True)
    (root / "data" / "registrations.csv").write_text("name,email\nA,a@example.com\n", encoding="utf-8")
    event.write_text(
        "[event]\n"
        "id = \"clean-test\"\n"
        "name = \"Clean test\"\n"
        "\n"
        "[input]\n"
        "registrations = \"data/registrations.csv\"\n"
        "\n"
        "[output]\n"
        "dir = \"kit\"\n"
        "steps = [\"roster\"]\n",
        encoding="utf-8",
    )
    return event


def _write_audit(root, files, *, input_digest="0" * 64):
    records = [{"input_digest": input_digest}]
    records.extend(
        {
            "path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "step": "roster",
        }
        for path, content in files.items()
    )
    prefix = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")
    records.append({"audit_sha256": hashlib.sha256(prefix).hexdigest()})
    root.mkdir(parents=True, exist_ok=True)
    (root / "audit.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def _make_kit(event, files):
    kit = event.parent / "kit"
    for relative_path, content in files.items():
        target = kit / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _write_audit(kit, files)
    return kit


def test_clean_removes_unmodified_outputs_and_empty_generated_directories(tmp_path):
    event = _event(tmp_path)
    files = {
        "roster.csv": b"row\n",
        "certificates/one.svg": b"<svg />\n",
    }
    kit = _make_kit(event, files)

    result = clean_event(event)

    assert not (kit / "roster.csv").exists()
    assert not (kit / "certificates").exists()
    assert not (kit / "audit.jsonl").exists()
    assert kit.is_dir()
    assert [item.action for item in result.actions] == ["delete", "delete", "rmdir", "delete"]
    assert result.actions[-1].relative_path == "audit.jsonl"
    assert not result.unresolved


def test_clean_keeps_edited_and_unowned_files_as_unresolved(tmp_path):
    event = _event(tmp_path)
    files = {"roster.csv": b"generated\n", "issues.md": b"issues\n"}
    kit = _make_kit(event, files)
    (kit / "roster.csv").write_bytes(b"edited by organizer\n")
    (kit / "notes.txt").write_bytes(b"not generated\n")

    result = clean_event(event)

    assert (kit / "roster.csv").read_bytes() == b"edited by organizer\n"
    assert (kit / "notes.txt").read_bytes() == b"not generated\n"
    assert not (kit / "issues.md").exists()
    assert not (kit / "audit.jsonl").exists()
    assert any("roster.csv" in item for item in result.unresolved)
    assert any("notes.txt" in item for item in result.unresolved)


@pytest.mark.parametrize("edit", ["append", "malformed"])
def test_malformed_or_edited_audit_keeps_all_outputs(tmp_path, edit):
    event = _event(tmp_path)
    kit = _make_kit(event, {"roster.csv": b"generated\n"})
    audit = kit / "audit.jsonl"
    if edit == "append":
        audit.write_bytes(audit.read_bytes() + b'{"edited":true}\n')
    else:
        audit.write_text("not json\n", encoding="utf-8")

    result = clean_event(event)

    assert (kit / "roster.csv").exists()
    assert audit.exists()
    assert all(item.action != "delete" for item in result.actions)
    assert any("audit" in item.lower() for item in result.unresolved)


def test_clean_dry_run_has_actions_but_writes_nothing(tmp_path):
    event = _event(tmp_path)
    kit = _make_kit(event, {"roster.csv": b"generated\n"})
    before = {path.relative_to(kit): path.read_bytes() for path in kit.rglob("*") if path.is_file()}

    result = clean_event(event, dry_run=True)

    after = {path.relative_to(kit): path.read_bytes() for path in kit.rglob("*") if path.is_file()}
    assert before == after
    assert (kit / "roster.csv").exists()
    assert (kit / "audit.jsonl").exists()
    assert [item.action for item in result.actions] == ["delete", "delete"]
    assert "delete    audit.jsonl" in format_clean_plan(result)


def test_clean_refuses_symlink_and_path_traversal_escapes(tmp_path):
    event = _event(tmp_path)
    kit = event.parent / "kit"
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.txt").write_bytes(b"do not touch\n")
    kit.mkdir()
    try:
        (kit / "linked").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    _write_audit(kit, {"linked/secret.txt": b"do not touch\n"})

    with pytest.raises(ValueError, match="symlink"):
        clean_event(event)
    assert (external / "secret.txt").exists()

    (kit / "linked").unlink()
    kit.rmdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"do not touch\n")
    kit.mkdir()
    _write_audit(kit, {"../outside.txt": b"do not touch\n"})
    with pytest.raises(ValueError, match="escapes"):
        clean_event(event)
    assert outside.exists()


def test_clean_actions_are_deterministic_and_sorted(tmp_path):
    first_event = _event(tmp_path / "first")
    second_event = _event(tmp_path / "second")
    files = {
        "z-last.txt": b"z\n",
        "a-first.txt": b"a\n",
        "nested/value.txt": b"nested\n",
    }
    first_kit = _make_kit(first_event, files)
    second_kit = _make_kit(second_event, files)

    first = plan_clean(first_kit)
    second = plan_clean(second_kit)

    assert [
        (item.relative_path, item.action, item.sha256)
        for item in first.actions
    ] == [
        (item.relative_path, item.action, item.sha256)
        for item in second.actions
    ]
    assert [item.relative_path for item in first.actions] == [
        "a-first.txt",
        "nested/value.txt",
        "z-last.txt",
        "nested",
        "audit.jsonl",
    ]
