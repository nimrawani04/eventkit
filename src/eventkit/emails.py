"""Deterministic, unsent email draft generation.

This module renders RFC-style ``.eml`` bytes but deliberately has no sending
integration.  It uses :mod:`email` for message construction, :mod:`csv` for
the mail-merge index, and :mod:`html` for HTML escaping.  MIME boundaries are
set explicitly because the email package otherwise creates them lazily with
non-deterministic values.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html import escape as html_escape
import hashlib
import io
import os
from pathlib import Path
import string
from typing import Any

from .certificates import certificate_filename, safe_filename_slug

__all__ = [
    "DEFAULT_HTML_TEMPLATE",
    "DEFAULT_SUBJECT_TEMPLATE",
    "DEFAULT_TEXT_TEMPLATE",
    "MAILMERGE_FIELDS",
    "EmailDraft",
    "EmailRecipient",
    "build_email_drafts",
    "render_email_draft",
    "render_mailmerge_csv",
    "verify_email_draft",
]


DEFAULT_SUBJECT_TEMPLATE = "Your {event} certificate"
DEFAULT_TEXT_TEMPLATE = (
    "Hello {name},\n\n"
    "Thank you for participating in {event}.\n\n"
    "Your certificate code is {code}.\n"
)
DEFAULT_HTML_TEMPLATE = (
    "<html><body>"
    "<p>Hello {name},</p>"
    "<p>Thank you for participating in {event}.</p>"
    "<p>Your certificate code is <strong>{code}</strong>.</p>"
    "</body></html>"
)

# These are metadata only.  The message bytes are intentionally not represented
# in the CSV, which keeps this index useful to a mail-merge tool and out of any
# audit/log metadata a caller may derive from it.
MAILMERGE_FIELDS = ("name", "email", "event", "code", "draft", "certificate")

_TEMPLATE_FIELDS = frozenset({"name", "event", "code"})
_SMTP_POLICY = policy.SMTP


@dataclass(frozen=True, slots=True)
class EmailRecipient:
    """The private recipient data needed to render one email draft."""

    name: str
    email: str
    code: str


@dataclass(frozen=True, slots=True)
class EmailDraft:
    """One deterministic email draft and its output metadata.

    ``message`` contains the complete ``.eml`` file.  It is excluded from the
    dataclass representation so accidental debug logging does not print an
    email payload.  ``filename`` is only a safe ASCII basename; a caller can
    place it under its ``emails/`` output directory.
    """

    name: str
    email: str
    event: str
    code: str
    filename: str
    message: bytes = field(repr=False)
    certificate_filename: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, bytes):
            raise TypeError("email draft message must be bytes")

    @property
    def eml_filename(self) -> str:
        """Return the generated ``.eml`` basename."""

        return self.filename

    @property
    def bytes(self) -> bytes:
        """Return the complete RFC message bytes."""

        return self.message

    @property
    def eml_bytes(self) -> bytes:
        """Alias for :attr:`message` useful to file-writing callers."""

        return self.message

    @property
    def has_certificate(self) -> bool:
        """Whether this draft contains a certificate attachment."""

        return self.certificate_filename is not None


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    return value


def _required_text(value: Any, field_name: str) -> str:
    result = _text(value, field_name)
    if not result.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return result


def _header_text(value: str, field_name: str) -> str:
    """Reject header injection before passing text to ``EmailMessage``."""

    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} cannot contain a line break")
    return value


def _template_values(
    template: str,
    values: Mapping[str, str],
) -> str:
    """Format a template containing only the three supported field names."""

    template = _text(template, "template")
    formatter = string.Formatter()
    try:
        parsed = formatter.parse(template)
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if field_name not in _TEMPLATE_FIELDS:
                raise ValueError(
                    f"unsupported email template field: {{{field_name}}}"
                )
            if format_spec or conversion:
                raise ValueError(
                    f"email template field {{{field_name}}} cannot use a format or conversion"
                )
        return template.format_map(values)
    except (KeyError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "unsupported email template field"
        ):
            raise
        raise ValueError("invalid email template") from exc


def _read_certificate(certificate: bytes | bytearray | os.PathLike[str] | str) -> bytes:
    if isinstance(certificate, bytes):
        return certificate
    if isinstance(certificate, bytearray):
        return bytes(certificate)
    if isinstance(certificate, (str, os.PathLike)):
        return Path(certificate).read_bytes()
    raise TypeError("certificate must be SVG bytes or a filesystem path")


def _boundary(
    kind: str,
    values: Iterable[bytes],
    forbidden: Iterable[bytes],
) -> str:
    """Create a stable boundary which is absent from the unencoded bodies."""

    digest = hashlib.sha256(b"\x1f".join(values)).hexdigest()
    forbidden_bytes = tuple(forbidden)
    for suffix in range(1000):
        suffix_text = "" if suffix == 0 else f"-{suffix}"
        candidate = f"eventkit-{kind}-{digest[:24]}{suffix_text}"
        marker = candidate.encode("ascii")
        if all(marker not in value for value in forbidden_bytes):
            return candidate
    raise ValueError("could not choose a deterministic MIME boundary")


def _set_deterministic_boundaries(
    message: EmailMessage,
    *,
    plain_text: str,
    html_text: str,
    certificate: bytes | None,
) -> None:
    body_values = (
        plain_text.encode("utf-8"),
        html_text.encode("utf-8"),
        b"" if certificate is None else certificate,
    )
    alternative_boundary = _boundary("alternative", body_values, body_values)

    if certificate is None:
        message.set_boundary(alternative_boundary)
        return

    payload = message.get_payload()
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], Message):
        raise ValueError("email alternative body was not built as a MIME part")
    alternative = payload[0]
    alternative.set_boundary(alternative_boundary)
    mixed_boundary = _boundary(
        "mixed",
        (*body_values, alternative_boundary.encode("ascii")),
        (*body_values, alternative_boundary.encode("ascii")),
    )
    message.set_boundary(mixed_boundary)


def render_email_draft(
    name: str,
    email: str,
    event: str,
    code: str,
    *,
    text_template: str = DEFAULT_TEXT_TEMPLATE,
    html_template: str = DEFAULT_HTML_TEMPLATE,
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE,
    certificate: bytes | bytearray | os.PathLike[str] | str | None = None,
) -> EmailDraft:
    """Render one unsent, deterministic draft email.

    The text part receives the source values unchanged.  The HTML part gets
    each value escaped with :func:`html.escape`.  ``certificate`` may be SVG
    bytes or a path to an SVG file; when present it is attached with the safe
    generated certificate filename.  No ``Date`` header is added.
    """

    name = _text(name, "name")
    email = _required_text(email, "email")
    event = _required_text(event, "event")
    code = _required_text(code, "code")
    _header_text(email, "email")

    raw_values = {"name": name, "event": event, "code": code}
    html_values = {
        key: html_escape(value, quote=True) for key, value in raw_values.items()
    }
    plain_text = _template_values(text_template, raw_values)
    html_text = _template_values(html_template, html_values)
    subject = _template_values(subject_template, raw_values)
    _header_text(subject, "subject")

    certificate_bytes = None if certificate is None else _read_certificate(certificate)
    attached_filename = (
        certificate_filename(name, code) if certificate_bytes is not None else None
    )

    message = EmailMessage(policy=_SMTP_POLICY)
    # X-Unsent marks the message as a draft for desktop mail clients.  There
    # is intentionally no From or Date header: this module never sends mail.
    message["X-Unsent"] = "1"
    message["To"] = email
    message["Subject"] = subject
    message.set_content(plain_text, subtype="plain", charset="utf-8")
    message.add_alternative(html_text, subtype="html", charset="utf-8")
    if certificate_bytes is not None:
        message.add_attachment(
            certificate_bytes,
            maintype="image",
            subtype="svg+xml",
            filename=attached_filename,
        )

    _set_deterministic_boundaries(
        message,
        plain_text=plain_text,
        html_text=html_text,
        certificate=certificate_bytes,
    )
    rendered = message.as_bytes(policy=_SMTP_POLICY, unixfrom=False)
    return EmailDraft(
        name=name,
        email=email,
        event=event,
        code=code,
        filename=f"{safe_filename_slug(name, code)}.eml",
        message=rendered,
        certificate_filename=attached_filename,
    )


def _record_value(record: object, names: Sequence[str], field_name: str) -> str:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                value = record[name]
                return "" if value is None else _text(value, field_name)
    else:
        for name in names:
            if hasattr(record, name):
                value = getattr(record, name)
                return "" if value is None else _text(value, field_name)
    raise ValueError(f"email recipient is missing {field_name}")


def _coerce_recipient(record: object) -> EmailRecipient:
    if isinstance(record, EmailRecipient):
        return record
    if isinstance(record, (tuple, list)):
        if len(record) < 3:
            raise ValueError("email recipient sequence must contain name, email, and code")
        return EmailRecipient(
            name=_text(record[0], "name"),
            email=_text(record[1], "email"),
            code=_text(record[2], "code"),
        )
    return EmailRecipient(
        name=_record_value(record, ("name", "attendee_name"), "name"),
        email=_record_value(record, ("email", "recipient_email"), "email"),
        code=_record_value(
            record,
            ("code", "certificate_code", "certificate"),
            "code",
        ),
    )


def build_email_drafts(
    recipients: Iterable[EmailRecipient | Mapping[str, Any] | object],
    event: str,
    *,
    certificate_dir: os.PathLike[str] | str | None = None,
    text_template: str = DEFAULT_TEXT_TEMPLATE,
    html_template: str = DEFAULT_HTML_TEMPLATE,
    subject_template: str = DEFAULT_SUBJECT_TEMPLATE,
) -> tuple[EmailDraft, ...]:
    """Build drafts in the supplied recipient order.

    ``recipients`` can contain :class:`EmailRecipient`, mappings, three-item
    ``(name, email, code)`` sequences, or objects with matching attributes
    (including the existing ``IssuedCertificate`` type).  If
    ``certificate_dir`` is supplied, the matching deterministic SVG basename
    is looked up there.  A missing SVG is not an error; that draft simply has
    no attachment.
    """

    root = None if certificate_dir is None else Path(certificate_dir)
    drafts: list[EmailDraft] = []
    for raw_recipient in recipients:
        recipient = _coerce_recipient(raw_recipient)
        certificate: Path | None = None
        if root is not None:
            candidate = root / certificate_filename(recipient.name, recipient.code)
            if candidate.is_file():
                certificate = candidate
        drafts.append(
            render_email_draft(
                recipient.name,
                recipient.email,
                event,
                recipient.code,
                text_template=text_template,
                html_template=html_template,
                subject_template=subject_template,
                certificate=certificate,
            )
        )
    return tuple(drafts)


def render_mailmerge_csv(
    drafts: Iterable[EmailDraft],
    *,
    fieldnames: Sequence[str] = MAILMERGE_FIELDS,
) -> bytes:
    """Render the metadata index for drafts, preserving draft source order.

    The default columns are ``name,email,event,code,draft,certificate``.  No
    message body or MIME bytes are written to this CSV.
    """

    fields = tuple(fieldnames)
    unknown = sorted(set(fields) - set(MAILMERGE_FIELDS))
    if unknown:
        raise ValueError(f"unsupported mail-merge field(s): {', '.join(unknown)}")
    if len(set(fields)) != len(fields):
        raise ValueError("mail-merge fieldnames must be unique")

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for draft in drafts:
        row = {
            "name": draft.name,
            "email": draft.email,
            "event": draft.event,
            "code": draft.code,
            "draft": draft.filename,
            "certificate": draft.certificate_filename or "",
        }
        writer.writerow({field_name: row[field_name] for field_name in fields})
    return stream.getvalue().encode("utf-8")


def verify_email_draft(
    content: EmailDraft | bytes | bytearray,
    *,
    expected_email: str | None = None,
    expected_attachment: bool | None = None,
) -> EmailMessage:
    """Parse and validate the draft-only invariants.

    The returned :class:`~email.message.EmailMessage` is useful for callers'
    post-checks.  Validation errors contain metadata only; the message body
    and attachment bytes are never included in an exception or log string.
    """

    if isinstance(content, EmailDraft):
        raw = content.message
    elif isinstance(content, bytes):
        raw = content
    elif isinstance(content, bytearray):
        raw = bytes(content)
    else:
        raise TypeError("email draft content must be EmailDraft or bytes")

    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("email draft is not parseable") from exc
    if message.defects:
        raise ValueError("email draft contains malformed MIME data")
    if message.get("X-Unsent") != "1":
        raise ValueError("email draft must have X-Unsent: 1")
    if message.get("Date") is not None:
        raise ValueError("email draft must not have a Date header")
    if expected_email is not None and message.get("To") != expected_email:
        raise ValueError("email draft recipient does not match")

    has_attachment = any(
        part.get_content_disposition() == "attachment" for part in message.iter_attachments()
    )
    if expected_attachment is not None and has_attachment != expected_attachment:
        raise ValueError("email draft attachment state does not match")
    return message
