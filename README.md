# eventkit

**A private, deterministic event-operations kit from ordinary CSV files.**

eventkit turns registration, attendance, and schedule exports into a complete
local event kit: a cleaned roster, explained issues, a printable check-in sheet,
calendar and agenda files, offline-verifiable certificates, signed credentials,
unsent email drafts, an offline event-day check-in app, a human-review ledger,
a risk report, and a polished offline dashboard.

It is deliberately **offline-first**: runtime code uses the Python standard
library, makes no network or AI calls, sends no email, and produces byte-stable
artifacts for the same inputs.

> Product requirements: [`eventkit Product Requirements Document.md`](eventkit%20Product%20Requirements%20Document.md)

## What you get

| Capability | Output or command |
| --- | --- |
| Roster cleaning | `roster.csv`, `issues.md` |
| Attendance matching | EK07/EK08 findings and certificate eligibility |
| Printable check-in | `checkin.html` |
| Schedule validation | SC01, SC02, SC03 findings |
| Calendar and agenda | `schedule.ics`, `agenda.html` |
| Certificates | `certificates/*.svg`, `manifest.json` |
| Offline verification | `eventkit verify`, `verify.html` |
| Signed credentials | `credentials.json`, Ed25519 verification, revocations |
| Email drafts | `emails/*.eml`, `mailmerge.csv` |
| Event-day operations | `checkin-app.html`, `checkin.log` |
| Human review | `review.jsonl`, `review.md`, `eventkit review` |
| Operational safety | dry run, audit log, safe `clean` |
| Change visibility | privacy-safe `eventkit diff` |
| Executive view | `dashboard.html`, `risk.md`, `report.md` |

The generated dashboard is a self-contained, responsive, print-friendly HTML
interface with a restrained navy, ivory, and champagne-gold visual system. It
opens directly from disk and does not need a web server.

## Requirements

- Python **3.12**; the version is pinned in `.python-version` and Docker uses
  `python:3.12-slim`.
- No runtime dependencies beyond the Python standard library.
- `pytest` is the pinned development dependency set in
  `requirements-dev.txt`.
- Docker is optional for local development and required only for container
  rebuild verification.

## Install

### Linux / macOS

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
```

If PowerShell execution policy blocks activation, invoke the environment
directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

The application does not load `.env` files. Keep real secrets out of the
repository; `.env.example` contains only a placeholder.

## Quickstart: roster and schedule

Create a deliberate sample event:

```sh
eventkit init demo
eventkit run demo/event.toml --dry-run
eventkit run demo/event.toml
```

The dry run prints every planned action and writes nothing. Open the result:

```sh
# macOS
open demo/kit/dashboard.html

# Linux
xdg-open demo/kit/dashboard.html

# Windows PowerShell
Start-Process (Resolve-Path demo/kit/dashboard.html)
```

The sample intentionally contains bad registrations and schedule clashes so
the issues, risk report, and dashboard have something useful to show.

## Full demo: certificates and email drafts

Initialize the complete pipeline:

```sh
eventkit init demo-full --full
```

Set a local signing secret with at least 16 characters, then run:

```sh
# Linux / macOS
export EVENTKIT_SECRET='replace-with-a-local-secret-at-least-16-chars'
eventkit run demo-full/event.toml

# Windows PowerShell
$env:EVENTKIT_SECRET = 'replace-with-a-local-secret-at-least-16-chars'
eventkit run demo-full/event.toml
```

The full run produces certificates, the public manifest, an offline verifier,
and unsent drafts. Nothing is delivered to a mail server.

## Command reference

```text
eventkit --help
eventkit --version
eventkit init [DIR] [--full]
eventkit run EVENT_TOML [--dry-run] [--strict] [--out DIR]
eventkit verify CODE --manifest PATH
eventkit clean EVENT_TOML [--dry-run]
eventkit diff EVENT_TOML [--include-names]
eventkit review EVENT_TOML [--public]
eventkit revoke EVENT_TOML CODE --reason TEXT [--out PATH]
```

### `init`

```sh
eventkit init demo-full --full
```

The normal sample creates registrations and schedule data. `--full` also adds
attendance data and enables check-in, certificates, and email drafts.

### `run`

```sh
eventkit run event.toml --dry-run
eventkit run event.toml --strict
eventkit run event.toml --out another-kit
```

- `--dry-run` plans every file operation without writing.
- `--strict` returns `1` when enabled validation produces error-level issues.
- `--out` changes only the output directory; input paths remain relative to
  `event.toml`.

### `verify`

```sh
eventkit verify ABCD-1234-EFGH --manifest kit/manifest.json
```

The command normalizes case, spaces, hyphens, and Crockford ambiguous
characters. A valid result proves only that the code is present in the
manifest supplied to the command. The organizer must publish that manifest
through a channel the recipient trusts.

When `[certificates].signed = true`, point the command at `kit/credentials.json`
instead. The signed package is checked against its embedded Ed25519 public key,
event ID, credential signatures, expiry and signed revocations. The signing key
is derived from `EVENTKIT_SECRET` and is never written to the package.

### `clean`

```sh
eventkit clean event.toml --dry-run
eventkit clean event.toml
```

Only files owned by a valid audit log and unchanged since generation are
removed. Edited, unowned, malformed, and unsafe files are kept and reported.
The audit log is removed last. Cleanup never follows a symlink outside the kit.

### `diff`

```sh
eventkit diff event.toml
eventkit diff event.toml --include-names
```

The preview compares the current registration input with the previous generated
roster. By default it uses stable pseudonymous IDs and never renders email
addresses. `--include-names` is intended for a local organizer preview only.

## Configuration

The configuration is one `event.toml` file. Paths are relative to that file;
absolute paths and path traversal are rejected.

```toml
[event]
id = "demo-summit-2026"
name = "Demo Summit 2026"
organizer = "Campus Tech Club"
utc_offset = "+05:30"

[input]
registrations = "data/registrations.csv"
attendance = "data/attendance.csv"
schedule = "data/schedule.csv"
review = "review.jsonl"

[columns]
name = "Full Name"
email = "Email Address"
phone = "Phone"
institution = "College"

[certificates]
title = "Certificate of Participation"
issue_to = "attended"
manifest_names = true
signed = true
revocations = "data/revocations.json"

[emails]
subject = "Your {event} certificate"
text = "Hello {name},\\n\\nYour code is {code}."
html = "<p>Hello {name}</p><p>Your code is <strong>{code}</strong>.</p>"

[output]
dir = "kit"
profile = "private"
steps = ["roster", "checkin", "checkin-app", "schedule", "certificates", "emails"]
```

Supported output steps are:

```text
roster, checkin, checkin-app, schedule, certificates, emails
```

`emails` requires `certificates` because drafts attach the matching SVG when
one is available.

`checkin-app` creates a self-contained event-day HTML surface. It supports local
search, check-in/undo actions, browser-local persistence and JSONL export. It
does not require a server or network connection.

## Signed credentials and revocations

Enable the signed package with `[certificates].signed = true`. Create a signed
revocation without hand-editing signatures:

```sh
eventkit revoke event.toml ABCD-1234-EFGH --reason "replacement issued"
```

If no `certificates.revocations` path is configured, the command writes
`revocations.json` beside `event.toml`. Rerun the certificate pipeline to
publish the updated package. A revoked credential returns `INVALID`.

## Human review and privacy profiles

The optional `input.review` file is a deterministic JSONL ledger. Each decision
records an issue code, source row, action (`accept`, `reject`, `defer`, or
`replace`), reason and reviewer. Inspect it with:

```sh
eventkit review event.toml
eventkit review event.toml --public
```

Use `output.profile = "public"` for a fail-closed public package. Email and
phone columns are omitted from the roster, email-shaped values are redacted in
reports, private email drafts are refused, and every generated text file is
scanned before writing. The default `private` profile preserves the complete
organizer-facing data.

## Input files

### Registrations

Required logical columns:

- `name`
- `email`

Optional logical columns:

- `phone`
- `institution`

CSV headers can be mapped with `[columns]`. UTF-8 BOMs, quoted commas,
multiline fields, and blank lines are supported. Extra columns are retained in
the issue report as ignored input columns.

### Attendance

Required column:

```csv
email
```

Optional column:

```csv
email,role
ada@example.com,Speaker
```

Missing roles become `Participant`. Unknown or excluded registrations receive
EK07. Repeated attendance rows receive EK08.

### Schedule

Required columns:

```csv
start,end,title,room
2026-05-01 09:00,2026-05-01 10:00,Opening,Hall 1
```

Optional column: `speaker`.

Times are local to `[event].utc_offset` and use `YYYY-MM-DD HH:MM`. Invalid or
backwards sessions receive SC01. Overlapping room sessions receive SC02;
overlapping sessions with the same speaker receive SC03. Touching sessions are
allowed.

## Generated kit

A complete run looks like this:

```text
kit/
├── dashboard.html          # polished offline event interface
├── roster.csv              # every registration with status and issues
├── issues.md               # row-level explanations and suggested fixes
├── checkin.html            # printable included-attendee sheet
├── checkin-app.html        # searchable offline event-day app
├── checkin.log             # importable JSONL event log
├── schedule.ics            # deterministic UTC calendar
├── agenda.html             # grouped local-time agenda
├── certificates/
│   └── *.svg               # deterministic A4 landscape certificates
├── manifest.json            # public certificate hashes; no emails
├── credentials.json         # optional signed credential package
├── verify.html              # browser-based offline verifier
├── emails/
│   └── *.eml               # unsent draft messages
├── mailmerge.csv            # draft metadata only
├── risk.md                 # deterministic operational risk summary
├── report.md               # verified and unresolved run report
├── review.md               # optional human-review resolution report
└── audit.jsonl             # ownership hashes and input digest
```

The dashboard links to the local deliverables and works with a double-click;
no server is required.

## Validation and safety rules

### Roster issues

| Code | Meaning | Effect |
| --- | --- | --- |
| EK01 | name or email missing | row excluded |
| EK02 | invalid email syntax | row excluded |
| EK03 | exact duplicate email | later row excluded |
| EK04 | possible duplicate | warning; row kept |
| EK05 | phone length outside 8–15 digits | warning; row kept |
| EK06 | likely common email-domain typo | warning; never auto-fixed |
| EK07 | attendance email missing or excluded from roster | no certificate |
| EK08 | repeated attendance email | warning; review role/duplicate |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | successful command; valid `verify` |
| `1` | `run --strict` found errors, or `verify` found an invalid code |
| `2` | usage, config, input, or required-secret error |

Expected errors are printed as short messages without tracebacks. Missing
secrets are checked before the writer starts, so failed certificate/check-in
runs do not create a partial kit.

## Determinism, privacy, and trust

- No timestamps, randomness, network calls, subprocesses, or runtime AI calls.
- Stable row order is preserved unless an output explicitly defines a sort.
- Generated files are hash-owned through `audit.jsonl`.
- Edited files are never silently overwritten.
- Output paths are resolved and symlink-checked before writes.
- `manifest.json` stores certificate hashes, roles, and optional names, never
  email addresses.
- `credentials.json` stores signed public credentials and revocations, never
  email addresses. Its Ed25519 signature protects the package against edited
  manifests.
- Public profiles omit email and phone fields and fail closed if an email-shaped
  value remains in a generated file.
- `risk.md` and `dashboard.html` are local operator artifacts; do not publish
  them if their attendee details are not appropriate for your audience.
- A legacy `manifest.json` result proves only membership in a manifest the
  recipient trusts; eventkit is not a certificate authority or hosted
  verification service. A signed `credentials.json` package additionally
  proves that the package and requested credential were signed by its issuer
  key and were not revoked.

## Tests

Run the project suite from the repository root:

```sh
python -m pytest -q
```

or:

```sh
make test
```

The test suite blocks sockets and subprocesses through `tests/conftest.py` and
checks:

- CSV/BOM/multiline ingestion
- normalization and EK01–EK08 validation
- schedule clashes and ICS folding/UTC output
- check-in and certificate determinism
- SVG, HTML, manifest, and email post-checks
- dry runs and byte-identical reruns
- edited output, audit integrity, stale files, traversal, and symlinks
- safe cleanup and privacy-safe impact previews
- the complete roster/check-in/schedule/certificate/email/dashboard flow

## Docker

Build the pinned Python 3.12 image:

```sh
docker build -t eventkit .
```

Run the offline test suite inside the image:

```sh
docker run --rm --network none eventkit python -m pytest -q
```

Check the CLI:

```sh
docker run --rm --network none eventkit eventkit --version
```

Run a full demo with a mounted output directory:

```sh
docker build -t eventkit .
docker run --rm --network none \
  -e EVENTKIT_SECRET='replace-with-a-local-secret-at-least-16-chars' \
  -v "$PWD/demo-full:/app/demo-full" \
  eventkit sh -c 'eventkit init demo-full --full && eventkit run demo-full/event.toml'
```

The image has no entrypoint; commands remain explicit and reproducible.

## Development

The source layout is intentionally small and standard-library-only:

```text
src/eventkit/
├── cli.py                 # command dispatch and pipeline orchestration
├── config.py              # TOML loading and path/step validation
├── csvio.py               # BOM-safe CSV with physical line numbers
├── validate.py            # roster normalization and issue rules
├── schedule.py            # sessions and clash detection
├── calendar.py            # deterministic ICS
├── agenda.py              # schedule HTML
├── checkin.py             # printable check-in HTML
├── certificates.py        # SVG certificates
├── codes.py               # HMAC codes and manifest verification
├── emails.py              # deterministic unsent EML drafts
├── clean.py               # audited safe cleanup
├── impact.py              # privacy-safe change preview
├── dashboard.py           # polished offline interface
├── risk.py                # deterministic risk report
└── output.py              # safe deterministic writer/audit
```

Keep runtime dependencies at zero. Add behavior with a focused fixture,
post-check, and end-to-end assertion. Do not commit credentials or generated
event kits containing real attendee data.

## License and project status

This repository is an OmniRush Frontier Buildathon project. The current CLI and
offline artifact pipeline are implemented locally; Docker validation depends on
having a Docker engine available on the machine running the rebuild.
