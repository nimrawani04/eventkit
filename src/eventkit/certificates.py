"""Pure, deterministic SVG certificate rendering helpers.

The renderer deliberately returns UTF-8 bytes rather than writing a file, and
the same inputs always produce the same bytes.
"""

from __future__ import annotations

import re
import unicodedata
from xml.sax.saxutils import escape

__all__ = [
    "certificate_filename",
    "render_certificate_svg",
    "safe_filename_slug",
]

_SVG_NS = "http://www.w3.org/2000/svg"
_A4_LANDSCAPE_WIDTH_MM = 297
_A4_LANDSCAPE_HEIGHT_MM = 210
_VIEWBOX_WIDTH = 297
_VIEWBOX_HEIGHT = 210
_FONT_STACK = '"DejaVu Sans", "Noto Sans", "Segoe UI", Arial, Helvetica, sans-serif'
_RTL_BIDI_CLASSES = frozenset({"R", "AL", "AN", "RLE", "RLO", "RLI"})
_NON_XML_CODEPOINTS = frozenset({0xFFFE, 0xFFFF})


def _as_text(value: str) -> str:
    """Return *value* as a string, rejecting ambiguous non-text inputs."""

    if not isinstance(value, str):
        raise TypeError("certificate fields must be strings")
    return value


def _xml_safe_text(value: str) -> str:
    """Remove characters that XML 1.0 cannot represent."""

    return "".join(
        character
        for character in value
        if (
            ord(character) in (0x9, 0xA, 0xD)
            or 0x20 <= ord(character) <= 0xD7FF
            or 0xE000 <= ord(character) <= 0xFFFD
            or 0x10000 <= ord(character) <= 0x10FFFF
        )
        and ord(character) not in _NON_XML_CODEPOINTS
    )


def _xml_text(value: str) -> str:
    """Prepare user text for an SVG text node.

    Escaping quotes and apostrophes too is not required in a text node, but it
    makes the escaping contract explicit and keeps this helper safe if a
    rendered field is moved into an attribute later.
    """

    return escape(
        _xml_safe_text(_as_text(value)),
        {"\"": "&quot;", "'": "&apos;"},
    )


def _contains_rtl(value: str) -> bool:
    return any(
        unicodedata.bidirectional(character) in _RTL_BIDI_CLASSES
        for character in value
    )


def _font_size_for_name(name: str) -> str:
    """Choose a readable name size that decreases for longer names."""

    # Keep ordinary names prominent while ensuring very long names do not
    # consume the certificate's other content.
    size = max(18.0, min(36.0, 36.0 - max(0, len(name) - 22) * 0.35))
    return f"{size:.2f}".rstrip("0").rstrip(".")


def _slug_fragment(value: str, *, lowercase: bool) -> str:
    """Convert text to a filesystem-safe ASCII hyphenated fragment."""

    ascii_text = (
        unicodedata.normalize("NFKD", _as_text(value))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    fragment = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    return fragment.lower() if lowercase else fragment


def safe_filename_slug(attendee: str, certificate_code: str) -> str:
    """Return an ASCII filename stem containing the attendee and code.

    Accented Latin characters are reduced to ASCII, while a name that has no
    ASCII representation (or is empty) uses the stable ``attendee`` fallback.
    The certificate code is sanitized independently so the result cannot
    introduce path separators or other unsafe filename characters.
    """

    attendee_slug = _slug_fragment(attendee, lowercase=True) or "attendee"
    code_slug = _slug_fragment(certificate_code, lowercase=False) or "certificate"
    return f"{attendee_slug}-{code_slug}"


def certificate_filename(attendee: str, certificate_code: str) -> str:
    """Return the SVG filename for an attendee and certificate code."""

    return f"{safe_filename_slug(attendee, certificate_code)}.svg"


def render_certificate_svg(
    attendee: str,
    event_name: str,
    organizer: str,
    role: str,
    certificate_code: str,
    *,
    certificate_title: str = "Certificate of Participation",
) -> bytes:
    """Render one deterministic A4-landscape certificate as SVG text.

    All five arguments are user-provided text and are XML-escaped before they
    are inserted.  The output uses only a system font stack and contains no
    clock, random, or environment-dependent values.
    """

    attendee_text = _xml_safe_text(_as_text(attendee))
    event_text = _xml_safe_text(_as_text(event_name))
    organizer_text = _xml_safe_text(_as_text(organizer))
    role_text = _xml_safe_text(_as_text(role))
    code_text = _xml_safe_text(_as_text(certificate_code))
    title_text = _xml_safe_text(_as_text(certificate_title))

    name_style = "text-anchor: middle;"
    if _contains_rtl(attendee_text):
        name_style += " unicode-bidi: plaintext; direction: rtl;"

    name_font_size = _font_size_for_name(attendee_text)
    svg = "\n".join(
        (
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="{_SVG_NS}" '
                f'width="{_A4_LANDSCAPE_WIDTH_MM}mm" '
                f'height="{_A4_LANDSCAPE_HEIGHT_MM}mm" '
                f'viewBox="0 0 {_VIEWBOX_WIDTH} {_VIEWBOX_HEIGHT}" '
                'version="1.1">'
            ),
            "  <title>Certificate for " + _xml_text(attendee_text) + "</title>",
            (
                "  <desc>Certificate for "
                + _xml_text(attendee_text)
                + " at "
                + _xml_text(event_text)
                + ", organized by "
                + _xml_text(organizer_text)
                + ".</desc>"
            ),
            "  <style>",
            f"    .system {{ font-family: {_FONT_STACK}; }}",
            "    .background { fill: #fbfaf7; }",
            "    .border { fill: none; stroke: #1e3a5f; stroke-width: 1.2; }",
            "    .accent { fill: #c9953d; }",
            "    .eyebrow { fill: #1e3a5f; font-size: 6px; font-weight: 700; letter-spacing: 1.8px; }",
            "    .event { fill: #263238; font-size: 12px; font-weight: 600; }",
            "    .body { fill: #4e5d66; font-size: 7px; }",
            "    .role { fill: #1e3a5f; font-size: 10px; font-weight: 600; }",
            "    .meta { fill: #4e5d66; font-size: 5.5px; }",
            "  </style>",
            '  <rect class="background" x="0" y="0" width="297" height="210"/>',
            '  <rect class="border" x="10" y="10" width="277" height="190" rx="2"/>',
            '  <rect class="accent" x="22" y="22" width="42" height="2" rx="1"/>',
            '  <rect class="accent" x="233" y="22" width="42" height="2" rx="1"/>',
            '  <g class="system" text-anchor="middle">',
            '    <text class="eyebrow" x="148.5" y="42">' + _xml_text(title_text.upper()) + "</text>",
            '    <text class="event" x="148.5" y="59">' + _xml_text(event_text) + "</text>",
            '    <line x1="112" y1="69" x2="185" y2="69" stroke="#c9953d" stroke-width="0.8"/>',
            '    <text class="body" x="148.5" y="88">This certificate is presented to</text>',
            (
                '    <text class="certificate-name" '
                f'x="148.5" y="119" font-size="{name_font_size}" '
                f'style="{name_style}">' + _xml_text(attendee_text) + "</text>"
            ),
            '    <text class="body" x="148.5" y="139">for participating as</text>',
            '    <text class="role" x="148.5" y="155">' + _xml_text(role_text) + "</text>",
            '    <line x1="112" y1="165" x2="185" y2="165" stroke="#c9953d" stroke-width="0.8"/>',
            '  </g>',
            '  <g class="system">',
            '    <text class="meta" x="22" y="185">Organized by ' + _xml_text(organizer_text) + "</text>",
            '    <text class="meta" x="275" y="185" text-anchor="end">Certificate code: '
            + _xml_text(code_text)
            + "</text>",
            '  </g>',
            "</svg>",
        )
    )
    return svg.encode("utf-8")
