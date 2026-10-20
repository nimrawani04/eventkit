from __future__ import annotations

import csv
import io
from email import policy
from email.parser import BytesParser

from eventkit.certificates import certificate_filename
from eventkit.emails import (
    MAILMERGE_FIELDS,
    build_email_drafts,
    render_email_draft,
    render_mailmerge_csv,
    verify_email_draft,
)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><text>certificate</text></svg>\n'


def _parse(draft):
    return BytesParser(policy=policy.default).parsebytes(draft.message)


def test_draft_parses_as_unsent_multipart_without_date_and_has_svg_attachment():
    draft = render_email_draft(
        "Ada Lovelace",
        "ada@example.com",
        "Computing Day",
        "ABCD-1234-EFGH",
        certificate=SVG,
    )

    message = _parse(draft)

    assert message["X-Unsent"] == "1"
    assert message["To"] == "ada@example.com"
    assert message["Date"] is None
    assert message.is_multipart()
    assert verify_email_draft(draft, expected_email="ada@example.com", expected_attachment=True)

    parts = list(message.walk())
    assert {part.get_content_type() for part in parts} >= {"text/plain", "text/html"}
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "image/svg+xml"
    assert attachments[0].get_filename() == certificate_filename(
        "Ada Lovelace", "ABCD-1234-EFGH"
    )
    assert attachments[0].get_payload(decode=True) == SVG


def test_text_keeps_source_values_while_html_escapes_template_fields():
    draft = render_email_draft(
        'A <Ada> & "Lovelace"',
        "ada@example.com",
        "R&D <show>",
        "CODE&1",
    )
    message = _parse(draft)
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()

    assert 'A <Ada> & "Lovelace"' in plain
    assert "R&D <show>" in plain
    assert "CODE&1" in plain
    assert "A &lt;Ada&gt; &amp; &quot;Lovelace&quot;" in html
    assert "R&amp;D &lt;show&gt;" in html
    assert "CODE&amp;1" in html


def test_rendering_is_byte_deterministic_including_mime_boundaries():
    arguments = {
        "name": "Zoë O'Neil",
        "email": "zoe@example.com",
        "event": "Reliable Systems",
        "code": "CODE-0002",
        "certificate": SVG,
    }

    first = render_email_draft(**arguments)
    second = render_email_draft(**arguments)

    assert first.message == second.message
    assert first.filename.isascii()
    assert "/" not in first.filename and "\\" not in first.filename


def test_build_looks_up_matching_certificate_preserves_order_and_allows_missing(tmp_path):
    first_name = "张伟"
    first_code = "FIRST-0001"
    matching = tmp_path / certificate_filename(first_name, first_code)
    matching.write_bytes(SVG)

    drafts = build_email_drafts(
        [
            {"name": first_name, "email": "first@example.com", "code": first_code},
            {"name": "Second Person", "email": "second@example.com", "code": "SECOND-0002"},
        ],
        "Event",
        certificate_dir=tmp_path,
    )

    assert [draft.email for draft in drafts] == ["first@example.com", "second@example.com"]
    assert drafts[0].has_certificate
    assert drafts[0].certificate_filename == matching.name
    assert not drafts[1].has_certificate
    assert list(_parse(drafts[1]).iter_attachments()) == []


def test_mailmerge_csv_is_metadata_only_and_keeps_draft_order(tmp_path):
    certificate = tmp_path / "certificates"
    certificate.mkdir()
    drafts = build_email_drafts(
        [
            ("First", "first@example.com", "CODE-1"),
            ("Second", "second@example.com", "CODE-2"),
        ],
        "CSV Event",
        certificate_dir=certificate,
    )

    rendered = render_mailmerge_csv(drafts)
    rows = list(csv.DictReader(io.StringIO(rendered.decode("utf-8"))))

    assert rendered.endswith(b"\n")
    assert list(rows[0]) == list(MAILMERGE_FIELDS)
    assert [row["email"] for row in rows] == ["first@example.com", "second@example.com"]
    assert rows[0]["event"] == "CSV Event"
    assert rows[0]["draft"] == drafts[0].filename
    assert rows[0]["certificate"] == ""
    assert "Hello" not in rendered.decode("utf-8")
    assert "X-Unsent" not in rendered.decode("utf-8")
