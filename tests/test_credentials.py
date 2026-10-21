"""Focused tests for signed credentials and public packages."""

from __future__ import annotations

import json

from eventkit.credentials import (
    credential_from_json,
    credential_to_bytes,
    credential_to_json,
    derive_signing_key,
    generate_keypair,
    issue_credential,
    issue_revocation,
    manifest_from_json,
    manifest_to_bytes,
    manifest_to_json,
    sign_manifest,
    verify_credential,
    verify_manifest,
    verify_revocation,
)


SECRET = "organizer secret used by tests"
EVENT_ID = "campus-2026"


def _credential():
    return issue_credential(
        EVENT_ID,
        "CERT-0001",
        "speaker",
        derive_signing_key(SECRET),
        holder="Ada Lovelace",
        issued_at="2026-01-01T00:00:00Z",
        expiry="2099-01-01T00:00:00Z",
    )


def test_credential_and_manifest_round_trip():
    key = derive_signing_key(SECRET)
    credential = _credential()
    manifest = sign_manifest(EVENT_ID, [credential], key)

    assert credential_from_json(credential_to_json(credential)) == credential
    assert manifest_from_json(manifest_to_json(manifest)) == manifest
    assert verify_credential(credential, key.public_key, event_id=EVENT_ID)
    assert verify_manifest(manifest, event_id=EVENT_ID)


def test_credential_and_manifest_tampering_fails_verification():
    key = derive_signing_key(SECRET)
    credential = _credential()
    manifest = sign_manifest(EVENT_ID, [credential], key)

    changed_credential = json.loads(credential_to_json(credential))
    changed_credential["role"] = "organizer"
    assert not verify_credential(changed_credential, key.public_key, event_id=EVENT_ID)

    changed_manifest = json.loads(manifest_to_json(manifest))
    changed_manifest["credentials"][0]["holder"] = "Grace Hopper"
    assert not verify_manifest(changed_manifest, event_id=EVENT_ID)

    changed_manifest["unexpected"] = "unsigned data"
    assert not verify_manifest(changed_manifest, event_id=EVENT_ID)


def test_wrong_event_is_not_accepted():
    key = derive_signing_key(SECRET)
    credential = _credential()
    manifest = sign_manifest(EVENT_ID, [credential], key)

    assert not verify_credential(credential, key.public_key, event_id="other-event")
    assert not verify_manifest(manifest, event_id="other-event")


def test_signed_revocation_blocks_credential_and_package():
    key = derive_signing_key(SECRET)
    credential = _credential()
    revocation = issue_revocation(
        EVENT_ID,
        credential.code,
        key,
        revoked_at="2026-02-01T00:00:00Z",
        reason="replacement issued",
    )

    assert verify_revocation(revocation, key.public_key, event_id=EVENT_ID)
    assert not verify_credential(
        credential,
        key.public_key,
        event_id=EVENT_ID,
        revocations=[revocation],
    )
    manifest = sign_manifest(EVENT_ID, [credential], key, revocations=[revocation])
    assert verify_manifest(manifest, event_id=EVENT_ID)


def test_same_seed_and_inputs_produce_identical_bytes():
    first = generate_keypair(seed=b"seed" * 8)
    second = generate_keypair(seed=b"seed" * 8)
    assert first.public_key.to_bytes() == second.public_key.to_bytes()

    first_credential = issue_credential(EVENT_ID, "CERT-0002", "attendee", first.private_key)
    second_credential = issue_credential(EVENT_ID, "CERT-0002", "attendee", second.private_key)
    assert credential_to_bytes(first_credential) == credential_to_bytes(second_credential)

    first_manifest = sign_manifest(EVENT_ID, [first_credential], first.private_key)
    second_manifest = sign_manifest(EVENT_ID, [second_credential], second.private_key)
    assert manifest_to_bytes(first_manifest) == manifest_to_bytes(second_manifest)


def test_public_serialization_never_leaks_private_email():
    key = derive_signing_key(SECRET)
    private_email = "ada.private@example.com"
    credential = issue_credential(
        EVENT_ID,
        "CERT-0003",
        "speaker",
        key,
        name="Ada Lovelace",
    )
    manifest = sign_manifest(EVENT_ID, [credential], key)

    credential_json = credential_to_json(credential)
    manifest_json = manifest_to_json(manifest)
    assert private_email not in credential_json
    assert private_email not in manifest_json
    assert "email" not in credential_json
    assert "email" not in manifest_json
