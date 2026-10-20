from __future__ import annotations

from xml.etree import ElementTree

from eventkit.certificates import (
    certificate_filename,
    render_certificate_svg,
    safe_filename_slug,
)


def _certificate_name_element(svg: bytes) -> ElementTree.Element:
    root = ElementTree.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    return root.find(f".//{namespace}text[@class='certificate-name']")  # type: ignore[return-value]


def test_certificate_is_well_formed_a4_landscape_xml():
    svg = render_certificate_svg(
        "Ada Lovelace",
        "Computing Day",
        "Eventkit Foundation",
        "Participant",
        "ABCD-1234-EFGH",
    )

    root = ElementTree.fromstring(svg)

    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["width"] == "297mm"
    assert root.attrib["height"] == "210mm"
    assert root.attrib["viewBox"] == "0 0 297 210"


def test_user_text_is_xml_escaped_and_round_trips_through_parser():
    attendee = 'A & <Ada> "Lovelace"'
    event_name = "R&D <show>"
    organizer = "O'Reilly & Co."
    role = "Speaker > mentor"
    code = "A&B <C>"

    svg = render_certificate_svg(attendee, event_name, organizer, role, code)
    root = ElementTree.fromstring(svg)
    rendered_text = "".join(root.itertext())

    assert attendee in rendered_text
    assert event_name in rendered_text
    assert organizer in rendered_text
    assert role in rendered_text
    assert code in rendered_text
    assert b"&amp;" in svg
    assert b"&lt;" in svg
    assert b"&gt;" in svg
    assert b"&quot;" in svg
    assert b"&apos;" in svg


def test_rtl_names_get_plaintext_bidi_style_marker():
    svg = render_certificate_svg(
        "שלום עולם",
        "אירוע",
        "מארגן",
        "משתתף",
        "RTL-0001",
    )

    assert b"unicode-bidi: plaintext" in svg
    assert b"direction: rtl" in svg
    ElementTree.fromstring(svg)


def test_same_inputs_produce_identical_utf8_bytes():
    arguments = (
        "Zoë O'Neil",
        "Reliable Systems",
        "Open Events",
        "Volunteer",
        "CODE-0002",
    )

    first = render_certificate_svg(*arguments)
    second = render_certificate_svg(*arguments)

    assert first == second


def test_longer_names_use_a_smaller_font_size():
    short = render_certificate_svg(
        "Ada",
        "Event",
        "Organizer",
        "Participant",
        "SHORT",
    )
    long = render_certificate_svg(
        "A very long attendee name that needs a smaller readable size",
        "Event",
        "Organizer",
        "Participant",
        "LONG",
    )

    short_size = float(_certificate_name_element(short).attrib["font-size"])
    long_size = float(_certificate_name_element(long).attrib["font-size"])

    assert long_size < short_size
    assert long_size >= 18


def test_non_latin_name_uses_attendee_filename_fallback_and_keeps_code():
    slug = safe_filename_slug("张伟", "ABCD-1234")

    assert slug == "attendee-ABCD-1234"
    assert slug.isascii()
    assert certificate_filename("张伟", "ABCD-1234") == "attendee-ABCD-1234.svg"
    assert safe_filename_slug("", "ABCD-1234") == "attendee-ABCD-1234"
