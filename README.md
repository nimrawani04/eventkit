# eventkit

eventkit is an offline, deterministic command-line tool for turning messy
event registration exports into an explained roster. The product requirements
are in [`eventkit Product Requirements Document.md`](eventkit%20Product%20Requirements%20Document.md).

## Current build slice

The first buildable slice implements the PRD's roster milestone:

- BOM-safe CSV reading with physical line numbers and quoted multiline fields;
- NFC/whitespace name normalization, email comparison normalization, and phone normalization;
- EK01-EK06 validation, including exact duplicates, possible duplicates, phone warnings, and common email-domain suggestions;
- deterministic `roster.csv`, `issues.md`, `report.md`, and `audit.jsonl` output;
- `init`, `run`, `--dry-run`, `--strict`, and `--version`;
- safe output handling that keeps files edited outside eventkit's audit record.
- deterministic attendance-filtered certificate SVGs, a privacy-preserving
  `manifest.json`, and offline `verify`.

Schedule, email drafts, and `clean` are planned PRD slices and are intentionally
not claimed as implemented yet.

## Requirements

- Python 3.12 (pinned in `.python-version`);
- runtime dependencies: none;
- pytest is the pinned development dependency in `requirements-dev.txt`.

## Setup

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

On Windows, use the equivalent `.venv\Scripts\python` paths. Do not put a real
`EVENTKIT_SECRET` in the repository; `.env.example` is only a placeholder. The
secret must be supplied in the environment and must be at least 16 characters.

## Quickstart

Activate the project environment first so the commands use the pinned local
tools:

```sh
. .venv/bin/activate
eventkit init demo
eventkit run demo/event.toml --dry-run
eventkit run demo/event.toml
cat demo/kit/issues.md
```

`init` writes deliberate data problems. The dry run prints create/update/keep
actions and writes nothing. Add `--strict` when an error-level issue should
return exit code 1. Configuration and input errors return exit code 2. The
normal run keeps all source rows in `roster.csv`, marking excluded rows and
listing each issue with its source row number in `issues.md`.

The output writer records content hashes in `audit.jsonl`. A rerun with
unchanged inputs is deterministic; a file changed by an organizer is kept
instead of being overwritten.

## Certificates and offline verification

Enable the certificate step in `event.toml`:

```toml
[input]
registrations = "data/registrations.csv"
attendance = "data/attendance.csv"

[certificates]
title = "Certificate of Participation"
issue_to = "attended"
manifest_names = true

[output]
dir = "kit"
steps = ["roster", "certificates"]
```

The attendance CSV requires `email` and may include `role`; missing roles use
`Participant`. Unknown attendance emails and emails matching excluded roster
rows receive EK07 and do not get certificates. When `issue_to = "attended"`
and no attendance file is configured, the certificate step is skipped and the
reason is recorded in `report.md`.

Run with a secret supplied only by the environment:

```sh
export EVENTKIT_SECRET='replace-with-a-local-secret-at-least-16-chars'
eventkit run event.toml
eventkit verify ABCD-1234-EFGH --manifest kit/manifest.json
```

Certificates are written under `kit/certificates/` as deterministic A4
landscape SVG files. The manifest stores event metadata, roles, optional names,
and SHA-256 hashes of canonical codes; it never stores email addresses. A
`VALID` result proves only that the code is present in the manifest supplied to
`verify`. Recipients must obtain that manifest from an organizer or another
source they trust.

## Tests

Run the single project check from the repository root:

```sh
python -m pytest -q
```

or:

```sh
make test
```

The test suite is offline at runtime and uses only the local source tree.
`tests/conftest.py` blocks sockets and subprocesses and includes a canary test
so an accidental network or process dependency fails loudly.

## Sample output

A dry run on the included sample reports the deliberate input problems before
writing anything:

```text
Dry run: 8 rows, 5 included, 3 excluded, 3 errors, 4 warnings
create    roster.csv
create    issues.md
create    report.md
create    audit.jsonl
No files written.
```

The trust model for the planned certificate step is intentionally limited: a
future `VALID` result will prove only that a code appears in an organizer's
published manifest. The recipient must obtain that manifest from a source they
trust; eventkit will not provide a hosted trust service.

## Docker

```sh
docker build -t eventkit .
docker run --rm --network none eventkit python -m pytest -q
docker run --rm --network none eventkit eventkit --version
```

The image builds with Python 3.12, installs the pinned test tools, and has no
network requirement when the commands above run.

## Deployment / release

The current slice is a local CLI rather than a hosted service. For a
reproducible release or demo, build the image and run it with a mounted event
directory:

```sh
docker build -t eventkit .
docker run --rm --network none \
  -v "$PWD/demo:/app/demo" \
  eventkit eventkit run demo/event.toml
```

The generated kit remains in the mounted directory. Future certificate
manifests should be published through an organizer-controlled channel; eventkit
itself makes no network calls and does not send email.

## Development notes

All runtime code uses the Python standard library. Outputs are derived from
input bytes and stable row order; no clock, randomness, network, or AI call is
used by the current slice. Keep the PRD's cut order when adding the next
feature: codes/certificates and verification are higher priority than polish.
