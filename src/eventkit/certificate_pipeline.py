"""Build certificate SVG and manifest files from a validated roster."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from .attendance import AttendanceResult, filter_attendance, recipients_from_roster
from .certificates import certificate_filename, render_certificate_svg
from .codes import (
    CertificateRecipient,
    IssuedCertificate,
    create_manifest,
    issue_certificates,
    manifest_to_json,
    secret_from_environment,
    verify_code,
)
from .config import EventConfig
from .csvio import CsvTable
from .model import RosterResult


@dataclass(frozen=True, slots=True)
class CertificateBundle:
    """Certificate output bytes plus attendance findings and skip state."""

    files: dict[str, bytes]
    attendance: AttendanceResult | None
    skipped_reason: str | None = None
    verified: tuple[str, ...] = ()
    verification_issues: tuple[str, ...] = ()
    issued: tuple[IssuedCertificate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", dict(self.files))


def _unique_recipients(
    recipients: tuple[CertificateRecipient, ...],
) -> tuple[CertificateRecipient, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[CertificateRecipient] = []
    for recipient in recipients:
        key = (recipient.email.strip().lower(), recipient.role.strip())
        if key not in seen:
            seen.add(key)
            result.append(recipient)
    return tuple(result)


def build_certificate_bundle(
    config: EventConfig,
    roster: RosterResult,
    *,
    attendance_table: CsvTable | None = None,
) -> CertificateBundle:
    """Issue deterministic certificates for the configured recipient set."""

    attendance: AttendanceResult | None = None
    if config.certificate_issue_to == "attended":
        if attendance_table is None:
            return CertificateBundle(
                files={},
                attendance=None,
                skipped_reason=(
                    "Certificates skipped: issue_to is 'attended' but no attendance CSV was provided."
                ),
            )
        attendance = filter_attendance(attendance_table, roster)
        recipients = attendance.recipients
    else:
        recipients = recipients_from_roster(roster)

    secret = secret_from_environment()
    recipients = _unique_recipients(recipients)
    issued = issue_certificates(config.event_id, recipients, secret)
    manifest = create_manifest(
        config.event_id,
        config.name,
        issued,
        include_names=config.manifest_names,
    )

    files: dict[str, bytes] = {
        "manifest.json": manifest_to_json(manifest).encode("utf-8"),
    }
    for certificate in issued:
        name = certificate.name or ""
        files[
            f"certificates/{certificate_filename(name, certificate.code)}"
        ] = render_certificate_svg(
            name,
            config.name,
            config.organizer,
            certificate.role,
            certificate.code,
            certificate_title=config.certificate_title,
        )
    verified: list[str] = []
    verification_issues: list[str] = []
    try:
        for certificate in issued:
            path = f"certificates/{certificate_filename(certificate.name or '', certificate.code)}"
            ElementTree.fromstring(files[path])
            if not verify_code(certificate.code, manifest):
                raise ValueError(f"certificate code did not verify: {certificate.code}")
        if len(issued) != manifest.certificate_count:
            raise ValueError("certificate count does not match manifest")
    except (ElementTree.ParseError, ValueError) as exc:
        verification_issues.append(f"Certificate post-check failed: {exc}")
    else:
        verified.extend(
            (
                "Every generated certificate SVG parses as XML.",
                "Every generated certificate code verifies against the manifest.",
                "Certificate count matches the manifest.",
            )
        )
    return CertificateBundle(
        files=files,
        attendance=attendance,
        verified=tuple(verified),
        verification_issues=tuple(verification_issues),
        issued=issued,
    )


__all__ = ["CertificateBundle", "build_certificate_bundle"]
