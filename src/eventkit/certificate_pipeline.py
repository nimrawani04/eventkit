"""Build certificate SVG and manifest files from a validated roster."""

from __future__ import annotations

from dataclasses import dataclass
import json
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
from .credentials import (
    derive_signing_key,
    issue_credential,
    manifest_to_bytes as signed_manifest_to_bytes,
    revocation_from_dict,
    sign_manifest,
    verify_manifest,
)
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
    signed_manifest = None
    if config.signed_credentials:
        signing_key = derive_signing_key(secret)
        revocations = ()
        if config.revocations_path is not None and config.revocations_path.is_file():
            try:
                raw = json.loads(config.revocations_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Could not read revocations file: {exc}") from exc
            raw_items = raw.get("revocations", []) if isinstance(raw, dict) else raw
            if not isinstance(raw_items, list):
                raise ValueError("Revocations file must contain a list or a revocations list")
            revocations = tuple(revocation_from_dict(item) for item in raw_items)
        signed_credentials = tuple(
            issue_credential(
                config.event_id,
                certificate.code,
                certificate.role,
                signing_key,
                holder=certificate.name if config.manifest_names else None,
            )
            for certificate in issued
        )
        signed_manifest = sign_manifest(
            config.event_id,
            signed_credentials,
            signing_key,
            revocations=revocations,
        )
        files["credentials.json"] = signed_manifest_to_bytes(signed_manifest)
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
        if signed_manifest is not None:
            if not verify_manifest(signed_manifest, event_id=config.event_id):
                raise ValueError("signed credential manifest did not verify")
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
        if signed_manifest is not None:
            verified.append("Signed credential manifest verifies against its embedded public key.")
    return CertificateBundle(
        files=files,
        attendance=attendance,
        verified=tuple(verified),
        verification_issues=tuple(verification_issues),
        issued=issued,
    )


__all__ = ["CertificateBundle", "build_certificate_bundle"]
