"""Focused tests for deterministic eventkit codes and manifests."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from eventkit.codes import (
    CODE_SEPARATOR,
    CROCKFORD_ALPHABET,
    CertificateRecipient,
    SecretError,
    canonical_code_sha256,
    canonicalize_code,
    create_manifest,
    create_manifest_from_recipients,
    generate_certificate_code,
    generate_checkin_codes,
    lookup_code,
    manifest_from_json,
    manifest_to_json,
    secret_from_environment,
    validate_secret,
    verify,
    verify_code,
)


SECRET = "eventkit-test-secret"


def _expected_crockford_prefix(digest: bytes, length: int) -> str:
    value = int.from_bytes(digest, "big")
    bits = len(digest) * 8
    return "".join(
        CROCKFORD_ALPHABET[(value >> (bits - (index + 1) * 5)) & 0x1F]
        for index in range(length)
    )


def test_certificate_code_is_deterministic_and_uses_first_60_hmac_bits():
    event_id = "campus-2026"
    email = "  Ada.Example@Example.COM "
    role = "speaker"
    first = generate_certificate_code(event_id, email, role, SECRET)
    second = generate_certificate_code(event_id, "ada.example@example.com", role, SECRET)

    assert first == second
    assert first.count("-") == 2
    assert tuple(len(part) for part in first.split("-")) == (4, 4, 4)

    payload = CODE_SEPARATOR.join(
        ("cert", event_id, "ada.example@example.com", role)
    ).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    assert canonicalize_code(first) == _expected_crockford_prefix(digest, 12)


def test_secret_validation_has_no_default():
    with pytest.raises(SecretError):
        validate_secret(None)
    with pytest.raises(SecretError):
        validate_secret("too-short")
    with pytest.raises(SecretError):
        secret_from_environment({})

    assert validate_secret(SECRET) == SECRET
    assert secret_from_environment({"EVENTKIT_SECRET": SECRET}) == SECRET


def test_canonicalization_handles_case_formatting_and_ambiguous_characters():
    assert canonicalize_code("  oIl-1  ") == "0111"
    assert canonicalize_code("abcd efgh-jkmn") == "ABCDEFGHJKMN"


def test_checkin_codes_are_unique_and_lengthen_on_collision():
    recipient = CertificateRecipient("person@example.com", "attendee")
    codes = generate_checkin_codes("campus-2026", [recipient, recipient, recipient], SECRET)

    assert len(codes) == 3
    assert len(codes[0]) == 6
    assert len(codes[1]) == 7
    assert len(codes[2]) == 8
    assert len(set(codes)) == 3
    assert codes[1].startswith(codes[0])
    assert codes[2].startswith(codes[1])


def test_manifest_is_private_and_json_is_deterministic():
    recipients = [
        CertificateRecipient("ada@example.com", "speaker", "Ada Lovelace"),
        CertificateRecipient("grace@example.com", "attendee", "Grace Hopper"),
    ]
    issued_manifest = create_manifest_from_recipients(
        "campus-2026", "Campus Conference", recipients, SECRET
    )

    document = issued_manifest.as_dict()
    rendered = manifest_to_json(issued_manifest)
    assert document["certificate_count"] == 2
    assert "ada@example.com" not in rendered
    assert "grace@example.com" not in rendered
    assert document["certificates"][0]["name"] == "Ada Lovelace"
    assert manifest_to_json(issued_manifest) == rendered
    assert manifest_from_json(rendered) == issued_manifest

    without_names = create_manifest(
        "campus-2026",
        "Campus Conference",
        [
            {
                "code": generate_certificate_code(
                    "campus-2026", "ada@example.com", "speaker", SECRET
                ),
                "email": "ada@example.com",
                "role": "speaker",
                "name": "Ada Lovelace",
            }
        ],
        include_names=False,
    )
    assert "name" not in without_names.as_dict()["certificates"][0]
    assert "ada@example.com" not in json.dumps(without_names.as_dict())


def test_valid_invalid_and_wrong_event_lookups():
    email = "ada@example.com"
    code = generate_certificate_code("campus-2026", email, "speaker", SECRET)
    manifest = create_manifest(
        "campus-2026",
        "Campus Conference",
        [{"code": code, "role": "speaker", "name": "Ada Lovelace"}],
    )

    assert lookup_code(code.lower().replace("-", " "), manifest).role == "speaker"
    assert verify_code(code, manifest)
    result = verify(code, manifest)
    assert result.valid
    assert result.event_id == "campus-2026"
    assert result.name == "Ada Lovelace"

    canonical = canonicalize_code(code)
    changed = ("0" if canonical[0] != "0" else "1") + canonical[1:]
    assert lookup_code(changed, manifest) is None
    assert not verify_code(changed, manifest)

    wrong_event_code = generate_certificate_code(
        "other-event", email, "speaker", SECRET
    )
    assert lookup_code(wrong_event_code, manifest) is None
    assert not verify_code(wrong_event_code, manifest)
    assert canonical_code_sha256(code) == manifest.certificates[0].code_sha256
