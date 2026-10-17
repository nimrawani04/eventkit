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

Schedule, certificates, email drafts, offline verification, and `clean` are
planned PRD slices and are intentionally not claimed as implemented yet.

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
`EVENTKIT_SECRET` in the repository; `.env.example` is only a placeholder for
the later certificate step.

## Quickstart

```sh
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

## Docker

```sh
docker build -t eventkit .
docker run --rm --network none eventkit python -m pytest -q
docker run --rm --network none eventkit eventkit --version
```

The image builds with Python 3.12, installs the pinned test tools, and has no
network requirement when the commands above run.

## Development notes

All runtime code uses the Python standard library. Outputs are derived from
input bytes and stable row order; no clock, randomness, network, or AI call is
used by the current slice. Keep the PRD's cut order when adding the next
feature: codes/certificates and verification are higher priority than polish.
