"""Standard-library-only signed event credentials.

This module contains the small cryptographic boundary used by eventkit's
credential format.  It intentionally does not import a crypto package:
Ed25519 is implemented directly from RFC 8032, while SHA-256, SHA-512, JSON,
and base64 come from the Python standard library.

The public objects in this module are immutable and serialize to canonical
JSON.  Signatures cover every serialized field except the signature itself;
parsers reject unknown fields so an apparently harmless modification cannot be
silently ignored by verification.  Email addresses are not accepted as a
credential field and therefore cannot enter a public credential or manifest.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any, Iterator, TypeAlias


__all__ = [
    "CREDENTIAL_SCHEMA",
    "MANIFEST_SCHEMA",
    "REVOCATION_SCHEMA",
    "CredentialsError",
    "Credential",
    "Revocation",
    "SignedManifest",
    "SignedPackage",
    "CredentialPackage",
    "Manifest",
    "Ed25519PrivateKey",
    "Ed25519PublicKey",
    "SigningKey",
    "KeyPair",
    "derive_signing_key",
    "generate_signing_key",
    "generate_keypair",
    "public_key_bytes",
    "serialize_public_key",
    "deserialize_public_key",
    "public_key_id",
    "issue_credential",
    "verify_credential",
    "credential_to_dict",
    "credential_from_dict",
    "credential_to_json",
    "credential_from_json",
    "credential_to_bytes",
    "issue_revocation",
    "verify_revocation",
    "is_revoked",
    "revocation_to_dict",
    "revocation_from_dict",
    "revocation_to_json",
    "revocation_from_json",
    "revocation_to_bytes",
    "sign_manifest",
    "verify_manifest",
    "manifest_to_dict",
    "manifest_from_dict",
    "manifest_to_json",
    "manifest_from_json",
    "manifest_to_bytes",
    "sign_package",
    "verify_package",
    "package_to_json",
    "package_from_json",
]


CREDENTIAL_SCHEMA = "eventkit.credential"
REVOCATION_SCHEMA = "eventkit.revocation"
MANIFEST_SCHEMA = "eventkit.manifest"
_KEY_ID_PREFIX = "ed25519-"
_SEED_DOMAIN = b"eventkit/ed25519-seed\x00"
_SIGNATURE_DOMAIN = b"eventkit/"


class CredentialsError(ValueError):
    """Raised when credential data or key material is malformed."""


# ---------------------------------------------------------------------------
# RFC 8032 Ed25519
# ---------------------------------------------------------------------------

_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_IDENTITY = (0, 1, 1, 0)
_Point: TypeAlias = tuple[int, int, int, int]


def _x_recover(y: int) -> int:
    xx = ((y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q)) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q:
        raise CredentialsError("invalid Ed25519 point")
    return x


def _point_add(first: _Point, second: _Point) -> _Point:
    x1, y1, z1, t1 = first
    x2, y2, z2, t2 = second
    a = ((y1 - x1) * (y2 - x2)) % _Q
    b = ((y1 + x1) * (y2 + x2)) % _Q
    c = (2 * _D * t1 * t2) % _Q
    d = (2 * z1 * z2) % _Q
    e = (b - a) % _Q
    f = (d - c) % _Q
    g = (d + c) % _Q
    h = (b + a) % _Q
    return ((e * f) % _Q, (g * h) % _Q, (f * g) % _Q, (e * h) % _Q)


def _point_double(point: _Point) -> _Point:
    return _point_add(point, point)


def _scalar_mult(scalar: int, point: _Point) -> _Point:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        scalar >>= 1
    return result


def _point_equal(first: _Point, second: _Point) -> bool:
    x1, y1, z1, _ = first
    x2, y2, z2, _ = second
    return (x1 * z2 - x2 * z1) % _Q == 0 and (y1 * z2 - y2 * z1) % _Q == 0


def _encode_point(point: _Point) -> bytes:
    x, y, z, _ = point
    inverse_z = pow(z, _Q - 2, _Q)
    affine_x = (x * inverse_z) % _Q
    affine_y = (y * inverse_z) % _Q
    return (affine_y | ((affine_x & 1) << 255)).to_bytes(32, "little")


def _decode_point(data: bytes) -> _Point | None:
    if len(data) != 32:
        return None
    encoded = int.from_bytes(data, "little")
    sign = encoded >> 255
    y = encoded & ((1 << 255) - 1)
    if y >= _Q:
        return None
    try:
        x = _x_recover(y)
    except CredentialsError:
        return None
    if (x & 1) != sign:
        x = _Q - x
    if x == 0 and sign:
        return None
    return (x, y, 1, (x * y) % _Q)


_BASE_Y = (4 * pow(5, _Q - 2, _Q)) % _Q
_BASE_X = _x_recover(_BASE_Y)
if _BASE_X & 1:
    _BASE_X = _Q - _BASE_X
_BASE_POINT: _Point = (_BASE_X, _BASE_Y, 1, (_BASE_X * _BASE_Y) % _Q)


def _is_small_order(point: _Point) -> bool:
    return _point_equal(_scalar_mult(8, point), _IDENTITY)


def _private_public_bytes(seed: bytes) -> bytes:
    digest = hashlib.sha512(seed).digest()
    scalar_bytes = bytearray(digest[:32])
    scalar_bytes[0] &= 248
    scalar_bytes[31] &= 63
    scalar_bytes[31] |= 64
    scalar = int.from_bytes(scalar_bytes, "little")
    return _encode_point(_scalar_mult(scalar, _BASE_POINT))


def _ed25519_sign(seed: bytes, message: bytes) -> bytes:
    digest = hashlib.sha512(seed).digest()
    scalar_bytes = bytearray(digest[:32])
    scalar_bytes[0] &= 248
    scalar_bytes[31] &= 63
    scalar_bytes[31] |= 64
    scalar = int.from_bytes(scalar_bytes, "little")
    public = _encode_point(_scalar_mult(scalar, _BASE_POINT))
    nonce = int.from_bytes(
        hashlib.sha512(digest[32:] + message).digest(), "little"
    ) % _L
    encoded_r = _encode_point(_scalar_mult(nonce, _BASE_POINT))
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public + message).digest(), "little"
    ) % _L
    response = (nonce + challenge * scalar) % _L
    return encoded_r + response.to_bytes(32, "little")


def _ed25519_verify(public: bytes, signature: bytes, message: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    encoded_r = signature[:32]
    response = int.from_bytes(signature[32:], "little")
    if response >= _L:
        return False
    point_a = _decode_point(public)
    point_r = _decode_point(encoded_r)
    if point_a is None or point_r is None:
        return False
    # Reject torsion points.  This also prevents the small-order public-key and
    # R encodings that can otherwise make an Ed25519 verifier accept surprises.
    if _is_small_order(point_a) or _is_small_order(point_r):
        return False
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public + message).digest(), "little"
    ) % _L
    left = _scalar_mult(response, _BASE_POINT)
    right = _point_add(point_r, _scalar_mult(challenge, point_a))
    return _point_equal(left, right)


@dataclass(frozen=True, slots=True)
class Ed25519PublicKey:
    """An Ed25519 public key represented by its 32 raw bytes."""

    _raw: bytes

    def __post_init__(self) -> None:
        raw = bytes(self._raw)
        if len(raw) != 32 or _decode_point(raw) is None:
            raise CredentialsError("Ed25519 public key must be a valid 32-byte point")
        object.__setattr__(self, "_raw", raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Ed25519PublicKey":
        return cls(raw)

    @classmethod
    def from_serialized(cls, value: str) -> "Ed25519PublicKey":
        return cls(_base64_decode(value, "public key"))

    def to_bytes(self) -> bytes:
        return self._raw

    serialize = to_bytes

    def to_base64(self) -> str:
        return _base64_encode(self._raw)

    @property
    def key_id(self) -> str:
        return public_key_id(self)

    def verify(self, signature: bytes, message: bytes) -> bool:
        return _ed25519_verify(self._raw, bytes(signature), bytes(message))


@dataclass(frozen=True, slots=True)
class Ed25519PrivateKey:
    """An Ed25519 private signing key backed by a 32-byte seed."""

    seed: bytes

    def __post_init__(self) -> None:
        seed = bytes(self.seed)
        if len(seed) != 32:
            raise CredentialsError("Ed25519 seed must be exactly 32 bytes")
        object.__setattr__(self, "seed", seed)

    @classmethod
    def from_seed(cls, seed: bytes) -> "Ed25519PrivateKey":
        return cls(seed)

    @property
    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey(_private_public_bytes(self.seed))

    @property
    def issuer_key_id(self) -> str:
        return self.public_key.key_id

    def sign(self, message: bytes) -> bytes:
        return _ed25519_sign(self.seed, bytes(message))


SigningKey = Ed25519PrivateKey


@dataclass(frozen=True, slots=True)
class KeyPair:
    """A generated signing/public key pair, iterable for tuple-style callers."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey

    @property
    def signing_key(self) -> Ed25519PrivateKey:
        return self.private_key

    def __iter__(self) -> Iterator[Ed25519PrivateKey | Ed25519PublicKey]:
        yield self.private_key
        yield self.public_key


def _secret_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        value = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        value = secret
    else:
        raise TypeError("organizer_secret must be text or bytes")
    if not value:
        raise CredentialsError("organizer_secret cannot be empty")
    return value


def derive_signing_key(organizer_secret: str | bytes) -> Ed25519PrivateKey:
    """Derive the same signing key from the same organizer secret every time."""

    seed = hashlib.sha256(_SEED_DOMAIN + _secret_bytes(organizer_secret)).digest()
    return Ed25519PrivateKey(seed)


def generate_signing_key(
    seed: bytes | None = None,
    *,
    organizer_secret: str | bytes | None = None,
) -> Ed25519PrivateKey:
    """Create a key from an explicit seed, organizer secret, or OS randomness."""

    if seed is not None and organizer_secret is not None:
        raise CredentialsError("pass seed or organizer_secret, not both")
    if seed is not None:
        return Ed25519PrivateKey.from_seed(seed)
    if organizer_secret is not None:
        return derive_signing_key(organizer_secret)
    return Ed25519PrivateKey.from_seed(secrets.token_bytes(32))


def generate_keypair(
    seed: bytes | None = None,
    *,
    organizer_secret: str | bytes | None = None,
) -> KeyPair:
    """Return a deterministic or randomly generated Ed25519 key pair."""

    private_key = generate_signing_key(seed, organizer_secret=organizer_secret)
    return KeyPair(private_key, private_key.public_key)


def _private_key(value: Ed25519PrivateKey | bytes) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    if isinstance(value, bytes):
        return Ed25519PrivateKey.from_seed(value)
    raise TypeError("signing key must be an Ed25519PrivateKey")


def _public_key(value: Ed25519PublicKey | Ed25519PrivateKey | bytes | str) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if isinstance(value, Ed25519PrivateKey):
        return value.public_key
    if isinstance(value, bytes):
        return Ed25519PublicKey.from_bytes(value)
    if isinstance(value, str):
        return Ed25519PublicKey.from_serialized(value)
    raise TypeError("public key must be an Ed25519PublicKey or serialized key")


def public_key_bytes(
    key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str,
) -> bytes:
    """Return raw 32-byte public-key serialization."""

    return _public_key(key).to_bytes()


def serialize_public_key(
    key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str,
) -> str:
    """Return URL-safe base64 public-key serialization for JSON documents."""

    return _public_key(key).to_base64()


def deserialize_public_key(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_serialized(value)


def public_key_id(
    key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str,
) -> str:
    """Return the stable identifier carried by signed public documents."""

    raw = _public_key(key).to_bytes()
    return _KEY_ID_PREFIX + hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Canonical document primitives
# ---------------------------------------------------------------------------


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CredentialsError(f"{field} must be text")
    cleaned = value.strip()
    if not cleaned:
        raise CredentialsError(f"{field} cannot be empty")
    return cleaned


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CredentialsError("version must be a positive integer")
    return value


def _timestamp(value: Any, field: str) -> str | int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise CredentialsError(f"{field} must be an ISO timestamp or Unix number")
    if isinstance(value, str):
        value = _required_text(value, field)
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CredentialsError(f"{field} must be a valid ISO timestamp") from exc
    return value


def _base64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _base64_decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise CredentialsError(f"{field} must be base64 text")
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError, base64.binascii.Error) as exc:
        raise CredentialsError(f"{field} is not valid base64") from exc
    if _base64_encode(raw) != value:
        raise CredentialsError(f"{field} is not canonical base64")
    return raw


def _canonical_json(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CredentialsError("document cannot be serialized as canonical JSON") from exc
    return rendered.encode("utf-8")


def _signed_payload(kind: str, payload: Mapping[str, Any]) -> bytes:
    return _SIGNATURE_DOMAIN + kind.encode("ascii") + b"\x00" + _canonical_json(payload)


def _signature_text(signature: Any) -> str:
    text = _required_text(signature, "signature")
    raw = _base64_decode(text, "signature")
    if len(raw) != 64:
        raise CredentialsError("signature must contain 64 bytes")
    return text


def _check_keys(data: Mapping[str, Any], allowed: set[str], required: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise CredentialsError("document must be an object")
    unknown = set(data) - allowed
    missing = required - set(data)
    if unknown:
        raise CredentialsError("document contains unknown field(s)")
    if missing:
        raise CredentialsError("document is missing required field(s)")


def _signature_bytes(value: str) -> bytes:
    return _base64_decode(value, "signature")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Credential:
    """A signed, public credential with no private recipient email."""

    event_id: str
    code: str
    role: str
    issuer_key_id: str
    holder: str | None = None
    issued_at: str | int | float | None = None
    expiry: str | int | float | None = None
    schema: str = CREDENTIAL_SCHEMA
    version: int = 1
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "code", _required_text(self.code, "code"))
        object.__setattr__(self, "role", _required_text(self.role, "role"))
        object.__setattr__(self, "issuer_key_id", _required_text(self.issuer_key_id, "issuer_key_id"))
        object.__setattr__(self, "holder", _optional_text(self.holder, "holder"))
        object.__setattr__(self, "issued_at", _timestamp(self.issued_at, "issued_at"))
        object.__setattr__(self, "expiry", _timestamp(self.expiry, "expiry"))
        object.__setattr__(self, "schema", _required_text(self.schema, "schema"))
        object.__setattr__(self, "version", _version(self.version))
        if self.signature:
            object.__setattr__(self, "signature", _signature_text(self.signature))

    @property
    def name(self) -> str | None:
        """Alias for callers that use ``name`` rather than ``holder``."""

        return self.holder

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "event_id": self.event_id,
            "issuer_key_id": self.issuer_key_id,
            "role": self.role,
            "schema": self.schema,
            "version": self.version,
        }
        if self.holder is not None:
            result["holder"] = self.holder
        if self.issued_at is not None:
            result["issued_at"] = self.issued_at
        if self.expiry is not None:
            result["expiry"] = self.expiry
        return result

    def as_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["signature"] = self.signature
        return result

    def to_json(self) -> str:
        return _canonical_json(self.as_dict()).decode("utf-8")

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


def issue_credential(
    event_id: str,
    code: str,
    role: str,
    signing_key: Ed25519PrivateKey | bytes | None = None,
    *,
    holder: str | None = None,
    name: str | None = None,
    issued_at: str | int | float | None = None,
    expiry: str | int | float | None = None,
    schema: str = CREDENTIAL_SCHEMA,
    version: int = 1,
    organizer_secret: str | bytes | None = None,
    seed: bytes | None = None,
) -> Credential:
    """Issue a signed credential using a key, organizer secret, or seed.

    ``name`` is an API alias for ``holder``.  The canonical public field is
    ``holder``.  No email argument exists because email is private input and
    must remain outside a public signed document.
    """

    if holder is not None and name is not None and holder != name:
        raise CredentialsError("holder and name must match when both are supplied")
    selected_holder = holder if holder is not None else name
    if signing_key is not None and (organizer_secret is not None or seed is not None):
        raise CredentialsError("pass signing_key or organizer_secret/seed, not both")
    if signing_key is None:
        signing_key = generate_signing_key(seed, organizer_secret=organizer_secret)
    private_key = _private_key(signing_key)
    unsigned = Credential(
        event_id=event_id,
        code=code,
        role=role,
        issuer_key_id=private_key.issuer_key_id,
        holder=selected_holder,
        issued_at=issued_at,
        expiry=expiry,
        schema=schema,
        version=version,
    )
    signature = _base64_encode(private_key.sign(_signed_payload("credential", unsigned.payload())))
    return Credential(**{**unsigned.as_dict(), "signature": signature})


def credential_to_dict(value: Credential | Mapping[str, Any]) -> dict[str, Any]:
    return _as_credential(value).as_dict()


def credential_from_dict(data: Mapping[str, Any]) -> Credential:
    allowed = {
        "code",
        "event_id",
        "expiry",
        "holder",
        "issued_at",
        "issuer_key_id",
        "role",
        "schema",
        "signature",
        "version",
    }
    _check_keys(data, allowed, allowed - {"expiry", "holder", "issued_at"})
    return Credential(
        event_id=data["event_id"],
        code=data["code"],
        role=data["role"],
        issuer_key_id=data["issuer_key_id"],
        holder=data.get("holder"),
        issued_at=data.get("issued_at"),
        expiry=data.get("expiry"),
        schema=data["schema"],
        version=data["version"],
        signature=_signature_text(data["signature"]),
    )


def _as_credential(value: Credential | Mapping[str, Any]) -> Credential:
    if isinstance(value, Credential):
        return value
    return credential_from_dict(value)


def credential_to_json(value: Credential | Mapping[str, Any]) -> str:
    return _as_credential(value).to_json()


def credential_from_json(data: str | bytes) -> Credential:
    try:
        value = json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CredentialsError("credential JSON is invalid") from exc
    return credential_from_dict(value)


def credential_to_bytes(value: Credential | Mapping[str, Any]) -> bytes:
    return credential_to_json(value).encode("utf-8")


def _credential_expired(credential: Credential, now: datetime | None) -> bool:
    if credential.expiry is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        if isinstance(credential.expiry, (int, float)):
            expiry_at = datetime.fromtimestamp(credential.expiry, timezone.utc)
        else:
            expiry_at = datetime.fromisoformat(credential.expiry.replace("Z", "+00:00"))
            if expiry_at.tzinfo is None:
                expiry_at = expiry_at.replace(tzinfo=timezone.utc)
            expiry_at = expiry_at.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return True
    return now.astimezone(timezone.utc) >= expiry_at


def verify_credential(
    value: Credential | Mapping[str, Any],
    public_key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str,
    *,
    event_id: str | None = None,
    revocations: Iterable[Any] = (),
    now: datetime | None = None,
) -> bool:
    """Return whether a credential is authentic, for this event, and active."""

    try:
        credential = _as_credential(value)
        key = _public_key(public_key)
        if credential.issuer_key_id != key.key_id:
            return False
        if event_id is not None and credential.event_id != event_id:
            return False
        if _credential_expired(credential, now):
            return False
        if is_revoked(credential, revocations, public_key=key, event_id=event_id):
            return False
        return key.verify(
            _signature_bytes(credential.signature),
            _signed_payload("credential", credential.payload()),
        )
    except (CredentialsError, TypeError, ValueError, OverflowError):
        return False


# ---------------------------------------------------------------------------
# Signed revocations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Revocation:
    """A signed event-scoped revocation for a credential code."""

    event_id: str
    code: str
    issuer_key_id: str
    revoked_at: str | int | float | None = None
    reason: str | None = None
    schema: str = REVOCATION_SCHEMA
    version: int = 1
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "code", _required_text(self.code, "code"))
        object.__setattr__(self, "issuer_key_id", _required_text(self.issuer_key_id, "issuer_key_id"))
        object.__setattr__(self, "revoked_at", _timestamp(self.revoked_at, "revoked_at"))
        object.__setattr__(self, "reason", _optional_text(self.reason, "reason"))
        object.__setattr__(self, "schema", _required_text(self.schema, "schema"))
        object.__setattr__(self, "version", _version(self.version))
        if self.signature:
            object.__setattr__(self, "signature", _signature_text(self.signature))

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "event_id": self.event_id,
            "issuer_key_id": self.issuer_key_id,
            "schema": self.schema,
            "version": self.version,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        if self.revoked_at is not None:
            result["revoked_at"] = self.revoked_at
        return result

    def as_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["signature"] = self.signature
        return result

    def to_json(self) -> str:
        return _canonical_json(self.as_dict()).decode("utf-8")

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


def issue_revocation(
    event_id: str,
    code: str,
    signing_key: Ed25519PrivateKey | bytes | None = None,
    *,
    revoked_at: str | int | float | None = None,
    reason: str | None = None,
    schema: str = REVOCATION_SCHEMA,
    version: int = 1,
    organizer_secret: str | bytes | None = None,
    seed: bytes | None = None,
) -> Revocation:
    """Issue a signed revocation for one event and credential code."""

    if signing_key is not None and (organizer_secret is not None or seed is not None):
        raise CredentialsError("pass signing_key or organizer_secret/seed, not both")
    if signing_key is None:
        signing_key = generate_signing_key(seed, organizer_secret=organizer_secret)
    private_key = _private_key(signing_key)
    unsigned = Revocation(
        event_id=event_id,
        code=code,
        issuer_key_id=private_key.issuer_key_id,
        revoked_at=revoked_at,
        reason=reason,
        schema=schema,
        version=version,
    )
    signature = _base64_encode(private_key.sign(_signed_payload("revocation", unsigned.payload())))
    return Revocation(**{**unsigned.as_dict(), "signature": signature})


def revocation_to_dict(value: Revocation | Mapping[str, Any]) -> dict[str, Any]:
    return _as_revocation(value).as_dict()


def revocation_from_dict(data: Mapping[str, Any]) -> Revocation:
    allowed = {
        "code",
        "event_id",
        "issuer_key_id",
        "reason",
        "revoked_at",
        "schema",
        "signature",
        "version",
    }
    _check_keys(data, allowed, allowed - {"reason", "revoked_at"})
    return Revocation(
        event_id=data["event_id"],
        code=data["code"],
        issuer_key_id=data["issuer_key_id"],
        revoked_at=data.get("revoked_at"),
        reason=data.get("reason"),
        schema=data["schema"],
        version=data["version"],
        signature=_signature_text(data["signature"]),
    )


def _as_revocation(value: Revocation | Mapping[str, Any]) -> Revocation:
    if isinstance(value, Revocation):
        return value
    return revocation_from_dict(value)


def revocation_to_json(value: Revocation | Mapping[str, Any]) -> str:
    return _as_revocation(value).to_json()


def revocation_from_json(data: str | bytes) -> Revocation:
    try:
        value = json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CredentialsError("revocation JSON is invalid") from exc
    return revocation_from_dict(value)


def revocation_to_bytes(value: Revocation | Mapping[str, Any]) -> bytes:
    return revocation_to_json(value).encode("utf-8")


def verify_revocation(
    value: Revocation | Mapping[str, Any],
    public_key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str,
    *,
    event_id: str | None = None,
) -> bool:
    try:
        revocation = _as_revocation(value)
        key = _public_key(public_key)
        if revocation.issuer_key_id != key.key_id:
            return False
        if event_id is not None and revocation.event_id != event_id:
            return False
        return key.verify(
            _signature_bytes(revocation.signature),
            _signed_payload("revocation", revocation.payload()),
        )
    except (CredentialsError, TypeError, ValueError):
        return False


def is_revoked(
    credential_or_code: Credential | Mapping[str, Any] | str,
    revocations: Iterable[Any],
    *,
    public_key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str | None = None,
    event_id: str | None = None,
) -> bool:
    """Check a code against plain codes or signed revocation records."""

    try:
        code = (
            credential_or_code
            if isinstance(credential_or_code, str)
            else _as_credential(credential_or_code).code
        )
    except (CredentialsError, TypeError, ValueError):
        return False
    for item in revocations:
        if isinstance(item, str):
            if hmac.compare_digest(item, code):
                return True
            continue
        try:
            revocation = _as_revocation(item)
        except (CredentialsError, TypeError, ValueError):
            continue
        if revocation.code != code:
            continue
        if public_key is None:
            return True
        if verify_revocation(revocation, public_key, event_id=event_id):
            return True
    return False


# ---------------------------------------------------------------------------
# Signed public manifest/package
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignedManifest:
    """A signed public package containing credentials and revocations."""

    event_id: str
    issuer_key_id: str
    public_key: str
    credentials: tuple[Credential, ...]
    revocations: tuple[Revocation, ...] = ()
    schema: str = MANIFEST_SCHEMA
    version: int = 1
    signature: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "issuer_key_id", _required_text(self.issuer_key_id, "issuer_key_id"))
        key = _public_key(self.public_key)
        object.__setattr__(self, "public_key", key.to_base64())
        if self.issuer_key_id != key.key_id:
            raise CredentialsError("issuer_key_id does not match public_key")
        credential_values = tuple(self.credentials)
        revocation_values = tuple(self.revocations)
        if not all(isinstance(item, Credential) for item in credential_values):
            raise CredentialsError("credentials must contain Credential objects")
        if not all(isinstance(item, Revocation) for item in revocation_values):
            raise CredentialsError("revocations must contain Revocation objects")
        object.__setattr__(self, "credentials", credential_values)
        object.__setattr__(self, "revocations", revocation_values)
        object.__setattr__(self, "schema", _required_text(self.schema, "schema"))
        object.__setattr__(self, "version", _version(self.version))
        if self.signature:
            object.__setattr__(self, "signature", _signature_text(self.signature))

    @property
    def package(self) -> "SignedManifest":
        return self

    def payload(self) -> dict[str, Any]:
        return {
            "credentials": [item.as_dict() for item in self.credentials],
            "event_id": self.event_id,
            "issuer_key_id": self.issuer_key_id,
            "public_key": self.public_key,
            "revocations": [item.as_dict() for item in self.revocations],
            "schema": self.schema,
            "version": self.version,
        }

    def as_dict(self) -> dict[str, Any]:
        result = self.payload()
        result["signature"] = self.signature
        return result

    def to_json(self) -> str:
        return _canonical_json(self.as_dict()).decode("utf-8")

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")


SignedPackage = SignedManifest
CredentialPackage = SignedManifest
Manifest = SignedManifest


def sign_manifest(
    event_id: str,
    credentials: Iterable[Credential | Mapping[str, Any]],
    signing_key: Ed25519PrivateKey | bytes,
    *,
    revocations: Iterable[Revocation | Mapping[str, Any]] = (),
    schema: str = MANIFEST_SCHEMA,
    version: int = 1,
) -> SignedManifest:
    """Create a signed public manifest/package from signed records."""

    private_key = _private_key(signing_key)
    key = private_key.public_key
    document_credentials = tuple(_as_credential(item) for item in credentials)
    document_revocations = tuple(_as_revocation(item) for item in revocations)
    event = _required_text(event_id, "event_id")
    for credential in document_credentials:
        if credential.event_id != event or credential.issuer_key_id != key.key_id:
            raise CredentialsError("credential does not belong to this event and issuer")
    for revocation in document_revocations:
        if revocation.event_id != event or revocation.issuer_key_id != key.key_id:
            raise CredentialsError("revocation does not belong to this event and issuer")
    unsigned = SignedManifest(
        event_id=event,
        issuer_key_id=key.key_id,
        public_key=key.to_base64(),
        credentials=document_credentials,
        revocations=document_revocations,
        schema=schema,
        version=version,
    )
    signature = _base64_encode(private_key.sign(_signed_payload("manifest", unsigned.payload())))
    return SignedManifest(
        event_id=unsigned.event_id,
        issuer_key_id=unsigned.issuer_key_id,
        public_key=unsigned.public_key,
        credentials=unsigned.credentials,
        revocations=unsigned.revocations,
        schema=unsigned.schema,
        version=unsigned.version,
        signature=signature,
    )


def manifest_to_dict(value: SignedManifest | Mapping[str, Any]) -> dict[str, Any]:
    return _as_manifest(value).as_dict()


def manifest_from_dict(data: Mapping[str, Any]) -> SignedManifest:
    allowed = {
        "credentials",
        "event_id",
        "issuer_key_id",
        "public_key",
        "revocations",
        "schema",
        "signature",
        "version",
    }
    _check_keys(data, allowed, allowed - {"revocations"})
    raw_credentials = data["credentials"]
    raw_revocations = data.get("revocations", [])
    if not isinstance(raw_credentials, list) or not isinstance(raw_revocations, list):
        raise CredentialsError("manifest records must be lists")
    return SignedManifest(
        event_id=data["event_id"],
        issuer_key_id=data["issuer_key_id"],
        public_key=data["public_key"],
        credentials=tuple(credential_from_dict(item) for item in raw_credentials),
        revocations=tuple(revocation_from_dict(item) for item in raw_revocations),
        schema=data["schema"],
        version=data["version"],
        signature=_signature_text(data["signature"]),
    )


def _as_manifest(value: SignedManifest | Mapping[str, Any]) -> SignedManifest:
    if isinstance(value, SignedManifest):
        return value
    return manifest_from_dict(value)


def manifest_to_json(value: SignedManifest | Mapping[str, Any]) -> str:
    return _as_manifest(value).to_json()


def manifest_from_json(data: str | bytes) -> SignedManifest:
    try:
        value = json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CredentialsError("manifest JSON is invalid") from exc
    return manifest_from_dict(value)


def manifest_to_bytes(value: SignedManifest | Mapping[str, Any]) -> bytes:
    return manifest_to_json(value).encode("utf-8")


def verify_manifest(
    value: SignedManifest | Mapping[str, Any],
    *,
    event_id: str | None = None,
    public_key: Ed25519PublicKey | Ed25519PrivateKey | bytes | str | None = None,
    now: datetime | None = None,
) -> bool:
    """Return whether every package layer is authentic and internally valid."""

    try:
        manifest = _as_manifest(value)
        embedded_key = _public_key(manifest.public_key)
        if public_key is not None and not hmac.compare_digest(
            embedded_key.to_bytes(), _public_key(public_key).to_bytes()
        ):
            return False
        if event_id is not None and manifest.event_id != event_id:
            return False
        if not embedded_key.verify(
            _signature_bytes(manifest.signature),
            _signed_payload("manifest", manifest.payload()),
        ):
            return False
        for revocation in manifest.revocations:
            if not verify_revocation(revocation, embedded_key, event_id=manifest.event_id):
                return False
        seen_codes: set[str] = set()
        for credential in manifest.credentials:
            if credential.code in seen_codes:
                return False
            seen_codes.add(credential.code)
            if not verify_credential(
                credential,
                embedded_key,
                event_id=manifest.event_id,
                # Package integrity is independent from credential activity.
                # A signed package may legitimately retain a historical
                # credential alongside its signed revocation. Callers that
                # verify a credential pass the revocations separately and get
                # INVALID for the revoked code.
                revocations=(),
                now=now,
            ):
                return False
        return True
    except (CredentialsError, TypeError, ValueError, OverflowError):
        return False


def sign_package(*args: Any, **kwargs: Any) -> SignedManifest:
    return sign_manifest(*args, **kwargs)


def verify_package(*args: Any, **kwargs: Any) -> bool:
    return verify_manifest(*args, **kwargs)


def package_to_json(value: SignedManifest | Mapping[str, Any]) -> str:
    return manifest_to_json(value)


def package_from_json(data: str | bytes) -> SignedManifest:
    return manifest_from_json(data)
