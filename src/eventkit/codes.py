"""Deterministic certificate and check-in codes.

The functions in this module deliberately do not read a secret implicitly.
Code-producing callers must pass the value of ``EVENTKIT_SECRET``.  This keeps
the signing boundary explicit and makes it possible for a caller to validate
the environment before it starts writing an event kit.

The manifest types contain only public verification data.  In particular,
email addresses are used while issuing a code but are never copied into a
manifest entry or its JSON representation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from typing import Any


EVENTKIT_SECRET = "EVENTKIT_SECRET"
CODE_SEPARATOR = "\x1f"
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_ALPHABET_SET = frozenset(CROCKFORD_ALPHABET)
_VERIFICATION_ALIASES = str.maketrans({"I": "1", "L": "1", "O": "0"})


class CodeError(ValueError):
    """Base class for invalid code input or code data."""


class SecretError(ValueError):
    """Raised when an eventkit signing secret is missing or too short."""


class ManifestError(ValueError):
    """Raised when a manifest cannot be represented safely."""


def validate_secret(secret: str | None) -> str:
    """Validate and return an ``EVENTKIT_SECRET`` value.

    There is intentionally no fallback secret.  The minimum is measured in
    characters, matching the PRD's user-facing requirement.
    """

    if not isinstance(secret, str) or not secret:
        raise SecretError("EVENTKIT_SECRET is required")
    if len(secret) < 16:
        raise SecretError("EVENTKIT_SECRET must be at least 16 characters")
    return secret


def secret_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Read and validate ``EVENTKIT_SECRET`` from an environment mapping."""

    values = os.environ if environ is None else environ
    return validate_secret(values.get(EVENTKIT_SECRET))


# This name reads naturally at call sites that receive an optional environment
# value from a CLI/config layer.
require_secret = validate_secret


def normalize_email(email: str) -> str:
    """Return the email form included in the code input.

    eventkit's roster normalization uses a trimmed, lower-case email for
    comparisons, so codes use that same form.
    """

    if not isinstance(email, str):
        raise ValueError("email must be text")
    value = email.strip().lower()
    if not value:
        raise ValueError("email cannot be empty")
    return value


def _required_text(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    return text


def _hmac_digest(
    prefix: str,
    event_id: str,
    email: str,
    role: str,
    secret: str,
) -> bytes:
    key = validate_secret(secret).encode("utf-8")
    fields = (
        _required_text(prefix, "prefix"),
        _required_text(event_id, "event_id"),
        normalize_email(email),
        _required_text(role, "role"),
    )
    payload = CODE_SEPARATOR.join(fields).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).digest()


def _crockford_prefix(digest: bytes, length: int) -> str:
    """Encode the first ``length * 5`` digest bits as Crockford base32."""

    if length <= 0:
        raise ValueError("code length must be positive")
    available_bits = len(digest) * 8
    requested_bits = length * 5
    if requested_bits > available_bits:
        raise ValueError("requested code is longer than the HMAC digest")

    value = int.from_bytes(digest, "big")
    characters: list[str] = []
    for index in range(length):
        shift = available_bits - ((index + 1) * 5)
        characters.append(CROCKFORD_ALPHABET[(value >> shift) & 0x1F])
    return "".join(characters)


def canonicalize_code(code: str) -> str:
    """Normalize a typed/scanned code to its canonical Crockford form.

    Case, ASCII hyphens, and all Unicode whitespace are ignored.  Crockford's
    ambiguous ``I``/``L`` and ``O`` aliases are mapped to ``1`` and ``0``.
    Other characters are rejected rather than silently changing the lookup
    input.
    """

    if not isinstance(code, str):
        raise CodeError("code must be text")
    compact = "".join(code.split()).replace("-", "").upper()
    canonical = compact.translate(_VERIFICATION_ALIASES)
    if not canonical:
        raise CodeError("code cannot be empty")
    invalid = sorted(set(canonical) - _CROCKFORD_ALPHABET_SET)
    if invalid:
        raise CodeError(f"code contains invalid character(s): {''.join(invalid)}")
    return canonical


canonical_code = canonicalize_code


def format_certificate_code(code: str) -> str:
    """Return a 12-character canonical certificate code as ``XXXX-XXXX-XXXX``."""

    canonical = canonicalize_code(code)
    if len(canonical) != 12:
        raise CodeError("certificate code must contain 12 characters")
    return "-".join(
        canonical[index : index + 4] for index in range(0, len(canonical), 4)
    )


def generate_code(
    prefix: str,
    event_id: str,
    email: str,
    role: str,
    secret: str,
    *,
    length: int,
) -> str:
    """Generate an unformatted Crockford code for a prefix and length."""

    digest = _hmac_digest(prefix, event_id, email, role, secret)
    return _crockford_prefix(digest, length)


def generate_certificate_code(
    event_id: str,
    email: str,
    role: str,
    secret: str,
) -> str:
    """Generate a deterministic displayed certificate code."""

    canonical = generate_code(
        "cert", event_id, email, role, secret, length=12
    )
    return format_certificate_code(canonical)


certificate_code = generate_certificate_code


def _code_conflicts(candidate: str, existing: set[str]) -> bool:
    """Detect an exact code collision or a short candidate prefix collision."""

    return any(
        candidate == value
        or value.startswith(candidate)
        for value in existing
    )


def generate_checkin_code(
    event_id: str,
    email: str,
    role: str,
    secret: str,
    used_codes: Iterable[str] = (),
) -> str:
    """Generate a six-character check-in code, extending on collision.

    ``used_codes`` may contain displayed certificate-like formatting; it is
    canonicalized before collision checks.  The first six characters are the
    same HMAC-derived prefix on every call, and additional characters are
    taken from the same digest only when needed.
    """

    existing = {canonicalize_code(value) for value in used_codes}
    digest = _hmac_digest("checkin", event_id, email, role, secret)
    # Six characters are enough for normal use, but collision extension may
    # need more than the 256 bits in one digest.  Extend the deterministic
    # bit stream with keyed blocks derived from that digest rather than
    # truncating, looping forever, or introducing randomness.
    stream = bytearray(digest)
    key = validate_secret(secret).encode("utf-8")
    extension = 0
    length = 6
    while True:
        while length * 5 > len(stream) * 8:
            stream.extend(
                hmac.new(
                    key,
                    digest + extension.to_bytes(4, "big"),
                    hashlib.sha256,
                ).digest()
            )
            extension += 1
        candidate = _crockford_prefix(bytes(stream), length)
        if not _code_conflicts(candidate, existing):
            return candidate
        length += 1


checkin_code = generate_checkin_code


@dataclass(frozen=True, slots=True)
class CertificateRecipient:
    """Private input used to issue a certificate or check-in code."""

    email: str
    role: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedCertificate:
    """A certificate issued to a recipient before manifest publication."""

    code: str
    email: str
    role: str
    name: str | None = None


def _recipient(value: Any) -> CertificateRecipient:
    if isinstance(value, CertificateRecipient):
        return value
    if isinstance(value, Mapping):
        return CertificateRecipient(
            email=value.get("email", ""),
            role=value.get("role", ""),
            name=value.get("name"),
        )
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return CertificateRecipient(
            email=value[0], role=value[1], name=value[2] if len(value) > 2 else None
        )
    try:
        return CertificateRecipient(
            email=value.email,
            role=value.role,
            name=getattr(value, "name", None),
        )
    except AttributeError as exc:
        raise TypeError(
            "recipient must provide email, role, and optional name"
        ) from exc


def issue_certificate(
    event_id: str,
    recipient: CertificateRecipient | Mapping[str, Any],
    secret: str,
) -> IssuedCertificate:
    """Issue one certificate while retaining private input for the caller."""

    item = _recipient(recipient)
    role = _required_text(item.role, "role")
    email = normalize_email(item.email)
    name = None if item.name is None else str(item.name).strip() or None
    return IssuedCertificate(
        code=generate_certificate_code(event_id, email, role, secret),
        email=email,
        role=role,
        name=name,
    )


def issue_certificates(
    event_id: str,
    recipients: Iterable[CertificateRecipient | Mapping[str, Any]],
    secret: str,
) -> tuple[IssuedCertificate, ...]:
    """Issue certificates in the supplied deterministic order."""

    return tuple(issue_certificate(event_id, recipient, secret) for recipient in recipients)


def generate_checkin_codes(
    event_id: str,
    recipients: Iterable[CertificateRecipient | Mapping[str, Any]],
    secret: str,
) -> tuple[str, ...]:
    """Generate a unique, deterministic check-in code for each recipient."""

    used: set[str] = set()
    result: list[str] = []
    for value in recipients:
        item = _recipient(value)
        code = generate_checkin_code(
            event_id,
            item.email,
            item.role,
            secret,
            used_codes=used,
        )
        result.append(code)
        used.add(canonicalize_code(code))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ManifestCertificate:
    """Public certificate metadata stored in a manifest."""

    code_sha256: str
    role: str
    name: str | None = None

    def __post_init__(self) -> None:
        digest = str(self.code_sha256).lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ManifestError("code_sha256 must be a 64-character SHA-256 hex digest")
        object.__setattr__(self, "code_sha256", digest)
        object.__setattr__(self, "role", _required_text(self.role, "role"))
        if self.name is not None:
            cleaned_name = str(self.name).strip()
            object.__setattr__(self, "name", cleaned_name or None)

    @property
    def code_hash(self) -> str:
        """Alias for callers that use the shorter manifest terminology."""

        return self.code_sha256

    def as_dict(self) -> dict[str, str]:
        result: dict[str, str] = {
            "code_sha256": self.code_sha256,
            "role": self.role,
        }
        if self.name is not None:
            result["name"] = self.name
        return result


@dataclass(frozen=True, slots=True)
class Manifest:
    """The public, privacy-preserving certificate manifest."""

    event_id: str
    event_name: str
    certificates: tuple[ManifestCertificate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "event_name", _required_text(self.event_name, "event_name"))
        entries = tuple(self.certificates)
        if not all(isinstance(entry, ManifestCertificate) for entry in entries):
            raise ManifestError("certificates must contain ManifestCertificate entries")
        object.__setattr__(self, "certificates", entries)

    @property
    def name(self) -> str:
        """Alias for the event name used by display-oriented callers."""

        return self.event_name

    @property
    def certificate_count(self) -> int:
        return len(self.certificates)

    @property
    def entries(self) -> tuple[ManifestCertificate, ...]:
        return self.certificates

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "certificate_count": self.certificate_count,
            "certificates": [entry.as_dict() for entry in self.certificates],
        }

    def lookup(self, code: str) -> ManifestCertificate | None:
        return lookup_code(code, self)


def canonical_code_sha256(code: str) -> str:
    """Hash a canonicalized code for manifest storage and lookup."""

    canonical = canonicalize_code(code)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


code_sha256 = canonical_code_sha256


def _certificate_parts(value: Any) -> tuple[str | None, str, str | None, bool]:
    """Return raw code/hash, role, name, and whether the value is a hash entry."""

    if isinstance(value, ManifestCertificate):
        return value.code_sha256, value.role, value.name, True
    if isinstance(value, IssuedCertificate):
        return value.code, value.role, value.name, False
    if isinstance(value, Mapping):
        if value.get("code_sha256") is not None:
            return (
                str(value["code_sha256"]),
                _required_text(value.get("role"), "role"),
                value.get("name"),
                True,
            )
        code = value.get("code", value.get("canonical_code"))
        return (
            None if code is None else str(code),
            _required_text(value.get("role"), "role"),
            value.get("name"),
            False,
        )
    try:
        return (
            str(value.code),
            _required_text(value.role, "role"),
            getattr(value, "name", None),
            False,
        )
    except AttributeError as exc:
        raise TypeError(
            "certificate must provide code, role, and optional name"
        ) from exc


def _stored_hash(raw: str, already_hashed: bool) -> str:
    if already_hashed:
        digest = raw.lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ManifestError("code_sha256 must be a 64-character SHA-256 hex digest")
        return digest
    return canonical_code_sha256(raw)


def create_manifest(
    event_id: str,
    event_name: str,
    certificates: Iterable[Any],
    *,
    include_names: bool = True,
) -> Manifest:
    """Create a manifest from issued codes or code-bearing mappings.

    An input mapping may also include an ``email`` key for caller convenience;
    it is intentionally ignored.  The resulting manifest has no email field.
    Duplicate canonical codes are rejected because they would make lookup
    ambiguous.
    """

    entries: list[ManifestCertificate] = []
    seen_hashes: set[str] = set()
    for certificate in certificates:
        raw_code, role, name, already_hashed = _certificate_parts(certificate)
        if raw_code is None:
            raise ManifestError("certificate code is required")
        digest = _stored_hash(raw_code, already_hashed)
        if digest in seen_hashes:
            raise ManifestError("duplicate certificate code")
        seen_hashes.add(digest)
        stored_name = name if include_names else None
        entries.append(ManifestCertificate(digest, role, stored_name))
    return Manifest(_required_text(event_id, "event_id"), _required_text(event_name, "event_name"), tuple(entries))


def create_manifest_from_recipients(
    event_id: str,
    event_name: str,
    recipients: Iterable[CertificateRecipient | Mapping[str, Any]],
    secret: str,
    *,
    include_names: bool = True,
) -> Manifest:
    """Issue certificates and immediately create their public manifest."""

    return create_manifest(
        event_id,
        event_name,
        issue_certificates(event_id, recipients, secret),
        include_names=include_names,
    )


def _as_manifest(value: Manifest | Mapping[str, Any]) -> Manifest:
    if isinstance(value, Manifest):
        return value
    if isinstance(value, Mapping):
        return manifest_from_dict(value)
    raise TypeError("manifest must be a Manifest or mapping")


def lookup_code(
    code: str,
    manifest: Manifest | Mapping[str, Any],
) -> ManifestCertificate | None:
    """Return public metadata for a valid code, or ``None`` for invalid input."""

    try:
        digest = canonical_code_sha256(code)
    except (CodeError, TypeError, ValueError):
        return None
    document = _as_manifest(manifest)
    for entry in document.certificates:
        if hmac.compare_digest(digest, entry.code_sha256):
            return entry
    return None


lookup_certificate = lookup_code
lookup_manifest_code = lookup_code


def verify_code(code: str, manifest: Manifest | Mapping[str, Any]) -> bool:
    """Return whether ``code`` is present in the supplied event manifest."""

    return lookup_code(code, manifest) is not None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """A display-friendly result from :func:`verify`."""

    valid: bool
    event_id: str
    event_name: str
    role: str | None = None
    name: str | None = None
    canonical_code: str | None = None

    def __bool__(self) -> bool:
        return self.valid


def verify(
    code: str,
    manifest: Manifest | Mapping[str, Any],
) -> VerificationResult:
    """Verify a code and include the manifest event context in the result."""

    document = _as_manifest(manifest)
    try:
        canonical = canonicalize_code(code)
    except (CodeError, TypeError, ValueError):
        canonical = None
    entry = None if canonical is None else lookup_code(canonical, document)
    return VerificationResult(
        valid=entry is not None,
        event_id=document.event_id,
        event_name=document.event_name,
        role=None if entry is None else entry.role,
        name=None if entry is None else entry.name,
        canonical_code=canonical,
    )


verify_manifest_code = verify


def manifest_to_dict(manifest: Manifest | Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-ready manifest mapping."""

    return _as_manifest(manifest).as_dict()


def manifest_to_json(
    manifest: Manifest | Mapping[str, Any],
    *,
    newline: bool = True,
) -> str:
    """Serialize a manifest with stable key ordering and no incidental spacing."""

    rendered = json.dumps(
        manifest_to_dict(manifest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered + ("\n" if newline else "")


def manifest_from_dict(data: Mapping[str, Any]) -> Manifest:
    """Load and validate a manifest mapping."""

    if not isinstance(data, Mapping):
        raise ManifestError("manifest must be an object")
    raw_entries = data.get("certificates")
    if not isinstance(raw_entries, list):
        raise ManifestError("manifest certificates must be a list")
    entries = tuple(
        ManifestCertificate(
            code_sha256=item.get("code_sha256"),
            role=item.get("role"),
            name=item.get("name"),
        )
        for item in raw_entries
        if isinstance(item, Mapping)
    )
    if len(entries) != len(raw_entries):
        raise ManifestError("manifest certificates must contain objects")
    count = data.get("certificate_count", len(entries))
    if count != len(entries):
        raise ManifestError("manifest certificate_count does not match certificates")
    return Manifest(
        event_id=data.get("event_id"),
        event_name=data.get("event_name"),
        certificates=entries,
    )


def manifest_from_json(data: str) -> Manifest:
    """Parse and validate deterministic manifest JSON."""

    try:
        value = json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest JSON is invalid") from exc
    return manifest_from_dict(value)


__all__ = [
    "CODE_SEPARATOR",
    "CROCKFORD_ALPHABET",
    "EVENTKIT_SECRET",
    "CertificateRecipient",
    "CodeError",
    "IssuedCertificate",
    "Manifest",
    "ManifestCertificate",
    "ManifestError",
    "SecretError",
    "VerificationResult",
    "canonical_code",
    "canonical_code_sha256",
    "canonicalize_code",
    "certificate_code",
    "checkin_code",
    "code_sha256",
    "create_manifest",
    "create_manifest_from_recipients",
    "format_certificate_code",
    "generate_certificate_code",
    "generate_checkin_code",
    "generate_checkin_codes",
    "generate_code",
    "issue_certificate",
    "issue_certificates",
    "lookup_certificate",
    "lookup_code",
    "lookup_manifest_code",
    "manifest_from_dict",
    "manifest_from_json",
    "manifest_to_dict",
    "manifest_to_json",
    "normalize_email",
    "require_secret",
    "secret_from_environment",
    "validate_secret",
    "verify",
    "verify_code",
    "verify_manifest_code",
]
